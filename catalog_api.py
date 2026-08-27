#!/usr/bin/env python3
"""Serve a small read-only HTTP API and browser UI for the product catalog."""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = ROOT / "data" / "products.sqlite3"

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Nutrilite Converter Catalog</title>
  <style>
    :root { color-scheme: light dark; font-family: system-ui, sans-serif; }
    body { max-width: 58rem; margin: 3rem auto; padding: 0 1.25rem; }
    form { display: flex; gap: .6rem; margin: 1.5rem 0; }
    input { flex: 1; padding: .75rem; font: inherit; }
    button { padding: .75rem 1rem; font: inherit; cursor: pointer; }
    article { border-block-start: 1px solid #8886; padding: 1rem 0; }
    .meta { opacity: .72; font-size: .92rem; }
    a { color: #2684ff; }
  </style>
</head>
<body>
  <h1>Nutrilite Converter catalog</h1>
  <p>Search the locally maintained Amway US catalog. This deployment exposes
     public catalog data only; supplement-label analysis is a later application layer.</p>
  <form id="search">
    <input id="query" name="q" aria-label="Product search" placeholder="Vitamin D" required>
    <button>Search</button>
  </form>
  <p id="status" class="meta"></p>
  <main id="results"></main>
  <script>
    const form = document.querySelector('#search');
    const status = document.querySelector('#status');
    const results = document.querySelector('#results');
    const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, c =>
      ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
    form.addEventListener('submit', async event => {
      event.preventDefault();
      status.textContent = 'Searching…';
      results.replaceChildren();
      const q = document.querySelector('#query').value;
      const response = await fetch(`/api/products?q=${encodeURIComponent(q)}&limit=25`);
      const body = await response.json();
      status.textContent = `${body.count} result${body.count === 1 ? '' : 's'}`;
      results.innerHTML = body.products.map(product => `
        <article>
          <h2>${escapeHtml(product.name)}</h2>
          <p class="meta">${escapeHtml(product.brand || 'Unbranded')} · SKU ${escapeHtml(product.source_code)}</p>
          <p>${escapeHtml(product.description || '')}</p>
          <a href="${escapeHtml(product.product_url)}" rel="noreferrer">View source product</a>
        </article>`).join('');
    });
  </script>
</body>
</html>
"""


class CatalogHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], database: Path):
        self.database = database.resolve()
        super().__init__(address, CatalogHandler)


class CatalogHandler(BaseHTTPRequestHandler):
    server: CatalogHTTPServer

    def log_message(self, message: str, *args: object) -> None:
        print(f"{self.address_string()} - {message % args}")

    def security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'",
        )

    def send_body(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.security_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.send_body(body, "application/json; charset=utf-8", status)

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
            conditions.append(
                "(name LIKE ? COLLATE NOCASE OR brand LIKE ? COLLATE NOCASE "
                "OR description LIKE ? COLLATE NOCASE OR source_code LIKE ? COLLATE NOCASE)"
            )
            pattern = f"%{term}%"
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

    def route(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self.send_body(INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            elif parsed.path in {"/health", "/api/status"}:
                status = self.catalog_status()
                code = HTTPStatus.OK if status["integrity"] == "ok" else HTTPStatus.SERVICE_UNAVAILABLE
                self.send_json(status, code)
            elif parsed.path == "/api/products":
                self.send_json(self.search_products(parse_qs(parsed.query)))
            elif parsed.path == "/favicon.ico":
                self.send_body(b"", "image/x-icon", HTTPStatus.NO_CONTENT)
            else:
                self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except (ValueError, sqlite3.Error, OSError) as error:
            self.send_json(
                {"error": "catalog request failed", "detail": str(error)},
                HTTPStatus.BAD_REQUEST,
            )

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.route()

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.route()


def make_server(host: str, port: int, database: Path) -> CatalogHTTPServer:
    if not database.is_file():
        raise FileNotFoundError(f"Catalog database not found: {database}")
    return CatalogHTTPServer((host, port), database)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    args = parser.parse_args()
    try:
        server = make_server(args.host, args.port, args.database)
    except (FileNotFoundError, OSError) as error:
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
