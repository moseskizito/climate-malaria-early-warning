# Running on real data

```bash
python run_pipeline.py --data real          # results at http://localhost:8000, files in outputs/real/
```

Put these files in `data/real/` (or pass other paths with the options shown):

| File | Source | Status | Option |
|---|---|---|---|
| `hmis_cases.csv` | Confirmed malaria by district and month: DHIS2 export, or a warehouse extract converted with `scripts/convert_warehouse_cases.py` | **Have** Jan 2020 – Sep 2026 (warehouse extract) | `--cases` |
| `../uganda_population_census2024.csv` | UBOS 2024 census, keyed by DHIS2 org-unit ID | **Included** | `--population` |
| `districts.geojson` | DHIS2 district boundaries (copied from `dwh_visualization/public/assets/uganda_districts.geojson`; 146 units, DHIS2 IDs) | **Have** | `--boundaries` |
| `chirps_YYYY.nc` | CHIRPS v2.0 monthly rainfall | **Have** 2018 – Aug 2026 | `--chirps` |
| `t2m_nasapower_START_END.nc` (or `t2m_era5_…`) | Monthly 2 m temperature: NASA POWER (no account) or ERA5 | **Have** (NASA POWER) | `--temperature` |

## 1. Malaria cases (DHIS2)

Data Visualizer or Pivot Table: data `105-EP01c. Malaria confirmed (B/s & RDT)`, periods monthly **January 2018 to the
latest month**, organisation units **district level** (the 146 districts and cities), then *Download → CSV (raw data)*,
with "Hierarchy" columns on. The file needs `periodid`, `organisationunitid`, `organisationunitname`, `orgunitlevel2`
and the 105-EP01c column, as in the current export.

A warehouse extract with columns `district, year_month, suspected, tested, confirmed, treated` is converted with:

```bash
python scripts/convert_warehouse_cases.py <extract.csv>
```

The skill test trains on months up to `--train-end` (default December 2023, at least 3 years needed) and tests on the
months after it; the live forecast is then refitted on all complete months. By default the model follows the long-term
trend in cases and sets thresholds relative to each district's expected level today; `--no-trend` uses fixed
WHO-style thresholds from past case levels instead.
Months still waiting for late reports are detected and left out (override with `--last-complete-month 2026-06`).

## 2. District boundaries (DHIS2)

Already included (from the DWH visualization project). To refresh after boundary changes, open this in a browser while
logged in to DHIS2 and save the result as `data/real/districts.geojson`:

```
https://<your-dhis2-server>/api/organisationUnits.geojson?level=3
```

Use the level your districts are at (the level with the 146 districts and cities). The feature `id` is the
org-unit ID, which is how cases, population and boundaries are joined.

## 3. Climate (CHIRPS and ERA5)

```bash
python scripts/fetch_climate.py --start 2018 --end 2026            # CHIRPS + NASA POWER temperature (no account)
python scripts/fetch_climate.py --start 2018 --end 2026 --temperature era5   # ERA5 instead (needs cdsapi + CDS key)
```

- CHIRPS: one global file per year (~165 MB) is downloaded from the Climate Hazards Center, cut to Uganda and deleted.
  Final CHIRPS for a month is published about 3 weeks after it ends.
- NASA POWER: daily MERRA-2 temperature at 0.5°, averaged to months; available to a few days ago, no account.
- ERA5 (optional, finer 0.25° grid): needs a free account at https://cds.climate.copernicus.eu and your API key in
  `~/.cdsapirc`. Keep only one temperature file in `data/real/`.

The forecast is for the month after the latest month that has both rainfall and temperature.

## Population (included)

`uganda_population_census2024.csv`: final 2024 National Population and Housing Census (UBOS, Volume 1, 31 Dec 2024) for all
146 districts and cities, keyed by DHIS2 organisation-unit ID (`district_code`). Figures were taken from
[citypopulation.de](https://citypopulation.de/en/uganda/admin/), which republishes the UBOS results; they sum to the national
total of 45,905,417. Two DHIS2 names differ from UBOS: Kampala District = Kampala City, Sembabule = Ssembabule.
Other years are projected from 2024 at 2.9% a year (`load_population(growth=...)`).

## Before acting on the results

- Re-check the lag chart and warning skill on real data; they will differ from the simulation.
- Agree the Alert / Warning / Watch cut-offs (60% / 35% / 15%) with district health teams and the epidemiology unit.
- Districts created after 2018 have shorter histories, so their thresholds are less stable.
- CAP alerts stay `status=Exercise` until the designated authority approves real issuing.
- Later: seasonal rainfall outlooks (ICPAC, UNMA) in place of observed rainfall would extend the lead time,
  and weekly 033B data (mTrac/eIDSR) would give a faster signal.
