#!/usr/bin/env python3
"""Import the complete public Amway US product catalog into SQLite.

Uses only Python's standard library. The storefront issues an anonymous session
token, which is then used with its public product-search API.
"""

from __future__ import annotations

import argparse
import gzip
import html
import json
import os
import sqlite3
import ssl
import sys
import tempfile
from contextlib import closing
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SOURCE = "amway-us"
CATALOG_URL = "https://www.amway.com/en_US/Shop"
SESSION_URL = "https://www.amway.com/session"
API_ROOT = "https://front-proxy-prod.prod.amer.amway.net"
SEARCH_URL = API_ROOT + "/ProductSearch/1.0.0/v2/lynx/products/search"
IMAGE_ROOT = "https://www.amway.com"
PAGE_SIZE = 400


def tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    # python.org macOS builds can be installed without running their certificate
    # setup script. Use the OS trust bundle when Python's configured bundle is
    # absent; verification remains enabled.
    system_bundle = Path("/etc/ssl/cert.pem")
    if ssl.get_default_verify_paths().cafile is None and system_bundle.exists():
        context.load_verify_locations(cafile=system_bundle)
    return context


TLS_CONTEXT = tls_context()


def request_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=45, context=TLS_CONTEXT) as response:
            body = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return json.loads(body)
    except (urllib.error.URLError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Unable to read {url}: {error}") from error


def get_session() -> dict[str, Any]:
    return request_json(
        SESSION_URL,
        {
            "Accept": "application/json",
            "User-Agent": "NutriliteConverter/1.0 (+local product catalog)",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": CATALOG_URL,
        },
    )


def fetch_catalog() -> list[dict[str, Any]]:
    session = get_session()
    token = session["securityToken"]
    headers = {
        "Accept": "application/json",
        "Authorization": f"{token['tokenType']} {token['accessToken']}",
        "id_token": token["idToken"],
        "Origin": API_ROOT,
        "User-Agent": "NutriliteConverter/1.0 (+local product catalog)",
    }

    products: dict[str, dict[str, Any]] = {}
    received_count = 0
    page = 0
    expected_total: int | None = None
    while True:
        query = urllib.parse.urlencode(
            {
                "query": "",
                "pageSize": PAGE_SIZE,
                "currentPage": page,
                "lang": "en_US",
            }
        )
        payload = request_json(f"{SEARCH_URL}?{query}", headers)
        pagination = payload.get("pagination", {})
        if expected_total is None:
            expected_total = int(pagination.get("totalResults", 0))
        for product in payload.get("products", []):
            received_count += 1
            code = product["code"].casefold()
            if code in products:
                raise RuntimeError(f"Catalog returned duplicate SKU {product['code']}")
            products[code] = product

        page += 1
        if page >= int(pagination.get("totalPages", 1)):
            break

    if expected_total is None or received_count != expected_total:
        raise RuntimeError(
            "Catalog refresh was incomplete: "
            f"expected {expected_total or 0} products, received {received_count}"
        )
    urls = [absolute_product_url(product) for product in products.values()]
    if len(urls) != len(set(urls)):
        raise RuntimeError("Catalog returned multiple SKUs with the same product URL")
    return sorted(products.values(), key=lambda product: product["code"].casefold())


def money_to_cents(price: dict[str, Any] | None) -> int | None:
    if not price or price.get("value") is None:
        return None
    return round(float(price["value"]) * 100)


def clean_text(value: str | None) -> str | None:
    return html.unescape(value).strip() if value else None


def primary_image(product: dict[str, Any]) -> str | None:
    images = product.get("images", [])
    preferred = next(
        (image for image in images if image.get("format") == "product"),
        images[0] if images else None,
    )
    if not preferred:
        return None
    image_url = preferred.get("publicUrl") or preferred.get("url")
    if not image_url:
        return None
    return urllib.parse.urljoin(IMAGE_ROOT, image_url)


def absolute_product_url(product: dict[str, Any]) -> str:
    return urllib.parse.urljoin("https://www.amway.com/en_US/", product["url"].lstrip("/"))


def initialize_database(connection: sqlite3.Connection, schema_path: Path) -> None:
    products_table_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'products'"
    ).fetchone()
    if products_table_exists:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(products)")
        }
        if "is_active" not in columns:
            connection.execute(
                "ALTER TABLE products ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1 "
                "CHECK (is_active IN (0, 1))"
            )
        if "consecutive_misses" not in columns:
            connection.execute(
                "ALTER TABLE products ADD COLUMN consecutive_misses INTEGER "
                "NOT NULL DEFAULT 0 CHECK (consecutive_misses >= 0)"
            )
        if "discontinued_at" not in columns:
            connection.execute(
                "ALTER TABLE products ADD COLUMN discontinued_at TEXT"
            )
    connection.executescript(schema_path.read_text(encoding="utf-8"))


def import_products(
    connection: sqlite3.Connection,
    products: list[dict[str, Any]],
    fetched_at: str,
    purge_after_misses: int,
) -> int:
    # A missing product is immediately labeled discontinued. Requiring complete,
    # consecutive catalog refreshes before purging avoids treating a partial API
    # response or short-lived delisting as a permanent discontinuation.
    connection.execute(
        """
        UPDATE products
        SET is_active = 0,
            consecutive_misses = consecutive_misses + 1,
            discontinued_at = COALESCE(discontinued_at, ?)
        WHERE source = ?
        """,
        (fetched_at, SOURCE),
    )
    for product in products:
        retail_price = product.get("retailPrice") or product.get("price")
        member_price = product.get("iboDefaultPrice")
        currency = (retail_price or member_price or {}).get("currencyIso")
        connection.execute(
            """
            INSERT INTO products (
                source, source_code, name, brand, description, product_url,
                image_url, retail_price_cents, member_price_cents, currency,
                is_bundle, is_purchasable, is_sellable, is_active,
                consecutive_misses, discontinued_at, stock_disposition,
                fetched_at, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (source, source_code) DO UPDATE SET
                name = excluded.name,
                brand = excluded.brand,
                description = excluded.description,
                product_url = excluded.product_url,
                image_url = excluded.image_url,
                retail_price_cents = excluded.retail_price_cents,
                member_price_cents = excluded.member_price_cents,
                currency = excluded.currency,
                is_bundle = excluded.is_bundle,
                is_purchasable = excluded.is_purchasable,
                is_sellable = excluded.is_sellable,
                is_active = excluded.is_active,
                consecutive_misses = excluded.consecutive_misses,
                discontinued_at = excluded.discontinued_at,
                stock_disposition = excluded.stock_disposition,
                fetched_at = excluded.fetched_at,
                raw_json = excluded.raw_json
            """,
            (
                SOURCE,
                product["code"],
                clean_text(product["name"]),
                clean_text(product.get("brandName")),
                clean_text(product.get("description")),
                absolute_product_url(product),
                primary_image(product),
                money_to_cents(retail_price),
                money_to_cents(member_price),
                currency,
                int(bool(product.get("bundle"))),
                int(bool(product.get("purchasable"))),
                int(bool(product.get("sellable"))),
                1,
                0,
                None,
                product.get("stock", {}).get("shipDisposition"),
                fetched_at,
                json.dumps(product, ensure_ascii=False, separators=(",", ":")),
            ),
        )
        product_id = connection.execute(
            "SELECT id FROM products WHERE source = ? AND source_code = ?",
            (SOURCE, product["code"]),
        ).fetchone()[0]

        connection.execute(
            "DELETE FROM product_categories WHERE product_id = ?", (product_id,)
        )
        for category in product.get("categories", []):
            name = clean_text(category.get("name"))
            if not name:
                continue
            connection.execute(
                "INSERT INTO categories(name) VALUES (?) ON CONFLICT DO NOTHING",
                (name,),
            )
            category_id = connection.execute(
                "SELECT id FROM categories WHERE name = ?", (name,)
            ).fetchone()[0]
            connection.execute(
                "INSERT OR IGNORE INTO product_categories VALUES (?, ?)",
                (product_id, category_id),
            )

        connection.execute(
            "DELETE FROM product_sources WHERE product_id = ? AND source_type = 'catalog_url'",
            (product_id,),
        )
        for source_type, source_ref in (
            ("catalog_url", CATALOG_URL),
            ("product_url", absolute_product_url(product)),
        ):
            connection.execute(
                """
                INSERT INTO product_sources (
                    product_id, source_type, source_ref, captured_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT (product_id, source_type, source_ref)
                DO UPDATE SET captured_at = excluded.captured_at
                """,
                (product_id, source_type, source_ref, fetched_at),
            )

    if purge_after_misses == 0:
        return 0

    discontinued = connection.execute(
        """
        SELECT source_code, name, consecutive_misses
        FROM products
        WHERE source = ? AND is_active = 0 AND consecutive_misses >= ?
        """,
        (SOURCE, purge_after_misses),
    ).fetchall()
    for source_code, name, misses in discontinued:
        connection.execute(
            """
            INSERT INTO catalog_events (
                source, source_code, product_name, event_type,
                occurred_at, details_json
            ) VALUES (?, ?, ?, 'discontinued_removed', ?, ?)
            """,
            (
                SOURCE,
                source_code,
                name,
                fetched_at,
                json.dumps({"consecutive_complete_refresh_misses": misses}),
            ),
        )
    connection.execute(
        """
        DELETE FROM products
        WHERE source = ? AND is_active = 0 AND consecutive_misses >= ?
        """,
        (SOURCE, purge_after_misses),
    )
    return len(discontinued)

def write_snapshot(path: Path, products: list[dict[str, Any]], fetched_at: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": CATALOG_URL,
        "fetched_at": fetched_at,
        "scope": "all Amway US storefront products",
        "count": len(products),
        "products": products,
    }
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as temporary:
        temporary.write(serialized)
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    os.replace(temporary_path, path)


def record_refresh(
    connection: sqlite3.Connection,
    fetched_at: str,
    removed_discontinued: int,
) -> tuple[int, int, int]:
    active_total = connection.execute(
        "SELECT count(*) FROM products WHERE source = ? AND is_active = 1",
        (SOURCE,),
    ).fetchone()[0]
    active_nutrilite = connection.execute(
        """
        SELECT count(*) FROM products
        WHERE source = ? AND is_active = 1 AND lower(brand) = 'nutrilite'
        """,
        (SOURCE,),
    ).fetchone()[0]
    active_xs = connection.execute(
        """
        SELECT count(*) FROM products
        WHERE source = ? AND is_active = 1
          AND (lower(brand) = 'xs' OR lower(brand) LIKE 'xs %')
        """,
        (SOURCE,),
    ).fetchone()[0]
    connection.execute(
        """
        INSERT INTO catalog_refreshes (
            source, fetched_at, active_total, active_nutrilite, active_xs,
            removed_discontinued
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            SOURCE,
            fetched_at,
            active_total,
            active_nutrilite,
            active_xs,
            removed_discontinued,
        ),
    )
    return active_total, active_nutrilite, active_xs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("data/products.sqlite3"))
    parser.add_argument("--schema", type=Path, default=Path("schema.sql"))
    parser.add_argument(
        "--snapshot", type=Path, default=Path("data/raw/amway-all-products.json")
    )
    parser.add_argument(
        "--purge-after-misses",
        type=int,
        default=2,
        metavar="N",
        help=(
            "remove products after N consecutive complete refreshes where they "
            "are absent; use 0 to retain discontinued products (default: 2)"
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.purge_after_misses < 0:
        print("--purge-after-misses must be zero or greater", file=sys.stderr)
        return 2
    products = fetch_catalog()
    if not products:
        print("No Amway products were returned; database unchanged.", file=sys.stderr)
        return 1

    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    args.database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(args.database)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        initialize_database(connection, args.schema)
        removed = import_products(
            connection, products, fetched_at, args.purge_after_misses
        )
        active_total, active_nutrilite, active_xs = record_refresh(
            connection, fetched_at, removed
        )
        connection.commit()
    write_snapshot(args.snapshot, products, fetched_at)
    print(
        f"Imported {active_total} active products into {args.database} "
        f"({active_nutrilite} Nutrilite, {active_xs} XS); "
        f"removed {removed} confirmed discontinued products"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
