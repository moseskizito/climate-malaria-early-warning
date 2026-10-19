"""Real-data loaders, checked on files shaped like DHIS2, CHIRPS and ERA5 exports (built from the simulation)."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import geopandas as gpd
import pandas as pd
import pytest
from climalaria import real_data
from climalaria.climate import district_means, simulate_grid
from climalaria.malaria import simulate_cases

ROOT = Path(__file__).resolve().parents[1]
CASES_COL = "105-EP01c. Malaria confirmed (B/s & RDT)"


def make_inputs(tmp: Path, start="2018-01", months=105):
    """Write DHIS2-, CHIRPS- and ERA5-shaped files to `tmp`; returns their paths."""
    D = gpd.read_file(ROOT / "data/uganda_districts.geojson")
    ds = simulate_grid(start=start, months=months)
    sim = simulate_cases(district_means(ds, D), D.drop(columns="geometry"))
    D.rename(columns={"district_code": "id", "district": "name"})[["id", "name", "geometry"]].to_file(tmp / "districts.geojson")
    pd.DataFrame({"periodid": sim.month.dt.strftime("%Y%m"), "organisationunitid": sim.district_code,
                  "organisationunitname": sim.district + " District", "orgunitlevel2": sim.region,
                  CASES_COL: sim.cases}).to_csv(tmp / "cases.csv", index=False)
    pd.DataFrame({"district_code": D.district_code, "census_year": 2024, "population": 300000}).to_csv(tmp / "pop.csv", index=False)
    ds.precip.rename({"lat": "latitude", "lon": "longitude"}).to_dataset(name="precip").to_netcdf(tmp / "chirps.nc")
    (ds.t2m.isel(lat=slice(None, None, 2), lon=slice(None, None, 2)) + 273.15).rename(
        {"lat": "latitude", "lon": "longitude", "time": "valid_time"}).to_dataset(name="t2m").to_netcdf(tmp / "era5.nc")
    return {k: tmp / f for k, f in dict(cases="cases.csv", pop="pop.csv", bounds="districts.geojson",
                                        chirps="chirps.nc", era5="era5.nc").items()}


@pytest.fixture(scope="module")
def inputs(tmp_path_factory):
    return make_inputs(tmp_path_factory.mktemp("real"))


def test_hmis_cases_parsed(inputs):
    c = real_data.load_hmis_cases(inputs["cases"])
    assert list(c.columns) == ["district_code", "district", "region", "month", "cases"]
    assert c.month.min() == pd.Timestamp("2018-01-01") and c.cases.ge(0).all()


def test_climate_standardised_to_degc_and_common_grid(inputs):
    ds = real_data.load_climate(inputs["chirps"], inputs["era5"])
    assert set(ds.data_vars) == {"precip", "t2m"} and ds.t2m.shape == ds.precip.shape
    assert 10 < float(ds.t2m.mean()) < 30


def test_population_projected_from_census(inputs):
    p = real_data.load_population(inputs["pop"]).set_index("year")
    assert p.loc[2024, "population"].iloc[0] == 300000 and p.loc[2018, "population"].iloc[0] < 300000


def test_panel_has_model_columns(inputs):
    D = real_data.load_boundaries(inputs["bounds"])
    clim = district_means(real_data.load_climate(inputs["chirps"], inputs["era5"]), D)
    df = real_data.build_panel(clim, real_data.load_hmis_cases(inputs["cases"]), real_data.load_population(inputs["pop"]))
    for col in ["rain_lag1", "rain_lag2", "tsuit_lag1", "cases", "population", "region"]:
        assert col in df
    real_data.check_history(df, "2022-12-01")
    with pytest.raises(SystemExit):
        real_data.check_history(df[df.month >= "2022-01-01"], "2022-12-01")


def test_late_reports_detected():
    m = pd.date_range("2024-01", "2025-09", freq="MS")
    c = pd.DataFrame({"month": m, "cases": [100] * (len(m) - 2) + [30, 20]})
    assert real_data.suggest_last_complete_month(c) == "2025-07"
