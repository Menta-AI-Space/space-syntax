-- Post-import setup: roads_clean service table + spatial indexes
-- Run after osm2pgsql import:
--   psql -h localhost -U spacesyntax -d spacesyntax -f scripts/init_db.sql

CREATE EXTENSION IF NOT EXISTS postgis;

-- Keep indexes on raw OSM table for re-builds
CREATE INDEX IF NOT EXISTS planet_osm_line_way_idx
  ON planet_osm_line USING GIST (way);

CREATE INDEX IF NOT EXISTS planet_osm_line_highway_idx
  ON planet_osm_line (highway)
  WHERE highway IS NOT NULL;

-- Service-layer cleaned roads table (query this, not planet_osm_line).
-- Includes both vehicle-usable and pedestrian ways. Each row carries two
-- boolean mode flags derived from the highway tag:
--   drivable = car-usable roads including highways (excludes pedestrian-only)
--   walkable = pedestrian ways + general roads + outdoor tracks,
--              excluding auto-only motorway/trunk and indoor corridors
-- General roads (primary..residential, service, living_street, unclassified)
-- overlap into BOTH modes.
DROP TABLE IF EXISTS roads_clean;

CREATE TABLE roads_clean AS
SELECT
  osm_id,
  highway,
  name,
  way AS geom,
  ST_Transform(way, 5179) AS geom_5179,
  ST_Length(ST_Transform(way, 5179)) AS length_m,
  CASE WHEN layer ~ '^-?[0-9]+$' THEN layer::integer ELSE 0 END AS layer,
  (bridge IS NOT NULL AND bridge NOT IN ('no', 'false', '0')) AS bridge,
  (tunnel IS NOT NULL AND tunnel NOT IN ('no', 'false', '0')) AS tunnel,
  (highway IN (
    'motorway', 'trunk', 'busway', 'track', 'primary', 'secondary', 'tertiary',
    'residential', 'service', 'living_street', 'unclassified', 'road',
    'motorway_link', 'trunk_link', 'primary_link', 'secondary_link', 'tertiary_link'
  )) AS drivable,
  (highway IN (
    'footway', 'pedestrian', 'steps', 'path', 'track', 'cycleway', 'bridleway', 'busway',
    'primary', 'secondary', 'tertiary',
    'residential', 'service', 'living_street', 'unclassified', 'road',
    'primary_link', 'secondary_link', 'tertiary_link'
  )) AS walkable
FROM planet_osm_line
WHERE highway IS NOT NULL
  AND ST_GeometryType(way) IN ('ST_LineString', 'ST_MultiLineString')
  AND highway IN (
    'motorway', 'trunk', 'busway', 'primary', 'secondary', 'tertiary',
    'residential', 'service', 'living_street', 'unclassified', 'road',
    'motorway_link', 'trunk_link', 'primary_link', 'secondary_link', 'tertiary_link',
    'footway', 'pedestrian', 'steps', 'path', 'track', 'cycleway', 'bridleway'
  );

-- Flatten MultiLineString to LineString rows where needed
-- (osm2pgsql usually stores as LineString; this handles edge cases)
DELETE FROM roads_clean WHERE geom IS NULL OR ST_IsEmpty(geom);

CREATE INDEX roads_clean_geom_5179_idx
  ON roads_clean USING GIST (geom_5179);

CREATE INDEX roads_clean_geom_idx
  ON roads_clean USING GIST (geom);

CREATE INDEX roads_clean_highway_idx
  ON roads_clean (highway);

CREATE INDEX roads_clean_osm_id_idx
  ON roads_clean (osm_id);

CREATE INDEX roads_clean_drivable_idx
  ON roads_clean (drivable) WHERE drivable;

CREATE INDEX roads_clean_walkable_idx
  ON roads_clean (walkable) WHERE walkable;

ANALYZE roads_clean;
