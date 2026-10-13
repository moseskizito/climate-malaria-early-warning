from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import geopandas as gpd
import numpy as np
from climalaria import cap
from climalaria.climate import district_means, simulate_grid
from climalaria.malaria import simulate_cases, temp_suitability
from climalaria.model import exceed_probability, fit, lag_correlation, level

D = gpd.read_file(Path(__file__).resolve().parents[1] / "data/uganda_districts.geojson")
ds = simulate_grid(months=60)
clim = district_means(ds, D)
df = simulate_cases(clim, D.drop(columns="geometry")).dropna(subset=["rain_lag2", "tsuit_lag1"])


def test_every_district_gets_climate():
    assert clim.district_code.nunique() == 112
    assert clim.rain_mm.notna().all() and (clim.rain_mm >= 0).all()


def test_highlands_cooler_than_north():
    t = clim.groupby("district_code").temp_c.mean()
    code = D.set_index("district").district_code
    assert t[code["Kabale"]] < t[code["Gulu"]]


def test_rainfall_leads_malaria_by_one_month():
    lags = lag_correlation(df)
    assert lags.loc[lags.correlation.idxmax(), "lag_months"] in (1, 2)


def test_model_recovers_positive_rain_effect():
    model, _ = fit(df)
    assert model.params["rain_lag1"] > 0


def test_probability_and_levels():
    p = exceed_probability(np.array([100.0, 2000.0]), np.array([500.0, 500.0]))
    assert p[0] < 0.05 < 0.95 < p[1]
    assert list(level(np.array([0.05, 0.2, 0.4, 0.9]))) == ["NORMAL", "WATCH", "WARNING", "ALERT"]
    assert temp_suitability(25) > temp_suitability(15)


def test_cap_valid():
    a = cap.build_alert(sender="x@y", event="Malaria upsurge risk", category="Health", urgency="Expected",
                        severity="Severe", certainty="Likely", headline="h", description="d", instruction="i",
                        area_desc="a", polygon=cap.polygon_string(D.geometry.iloc[5]), geocode=("UG-DISTRICT", "UG-D006"))
    assert cap.validate(a) == []
