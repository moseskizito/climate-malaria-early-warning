"""Simulated monthly confirmed malaria cases (HMIS 105-style) driven by lagged climate.

Mechanism used for the simulation (and what the model must recover):
 - rainfall creates breeding sites -> cases rise 1-2 months later;
 - temperature suitability for P. falciparum peaks near 25 C and falls in cool highlands;
 - endemicity differs by region (higher in the north and east).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ENDEMIC = {"Northern": 1.0, "Eastern": 0.6, "Western": 0.0, "Central": 0.2}


def temp_suitability(t):
    """Unimodal thermal suitability (0-1), peak ~25 C, near zero below 17 C."""
    return np.exp(-((t - 25.0) / 4.0) ** 2) * (t > 16)


def add_lags(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["district_code", "month"]).copy()
    g = df.groupby("district_code")
    cal = pd.to_datetime(df["month"]).dt.month
    clim = df.groupby([df["district_code"], cal])["rain_mm"].transform("mean")
    df["rain_anom"] = df["rain_mm"] / clim - 1
    for k in (1, 2):
        df[f"rain_lag{k}"] = g["rain_mm"].shift(k)
        df[f"rain_anom_lag{k}"] = g["rain_anom"].shift(k)
    df["tsuit_lag1"] = g["temp_c"].shift(1).pipe(temp_suitability)
    return df


def simulate_cases(clim: pd.DataFrame, districts: pd.DataFrame, seed=8) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    df = add_lags(clim).merge(districts[["district_code", "district", "region"]], on="district_code")
    pops = {c: rng.lognormal(np.log(330000), 0.5) for c in df.district_code.unique()}
    base = {c: rng.normal(0, 0.25) for c in df.district_code.unique()}
    df["population"] = df.district_code.map(pops).round()
    eta = (np.log(df.population / 1000) + np.log(9) + df.region.map(ENDEMIC) + df.district_code.map(base)
           + 0.0045 * (df.rain_lag1.fillna(100) - 100) + 0.0025 * (df.rain_lag2.fillna(100) - 100)
           + 1.6 * (df.tsuit_lag1.fillna(0.7) - 0.7))
    mu = np.exp(eta)
    df["cases"] = rng.negative_binomial(8, 8 / (8 + mu))
    return df
