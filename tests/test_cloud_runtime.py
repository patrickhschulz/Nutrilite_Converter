from __future__ import annotations

import gzip
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

from backup_catalog import create_backup
from catalog_api import CatalogHandler


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data" / "products.sqlite3"


class CloudRuntimeTests(unittest.TestCase):
    def test_catalog_status_and_search_queries(self) -> None:
        with closing(sqlite3.connect(DATABASE)) as connection:
            expected_products = connection.execute(
                "SELECT count(*) FROM products WHERE is_active = 1"
            ).fetchone()[0]

        # Exercise the handler's database boundary directly. The GitHub Actions
        # deployment workflow separately performs a real container HTTP smoke
        # test; avoiding a socket here keeps the unit suite sandbox-compatible.
        handler = CatalogHandler.__new__(CatalogHandler)
        handler.server = SimpleNamespace(database=DATABASE.resolve())
        health = handler.catalog_status()
        search = handler.search_products({"q": ["vitamin d"], "limit": ["3"]})

        self.assertEqual(health["integrity"], "ok")
        self.assertEqual(health["active_total"], expected_products)
        self.assertGreaterEqual(search["count"], 1)
        self.assertLessEqual(search["count"], 3)

    def test_backup_is_compressed_valid_sqlite(self) -> None:
        with closing(sqlite3.connect(DATABASE)) as connection:
            expected_products = connection.execute(
                "SELECT count(*) FROM products"
            ).fetchone()[0]
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "backups"
            result = create_backup(DATABASE, output, retain=2)
            restored = Path(temporary) / "restored.sqlite3"
            with gzip.open(result["backup"], "rb") as source:
                restored.write_bytes(source.read())
            with closing(sqlite3.connect(restored)) as connection:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                products = connection.execute("SELECT count(*) FROM products").fetchone()[0]

        self.assertEqual(integrity, "ok")
        self.assertEqual(products, expected_products)


if __name__ == "__main__":
    unittest.main()
