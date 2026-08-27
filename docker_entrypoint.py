#!/usr/bin/env python3
"""Initialize persistent catalog storage and start the HTTP service."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from refresh_catalog import ensure_schema, validate_catalog
from seed_catalog import rebuild_database


ROOT = Path(__file__).resolve().parent


def main() -> int:
    database = Path(
        os.environ.get(
            "CATALOG_DATABASE", "/var/lib/nutrilite/products.sqlite3"
        )
    )
    schema = Path(os.environ.get("CATALOG_SCHEMA", str(ROOT / "schema.sql")))
    seed = Path(
        os.environ.get(
            "CATALOG_SEED", str(ROOT / "data" / "raw" / "amway-all-products.json")
        )
    )
    host = os.environ.get("HOST", "0.0.0.0")
    port = os.environ.get("PORT", "8000")

    try:
        if database.is_file():
            ensure_schema(database, schema)
            status = validate_catalog(database)
        else:
            status = rebuild_database(
                database, schema, seed, keep_backup=False
            )
    except (OSError, RuntimeError) as error:
        print(json.dumps({"status": "error", "error": str(error)}))
        return 1

    print(json.dumps({"status": "catalog-ready", **status}))
    os.execv(
        sys.executable,
        [
            sys.executable,
            str(ROOT / "catalog_api.py"),
            "--host",
            host,
            "--port",
            port,
            "--database",
            str(database),
        ],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
