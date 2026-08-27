from __future__ import annotations

import base64
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

from catalog_api import CatalogHandler, RateLimitExceeded, SlidingWindowRateLimiter
from supplement_analyzer import InputValidationError


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data" / "products.sqlite3"


class FakeAnalyzer:
    def __init__(self, candidates):
        self.candidates = candidates

    def analyze_product(self, **values):
        return {
            "analysis_type": "individual",
            "subject": {
                "name": "Example D3",
                "brand": "Example",
                "serving_size": "1 capsule",
                "components": [
                    {"name": "Vitamin D3", "amount": 25, "unit": "mcg"}
                ],
            },
            "recommendations": [],
            "gaps": [],
            "confidence": 0.9,
            "evidence": [],
            "limitations": [],
            "disclaimer": "Informational comparison only.",
        }

    def compare_portfolio(self, supplements, **values):
        return {
            "analysis_type": "portfolio",
            "products": supplements,
            "recommendations": [],
            "gaps": [],
            "confidence": 0.8,
            "evidence": [],
            "limitations": [],
            "disclaimer": "Informational comparison only.",
        }


def make_handler(**overrides):
    server = SimpleNamespace(
        database=DATABASE.resolve(),
        web_root=(ROOT / "web").resolve(),
        analyzer_factory=FakeAnalyzer,
        analysis_available=True,
        access_token="friend-token",
        rate_limiter=SlidingWindowRateLimiter(2, 60),
        max_request_bytes=8_000_000,
        max_image_bytes=6_000_000,
    )
    for name, value in overrides.items():
        setattr(server, name, value)
    handler = CatalogHandler.__new__(CatalogHandler)
    handler.server = server
    handler.client_address = ("203.0.113.10", 1234)
    handler.headers = Message()
    return handler


class CatalogAPITests(unittest.TestCase):
    def test_catalog_candidates_are_only_comparison_brands(self) -> None:
        candidates = make_handler().comparison_candidates()

        self.assertGreater(len(candidates), 100)
        self.assertTrue(
            all(
                item["brand"].casefold() == "nutrilite"
                or item["brand"].casefold() == "xs"
                or item["brand"].casefold().startswith("xs ")
                for item in candidates
            )
        )
        self.assertTrue(all(item["is_purchasable"] == 1 for item in candidates))
        self.assertTrue(all(item["is_sellable"] == 1 for item in candidates))

    def test_search_treats_sql_wildcards_as_literal_text(self) -> None:
        result = make_handler().search_products({"q": ["%"], "limit": ["100"]})

        self.assertGreater(result["count"], 0)
        self.assertLess(result["count"], 100)
        for product in result["products"]:
            searchable = " ".join(
                str(product.get(field) or "")
                for field in ("name", "brand", "description", "source_code")
            )
            self.assertIn("%", searchable)

    def test_analyze_wraps_transient_result_without_image(self) -> None:
        image = "data:image/jpeg;base64," + base64.b64encode(
            b"\xff\xd8\xffnot-retained"
        ).decode()
        result = make_handler().analyze_payload(
            {"source_type": "image", "image_data_url": image}
        )

        self.assertEqual(result["product"]["name"], "Example D3")
        self.assertEqual(result["source"], {"type": "image", "product_url": None})
        self.assertNotIn("image_data_url", result)
        self.assertIn("analysis_id", result)

    def test_profile_is_opt_in(self) -> None:
        payload = {"profile": {"age_range": "50-59"}}
        self.assertIsNone(CatalogHandler.optional_profile(payload))
        payload["include_profile"] = True
        self.assertEqual(
            CatalogHandler.optional_profile(payload), {"age_range": "50-59"}
        )
        with self.assertRaises(InputValidationError):
            CatalogHandler.optional_profile({"include_profile": "false", "profile": {}})

    def test_analysis_rejects_mixed_input_types(self) -> None:
        with self.assertRaisesRegex(InputValidationError, "only the selected"):
            make_handler().analyze_payload(
                {
                    "source_type": "text",
                    "product_text": "Vitamin D3 25 mcg",
                    "product_url": "https://example.com/product",
                }
            )

    def test_portfolio_response_preserves_warnings_and_source_summary(self) -> None:
        analyzer = FakeAnalyzer([])
        original = analyzer.compare_portfolio

        def with_warning(supplements, **values):
            result = original(supplements, **values)
            result["limitations"] = ["Verify serving amounts."]
            return result

        analyzer.compare_portfolio = with_warning
        handler = make_handler(analyzer_factory=lambda candidates: analyzer)
        result = handler.compare_payload({"supplements": [{"name": "Example"}]})

        self.assertEqual(result["warnings"], ["Verify serving amounts."])
        self.assertEqual(result["source"], {"type": "portfolio", "product_count": 1})

    def test_image_validation_rejects_unsupported_data(self) -> None:
        with self.assertRaises(InputValidationError):
            make_handler().validate_image("data:text/plain;base64,dGVzdA==")
        with self.assertRaisesRegex(InputValidationError, "declared type"):
            make_handler().validate_image("data:image/png;base64,dGVzdA==")

    def test_access_token_uses_rate_limit(self) -> None:
        handler = make_handler()
        handler.headers["Authorization"] = "Bearer friend-token"
        handler.require_analysis_access()
        handler.require_analysis_access()
        with self.assertRaises(RateLimitExceeded):
            handler.require_analysis_access()


if __name__ == "__main__":
    unittest.main()
