-- PostGIS schema for the climate-informed malaria early-warning system
-- Districts:  ogr2ogr -f PostgreSQL PG:"dbname=ews" data/uganda_districts.geojson -nln admin.districts -lco GEOMETRY_NAME=geom
-- Real gridded rainfall (CHIRPS GeoTIFF) can be loaded as rasters:
--   raster2pgsql -s 4326 -I -C -t 50x50 -F chirps-v2.0.2026.08.tif climate.chirps_monthly | psql ews
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE SCHEMA IF NOT EXISTS admin;
CREATE SCHEMA IF NOT EXISTS climate;
CREATE SCHEMA IF NOT EXISTS health;

CREATE TABLE IF NOT EXISTS admin.districts (
    district_code text PRIMARY KEY,
    district      text NOT NULL,
    region        text,
    geom          geometry(MultiPolygon, 4326) NOT NULL
);
CREATE INDEX IF NOT EXISTS districts_geom_gix ON admin.districts USING gist (geom);

-- Grid cells (one row per cell per month) - vector alternative to rasters
CREATE TABLE IF NOT EXISTS climate.grid_monthly (
    month    date NOT NULL,
    cell     geometry(Point, 4326) NOT NULL,
    precip   real,           -- mm/month
    t2m      real,           -- deg C
    PRIMARY KEY (month, cell)
);
CREATE INDEX IF NOT EXISTS grid_cell_gix ON climate.grid_monthly USING gist (cell);

-- Zonal statistics: district mean of grid cells falling inside each polygon
CREATE OR REPLACE VIEW climate.v_district_monthly AS
SELECT d.district_code, g.month, AVG(g.precip) AS rain_mm, AVG(g.t2m) AS temp_c, COUNT(*) AS n_cells
FROM climate.grid_monthly g
JOIN admin.districts d ON ST_Within(g.cell, d.geom)
GROUP BY d.district_code, g.month;

CREATE TABLE IF NOT EXISTS health.malaria_monthly (
    district_code text REFERENCES admin.districts,
    month         date NOT NULL,
    cases         integer NOT NULL CHECK (cases >= 0),
    population    integer,
    PRIMARY KEY (district_code, month)
);

-- WHO-style epidemic threshold: mean + 2 SD of the same calendar month in previous years
CREATE OR REPLACE VIEW health.v_epidemic_threshold AS
SELECT district_code, EXTRACT(month FROM month)::int AS cal_month,
       AVG(cases) + 2 * STDDEV_SAMP(cases) AS threshold
FROM health.malaria_monthly
WHERE month < date_trunc('year', CURRENT_DATE)
GROUP BY district_code, EXTRACT(month FROM month);

-- Latest month: cases vs threshold, with lagged rainfall, ready for GeoServer (WMS/WFS) styling
CREATE OR REPLACE VIEW health.v_malaria_status AS
SELECT d.district_code, d.district, m.month, m.cases, t.threshold,
       (m.cases > t.threshold) AS above_threshold,
       c1.rain_mm AS rain_lag1, d.geom
FROM health.malaria_monthly m
JOIN admin.districts d USING (district_code)
JOIN health.v_epidemic_threshold t
  ON t.district_code = m.district_code AND t.cal_month = EXTRACT(month FROM m.month)
LEFT JOIN climate.v_district_monthly c1
  ON c1.district_code = m.district_code AND c1.month = (m.month - interval '1 month')::date
WHERE m.month = (SELECT MAX(month) FROM health.malaria_monthly);
