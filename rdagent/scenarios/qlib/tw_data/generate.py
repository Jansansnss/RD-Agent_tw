"""
Merge raw CSVs from collect.py into a single Qlib-compatible HDF5 file.

Output HDF5 has MultiIndex (datetime, instrument) matching the format
expected by Qlib's StaticDataLoader and RD-Agent's factor templates.

Inputs (any subset; missing files are silently skipped):
  ohlcv.csv             - $open $close $high $low $volume $factor (base)
  institutional.csv     - foreign_net trust_net dealer_*
  margin.csv            - margin_*  short_*
  valuation.csv         - div_yield pe_ratio pb_ratio
  foreign_holdings.csv  - foreign_held_pct foreign_remaining_pct ...
  trust_holdings.csv    - trust_held trust_held_pct
  sbl_short.csv         - sbl_short_balance sbl_short_volume

Derived signals (computed if source columns exist):
  short_margin_ratio    - 券資比 (contrarian signal)
  total_inst_net        - sum of foreign_net + trust_net + dealer_net

Usage:
    python generate.py --input_dir tw_raw_data --output tw_daily.h5
    python generate.py --input_dir tw_raw_data --output tw_daily_debug.h5 --debug

TODO future work:
  - Real $factor column computed from TWSE corporate-action data
    (dividends, splits, rights issues) instead of placeholder 1.0
  - Survivorship-bias handling: include delisted stocks at the time
    they were active, not just current TWSE universe
  - Point-in-time discipline: store knowledge_date alongside each row
    so backtests can query "data available as of date X"
  - Filter by user-supplied universe (currently keeps every instrument
    seen across all CSVs; smoke-test universes are filtered upstream)
  - Outlier detection / data quality report (missing dates per stock,
    sudden field-schema drift in TWSE response, stale repeats)
  - Incremental build: append new days to existing HDF5 instead of
    rebuilding from scratch
  - Per-stock parquet shards instead of one HDF5 (faster for partial reads)
"""

import argparse
from pathlib import Path

import pandas as pd


# Order matters: ohlcv is the base, others left-joined onto it
DATA_FILES = [
    "ohlcv.csv",
    "institutional.csv",
    "margin.csv",
    "valuation.csv",
    "foreign_holdings.csv",
]


def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path, parse_dates=["date"])
    df["instrument"] = df["instrument"].astype(str).str.zfill(4)
    return df


def build(input_dir: Path, output: Path, debug: bool = False) -> None:
    base = _load_csv(input_dir / "ohlcv.csv")
    if base.empty:
        raise FileNotFoundError(f"ohlcv.csv missing or empty in {input_dir}")
    print(f"Base OHLCV: {len(base):,} rows, {base['instrument'].nunique()} instruments")

    df = base
    for fname in DATA_FILES[1:]:
        path = input_dir / fname
        extra = _load_csv(path)
        if extra.empty:
            print(f"  Skipping {fname} (not present)")
            continue
        new_cols = [c for c in extra.columns if c not in ("date", "instrument")]
        df = df.merge(extra, on=["date", "instrument"], how="left")
        print(f"  Merged {fname}: +{len(new_cols)} cols ({new_cols})")

    # Derived signals
    if {"short_balance", "margin_balance"}.issubset(df.columns):
        df["short_margin_ratio"] = df["short_balance"] / (df["margin_balance"] + 1)

    inst_cols = [c for c in ["foreign_net", "trust_net", "dealer_net"] if c in df.columns]
    if inst_cols:
        df["total_inst_net"] = df[inst_cols].sum(axis=1)

    # MultiIndex (datetime, instrument) — Qlib convention.
    # Drop duplicate (date, instrument) rows that occasionally appear in TWSE
    # STOCK_DAY (e.g. corporate-action days returning pre+post-adjustment rows).
    before = len(df)
    df = df.drop_duplicates(subset=["date", "instrument"], keep="last")
    if len(df) < before:
        print(f"  Dropped {before - len(df)} duplicate (date, instrument) rows")
    df = df.sort_values(["date", "instrument"]).reset_index(drop=True)
    df = df.set_index(["date", "instrument"])
    df.index.names = ["datetime", "instrument"]

    if debug:
        cutoff = df.index.get_level_values("datetime").max() - pd.DateOffset(years=2)
        df = df[df.index.get_level_values("datetime") >= cutoff]
        top = df.index.get_level_values("instrument").unique()[:50]
        df = df[df.index.get_level_values("instrument").isin(top)]
        print(f"Debug mode: trimmed to {len(df):,} rows, {top.size} instruments")

    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_hdf(str(output), key="data", mode="w", complevel=5)
    print(f"\nSaved: {output}")
    print(f"  rows:    {len(df):,}")
    print(f"  columns: {df.columns.tolist()}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input_dir", default="tw_raw_data")
    parser.add_argument("--output", default="tw_daily.h5")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    build(
        input_dir=Path(args.input_dir),
        output=Path(args.output),
        debug=args.debug,
    )


if __name__ == "__main__":
    main()
