"""Shared helpers: rolling stats, Prelec fit, JSON writers."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from scipy.optimize import curve_fit


def prelec_1p(p: np.ndarray, alpha: float) -> np.ndarray:
    """One-parameter Prelec weighting function. alpha=1 is the identity."""
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return np.exp(-((-np.log(p)) ** alpha))


def fit_prelec(prices: np.ndarray, realized: np.ndarray, weights: np.ndarray | None = None) -> dict:
    """Fit one-parameter Prelec; returns alpha, r2, and residual stats."""
    sigma = 1 / np.sqrt(weights) if weights is not None else None
    popt, _ = curve_fit(prelec_1p, prices, realized, p0=[0.7], sigma=sigma, absolute_sigma=False)
    pred = prelec_1p(prices, popt[0])
    ss_res = np.sum((realized - pred) ** 2)
    ss_tot = np.sum((realized - realized.mean()) ** 2)
    r2 = float(1 - ss_res / ss_tot) if ss_tot > 0 else float("nan")
    return {"alpha": float(popt[0]), "r2": r2}


def add_rolling_stats(
    df: pd.DataFrame,
    value_col: str,
    windows: Iterable[int] = (4, 13, 52),
) -> pd.DataFrame:
    """Append moving averages and a 52-week rolling z-score for `value_col`.

    MAs use min_periods=1 so the series is never NaN in the early weeks;
    the z-score requires at least 13 weeks to stabilize and is NaN before then.
    """
    out = df.copy()
    for w in windows:
        out[f"{value_col}_ma{w}w"] = out[value_col].rolling(w, min_periods=1).mean()
    rolling = out[value_col].rolling(52, min_periods=13)
    out[f"{value_col}_z52w"] = (out[value_col] - rolling.mean()) / rolling.std(ddof=1)
    return out


def summary_stats(series: pd.Series, window: int = 52) -> dict:
    """Summary of the last `window` observations for JSON payloads."""
    recent = series.dropna().tail(window)
    if len(recent) == 0:
        return {}
    return {
        "mean_52w": float(recent.mean()),
        "sd_52w": float(recent.std(ddof=1)) if len(recent) > 1 else float("nan"),
        "min_52w": float(recent.min()),
        "max_52w": float(recent.max()),
        "n_obs_52w": int(len(recent)),
    }


def write_json(path: Path, payload: dict | list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
