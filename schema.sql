PRAGMA foreign_keys = ON;
PRAGMA user_version = 1;

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    source_code TEXT NOT NULL,
    name TEXT NOT NULL,
    brand TEXT,
    description TEXT,
    product_url TEXT NOT NULL,
    image_url TEXT,
    retail_price_cents INTEGER,
    member_price_cents INTEGER,
    currency TEXT,
    is_bundle INTEGER NOT NULL DEFAULT 0 CHECK (is_bundle IN (0, 1)),
    is_purchasable INTEGER NOT NULL DEFAULT 0 CHECK (is_purchasable IN (0, 1)),
    is_sellable INTEGER NOT NULL DEFAULT 0 CHECK (is_sellable IN (0, 1)),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    consecutive_misses INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_misses >= 0),
    discontinued_at TEXT,
    stock_disposition TEXT,
    fetched_at TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    UNIQUE (source, source_code)
);

CREATE INDEX IF NOT EXISTS products_brand_idx ON products (brand);
CREATE INDEX IF NOT EXISTS products_price_idx ON products (retail_price_cents);
CREATE INDEX IF NOT EXISTS products_purchasable_idx ON products (is_purchasable);
CREATE INDEX IF NOT EXISTS products_active_idx ON products (is_active);
CREATE UNIQUE INDEX IF NOT EXISTS products_source_code_nocase_unique_idx
    ON products (source, source_code COLLATE NOCASE);
CREATE UNIQUE INDEX IF NOT EXISTS products_source_url_unique_idx
    ON products (source, product_url);
CREATE UNIQUE INDEX IF NOT EXISTS products_source_url_nocase_unique_idx
    ON products (source, product_url COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS catalog_events (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    source_code TEXT NOT NULL,
    product_name TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('discontinued_removed')),
    occurred_at TEXT NOT NULL,
    details_json TEXT
);

CREATE INDEX IF NOT EXISTS catalog_events_product_idx
    ON catalog_events (source, source_code, occurred_at);

-- One row per successful complete-catalog refresh. This makes unattended
-- refreshes auditable without having to infer run history from product rows.
CREATE TABLE IF NOT EXISTS catalog_refreshes (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    active_total INTEGER NOT NULL CHECK (active_total >= 0),
    active_nutrilite INTEGER NOT NULL CHECK (active_nutrilite >= 0),
    active_xs INTEGER NOT NULL CHECK (active_xs >= 0),
    removed_discontinued INTEGER NOT NULL DEFAULT 0
        CHECK (removed_discontinued >= 0),
    UNIQUE (source, fetched_at)
);

CREATE INDEX IF NOT EXISTS catalog_refreshes_source_time_idx
    ON catalog_refreshes (source, fetched_at DESC);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS product_categories (
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    PRIMARY KEY (product_id, category_id)
);

-- A product can be learned from a catalog URL, a product URL, or one or more
-- package/label images. Keeping provenance separate prevents OCR-derived facts
-- from being confused with storefront marketing data.
CREATE TABLE IF NOT EXISTS product_sources (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL CHECK (source_type IN ('catalog_url', 'product_url', 'label_image', 'package_image')),
    source_ref TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    UNIQUE (product_id, source_type, source_ref)
);

CREATE TABLE IF NOT EXISTS label_panels (
    id INTEGER PRIMARY KEY,
    product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    source_id INTEGER REFERENCES product_sources(id) ON DELETE SET NULL,
    serving_size TEXT,
    servings_per_container REAL,
    confidence REAL CHECK (confidence IS NULL OR confidence BETWEEN 0 AND 1),
    notes TEXT,
    captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nutrients (
    id INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS product_nutrients (
    label_panel_id INTEGER NOT NULL REFERENCES label_panels(id) ON DELETE CASCADE,
    nutrient_id INTEGER NOT NULL REFERENCES nutrients(id),
    amount REAL,
    unit TEXT,
    daily_value_percent REAL,
    raw_name TEXT,
    PRIMARY KEY (label_panel_id, nutrient_id)
);

CREATE TABLE IF NOT EXISTS ingredients (
    id INTEGER PRIMARY KEY,
    label_panel_id INTEGER NOT NULL REFERENCES label_panels(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    amount REAL,
    unit TEXT,
    is_active INTEGER CHECK (is_active IS NULL OR is_active IN (0, 1)),
    UNIQUE (label_panel_id, position)
);

CREATE VIRTUAL TABLE IF NOT EXISTS products_fts USING fts5(
    name,
    brand,
    description,
    content='products',
    content_rowid='id'
);

CREATE TRIGGER IF NOT EXISTS products_ai AFTER INSERT ON products BEGIN
    INSERT INTO products_fts(rowid, name, brand, description)
    VALUES (new.id, new.name, new.brand, new.description);
END;

CREATE TRIGGER IF NOT EXISTS products_ad AFTER DELETE ON products BEGIN
    INSERT INTO products_fts(products_fts, rowid, name, brand, description)
    VALUES ('delete', old.id, old.name, old.brand, old.description);
END;

CREATE TRIGGER IF NOT EXISTS products_au AFTER UPDATE ON products BEGIN
    INSERT INTO products_fts(products_fts, rowid, name, brand, description)
    VALUES ('delete', old.id, old.name, old.brand, old.description);
    INSERT INTO products_fts(rowid, name, brand, description)
    VALUES (new.id, new.name, new.brand, new.description);
END;

DROP VIEW IF EXISTS product_comparison;
CREATE VIEW product_comparison AS
SELECT
    p.source_code,
    p.name,
    p.brand,
    p.retail_price_cents / 100.0 AS retail_price,
    p.member_price_cents / 100.0 AS member_price,
    p.currency,
    lp.serving_size,
    lp.servings_per_container,
    n.canonical_name AS nutrient,
    pn.amount,
    pn.unit,
    pn.daily_value_percent,
    CASE
        WHEN lp.servings_per_container > 0 AND p.retail_price_cents IS NOT NULL
        THEN (p.retail_price_cents / 100.0) / lp.servings_per_container
    END AS retail_price_per_serving
FROM products p
LEFT JOIN label_panels lp ON lp.product_id = p.id
LEFT JOIN product_nutrients pn ON pn.label_panel_id = lp.id
LEFT JOIN nutrients n ON n.id = pn.nutrient_id
WHERE p.is_active = 1;

-- The application compares against Nutrilite and XS, while the underlying
-- database retains the complete Amway catalog for future use. XS sub-brands
-- such as "XS Sport Nutrition" are intentionally included.
DROP VIEW IF EXISTS nutrilite_xs_catalog;
CREATE VIEW nutrilite_xs_catalog AS
SELECT
    p.id,
    p.source_code,
    p.name,
    p.brand,
    p.description,
    p.product_url,
    p.image_url,
    p.retail_price_cents,
    p.member_price_cents,
    p.currency,
    p.is_bundle,
    p.is_purchasable,
    p.is_sellable,
    p.stock_disposition,
    p.fetched_at
FROM products AS p
WHERE p.is_active = 1
  AND (
      lower(p.brand) = 'nutrilite'
      OR lower(p.brand) = 'xs'
      OR lower(p.brand) LIKE 'xs %'
  );
