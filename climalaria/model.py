"""Lag analysis, forecasting model and epidemic-threshold early warning."""
from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats

FEATURES = ["rain_lag1", "rain_lag2", "tsuit_lag1", "sin12", "cos12"]


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


def design(df: pd.DataFrame, climate: bool = True) -> pd.DataFrame:
    X = df[FEATURES[:3]].copy() if climate else pd.DataFrame(index=df.index)
    m = pd.to_datetime(df["month"]).dt.month
    X["sin12"], X["cos12"] = np.sin(2 * np.pi * m / 12), np.cos(2 * np.pi * m / 12)
    X = pd.concat([X, pd.get_dummies(df["district_code"], prefix="d", drop_first=True, dtype=float)], axis=1)
    return sm.add_constant(X, has_constant="add")


def fit(train: pd.DataFrame, alpha=0.125, climate: bool = True):
    X = design(train, climate)
    model = sm.GLM(train["cases"], X, family=sm.families.NegativeBinomial(alpha=alpha),
                   offset=np.log(train["population"])).fit()
    return model, X.columns


def predict(model, cols, df: pd.DataFrame, climate: bool = True) -> np.ndarray:
    X = design(df, climate).reindex(columns=cols, fill_value=0.0)
    return model.predict(X, offset=np.log(df["population"]))


def epidemic_threshold(history: pd.DataFrame) -> pd.DataFrame:
    """WHO-style threshold: per district and calendar month, mean + 2 SD of past cases."""
    h = history.assign(cal_month=pd.to_datetime(history.month).dt.month)
    t = h.groupby(["district_code", "cal_month"])["cases"].agg(["mean", "std"]).reset_index()
    t["threshold"] = t["mean"] + 2 * t["std"]
    return t[["district_code", "cal_month", "threshold"]]


def exceed_probability(mu: np.ndarray, threshold: np.ndarray, alpha=0.125) -> np.ndarray:
    """P(cases > threshold) under a negative binomial with mean mu and dispersion alpha."""
    n = 1 / alpha
    p = n / (n + mu)
    return stats.nbinom.sf(np.floor(threshold), n, p)


LEVEL_CUTS = [(0.6, "ALERT"), (0.35, "WARNING"), (0.15, "WATCH")]
CAP_MAP = {"WATCH": ("Future", "Minor", "Possible", "Monitor"),
           "WARNING": ("Expected", "Moderate", "Likely", "Prepare"),
           "ALERT": ("Expected", "Severe", "Likely", "Execute")}


def level(prob: np.ndarray) -> np.ndarray:
    out = np.full(len(prob), "NORMAL", dtype=object)
    for cut, name in reversed(LEVEL_CUTS):
        out[prob >= cut] = name
    return out
