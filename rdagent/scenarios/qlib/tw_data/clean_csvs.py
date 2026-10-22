"""Apply data-quality cleaning rules to data/tw_finmind_full/*.csv.

Writes cleaned copies to data/tw_finmind_full_clean/ (non-destructive). Rules
are documented in data/DATA_AUDIT_REPORT.md. Files with no transformation
rule are copied through unchanged (with universe filter where applicable) so
downstream consumers can point at one directory.

Memory-aware: files over 500 MB stream in 500K-row chunks; smaller files
load whole.

Usage:
    python -m rdagent.scenarios.qlib.tw_data.clean_csvs
"""
import re
import shutil
import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from rdagent.scenarios.qlib.tw_data._paths import DATA_DIR
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _paths import DATA_DIR

SRC = DATA_DIR / "tw_finmind_full"
DST = DATA_DIR / "tw_finmind_full_clean"
CHUNK = 500_000
LARGE_THRESHOLD_MB = 500

# ---------------------------------------------------------------------------
# Universe filter — follows the official TWSE securities-coding rules
# (中華民國證券市場編碼原則, 2022-01-18, 金管證券字第1100375967號).
# ---------------------------------------------------------------------------
# Patterns to KEEP (tradable equities and equity-like trusts):
#   ^\d{4}$               — 4-digit stocks + classic ETFs (1101, 0050, 9955)
#   ^\d{4}[A-Z]$          — 特別股 preferred (2882A)
#   ^\d{4}[A-Z]\d$        — preferred variants (2887Z1)
#   ^00\d{2,4}[A-Z]?$     — ETF / ETN / 受益證券 (00878, 006208, 00625K, 00631L)
#   ^\d{2}\d{3}[TPLRBS]$  — REIT(T) / REAT(P) / ETN Leveraged(L) / Reverse(R) /
#                           Bond-index(B) / Strategy(S)  — §17, §18 of doc
#   ^02\d{4}$             — ETN base 6-digit no letter (020001-020041) — §18
#
# Everything else is dropped:
#   - All-alphabetic codes (sector indices Electronic/IronSteel/TAIEX/TPEx)
#   - Letter-prefix: A/B/G/F/H/T/ST (gov/corp/financial/foreign bonds, funds, STO)
#   - 6-digit not starting with 00 or 02 (warrants — 708785, 911868)
#   - 4-digit stock + 1-2 digit suffix (轉換/交換公司債 convertible bonds)
#   - 4-digit stock + digit + E/W (equity-linked FX bonds)

KEEP_PATTERNS = [
    re.compile(r"^\d{4}$"),
    re.compile(r"^\d{4}[A-Z]$"),
    re.compile(r"^\d{4}[A-Z]\d$"),
    re.compile(r"^00\d{2,4}[A-Z]?$"),
    re.compile(r"^\d{2}\d{3}[TPLRBS]$"),
    re.compile(r"^02\d{4}$"),
]

# Files whose "instrument" column references TW stock-universe codes.
# Universe filter applies to these. Other files (futures/option contracts,
# currency codes, US stock info, bond names, etc.) pass through.
STOCK_FILES = {
    "ohlcv.csv",
    "institutional.csv",
    "margin.csv",
    "valuation.csv",
    "foreign_holdings.csv",
    "holding_shares_per.csv",
    "month_revenue.csv",
    "dividend.csv",
    "dividend_result.csv",
    "market_value.csv",
    "market_value_weight.csv",
    "securities_lending.csv",
    "fs_income.csv",
    "fs_balance.csv",
    "fs_cashflow.csv",
    "day_trading.csv",
    "block_trade.csv",
    "loan_collateral.csv",
    "gov_bank.csv",
    "broker_secid_agg.csv",
}

# Rows extracted to benchmark.csv for backtest use.
BENCHMARK_INSTRUMENTS = ("TAIEX", "TPEx", "0050")


def is_tradable(code) -> bool:
    if not isinstance(code, str):
        return False
    return any(p.fullmatch(code) for p in KEEP_PATTERNS)


def apply_universe_filter(df: pd.DataFrame, name: str) -> pd.DataFrame:
    """Drop rows whose 'instrument' isn't a tradable TW stock-universe code.
    Applied only to STOCK_FILES; pass-through for contract/macro datasets.
    """
    if name not in STOCK_FILES or "instrument" not in df.columns:
        return df
    return df[df["instrument"].map(is_tradable)].copy()


# ---------------------------------------------------------------------------
# loan_collateral: drop only truly-dead cols (audit revised — was over-aggressive)
# ---------------------------------------------------------------------------
# Audit confirmed these columns have 0 non-zero values across the dataset.
# Other near-dead cols (1-2% non-zero) are KEPT — rare events can carry signal.
LOAN_DROP_COLS = [
    "SecuritiesFinanceSecuredLoanNextDayQuota",
    "SettlementMarginSell",
    "SettlementMarginReplacement",
]


# ---------------------------------------------------------------------------
# Per-file cleaners — each takes a DataFrame and returns a cleaned DataFrame.
# Row-local only (no cross-row aggregations) so chunked execution is safe.
# ---------------------------------------------------------------------------

def clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Drop zero-volume placeholder rows where $open is unadjusted while OHLC
    are split-adjusted (a FinMind quirk on suspended-trading days around
    corporate actions). Also drop hard-bad rows where $high < $low.
    """
    n = len(df)
    # Coerce numerics defensively
    for c in ("$open", "$high", "$low", "$close", "$volume"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    # Zero-volume + open out-of-OHLC-range — these are placeholder rows
    bad_placeholder = (df["$volume"] == 0) & (df["$high"] < df["$open"])
    # Hard-bad: high < low (column corruption)
    bad_hl = df["$high"] < df["$low"]
    drop = bad_placeholder | bad_hl
    return df[~drop].copy()


def clean_institutional(df: pd.DataFrame) -> pd.DataFrame:
    """Recompute dealer_net from self+hedge unconditionally (audit bug fix)."""
    if {"dealer_self_net", "dealer_hedge_net"}.issubset(df.columns):
        df["dealer_net"] = (
            df["dealer_self_net"].fillna(0) + df["dealer_hedge_net"].fillna(0)
        )
    primary = [c for c in ("foreign_net", "trust_net", "dealer_net") if c in df.columns]
    if primary:
        df["inst_total_net"] = df[primary].sum(axis=1)
    return df


def clean_exchange_rate(df: pd.DataFrame) -> pd.DataFrame:
    """FX rates ≤ 0 are FinMind sentinels (every negative value is exactly −1)."""
    for col in ("cash_buy", "cash_sell", "spot_buy", "spot_sell"):
        if col in df.columns:
            df.loc[df[col] <= 0, col] = np.nan
    return df


def clean_valuation(df: pd.DataFrame) -> pd.DataFrame:
    """FinMind ships pe_ratio = −1 for loss-making firms (undefined PE)."""
    if "pe_ratio" in df.columns:
        df.loc[df["pe_ratio"] < 0, "pe_ratio"] = np.nan
    return df


def clean_holding_shares_per(df: pd.DataFrame) -> pd.DataFrame:
    """Drop rows where percent or unit went negative (FinMind anomalies)."""
    if "percent" in df.columns:
        df = df[df["percent"] >= 0]
    if "unit" in df.columns:
        df = df[df["unit"] >= 0]
    return df


def clean_foreign_holdings(df: pd.DataFrame) -> pd.DataFrame:
    """Clip foreign-pct columns to [0, 100] — over-100 is a corporate-action artifact."""
    for col in ("foreign_held_pct", "foreign_remaining_pct"):
        if col in df.columns:
            df[col] = df[col].clip(lower=0, upper=100)
    return df


def clean_dividend(df: pd.DataFrame) -> pd.DataFrame:
    """div_year is 民國 calendar (99-115); convert to Western (2010-2026)."""
    if "div_year" in df.columns:
        y = pd.to_numeric(df["div_year"], errors="coerce")
        df["div_year"] = (y + 1911).astype("Int64")
    return df


def clean_loan_collateral(df: pd.DataFrame) -> pd.DataFrame:
    """Drop the truly-dead (0 non-zero) product-type columns. Sparse but
    non-zero columns are KEPT — rare events may carry signal."""
    return df.drop(columns=[c for c in LOAN_DROP_COLS if c in df.columns])


RULES = {
    "ohlcv.csv": clean_ohlcv,
    "institutional.csv": clean_institutional,
    "exchange_rate.csv": clean_exchange_rate,
    "valuation.csv": clean_valuation,
    "holding_shares_per.csv": clean_holding_shares_per,
    "foreign_holdings.csv": clean_foreign_holdings,
    "dividend.csv": clean_dividend,
    "loan_collateral.csv": clean_loan_collateral,
}


# ---------------------------------------------------------------------------
# Benchmark extraction (TAIEX, TPEx, 0050) — runs once, against raw ohlcv
# ---------------------------------------------------------------------------

def extract_benchmark(raw_ohlcv: Path, dst: Path) -> int:
    """Write benchmark series for backtest into a single CSV."""
    df = pd.read_csv(raw_ohlcv, dtype={"instrument": str}, low_memory=False)
    bench = df[df["instrument"].isin(BENCHMARK_INSTRUMENTS)].copy()
    bench = bench.sort_values(["instrument", "date"]).reset_index(drop=True)
    bench.to_csv(dst, index=False)
    return len(bench)


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------

def _apply_rule_and_filter(df: pd.DataFrame, name: str, rule) -> pd.DataFrame:
    if rule is not None:
        df = rule(df)
    return apply_universe_filter(df, name)


def process_whole(src_path: Path, dst_path: Path, rule) -> dict:
    df = pd.read_csv(src_path, dtype={"instrument": str, "stock_id": str}, low_memory=False)
    n_in = len(df)
    df = _apply_rule_and_filter(df, src_path.name, rule)
    df.to_csv(dst_path, index=False)
    return {"rows_in": n_in, "rows_out": len(df), "action": "cleaned"}


def process_chunked(src_path: Path, dst_path: Path, rule) -> dict:
    """Stream-clean in CHUNK-row slices. First chunk writes header; rest append."""
    rows_in = rows_out = 0
    header = True
    for chunk in pd.read_csv(
        src_path, chunksize=CHUNK,
        dtype={"instrument": str, "stock_id": str}, low_memory=False,
    ):
        rows_in += len(chunk)
        cleaned = _apply_rule_and_filter(chunk, src_path.name, rule)
        rows_out += len(cleaned)
        cleaned.to_csv(
            dst_path, mode="w" if header else "a", header=header, index=False
        )
        header = False
    return {"rows_in": rows_in, "rows_out": rows_out, "action": "cleaned (chunked)"}


def process_file(src_path: Path, dst_path: Path) -> dict:
    rule = RULES.get(src_path.name)
    needs_universe = src_path.name in STOCK_FILES
    # If no rule and no universe filter applies, plain copy is fastest.
    if rule is None and not needs_universe:
        shutil.copy(src_path, dst_path)
        return {"rows_in": -1, "rows_out": -1, "action": "copied (no rule)"}
    size_mb = src_path.stat().st_size / 1024 / 1024
    if size_mb > LARGE_THRESHOLD_MB:
        return process_chunked(src_path, dst_path, rule)
    return process_whole(src_path, dst_path, rule)


def main() -> None:
    if not SRC.exists():
        raise FileNotFoundError(f"Source dir missing: {SRC}")
    DST.mkdir(parents=True, exist_ok=True)

    # 1. Extract benchmark.csv FIRST (from raw OHLCV, before universe filter strips it)
    bench_path = DST / "benchmark.csv"
    n_bench = extract_benchmark(SRC / "ohlcv.csv", bench_path)
    print(f"benchmark.csv: {n_bench:,} rows ({', '.join(BENCHMARK_INSTRUMENTS)}) -> {bench_path}\n")

    # 2. Clean every CSV
    csvs = sorted(SRC.glob("*.csv"), key=lambda p: p.stat().st_size, reverse=True)
    print(f"Cleaning {len(csvs)} CSVs from {SRC} -> {DST}\n")
    print(f"{'file':<34} {'action':<22} {'rows_in':>12} {'rows_out':>12} {'delta':>12}")
    print("-" * 97)
    for src in csvs:
        stats = process_file(src, DST / src.name)
        if stats["rows_in"] >= 0:
            delta = stats["rows_out"] - stats["rows_in"]
            print(f"{src.name:<34} {stats['action']:<22} "
                  f"{stats['rows_in']:>12,} {stats['rows_out']:>12,} {delta:>+12,}")
        else:
            print(f"{src.name:<34} {stats['action']:<22} "
                  f"{'-':>12} {'-':>12} {'-':>12}")
    print(f"\nDone. Cleaned dataset at {DST}")
    print(f"Benchmark series at {bench_path}")


if __name__ == "__main__":
    main()
