#!/usr/bin/env python3
"""Prepare and verify an extracted Nutrilite Converter release."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from create_client import create_client_database
from seed_catalog import rebuild_database
from verify_install import VerificationError, audit_install


ROOT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rebuild-catalog",
        action="store_true",
        help="reconstruct the catalog from the bundled offline seed",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="contact Amway after setup and refresh the catalog",
    )
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="make no changes; verify the extracted release and catalog",
    )
    parser.add_argument(
        "--create-client",
        metavar="NAME",
        help="also create an empty private supplement database for NAME",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    database = ROOT / "data" / "products.sqlite3"
    manifest_exists = (ROOT / "manifest.json").is_file()

    if args.verify_only and (args.rebuild_catalog or args.refresh or args.create_client):
        print("--verify-only cannot be combined with setup-changing options")
        return 2

    try:
        rebuilt = False
        if not args.verify_only and (args.rebuild_catalog or not database.is_file()):
            rebuild_database(
                database,
                ROOT / "schema.sql",
                ROOT / "data" / "seed" / "amway-all-products.json.gz",
                keep_backup=args.rebuild_catalog,
            )
            rebuilt = True

        result = audit_install(
            ROOT,
            strict_snapshot=rebuilt or args.verify_only,
            skip_manifest=not manifest_exists,
        )

        if args.refresh:
            subprocess.run(
                [sys.executable, str(ROOT / "refresh_catalog.py"), "--force"],
                cwd=ROOT,
                check=True,
            )
            result = audit_install(ROOT, skip_manifest=not manifest_exists)

        client_database: Path | None = None
        if args.create_client:
            client_database = create_client_database(
                args.create_client,
                ROOT / "Clients",
                ROOT / "templates" / "client_schema.sql",
            )

        output = {
            "status": "ready",
            "project_root": str(ROOT),
            "catalog": result,
            "client_database": str(client_database) if client_database else None,
            "next_command": f'{sys.executable} query_products.py "vitamin d"',
        }
        print(json.dumps(output, indent=2))
        return 0
    except (
        VerificationError,
        FileExistsError,
        OSError,
        RuntimeError,
        sqlite3.Error,
        subprocess.CalledProcessError,
    ) as error:
        print(json.dumps({"status": "error", "error": str(error)}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
