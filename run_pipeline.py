"""Climate-informed malaria early warning: gridded climate -> districts -> lag analysis ->
forecast model -> epidemic-threshold probabilities -> CAP alerts, maps and GeoJSON.

Usage:  python run_pipeline.py
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import geopandas as gpd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Patch

from climalaria import cap
from climalaria.climate import district_means, simulate_grid
from climalaria.malaria import simulate_cases
from climalaria.model import CAP_MAP, epidemic_threshold, exceed_probability, fit, lag_correlation, level, predict

ROOT = Path(__file__).parent
OUT = ROOT / "outputs"
COLORS = {"NORMAL": "#E8EEF4", "WATCH": "#F6D55C", "WARNING": "#ED8B39", "ALERT": "#C0392B"}
TRAIN_END = "2022-12-01"

districts = gpd.read_file(ROOT / "data/uganda_districts.geojson")

# 1. gridded climate (NetCDF) and district aggregation
ds = simulate_grid()
ds.to_netcdf(OUT / "uganda_climate_monthly_simulated.nc")
clim = district_means(ds, districts)
df = simulate_cases(clim, districts.drop(columns="geometry"))
df.drop(columns=["rain_anom_lag1", "rain_anom_lag2"]).to_csv(OUT / "district_climate_malaria_simulated.csv", index=False)
df = df.dropna(subset=["rain_lag2", "tsuit_lag1"])
print(f"Grid {ds.sizes['lat']}x{ds.sizes['lon']} x {ds.sizes['time']} months -> {df.district_code.nunique()} districts")

# 2. which lag links rainfall and malaria?
lags = lag_correlation(df)
print("Rainfall-malaria correlation by lag:", dict(zip(lags.lag_months, lags.correlation)))

# 3. model: train to end-2022, evaluate one-month-ahead warnings 2023-2026
train, test = df[df.month <= TRAIN_END], df[df.month > TRAIN_END].copy()
model, cols = fit(train)
coefs = model.params[["rain_lag1", "rain_lag2", "tsuit_lag1"]]
print("Model effects (rate ratio): rain +50 mm lag1 =", round(float(np.exp(50 * coefs.rain_lag1)), 2),
      "| lag2 =", round(float(np.exp(50 * coefs.rain_lag2)), 2))
thr = epidemic_threshold(train)
test["cal_month"] = pd.to_datetime(test.month).dt.month
test = test.merge(thr, on=["district_code", "cal_month"])
test["predicted"] = predict(model, cols, test)
test["p_exceed"] = exceed_probability(test.predicted.to_numpy(), test.threshold.to_numpy())
test["level"] = level(test.p_exceed.to_numpy())
test["epidemic"] = test.cases > test.threshold


def skill_of(lv):
    warned = pd.Series(lv, index=test.index).isin(["WARNING", "ALERT"])
    hits, misses = int((warned & test.epidemic).sum()), int((~warned & test.epidemic).sum())
    false = int((warned & ~test.epidemic).sum())
    return dict(epidemic_months=int(test.epidemic.sum()), detected=hits, missed=misses,
                hit_rate=round(hits / max(hits + misses, 1), 2), false_alarm_ratio=round(false / max(hits + false, 1), 2))


# seasonal-only baseline (no climate) to show what climate adds
base_model, base_cols = fit(train, climate=False)
base_level = level(exceed_probability(predict(base_model, base_cols, test, climate=False), test.threshold.to_numpy()))
skill = pd.DataFrame([dict(model="seasonal only (no climate)", **skill_of(base_level)),
                      dict(model="climate-informed (rain lag 1-2, temperature)", **skill_of(test.level))])
print("Warning skill, 2023-2026 (WARNING+ vs threshold exceedance):\n" + skill.to_string(index=False))
skill.to_csv(OUT / "warning_skill_2023_2026.csv", index=False)

# 4. forecast for the next month from the latest observed climate
last = clim.month.max()
nxt = last + pd.offsets.MonthBegin(1)
fut = clim[clim.month >= last - pd.offsets.MonthBegin(2)].copy()
fut = pd.concat([fut, pd.DataFrame({"district_code": districts.district_code, "month": nxt})])
from climalaria.malaria import add_lags
fut = add_lags(fut)
fut = fut[fut.month == nxt].merge(df[["district_code", "population"]].drop_duplicates(), on="district_code")
fut["cal_month"] = nxt.month
fut = fut.merge(thr, on=["district_code", "cal_month"])
fut["predicted"] = predict(model, cols, fut)
fut["p_exceed"] = exceed_probability(fut.predicted.to_numpy(), fut.threshold.to_numpy())
fut["level"] = level(fut.p_exceed.to_numpy())
period = nxt.strftime("%Y-%m")
print(f"Forecast for {period}: " + ", ".join(f"{l} {int((fut.level == l).sum())}" for l in ["ALERT", "WARNING", "WATCH", "NORMAL"]))

g = districts.merge(fut[["district_code", "rain_lag1", "predicted", "threshold", "p_exceed", "level"]], on="district_code")
g.to_file(OUT / f"malaria_risk_forecast_{period}.geojson", driver="GeoJSON")

# 5. CAP alerts (WARNING and ALERT)
sent = dt.datetime(nxt.year, nxt.month, 1, 6, 0, tzinfo=dt.timezone.utc)
n = 0
for _, r in g[g.level.isin(["WARNING", "ALERT"])].sort_values("p_exceed", ascending=False).iterrows():
    urg, sev, cer, resp = CAP_MAP[r.level]
    a = cap.build_alert(
        sender="situationroom@opm.go.ug.example", event="Malaria upsurge risk", category="Health",
        urgency=urg, severity=sev, certainty=cer, response_type=resp, sent=sent, expires_days=31,
        headline=f"{r.level}: malaria upsurge likely in {r.district} District ({period})",
        description=(f"Rainfall last month {r.rain_lag1:.0f} mm. Forecast {r.predicted:,.0f} confirmed cases against an "
                     f"epidemic threshold of {r.threshold:,.0f} (probability of exceeding {r.p_exceed:.0%}). "
                     "SIMULATED DATA - EXERCISE."),
        instruction="District Health Team: check ACT and RDT stocks, intensify case management and vector control, report weekly.",
        area_desc=f"{r.district} District, {r.region} Region, Uganda", polygon=cap.polygon_string(r.geometry),
        geocode=("UG-DISTRICT", r.district_code))
    assert not cap.validate(a), cap.validate(a)
    (OUT / "cap_alerts" / f"{r.district_code}_{r.district}_{period}.xml").write_bytes(cap.to_xml(a))
    n += 1
print(f"CAP messages written and validated: {n}")

# 6. figures
#  a. rainfall anomaly grid for the latest month with district outlines
latest = ds.sel(time=last)
clim_mean = ds.precip.groupby("time.month").mean().sel(month=last.month)
anom = (latest.precip / clim_mean - 1) * 100
fig, axes = plt.subplots(1, 2, figsize=(12, 6.2))
im = axes[0].pcolormesh(ds.lon, ds.lat, anom, cmap="BrBG", vmin=-80, vmax=80, shading="nearest")
districts.boundary.plot(ax=axes[0], color="#333", linewidth=0.3)
fig.colorbar(im, ax=axes[0], shrink=0.7, label="rainfall anomaly (%)")
axes[0].set_title(f"Gridded rainfall anomaly, {last:%Y-%m}")
g.plot(ax=axes[1], color=g.level.map(COLORS), edgecolor="white", linewidth=0.4)
axes[1].legend(handles=[Patch(color=COLORS[l], label=l.title()) for l in ["ALERT", "WARNING", "WATCH", "NORMAL"]],
               loc="upper center", bbox_to_anchor=(0.5, 0.02), ncol=4, frameon=False, fontsize=9)
axes[1].set_title(f"Malaria upsurge warning level, forecast for {period}")
for ax in axes:
    ax.set_axis_off()
fig.suptitle("Uganda districts — SIMULATED climate and malaria data (demonstration)", fontsize=10, color="#555")
fig.tight_layout()
fig.savefig(OUT / "rainfall_and_malaria_warning_map.png", dpi=150)

#  b. lag correlation
fig, ax = plt.subplots(figsize=(6, 3.2))
ax.bar(lags.lag_months, lags.correlation, color=["#9DB4C8" if c < lags.correlation.max() else "#2E86C1" for c in lags.correlation])
for x, y in zip(lags.lag_months, lags.correlation):
    ax.text(x, y + 0.01, f"{y:.2f}", ha="center", fontsize=9)
ax.set_xlabel("lag (months after rainfall)")
ax.set_ylabel("correlation")
ax.set_title("Rainfall anomaly vs malaria case anomaly (simulated)")
ax.spines[["top", "right"]].set_visible(False)
fig.tight_layout()
fig.savefig(OUT / "rainfall_malaria_lag.png", dpi=150)

#  c. one district: observed, forecast and threshold
code = test.groupby("district_code").epidemic.sum().idxmax()
s = test[test.district_code == code].sort_values("month")
name = districts.set_index("district_code").loc[code, "district"]
fig, ax = plt.subplots(figsize=(9, 3.4))
ax.plot(s.month, s.cases, color="#555", lw=1.4, label="observed cases")
ax.plot(s.month, s.predicted, color="#2E86C1", lw=1.8, label="1-month-ahead forecast")
ax.plot(s.month, s.threshold, color="#999", ls="--", lw=1, label="epidemic threshold")
w = s[s.level.isin(["WARNING", "ALERT"])]
ax.scatter(w.month, w.predicted, color="#C0392B", s=26, zorder=3, label="warning issued")
ax.set_title(f"{name} District — malaria forecast vs threshold, 2023–2026 (simulated)")
ax.legend(frameon=False, fontsize=8, ncol=4, loc="upper left")
ax.spines[["top", "right"]].set_visible(False)
fig.tight_layout()
fig.savefig(OUT / "district_forecast.png", dpi=150)
print("Outputs in", OUT)
