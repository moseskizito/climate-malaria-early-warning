"""Climate-informed malaria early warning: gridded climate -> districts -> lag analysis ->
forecast model -> epidemic-threshold probabilities -> CAP alerts, maps and GeoJSON.

Usage:  python run_pipeline.py              # run, then view the results at http://localhost:8000
        python run_pipeline.py --no-serve   # run only (outputs/index.html can still be opened directly)
        python run_pipeline.py --open       # run, serve and open the browser
        python run_pipeline.py --data real  # real DHIS2 cases, census population, CHIRPS and ERA5 (see data/REAL_DATA.md)
"""
from __future__ import annotations

import argparse
import datetime as dt
import functools
import glob
import http.server
import webbrowser
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
from climalaria.model import (CAP_MAP, LEVEL_CUTS, calibrate_cuts, epidemic_threshold, estimate_alpha, exceed_probability, fit,
                              lag_correlation, level, predict, relative_threshold_factors, warning_tradeoff)
from climalaria import real_data
from climalaria.report import write_report

parser = argparse.ArgumentParser(description="Run the malaria early-warning pipeline and serve the results.")
parser.add_argument("--no-serve", action="store_true", help="do not start the local web server")
parser.add_argument("--port", type=int, default=8000, help="port for the local web server (default 8000)")
parser.add_argument("--open", action="store_true", help="open the results in the default browser")
parser.add_argument("--data", choices=["simulated", "real"], default="simulated", help="input data (default simulated)")
real_args = parser.add_argument_group("real data (--data real)")
real_args.add_argument("--cases", default="data/real/hmis_cases.csv", help="DHIS2 export with 105-EP01c confirmed malaria by district and month")
real_args.add_argument("--population", default="data/uganda_population_census2024.csv", help="census population by DHIS2 org unit")
real_args.add_argument("--boundaries", default="data/real/districts.geojson", help="district polygons keyed by DHIS2 org-unit ID")
real_args.add_argument("--chirps", default="data/real/chirps_*.nc", help="CHIRPS monthly rainfall NetCDF file(s), glob allowed")
real_args.add_argument("--temperature", "--era5", dest="era5", default="data/real/t2m_*.nc", help="monthly 2 m temperature NetCDF (NASA POWER or ERA5), glob allowed")
real_args.add_argument("--train-end", default="2023-12", help="last month used to train the model for the skill test (default 2023-12)")
real_args.add_argument("--no-trend", action="store_true", help="no long-term trend; thresholds from past case levels (WHO mean + 2 SD)")
real_args.add_argument("--regional", action="store_true", help="a seasonal curve per region (did not improve the backtest, so off by default)")
real_args.add_argument("--no-recent", action="store_true", help="do not use the latest complete month of cases")
real_args.add_argument("--no-calibrate", action="store_true", help="fixed 15/35/60%% cut-offs instead of cut-offs chosen on the last training year")
real_args.add_argument("--last-complete-month", help="ignore cases after this month (YYYY-MM) while reports are still coming in")
args = parser.parse_args()

ROOT = Path(__file__).parent
REAL = args.data == "real"
OUT = ROOT / "outputs" / ("real" if REAL else "")
(OUT / "cap_alerts").mkdir(parents=True, exist_ok=True)
TAG = "real" if REAL else "simulated"
COLORS = {"NORMAL": "#E8EEF4", "WATCH": "#F6D55C", "WARNING": "#ED8B39", "ALERT": "#C0392B"}
TRAIN_END = pd.Timestamp(args.train_end if REAL else "2022-12").strftime("%Y-%m-01")
TREND = REAL and not args.no_trend   # follow the long-term decline; thresholds relative to today's expected level
REFIT = REAL                         # live forecast from a model refitted on all complete months
OPTS = dict(regional=REAL and args.regional, recent=REAL and not args.no_recent)
POOLED = REAL                        # threshold spread per district over all months (steadier than per calendar month)
CALIBRATE = REAL and not args.no_calibrate


def path(p):
    return Path(p) if Path(p).is_absolute() else ROOT / p


def files(pattern):
    found = sorted(glob.glob(str(path(pattern))))
    if not found:
        raise SystemExit(f"No file matches {pattern}. See data/REAL_DATA.md (scripts/fetch_climate.py downloads CHIRPS and ERA5).")
    return found


# 1. gridded climate (NetCDF) and district aggregation
if REAL:
    for f in (args.cases, args.population, args.boundaries):
        if not path(f).exists():
            raise SystemExit(f"Missing {f}. See data/REAL_DATA.md.")
    cases = real_data.load_hmis_cases(path(args.cases))
    districts = real_data.load_boundaries(path(args.boundaries)).drop(columns="district")
    districts = districts.merge(cases[["district_code", "district", "region"]].drop_duplicates("district_code"), on="district_code")
    print(f"Cases: {cases.district_code.nunique()} org units, {cases.month.min():%b %Y} to {cases.month.max():%b %Y}; "
          f"{len(districts)} of them have boundaries")
    ds = real_data.load_climate(files(args.chirps), files(args.era5))
    complete = args.last_complete_month or real_data.suggest_last_complete_month(cases)
    if complete and not args.last_complete_month:
        print(f"Cases drop sharply after {complete} (reports still coming in?), so later months are left out. "
              "Override with --last-complete-month.")
    clim = district_means(ds, districts)
    df = real_data.build_panel(clim, cases, real_data.load_population(path(args.population)), complete)
    real_data.check_history(df, TRAIN_END)
else:
    districts = gpd.read_file(ROOT / "data/uganda_districts.geojson")
    ds = simulate_grid()
    clim = district_means(ds, districts)
    df = simulate_cases(clim, districts.drop(columns="geometry"))
ds.to_netcdf(OUT / f"uganda_climate_monthly_{TAG}.nc")
df.drop(columns=["rain_anom_lag1", "rain_anom_lag2"]).to_csv(OUT / f"district_climate_malaria_{TAG}.csv", index=False)
df = df.dropna(subset=["rain_lag2", "tsuit_lag1"])
print(f"Grid {ds.sizes['lat']}x{ds.sizes['lon']} x {ds.sizes['time']} months -> {df.district_code.nunique()} districts")

# 2. which lag links rainfall and malaria?
lags = lag_correlation(df)
print("Rainfall-malaria correlation by lag:", dict(zip(lags.lag_months, lags.correlation)))

# 3. model: train to TRAIN_END, evaluate one-month-ahead warnings on the months after it
NEXT = clim.month.max() + pd.offsets.MonthBegin(1)  # the month to forecast
# months between the latest complete month of cases and the month forecast; the same gap is used in the backtest
K = (NEXT.year - df.month.max().year) * 12 + NEXT.month - df.month.max().month if REAL else 1
STEP = 6 if REAL else None  # backtest: refit every 6 months on the data available then (as in real use)
if OPTS["recent"]:
    inc = np.log((df.cases + 1) / df.population * 1000)
    lagged = pd.DataFrame({"district_code": df.district_code, "month": df.month + pd.DateOffset(months=K), "recent_cases": inc})
    df = df.merge(lagged, on=["district_code", "month"], how="left")
    print(f"Recent cases: each forecast uses the district's cases {K} months earlier (latest complete month {df.month.max():%b %Y})")
    df = df.dropna(subset=["recent_cases"])
MAIN = dict(climate=True, trend=TREND, **OPTS)
BASE = dict(climate=False, trend=TREND, regional=OPTS["regional"])  # the expected level, without climate or recent cases
train, test = df[df.month <= TRAIN_END], df[df.month > TRAIN_END].copy()
ALPHA = estimate_alpha(train, **MAIN) if REAL else 0.125
if REAL:
    print(f"Dispersion (alpha) estimated from the data: {ALPHA:.3f}")


def thresholds(history: pd.DataFrame, target: pd.DataFrame) -> np.ndarray:
    """Epidemic threshold for each row of `target`, learnt from `history`.
    With TREND: expected level (season + district + trend, no climate) x (mean + 2 SD of past observed/expected).
    Without: WHO-style mean + 2 SD of past cases per district and calendar month."""
    t = target.assign(cal_month=pd.to_datetime(target.month).dt.month)
    if TREND:
        bm, bc = fit(history, ALPHA, **BASE)
        f = relative_threshold_factors(history, predict(bm, bc, history, **BASE), pooled=POOLED)
        t = t.merge(f, on=["district_code", "cal_month"], how="left")
        return predict(bm, bc, t, **BASE).to_numpy() * t.factor.to_numpy()
    return t.merge(epidemic_threshold(history), on=["district_code", "cal_month"], how="left").threshold.to_numpy()


def probabilities(history: pd.DataFrame, target: pd.DataFrame, spec: dict) -> np.ndarray:
    m, c = fit(history, ALPHA, **spec)
    return exceed_probability(predict(m, c, target, **spec).to_numpy(), target.threshold.to_numpy(), ALPHA)


CUTS = LEVEL_CUTS
if CALIBRATE:  # chosen on the last training year, never on the test months
    vstart = pd.Timestamp(TRAIN_END) - pd.DateOffset(months=11)
    part, val = train[train.month <= vstart - pd.DateOffset(months=K)], train[train.month >= vstart].copy()
    val["threshold"] = thresholds(part, val)
    val = val.dropna(subset=["threshold"])
    CUTS = calibrate_cuts(probabilities(part, val, MAIN), val.cases > val.threshold)
    print(f"Cut-offs chosen on {vstart:%b %Y}–{pd.Timestamp(TRAIN_END):%b %Y}: "
          + ", ".join(f"{name} {cut:.0%}" for cut, name in CUTS))

# what each part adds: every version is judged against the same thresholds; the last row is the one used
if REAL:
    variants = [("seasonal only, no trend", dict(climate=False), LEVEL_CUTS),
                ("climate + trend (previous model)", dict(climate=True, trend=TREND), LEVEL_CUTS)]
    if OPTS["regional"]:
        variants.append(("+ regional seasons", dict(climate=True, trend=TREND, regional=True), LEVEL_CUTS))
    if OPTS["recent"]:
        variants.append(("+ recent cases", MAIN, LEVEL_CUTS))
    if CALIBRATE:
        variants.append(("final without climate", dict(MAIN, climate=False), CUTS))
        variants.append(("+ calibrated cut-offs (final)", MAIN, CUTS))
    elif variants[-1][1] != MAIN:
        variants.append(("final", MAIN, CUTS))
else:
    variants = [("seasonal only (no climate)", dict(climate=False), LEVEL_CUTS),
                ("climate-informed (rain lag 1-2, temperature)", MAIN, LEVEL_CUTS)]


def backtest_blocks():
    """(history, months to forecast) pairs. Simulated: one fit on the training years. Real: refit every STEP months
    on the cases complete K months before, as the system would be run month by month."""
    if not REAL:
        yield train, test
        return
    for start in pd.date_range(test.month.min(), test.month.max(), freq=f"{STEP}MS"):
        blk = test[(test.month >= start) & (test.month < start + pd.DateOffset(months=STEP))]
        yield df[df.month <= start - pd.DateOffset(months=K)], blk


parts = []
for hist, blk in backtest_blocks():
    blk = blk.copy()
    blk["threshold"] = thresholds(hist, blk)
    blk = blk.dropna(subset=["threshold"])
    fitted = {}
    for v, (name, spec, cuts) in enumerate(variants):
        key = tuple(sorted(spec.items()))
        if key not in fitted:
            m, c = fit(hist, ALPHA, **spec)
            fitted[key] = predict(m, c, blk, **spec).to_numpy()
        blk[f"p_variant{v}"] = exceed_probability(fitted[key], blk.threshold.to_numpy(), ALPHA)
        if spec == MAIN:
            blk["predicted"] = fitted[key]
    parts.append(blk)
test = pd.concat(parts)
test["epidemic"] = test.cases > test.threshold
test["p_exceed"] = test[f"p_variant{len(variants) - 1}"]
test["level"] = level(test.p_exceed.to_numpy(), CUTS)
TEST_PERIOD = f"{test.month.min():%b %Y}–{test.month.max():%b %Y}"
if TREND:
    fixed = test.assign(cal_month=test.month.dt.month).merge(
        epidemic_threshold(train).rename(columns={"threshold": "fixed"}), on=["district_code", "cal_month"])
    print(f"Upsurge months {TEST_PERIOD}: {int(test.epidemic.sum())} against the expected level at the time, "
          f"{int((fixed.cases > fixed.fixed).sum())} against fixed thresholds from {train.month.min():%Y}–{train.month.max():%Y} levels")


def skill_of(lv):
    warned = pd.Series(lv, index=test.index).isin(["WARNING", "ALERT"])
    hits, misses = int((warned & test.epidemic).sum()), int((~warned & test.epidemic).sum())
    false = int((warned & ~test.epidemic).sum())
    return dict(epidemic_months=int(test.epidemic.sum()), detected=hits, missed=misses,
                hit_rate=round(hits / max(hits + misses, 1), 2), false_alarm_ratio=round(false / max(hits + false, 1), 2),
                csi=round(hits / max(hits + misses + false, 1), 3))


rows = []
y = test.epidemic.to_numpy()
for v, (name, spec, cuts) in enumerate(variants):
    prob = test[f"p_variant{v}"].to_numpy()
    r = pd.Series(prob).rank().to_numpy()
    auc = (r[y].sum() - y.sum() * (y.sum() + 1) / 2) / max(y.sum() * (~y).sum(), 1)
    rows.append(dict(model=name, **skill_of(level(prob, cuts)), auc=round(float(auc), 3)))
skill = pd.DataFrame(rows)
print(f"Warning skill, {TEST_PERIOD} (WARNING+ vs threshold exceedance):\n" + skill.to_string(index=False))
if REAL:
    skill.to_csv(OUT / "warning_skill.csv", index=False)
else:
    skill.drop(columns=["csi", "auc"]).to_csv(OUT / "warning_skill_2023_2026.csv", index=False)
if REAL:
    test.drop(columns=[c for c in ["rain_anom_lag1", "rain_anom_lag2"] if c in test]).to_csv(OUT / "backtest.csv", index=False)
tradeoff = warning_tradeoff(test.p_exceed, test.epidemic, cuts=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]) if REAL else None
if tradeoff is not None:
    tradeoff.to_csv(OUT / "warning_tradeoff.csv", index=False)

# 4. forecast for the next month from the latest observed climate
history = df if REFIT else train
model, cols = fit(history, ALPHA, **MAIN)
if REFIT:
    print(f"Live forecast model refitted on {history.month.min():%b %Y}–{history.month.max():%b %Y}")
coefs = model.params[["rain_lag1", "rain_lag2", "tsuit_lag1"]]
print("Model effects (rate ratio): rain +50 mm lag1 =", round(float(np.exp(50 * coefs.rain_lag1)), 2),
      "| lag2 =", round(float(np.exp(50 * coefs.rain_lag2)), 2))
if TREND:
    print(f"Long-term trend: {np.exp(model.params['trend']) - 1:+.1%} cases per year")
last = clim.month.max()
nxt = NEXT
fut = clim[clim.month >= last - pd.offsets.MonthBegin(2)].copy()
fut = pd.concat([fut, pd.DataFrame({"district_code": districts.district_code, "month": nxt})])
from climalaria.malaria import add_lags
fut = add_lags(fut)
fut = fut[fut.month == nxt].merge(df.sort_values("month").groupby("district_code", as_index=False).population.last(),
                                  on="district_code")
fut = fut.merge(districts[["district_code", "region"]], on="district_code")
if OPTS["recent"]:
    latest = df.month.max()
    recent = df[df.month == latest][["district_code", "cases", "population"]]
    fut = fut.merge(recent.assign(recent_cases=np.log((recent.cases + 1) / recent.population * 1000))[["district_code", "recent_cases"]],
                    on="district_code")
fut["threshold"] = thresholds(history, fut)
fut = fut.dropna(subset=["threshold"])
fut["predicted"] = predict(model, cols, fut, **MAIN)
fut["p_exceed"] = exceed_probability(fut.predicted.to_numpy(), fut.threshold.to_numpy(), ALPHA)
fut["level"] = level(fut.p_exceed.to_numpy(), CUTS)
period = nxt.strftime("%Y-%m")
print(f"Forecast for {period}: " + ", ".join(f"{l} {int((fut.level == l).sum())}" for l in ["ALERT", "WARNING", "WATCH", "NORMAL"]))

g = districts.merge(fut[["district_code", "rain_lag1", "predicted", "threshold", "p_exceed", "level"]], on="district_code")
g.to_file(OUT / f"malaria_risk_forecast_{period}.geojson", driver="GeoJSON")

# 5. CAP alerts (WARNING and ALERT)
def area_name(name):
    return name if name.endswith((" District", " City")) else f"{name} District"


sent = dt.datetime(nxt.year, nxt.month, 1, 6, 0, tzinfo=dt.timezone.utc)
cap_files = []
for _, r in g[g.level.isin(["WARNING", "ALERT"])].sort_values("p_exceed", ascending=False).iterrows():
    urg, sev, cer, resp = CAP_MAP[r.level]
    a = cap.build_alert(
        sender="situationroom@opm.go.ug.example", event="Malaria upsurge risk", category="Health",
        urgency=urg, severity=sev, certainty=cer, response_type=resp, sent=sent, expires_days=31,
        headline=f"{r.level}: malaria upsurge likely in {area_name(r.district)} ({period})",
        description=(f"Rainfall last month {r.rain_lag1:.0f} mm. Forecast {r.predicted:,.0f} confirmed cases against an "
                     f"epidemic threshold of {r.threshold:,.0f} (probability of exceeding {r.p_exceed:.0%}). "
                     + ("EXERCISE." if REAL else "SIMULATED DATA - EXERCISE.")),
        instruction="District Health Team: check ACT and RDT stocks, intensify case management and vector control, report weekly.",
        area_desc=f"{area_name(r.district)}, {r.region}, Uganda", polygon=cap.polygon_string(r.geometry),
        geocode=("UG-DISTRICT", r.district_code))
    assert not cap.validate(a), cap.validate(a)
    cap_files.append(OUT / "cap_alerts" / f"{r.district_code}_{area_name(r.district).replace(' ', '_')}_{period}.xml")
    cap_files[-1].write_bytes(cap.to_xml(a))
print(f"CAP messages written and validated: {len(cap_files)}")

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
fig.suptitle("Uganda districts — CHIRPS rainfall, ERA5 temperature, DHIS2 HMIS 105 malaria" if REAL
             else "Uganda districts — SIMULATED climate and malaria data (demonstration)", fontsize=10, color="#555")
fig.tight_layout()
fig.savefig(OUT / "rainfall_and_malaria_warning_map.png", dpi=150)

#  b. lag correlation
fig, ax = plt.subplots(figsize=(6, 3.2))
ax.bar(lags.lag_months, lags.correlation, color=["#9DB4C8" if c < lags.correlation.max() else "#2E86C1" for c in lags.correlation])
for x, y in zip(lags.lag_months, lags.correlation):
    ax.text(x, y + 0.01, f"{y:.2f}", ha="center", fontsize=9)
ax.set_xlabel("lag (months after rainfall)")
ax.set_ylabel("correlation")
ax.set_title(f"Rainfall anomaly vs malaria case anomaly ({TAG})")
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
ax.set_title(f"{area_name(name)} — malaria forecast vs threshold, {s.month.min():%Y}–{s.month.max():%Y} ({TAG})")
ax.legend(frameon=False, fontsize=8, ncol=4, loc="upper left")
ax.spines[["top", "right"]].set_visible(False)
fig.tight_layout()
fig.savefig(OUT / "district_forecast.png", dpi=150)
print("Outputs in", OUT)

# 7. HTML report and local server
effects = {"lag1": float(np.exp(50 * coefs.rain_lag1)), "lag2": float(np.exp(50 * coefs.rain_lag2)),
           "trend": float(np.exp(model.params["trend"]) - 1) if TREND else None,
           "train": f"{train.month.min():%Y}–{train.month.max():%Y}", "test": TEST_PERIOD, "cuts": CUTS, "step": STEP}
report = write_report(OUT, period, g.drop(columns="geometry").merge(fut[["district_code", "population"]], on="district_code"),
                      skill, lags, effects, cap_files, simulated=not REAL, tradeoff=tradeoff)
print("Report:", report)


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_):
        pass


def serve(directory: Path, port: int, open_browser: bool):
    handler = functools.partial(QuietHandler, directory=str(directory))
    for p in range(port, port + 20):
        try:
            httpd = http.server.ThreadingHTTPServer(("127.0.0.1", p), handler)
            break
        except OSError:
            continue
    else:
        raise SystemExit(f"No free port between {port} and {port + 19}; try --port")
    url = f"http://localhost:{p}/"
    print(f"\nView the results in your browser: {url}\nPress Ctrl+C to stop the server.")
    if open_browser:
        webbrowser.open(url)
    with httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServer stopped.")


if not args.no_serve:
    serve(OUT, args.port, args.open)
