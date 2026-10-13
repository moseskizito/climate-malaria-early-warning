# Switching to real data

| Input | Real source | Format | How to plug in |
|---|---|---|---|
| Rainfall | **CHIRPS v2.0** monthly, 0.05° (Climate Hazards Center, UCSB) | NetCDF / GeoTIFF | `xr.open_dataset("chirps-v2.0.monthly.nc").sel(latitude=slice(-1.5,4.3), longitude=slice(29.5,35.1))`, rename to `precip`, `lat`, `lon` |
| Temperature | **ERA5** monthly 2 m temperature (Copernicus CDS) | NetCDF | Convert K to °C, rename to `t2m` |
| Malaria cases | **DHIS2 / HMIS 105** confirmed malaria (district, monthly) or weekly **033B** | CSV via DHIS2 Web API | Columns `district_code, month, cases, population` |
| Districts | Uganda Bureau of Statistics or geoBoundaries ADM2 | GeoJSON / Shapefile | Keep `district_code, district, region, geometry` |
| Seasonal outlooks | ICPAC Greater Horn of Africa Climate Outlook Forum, UNMA | Gridded forecast | Use forecast rainfall in place of observed lag-1 rainfall to extend lead time |

`district_means()` works on any regular lat/lon grid; nothing else changes.
