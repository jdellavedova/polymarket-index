"""
convert_to_parquet.py — One-time conversion of processed_trades.csv into the
partitioned Parquet store the dashboard pipeline reads.

    python tools/convert_to_parquet.py                    # plain conversion
    python tools/convert_to_parquet.py --dedup-window A B # also dedup ids in block window
    python tools/convert_to_parquet.py --verify           # row-count parquet vs CSV

Output layout (hive-partitioned by ISO week, matches what
H:/.../append_delta_to_master.py writes for weekly deltas):

    H:/Research/10. Prediction/data/blockchain/trades_parquet/
        part_week=2022-W47/base_<uuid>.parquet
        ...
        part_week=2026-W23/base_<uuid>.parquet

All columns are kept as VARCHAR (read_csv_auto all_varchar=TRUE) so every
existing DuckDB query — which CASTs explicitly — behaves identically against
CSV and Parquet. Read pattern:

    read_parquet('.../trades_parquet/**/*.parquet')

--dedup-window: the May 2026 checkpoint-seeding incidents may have appended
the same block range twice to the CSV master. Pass the overlapping block
range (e.g. 86609906 87271027) and rows in that window are deduplicated on
the event id; rows outside the window pass through untouched. The CSV master
is never modified — parquet becomes the clean canonical read source.

Runtime: one full CSV scan (~30-60 min on H:); two scans with --dedup-window.
Not resumable — if interrupted, delete trades_parquet/ and rerun.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import duckdb

TRADES_CSV = Path("H:/Research/10. Prediction/data/blockchain/processed_trades.csv")
PARQUET_DIR = Path("H:/Research/10. Prediction/data/blockchain/trades_parquet")

CSV_SRC = f"read_csv_auto('{TRADES_CSV.as_posix()}', all_varchar=TRUE, parallel=TRUE)"
PQ_SRC = f"read_parquet('{PARQUET_DIR.as_posix()}/**/*.parquet')"


def connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute("PRAGMA memory_limit='24GB'")
    con.execute("PRAGMA threads=8")
    con.execute(f"PRAGMA temp_directory='{PARQUET_DIR.parent.as_posix()}/_duckdb_tmp'")
    return con


def convert(dedup_window: tuple[int, int] | None) -> None:
    if PARQUET_DIR.exists() and any(PARQUET_DIR.rglob("*.parquet")):
        print(f"ERROR: {PARQUET_DIR} already contains parquet files.")
        print("This conversion is not resumable - delete the directory and rerun.")
        sys.exit(1)
    PARQUET_DIR.mkdir(parents=True, exist_ok=True)

    if dedup_window:
        a, b = dedup_window
        select = f"""
            SELECT *, strftime(CAST(date AS DATE), '%G-W%V') AS part_week
            FROM {CSV_SRC}
            WHERE CAST(blockNumber AS BIGINT) < {a} OR CAST(blockNumber AS BIGINT) > {b}
            UNION ALL
            SELECT * EXCLUDE (rn) FROM (
                SELECT *, strftime(CAST(date AS DATE), '%G-W%V') AS part_week,
                       ROW_NUMBER() OVER (PARTITION BY id) AS rn
                FROM {CSV_SRC}
                WHERE CAST(blockNumber AS BIGINT) BETWEEN {a} AND {b}
            ) WHERE rn = 1
        """
        print(f"Converting with id-dedup inside block window [{a:,}, {b:,}] (two CSV scans)")
    else:
        select = f"""
            SELECT *, strftime(CAST(date AS DATE), '%G-W%V') AS part_week
            FROM {CSV_SRC}
        """
        print("Converting (no dedup window; one CSV scan)")

    t0 = time.time()
    con = connect()
    con.execute(f"""
        COPY ({select})
        TO '{PARQUET_DIR.as_posix()}'
        (FORMAT PARQUET, PARTITION_BY (part_week), COMPRESSION ZSTD,
         FILENAME_PATTERN 'base_{{uuid}}')
    """)
    con.close()

    n_files = len(list(PARQUET_DIR.rglob("*.parquet")))
    total_gb = sum(p.stat().st_size for p in PARQUET_DIR.rglob("*.parquet")) / 1e9
    print(f"Done in {(time.time()-t0)/60:.1f} min: {n_files} files, {total_gb:.1f} GB")
    print("Next: python tools/convert_to_parquet.py --verify")


def verify() -> None:
    con = connect()
    t0 = time.time()
    pq_rows = con.execute(f"SELECT COUNT(*) FROM {PQ_SRC}").fetchone()[0]
    print(f"Parquet rows: {pq_rows:,}  ({time.time()-t0:.0f}s)")
    t0 = time.time()
    csv_rows = con.execute(f"SELECT COUNT(*) FROM {CSV_SRC}").fetchone()[0]
    print(f"CSV rows:     {csv_rows:,}  ({(time.time()-t0)/60:.1f} min)")
    diff = csv_rows - pq_rows
    if diff == 0:
        print("MATCH: parquet is a complete mirror of the CSV.")
    else:
        print(f"DIFFERENCE: {diff:,} rows (expected if --dedup-window removed duplicates;")
        print("otherwise investigate before switching the pipeline to parquet).")
    con.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dedup-window", nargs=2, type=int, metavar=("FROM_BLOCK", "TO_BLOCK"))
    ap.add_argument("--verify", action="store_true")
    args = ap.parse_args()
    if args.verify:
        verify()
    else:
        convert(tuple(args.dedup_window) if args.dedup_window else None)


if __name__ == "__main__":
    main()
