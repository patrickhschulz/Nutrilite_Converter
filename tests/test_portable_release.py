from __future__ import annotations

import gzip
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from create_client import client_slug, create_client_database
from seed_catalog import rebuild_database
from verify_install import database_checks


ROOT = Path(__file__).resolve().parents[1]


class PortableReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def test_catalog_can_be_reconstructed_from_compressed_seed(self) -> None:
        seed = self.directory / "catalog.json.gz"
        packaged_seed = ROOT / "data" / "seed" / "amway-all-products.json.gz"
        if packaged_seed.is_file():
            shutil.copy2(packaged_seed, seed)
        else:
            raw_seed = ROOT / "data" / "raw" / "amway-all-products.json"
            with raw_seed.open("rb") as source:
                with gzip.open(seed, "wb") as destination:
                    shutil.copyfileobj(source, destination)
        database = self.directory / "products.sqlite3"

        rebuilt = rebuild_database(
            database, ROOT / "schema.sql", seed, keep_backup=False
        )
        verified = database_checks(database, expected_schema_version=1)

        self.assertEqual(rebuilt["active_total"], 499)
        self.assertEqual(verified["active_nutrilite"], 74)
        self.assertEqual(verified["active_xs"], 58)
        self.assertEqual(verified["catalog_refresh_rows"], 1)

    def test_client_database_is_private_empty_and_named(self) -> None:
        database = create_client_database(
            "Sample Client", self.directory / "Clients", ROOT / "templates" / "client_schema.sql"
        )

        with closing(sqlite3.connect(database)) as connection:
            profile = connection.execute("SELECT name FROM client_profile").fetchone()[0]
            supplements = connection.execute("SELECT count(*) FROM supplements").fetchone()[0]
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]

        self.assertEqual(database.parent.name, "sample-client")
        self.assertEqual(profile, "Sample Client")
        self.assertEqual(supplements, 0)
        self.assertEqual(integrity, "ok")

    def test_client_slug_rejects_path_only_names(self) -> None:
        with self.assertRaises(ValueError):
            client_slug("../")


if __name__ == "__main__":
    unittest.main()
