#!/usr/bin/env python3
"""Build the Docker/Ubuntu release for Lightsail or a DigitalOcean Droplet."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from build_release import write_zip
from refresh_catalog import validate_catalog
from verify_install import sha256_file


ROOT = Path(__file__).resolve().parent
ARTIFACT_NAME = "Nutrilite_Converter_Lightsail"
ARTIFACT_VERSION = "1.0.0"

PACKAGE_FILES = (
    ".dockerignore",
    ".github/workflows/deploy-lightsail.yml",
    "Dockerfile",
    "README.md",
    "requirements.txt",
    "schema.sql",
    "backup_catalog.py",
    "build_cloud_release.py",
    "build_release.py",
    "catalog_api.py",
    "docker_entrypoint.py",
    "import_amway.py",
    "refresh_catalog.py",
    "seed_catalog.py",
    "verify_install.py",
    "data/raw/amway-all-products.json",
    "data/products.sqlite3",
    "deploy/lightsail/.env.example",
    "deploy/lightsail/Caddyfile",
    "deploy/lightsail/README.md",
    "deploy/lightsail/compose.production.yaml",
    "deploy/lightsail/deploy.sh",
    "deploy/lightsail/provision-ubuntu.sh",
    "deploy/lightsail/remote-deploy.sh",
    "deploy/lightsail/systemd/nutrilite-backup.service",
    "deploy/lightsail/systemd/nutrilite-backup.timer",
    "deploy/lightsail/systemd/nutrilite-refresh.service",
    "deploy/lightsail/systemd/nutrilite-refresh.timer",
    "tests/test_cloud_runtime.py",
    "tests/test_cloud_release.py",
)


def copy_files(staging: Path) -> None:
    for relative in PACKAGE_FILES:
        source = ROOT / relative
        if not source.is_file():
            raise FileNotFoundError(f"Cloud release input is missing: {source}")
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def build_manifest(staging: Path) -> dict[str, object]:
    catalog = validate_catalog(ROOT / "data" / "products.sqlite3")
    files: dict[str, dict[str, object]] = {}
    for path in sorted(item for item in staging.rglob("*") if item.is_file()):
        relative = path.relative_to(staging).as_posix()
        files[relative] = {
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
    return {
        "artifact_name": ARTIFACT_NAME,
        "artifact_version": ARTIFACT_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repository": "https://github.com/patrickhschulz/Nutrilite_Converter",
        "runtime": "Docker Compose on Ubuntu 24.04",
        "primary_target": "AWS Lightsail",
        "compatible_target": "DigitalOcean Droplet",
        "catalog_snapshot": {
            "active_total": catalog["active_total"],
            "active_nutrilite": catalog["active_nutrilite"],
            "active_xs": catalog["active_xs"],
            "latest_fetched_at": catalog["latest_fetched_at"],
        },
        "privacy": {
            "contains_client_data": False,
            "contains_client_images": False,
            "catalog_scope": "public Amway US storefront products",
        },
        "files": files,
    }


def build_cloud_release(output_directory: Path) -> dict[str, object]:
    output_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nutrilite-cloud-release-") as temporary:
        staging = Path(temporary) / ARTIFACT_NAME
        staging.mkdir()
        copy_files(staging)
        manifest = build_manifest(staging)
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        archive = output_directory / f"{ARTIFACT_NAME}.zip"
        write_zip(staging, archive, archive_root=ARTIFACT_NAME)
        external_manifest = output_directory / f"{ARTIFACT_NAME}.manifest.json"
        shutil.copy2(manifest_path, external_manifest)
        checksum = sha256_file(archive)
        checksum_path = output_directory / f"{ARTIFACT_NAME}.sha256"
        checksum_path.write_text(
            f"{checksum}  {archive.name}\n", encoding="utf-8"
        )

    return {
        "artifact": str(archive),
        "sha256": checksum,
        "manifest": str(external_manifest),
        "checksum_file": str(checksum_path),
        "size_bytes": archive.stat().st_size,
        "catalog": manifest["catalog_snapshot"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "release")
    args = parser.parse_args()
    try:
        result = build_cloud_release(args.output.resolve())
    except (OSError, RuntimeError, ValueError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, indent=2))
        return 1
    print(json.dumps({"status": "built", **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
