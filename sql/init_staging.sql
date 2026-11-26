
-- Activar extensión espacial PostGIS
CREATE EXTENSION IF NOT EXISTS postgis;

-- Crear esquema aislado de staging (NUNCA escribir en iris_core)
CREATE SCHEMA IF NOT EXISTS iris_staging;

-- Crear tabla destino para zonas BORIS de Baja Sajonia (LGLN)
CREATE TABLE IF NOT EXISTS iris_staging.spatial_boris_staging (
    id BIGSERIAL PRIMARY KEY,
    zone_id VARCHAR(128) NOT NULL,
    state_code VARCHAR(16) NOT NULL DEFAULT 'NI',
    valuation_date DATE NOT NULL,
    land_value_eur_m2 NUMERIC(12, 2),
    land_use_type VARCHAR(128),
    municipality VARCHAR(255),
    geom GEOMETRY(MultiPolygon, 25832) NOT NULL,
    source_name VARCHAR(128) NOT NULL DEFAULT 'LGLN_BORIS_WFS',
    source_url TEXT NOT NULL,
    raw_attributes JSONB,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_boris_zone_date UNIQUE (zone_id, valuation_date)
);

-- Índice espacial para consultas rápidas de mapas
CREATE INDEX IF NOT EXISTS idx_spatial_boris_staging_geom
    ON iris_staging.spatial_boris_staging
    USING GIST (geom);
    