"""Load real inputs: DHIS2/HMIS malaria cases, UBOS census population, district boundaries, CHIRPS and ERA5.

Everything is keyed by the DHIS2 organisation-unit ID (`district_code`), so cases, population and
boundaries join without name matching. The output of `build_panel` has the same columns the model
uses on simulated data (see `malaria.simulate_cases`).
"""
from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import xarray as xr

from climalaria.malaria import add_lags

UGANDA_BBOX = dict(lat=(-1.5, 4.3), lon=(29.5, 35.1))
CASES_PREFIX = "105-EP01c"  # HMIS 105: Malaria confirmed (B/s & RDT)


def load_hmis_cases(path: str | Path, column: str | None = None) -> pd.DataFrame:
    """DHIS2 analytics CSV (one row per org unit and month) -> district_code, district, region, month, cases.

    Expects the standard DHIS2 export columns `periodid` (YYYYMM), `organisationunitid`,
    `organisationunitname`, `orgunitlevel2` and a data column starting with '105-EP01c'.
    """
    raw = pd.read_csv(path)
    if column is None:
        matches = [c for c in raw.columns if c.startswith(CASES_PREFIX)]
        if not matches:
            raise ValueError(f"{path}: no column starting with '{CASES_PREFIX}' (confirmed malaria); pass column=")
        column = matches[0]
    df = pd.DataFrame({
        "district_code": raw["organisationunitid"],
        "district": raw["organisationunitname"],
        "region": raw.get("orgunitlevel2", pd.Series("Uganda", index=raw.index)),
        "month": pd.to_datetime(raw["periodid"].astype(str), format="%Y%m"),
        "cases": pd.to_numeric(raw[column], errors="coerce"),
    })
    df = df.dropna(subset=["cases"])
    if df.duplicated(["district_code", "month"]).any():
        raise ValueError(f"{path}: more than one row for the same org unit and month")
    return df.sort_values(["district_code", "month"]).reset_index(drop=True)


def load_population(path: str | Path, growth: float = 0.029) -> pd.DataFrame:
    """Census population (district_code, census_year, population) -> one population per district and year.

    Returns district_code, year, population, projected from the census year with a constant
    annual growth rate (UBOS national average about 2.9%).
    """
    p = pd.read_csv(path)
    years = np.arange(2010, 2036)
    out = p[["district_code", "census_year", "population"]].merge(pd.DataFrame({"year": years}), how="cross")
    out["population"] = (out.population * (1 + growth) ** (out.year - out.census_year)).round()
    return out[["district_code", "year", "population"]]


def load_boundaries(path: str | Path) -> gpd.GeoDataFrame:
    """District polygons keyed by DHIS2 org-unit ID.

    Accepts a DHIS2 export (`/api/organisationUnits.geojson?level=3`, properties `id`/`code` and `name`)
    or any file that already has `district_code` and `district`.
    """
    g = gpd.read_file(path)
    if "district_code" not in g.columns:
        key = "id" if "id" in g.columns else None
        if key is None:
            raise ValueError(f"{path}: needs a 'district_code' or DHIS2 'id' property on each feature")
        g = g.rename(columns={key: "district_code", "name": "district"})
    g = g[g.geometry.notna() & g.geom_type.isin(["Polygon", "MultiPolygon"])]
    return g.set_crs("EPSG:4326", allow_override=True)[["district_code", "district", "geometry"]]


def _standardise(ds: xr.Dataset) -> xr.Dataset:
    ren = {k: v for k, v in {"latitude": "lat", "longitude": "lon", "valid_time": "time", "X": "lon", "Y": "lat", "T": "time"}.items()
           if k in ds.dims or k in ds.coords}
    ds = ds.rename(ren)
    ds = ds.sortby("lat").sortby("lon")
    ds = ds.sel(lat=slice(*UGANDA_BBOX["lat"]), lon=slice(*UGANDA_BBOX["lon"]))
    ds["time"] = pd.to_datetime(ds.time.values).to_period("M").to_timestamp()
    return ds


def load_climate(chirps: str | Path | list, era5: str | Path | list) -> xr.Dataset:
    """CHIRPS monthly rainfall + ERA5 monthly 2 m temperature -> Dataset(precip mm/month, t2m degC) on the CHIRPS grid.

    `chirps` and `era5` may be single NetCDF files or lists of them (e.g. one CHIRPS file per year).
    Temperature is interpolated to the rainfall grid; only months present in both are kept.
    """
    def open_(p):
        files = p if isinstance(p, list) else [p]
        return xr.concat([_standardise(xr.open_dataset(f)) for f in files], dim="time").sortby("time")

    rain = open_(chirps)
    rname = "precip" if "precip" in rain else next(v for v in rain.data_vars if "prec" in v.lower())
    precip = rain[rname]
    if "day" in str(precip.attrs.get("units", "")).lower():
        precip = precip * precip.time.dt.days_in_month
    precip = precip.where(precip >= 0)

    temp = open_(era5)
    t2m = temp["t2m"]
    for d in [d for d in t2m.dims if d not in ("time", "lat", "lon")]:  # e.g. ERA5 'expver' or 'number'
        t2m = t2m.mean(d) if t2m.sizes[d] > 1 else t2m.squeeze(d, drop=True)
    if float(t2m.mean()) > 200:
        t2m = t2m - 273.15
    # linear inside the temperature grid; nearest cell for rainfall cells beyond its edge or next to empty cells
    filled = t2m
    for dim in ("lon", "lat"):
        filled = filled.interpolate_na(dim, method="nearest", fill_value="extrapolate")
    near = filled.reindex(lat=precip.lat, lon=precip.lon, method="nearest")
    t2m = t2m.interp(lat=precip.lat, lon=precip.lon, method="linear").fillna(near)

    months = np.intersect1d(precip.time.values, t2m.time.values)
    if len(months) == 0:
        raise ValueError("CHIRPS and ERA5 files have no month in common")
    ds = xr.Dataset({"precip": precip.sel(time=months), "t2m": t2m.sel(time=months)}).load()
    ds.precip.attrs.update(units="mm/month", long_name="rainfall (CHIRPS v2.0)")
    ds.t2m.attrs.update(units="degC", long_name="2 m temperature (ERA5)")
    ds.attrs = {"title": "CHIRPS rainfall and ERA5 temperature, monthly, Uganda", "Conventions": "CF-1.8"}
    return ds


def build_panel(clim: pd.DataFrame, cases: pd.DataFrame, population: pd.DataFrame,
                last_complete_month: str | None = None) -> pd.DataFrame:
    """District climate (from `district_means`) + cases + population -> the model's monthly panel.

    Months after `last_complete_month` (reports still coming in) keep their climate but lose their cases.
    """
    df = add_lags(clim)
    df = df.merge(cases, on=["district_code", "month"], how="inner")
    df["year"] = df.month.dt.year
    df = df.merge(population, on=["district_code", "year"], how="left").drop(columns="year")
    missing = df[df.population.isna()].district.unique()
    if len(missing):
        raise ValueError(f"No population for: {', '.join(map(str, missing[:10]))}")
    if last_complete_month:
        df = df[df.month <= pd.Timestamp(last_complete_month)]
    return df.reset_index(drop=True)


def check_history(df: pd.DataFrame, train_end: str, min_years: int = 3) -> None:
    """Stop with a clear message when there is too little case history before `train_end`."""
    hist = df[df.month <= pd.Timestamp(train_end)]
    years = hist.month.dt.to_period("M").nunique() / 12
    if years < min_years:
        span = f"{df.month.min():%b %Y} to {df.month.max():%b %Y}" if len(df) else "no months"
        raise SystemExit(
            f"The case data covers {span}, which leaves {years:.1f} years before the training cut-off "
            f"({pd.Timestamp(train_end):%b %Y}). The model and the epidemic thresholds need at least {min_years} years.\n"
            "Export confirmed malaria (105-EP01c) from DHIS2 for 2018 onwards, or set --train-end later.")


def suggest_last_complete_month(cases: pd.DataFrame, drop: float = 0.7) -> str | None:
    """If the most recent months' national cases are below `drop` x the same months a year earlier (reports still
    coming in), the month before that run; otherwise None. A year-on-year comparison needs 13+ months of data."""
    tot = cases.groupby("month").cases.sum()
    prev = tot.shift(12, freq="MS").reindex(tot.index)
    short = (prev.notna() & (tot < drop * prev)).to_numpy()
    n = 0
    while n < len(short) and short[len(short) - 1 - n]:
        n += 1
    if n == 0:
        return None
    return (tot.index[len(tot) - n] - pd.offsets.MonthBegin(1)).strftime("%Y-%m")
