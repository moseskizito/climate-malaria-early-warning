"""Download real monthly climate for Uganda into data/real/.

  CHIRPS v2.0 rainfall   one global file per year (~165 MB) from the Climate Hazards Center, cut to Uganda
                         and saved as data/real/chirps_YYYY.nc (~0.1 MB); the global file is then deleted.
  2 m temperature        either NASA POWER (MERRA-2 reanalysis, 0.5 deg, no account, up to a few days ago; default)
                         -> data/real/t2m_nasapower_START_END.nc
                         or ERA5 monthly means from the Copernicus Climate Data Store (0.25 deg; needs
                         `pip install cdsapi` and a free CDS account with ~/.cdsapirc) -> data/real/t2m_era5_START_END.nc
                         Keep only one temperature file in data/real/.

Usage:  python scripts/fetch_climate.py --start 2018 --end 2026                    # CHIRPS + NASA POWER
        python scripts/fetch_climate.py --start 2018 --end 2026 --temperature era5
        python scripts/fetch_climate.py --start 2018 --end 2026 --only chirps
"""
from __future__ import annotations

import argparse
import datetime as dt
import shutil
import urllib.request
from pathlib import Path

import pandas as pd
import xarray as xr

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "real"
POWER_URL = ("https://power.larc.nasa.gov/api/temporal/daily/regional?parameters=T2M&community=AG"
             "&latitude-min={south}&latitude-max={north}&longitude-min={west}&longitude-max={east}"
             "&start={start}&end={end}&format=NETCDF")
CHIRPS_URL = "https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_monthly/netcdf/byYear/chirps-v2.0.{year}.monthly.nc"
AREA = dict(north=4.3, west=29.5, south=-1.5, east=35.1)


def fetch_chirps(year: int, force: bool = False) -> Path:
    dest = OUT / f"chirps_{year}.nc"
    if dest.exists() and not force:
        print(f"CHIRPS {year}: already have {dest.name}")
        return dest
    tmp = OUT / f"_chirps_global_{year}.nc"
    print(f"CHIRPS {year}: downloading global file (~165 MB)...")
    with urllib.request.urlopen(CHIRPS_URL.format(year=year)) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, length=1 << 20)
    with xr.open_dataset(tmp) as ds:
        ug = ds.sel(latitude=slice(AREA["south"], AREA["north"]), longitude=slice(AREA["west"], AREA["east"])).load()
    ug.to_netcdf(dest)
    tmp.unlink()
    print(f"CHIRPS {year}: saved {dest.name} ({ug.sizes['time']} months)")
    return dest


def fetch_nasapower(start: int, end: int, force: bool = False) -> Path:
    """Daily 2 m temperature from NASA POWER, averaged to calendar months (months with fewer than 25 days are dropped)."""
    dest = OUT / f"t2m_nasapower_{start}_{end}.nc"
    if dest.exists() and not force:
        print(f"NASA POWER: already have {dest.name}")
        return dest
    parts = []
    for year in range(start, end + 1):
        last = min(dt.date(year, 12, 31), dt.date.today() - dt.timedelta(days=1))
        if last.year < year:
            break
        tmp = OUT / f"_power_{year}.nc"
        print(f"NASA POWER {year}: requesting daily temperature...")
        url = POWER_URL.format(**AREA, start=f"{year}0101", end=last.strftime("%Y%m%d"))
        with urllib.request.urlopen(url, timeout=600) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, length=1 << 20)
        with xr.open_dataset(tmp) as ds:
            t = ds["T2M"].where(ds["T2M"] > -100).load()
        tmp.unlink()
        days = (t.notnull().mean(["lat", "lon"]) >= 0.5).resample(time="MS").sum()  # a few lake/edge cells can be empty
        monthly = t.resample(time="MS").mean().sel(time=days.time[days >= 25])
        parts.append(monthly)
    t2m = xr.concat(parts, dim="time").rename("t2m")
    t2m.attrs = {"units": "degC", "long_name": "2 m temperature, monthly mean of daily values (NASA POWER, MERRA-2)"}
    t2m.to_dataset().to_netcdf(dest)
    print(f"NASA POWER: saved {dest.name} ({t2m.sizes['time']} months, "
          f"{pd.Timestamp(t2m.time.values[0]):%b %Y} to {pd.Timestamp(t2m.time.values[-1]):%b %Y})")
    return dest


def fetch_era5(start: int, end: int, force: bool = False) -> Path:
    dest = OUT / f"t2m_era5_{start}_{end}.nc"
    if dest.exists() and not force:
        print(f"ERA5: already have {dest.name}")
        return dest
    try:
        import cdsapi
    except ImportError:
        raise SystemExit("ERA5 needs the CDS client: pip install cdsapi, then put your key in ~/.cdsapirc "
                         "(see https://cds.climate.copernicus.eu/how-to-api)")
    print(f"ERA5: requesting 2 m temperature {start}-{end} (the CDS queue can take a few minutes)...")
    cdsapi.Client().retrieve("reanalysis-era5-single-levels-monthly-means", {
        "product_type": ["monthly_averaged_reanalysis"],
        "variable": ["2m_temperature"],
        "year": [str(y) for y in range(start, end + 1)],
        "month": [f"{m:02d}" for m in range(1, 13)],
        "time": ["00:00"],
        "area": [AREA["north"], AREA["west"], AREA["south"], AREA["east"]],
        "data_format": "netcdf",
        "download_format": "unarchived",
    }, str(dest))
    print(f"ERA5: saved {dest.name}")
    return dest


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=int, default=2018)
    ap.add_argument("--end", type=int, default=2026)
    ap.add_argument("--only", choices=["chirps", "temperature"])
    ap.add_argument("--temperature", choices=["nasapower", "era5"], default="nasapower", help="temperature source (default nasapower)")
    ap.add_argument("--force", action="store_true", help="download again even if the file exists")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.only != "temperature":
        for y in range(a.start, a.end + 1):
            fetch_chirps(y, a.force)
    if a.only != "chirps":
        (fetch_era5 if a.temperature == "era5" else fetch_nasapower)(a.start, a.end, a.force)
