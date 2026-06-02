"""
convert_to_parquet.py — One-time conversion of processed_trades.csv to weekly Parquet.

Run overnight on the workstation that has H: drive access:
    python tools/convert_to_parquet.py

Output: H:/Research/10. Prediction/data/blockchain/trades_parquet/<YYYY-Www>.parquet
  - One file per ISO week, compressed with zstd.
  - Skips weeks already converted (safe to resume if interrupted).

After conversion, run incremental_reaggregate.py weekly instead of the
full paper4 scripts. Total pipeline time drops from hours to ~15 min.
"""

import gc
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

TRADES_CSV  = Path(r"H:\Research\10. Prediction\data\blockchain\processed_trades.csv")
PARQUET_DIR = Path(r"H:\Research\10. Prediction\data\blockchain\trades_parquet")
CHUNK_SIZE  = 5_000_000

CSV_DTYPES = {
    "blockNumber":    "int64",
    "maker_address":  "str",
    "taker_address":  "str",
    "token_id":       "str",
    "contract":       "str",
    "maker_side":     "str",
    "usdc_amount":    "float64",
    "token_amount":   "float64",
    "price":          "float64",
    "fee":            "float64",
    "market_id":      "str",
}


def flush(dfs: list, path: Path) -> None:
    df = pd.concat(dfs, ignore_index=True)
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, path, compression="zstd", compression_level=3)
    mb = path.stat().st_size / 1e6
    print(f"  wrote {path.name}  {len(df)/1e6:.2f}M rows  {mb:.0f} MB", flush=True)
    del df, table
    gc.collect()


def main() -> None:
    PARQUET_DIR.mkdir(parents=True, exist_ok=True)
    existing = {p.stem for p in PARQUET_DIR.glob("*.parquet")}
    print(f"Already converted: {len(existing)} weeks — will skip these.")

    buffers: dict[str, list] = {}
    t0 = total_rows = chunk_n = 0
    t0 = time.time()

    for chunk in pd.read_csv(
        TRADES_CSV,
        chunksize=CHUNK_SIZE,
        dtype=CSV_DTYPES,
        low_memory=False,
    ):
        chunk_n += 1
        total_rows += len(chunk)

        dates = pd.to_datetime(chunk["date"], errors="coerce")
        chunk["_week"] = dates.dt.strftime("%G-W%V")

        for week, grp in chunk.groupby("_week", sort=False):
            if week in existing:
                continue
            if week not in buffers:
                buffers[week] = []
            buffers[week].append(grp.drop(columns=["_week"]))

        # Flush all but the two most-recent buffered weeks (they may still get more rows)
        weeks_sorted = sorted(buffers)
        for w in weeks_sorted[:-2]:
            flush(buffers.pop(w), PARQUET_DIR / f"{w}.parquet")

        del chunk
        gc.collect()

        elapsed = time.time() - t0
        rate = total_rows / elapsed / 1e6
        print(
            f"chunk {chunk_n:4d}  {total_rows/1e6:6.1f}M rows  "
            f"{rate:.2f}M/s  {len(buffers)} weeks buffered",
            flush=True,
        )

    # Flush remaining
    for week, dfs in sorted(buffers.items()):
        flush(dfs, PARQUET_DIR / f"{week}.parquet")

    n_files = len(list(PARQUET_DIR.glob("*.parquet")))
    total_mb = sum(p.stat().st_size for p in PARQUET_DIR.glob("*.parquet")) / 1e6
    print(f"\nDone: {total_rows/1e6:.1f}M rows → {n_files} Parquet files ({total_mb:.0f} MB total)")
    print(f"Elapsed: {(time.time()-t0)/60:.1f} min")


if __name__ == "__main__":
    main()
