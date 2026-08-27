#!/usr/bin/env python3
"""Quickly search the local product catalog."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", default="")
    parser.add_argument("--database", type=Path, default=Path("data/products.sqlite3"))
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    with sqlite3.connect(args.database) as connection:
        connection.row_factory = sqlite3.Row
        if args.query:
            rows = connection.execute(
                """
                SELECT p.source_code, p.name, p.brand,
                       p.retail_price_cents / 100.0 AS price, p.currency,
                       p.product_url
                FROM products_fts f
                JOIN products p ON p.id = f.rowid
                WHERE products_fts MATCH ?
                  AND p.is_active = 1
                ORDER BY rank
                LIMIT ?
                """,
                (args.query, args.limit),
            )
        else:
            rows = connection.execute(
                """
                SELECT source_code, name, brand,
                       retail_price_cents / 100.0 AS price, currency, product_url
                FROM products
                WHERE is_active = 1
                ORDER BY name
                LIMIT ?
                """,
                (args.limit,),
            )

        for row in rows:
            print(
                f"{row['source_code']:>7}  ${row['price']:>7.2f}  "
                f"{row['name']} ({row['brand'] or 'Unbranded'})"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
