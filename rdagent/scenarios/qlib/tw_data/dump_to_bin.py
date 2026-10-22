"""
Convert raw TWSE CSVs (from collect.py) into Qlib's binary on-disk format.

Output layout (Qlib convention) at <out_dir>:
    calendars/day.txt                — every trading date, one per line, YYYY-MM-DD
    instruments/all.txt              — <code>\\t<start>\\t<end>\\n  per stock
    features/<code>/<field>.day.bin  — float32 array, prefixed by 1 float32 = start index

Each <field>.day.bin layout:
    [start_idx (float32)] [v_0, v_1, ..., v_{n-1} (float32 each)]
where start_idx is the offset (in days) into calendars/day.txt at which v_0 lands.

Field naming: Qlib expression engine references columns as $name → <name>.day.bin.
We export OHLCV as $open/$close/$high/$low/$volume/$factor and Taiwan-specific
signals (foreign_net, margin_balance, ...) under their natural names.

Usage:
    python dump_to_bin.py --input_dir tw_raw_mini --out_dir ~/.qlib/qlib_data/tw_data
"""
import argparse
import struct
import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from rdagent.scenarios.qlib.tw_data._paths import DATA_DIR
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _paths import DATA_DIR

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
    # Codes must be read as text: chunks where every code looks numeric parse as
    # int, and zfill(4) can't restore 006201 -> it becomes 6201, a real stock.
    df = pd.read_csv(path, parse_dates=["date"], dtype={"instrument": str})
    df["instrument"] = df["instrument"].astype(str).str.zfill(4)
    return df


def _merge_all(input_dir: Path) -> pd.DataFrame:
    base = _load_csv(input_dir / "ohlcv.csv")
    if base.empty:
        raise FileNotFoundError(f"ohlcv.csv missing in {input_dir}")
    df = base
    for fname in DATA_FILES[1:]:
        extra = _load_csv(input_dir / fname)
        if extra.empty:
            print(f"  Skipping {fname} (not present)")
            continue
        df = df.merge(extra, on=["date", "instrument"], how="left")
    # Dedup (TWSE STOCK_DAY occasionally returns duplicate (date, code) rows
    # on corporate-action days — keep the last to favour post-adjustment).
    before = len(df)
    df = df.drop_duplicates(subset=["date", "instrument"], keep="last")
    if len(df) < before:
        print(f"  Dropped {before - len(df)} duplicate (date, instrument) rows")
    # Derived signals (mirror generate.py)
    if {"short_balance", "margin_balance"}.issubset(df.columns):
        df["short_margin_ratio"] = df["short_balance"] / (df["margin_balance"] + 1)
    inst_cols = [c for c in ["foreign_net", "trust_net", "dealer_net"] if c in df.columns]
    if inst_cols:
        df["total_inst_net"] = df[inst_cols].sum(axis=1)
    # $vwap proxy = (open+high+low+close)/4. Required by Qlib's Alpha158 feature
    # set. TODO: replace with real VWAP = 成交金額 / 成交股數 once collect.py
    # captures TWSE STOCK_DAY's `Trading_money` field.
    if {"$open", "$high", "$low", "$close"}.issubset(df.columns):
        df["$vwap"] = (df["$open"] + df["$high"] + df["$low"] + df["$close"]) / 4.0
    return df.sort_values(["date", "instrument"]).reset_index(drop=True)


def _write_bin(path: Path, start_idx: int, values: np.ndarray) -> None:
    """Qlib .day.bin layout: float32 start_idx then float32 values."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(struct.pack("<f", float(start_idx)))
        values.astype("<f4").tofile(f)


def dump(input_dir: Path, out_dir: Path) -> None:
    df = _merge_all(input_dir)
    print(f"Merged: {len(df):,} rows, {df['instrument'].nunique()} instruments, "
          f"{df['date'].min().date()} -> {df['date'].max().date()}")

    # Calendar = sorted unique trading dates
    calendar = sorted(df["date"].unique())
    cal_index = {d: i for i, d in enumerate(calendar)}
    cal_dir = out_dir / "calendars"
    cal_dir.mkdir(parents=True, exist_ok=True)
    (cal_dir / "day.txt").write_text(
        "\n".join(pd.Timestamp(d).strftime("%Y-%m-%d") for d in calendar) + "\n"
    )
    print(f"Calendar: {len(calendar)} trading days")

    # Field rename: OHLCV gets $-prefix in Qlib; ours already do — strip and lowercase for filenames.
    rename = {"$open": "open", "$close": "close", "$high": "high", "$low": "low",
              "$volume": "volume", "$factor": "factor", "$vwap": "vwap"}
    feature_cols = [c for c in df.columns if c not in ("date", "instrument")]

    # Per-stock: write features and collect (code, start, end) for instruments/all.txt
    inst_lines = []
    for code, sub in df.groupby("instrument", sort=True):
        sub = sub.sort_values("date")
        stock_dir = out_dir / "features" / code.lower()
        stock_dir.mkdir(parents=True, exist_ok=True)
        start_idx = cal_index[sub["date"].iloc[0]]
        # Reindex onto a contiguous calendar slice so missing days become NaN
        full = pd.date_range(sub["date"].iloc[0], sub["date"].iloc[-1], freq="D")
        full = [d for d in full if d in cal_index]
        sub = sub.set_index("date").reindex(full)
        for col in feature_cols:
            field_name = rename.get(col, col)
            values = pd.to_numeric(sub[col], errors="coerce").to_numpy(dtype=np.float32)
            _write_bin(stock_dir / f"{field_name}.day.bin", start_idx, values)
        inst_lines.append((code, sub.index[0], sub.index[-1]))

    inst_dir = out_dir / "instruments"
    inst_dir.mkdir(parents=True, exist_ok=True)
    (inst_dir / "all.txt").write_text(
        "".join(f"{c}\t{s.strftime('%Y-%m-%d')}\t{e.strftime('%Y-%m-%d')}\n"
                for c, s, e in inst_lines)
    )
    print(f"Instruments: {len(inst_lines)} stocks")
    print(f"Fields per stock: {len(feature_cols)} ({[rename.get(c, c) for c in feature_cols]})")
    print(f"\nDone. Qlib data dir: {out_dir}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input_dir", default=str(DATA_DIR / "tw_raw_mini"))
    ap.add_argument("--out_dir", default="~/.qlib/qlib_data/tw_data")
    args = ap.parse_args()
    dump(Path(args.input_dir), Path(args.out_dir).expanduser())


if __name__ == "__main__":
    main()
