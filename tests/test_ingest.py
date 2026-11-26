from shapely.geometry import Polygon

from src.ingest_boris import extract_polygon_geometry, normalize_feature


def test_repairs_invalid_bowtie_polygon():
    # Polígono en forma de moño (se cruza a sí mismo -> inválido)
    bowtie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10), (0, 0)])
    assert not bowtie.is_valid

    repaired = extract_polygon_geometry(bowtie)
    assert repaired is not None
    assert repaired.is_valid
    assert repaired.geom_type == "MultiPolygon"


def test_rejects_empty_geometry():
    props = {"brw_id": "NI-TEST-01", "stichtag": "2025-01-01", "brw": 200.0}
    rec, err = normalize_feature(props, None, "fixture://test")
    assert rec is None
    assert err is not None


def test_normalizes_valid_boris_feature():
    poly = Polygon([(548000, 5802000), (548100, 5802000), (548100, 5802100), (548000, 5802100), (548000, 5802000)])
    props = {
        "brw_id": "NI-03241001-0001",
        "stichtag": "2025-01-01",
        "brw": "450,50",
        "nutzungsart": "Gewerbegebiet (G)",
        "gemeinde": "Hannover",
    }
    rec, err = normalize_feature(props, poly, "fixture://test")
    assert err is None
    assert rec["zone_id"] == "NI-03241001-0001"
    assert rec["valuation_date"] == "2025-01-01"
    assert rec["land_value_eur_m2"] == 450.5
    assert rec["municipality"] == "Hannover"