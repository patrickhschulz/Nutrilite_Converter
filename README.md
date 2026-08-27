# Nutrilite Converter

A lightweight, installable web app that turns a supplement photo, public
product URL, or pasted label into a structured Nutrilite/XS comparison. The
friend-facing PWA has no framework or third-party browser dependencies; its
HTML, CSS, and JavaScript shell is about 90 KB. Profiles, saved supplements,
and results remain in that browser, while the server keeps only the shared
public catalog and processes each analysis transiently.

The included dependency-free SQLite catalog contains the complete product set
returned by the public Amway US storefront catalog. The current snapshot
contains 499 active products.

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

`release/Nutrilite_Converter_Lightsail.zip` is the recommended friend-facing
Docker/Ubuntu distribution. It provides an installable PWA for camera/photo,
product-URL, and manual supplement input; confirmation of extracted facts;
Nutrilite/XS comparisons; and optional browser-local demographics and history.
Friends open one private link and can add the app to their Home Screen—there is
no local database, API key, AI subscription, account, or setup on their device.

The hosted server adds automatic HTTPS, a persistent public SQLite catalog,
daily compressed catalog backups, server-side catalog refresh, request/body
limits, a server-only OpenAI API key, and manually approved GitHub Actions
deployment. It does not retain user photos, demographics, supplement histories,
or comparison results, and release artifacts contain no credentials or client
records.

AWS Lightsail is the primary target because an instance can later be exported
to EC2 and connected to the wider AWS ecosystem. The deployment itself uses
ordinary SSH and Docker Compose, so the same artifact can run on a DigitalOcean
Droplet. Follow [`deploy/lightsail/README.md`](deploy/lightsail/README.md) for the
one-time server, DNS, SSH, OpenAI key, private invite link, PWA installation,
privacy/cost controls, deployment, rollback, and growth-path instructions.

Run the web/API service locally without Docker. Use development-only values and
keep the real API key out of source control:

```sh
export APP_ACCESS_TOKEN="replace-with-a-long-random-development-token"
export OPENAI_API_KEY="your-server-side-api-key"
python3 catalog_api.py --host 127.0.0.1 --port 8000
```

Then open
`http://127.0.0.1:8000/#invite=replace-with-a-long-random-development-token`.
The app removes the fragment after importing it into browser-local storage.

The access token in a shared URL fragment provides lightweight bearer access
for a trusted circle; it is not a user-account or health-record system. The PWA
keeps profiles and history in each browser, while analysis inputs are processed
in memory and sent to the configured OpenAI API without being written to server
storage. Rotate the token if an invite link leaks and do not add server-side
client retention without a dedicated privacy and security design.

The complete catalog remains local, with an application-facing
`nutrilite_xs_catalog` view for the products used by the converter. In the
August 22, 2026 snapshot that view contains 74 Nutrilite products and 58 XS
products, including the `XS Sport Nutrition` sub-brand. The comparison engine
recommends only the currently purchasable and sellable subset; the database
still retains the full active catalog and its availability metadata.

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
- `web/` — installable, dependency-free PWA assets
- `catalog_api.py`, `supplement_analyzer.py`, and `safe_url.py` — hosted API,
  structured AI analysis, and guarded public-URL retrieval
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
