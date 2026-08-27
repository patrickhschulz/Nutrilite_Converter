from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from import_amway import record_refresh
from refresh_catalog import (
    CatalogError,
    catalog_age_hours,
    ensure_schema,
    refresh_lock,
    validate_catalog,
)


ROOT = Path(__file__).resolve().parents[1]


class RefreshCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.database = self.directory / "products.sqlite3"
        ensure_schema(self.database, ROOT / "schema.sql")

    def insert_product(self, code: str, brand: str, fetched_at: str) -> None:
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                """
                INSERT INTO products (
                    source, source_code, name, brand, product_url,
                    is_active, fetched_at, raw_json
                ) VALUES ('amway-us', ?, ?, ?, ?, 1, ?, '{}')
                """,
                (
                    code,
                    f"{brand} product",
                    brand,
                    f"https://example.test/{code}",
                    fetched_at,
                ),
            )
            connection.commit()

    def test_validation_counts_nutrilite_and_all_xs_brands(self) -> None:
        timestamp = "2026-08-26T12:00:00+00:00"
        self.insert_product("N1", "Nutrilite", timestamp)
        self.insert_product("X1", "XS", timestamp)
        self.insert_product("X2", "XS Sport Nutrition", timestamp)
        self.insert_product("A1", "Amway Home", timestamp)

        result = validate_catalog(self.database)

        self.assertEqual(result["active_total"], 4)
        self.assertEqual(result["active_nutrilite"], 1)
        self.assertEqual(result["active_xs"], 2)
        self.assertEqual(result["nutrilite_xs_view"], 3)

    def test_catalog_age_uses_latest_active_row(self) -> None:
        self.insert_product("N1", "Nutrilite", "2026-08-25T12:00:00+00:00")
        self.insert_product("X1", "XS", "2026-08-26T06:00:00+00:00")

        age = catalog_age_hours(
            self.database, now=datetime(2026, 8, 26, 12, tzinfo=timezone.utc)
        )

        self.assertEqual(age, 6.0)

    def test_validation_rejects_missing_xs_catalog(self) -> None:
        self.insert_product("N1", "Nutrilite", "2026-08-26T12:00:00+00:00")

        with self.assertRaisesRegex(CatalogError, "no active XS"):
            validate_catalog(self.database)

    def test_refresh_lock_prevents_overlapping_runs(self) -> None:
        lock_path = self.directory / ".refresh.lock"

        with refresh_lock(lock_path):
            with self.assertRaisesRegex(CatalogError, "Another catalog refresh"):
                with refresh_lock(lock_path):
                    pass

        self.assertFalse(lock_path.exists())

    def test_successful_refresh_counts_are_auditable(self) -> None:
        timestamp = "2026-08-26T12:00:00+00:00"
        self.insert_product("N1", "Nutrilite", timestamp)
        self.insert_product("X1", "XS", timestamp)

        with closing(sqlite3.connect(self.database)) as connection:
            counts = record_refresh(connection, timestamp, removed_discontinued=2)
            connection.commit()
            audit_row = connection.execute(
                """
                SELECT active_total, active_nutrilite, active_xs,
                       removed_discontinued
                FROM catalog_refreshes
                """
            ).fetchone()

        self.assertEqual(counts, (2, 1, 1))
        self.assertEqual(audit_row, (2, 1, 1, 2))


if __name__ == "__main__":
    unittest.main()
