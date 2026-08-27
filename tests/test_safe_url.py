from __future__ import annotations

import io
import socket
import unittest
from email.message import Message

from safe_url import SafeURLError, SafeURLFetcher, html_to_text, validate_public_url


def resolver_for(*addresses: str):
    def resolve(host: str, port: int):
        del host
        return [
            (socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))
            for address in addresses
        ]

    return resolve


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, *, status: int = 200, headers: dict[str, str] | None = None):
        super().__init__(body)
        self.status = status
        self.headers = Message()
        for name, value in (headers or {}).items():
            self.headers[name] = value

    def getcode(self) -> int:
        return self.status


class FakeOpener:
    def __init__(self, responses: list[FakeResponse]):
        self.responses = responses
        self.requests = []

    def open(self, request, timeout: float):  # noqa: ANN001
        self.requests.append((request, timeout))
        return self.responses.pop(0)


class PublicURLValidationTests(unittest.TestCase):
    def test_accepts_http_and_https_hosts_resolving_only_publicly(self) -> None:
        parsed = validate_public_url(
            "https://products.example/item?id=1",
            resolver=resolver_for("93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"),
        )
        self.assertEqual(parsed.hostname, "products.example")
        self.assertIsNone(parsed.port)

    def test_rejects_non_web_schemes_and_credentials(self) -> None:
        for url in (
            "file:///etc/passwd",
            "ftp://example.com/item",
            "https://user:secret@example.com/item",
            "https://example.com:8443/item",
            "//example.com/no-scheme",
        ):
            with self.subTest(url=url), self.assertRaises(SafeURLError):
                validate_public_url(url, resolver=resolver_for("93.184.216.34"))

    def test_rejects_every_non_public_ip_class(self) -> None:
        unsafe = (
            "127.0.0.1",
            "10.2.3.4",
            "169.254.169.254",
            "0.0.0.0",
            "224.0.0.1",
            "192.0.2.10",
            "::1",
            "fc00::1",
            "fe80::1",
        )
        for address in unsafe:
            host = f"[{address}]" if ":" in address else address
            with self.subTest(address=address), self.assertRaises(SafeURLError):
                validate_public_url(f"https://{host}/")

    def test_rejects_dns_answer_if_even_one_address_is_private(self) -> None:
        with self.assertRaisesRegex(SafeURLError, "only to public"):
            validate_public_url(
                "https://mixed.example/product",
                resolver=resolver_for("93.184.216.34", "127.0.0.1"),
            )


class SafeFetchTests(unittest.TestCase):
    def test_default_connection_revalidates_and_pins_the_dns_answer(self) -> None:
        answers = [
            "93.184.216.34",
            "127.0.0.1",
        ]

        def changing_resolver(host: str, port: int):
            del host
            address = answers.pop(0)
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))
            ]

        fetcher = SafeURLFetcher(resolver=changing_resolver)
        with self.assertRaisesRegex(SafeURLError, "only to public"):
            fetcher.fetch("http://changing.example/product")
        self.assertEqual(answers, [])

    def test_fetches_bounded_html_and_strips_executable_content(self) -> None:
        opener = FakeOpener(
            [
                FakeResponse(
                    b"<html><head><title>Vitamin D</title><script>steal()</script></head>"
                    b"<body><h1>Label</h1><p>25 mcg vitamin D.</p></body></html>",
                    headers={"Content-Type": "text/html; charset=utf-8"},
                )
            ]
        )
        result = SafeURLFetcher(
            resolver=resolver_for("93.184.216.34"), opener=opener
        ).fetch("https://example.test/product")

        self.assertEqual(result.status, 200)
        self.assertIn("Vitamin D", result.text)
        self.assertIn("25 mcg vitamin D.", result.text)
        self.assertNotIn("steal", result.text)
        request, timeout = opener.requests[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Accept-encoding"), "identity")
        self.assertGreater(timeout, 0)

    def test_revalidates_and_rejects_a_private_redirect_before_fetching_it(self) -> None:
        opener = FakeOpener(
            [
                FakeResponse(
                    b"",
                    status=302,
                    headers={"Location": "http://169.254.169.254/latest/meta-data"},
                )
            ]
        )
        fetcher = SafeURLFetcher(
            resolver=resolver_for("93.184.216.34"), opener=opener, max_redirects=3
        )
        with self.assertRaisesRegex(SafeURLError, "public"):
            fetcher.fetch("https://example.test/product")
        self.assertEqual(len(opener.requests), 1)

    def test_follows_a_public_redirect_and_revalidates_both_hosts(self) -> None:
        resolved: list[str] = []

        def resolver(host: str, port: int):
            resolved.append(host)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

        opener = FakeOpener(
            [
                FakeResponse(b"", status=301, headers={"Location": "https://cdn.example/new"}),
                FakeResponse(b"facts", headers={"Content-Type": "text/plain"}),
            ]
        )
        result = SafeURLFetcher(resolver=resolver, opener=opener).fetch(
            "https://shop.example/old"
        )
        self.assertEqual(resolved, ["shop.example", "cdn.example"])
        self.assertEqual(result.url, "https://cdn.example/new")
        self.assertEqual(result.text, "facts")

    def test_rejects_excess_size_bad_type_compression_and_redirect_loops(self) -> None:
        cases = [
            (
                FakeResponse(b"12345", headers={"Content-Type": "text/plain", "Content-Length": "5"}),
                {"max_bytes": 4},
                "too large",
            ),
            (
                FakeResponse(b"image", headers={"Content-Type": "image/png"}),
                {},
                "content type",
            ),
            (
                FakeResponse(
                    b"compressed",
                    headers={"Content-Type": "text/plain", "Content-Encoding": "gzip"},
                ),
                {},
                "Compressed",
            ),
            (
                FakeResponse(b"", status=302, headers={"Location": "/again"}),
                {"max_redirects": 0},
                "redirect limit",
            ),
        ]
        for response, options, message in cases:
            with self.subTest(message=message):
                fetcher = SafeURLFetcher(
                    resolver=resolver_for("93.184.216.34"),
                    opener=FakeOpener([response]),
                    **options,
                )
                with self.assertRaisesRegex(SafeURLError, message):
                    fetcher.fetch("https://example.test/product")

    def test_plain_html_helper_normalizes_layout(self) -> None:
        self.assertEqual(
            html_to_text("<h1> Supplement&nbsp;Facts </h1><p>Vitamin C&nbsp; 90 mg</p>"),
            "Supplement Facts\nVitamin C 90 mg",
        )

    def test_plain_html_helper_keeps_product_metadata_and_json_ld(self) -> None:
        document = """
            <meta property="og:title" content="Example Magnesium">
            <meta itemprop="price" content="19.95">
            <script>window.privateNoise = true;</script>
            <script type="application/ld+json">
              {"@type":"Product","name":"Example Magnesium","sku":"MAG-1"}
            </script>
        """
        text = html_to_text(document)

        self.assertIn("og:title: Example Magnesium", text)
        self.assertIn("price: 19.95", text)
        self.assertIn('"sku":"MAG-1"', text)
        self.assertNotIn("privateNoise", text)


if __name__ == "__main__":
    unittest.main()
