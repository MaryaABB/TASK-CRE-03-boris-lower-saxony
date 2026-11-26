import io
import json
import logging
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import geopandas as gpd
import pandas as pd
import requests
from owslib.wfs import WebFeatureService
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.validation import make_valid
from sqlalchemy import create_engine, text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("boris_ingestor")

TARGET_CRS = "EPSG:25832"
DEFAULT_WFS_URL = "https://opendata.lgln.niedersachsen.de/doorman/noauth/boris_wfs"
DEFAULT_LAYER = "boris:BR_BodenrichtwertZonal"
WFS_GML_FORMAT = "application/gml+xml; version=3.2"


def extract_polygon_geometry(geom) -> Optional[MultiPolygon]:
    """Validate geometry with make_valid() and normalize to MultiPolygon."""
    if geom is None or geom.is_empty:
        return None

    if not geom.is_valid:
        geom = make_valid(geom)

    if geom.is_empty:
        return None

    if isinstance(geom, Polygon):
        return MultiPolygon([geom])
    if isinstance(geom, MultiPolygon):
        return geom
    if geom.geom_type == "GeometryCollection":
        polys = [g for g in geom.geoms if isinstance(g, (Polygon, MultiPolygon))]
        if not polys:
            return None
        flat_polys = []
        for p in polys:
            if isinstance(p, Polygon):
                flat_polys.append(p)
            else:
                flat_polys.extend(list(p.geoms))
        return MultiPolygon(flat_polys) if flat_polys else None

    return None


def parse_valuation_date(props: Dict[str, Any]) -> Optional[str]:
    """Extract and normalize valuation date (YYYY-MM-DD) from BORIS attributes."""
    for key in (
        "stichtag",
        "STICHTAG",
        "valuation_date",
        "stag",
        "STAG",
        "bodenrichtwertStichtag",
    ):
        val = props.get(key)
        if val is not None and not pd.isna(val):
            val_str = str(val).strip()[:10]
            try:
                datetime.strptime(val_str, "%Y-%m-%d")
                return val_str
            except ValueError:
                continue
    return None


def normalize_feature(
    props: Dict[str, Any], geom, source_url: str
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Map raw WFS/GeoJSON feature to staging schema. Returns (record, error_reason)."""
    valid_geom = extract_polygon_geometry(geom)
    if valid_geom is None:
        return None, "Empty or non-polygonal geometry"

    zone_id = None
    for key in (
        "brw_id",
        "BRW_ID",
        "bodenrichtwertnummer",
        "wnum",
        "WNUM",
        "zone_id",
        "gml_id",
        "id",
    ):
        val = props.get(key)
        if val is not None and not pd.isna(val) and str(val).strip():
            zone_id = str(val).strip()
            break

    if not zone_id:
        return None, "Missing stable zone_id"

    valuation_date = parse_valuation_date(props)
    if not valuation_date:
        return None, f"Missing or invalid valuation_date for zone {zone_id}"

    land_value = None
    for key in ("brw", "BRW", "bodenrichtwert", "wert", "land_value_eur_m2"):
        val = props.get(key)
        if val is not None and not pd.isna(val):
            try:
                land_value = float(str(val).replace(",", "."))
                break
            except ValueError:
                pass

    land_use = None
    for key in (
        "nutzungsart",
        "NUTZUNGSART",
        "art", "artDerNutzung",
        "nutz",
        "land_use_type",
    ):
        val = props.get(key)
        if val is not None and not pd.isna(val) and str(val).strip():
            land_use = str(val).strip()
            break

    municipality = None
    for key in ("name", "gemeindename", "gemeinde", "GEMEINDE", "gena", "municipality"):
        val = props.get(key)
        if val is not None and not pd.isna(val) and str(val).strip():
            municipality = str(val).strip()
            break

    record = {
        "zone_id": zone_id,
        "state_code": "NI",
        "valuation_date": valuation_date,
        "land_value_eur_m2": land_value,
        "land_use_type": land_use,
        "municipality": municipality,
        "geom_wkt": valid_geom.wkt,
        "source_name": "LGLN_BORIS_WFS",
        "source_url": source_url,
        "raw_attributes": json.dumps(props, default=str),
    }
    return record, None


def load_from_fixture(fixture_path: str) -> gpd.GeoDataFrame:
    """Load local GeoJSON fixture and ensure EPSG:25832."""
    logger.info("Loading BORIS fixture from %s", fixture_path)
    with open(fixture_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    rows = []
    for feat in data.get("features", []):
        geom = shape(feat["geometry"]) if feat.get("geometry") else None
        props = feat.get("properties", {})
        rows.append({"properties": props, "geometry": geom})

    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=TARGET_CRS)
    return gdf


def fetch_from_wfs(
    wfs_url: str,
    layer_name: str = DEFAULT_LAYER,
    page_size: int = 500,
    max_features: int = 500,
    timeout: int = 60,
) -> gpd.GeoDataFrame:
    """Fetch BORIS zonal land-value polygons from LGLN WFS (GML 3.2) with paging."""
    logger.info("Connecting to LGLN WFS endpoint: %s", wfs_url)
    wfs = WebFeatureService(url=wfs_url, version="2.0.0", timeout=timeout)
    available_layers = list(wfs.contents.keys())
    if not available_layers:
        raise RuntimeError("No WFS layers advertised by LGLN endpoint.")

    if layer_name not in available_layers:
        raise ValueError(
            f"Requested layer '{layer_name}' not found. Available: {available_layers}"
        )

    logger.info(
        "Using WFS layer: %s (page_size=%d, max_features=%d)",
        layer_name,
        page_size,
        max_features,
    )

    rows: List[Dict[str, Any]] = []
    start_index = 0

    while True:
        if max_features > 0:
            remaining = max_features - len(rows)
            if remaining <= 0:
                break
            count = min(page_size, remaining)
        else:
            count = page_size

        params = {
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": layer_name,
            "outputFormat": WFS_GML_FORMAT,
            "srsName": TARGET_CRS,
            "count": count,
            "startIndex": start_index,
        }
        logger.info("Fetching WFS page: startIndex=%d, count=%d", start_index, count)
        resp = requests.get(wfs_url, params=params, timeout=timeout)
        resp.raise_for_status()

        page_gdf = gpd.read_file(io.BytesIO(resp.content))
        if page_gdf.empty:
            logger.info("Reached end of WFS features at startIndex=%d", start_index)
            break

        if page_gdf.crs is None:
            page_gdf = page_gdf.set_crs(TARGET_CRS)
        elif page_gdf.crs.to_string() != TARGET_CRS:
            logger.info("Reprojecting from %s to %s", page_gdf.crs.to_string(), TARGET_CRS)
            page_gdf = page_gdf.to_crs(TARGET_CRS)

        geom_col = page_gdf.geometry.name
        prop_cols = [c for c in page_gdf.columns if c != geom_col]

        for _, feat_row in page_gdf.iterrows():
            props = {
                col: (None if pd.isna(feat_row[col]) else feat_row[col])
                for col in prop_cols
            }
            rows.append({"properties": props, "geometry": feat_row[geom_col]})

        fetched_count = len(page_gdf)
        start_index += fetched_count
        if fetched_count < count:
            break

    return gpd.GeoDataFrame(rows, geometry="geometry", crs=TARGET_CRS)


def upsert_records(db_uri: str, records: List[Dict[str, Any]]) -> int:
    """Idempotent upsert into iris_staging.spatial_boris_staging ONLY."""
    engine = create_engine(db_uri)
    upsert_sql = text(
        """
        INSERT INTO iris_staging.spatial_boris_staging (
            zone_id, state_code, valuation_date, land_value_eur_m2,
            land_use_type, municipality, geom, source_name, source_url,
            raw_attributes, updated_at
        )
        VALUES (
            :zone_id, :state_code, :valuation_date, :land_value_eur_m2,
            :land_use_type, :municipality,
            ST_GeomFromText(:geom_wkt, 25832),
            :source_name, :source_url,
            CAST(:raw_attributes AS JSONB),
            NOW()
        )
        ON CONFLICT (zone_id, valuation_date)
        DO UPDATE SET
            land_value_eur_m2 = EXCLUDED.land_value_eur_m2,
            land_use_type = EXCLUDED.land_use_type,
            municipality = EXCLUDED.municipality,
            geom = EXCLUDED.geom,
            source_url = EXCLUDED.source_url,
            raw_attributes = EXCLUDED.raw_attributes,
            updated_at = NOW();
        """
    )

    inserted_or_updated = 0
    with engine.begin() as conn:
        for rec in records:
            conn.execute(upsert_sql, rec)
            inserted_or_updated += 1
    return inserted_or_updated


def run_pipeline() -> Dict[str, int]:
    db_uri = os.environ.get("DB_URI")
    if not db_uri:
        raise EnvironmentError("DB_URI environment variable is required.")

    use_fixture = os.environ.get("USE_FIXTURE", "false").lower() == "true"
    wfs_url = os.environ.get("BORIS_WFS_URL", DEFAULT_WFS_URL)
    layer_name = os.environ.get("BORIS_LAYER", DEFAULT_LAYER)
    page_size = int(os.environ.get("WFS_PAGE_SIZE", "500"))
    max_features = int(os.environ.get("MAX_FEATURES", "500"))
    fixture_path = os.environ.get("FIXTURE_PATH", "tests/fixture_boris.json")

    if use_fixture:
        gdf = load_from_fixture(fixture_path)
        source_url = f"fixture://{fixture_path}"
    else:
        gdf = fetch_from_wfs(
            wfs_url=wfs_url,
            layer_name=layer_name,
            page_size=page_size,
            max_features=max_features,
        )
        source_url = wfs_url

    processed = 0
    failed = 0
    valid_records = []

    for _, row in gdf.iterrows():
        processed += 1
        rec, err = normalize_feature(row["properties"], row["geometry"], source_url)
        if err:
            failed += 1
            logger.warning("Skipping feature #%d: %s", processed, err)
            continue
        valid_records.append(rec)

    inserted = upsert_records(db_uri, valid_records) if valid_records else 0

    summary = {"processed": processed, "inserted": inserted, "failed": failed}
    logger.info(
        "Pipeline finished | processed=%d | inserted_or_updated=%d | failed=%d",
        processed,
        inserted,
        failed,
    )
    return summary


if __name__ == "__main__":
    try:
        run_pipeline()
    except Exception as exc:
        logger.error("Pipeline failed: %s", exc)
        sys.exit(1)
