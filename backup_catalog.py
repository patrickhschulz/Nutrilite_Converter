#!/usr/bin/env python3
"""Create and retain validated compressed SQLite catalog backups."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from refresh_catalog import validate_catalog


ROOT = Path(__file__).resolve().parent
DEFAULT_DATABASE = ROOT / "data" / "products.sqlite3"
DEFAULT_OUTPUT = ROOT / "backups"
PREFIX = "products-"


def create_backup(database: Path, output: Path, retain: int = 14) -> dict[str, object]:
    if retain < 1:
        raise ValueError("retain must be at least 1")
    if not database.is_file():
        raise FileNotFoundError(f"Catalog database not found: {database}")
    output.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = output / f"{PREFIX}{timestamp}.sqlite3.gz"

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".catalog-backup-", suffix=".sqlite3", dir=output
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    compressed_temporary = destination.with_suffix(destination.suffix + ".tmp")
    try:
        with closing(sqlite3.connect(database)) as source:
            with closing(sqlite3.connect(temporary)) as target:
                source.backup(target)
        status = validate_catalog(temporary)
        with temporary.open("rb") as source, compressed_temporary.open("wb") as target:
            with gzip.GzipFile(
                filename=destination.name.removesuffix(".gz"),
                mode="wb",
                fileobj=target,
                mtime=0,
            ) as compressed:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    compressed.write(chunk)
        os.replace(compressed_temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
        compressed_temporary.unlink(missing_ok=True)

    backups = sorted(output.glob(f"{PREFIX}*.sqlite3.gz"), reverse=True)
    removed = []
    for expired in backups[retain:]:
        expired.unlink()
        removed.append(expired.name)
    return {
        "backup": str(destination),
        "size_bytes": destination.stat().st_size,
        "retained": min(len(backups), retain),
        "removed": removed,
        "catalog": status,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--retain", type=int, default=14)
    args = parser.parse_args()
    try:
        result = create_backup(args.database, args.output, args.retain)
    except (ValueError, FileNotFoundError, OSError, sqlite3.Error, RuntimeError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, indent=2))
        return 1
    print(json.dumps({"status": "backed-up", **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
