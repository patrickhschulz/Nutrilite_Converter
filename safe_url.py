#!/usr/bin/env python3
"""Bounded, SSRF-resistant fetching for untrusted product-page URLs.

The fetcher intentionally accepts only textual web documents. Product images
are uploaded to the analyzer as data URLs, which avoids letting an AI request
turn arbitrary URLs into a general-purpose network proxy.
"""

from __future__ import annotations

import html
import http.client
import ipaddress
import socket
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import SplitResult, urljoin, urlsplit
from urllib.request import (
    HTTPHandler,
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)


DEFAULT_MAX_BYTES = 1_000_000
DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_REDIRECTS = 4
ALLOWED_CONTENT_TYPES = frozenset(
    {
        "application/json",
        "application/xhtml+xml",
        "text/html",
        "text/plain",
    }
)
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class SafeURLError(ValueError):
    """Raised when a URL is unsafe or its response exceeds fetch policy."""


@dataclass(frozen=True)
class FetchedContent:
    """Useful, decoded text returned from a safely fetched public URL."""

    url: str
    content_type: str
    text: str
    status: int


class _NoRedirect(HTTPRedirectHandler):
    """Make redirects visible so every destination can be revalidated."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


class _UsefulHTMLParser(HTMLParser):
    """Extract human-readable page content without scripts or styling."""

    _SKIPPED = frozenset({"script", "style", "noscript", "svg", "template"})
    _BREAKS = frozenset(
        {
            "address",
            "article",
            "aside",
            "blockquote",
            "br",
            "dd",
            "div",
            "dt",
            "figcaption",
            "footer",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "header",
            "li",
            "main",
            "nav",
            "p",
            "section",
            "td",
            "th",
            "title",
            "tr",
        }
    )
    _USEFUL_META_FIELDS = frozenset(
        {
            "brand",
            "description",
            "name",
            "og:description",
            "og:title",
            "price",
            "pricecurrency",
            "product:price:amount",
            "product:price:currency",
            "sku",
            "twitter:description",
            "twitter:title",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._json_ld_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        attributes = {
            name.casefold(): value
            for name, value in attrs
            if isinstance(name, str) and isinstance(value, str)
        }
        if (
            tag == "script"
            and not self._skip_depth
            and attributes.get("type", "").split(";", 1)[0].strip().casefold()
            == "application/ld+json"
        ):
            self._json_ld_depth += 1
            self._parts.append("\nStructured product data:\n")
        elif tag in self._SKIPPED:
            self._skip_depth += 1
        elif not self._skip_depth and tag == "meta":
            field = (
                attributes.get("name")
                or attributes.get("property")
                or attributes.get("itemprop")
                or ""
            ).casefold()
            content = attributes.get("content", "").strip()
            if field in self._USEFUL_META_FIELDS and content:
                self._parts.append(f"\n{field}: {content}\n")
        elif not self._skip_depth and tag in self._BREAKS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if tag == "script" and self._json_ld_depth:
            self._json_ld_depth -= 1
            self._parts.append("\n")
        elif tag in self._SKIPPED:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif not self._skip_depth and tag in self._BREAKS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._json_ld_depth or not self._skip_depth:
            self._parts.append(data)

    def text(self) -> str:
        lines = []
        for line in "".join(self._parts).splitlines():
            normalized = " ".join(line.split())
            if normalized:
                lines.append(normalized)
        return "\n".join(lines)


def _default_resolver(host: str, port: int) -> Iterable[tuple]:
    return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)


def _addresses_from_results(results: Iterable[tuple]) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for result in results:
        try:
            raw_address = result[4][0]
            addresses.add(ipaddress.ip_address(raw_address.split("%", 1)[0]))
        except (IndexError, TypeError, ValueError) as error:
            raise SafeURLError("The URL host returned an invalid address") from error
    if not addresses:
        raise SafeURLError("The URL host did not resolve")
    return addresses


def _is_public_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        address.is_global
        and not address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_reserved
        and not address.is_multicast
        and not address.is_unspecified
    )


def _resolved_public_results(
    host: str,
    port: int,
    resolver: Callable[[str, int], Iterable[tuple]],
) -> list[tuple]:
    """Resolve a host once and reject the complete answer if any IP is unsafe."""

    try:
        ascii_host = host.rstrip(".").encode("idna").decode("ascii")
        results = list(resolver(ascii_host, port))
        addresses = _addresses_from_results(results)
    except (OSError, UnicodeError) as error:
        raise SafeURLError("The URL host could not be resolved") from error
    if any(not _is_public_address(address) for address in addresses):
        raise SafeURLError("The URL host must resolve only to public addresses")
    return results


def _connect_public_socket(
    host: str,
    port: int,
    timeout: float | object,
    source_address: tuple[str, int] | None,
    resolver: Callable[[str, int], Iterable[tuple]],
) -> socket.socket:
    """Connect only to an address from the public DNS answer just validated.

    Pinning the socket to the validated address prevents a second hostname
    lookup inside the HTTP client from being redirected to a private service by
    DNS rebinding.
    """

    results = _resolved_public_results(host, port, resolver)
    last_error: OSError | None = None
    for family, socktype, proto, _canonical_name, sockaddr in results:
        if socktype not in {0, socket.SOCK_STREAM}:
            continue
        connection = socket.socket(family, socket.SOCK_STREAM, proto)
        try:
            connection.settimeout(timeout)
            if source_address:
                connection.bind(source_address)
            connection.connect(sockaddr)
            return connection
        except OSError as error:
            last_error = error
            connection.close()
    if last_error is not None:
        raise last_error
    raise SafeURLError("The URL host returned no usable web address")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, resolver=_default_resolver, **kwargs) -> None:  # noqa: ANN002, ANN003
        self._resolver = resolver
        super().__init__(*args, **kwargs)

    def connect(self) -> None:
        self.sock = _connect_public_socket(
            self.host,
            self.port,
            self.timeout,
            self.source_address,
            self._resolver,
        )
        if self._tunnel_host:
            self._tunnel()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, resolver=_default_resolver, **kwargs) -> None:  # noqa: ANN002, ANN003
        self._resolver = resolver
        super().__init__(*args, **kwargs)

    def connect(self) -> None:
        self.sock = _connect_public_socket(
            self.host,
            self.port,
            self.timeout,
            self.source_address,
            self._resolver,
        )
        server_hostname = self.host
        if self._tunnel_host:
            self._tunnel()
            server_hostname = self._tunnel_host
        self.sock = self._context.wrap_socket(
            self.sock,
            server_hostname=server_hostname,
        )


class _PinnedHTTPHandler(HTTPHandler):
    def __init__(self, resolver: Callable[[str, int], Iterable[tuple]]) -> None:
        super().__init__()
        self._resolver = resolver

    def http_open(self, request):  # noqa: ANN001
        def connection(host, **kwargs):  # noqa: ANN001, ANN003
            return _PinnedHTTPConnection(host, resolver=self._resolver, **kwargs)

        return self.do_open(connection, request)


class _PinnedHTTPSHandler(HTTPSHandler):
    def __init__(self, resolver: Callable[[str, int], Iterable[tuple]]) -> None:
        super().__init__()
        self._resolver = resolver

    def https_open(self, request):  # noqa: ANN001
        def connection(host, **kwargs):  # noqa: ANN001, ANN003
            return _PinnedHTTPSConnection(host, resolver=self._resolver, **kwargs)

        return self.do_open(connection, request, context=self._context)


def _build_pinned_opener(resolver: Callable[[str, int], Iterable[tuple]]):
    # Explicitly disable environment proxies so an untrusted URL cannot bypass
    # the pinned, public-only connection policy through proxy configuration.
    return build_opener(
        ProxyHandler({}),
        _PinnedHTTPHandler(resolver),
        _PinnedHTTPSHandler(resolver),
        _NoRedirect(),
    )


def validate_public_url(
    url: str,
    *,
    resolver: Callable[[str, int], Iterable[tuple]] = _default_resolver,
) -> SplitResult:
    """Validate one URL and every address to which its hostname resolves.

    A hostname is rejected if *any* answer is non-public. This conservative
    policy prevents a mixed public/private DNS response from choosing an
    internal address at connection time.
    """

    if not isinstance(url, str) or not url or len(url) > 8_192:
        raise SafeURLError("A non-empty URL of at most 8192 characters is required")
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise SafeURLError("The URL is malformed") from error
    if parsed.scheme.casefold() not in {"http", "https"}:
        raise SafeURLError("Only http and https URLs are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise SafeURLError("URLs containing credentials are not allowed")
    if not parsed.hostname:
        raise SafeURLError("The URL must include a hostname")
    if any(character.isspace() for character in parsed.netloc):
        raise SafeURLError("The URL hostname is malformed")
    if port is None:
        port = 443 if parsed.scheme.casefold() == "https" else 80
    if port not in {80, 443}:
        raise SafeURLError("Only standard web ports 80 and 443 are allowed")

    host = parsed.hostname.rstrip(".")
    if not host:
        raise SafeURLError("The URL must include a hostname")
    try:
        # Literal IPs should not depend on DNS. Brackets around IPv6 literals
        # have already been removed by SplitResult.hostname.
        addresses = {ipaddress.ip_address(host.split("%", 1)[0])}
    except ValueError:
        try:
            addresses = _addresses_from_results(
                _resolved_public_results(host, port, resolver)
            )
        except SafeURLError:
            raise

    if any(not _is_public_address(address) for address in addresses):
        raise SafeURLError("The URL host must resolve only to public addresses")
    return parsed


def html_to_text(document: str) -> str:
    """Turn HTML into compact, useful text suitable for product extraction."""

    parser = _UsefulHTMLParser()
    try:
        parser.feed(document)
        parser.close()
    except (AssertionError, ValueError):
        # HTMLParser is deliberately forgiving, but malformed character/entity
        # edge cases should still produce safe plain text instead of failing a
        # comparison outright.
        return " ".join(html.unescape(document).split())
    return parser.text()


class SafeURLFetcher:
    """Fetch public text with redirect, size, type, and total-time limits."""

    def __init__(
        self,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        resolver: Callable[[str, int], Iterable[tuple]] = _default_resolver,
        opener=None,
    ) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_redirects < 0 or max_redirects > 20:
            raise ValueError("max_redirects must be between 0 and 20")
        self.max_bytes = max_bytes
        self.timeout = timeout
        self.max_redirects = max_redirects
        self.resolver = resolver
        self.opener = opener or _build_pinned_opener(resolver)

    def fetch(self, url: str) -> FetchedContent:
        deadline = time.monotonic() + self.timeout
        current_url = url
        for redirect_count in range(self.max_redirects + 1):
            validate_public_url(current_url, resolver=self.resolver)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SafeURLError("The URL fetch timed out")
            request = Request(
                current_url,
                headers={
                    "Accept": "text/html, application/xhtml+xml, application/json, text/plain",
                    "Accept-Encoding": "identity",
                    "User-Agent": "Nutrilite-Converter/1.0 (+product-comparison)",
                },
                method="GET",
            )
            try:
                response = self.opener.open(request, timeout=remaining)
            except HTTPError as error:
                if error.code in REDIRECT_STATUSES:
                    response = error
                else:
                    raise SafeURLError(f"The URL returned HTTP {error.code}") from error
            except (TimeoutError, URLError, OSError) as error:
                raise SafeURLError("The URL could not be fetched") from error

            try:
                status = getattr(response, "status", None) or response.getcode()
                if status in REDIRECT_STATUSES:
                    location = response.headers.get("Location")
                    if not location:
                        raise SafeURLError("The URL returned a redirect without a destination")
                    if redirect_count >= self.max_redirects:
                        raise SafeURLError("The URL exceeded the redirect limit")
                    current_url = urljoin(current_url, location)
                    continue
                if not 200 <= status < 300:
                    raise SafeURLError(f"The URL returned HTTP {status}")

                content_encoding = response.headers.get("Content-Encoding", "identity").casefold()
                if content_encoding not in {"", "identity"}:
                    raise SafeURLError("Compressed URL responses are not accepted")
                if not response.headers.get("Content-Type"):
                    raise SafeURLError("The URL did not declare a content type")
                content_type = response.headers.get_content_type().casefold()
                if content_type not in ALLOWED_CONTENT_TYPES:
                    raise SafeURLError("The URL did not return an allowed text content type")
                content_length = response.headers.get("Content-Length")
                if content_length:
                    try:
                        declared_length = int(content_length)
                    except ValueError as error:
                        raise SafeURLError("The URL returned an invalid content length") from error
                    if declared_length < 0:
                        raise SafeURLError("The URL returned an invalid content length")
                    if declared_length > self.max_bytes:
                        raise SafeURLError("The URL response is too large")
                body = response.read(self.max_bytes + 1)
                if len(body) > self.max_bytes:
                    raise SafeURLError("The URL response is too large")
                charset = response.headers.get_content_charset() or "utf-8"
                try:
                    document = body.decode(charset, errors="replace")
                except LookupError as error:
                    raise SafeURLError("The URL returned an unsupported character encoding") from error
                text = html_to_text(document) if content_type in {
                    "text/html",
                    "application/xhtml+xml",
                } else " ".join(document.split())
                if not text:
                    raise SafeURLError("The URL did not contain useful text")
                return FetchedContent(
                    url=current_url,
                    content_type=content_type,
                    text=text,
                    status=status,
                )
            finally:
                response.close()
        raise SafeURLError("The URL exceeded the redirect limit")


def fetch_public_url(
    url: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
) -> FetchedContent:
    """Convenience wrapper using the production DNS resolver and opener."""

    return SafeURLFetcher(
        max_bytes=max_bytes,
        timeout=timeout,
        max_redirects=max_redirects,
    ).fetch(url)
