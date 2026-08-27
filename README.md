# Nutrilite Converter product database

A dependency-free SQLite catalog for product comparison work. It contains the
complete product set returned by the public Amway US storefront catalog.
The current snapshot contains 499 active products.

## Portable release

The `release/` directory contains the self-contained distribution:

- `Nutrilite_Converter.zip` — code, ready SQLite catalog, compressed offline
  seed, empty client schema, tests, and bootstrap documentation
- `Nutrilite_Converter.sha256` — archive integrity checksum
- `Nutrilite_Converter.manifest.json` — version, catalog baseline, privacy
  declaration, and per-file checksums

After extracting the archive, run:

```sh
python3 bootstrap.py
```

See `Nutrilite_Converter.md` for the complete human- and AI-agent-readable
installation playbook. The artifact can rebuild `data/products.sqlite3`
offline with `python3 bootstrap.py --rebuild-catalog`.

Existing `Clients/` folders are deliberately excluded from Git and release
artifacts because they can contain supplement histories and label images. Use
`python3 create_client.py "Client Name"` to create a new private, empty client
database from the included template.

## Hosted release

`release/Nutrilite_Converter_Lightsail.zip` is the Docker/Ubuntu distribution.
It adds a small read-only catalog website and JSON API, automatic HTTPS,
persistent SQLite storage, daily compressed backups, server-side catalog
refresh, and a manually triggered GitHub Actions deployment. It contains no
client records or images.

AWS Lightsail is the primary target because an instance can later be exported
to EC2 and connected to the wider AWS ecosystem. The deployment itself uses
ordinary SSH and Docker Compose, so the same artifact can run on a DigitalOcean
Droplet. Follow [`deploy/lightsail/README.md`](deploy/lightsail/README.md) for the
one-time server, DNS, SSH, GitHub secret, deployment, rollback, and growth-path
instructions.

Run the web/API service locally without Docker:

```sh
python3 catalog_api.py --host 127.0.0.1 --port 8000
```

The current hosted interface exposes public catalog search only. Product-label
image ingestion, user accounts, demographics, and personalized comparisons
must not be exposed until private storage and authentication are implemented.

The complete catalog remains local, with an application-facing
`nutrilite_xs_catalog` view for the products used by the converter. In the
August 22, 2026 snapshot that view contains 74 Nutrilite products and 58 XS
products, including the `XS Sport Nutrition` sub-brand.

## What's included

- `data/products.sqlite3` — the ready-to-query SQLite database
- `data/raw/amway-all-products.json` — reproducible full-catalog source snapshot
- `import_amway.py` — refreshes the snapshot and database from Amway's public,
  anonymous storefront API
- `refresh_catalog.py` — staleness-aware, validated entry point for scheduled
  refreshes
- `schema.sql` — catalog, provenance, label, ingredient, and nutrient schema
- `query_products.py` — a small full-text-search CLI
- `.github/workflows/refresh-catalog.yml` — unattended weekly refresh
- `build_release.py` — reproducibly assembles and validates the portable ZIP
- `build_cloud_release.py` — assembles the hosted Lightsail/Droplet ZIP
- `Dockerfile` and `deploy/lightsail/` — hosted runtime and operations
- `.github/workflows/deploy-lightsail.yml` — tested manual production deployment
- `seed_catalog.py` — atomically reconstructs SQLite from the offline seed
- `verify_install.py` — checks manifests, integrity, duplicates, lifecycle, and FTS

The catalog import contains storefront metadata. The empty `label_panels`,
`product_nutrients`, and `ingredients` tables are deliberately separate: facts
extracted later from a product URL or uploaded label/package photo retain their
own provenance and are suitable for per-serving comparisons.

## Use it

```sh
python3 query_products.py "vitamin d"
sqlite3 data/products.sqlite3 \
  "SELECT brand, count(*) FROM products GROUP BY brand ORDER BY count(*) DESC;"
```

Refresh the catalog when the local copy is at least seven days old:

```sh
python3 refresh_catalog.py
```

Force an immediate refresh or change the age threshold:

```sh
python3 refresh_catalog.py --force
python3 refresh_catalog.py --max-age-hours 24
```

Validate the local copy and report whether it is stale without using the
network:

```sh
python3 refresh_catalog.py --check-only
```

The wrapper uses an exclusive lock to prevent overlapping scheduled runs and
validates SQLite integrity, foreign keys, and the presence of active Nutrilite
and XS records before reporting success. Successful network refreshes are
recorded in `catalog_refreshes` with product counts and removal totals.

Query only the comparison catalog:

```sh
sqlite3 -header -column data/products.sqlite3 \
  "SELECT brand, count(*) FROM nutrilite_xs_catalog GROUP BY brand ORDER BY brand;"
```

## Automatic refresh

The included GitHub Actions workflow runs every Monday at 09:17 UTC, validates
the result, runs the datastore tests, and commits the refreshed SQLite database
and reproducible JSON snapshot when they change. It can also be run manually
with **Actions → Refresh local product catalog → Run workflow**.

For a local installation or hosted server, schedule the staleness-aware wrapper
daily. Calling it daily is inexpensive because it only contacts Amway after the
configured age threshold. For example, a Unix-like system can use:

```cron
17 9 * * * cd /absolute/path/to/nutrilite-converter && /usr/bin/python3 refresh_catalog.py
```

Windows Task Scheduler, a container scheduler, or a cloud scheduled job should
invoke the same `python3 refresh_catalog.py` command. Keep the database,
snapshot, and lock file on persistent storage.

The importer follows API pagination, verifies that every advertised result was
received before changing the database, and upserts products transactionally.
Duplicates are rejected by case-insensitive SKU and canonical product URL;
names are not deduplicated because distinct sizes and flavors can legitimately
share one name.

Products missing from a complete refresh are immediately labeled with
`is_active = 0`, `discontinued_at`, and a consecutive-miss count. By default,
the importer removes them after two consecutive complete refreshes and records
the removal in `catalog_events`. Removal cascades through that product's category,
source, label, ingredient, and nutrient rows; the audit event retains its SKU,
name, timestamp, and confirmed-miss count. Change the confirmation window with:

```sh
python3 refresh_catalog.py --force --purge-after-misses 3
python3 refresh_catalog.py --force --purge-after-misses 1  # first confirmed miss
python3 refresh_catalog.py --force --purge-after-misses 0  # label, never purge
```

The search CLI and `product_comparison` view return active products only.

Prices and availability are a point-in-time snapshot. Refresh before presenting
them as current. Product and label information should not be treated as medical
advice.
