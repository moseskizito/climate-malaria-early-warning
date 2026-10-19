"""Convert a data-warehouse malaria extract (district, year_month, suspected, tested, confirmed, treated)
to the DHIS2-style file the pipeline reads (data/real/hmis_cases.csv).

District names are matched to DHIS2 org-unit IDs through the boundary file. Rows without a month, stray rows
before the main series starts, and months after the last month with any reported cases are dropped.

Usage:  python scripts/convert_warehouse_cases.py <extract.csv> [--start 2020-01]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("extract")
    ap.add_argument("--start", default="2020-01", help="first month of the series (earlier stray rows are dropped)")
    ap.add_argument("--boundaries", default=str(ROOT / "data/real/districts.geojson"))
    ap.add_argument("--out", default=str(ROOT / "data/real/hmis_cases.csv"))
    a = ap.parse_args()

    raw = pd.read_csv(a.extract)
    d = raw.dropna(subset=["district", "year_month"])
    d = d[d.year_month >= a.start]
    totals = d.groupby("year_month").confirmed.sum()
    last = totals[totals > 0].index.max()
    d = d[d.year_month <= last]

    units = gpd.read_file(a.boundaries)[["id", "name", "region"]]
    m = d.merge(units, left_on="district", right_on="name", how="left")
    unmatched = sorted(m[m.id.isna()].district.unique())
    if unmatched:
        raise SystemExit(f"No DHIS2 org unit for: {', '.join(unmatched)}")
    if m.duplicated(["id", "year_month"]).any():
        raise SystemExit("More than one row for the same district and month")

    out = pd.DataFrame({
        "periodid": m.year_month.str.replace("-", "", regex=False),
        "organisationunitid": m.id,
        "organisationunitname": m.district,
        "orgunitlevel2": m.region,
        "105-EP01c. Malaria confirmed (B/s & RDT)": m.confirmed,
        "tested": m.tested,
        "suspected": m.suspected,
        "treated": m.treated,
    }).sort_values(["organisationunitid", "periodid"])
    out.to_csv(a.out, index=False)
    print(f"Kept {len(out)} rows, {out.organisationunitid.nunique()} districts, {out.periodid.min()} to {out.periodid.max()} "
          f"(dropped {len(raw) - len(out)} rows: no month, before {a.start}, or after {last})")
    print("Saved", a.out)
