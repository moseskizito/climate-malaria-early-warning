"""Gridded monthly climate over Uganda (SIMULATED, CHIRPS/ERA5-like) and district aggregation.

The grid mimics CHIRPS rainfall (mm/month) and ERA5 2 m temperature (deg C) at 0.25 deg.
Uganda's broad pattern is reproduced: two rainy seasons (Mar-May, Sep-Nov) in the south,
a longer single season in the north, cooler south-western highlands. Wet anomalies
are injected (late 2019, late 2023, and a July-August 2026 scenario over West Nile/Acholi; similar to strong positive Indian Ocean Dipole /
El Nino short rains) so the early-warning chain can be tested.

To use real data, replace `simulate_grid` with CHIRPS / ERA5 NetCDF loaded by xarray
(see data/REAL_DATA.md); `district_means` works unchanged on any lat/lon grid.
"""
from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr
from shapely.geometry import Point


def simulate_grid(start="2015-01", months=140, res=0.25, seed=5) -> xr.Dataset:
    rng = np.random.default_rng(seed)
    lats = np.round(np.arange(-1.5, 4.25 + 1e-9, res), 3)
    lons = np.round(np.arange(29.5, 35.0 + 1e-9, res), 3)
    time = pd.date_range(start, periods=months, freq="MS")
    LAT, LON = np.meshgrid(lats, lons, indexing="ij")
    north = np.clip((LAT - 1.5) / 2.5, 0, 1)  # 0 south ... 1 north
    rain = np.zeros((months, len(lats), len(lons)))
    temp = np.zeros_like(rain)
    highland = np.exp(-(((LAT + 1.0) / 0.8) ** 2 + ((LON - 29.9) / 0.6) ** 2))  # Kabale/Kisoro highlands
    elgon = np.exp(-(((LAT - 1.15) / 0.3) ** 2 + ((LON - 34.5) / 0.3) ** 2))   # Mt Elgon
    for i, t in enumerate(time):
        m = t.month
        bimodal = 60 + 90 * np.exp(-((m - 4) / 1.0) ** 2) + 70 * np.exp(-((m - 10.5) / 1.2) ** 2)
        unimodal = 30 + 130 * np.exp(-((m - 7.5) / 2.2) ** 2)
        mean = (1 - north) * bimodal + north * unimodal
        anomaly = 1.0
        if (t.year == 2019 and m in (10, 11, 12)) or (t.year == 2023 and m in (10, 11, 12)):
            anomaly = 1.7 + 0.4 * (LON > 32.5)
        if t.year == 2026 and m in (7, 8):  # demonstration scenario: heavy rains over West Nile / Acholi
            anomaly = 1.0 + 0.9 * ((LAT > 2.2) & (LON < 32.8))
        noise = rng.gamma(8, 1 / 8, size=LAT.shape)
        rain[i] = mean * anomaly * noise
        temp[i] = 24.5 - 0.6 * np.cos(2 * np.pi * (m - 2) / 12) - 6 * highland - 7 * elgon + 1.2 * north \
                  + 0.03 * (t.year - 2015) + rng.normal(0, 0.4, LAT.shape)
    return xr.Dataset(
        {"precip": (("time", "lat", "lon"), rain.astype("float32"), {"units": "mm/month", "long_name": "rainfall (simulated)"}),
         "t2m": (("time", "lat", "lon"), temp.astype("float32"), {"units": "degC", "long_name": "2 m temperature (simulated)"})},
        coords={"time": time, "lat": lats, "lon": lons},
        attrs={"title": "SIMULATED CHIRPS/ERA5-like monthly climate for Uganda", "Conventions": "CF-1.8"})


def district_means(ds: xr.Dataset, districts: gpd.GeoDataFrame) -> pd.DataFrame:
    """Zonal mean of each variable per district (grid-cell centres inside the polygon; nearest cell otherwise)."""
    LAT, LON = np.meshgrid(ds.lat.values, ds.lon.values, indexing="ij")
    cells = gpd.GeoDataFrame({"iy": np.repeat(np.arange(len(ds.lat)), len(ds.lon)),
                              "ix": np.tile(np.arange(len(ds.lon)), len(ds.lat))},
                             geometry=[Point(x, y) for x, y in zip(LON.ravel(), LAT.ravel())], crs="EPSG:4326")
    joined = gpd.sjoin(cells, districts[["district_code", "geometry"]], predicate="within")
    groups = {c: (g.iy.to_numpy(), g.ix.to_numpy()) for c, g in joined.groupby("district_code")}
    for _, d in districts.iterrows():  # small districts with no cell centre -> nearest cell
        if d.district_code not in groups:
            c = d.geometry.centroid
            groups[d.district_code] = (np.array([np.abs(ds.lat.values - c.y).argmin()]),
                                       np.array([np.abs(ds.lon.values - c.x).argmin()]))
    rows = []
    P, T = ds.precip.values, ds.t2m.values
    for code, (iy, ix) in groups.items():
        rows.append(pd.DataFrame({"district_code": code, "month": ds.time.values,
                                  "rain_mm": P[:, iy, ix].mean(axis=1), "temp_c": T[:, iy, ix].mean(axis=1)}))
    return pd.concat(rows, ignore_index=True)
