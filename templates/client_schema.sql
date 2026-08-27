PRAGMA foreign_keys = ON;
PRAGMA user_version = 1;

CREATE TABLE IF NOT EXISTS client_profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL,
    age_years INTEGER CHECK (age_years IS NULL OR age_years BETWEEN 0 AND 125),
    demographic_notes TEXT,
    dietary_preferences TEXT,
    allergies TEXT,
    supplementation_goals TEXT,
    health_context TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS supplements (
    id INTEGER PRIMARY KEY,
    stable_key TEXT NOT NULL UNIQUE,
    brand TEXT NOT NULL,
    product_name TEXT NOT NULL,
    regulatory_class TEXT NOT NULL
        CHECK (regulatory_class IN ('dietary_supplement', 'otc_drug')),
    dosage_form TEXT,
    package_quantity REAL,
    package_unit TEXT,
    suggested_use TEXT,
    comparison_eligible INTEGER NOT NULL DEFAULT 1
        CHECK (comparison_eligible IN (0, 1)),
    identification_confidence REAL NOT NULL
        CHECK (identification_confidence BETWEEN 0 AND 1),
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS product_images (
    id INTEGER PRIMARY KEY,
    supplement_id INTEGER NOT NULL REFERENCES supplements(id) ON DELETE CASCADE,
    filename TEXT NOT NULL UNIQUE,
    image_role TEXT NOT NULL
        CHECK (image_role IN ('front', 'supplement_facts', 'drug_facts', 'other')),
    sha256 TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    supplement_id INTEGER NOT NULL REFERENCES supplements(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL CHECK (
        source_type IN (
            'label_photo', 'official_web', 'regulatory', 'retailer',
            'label_database'
        )
    ),
    source_ref TEXT NOT NULL,
    title TEXT,
    publisher TEXT,
    accessed_at TEXT NOT NULL,
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    notes TEXT,
    UNIQUE (supplement_id, source_ref)
);

CREATE TABLE IF NOT EXISTS label_panels (
    id INTEGER PRIMARY KEY,
    supplement_id INTEGER NOT NULL UNIQUE
        REFERENCES supplements(id) ON DELETE CASCADE,
    serving_size_quantity REAL,
    serving_size_unit TEXT,
    serving_size_text TEXT,
    servings_per_container REAL,
    panel_type TEXT NOT NULL DEFAULT 'supplement_facts'
        CHECK (panel_type IN ('supplement_facts', 'drug_facts')),
    confidence REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    notes TEXT
);

CREATE TABLE IF NOT EXISTS components (
    id INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    component_type TEXT NOT NULL CHECK (
        component_type IN (
            'vitamin', 'mineral', 'macronutrient', 'botanical', 'amino_acid',
            'carotenoid', 'fatty_acid', 'other', 'active_drug'
        )
    )
);

CREATE TABLE IF NOT EXISTS supplement_components (
    id INTEGER PRIMARY KEY,
    supplement_id INTEGER NOT NULL REFERENCES supplements(id) ON DELETE CASCADE,
    component_id INTEGER NOT NULL REFERENCES components(id),
    raw_label_name TEXT NOT NULL,
    amount REAL NOT NULL,
    unit TEXT NOT NULL,
    amount_mg REAL GENERATED ALWAYS AS (
        CASE lower(unit)
            WHEN 'mcg' THEN amount / 1000.0
            WHEN 'mg' THEN amount
            WHEN 'g' THEN amount * 1000.0
            ELSE NULL
        END
    ) STORED,
    daily_value_percent REAL,
    form_or_source TEXT,
    amount_basis TEXT NOT NULL DEFAULT 'per_serving'
        CHECK (
            amount_basis IN (
                'per_serving', 'per_tablet', 'per_softgel', 'per_capsule'
            )
        ),
    ordinal INTEGER NOT NULL,
    notes TEXT,
    UNIQUE (supplement_id, ordinal)
);

CREATE TABLE IF NOT EXISTS ingredients (
    id INTEGER PRIMARY KEY,
    supplement_id INTEGER NOT NULL REFERENCES supplements(id) ON DELETE CASCADE,
    ingredient_name TEXT NOT NULL,
    ingredient_role TEXT NOT NULL DEFAULT 'other'
        CHECK (ingredient_role IN ('other', 'inactive_drug', 'capsule', 'carrier')),
    ordinal INTEGER NOT NULL,
    notes TEXT,
    UNIQUE (supplement_id, ordinal)
);

CREATE TABLE IF NOT EXISTS properties (
    id INTEGER PRIMARY KEY,
    supplement_id INTEGER NOT NULL REFERENCES supplements(id) ON DELETE CASCADE,
    property_name TEXT NOT NULL,
    property_value TEXT NOT NULL,
    property_type TEXT NOT NULL DEFAULT 'label_claim' CHECK (
        property_type IN (
            'label_claim', 'allergen', 'certification', 'provenance',
            'identifier', 'data_quality'
        )
    ),
    source_ref TEXT,
    UNIQUE (supplement_id, property_name, property_value)
);

CREATE INDEX IF NOT EXISTS client_supplements_brand_name_idx
    ON supplements(brand, product_name);
CREATE INDEX IF NOT EXISTS client_supplements_comparison_idx
    ON supplements(comparison_eligible, regulatory_class);
CREATE INDEX IF NOT EXISTS client_components_name_idx
    ON components(canonical_name);
CREATE INDEX IF NOT EXISTS client_supplement_components_idx
    ON supplement_components(component_id, amount_mg);
CREATE INDEX IF NOT EXISTS client_properties_lookup_idx
    ON properties(property_name, property_value);
CREATE INDEX IF NOT EXISTS client_sources_product_idx
    ON sources(supplement_id, source_type);

CREATE VIEW IF NOT EXISTS comparison_components AS
SELECT
    cp.name AS client_name,
    s.stable_key,
    s.brand,
    s.product_name,
    s.regulatory_class,
    s.dosage_form,
    s.comparison_eligible,
    c.canonical_name,
    c.component_type,
    sc.raw_label_name,
    sc.amount,
    sc.unit,
    sc.amount_mg,
    sc.daily_value_percent,
    sc.form_or_source,
    sc.amount_basis,
    lp.serving_size_quantity,
    lp.serving_size_unit,
    lp.serving_size_text
FROM supplements AS s
CROSS JOIN client_profile AS cp
JOIN supplement_components AS sc ON sc.supplement_id = s.id
JOIN components AS c ON c.id = sc.component_id
LEFT JOIN label_panels AS lp ON lp.supplement_id = s.id
WHERE cp.id = 1;

CREATE VIEW IF NOT EXISTS supplement_summary AS
SELECT
    s.*,
    COUNT(DISTINCT sc.id) AS component_count,
    COUNT(DISTINCT i.id) AS ingredient_count,
    COUNT(DISTINCT pi.id) AS image_count,
    COUNT(DISTINCT src.id) AS source_count
FROM supplements AS s
LEFT JOIN supplement_components AS sc ON sc.supplement_id = s.id
LEFT JOIN ingredients AS i ON i.supplement_id = s.id
LEFT JOIN product_images AS pi ON pi.supplement_id = s.id
LEFT JOIN sources AS src ON src.supplement_id = s.id
GROUP BY s.id;
