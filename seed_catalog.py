#!/usr/bin/env python3
"""Reconstruct the SQLite product catalog from the bundled offline snapshot."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from import_amway import (
    SOURCE,
    absolute_product_url,
    import_products,
    initialize_database,
    record_refresh,
)
from refresh_catalog import validate_catalog


ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = ROOT / "data" / "products.sqlite3"
DEFAULT_SCHEMA = ROOT / "schema.sql"
DEFAULT_SEED = ROOT / "data" / "seed" / "amway-all-products.json.gz"


class SeedError(RuntimeError):
    """Raised when a seed snapshot cannot safely reconstruct the catalog."""


def read_seed(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SeedError(f"Catalog seed not found: {path}")
    try:
        if path.suffix == ".gz":
            with gzip.open(path, "rt", encoding="utf-8") as seed_file:
                payload = json.load(seed_file)
        else:
            payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SeedError(f"Unable to read catalog seed {path}: {error}") from error

    products = payload.get("products")
    if not isinstance(products, list) or not products:
        raise SeedError("Catalog seed contains no products")
    if payload.get("count") != len(products):
        raise SeedError(
            "Catalog seed count mismatch: "
            f"declared {payload.get('count')}, found {len(products)}"
        )

    codes = [str(product.get("code", "")).casefold() for product in products]
    if any(not code for code in codes) or len(codes) != len(set(codes)):
        raise SeedError("Catalog seed has a blank or duplicate SKU")
    try:
        urls = [absolute_product_url(product).casefold() for product in products]
    except (KeyError, TypeError) as error:
        raise SeedError(f"Catalog seed contains an invalid product URL: {error}") from error
    if len(urls) != len(set(urls)):
        raise SeedError("Catalog seed has duplicate canonical product URLs")

    fetched_at = payload.get("fetched_at")
    if not isinstance(fetched_at, str) or not fetched_at:
        raise SeedError("Catalog seed has no fetched_at timestamp")
    return payload


def backup_name(database: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return database.with_name(f"{database.stem}.backup-{timestamp}{database.suffix}")


def rebuild_database(
    database: Path = DEFAULT_DATABASE,
    schema: Path = DEFAULT_SCHEMA,
    seed: Path = DEFAULT_SEED,
    *,
    keep_backup: bool = True,
) -> dict[str, object]:
    """Build and validate a temporary database, then atomically install it."""

    payload = read_seed(seed)
    products = payload["products"]
    fetched_at = payload["fetched_at"]
    database.parent.mkdir(parents=True, exist_ok=True)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{database.stem}.", suffix=".sqlite3", dir=database.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with closing(sqlite3.connect(temporary)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            initialize_database(connection, schema)
            removed = import_products(
                connection, products, fetched_at, purge_after_misses=0
            )
            record_refresh(connection, fetched_at, removed)
            connection.commit()
        result = validate_catalog(temporary)

        backup: Path | None = None
        if database.exists() and keep_backup:
            backup = backup_name(database)
            shutil.copy2(database, backup)
        os.replace(temporary, database)
        result["source"] = SOURCE
        result["seed"] = str(seed)
        result["backup"] = str(backup) if backup else None
        return result
    finally:
        temporary.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED)
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="replace an existing database without keeping a timestamped backup",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = rebuild_database(
            args.database,
            args.schema,
            args.seed,
            keep_backup=not args.no_backup,
        )
    except (SeedError, OSError, sqlite3.Error, RuntimeError) as error:
        print(f"Catalog reconstruction failed: {error}")
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
