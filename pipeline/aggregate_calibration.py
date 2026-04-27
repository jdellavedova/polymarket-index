"""Market Calibration Curve — current cross-sectional snapshot.

Reads calibration_nonbot.csv (20 price bins, pooled across all weeks), fits a
Prelec one-parameter weighting function, and emits a snapshot payload for the
landing-page scatter chart. Historical weekly calibration efficiency is
captured in aggregate_execution.py (via the per-week Prelec alpha by type).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from common import fit_prelec, prelec_1p, utc_now, write_json
from config import DATA_OUT, require_source


def main() -> None:
    src = require_source("calibration_nonbot")
    df = pd.read_csv(src).sort_values("price_bin_center").reset_index(drop=True)

    # Handle both legacy ("n_trades") and current ("n_obs") column names
    if "n_obs" in df.columns and "n_trades" not in df.columns:
        df = df.rename(columns={"n_obs": "n_trades"})

    # Paper convention: fit price = prelec(win_rate, alpha). The Prelec
    # weighting function maps true probability (win_rate) to subjective
    # probability (price). alpha < 1 indicates classical inverse-S / FLB.
    fit = fit_prelec(
        prices=df["realized_win_rate"].values,
        realized=df["price_bin_center"].values,
        weights=df["n_trades"].values,
    )
    # The chart plots price on x-axis and win_rate on y-axis. To overlay the
    # fitted curve on that orientation, we need win_rate = prelec_inv(price).
    # For one-parameter Prelec, the inverse has closed form (reciprocal alpha
    # is a close approximation). Compute the exact inverse directly.
    grid = np.linspace(0.02, 0.98, 97)
    # w(p) = exp(-(-ln p)^alpha)  =>  p = exp(-(-ln w)^(1/alpha))
    alpha = fit["alpha"]
    w_grid = np.clip(grid, 1e-9, 1 - 1e-9)
    fit_curve = np.exp(-((-np.log(w_grid)) ** (1.0 / alpha)))

    bins = df.to_dict(orient="records")
    bins_clean = [
        {
            "price_bin_center": float(b["price_bin_center"]),
            "realized_win_rate": float(b["realized_win_rate"]),
            "n_trades": int(b["n_trades"]),
            "ci_lower": float(b["ci_lower"]),
            "ci_upper": float(b["ci_upper"]),
            "calibration_gap": float(b["calibration_gap"]),
        }
        for b in bins
    ]

    payload = {
        "index_name": "Market Calibration Curve",
        "short_name": "Calibration",
        "snapshot_note": "Pooled across all resolved trades by non-bot wallets",
        "n_bins": len(df),
        "n_trades_total": int(df["n_trades"].sum()),
        "prelec_alpha": fit["alpha"],
        "prelec_r2": fit["r2"],
        "bins": bins_clean,
        "fit_curve": [
            {"price": float(p), "fit": float(f)} for p, f in zip(grid, fit_curve)
        ],
        "generated_at": utc_now(),
        "source": str(src),
    }
    write_json(DATA_OUT / "calibration_latest.json", payload)

    # Also emit a CSV snapshot for the downloads page
    df_out = df.copy()
    df_out["prelec_alpha_fit"] = fit["alpha"]
    df_out["prelec_r2_fit"] = fit["r2"]
    df_out.to_csv(DATA_OUT / "calibration_snapshot.csv", index=False)

    print(f"Calibration: {len(df)} bins, {int(df['n_trades'].sum()):,} trades, "
          f"alpha={fit['alpha']:.4f}, R2={fit['r2']:.4f}")


if __name__ == "__main__":
    main()
