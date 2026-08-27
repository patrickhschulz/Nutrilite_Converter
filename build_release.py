#!/usr/bin/env python3
"""Build the self-contained Nutrilite_Converter release artifact."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import stat
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from seed_catalog import read_seed, rebuild_database
from verify_install import audit_install, sha256_file


ROOT = Path(__file__).resolve().parent
ARTIFACT_NAME = "Nutrilite_Converter"
ARTIFACT_VERSION = "1.0.0"
SCHEMA_VERSION = 1

PACKAGE_FILES = (
    "Nutrilite_Converter.md",
    "README.md",
    "requirements.txt",
    "schema.sql",
    "bootstrap.py",
    "build_release.py",
    "create_client.py",
    "import_amway.py",
    "query_products.py",
    "refresh_catalog.py",
    "seed_catalog.py",
    "verify_install.py",
    "templates/client_schema.sql",
    "tests/test_refresh_catalog.py",
    "tests/test_query_products.py",
    "tests/test_portable_release.py",
)


def deterministic_gzip(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_file, destination.open("wb") as output_file:
        with gzip.GzipFile(
            filename=source.name,
            mode="wb",
            fileobj=output_file,
            mtime=0,
        ) as compressed:
            shutil.copyfileobj(input_file, compressed)


def copy_package_files(staging: Path) -> None:
    for relative in PACKAGE_FILES:
        source = ROOT / relative
        if not source.is_file():
            raise FileNotFoundError(f"Release input is missing: {source}")
        destination = staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def build_manifest(staging: Path, catalog: dict[str, object]) -> dict[str, object]:
    files: dict[str, dict[str, object]] = {}
    for path in sorted(item for item in staging.rglob("*") if item.is_file()):
        relative = path.relative_to(staging).as_posix()
        files[relative] = {
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
            "immutable": relative != "data/products.sqlite3",
        }
    return {
        "artifact_name": ARTIFACT_NAME,
        "artifact_version": ARTIFACT_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "repository": "https://github.com/patrickhschulz/Nutrilite_Converter",
        "python_minimum": "3.11",
        "schema_version": SCHEMA_VERSION,
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


def write_zip(
    staging: Path,
    destination: Path,
    archive_root: str = ARTIFACT_NAME,
) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}.", suffix=".zip", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for path in sorted(item for item in staging.rglob("*") if item.is_file()):
                relative = Path(archive_root) / path.relative_to(staging)
                info = zipfile.ZipInfo.from_file(path, relative.as_posix())
                mode = 0o755 if path.suffix in {".py", ".sh"} else 0o644
                info.external_attr = (stat.S_IFREG | mode) << 16
                with path.open("rb") as source:
                    archive.writestr(
                        info,
                        source.read(),
                        compress_type=zipfile.ZIP_DEFLATED,
                        compresslevel=9,
                    )
        os.replace(temporary, destination)
        destination.chmod(0o644)
    finally:
        temporary.unlink(missing_ok=True)


def build_release(output_directory: Path) -> dict[str, object]:
    raw_snapshot = ROOT / "data" / "raw" / "amway-all-products.json"
    if not raw_snapshot.is_file():
        raise FileNotFoundError(f"Catalog source snapshot is missing: {raw_snapshot}")

    output_directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nutrilite-release-") as temporary_dir:
        staging = Path(temporary_dir) / ARTIFACT_NAME
        staging.mkdir()
        copy_package_files(staging)

        seed = staging / "data" / "seed" / "amway-all-products.json.gz"
        deterministic_gzip(raw_snapshot, seed)
        seed_payload = read_seed(seed)

        database = staging / "data" / "products.sqlite3"
        catalog = rebuild_database(
            database,
            staging / "schema.sql",
            seed,
            keep_backup=False,
        )
        if catalog["active_total"] != seed_payload["count"]:
            raise RuntimeError(
                "Reconstructed catalog count does not match the source snapshot"
            )

        manifest = build_manifest(staging, catalog)
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        audit_install(staging, strict_snapshot=True)

        archive = output_directory / f"{ARTIFACT_NAME}.zip"
        write_zip(staging, archive)

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
        result = build_release(args.output.resolve())
    except (OSError, RuntimeError, ValueError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, indent=2))
        return 1
    print(json.dumps({"status": "built", **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
