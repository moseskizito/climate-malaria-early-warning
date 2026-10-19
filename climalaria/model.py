"""Lag analysis, forecasting model and epidemic-threshold early warning."""
from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats

FEATURES = ["rain_lag1", "rain_lag2", "tsuit_lag1", "sin12", "cos12"]
TREND_ORIGIN = pd.Timestamp("2020-01-01")


def lag_correlation(df: pd.DataFrame, max_lag=4) -> pd.DataFrame:
    """Pooled correlation between rainfall anomaly and case anomaly at lags 0..max_lag months."""
    d = df.sort_values(["district_code", "month"]).copy()
    g = d.groupby("district_code")
    d["case_anom"] = g["cases"].transform(lambda s: np.log1p(s) - np.log1p(s).rolling(12, min_periods=6, center=True).mean())
    out = []
    for k in range(max_lag + 1):
        r = d["case_anom"].corr(g["rain_anom"].shift(k))
        out.append(dict(lag_months=k, correlation=round(float(r), 3)))
    return pd.DataFrame(out)


def design(df: pd.DataFrame, climate: bool = True, trend: bool = False, regional: bool = False,
           recent: bool = False) -> pd.DataFrame:
    """Model inputs. climate: rainfall lags 1-2 and temperature suitability. trend: years since 2020.
    regional: a seasonal curve per region with two harmonics (one or two peaks a year). recent: `recent_cases`,
    the district's log cases per 1,000 in the latest month whose reports are complete."""
    X = df[FEATURES[:3]].copy() if climate else pd.DataFrame(index=df.index)
    month = pd.to_datetime(df["month"])
    m = month.dt.month
    seas = {"sin12": np.sin(2 * np.pi * m / 12), "cos12": np.cos(2 * np.pi * m / 12)}
    if regional:
        seas.update(sin6=np.sin(4 * np.pi * m / 12), cos6=np.cos(4 * np.pi * m / 12))
    for k, v in seas.items():
        X[k] = v
    if regional:
        R = pd.get_dummies(df["region"], prefix="r", dtype=float)
        R = R.drop(columns=sorted(R.columns)[0])
        X = pd.concat([X] + [R.mul(v, axis=0).add_suffix(f"_{k}") for k, v in seas.items()], axis=1)
    if trend:  # years since 2020: a constant % change per year on the log scale
        X["trend"] = (month - TREND_ORIGIN).dt.days / 365.25
    if recent:
        X["recent_cases"] = df["recent_cases"]
    X = pd.concat([X, pd.get_dummies(df["district_code"], prefix="d", drop_first=True, dtype=float)], axis=1)
    return sm.add_constant(X, has_constant="add")


def fit(train: pd.DataFrame, alpha=0.125, climate: bool = True, trend: bool = False, **opts):
    X = design(train, climate, trend, **opts)
    model = sm.GLM(train["cases"], X, family=sm.families.NegativeBinomial(alpha=alpha),
                   offset=np.log(train["population"])).fit()
    return model, X.columns


def predict(model, cols, df: pd.DataFrame, climate: bool = True, trend: bool = False, **opts) -> np.ndarray:
    X = design(df, climate, trend, **opts).reindex(columns=cols, fill_value=0.0)
    return model.predict(X, offset=np.log(df["population"]))


def epidemic_threshold(history: pd.DataFrame) -> pd.DataFrame:
    """WHO-style threshold: per district and calendar month, mean + 2 SD of past cases."""
    h = history.assign(cal_month=pd.to_datetime(history.month).dt.month)
    t = h.groupby(["district_code", "cal_month"])["cases"].agg(["mean", "std"]).reset_index()
    t["threshold"] = t["mean"] + 2 * t["std"]
    return t[["district_code", "cal_month", "threshold"]]


def estimate_alpha(train: pd.DataFrame, climate: bool = True, trend: bool = False, rounds: int = 3, **opts) -> float:
    """Negative-binomial dispersion from the data (method of moments on the fitted model's residuals)."""
    alpha = 0.125
    for _ in range(rounds):
        model, _ = fit(train, alpha, climate, trend, **opts)
        mu = model.fittedvalues.to_numpy()
        y = train["cases"].to_numpy()
        alpha = float(np.clip(np.mean(((y - mu) ** 2 - mu) / mu ** 2), 0.005, 2.0))
    return alpha


def relative_threshold_factors(history: pd.DataFrame, expected, pooled: bool = False) -> pd.DataFrame:
    """Threshold relative to the expected (trend + season + district) level: mean + 2 SD of observed / expected
    per district and calendar month, or with `pooled` per district over all months using the robust equivalent
    (median + 2 x 1.4826 MAD on the log scale), which is steadier with few years.
    Multiply by the expected level for a month to get its threshold."""
    h = history.assign(cal_month=pd.to_datetime(history.month).dt.month, ratio=history["cases"].to_numpy() / np.asarray(expected))
    if pooled:  # robust: median and MAD of log(observed/expected), so a few extreme or mis-entered months do not inflate it
        h["lr"] = np.log(h["ratio"].clip(lower=1e-3))
        t = h.groupby("district_code")["lr"].agg(center="median", mad=lambda x: (x - x.median()).abs().median()).reset_index()
        t["factor"] = np.exp(t["center"] + 2 * 1.4826 * t["mad"])
        return t.merge(pd.DataFrame({"cal_month": range(1, 13)}), how="cross")[["district_code", "cal_month", "factor"]]
    t = h.groupby(["district_code", "cal_month"])["ratio"].agg(["mean", "std"]).reset_index()
    t["factor"] = t["mean"] + 2 * t["std"]
    return t[["district_code", "cal_month", "factor"]]


def exceed_probability(mu: np.ndarray, threshold: np.ndarray, alpha=0.125) -> np.ndarray:
    """P(cases > threshold) under a negative binomial with mean mu and dispersion alpha."""
    n = 1 / alpha
    p = n / (n + mu)
    return stats.nbinom.sf(np.floor(threshold), n, p)


LEVEL_CUTS = [(0.6, "ALERT"), (0.35, "WARNING"), (0.15, "WATCH")]
CAP_MAP = {"WATCH": ("Future", "Minor", "Possible", "Monitor"),
           "WARNING": ("Expected", "Moderate", "Likely", "Prepare"),
           "ALERT": ("Expected", "Severe", "Likely", "Execute")}


def level(prob: np.ndarray, cuts=None) -> np.ndarray:
    out = np.full(len(prob), "NORMAL", dtype=object)
    for cut, name in reversed(cuts or LEVEL_CUTS):
        out[np.asarray(prob) >= cut] = name
    return out


def warning_tradeoff(prob, epidemic, cuts=None) -> pd.DataFrame:
    """Hit rate, false-alarm ratio and critical success index if a warning were issued at each probability cut-off."""
    prob, epidemic = np.asarray(prob), np.asarray(epidemic, dtype=bool)
    rows = []
    for c in (np.round(np.arange(0.05, 0.96, 0.01), 2) if cuts is None else cuts):
        w = prob >= c
        hits, false, miss = int((w & epidemic).sum()), int((w & ~epidemic).sum()), int((~w & epidemic).sum())
        rows.append(dict(cut=float(c), warnings=hits + false, hits=hits, hit_rate=hits / max(hits + miss, 1),
                         false_alarm_ratio=false / max(hits + false, 1), csi=hits / max(hits + false + miss, 1)))
    return pd.DataFrame(rows)


def calibrate_cuts(prob, epidemic, alert_far: float = 0.5):
    """Warning cut-off with the best critical success index; Alert at the lowest higher cut-off whose false-alarm
    ratio is at most `alert_far` (with at least one hit); Watch at half the Warning cut-off."""
    t = warning_tradeoff(prob, epidemic)
    warn = float(t.loc[t.csi.idxmax(), "cut"])
    ok = t[(t.cut > warn) & (t.hits > 0) & (t.false_alarm_ratio <= alert_far)]
    alert = float(ok.cut.min()) if len(ok) else round(warn + (1 - warn) / 2, 2)
    return [(alert, "ALERT"), (warn, "WARNING"), (round(warn / 2, 2), "WATCH")]
