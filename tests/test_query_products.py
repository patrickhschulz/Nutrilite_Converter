from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

from query_products import build_fts_query


ROOT = Path(__file__).resolve().parents[1]


class QueryProductsTests(unittest.TestCase):
    def test_user_text_is_converted_to_literal_fts_terms(self) -> None:
        self.assertEqual(build_fts_query("Women's Pack"), '"Women" AND "s" AND "Pack"')
        self.assertEqual(build_fts_query(" OR % "), '"OR"')
        self.assertEqual(build_fts_query("***"), "")

    def test_apostrophe_search_runs_against_the_catalog(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(ROOT / "query_products.py"),
                "Women's Pack",
                "--database",
                str(ROOT / "data" / "products.sqlite3"),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        self.assertIn("Women’s Pack", completed.stdout)


if __name__ == "__main__":
    unittest.main()
