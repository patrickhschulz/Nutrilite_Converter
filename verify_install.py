#!/usr/bin/env python3
"""Verify a Nutrilite Converter installation and its catalog invariants."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

from refresh_catalog import CatalogError, validate_catalog
from seed_catalog import read_seed


ROOT = Path(__file__).resolve().parent
REQUIRED_TABLES = {
    "products",
    "categories",
    "product_categories",
    "product_sources",
    "catalog_events",
    "catalog_refreshes",
    "label_panels",
    "nutrients",
    "product_nutrients",
    "ingredients",
}
REQUIRED_VIEWS = {"product_comparison", "nutrilite_xs_catalog"}


class VerificationError(RuntimeError):
    """Raised when an installation does not meet a release invariant."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise VerificationError(f"Release manifest not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise VerificationError(f"Invalid release manifest {path}: {error}") from error


def verify_manifest_files(root: Path, manifest: dict[str, Any]) -> int:
    verified = 0
    for relative, metadata in manifest.get("files", {}).items():
        if not metadata.get("immutable", True):
            continue
        path = root / relative
        if not path.is_file():
            raise VerificationError(f"Manifest file is missing: {relative}")
        expected_size = int(metadata["size_bytes"])
        if path.stat().st_size != expected_size:
            raise VerificationError(f"Manifest size mismatch: {relative}")
        if sha256_file(path) != metadata["sha256"]:
            raise VerificationError(f"Manifest checksum mismatch: {relative}")
        verified += 1
    return verified


def database_checks(database: Path, expected_schema_version: int) -> dict[str, Any]:
    base = validate_catalog(database)
    with closing(sqlite3.connect(database)) as connection:
        schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        objects = connection.execute(
            "SELECT type, name FROM sqlite_master WHERE type IN ('table', 'view')"
        ).fetchall()
        tables = {name for kind, name in objects if kind == "table"}
        views = {name for kind, name in objects if kind == "view"}
        missing_tables = sorted(REQUIRED_TABLES - tables)
        missing_views = sorted(REQUIRED_VIEWS - views)
        duplicate_skus = connection.execute(
            """
            SELECT count(*) FROM (
                SELECT source, lower(source_code), count(*) AS copies
                FROM products
                GROUP BY source, lower(source_code)
                HAVING copies > 1
            )
            """
        ).fetchone()[0]
        duplicate_urls = connection.execute(
            """
            SELECT count(*) FROM (
                SELECT source, lower(product_url), count(*) AS copies
                FROM products
                GROUP BY source, lower(product_url)
                HAVING copies > 1
            )
            """
        ).fetchone()[0]
        malformed_discontinued = connection.execute(
            """
            SELECT count(*) FROM products
            WHERE (is_active = 0 AND discontinued_at IS NULL)
               OR (is_active = 1 AND consecutive_misses <> 0)
            """
        ).fetchone()[0]
        blank_identity = connection.execute(
            """
            SELECT count(*) FROM products
            WHERE trim(source_code) = '' OR trim(name) = '' OR trim(product_url) = ''
            """
        ).fetchone()[0]
        fts_rows = connection.execute("SELECT count(*) FROM products_fts").fetchone()[0]
        product_rows = connection.execute("SELECT count(*) FROM products").fetchone()[0]
        refresh_rows = connection.execute(
            "SELECT count(*) FROM catalog_refreshes"
        ).fetchone()[0]

    if schema_version != expected_schema_version:
        raise VerificationError(
            f"Schema version is {schema_version}; expected {expected_schema_version}"
        )
    if missing_tables:
        raise VerificationError(f"Missing database tables: {', '.join(missing_tables)}")
    if missing_views:
        raise VerificationError(f"Missing database views: {', '.join(missing_views)}")
    if duplicate_skus:
        raise VerificationError(f"Found {duplicate_skus} duplicate case-insensitive SKUs")
    if duplicate_urls:
        raise VerificationError(f"Found {duplicate_urls} duplicate canonical URLs")
    if malformed_discontinued:
        raise VerificationError(
            f"Found {malformed_discontinued} inconsistent lifecycle records"
        )
    if blank_identity:
        raise VerificationError(f"Found {blank_identity} products with blank identity fields")
    if fts_rows != product_rows:
        raise VerificationError(
            f"Full-text index has {fts_rows} rows for {product_rows} products"
        )

    base.update(
        {
            "schema_version": schema_version,
            "product_rows": product_rows,
            "catalog_refresh_rows": refresh_rows,
            "duplicate_skus": duplicate_skus,
            "duplicate_urls": duplicate_urls,
            "lifecycle_errors": malformed_discontinued,
            "fts_rows": fts_rows,
        }
    )
    return base


def audit_install(
    root: Path = ROOT,
    *,
    manifest_path: Path | None = None,
    strict_snapshot: bool = False,
    skip_manifest: bool = False,
) -> dict[str, Any]:
    if sys.version_info < (3, 11):
        raise VerificationError("Python 3.11 or newer is required")

    manifest_file = manifest_path or root / "manifest.json"
    manifest: dict[str, Any] = {}
    verified_files = 0
    if not skip_manifest:
        manifest = load_manifest(manifest_file)
        verified_files = verify_manifest_files(root, manifest)

    schema_version = int(manifest.get("schema_version", 1))
    database = root / "data" / "products.sqlite3"
    result = database_checks(database, schema_version)

    seed_path = root / "data" / "seed" / "amway-all-products.json.gz"
    if seed_path.is_file():
        seed = read_seed(seed_path)
        result["seed_products"] = seed["count"]
        result["seed_fetched_at"] = seed["fetched_at"]
        if strict_snapshot:
            expected = manifest.get("catalog_snapshot", {})
            comparisons = {
                "active_total": result["active_total"],
                "active_nutrilite": result["active_nutrilite"],
                "active_xs": result["active_xs"],
                "latest_fetched_at": result["latest_fetched_at"],
            }
            for field, actual in comparisons.items():
                if field in expected and actual != expected[field]:
                    raise VerificationError(
                        f"Catalog snapshot mismatch for {field}: "
                        f"found {actual!r}, expected {expected[field]!r}"
                    )

    result["python"] = ".".join(map(str, sys.version_info[:3]))
    result["manifest_files_verified"] = verified_files
    result["status"] = "ok"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--strict-snapshot",
        action="store_true",
        help="require database counts and timestamp to match the packaged snapshot",
    )
    parser.add_argument(
        "--skip-manifest",
        action="store_true",
        help="validate the database without checking release file checksums",
    )
    args = parser.parse_args()
    try:
        result = audit_install(
            args.root.resolve(),
            manifest_path=args.manifest,
            strict_snapshot=args.strict_snapshot,
            skip_manifest=args.skip_manifest,
        )
    except (VerificationError, CatalogError, OSError, sqlite3.Error, RuntimeError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, indent=2))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
