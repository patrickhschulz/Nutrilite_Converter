from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from build_cloud_release import ARTIFACT_NAME, build_cloud_release
from verify_install import sha256_file


class CloudReleaseTests(unittest.TestCase):
    def test_hosted_archive_is_complete_and_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            result = build_cloud_release(output)
            archive = Path(result["artifact"])
            manifest_path = Path(result["manifest"])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

            with zipfile.ZipFile(archive) as package:
                names = set(package.namelist())

            prefix = f"{ARTIFACT_NAME}/"
            required = {
                f"{prefix}Dockerfile",
                f"{prefix}catalog_api.py",
                f"{prefix}data/products.sqlite3",
                f"{prefix}deploy/lightsail/README.md",
                f"{prefix}deploy/lightsail/compose.production.yaml",
                f"{prefix}.github/workflows/deploy-lightsail.yml",
                f"{prefix}manifest.json",
            }
            self.assertTrue(required.issubset(names))
            self.assertFalse(any("/Clients/" in name for name in names))
            self.assertFalse(manifest["privacy"]["contains_client_data"])
            self.assertEqual(sha256_file(archive), result["sha256"])


if __name__ == "__main__":
    unittest.main()
