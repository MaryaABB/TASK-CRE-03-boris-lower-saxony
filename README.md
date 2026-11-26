# TASK-CRE-03 | Ingest BORIS Standard Land Value Zones — Lower Saxony (LGLN WFS)

Reproducible Python 3.11 + Docker ingestion pipeline for official Lower Saxony (*Niedersachsen*) standard land value zones (*Bodenrichtwerte* / BORIS) from the LGLN WFS interface (`boris:BR_BodenrichtwertZonal`, GML 3.2) into an isolated PostGIS staging table (`iris_staging.spatial_boris_staging`).

## Exact Run Steps (Docker)

1. **Build the image and run automated smoke tests:**
   ```bash
   docker compose build ingestor
   docker compose run --rm ingestor python -m pytest -v
   ```

2. **Run against the live LGLN WFS endpoint (with paging and configurable limit):**
   ```bash
   docker compose run --rm -e USE_FIXTURE=false -e MAX_FEATURES=10 ingestor
   ```
   *(Set `MAX_FEATURES=0` for a full paged run across all available zones; `BORIS_LAYER` defaults to `boris:BR_BodenrichtwertZonal` and `WFS_PAGE_SIZE` defaults to `500`.)*

3. **Run using the local fixture (offline-safe):**
   ```bash
   docker compose run --rm -e USE_FIXTURE=true ingestor
   ```

4. **Verify staging table contents, CRS (`EPSG:25832`), and geometry validity:**
   ```bash
   docker exec iris_postgis psql -U iris_user -d iris_db -c "SELECT zone_id, valuation_date, land_value_eur_m2, land_use_type, municipality, ST_SRID(geom) AS srid, ST_IsValid(geom) AS is_valid FROM iris_staging.spatial_boris_staging ORDER BY zone_id LIMIT 10;"
   ```

## Schema Mapping (`iris_staging.spatial_boris_staging`)

All writes target `iris_staging.spatial_boris_staging` exclusively (no writes to `iris_core`). Credentials are supplied via the `DB_URI` environment variable.

| Target Column | Type | Source Attribute (LGLN WFS GML 3.2 / Fixture) | Notes |
|---|---|---|---|
| `zone_id` | `VARCHAR(128)` | `gml_id` / `brw_id` / `wnum` | Part of unique key `(zone_id, valuation_date)` |
| `state_code` | `VARCHAR(16)` | Constant `'NI'` | Lower Saxony state code |
| `valuation_date` | `DATE` | `stichtag` / `stag` | Normalized `YYYY-MM-DD`; part of unique key |
| `land_value_eur_m2` | `NUMERIC(12,2)` | `bodenrichtwert` / `brw` | EUR per m² (handles German comma decimals) |
| `land_use_type` | `VARCHAR(128)` | `art` / `nutzungsart` | Land use classification (e.g., `W`) |
| `municipality` | `VARCHAR(255)` | `name` / `gemeindename` / `gemeinde` | Municipality name (e.g., `Braunschweig. Stadt`) |
| `geom` | `GEOMETRY(MultiPolygon, 25832)` | Feature geometry | Reprojected/verified `EPSG:25832`, repaired via `shapely.make_valid()` |
| `source_name` | `VARCHAR(128)` | Constant `'LGLN_BORIS_WFS'` | Source provenance |
| `source_url` | `TEXT` | WFS endpoint or `fixture://...` | Traceable source URI |
| `raw_attributes` | `JSONB` | Full raw feature properties | Preserved for downstream auditability |

## Verification Evidence (Live WFS, Tests & Idempotency)

### 1. Automated Smoke Tests (`pytest`)
```text
tests/test_ingest.py::test_repairs_invalid_bowtie_polygon PASSED [ 33%]
tests/test_ingest.py::test_rejects_empty_geometry PASSED         [ 66%]
tests/test_ingest.py::test_normalizes_valid_boris_feature PASSED [100%]
3 passed in 0.96s
```

### 2. Live LGLN WFS Ingestion & Idempotency (2 consecutive runs)
Running twice against `boris:BR_BodenrichtwertZonal` (`outputFormat=application/gml+xml; version=3.2`) updates existing rows in place via `ON CONFLICT (zone_id, valuation_date) DO UPDATE` with zero duplicates:

```text
INFO | Connecting to LGLN WFS endpoint: [https://opendata.lgln.niedersachsen.de/doorman/noauth/boris_wfs](https://opendata.lgln.niedersachsen.de/doorman/noauth/boris_wfs)
INFO | Using WFS layer: boris:BR_BodenrichtwertZonal (page_size=500, max_features=10)
INFO | Fetching WFS page: startIndex=0, count=10
INFO | Pipeline finished | processed=10 | inserted_or_updated=10 | failed=0
```

### 3. Live Staging Table Output Sample (`PostGIS`)
```text
     zone_id      | valuation_date | land_value_eur_m2 | land_use_type |    municipality     | srid  | is_valid
------------------+----------------+-------------------+---------------+---------------------+-------+----------
 DENIBR0126B00007 | 2026-01-01     |            280.00 | W             | Braunschweig. Stadt | 25832 | t
 DENIBR0126B00009 | 2026-01-01     |            230.00 | W             | Braunschweig. Stadt | 25832 | t
 DENIBR0126B00011 | 2026-01-01     |            260.00 | W             | Braunschweig. Stadt | 25832 | t
 DENIBR0126B00014 | 2026-01-01     |            260.00 | W             | Braunschweig. Stadt | 25832 | t
 DENIBR0126B00020 | 2026-01-01     |            230.00 | W             | Braunschweig. Stadt | 25832 | t
(5 rows)
```

## Limitations & Assumptions

* **WFS Output Format & Layer:** The LGLN WFS endpoint does not support `application/json`; requests use `application/gml+xml; version=3.2` targeting `boris:BR_BodenrichtwertZonal` explicitly. Full-state batch loads can be enabled by setting `MAX_FEATURES=0`.
* **Target DDL Alignment:** The `iris_staging.spatial_boris_staging` DDL is a proposed normalized staging schema and should be verified against Tariq's final upstream DDL before promoting to shared environments.
* **Data Licensing:** LGLN Lower Saxony open data is typically published under *Datenlizenz Deutschland – Namensnennung – Version 2.0 (dl-de/by-2-0)*; attribution (`source_name` / `source_url`) is preserved per row, and license terms must be confirmed before commercial redistribution.
* **Valuation Limitations:** BORIS *Bodenrichtwerte* represent indicative zonal average land values as of a specific *Stichtag* (valuation date) and do not substitute for site-specific ImmoWertV appraisals, parcel-level encumbrances, or HBU (Highest and Best Use) adjustments.