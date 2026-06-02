"""
incremental_reaggregate.py — Fast incremental update of paper4 source CSVs.

Reads only the Parquet files for weeks not yet in the output CSVs, fits
Prelec alpha by wallet type, and appends new rows. Replaces the multi-hour
full-scan of processed_trades.csv for weekly updates.

Prerequisites:
    - trades_parquet/ directory populated by convert_to_parquet.py
    - wallet_statistics.csv, token_outcome_map.pkl, market_winner_map.pkl
      all current on H: drive

Usage:
    python tools/incremental_reaggregate.py            # append new weeks only
    python tools/incremental_reaggregate.py --dry-run  # show what would run
"""

import argparse
import gc
import pickle
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from scipy.optimize import minimize_scalar

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BLOCKCHAIN   = Path(r"H:\Research\10. Prediction\data\blockchain")
PARQUET_DIR  = BLOCKCHAIN / "trades_parquet"
WALLETS_FILE = BLOCKCHAIN / "wallet_statistics.csv"
TOKEN_MAP    = BLOCKCHAIN / "token_outcome_map.pkl"
WINNER_MAP   = BLOCKCHAIN / "market_winner_map.pkl"
OUTPUT_DIR   = BLOCKCHAIN / "paper4"

ALPHA_CSV = OUTPUT_DIR / "weekly_alpha_by_type.csv"
PWI_CSV   = OUTPUT_DIR / "weekly_pwi.csv"

# ---------------------------------------------------------------------------
# Fitting constants (must match composition_decomposition.py)
# ---------------------------------------------------------------------------
N_BINS          = 10
BIN_EDGES       = np.linspace(0, 1, N_BINS + 1)
BIN_CENTERS     = (BIN_EDGES[:-1] + BIN_EDGES[1:]) / 2
MIN_TRADES      = 200
MIN_BIN_COUNT   = 10
N_BOOT          = 200

TYPE_MAP = {"bot": 1, "sophisticated": 2, "active_retail": 3, "casual": 4, "one_shot": 5}
TYPE_LABELS = {v: k for k, v in TYPE_MAP.items()}


# ---------------------------------------------------------------------------
# Prelec fitting
# ---------------------------------------------------------------------------
def prelec_w(p: np.ndarray, alpha: float) -> np.ndarray:
    p = np.clip(p, 1e-10, 1 - 1e-10)
    return np.exp(-(-np.log(p)) ** alpha)


def fit_prelec(win_rates: np.ndarray, counts: np.ndarray):
    valid = counts >= MIN_BIN_COUNT
    if valid.sum() < 3:
        return np.nan, np.nan, 0
    x = BIN_CENTERS[valid]
    y = win_rates[valid]
    w = counts[valid]
    def sse(alpha):
        if alpha <= 0:
            return 1e9
        return np.sum(w * (y - prelec_w(x, alpha)) ** 2)
    res = minimize_scalar(sse, bounds=(0.05, 5.0), method="bounded")
    alpha = res.x
    fitted = prelec_w(x, alpha)
    ss_res = np.sum(w * (y - fitted) ** 2)
    ss_tot = np.sum(w * (y - np.average(y, weights=w)) ** 2)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    # Bootstrap SE
    se = np.nan
    boot_alphas = []
    rng = np.random.default_rng(42)
    for _ in range(N_BOOT):
        idx = rng.integers(0, len(x), size=len(x))
        xb, yb, wb = x[idx], y[idx], w[idx]
        try:
            rb = minimize_scalar(
                lambda a: np.sum(wb * (yb - prelec_w(xb, a)) ** 2) if a > 0 else 1e9,
                bounds=(0.05, 5.0), method="bounded",
            )
            boot_alphas.append(rb.x)
        except Exception:
            pass
    if len(boot_alphas) >= 10:
        se = float(np.std(boot_alphas))
    return float(alpha), float(se), int(valid.sum())


# ---------------------------------------------------------------------------
# Load reference data
# ---------------------------------------------------------------------------
def load_maps():
    print("Loading resolution maps...", flush=True)
    with open(TOKEN_MAP, "rb") as f:
        token_outcome = pickle.load(f)
    with open(WINNER_MAP, "rb") as f:
        market_winner = pickle.load(f)
    token_lookup = {str(tok): (str(mkt), str(out)) for tok, (mkt, out) in token_outcome.items()}
    winner_lookup = {str(mkt): str(win) for mkt, win in market_winner.items()}
    print(f"  tokens: {len(token_lookup):,}  winners: {len(winner_lookup):,}", flush=True)
    return token_lookup, winner_lookup


def load_wallet_types() -> dict:
    print("Loading wallet types...", flush=True)
    ws = pd.read_csv(WALLETS_FILE, usecols=["wallet", "wallet_type"])
    ws["wallet"] = ws["wallet"].str.lower()
    wmap = dict(zip(ws["wallet"], ws["wallet_type"].map(TYPE_MAP).fillna(0).astype(int)))
    print(f"  {len(wmap):,} wallets", flush=True)
    return wmap


# ---------------------------------------------------------------------------
# Process one week's Parquet file
# ---------------------------------------------------------------------------
def process_week(path: Path, token_lookup: dict, winner_lookup: dict, wtype_map: dict) -> list:
    """Returns list of dicts, one per (week, wallet_type) with sufficient trades."""
    week = path.stem  # e.g. "2026-W22"

    df = pq.read_table(
        path,
        columns=["token_id", "maker_address", "maker_side", "price", "date"],
    ).to_pandas()

    df["maker_address"] = df["maker_address"].str.lower()

    # Resolve tokens
    info = df["token_id"].map(token_lookup)
    df = df[info.notna()].copy()
    if df.empty:
        return []
    info_vals = info[info.notna()].values
    df["market_id_res"]  = [x[0] for x in info_vals]
    df["outcome_label"]  = [x[1] for x in info_vals]
    df["winner"]         = df["market_id_res"].map(winner_lookup)
    df = df[df["winner"].notna()].copy()
    if df.empty:
        return []

    # Bet perspective
    is_buy    = df["maker_side"].str.lower() == "buy"
    price     = df["price"].values.astype(np.float64)
    token_won = (df["outcome_label"].values == df["winner"].values).astype(np.int8)
    bet_price = np.where(is_buy, price, 1.0 - price)
    bet_won   = np.where(is_buy, token_won, 1 - token_won)
    wtype     = np.array([wtype_map.get(a, 0) for a in df["maker_address"].values], dtype=np.int8)

    valid = (bet_price > 0) & (bet_price < 1) & (wtype > 0)
    bet_price, bet_won, wtype = bet_price[valid], bet_won[valid], wtype[valid]
    if len(bet_price) == 0:
        return []

    price_bin = np.clip(np.digitize(bet_price, BIN_EDGES) - 1, 0, N_BINS - 1)

    rows = []
    date_str = week_to_date(week)

    for t in np.unique(wtype):
        mask  = wtype == t
        bp, bw = bet_price[mask], bet_won[mask]
        pb    = price_bin[mask]
        n_tot = mask.sum()
        if n_tot < MIN_TRADES:
            continue

        bins = np.zeros((N_BINS, 2), dtype=np.float64)
        for b in range(N_BINS):
            bm = pb == b
            bins[b, 1] = bm.sum()
            bins[b, 0] = bw[bm].sum()

        counts    = bins[:, 1]
        wins      = bins[:, 0]
        win_rates = np.where(counts > 0, wins / counts, 0.0)
        alpha, se, n_bins = fit_prelec(win_rates, counts)

        cal_err = (np.where(counts > 0, counts * np.abs(wins / np.where(counts > 0, counts, 1) - BIN_CENTERS), 0).sum()
                   / n_tot)
        ls_mask = BIN_CENTERS < 0.20
        ls_frac = counts[ls_mask].sum() / n_tot

        rows.append({
            "week":              week,
            "wallet_type":       TYPE_LABELS[int(t)],
            "alpha":             alpha,
            "alpha_se":          se,
            "r2":                np.nan,   # skipped for speed; refactor if needed
            "n_trades":          int(n_tot),
            "n_bins_used":       n_bins,
            "mean_cal_error":    float(cal_err),
            "longshot_fraction": float(ls_frac),
            "date":              date_str,
        })

    return rows


def week_to_date(week: str) -> str:
    """'2026-W22' -> Monday's date string."""
    try:
        dt = pd.Timestamp.fromisocalendar(int(week[:4]), int(week[6:]), 1)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(dry_run: bool = False) -> None:
    # Find already-processed weeks
    done_weeks: set[str] = set()
    if ALPHA_CSV.exists():
        df_existing = pd.read_csv(ALPHA_CSV, usecols=["week"])
        done_weeks = set(df_existing["week"].unique())
        print(f"Already processed: {len(done_weeks)} weeks in {ALPHA_CSV.name}")

    # Find Parquet files for new weeks
    all_parquets = sorted(PARQUET_DIR.glob("*.parquet"))
    new_parquets = [p for p in all_parquets if p.stem not in done_weeks]
    print(f"New weeks to process: {len(new_parquets)}")

    if not new_parquets:
        print("Nothing to do.")
        return

    if dry_run:
        for p in new_parquets:
            print(f"  would process: {p.name}")
        return

    token_lookup, winner_lookup = load_maps()
    wtype_map = load_wallet_types()

    all_new_rows = []
    t0 = time.time()

    for i, path in enumerate(new_parquets, 1):
        t1 = time.time()
        rows = process_week(path, token_lookup, winner_lookup, wtype_map)
        elapsed = time.time() - t1
        types_found = [r["wallet_type"] for r in rows]
        print(f"[{i}/{len(new_parquets)}] {path.stem}  "
              f"{len(rows)} types fitted  {elapsed:.1f}s  {types_found}", flush=True)
        all_new_rows.extend(rows)
        gc.collect()

    if not all_new_rows:
        print("No new rows produced (all weeks below trade threshold).")
        return

    new_df = pd.DataFrame(all_new_rows)

    # Append to weekly_alpha_by_type.csv
    if ALPHA_CSV.exists():
        existing = pd.read_csv(ALPHA_CSV)
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.sort_values(["week", "wallet_type"]).reset_index(drop=True)
    else:
        combined = new_df.sort_values(["week", "wallet_type"]).reset_index(drop=True)
    combined.to_csv(ALPHA_CSV, index=False)
    print(f"\nWrote {ALPHA_CSV.name}: {len(combined)} rows")

    # Rebuild weekly_pwi.csv (non-bot aggregate)
    _rebuild_pwi(combined)

    print(f"\nTotal time: {(time.time()-t0)/60:.1f} min")


def _rebuild_pwi(alpha_df: pd.DataFrame) -> None:
    """Rebuild weekly_pwi.csv as trade-weighted average of non-bot alpha."""
    non_bot = alpha_df[alpha_df["wallet_type"] != "bot"].copy()
    pwi = (
        non_bot.groupby("week")
        .apply(lambda g: pd.Series({
            "n_trades":          g["n_trades"].sum(),
            "mean_cal_error":    np.average(g["mean_cal_error"], weights=g["n_trades"]),
            "longshot_fraction": np.average(g["longshot_fraction"], weights=g["n_trades"]),
            "longshot_winrate":  np.nan,  # not recomputed here; use existing where available
        }))
        .reset_index()
    )
    # Merge with existing to preserve longshot_winrate for old weeks
    if PWI_CSV.exists():
        old = pd.read_csv(PWI_CSV)
        pwi = pwi.merge(old[["week", "longshot_winrate"]], on="week", how="left",
                        suffixes=("_new", ""))
        if "longshot_winrate_new" in pwi.columns:
            pwi = pwi.drop(columns=["longshot_winrate_new"])
    pwi = pwi.sort_values("week").reset_index(drop=True)
    pwi.to_csv(PWI_CSV, index=False)
    print(f"Wrote {PWI_CSV.name}: {len(pwi)} weeks")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would run without writing anything")
    args = parser.parse_args()
    main(dry_run=args.dry_run)
