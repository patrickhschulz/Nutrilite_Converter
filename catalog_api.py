#!/usr/bin/env python3
"""Serve the installable Nutrilite Converter web app and its small JSON API."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import mimetypes
import os
import sqlite3
import threading
import time
import uuid
from collections import defaultdict, deque
from contextlib import closing
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import parse_qs, unquote, urlparse

from supplement_analyzer import (
    AnalysisValidationError,
    AnalyzerError,
    InputValidationError,
    SupplementAnalyzer,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = ROOT / "data" / "products.sqlite3"
DEFAULT_WEB_ROOT = ROOT / "web"
APP_VERSION = "2.0.0"
ALLOWED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/gif",
}


class SlidingWindowRateLimiter:
    """A small in-memory limiter suitable for one-process friend deployments."""

    def __init__(self, limit: int, window_seconds: int = 3600):
        if limit < 1 or window_seconds < 1:
            raise ValueError("rate-limit values must be positive")
        self.limit = limit
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def consume(self, key: str, now: float | None = None) -> tuple[bool, int]:
        timestamp = time.monotonic() if now is None else now
        cutoff = timestamp - self.window_seconds
        with self._lock:
            events = self._events[key]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.limit:
                retry_after = max(1, int(self.window_seconds - (timestamp - events[0])))
                return False, retry_after
            events.append(timestamp)
        return True, 0


class RateLimitExceeded(Exception):
    def __init__(self, retry_after: int):
        super().__init__("Analysis rate limit reached; try again later")
        self.retry_after = retry_after


AnalyzerFactory = Callable[[Sequence[Mapping[str, Any]]], SupplementAnalyzer]


class CatalogHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        database: Path,
        *,
        web_root: Path = DEFAULT_WEB_ROOT,
        analyzer_factory: AnalyzerFactory | None = None,
        analysis_available: bool | None = None,
        access_token: str | None = None,
        rate_limit_per_hour: int = 20,
        max_request_bytes: int = 8_500_000,
        max_image_bytes: int = 6_000_000,
    ):
        self.database = database.resolve()
        self.web_root = web_root.resolve()
        self.analyzer_factory = analyzer_factory or (
            lambda candidates: SupplementAnalyzer(candidates)
        )
        self.analysis_available = (
            bool((os.environ.get("OPENAI_API_KEY") or "").strip())
            if analysis_available is None
            else analysis_available
        )
        self.access_token = (access_token or "").strip()
        self.rate_limiter = SlidingWindowRateLimiter(rate_limit_per_hour)
        self.max_request_bytes = max_request_bytes
        self.max_image_bytes = max_image_bytes
        super().__init__(address, CatalogHandler)


class CatalogHandler(BaseHTTPRequestHandler):
    server: CatalogHTTPServer
    server_version = "NutriliteConverter"
    sys_version = ""

    def log_message(self, message: str, *args: object) -> None:
        # Request bodies, access tokens, profile data, and image data are never
        # logged. URL query strings are omitted as well.
        path = urlparse(getattr(self, "path", "")).path
        print(f"{self.client_address[0]} - {self.command} {path} - {message % args}")

    def security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header(
            "Permissions-Policy",
            "camera=(self), geolocation=(), microphone=(), payment=()",
        )
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; base-uri 'none'; connect-src 'self'; "
            "font-src 'self'; form-action 'self'; frame-ancestors 'none'; "
            "img-src 'self' data: blob:; manifest-src 'self'; "
            "object-src 'none'; script-src 'self'; style-src 'self'; "
            "worker-src 'self'",
        )

    def send_body(
        self,
        body: bytes,
        content_type: str,
        status: int = 200,
        *,
        cache_control: str = "no-store",
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.security_headers()
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(
        self,
        payload: Any,
        status: int = 200,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        body = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.send_body(
            body,
            "application/json; charset=utf-8",
            status,
            extra_headers=headers,
        )

    def connection(self) -> sqlite3.Connection:
        uri = f"{self.server.database.as_uri()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        connection.row_factory = sqlite3.Row
        return connection

    def catalog_status(self) -> dict[str, Any]:
        with closing(self.connection()) as connection:
            row = connection.execute(
                """
                SELECT
                    count(*) FILTER (WHERE is_active = 1) AS active_total,
                    count(*) FILTER (
                        WHERE is_active = 1 AND lower(brand) = 'nutrilite'
                    ) AS active_nutrilite,
                    count(*) FILTER (
                        WHERE is_active = 1 AND
                        (lower(brand) = 'xs' OR lower(brand) LIKE 'xs %')
                    ) AS active_xs,
                    count(*) FILTER (WHERE is_active = 0) AS discontinued_pending,
                    max(fetched_at) FILTER (WHERE is_active = 1) AS latest_fetched_at
                FROM products
                """
            ).fetchone()
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
        return {**dict(row), "integrity": integrity}

    @staticmethod
    def integer_parameter(
        query: dict[str, list[str]], name: str, default: int, minimum: int, maximum: int
    ) -> int:
        try:
            value = int(query.get(name, [str(default)])[0])
        except ValueError as error:
            raise ValueError(f"{name} must be an integer") from error
        return max(minimum, min(value, maximum))

    def search_products(self, query: dict[str, list[str]]) -> dict[str, Any]:
        term = query.get("q", [""])[0].strip()
        brand = query.get("brand", [""])[0].strip()
        comparison_only = query.get("comparison_only", ["false"])[0].casefold() in {
            "1",
            "true",
            "yes",
        }
        limit = self.integer_parameter(query, "limit", 20, 1, 100)
        offset = self.integer_parameter(query, "offset", 0, 0, 100_000)

        conditions = ["is_active = 1"]
        parameters: list[Any] = []
        if term:
            match = (
                "name LIKE ? ESCAPE '\\' COLLATE NOCASE OR "
                "brand LIKE ? ESCAPE '\\' COLLATE NOCASE OR "
                "description LIKE ? ESCAPE '\\' COLLATE NOCASE OR "
                "source_code LIKE ? ESCAPE '\\' COLLATE NOCASE"
            )
            conditions.append(f"({match})")
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            parameters.extend([pattern, pattern, pattern, pattern])
        if brand:
            conditions.append("brand = ? COLLATE NOCASE")
            parameters.append(brand)
        if comparison_only:
            conditions.append(
                "(lower(brand) = 'nutrilite' OR lower(brand) = 'xs' "
                "OR lower(brand) LIKE 'xs %')"
            )

        where = " AND ".join(conditions)
        sql = f"""
            SELECT source_code, name, brand, description, product_url, image_url,
                   retail_price_cents, member_price_cents, currency,
                   is_bundle, is_purchasable, is_sellable, stock_disposition,
                   fetched_at
            FROM products
            WHERE {where}
            ORDER BY name COLLATE NOCASE, source_code
            LIMIT ? OFFSET ?
        """
        parameters.extend([limit, offset])
        with closing(self.connection()) as connection:
            products = [dict(row) for row in connection.execute(sql, parameters)]
        return {
            "query": term,
            "brand": brand or None,
            "comparison_only": comparison_only,
            "limit": limit,
            "offset": offset,
            "count": len(products),
            "products": products,
        }

    def comparison_candidates(self) -> list[dict[str, Any]]:
        with closing(self.connection()) as connection:
            return [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT source_code, name, brand, description, product_url,
                           image_url, retail_price_cents, member_price_cents,
                           currency, is_bundle, is_purchasable, is_sellable,
                           stock_disposition, fetched_at
                    FROM products
                    WHERE is_active = 1 AND
                          is_purchasable = 1 AND is_sellable = 1 AND
                          (lower(brand) = 'nutrilite' OR lower(brand) = 'xs' OR
                           lower(brand) LIKE 'xs %')
                    ORDER BY name COLLATE NOCASE, source_code
                    """
                )
            ]

    def app_config(self) -> dict[str, Any]:
        return {
            "app_version": APP_VERSION,
            "analysis_available": bool(
                self.server.analysis_available and self.server.access_token
            ),
            "access_required": True,
            "max_image_bytes": self.server.max_image_bytes,
            "local_profile_storage": True,
            "server_retains_inputs": False,
        }

    def read_json_body(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").partition(";")[0].strip()
        if content_type != "application/json":
            raise InputValidationError("Content-Type must be application/json")
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError as error:
            raise InputValidationError("A valid Content-Length is required") from error
        if length < 2:
            raise InputValidationError("Request body is empty")
        if length > self.server.max_request_bytes:
            raise InputValidationError("Request body exceeds the configured limit")
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise InputValidationError("Request body was incomplete")
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InputValidationError("Request body must be valid UTF-8 JSON") from error
        if not isinstance(payload, dict):
            raise InputValidationError("Request body must be a JSON object")
        return payload

    def require_analysis_access(self) -> None:
        if not self.server.analysis_available:
            raise AnalyzerError("Supplement analysis is not configured")
        if not self.server.access_token:
            raise AnalyzerError("Supplement analysis access control is not configured")
        authorization = self.headers.get("Authorization", "")
        prefix = "Bearer "
        supplied = authorization[len(prefix) :].strip() if authorization.startswith(prefix) else ""
        if not supplied or not hmac.compare_digest(supplied, self.server.access_token):
            raise PermissionError("A valid invite code is required")

        forwarded = self.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip()
        client_ip = forwarded or self.client_address[0]
        token_fingerprint = hashlib.sha256(supplied.encode("utf-8")).hexdigest()[:16]
        allowed, retry_after = self.server.rate_limiter.consume(
            f"{token_fingerprint}:{client_ip}"
        )
        if not allowed:
            raise RateLimitExceeded(retry_after)

    def validate_image(self, image_data_url: Any) -> str | None:
        if image_data_url in {None, ""}:
            return None
        if not isinstance(image_data_url, str) or not image_data_url.startswith("data:"):
            raise InputValidationError("image_data_url must be a data URL")
        header, separator, encoded = image_data_url.partition(",")
        if not separator or ";base64" not in header:
            raise InputValidationError("Image data must use base64 encoding")
        media_type = header[5:].split(";", 1)[0].casefold()
        if media_type not in ALLOWED_IMAGE_TYPES:
            raise InputValidationError("Image must be JPEG, PNG, WebP, or GIF")
        try:
            decoded = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise InputValidationError("Image data is not valid base64") from error
        decoded_size = len(decoded)
        if decoded_size == 0 or decoded_size > self.server.max_image_bytes:
            raise InputValidationError("Image exceeds the configured size limit")
        signatures = {
            "image/jpeg": decoded.startswith(b"\xff\xd8\xff"),
            "image/png": decoded.startswith(b"\x89PNG\r\n\x1a\n"),
            "image/gif": decoded.startswith((b"GIF87a", b"GIF89a")),
            "image/webp": (
                len(decoded) >= 12
                and decoded.startswith(b"RIFF")
                and decoded[8:12] == b"WEBP"
            ),
        }
        if not signatures[media_type]:
            raise InputValidationError("Image content does not match its declared type")
        return image_data_url

    @staticmethod
    def optional_profile(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
        include_profile = payload.get("include_profile", False)
        if not isinstance(include_profile, bool):
            raise InputValidationError("include_profile must be true or false")
        if not include_profile:
            return None
        profile = payload.get("profile")
        if not isinstance(profile, dict):
            raise InputValidationError("profile must be an object")
        return profile

    def analyzer(self) -> SupplementAnalyzer:
        candidates = self.comparison_candidates()
        if not candidates:
            raise AnalyzerError("No active Nutrilite or XS candidates are available")
        return self.server.analyzer_factory(candidates)

    def analyze_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        source_type = payload.get("source_type")
        if source_type not in {"image", "url", "text"}:
            raise InputValidationError("source_type must be image, url, or text")
        image = self.validate_image(payload.get("image_data_url"))
        text = payload.get("product_text")
        product_url = payload.get("product_url")
        if text is not None and not isinstance(text, str):
            raise InputValidationError("product_text must be text")
        if product_url is not None and not isinstance(product_url, str):
            raise InputValidationError("product_url must be text")
        if source_type == "image" and image is None:
            raise InputValidationError("Select a label or package image")
        if source_type == "url" and not (product_url or "").strip():
            raise InputValidationError("Enter a product URL")
        if source_type == "text" and not (text or "").strip():
            raise InputValidationError("Enter product or label text")

        selected_values = {
            "image": image is not None,
            "url": bool((product_url or "").strip()),
            "text": bool((text or "").strip()),
        }
        if any(value for kind, value in selected_values.items() if kind != source_type):
            raise InputValidationError("Submit only the selected product input type")

        result = self.analyzer().analyze_product(
            text=(text or "").strip() or None if source_type == "text" else None,
            image_data_url=image if source_type == "image" else None,
            product_url=(product_url or "").strip() or None
            if source_type == "url"
            else None,
            demographics=self.optional_profile(payload),
        )
        if not isinstance(result, dict):
            raise AnalysisValidationError("Analyzer returned an invalid result")
        return {
            "analysis_id": str(uuid.uuid4()),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": {
                "type": source_type,
                "product_url": (product_url or "").strip() or None,
            },
            "product": result.get("subject"),
            "warnings": result.get("limitations", []),
            **result,
        }

    def compare_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        supplements = payload.get("supplements")
        if not isinstance(supplements, list) or not supplements:
            raise InputValidationError("Add at least one analyzed supplement")
        if len(supplements) > 50 or not all(isinstance(item, dict) for item in supplements):
            raise InputValidationError("supplements must contain 1 to 50 objects")
        result = self.analyzer().compare_portfolio(
            supplements,
            demographics=self.optional_profile(payload),
        )
        if not isinstance(result, dict):
            raise AnalysisValidationError("Analyzer returned an invalid result")
        return {
            "analysis_id": str(uuid.uuid4()),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "warnings": result.get("limitations", []),
            "source": {"type": "portfolio", "product_count": len(supplements)},
            **result,
        }

    def send_static(self, request_path: str) -> bool:
        relative = "index.html" if request_path == "/" else unquote(request_path).lstrip("/")
        parts = Path(relative).parts
        if (
            not relative
            or any(part.startswith(".") for part in parts)
            or ".." in parts
        ):
            return False
        root = self.server.web_root
        path = (root / relative).resolve()
        if path.parent != root and root not in path.parents:
            return False
        if not path.is_file():
            return False
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if path.suffix in {".js", ".webmanifest"}:
            content_type = {
                ".js": "text/javascript",
                ".webmanifest": "application/manifest+json",
            }[path.suffix]
        headers = (
            {"Service-Worker-Allowed": "/"}
            if path.name == "service-worker.js"
            else None
        )
        self.send_body(
            path.read_bytes(),
            f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type,
            cache_control="no-cache",
            extra_headers=headers,
        )
        return True

    def route_get(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path in {"/health", "/api/status"}:
                status = self.catalog_status()
                code = (
                    HTTPStatus.OK
                    if status["integrity"] == "ok"
                    else HTTPStatus.SERVICE_UNAVAILABLE
                )
                self.send_json(status, code)
            elif parsed.path == "/api/config":
                self.send_json(self.app_config())
            elif parsed.path == "/api/products":
                self.send_json(self.search_products(parse_qs(parsed.query)))
            elif parsed.path.startswith("/api/"):
                self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            elif not self.send_static(parsed.path):
                self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, sqlite3.Error, OSError) as error:
            self.send_json(
                {"error": "catalog request failed", "detail": str(error)},
                HTTPStatus.BAD_REQUEST,
            )

    def route_post(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in {"/api/analyze", "/api/compare"}:
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            self.require_analysis_access()
            payload = self.read_json_body()
            result = (
                self.analyze_payload(payload)
                if parsed.path == "/api/analyze"
                else self.compare_payload(payload)
            )
            self.send_json(result)
        except PermissionError:
            self.send_json(
                {"error": "unauthorized", "detail": "A valid invite code is required"},
                HTTPStatus.UNAUTHORIZED,
            )
        except RateLimitExceeded as error:
            self.send_json(
                {"error": "rate_limited", "detail": str(error)},
                HTTPStatus.TOO_MANY_REQUESTS,
                headers={"Retry-After": str(error.retry_after)},
            )
        except InputValidationError as error:
            self.send_json(
                {"error": "invalid_request", "detail": str(error)},
                HTTPStatus.BAD_REQUEST,
            )
        except (AnalyzerError, AnalysisValidationError) as error:
            # Error text is intentionally narrow and comes from the controlled
            # analyzer layer; request content and credentials are never echoed.
            self.send_json(
                {"error": "analysis_unavailable", "detail": str(error)},
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
        except (OSError, sqlite3.Error) as error:
            print(f"Analysis request failed: {type(error).__name__}: {error}")
            self.send_json(
                {"error": "analysis_unavailable", "detail": "Try again later"},
                HTTPStatus.SERVICE_UNAVAILABLE,
            )

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.route_get()

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.route_get()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.route_post()


def environment_integer(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def make_server(
    host: str,
    port: int,
    database: Path,
    **options: Any,
) -> CatalogHTTPServer:
    if not database.is_file():
        raise FileNotFoundError(f"Catalog database not found: {database}")
    web_root = Path(options.pop("web_root", DEFAULT_WEB_ROOT))
    if not web_root.is_dir():
        raise FileNotFoundError(f"Web application not found: {web_root}")
    return CatalogHTTPServer((host, port), database, web_root=web_root, **options)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--web-root", type=Path, default=DEFAULT_WEB_ROOT)
    args = parser.parse_args()
    try:
        server = make_server(
            args.host,
            args.port,
            args.database,
            web_root=args.web_root,
            access_token=os.environ.get("APP_ACCESS_TOKEN"),
            rate_limit_per_hour=environment_integer(
                "ANALYSIS_RATE_LIMIT_PER_HOUR", 20, 1, 1000
            ),
            max_request_bytes=environment_integer(
                "MAX_REQUEST_BYTES", 8_500_000, 100_000, 25_000_000
            ),
            max_image_bytes=environment_integer(
                "MAX_IMAGE_BYTES", 6_000_000, 50_000, 20_000_000
            ),
        )
    except (FileNotFoundError, OSError, ValueError) as error:
        print(f"Unable to start catalog API: {error}")
        return 1
    print(f"Serving Nutrilite Converter on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
