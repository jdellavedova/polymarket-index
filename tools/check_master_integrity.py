"""
check_master_integrity.py — one-time duplicate probe on the master trade CSV.

Background: before July 2026 the weekly refresh seeded its pull checkpoint
from a frozen May 9 file, so the May 22 pull re-downloaded May 9-22 and the
old append script (no overlap guard) may have appended that window twice.

    python tools/check_master_integrity.py            # per-week dup counts (1 scan + big groupby)
    python tools/check_master_integrity.py --window 86609906 87271027
                                                      # fast targeted probe of the suspect window

If duplicates are found, do NOT rewrite the 360 GB CSV. Pass the affected
block range to the parquet conversion instead:

    python tools/convert_to_parquet.py --dedup-window <from_block> <to_block>

which makes trades_parquet/ the clean canonical read source while the CSV
keeps its duplicates as a known artifact.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import duckdb

TRADES_CSV = Path("H:/Research/10. Prediction/data/blockchain/processed_trades.csv")
CSV_SRC = f"read_csv_auto('{TRADES_CSV.as_posix()}', all_varchar=TRUE, parallel=TRUE)"


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("PRAGMA memory_limit='24GB'")
    con.execute("PRAGMA threads=8")
    con.execute("PRAGMA temp_directory='H:/Research/10. Prediction/data/blockchain/_duckdb_tmp'")
    return con


def probe_window(a: int, b: int) -> None:
    """Duplicate check restricted to a block window. Fast: the hash state only
    holds the window's rows (~tens of millions), not the full 640M."""
    con = connect()
    t0 = time.time()
    total, distinct = con.execute(f"""
        SELECT COUNT(*), COUNT(DISTINCT id)
        FROM {CSV_SRC}
        WHERE CAST(blockNumber AS BIGINT) BETWEEN {a} AND {b}
    """).fetchone()
    dups = total - distinct
    print(f"Window [{a:,}, {b:,}]  ({(time.time()-t0)/60:.1f} min scan)")
    print(f"  rows:         {total:,}")
    print(f"  distinct ids: {distinct:,}")
    print(f"  duplicates:   {dups:,}" + ("  <-- WINDOW IS DUPLICATED" if dups else "  (clean)"))
    if dups:
        print(f"\nNext: python tools/convert_to_parquet.py --dedup-window {a} {b}")
    con.close()


def probe_full() -> None:
    """Per-week duplicate counts over the whole master. One scan + a large
    groupby (spills to disk); expect ~45-90 min on the H: CSV."""
    con = connect()
    t0 = time.time()
    df = con.execute(f"""
        SELECT strftime(CAST(date AS DATE), '%G-W%V') AS week,
               COUNT(*) AS rows,
               COUNT(DISTINCT id) AS distinct_ids,
               COUNT(*) - COUNT(DISTINCT id) AS dups,
               MIN(CAST(blockNumber AS BIGINT)) AS min_block,
               MAX(CAST(blockNumber AS BIGINT)) AS max_block
        FROM {CSV_SRC}
        GROUP BY 1
        HAVING COUNT(*) - COUNT(DISTINCT id) > 0
        ORDER BY 1
    """).fetchdf()
    print(f"Scan complete in {(time.time()-t0)/60:.1f} min")
    if df.empty:
        print("No duplicated ids in any week. Master is clean.")
    else:
        print("Weeks with duplicate ids:")
        print(df.to_string(index=False))
        a, b = int(df["min_block"].min()), int(df["max_block"].max())
        print(f"\nNext: python tools/convert_to_parquet.py --dedup-window {a} {b}")
    con.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window", nargs=2, type=int, metavar=("FROM_BLOCK", "TO_BLOCK"))
    args = ap.parse_args()
    if args.window:
        probe_window(*args.window)
    else:
        probe_full()


if __name__ == "__main__":
    main()
