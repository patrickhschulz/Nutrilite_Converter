#!/usr/bin/env python3
"""Create a private, empty client supplement database from the template."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import tempfile
import unicodedata
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_SCHEMA = ROOT / "templates" / "client_schema.sql"
DEFAULT_CLIENTS = ROOT / "Clients"


def client_slug(name: str) -> str:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", normalized).strip("-").lower()
    if not slug:
        raise ValueError("Client name must contain at least one letter or number")
    return slug


def create_client_database(
    name: str,
    clients_root: Path = DEFAULT_CLIENTS,
    schema: Path = DEFAULT_SCHEMA,
) -> Path:
    if not name.strip():
        raise ValueError("Client name cannot be blank")
    if not schema.is_file():
        raise FileNotFoundError(f"Client schema not found: {schema}")

    directory = clients_root / client_slug(name)
    database = directory / "client_supplements.sqlite3"
    if database.exists():
        raise FileExistsError(f"Client database already exists: {database}")
    directory.mkdir(parents=True, exist_ok=True)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".client-", suffix=".sqlite3", dir=directory
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with closing(sqlite3.connect(temporary)) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(schema.read_text(encoding="utf-8"))
            connection.execute(
                "INSERT INTO client_profile(id, name) VALUES (1, ?)",
                (name.strip(),),
            )
            connection.commit()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"Client database integrity check failed: {integrity}")
        os.replace(temporary, database)
    finally:
        temporary.unlink(missing_ok=True)
    return database


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", help="client display name")
    parser.add_argument("--clients-root", type=Path, default=DEFAULT_CLIENTS)
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA)
    args = parser.parse_args()
    try:
        database = create_client_database(args.name, args.clients_root, args.schema)
    except (ValueError, FileNotFoundError, FileExistsError, OSError, sqlite3.Error) as error:
        print(f"Unable to create client database: {error}")
        return 1
    print(json.dumps({"client": args.name.strip(), "database": str(database)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
