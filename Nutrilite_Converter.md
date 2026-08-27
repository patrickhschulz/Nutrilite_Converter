# Nutrilite Converter bootstrap playbook

This document is the human- and agent-readable entry point for Nutrilite
Converter release `1.0.0`. The project provides a dependency-free Python and
SQLite Amway US product catalog, an offline catalog reconstruction path, catalog
refresh and lifecycle handling, search tools, and an empty private client
supplement-database template.

Repository: <https://github.com/patrickhschulz/Nutrilite_Converter>

## Instructions for an AI agent

When a user says **“run Nutrilite_Converter.md”**, read this entire document
before acting and then follow the applicable path below.

1. Confirm that the environment provides a writable filesystem, Python, SQLite,
   and terminal execution. If it does not, explain that the release can be
   inspected but cannot be installed in that chat environment.
2. Treat the attached release as the authority. Do not invent missing scripts,
   silently substitute schemas, or recreate client records from examples.
3. Do not send API keys, client data, health information, or product-label
   images to third parties without the user's explicit authorization.
4. The default bootstrap is offline. Request approval before a live catalog
   refresh or any other network access when the environment requires it.
5. Do not overwrite an existing client database. Catalog reconstruction creates
   a timestamped backup when explicitly requested.
6. Execute the verification step and report its concrete result, including
   integrity, product counts, duplicate checks, and schema version.

If only this Markdown file is attached, retrieve the matching
`release/Nutrilite_Converter.zip` and `.sha256` file from the repository above.
If network retrieval is unavailable, tell the user that the ZIP is required;
the Markdown file intentionally does not embed binary data or executable source.

## Machine-readable release contract

```yaml
project: nutrilite-converter
artifact: Nutrilite_Converter.zip
artifact_version: 1.0.0
schema_version: 1
python_minimum: "3.11"
manifest: manifest.json
catalog_database: data/products.sqlite3
catalog_seed: data/seed/amway-all-products.json.gz
bootstrap: bootstrap.py
verification: verify_install.py
refresh: refresh_catalog.py
client_schema: templates/client_schema.sql
dependencies: python-standard-library-only
```

## 1. Extract and verify the download

Extract the ZIP into a new directory. It contains one top-level
`Nutrilite_Converter` directory.

On macOS or Linux, verify the archive before extraction:

```sh
shasum -a 256 -c Nutrilite_Converter.sha256
unzip Nutrilite_Converter.zip
cd Nutrilite_Converter
```

On Windows PowerShell, compare the result with the value in
`Nutrilite_Converter.sha256`:

```powershell
Get-FileHash .\Nutrilite_Converter.zip -Algorithm SHA256
Expand-Archive .\Nutrilite_Converter.zip
Set-Location .\Nutrilite_Converter\Nutrilite_Converter
```

Never execute a release whose archive checksum does not match.

## 2. Confirm the environment

Python 3.11 or newer is required. The application uses only the Python standard
library; no package download is required.

```sh
python3 --version
python3 -m pip install -r requirements.txt
```

On Windows, `py -3` can be used instead of `python3`.

## 3. Run the offline bootstrap

The release already contains a ready-to-query SQLite database. The default
bootstrap verifies the immutable release files and database without contacting
the internet:

```sh
python3 bootstrap.py
```

Expected status is `ready`, with SQLite integrity `ok`, schema version `1`, no
duplicate SKUs or URLs, and non-zero Nutrilite and XS counts. Release `1.0.0`
ships with a baseline of 499 active products: 74 Nutrilite and 58 XS-family
products. Counts may legitimately change after a live refresh.

## 4. Prove reconstruction from scratch

To reconstruct `data/products.sqlite3` solely from the bundled compressed public
catalog seed, run:

```sh
python3 bootstrap.py --rebuild-catalog
```

The reconstruction is built and validated in a temporary database before an
atomic replacement. If a database already exists, it is copied to a timestamped
`data/products.backup-*.sqlite3` file first.

For a non-mutating verification at any later time:

```sh
python3 bootstrap.py --verify-only
python3 verify_install.py
```

The first command requires the database to match the packaged snapshot exactly.
The second accepts a successfully refreshed database while still checking file
integrity, schema, foreign keys, duplicate identities, lifecycle state, and the
full-text index.

## 5. Search the catalog

```sh
python3 query_products.py "vitamin d"
python3 query_products.py "perfect pack" --limit 10
```

The underlying database is `data/products.sqlite3`. SQLite-compatible tools can
query it directly. The `nutrilite_xs_catalog` view contains the comparison subset
and the `products` table retains the complete Amway storefront catalog.

## 6. Refresh from Amway

A live refresh is optional and requires outbound HTTPS access:

```sh
python3 refresh_catalog.py
```

The command contacts Amway only when the local data is at least seven days old.
To force an immediate refresh:

```sh
python3 refresh_catalog.py --force
```

The importer paginates through the full response and refuses to modify the
database when the advertised result count is incomplete. Case-insensitive SKUs
and canonical product URLs are unique. A missing product is first marked
inactive with a discontinuation timestamp; by default it is removed only after
two consecutive complete refresh misses, with an audit record retained in
`catalog_events`.

Validate freshness without network access:

```sh
python3 refresh_catalog.py --check-only
```

## 7. Create a private client workspace

Do not put client data in the public repository or release directory. Create a
private, empty client database locally:

```sh
python3 create_client.py "Client Name"
```

This creates:

```text
Clients/client-name/client_supplements.sqlite3
```

The database stores the optional demographic profile, current supplement list,
label facts, ingredients, sources, properties, and comparison-normalized
components. The command refuses to overwrite an existing client database.

## 8. Schedule catalog maintenance

The repository includes `.github/workflows/refresh-catalog.yml`, which refreshes
and validates the catalog weekly and rebuilds the portable release artifact.
For a local or hosted installation, invoke `python3 refresh_catalog.py` daily;
its age check prevents unnecessary network calls.

Example cron entry:

```cron
17 9 * * * cd /absolute/path/to/Nutrilite_Converter && /usr/bin/python3 refresh_catalog.py
```

Windows Task Scheduler can invoke the same script. Keep `data/` on persistent
storage and back it up separately from application code.

## 9. Run the test suite

```sh
python3 -m unittest discover -s tests -p "test_*.py"
```

The tests cover catalog validation, brand subset counts, freshness, refresh
locking, refresh audit records, offline database reconstruction, and private
client-database creation.

## 10. Operational and health-data boundaries

- Store server/API credentials only in environment variables or a secret store.
- Delete uploaded package images after extraction unless the user explicitly
  requests retention.
- Keep demographic information minimal and avoid unnecessary identifiers.
- Never include client folders in a public release.
- Product prices and availability are point-in-time data.
- Product matching and gap analysis are informational and are not diagnosis,
  treatment, or individualized medical advice. Medication interactions,
  pregnancy, diagnosed deficiencies, and medical conditions require review by a
  qualified healthcare professional.

## Troubleshooting

- **Checksum mismatch:** discard the archive and download it again.
- **Python is too old:** install Python 3.11 or newer, then rerun the bootstrap.
- **Manifest checksum failure:** restore a clean release; do not continue with
  modified bootstrap or schema files.
- **Catalog database is absent or corrupt:** run
  `python3 bootstrap.py --rebuild-catalog`.
- **Amway refresh fails:** retain the last validated database and try later. The
  offline seed remains usable.
- **A client database already exists:** choose a different client name or work
  with the existing private database; the creator intentionally will not
  overwrite it.
