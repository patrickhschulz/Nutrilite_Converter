#!/usr/bin/env python3
"""Refresh the local Amway catalog when it is stale and validate the result.

This is the stable entry point for cron, launchd, Task Scheduler, containers,
and hosted job schedulers. It delegates network ingestion to import_amway.py.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = ROOT / "data" / "products.sqlite3"
DEFAULT_SCHEMA = ROOT / "schema.sql"
DEFAULT_SNAPSHOT = ROOT / "data" / "raw" / "amway-all-products.json"
DEFAULT_IMPORTER = ROOT / "import_amway.py"
DEFAULT_LOCK = ROOT / "data" / ".catalog-refresh.lock"
DEFAULT_MAX_AGE_HOURS = 7 * 24


class CatalogError(RuntimeError):
    """Raised when the local catalog cannot be trusted."""


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def latest_catalog_timestamp(database: Path) -> datetime | None:
    if not database.is_file():
        return None
    with closing(sqlite3.connect(database)) as connection:
        products_exist = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='products'"
        ).fetchone()
        if not products_exist:
            return None
        row = connection.execute(
            "SELECT max(fetched_at) FROM products WHERE is_active = 1"
        ).fetchone()
    return parse_timestamp(row[0]) if row and row[0] else None


def catalog_age_hours(database: Path, now: datetime | None = None) -> float | None:
    latest = latest_catalog_timestamp(database)
    if latest is None:
        return None
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return max(0.0, (current - latest).total_seconds() / 3600.0)


def ensure_schema(database: Path, schema: Path) -> None:
    if not schema.is_file():
        raise CatalogError(f"Schema not found: {schema}")
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(schema.read_text(encoding="utf-8"))
        connection.commit()


def validate_catalog(database: Path) -> dict[str, object]:
    if not database.is_file():
        raise CatalogError(f"Catalog database not found: {database}")
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_violations = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
        active_total = connection.execute(
            "SELECT count(*) FROM products WHERE is_active = 1"
        ).fetchone()[0]
        active_nutrilite = connection.execute(
            """
            SELECT count(*) FROM products
            WHERE is_active = 1 AND lower(brand) = 'nutrilite'
            """
        ).fetchone()[0]
        active_xs = connection.execute(
            """
            SELECT count(*) FROM products
            WHERE is_active = 1
              AND (lower(brand) = 'xs' OR lower(brand) LIKE 'xs %')
            """
        ).fetchone()[0]
        view_count = connection.execute(
            "SELECT count(*) FROM nutrilite_xs_catalog"
        ).fetchone()[0]
        latest = connection.execute(
            "SELECT max(fetched_at) FROM products WHERE is_active = 1"
        ).fetchone()[0]
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

    if integrity != "ok":
        raise CatalogError(f"SQLite integrity check failed: {integrity}")
    if foreign_key_violations:
        raise CatalogError(
            f"Catalog has {len(foreign_key_violations)} foreign-key violations"
        )
    if active_total <= 0:
        raise CatalogError("Catalog has no active products")
    if active_nutrilite <= 0:
        raise CatalogError("Catalog has no active Nutrilite products")
    if active_xs <= 0:
        raise CatalogError("Catalog has no active XS products")
    if view_count != active_nutrilite + active_xs:
        raise CatalogError(
            "Nutrilite/XS view count does not match the validated brand counts"
        )
    if duplicate_skus:
        raise CatalogError(
            f"Catalog has {duplicate_skus} duplicate case-insensitive SKUs"
        )
    if duplicate_urls:
        raise CatalogError(
            f"Catalog has {duplicate_urls} duplicate canonical product URLs"
        )

    return {
        "database": str(database),
        "active_total": active_total,
        "active_nutrilite": active_nutrilite,
        "active_xs": active_xs,
        "nutrilite_xs_view": view_count,
        "latest_fetched_at": latest,
        "integrity": integrity,
        "duplicate_skus": duplicate_skus,
        "duplicate_urls": duplicate_urls,
    }


@contextmanager
def refresh_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        try:
            owner = path.read_text(encoding="utf-8").strip()
        except OSError:
            owner = "unknown process"
        raise CatalogError(f"Another catalog refresh holds {path}: {owner}") from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as lock_file:
            lock_file.write(
                json.dumps(
                    {
                        "pid": os.getpid(),
                        "created_at": datetime.now(timezone.utc).isoformat(
                            timespec="seconds"
                        ),
                    }
                )
            )
        yield
    finally:
        path.unlink(missing_ok=True)


def run_importer(args: argparse.Namespace) -> None:
    command = [
        sys.executable,
        str(args.importer),
        "--database",
        str(args.database),
        "--schema",
        str(args.schema),
        "--snapshot",
        str(args.snapshot),
        "--purge-after-misses",
        str(args.purge_after_misses),
    ]
    subprocess.run(command, cwd=ROOT, check=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--importer", type=Path, default=DEFAULT_IMPORTER)
    parser.add_argument("--lock-file", type=Path, default=DEFAULT_LOCK)
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=DEFAULT_MAX_AGE_HOURS,
        help="refresh when the newest active catalog row is this old (default: 168)",
    )
    parser.add_argument(
        "--purge-after-misses",
        type=int,
        default=2,
        help="purge products after this many complete-refresh misses (default: 2)",
    )
    parser.add_argument("--force", action="store_true", help="refresh regardless of age")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="validate and report freshness without using the network",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.max_age_hours < 0:
        print("--max-age-hours must be zero or greater", file=sys.stderr)
        return 2
    if args.purge_after_misses < 0:
        print("--purge-after-misses must be zero or greater", file=sys.stderr)
        return 2

    try:
        ensure_schema(args.database, args.schema)
        age = catalog_age_hours(args.database)

        if args.check_only:
            result = validate_catalog(args.database)
            result["age_hours"] = round(age, 2) if age is not None else None
            result["stale"] = age is None or age >= args.max_age_hours
            print(json.dumps(result, indent=2))
            return 1 if result["stale"] else 0

        if not args.force and age is not None and age < args.max_age_hours:
            result = validate_catalog(args.database)
            print(
                "Catalog is fresh; no network refresh needed "
                f"({age:.1f} hours old, {result['active_nutrilite']} Nutrilite, "
                f"{result['active_xs']} XS)."
            )
            return 0

        with refresh_lock(args.lock_file):
            # Check again after acquiring the lock in case another process just
            # completed the refresh while this process was waiting to start.
            age = catalog_age_hours(args.database)
            if not args.force and age is not None and age < args.max_age_hours:
                result = validate_catalog(args.database)
            else:
                run_importer(args)
                result = validate_catalog(args.database)

        print(json.dumps(result, indent=2))
        return 0
    except (CatalogError, OSError, sqlite3.Error, subprocess.CalledProcessError) as error:
        print(f"Catalog refresh failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
