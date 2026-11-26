cat << 'EOF' > README.md
# TASK-CRE-03 | Ingest BORIS Standard Land Value Zones — Lower Saxony (LGLN WFS)

Reproducible Python 3.11 + Docker ingestion pipeline for official Lower Saxony (*Niedersachsen*) standard land value zones (*Bodenrichtwerte* / BORIS) from the LGLN WFS interface into an isolated PostGIS staging table (`iris_staging.spatial_boris_staging`).

## Project Structure

```text
TASK-CRE-03-boris-lower-saxony/
├── Dockerfile                  # Python 3.11-slim reproducible image
├── docker-compose.yml          # PostGIS 16/3.4 + Ingestor service
├── requirements.txt            # Pinned Python dependencies
├── sql/
│   └── init_staging.sql        # Isolated iris_staging DDL + GIST spatial index
├── src/
│   └── ingest_boris.py         # WFS/fixture extraction, EPSG:25832 & make_valid(), upsert
└── tests/
    ├── fixture_boris.json      # Representative BORIS GeoJSON fixture (valid, bowtie, null)
    └── test_ingest.py          # Automated pytest smoke tests
```

## Exact Run Steps (Docker)

1. **Start PostGIS and run the pipeline using the local fixture (offline-safe):**
   ```bash
   docker compose run --rm --build -e USE_FIXTURE=true ingestor
   ```

2. **Run automated smoke tests:**
   ```bash
   docker compose run --rm ingestor python -m pytest -v
   ```

3. **Run against the live LGLN WFS endpoint:**
   ```bash
   docker compose run --rm -e USE_FIXTURE=false ingestor
   ```

4. **Verify staging table contents, CRS (`EPSG:25832`), and geometry validity:**
   ```bash
   docker exec iris_postgis psql -U iris_user -d iris_db -c "SELECT zone_id, valuation_date, land_value_eur_m2, municipality, ST_SRID(geom) AS srid, ST_IsValid(geom) AS is_valid FROM iris_staging.spatial_boris_staging ORDER BY zone_id;"
   ```

## Schema Mapping (`iris_staging.spatial_boris_staging`)

All writes target `iris_staging.spatial_boris_staging` exclusively (no writes to `iris_core`). Credentials are supplied via the `DB_URI` environment variable.

| Target Column | Type | Source Attribute (BORIS WFS / Fixture) | Notes |
|---|---|---|---|
| `zone_id` | `VARCHAR(128)` | `brw_id` / `wnum` / `gml_id` | Part of unique key `(zone_id, valuation_date)` |
| `state_code` | `VARCHAR(16)` | Constant `'NI'` | Lower Saxony state code |
| `valuation_date` | `DATE` | `stichtag` / `stag` | Normalized `YYYY-MM-DD`; part of unique key |
| `land_value_eur_m2` | `NUMERIC(12,2)` | `brw` / `bodenrichtwert` | EUR per m² (handles German comma decimals) |
| `land_use_type` | `VARCHAR(128)` | `nutzungsart` / `nutz` | Land use classification |
| `municipality` | `VARCHAR(255)` | `gemeinde` / `gena` | Municipality name |
| `geom` | `GEOMETRY(MultiPolygon, 25832)` | Feature geometry | Reprojected/verified `EPSG:25832`, repaired via `shapely.make_valid()` |
| `source_name` | `VARCHAR(128)` | Constant `'LGLN_BORIS_WFS'` | Source provenance |
| `source_url` | `TEXT` | WFS endpoint or `fixture://...` | Traceable source URI |
| `raw_attributes` | `JSONB` | Full raw feature properties | Preserved for downstream auditability |

## Verification Evidence (Tests, Idempotency & Validation)

### 1. Automated Smoke Tests (`pytest`)
```text
tests/test_ingest.py::test_repairs_invalid_bowtie_polygon PASSED [ 33%]
tests/test_ingest.py::test_rejects_empty_geometry PASSED         [ 66%]
tests/test_ingest.py::test_normalizes_valid_boris_feature PASSED [100%]
3 passed in 0.67s
```

### 2. Ingestion, Error Handling & Idempotency (2nd consecutive run)
The fixture contains 3 features: 1 valid polygon, 1 self-intersecting bowtie polygon (repaired with `make_valid()`), and 1 feature with `null` geometry (skipped and logged as failed). Running the pipeline twice produces no duplicates thanks to `ON CONFLICT (zone_id, valuation_date) DO UPDATE`:

```text
INFO    | Loading BORIS fixture from tests/fixture_boris.json
WARNING | Skipping feature #3: Empty or non-polygonal geometry
INFO    | Pipeline finished | processed=3 | inserted_or_updated=2 | failed=1
```

### 3. Staging Table Output Sample
```text
     zone_id      | valuation_date | land_value_eur_m2 | municipality | srid  | is_valid
------------------+----------------+-------------------+--------------+-------+----------
 NI-03241001-0001 | 2025-01-01     |            450.00 | Hannover     | 25832 | t
 NI-03241001-0002 | 2025-01-01     |            320.50 | Braunschweig | 25832 | t
(2 rows)
```

## Limitations & Assumptions

* **Endpoint Verification & Network Access:** The live LGLN WFS endpoint (`https://opendata.lgln.niedersachsen.de/doorman/noauth/boris_wfs`) was not queried in production batch mode during local verification; end-to-end validation was executed against a representative GeoJSON fixture (`tests/fixture_boris.json`). Note that the task brief contains a minor typographic rendering (`lgIn` vs. `lgln`); the pipeline defaults to `lgln`.
* **Target DDL Alignment:** The `iris_staging.spatial_boris_staging` DDL is a proposed normalized staging schema and should be verified against Tariq's final upstream DDL before promoting to shared environments.
* **Data Licensing:** LGLN Lower Saxony open data is typically published under *Datenlizenz Deutschland – Namensnennung – Version 2.0 (dl-de/by-2-0)*; attribution (`source_name` / `source_url`) is preserved per row, and license terms must be confirmed before commercial redistribution.
* **Valuation Limitations:** BORIS *Bodenrichtwerte* represent indicative zonal average land values as of a specific *Stichtag* (valuation date) and do not substitute for site-specific ImmoWertV appraisals, parcel-level encumbrances, or HBU (Highest and Best Use) adjustments.
EOF