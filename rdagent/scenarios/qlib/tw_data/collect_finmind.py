"""
Taiwan stock data collector using the FinMind REST API.

Alternative source to the TWSE official APIs used by ``collect.py``. FinMind
exposes 75+ Taiwan datasets behind a single auth token, including signals
the TWSE collector does not cover (monthly revenue, dividend policy,
financial statements). Output schema for the five "core" CSVs is aligned
with ``collect.py`` so ``dump_to_bin.py`` consumes either source unchanged.

Auth: token is read from ``FINDMIND_TOKEN_API_KEY`` in ``.env``
(loaded via ``python-dotenv``). Get one at https://finmindtrade.com/.

Tier assumed: Backer / Sponsor.
  - Daily-cadence datasets (price, institutional, margin, valuation,
    foreign holdings) are fetched in "all stocks per day" mode — one
    request per business day, no ``data_id``.
  - Sparse-cadence datasets (monthly revenue, dividend, FS / BS / CF)
    are fetched per-stock with a date range — one request covers years.
  - When ``--codes`` is set, the daily datasets switch to per-stock mode
    too (cheaper for a small universe).

Outputs (default ``--output_dir tw_finmind_raw``):
    ohlcv.csv             ← TaiwanStockPriceAdj   (already split-adjusted)
    institutional.csv     ← TaiwanStockInstitutionalInvestorsBuySell  (pivoted to wide)
    margin.csv            ← TaiwanStockMarginPurchaseShortSale
    valuation.csv         ← TaiwanStockPER
    foreign_holdings.csv  ← TaiwanStockShareholding
    month_revenue.csv     ← TaiwanStockMonthRevenue
    dividend.csv          ← TaiwanStockDividend  (curated columns)
    fs_income.csv         ← TaiwanStockFinancialStatements   (long-format)
    fs_balance.csv        ← TaiwanStockBalanceSheet           (long-format)
    fs_cashflow.csv       ← TaiwanStockCashFlowsStatement     (long-format)

Notes on dump_to_bin compatibility:
  * Core 5 CSVs match TWSE schema exactly → drop-in for ``dump_to_bin.py``.
  * ``ohlcv.csv`` carries an extra ``$amount`` column (TWD turnover from
    FinMind's ``Trading_money``) that ``dump_to_bin.py`` will pass through
    as another feature. Real VWAP = ``$amount / $volume`` is left as a
    follow-up — current ``dump_to_bin.py`` overwrites ``$vwap`` with the
    OHLC4 proxy, so we don't precompute it here.
  * ``month_revenue.csv`` / ``dividend.csv`` are sparse (monthly /
    annual). ``dump_to_bin.py`` will merge them by ``(date, instrument)``
    leaving NaN on most days — Qlib's ``Fillna`` processor handles it.
  * The three ``fs_*.csv`` files are kept in FinMind's native long
    format (``date, instrument, type, value, origin_name``) and are NOT
    merged by ``dump_to_bin.py`` (which assumes wide layout). Pivoting a
    curated subset of ``type`` values into bin features is a follow-up.

Usage:
    # Smoke test: 20 blue chips
    python collect_finmind.py --codes blue_chips --start 2023-01-01 --end 2024-12-31

    # Full TWSE universe (Backer/Sponsor)
    python collect_finmind.py --start 2018-01-01

    # Skip the daily 5, only pull the new fundamentals
    python collect_finmind.py --codes blue_chips --start 2018-01-01 \\
        --skip_ohlcv --skip_institutional --skip_margin \\
        --skip_valuation --skip_foreign_holdings
"""

import argparse
import os
import sys
import time
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd
import requests
from dotenv import load_dotenv
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

# Import shared helpers/universe from the TWSE collector. Try package import
# first (when invoked via ``python -m rdagent.scenarios.qlib.tw_data.collect_finmind``);
# fall back to sibling-script import when run directly as ``python collect_finmind.py``.
try:
    from rdagent.scenarios.qlib.tw_data.collect import BLUE_CHIPS, _to_num
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from collect import BLUE_CHIPS, _to_num


# ---------------------------------------------------------------------------
# FinMind HTTP helpers
# ---------------------------------------------------------------------------

FINMIND_URL = "https://api.finmindtrade.com/api/v4/data"
USAGE_URL = "https://api.web.finmindtrade.com/v2/user_info"

# FinMind rate limits: Free 600/hr, Backer/Sponsor higher (unspecified).
# 0.2s between calls = ~5 req/sec, comfortably under typical paid quotas.
# Tune via --sleep CLI flag if you hit 402.
DEFAULT_SLEEP = 0.2


def _load_token() -> str:
    load_dotenv()
    token = os.getenv("FINDMIND_TOKEN_API_KEY") or os.getenv("FINMIND_TOKEN_API_KEY")
    if not token:
        raise RuntimeError(
            "FINDMIND_TOKEN_API_KEY missing. Add it to .env "
            "(register at https://finmindtrade.com/ to get one)."
        )
    return token


def _make_progress() -> Progress:
    return Progress(
        TextColumn("[bold blue]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("[progress.percentage]{task.percentage:>5.1f}%"),
        TextColumn("elapsed"),
        TimeElapsedColumn(),
        TextColumn("eta"),
        TimeRemainingColumn(),
        TextColumn("{task.fields[postfix]}"),
    )


class FinMindClient:
    def __init__(self, token: str, sleep: float = DEFAULT_SLEEP):
        self.token = token
        self.sleep = sleep
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def get(self, dataset: str, retries: int = 3, **params) -> list[dict]:
        """Fetch /data and return the ``data`` array. ``[]`` on failure.

        402 (quota exhausted) is retried forever with exponential backoff —
        we never give up on rate-limit, otherwise an empty-list return would
        get cached as a "no data" part file and that stock/date would never
        be re-fetched.
        """
        params = {"dataset": dataset, **{k: v for k, v in params.items() if v is not None}}
        rl_attempt = 0  # rate-limit attempts (separate from network retries)
        attempt = 0
        while attempt < retries:
            try:
                resp = self.session.get(FINMIND_URL, params=params, timeout=30)
                if resp.status_code == 200:
                    payload = resp.json()
                    if payload.get("status") == 200:
                        return payload.get("data") or []
                    msg = str(payload.get("msg", ""))
                    if payload.get("status") == 402 or "upper limit" in msg.lower():
                        wait = min(60 * (2 ** rl_attempt), 1800)  # 60s, 120s, 240s, … cap 30 min
                        rl_attempt += 1
                        print(
                            f"  Rate limit ({msg}). Sleep {wait}s (attempt #{rl_attempt}) ...",
                            file=sys.stderr, flush=True,
                        )
                        time.sleep(wait)
                        continue  # don't increment `attempt` — keep trying
                    # Unknown non-200 status: log and bail.
                    print(f"  WARN {dataset} {params}: {payload}", file=sys.stderr)
                    return []
                if resp.status_code == 402:
                    wait = min(60 * (2 ** rl_attempt), 1800)
                    rl_attempt += 1
                    print(
                        f"  HTTP 402 rate limit. Sleep {wait}s (attempt #{rl_attempt}) ...",
                        file=sys.stderr, flush=True,
                    )
                    time.sleep(wait)
                    continue
            except Exception as e:
                print(f"  WARN {dataset} network attempt {attempt}: {e}", file=sys.stderr)
            attempt += 1
            time.sleep(2 ** attempt)
        return []

    def usage(self) -> tuple[int, int]:
        """Return (used, limit) request count for the current token."""
        resp = self.session.get(USAGE_URL, timeout=30)
        j = resp.json()
        return int(j.get("user_count", 0)), int(j.get("api_request_limit", 0))


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------

def get_finmind_stock_list(client: FinMindClient) -> list[str]:
    """All TWSE 4-digit ordinary stock codes per FinMind."""
    rows = client.get("TaiwanStockInfo")
    if not rows:
        return []
    df = pd.DataFrame(rows)
    df = df[df["stock_id"].astype(str).str.match(r"^\d{4}$")]
    return sorted(df["stock_id"].drop_duplicates().tolist())


# ---------------------------------------------------------------------------
# Generic download patterns
# ---------------------------------------------------------------------------

def _read_part(path: Path) -> pd.DataFrame:
    if not (path.exists() and path.stat().st_size > 0):
        return pd.DataFrame()
    try:
        # Force instrument / stock_id as string so ETF codes like "00637L" or
        # "00878" keep their leading zeros and pandas doesn't issue a
        # DtypeWarning when a part file mixes numeric and alphanumeric codes.
        return pd.read_csv(path, dtype={"stock_id": str, "instrument": str}, low_memory=False)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _download_by_date(
    client: FinMindClient,
    name: str,
    dataset: str,
    start: str,
    end: str,
    parts_dir: Path,
    extra_params: Optional[dict] = None,
) -> list[pd.DataFrame]:
    """All-stocks-per-day mode. One request per business day. Cache per day."""
    parts_dir.mkdir(parents=True, exist_ok=True)
    dates = pd.date_range(start, end, freq="B")
    pending = [d for d in dates if not (parts_dir / f"{d.strftime('%Y%m%d')}.csv").exists()]
    print(
        f"Downloading {name} for {len(dates)} business days "
        f"({len(dates) - len(pending)} cached, {len(pending)} pending) ..."
    )
    if pending:
        with _make_progress() as progress:
            task = progress.add_task(name, total=len(pending), postfix="")
            for d in pending:
                ymd = d.strftime("%Y-%m-%d")
                rows = client.get(dataset, start_date=ymd, end_date=ymd, **(extra_params or {}))
                df = pd.DataFrame(rows) if rows else pd.DataFrame()
                df.to_csv(parts_dir / f"{d.strftime('%Y%m%d')}.csv", index=False)
                time.sleep(client.sleep)
                progress.update(task, advance=1, postfix=ymd)

    return [_read_part(parts_dir / f"{d.strftime('%Y%m%d')}.csv") for d in dates]


def _download_per_stock(
    client: FinMindClient,
    name: str,
    dataset: str,
    codes: Iterable[str],
    start: str,
    end: str,
    parts_dir: Path,
) -> list[pd.DataFrame]:
    """Per-stock-per-range mode. One request covers full date range per stock."""
    parts_dir.mkdir(parents=True, exist_ok=True)
    codes = list(codes)
    pending = [c for c in codes if not (parts_dir / f"{c}.csv").exists()]
    print(
        f"Downloading {name} for {len(codes)} stocks "
        f"({len(codes) - len(pending)} cached, {len(pending)} pending) ..."
    )
    if pending:
        with _make_progress() as progress:
            task = progress.add_task(name, total=len(pending), postfix="")
            for code in pending:
                rows = client.get(dataset, data_id=code, start_date=start, end_date=end)
                df = pd.DataFrame(rows) if rows else pd.DataFrame()
                df.to_csv(parts_dir / f"{code}.csv", index=False)
                time.sleep(client.sleep)
                progress.update(task, advance=1, postfix=code)

    return [_read_part(parts_dir / f"{c}.csv") for c in codes]


def _concat_clean(frames: list[pd.DataFrame], rename: dict, keep_cols: list[str], numeric_cols: list[str]) -> pd.DataFrame:
    """Join part frames, rename to TWSE schema, coerce numerics, dedup."""
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    if "instrument" in df.columns:
        df["instrument"] = df["instrument"].astype(str).str.zfill(4)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
    keep = ["date", "instrument"] + [c for c in keep_cols if c in df.columns]
    df = df[keep].copy()
    for col in numeric_cols:
        if col in df.columns:
            df[col] = df[col].apply(_to_num)
    return df.drop_duplicates(subset=["date", "instrument"], keep="last").sort_values(
        ["date", "instrument"]
    ).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 1. OHLCV — TaiwanStockPriceAdj (already split-adjusted)
# ---------------------------------------------------------------------------

OHLCV_RENAME = {
    "stock_id": "instrument",
    "open": "$open",
    "max": "$high",
    "min": "$low",
    "close": "$close",
    "Trading_Volume": "$volume",
    "Trading_money": "$amount",
}
OHLCV_KEEP = ["$open", "$high", "$low", "$close", "$volume", "$amount", "$factor"]
OHLCV_NUM = ["$open", "$high", "$low", "$close", "$volume", "$amount"]


def download_ohlcv(client, codes, start, end, output_dir):
    parts = output_dir / "_ohlcv_parts"
    if codes is None:
        frames = _download_by_date(
            client, "OHLCV (TaiwanStockPriceAdj)", "TaiwanStockPriceAdj", start, end, parts
        )
    else:
        frames = _download_per_stock(
            client, "OHLCV (TaiwanStockPriceAdj)", "TaiwanStockPriceAdj", codes, start, end, parts
        )
    df = _concat_clean(frames, OHLCV_RENAME, OHLCV_KEEP, OHLCV_NUM)
    if df.empty:
        print("  No OHLCV data retrieved.")
        return
    # FinMind's TaiwanStockPriceAdj is already adjusted; treat as a single series.
    df["$factor"] = 1.0
    df = df[["date", "instrument"] + OHLCV_KEEP]
    df.to_csv(output_dir / "ohlcv.csv", index=False)
    print(f"  Saved ohlcv.csv  ({len(df):,} rows)")


# ---------------------------------------------------------------------------
# 2. Institutional — pivot long → wide
# ---------------------------------------------------------------------------

# FinMind ``name`` values (snake_case English from the API). Matched
# defensively because the column has historically used a few variants.
INST_NAME_MAP = {
    "Foreign_Investor": "foreign_net",
    "Foreign_Dealer_Self": "foreign_dealer_self_net",  # rare
    "Investment_Trust": "trust_net",
    "Dealer": "dealer_net",
    "Dealer_self": "dealer_self_net",
    "Dealer_Self": "dealer_self_net",
    "Dealer_Hedging": "dealer_hedge_net",
}


def download_institutional(client, codes, start, end, output_dir):
    parts = output_dir / "_institutional_parts"
    name = "法人進出 (InstitutionalInvestorsBuySell)"
    dataset = "TaiwanStockInstitutionalInvestorsBuySell"
    if codes is None:
        frames = _download_by_date(client, name, dataset, start, end, parts)
    else:
        frames = _download_per_stock(client, name, dataset, codes, start, end, parts)

    frames = [f for f in frames if not f.empty]
    if not frames:
        print("  No institutional data retrieved.")
        return
    raw = pd.concat(frames, ignore_index=True)
    raw["instrument"] = raw["stock_id"].astype(str).str.zfill(4)
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
    raw = raw.dropna(subset=["date"])
    raw["buy"] = raw["buy"].apply(_to_num)
    raw["sell"] = raw["sell"].apply(_to_num)
    raw["net"] = raw["buy"].fillna(0) - raw["sell"].fillna(0)
    raw["signal"] = raw["name"].map(INST_NAME_MAP)
    # Surface unmapped name values once so we can extend INST_NAME_MAP if FinMind
    # adds a new investor category. Drop them from the pivot to keep schema stable.
    unmapped = sorted(raw.loc[raw["signal"].isna(), "name"].dropna().unique())
    if unmapped:
        print(f"  WARNING institutional: unmapped name values dropped: {unmapped}", file=sys.stderr)
    raw = raw.dropna(subset=["signal"])

    wide = raw.pivot_table(
        index=["date", "instrument"], columns="signal", values="net", aggfunc="sum"
    ).reset_index()
    # FinMind returns dealer activity split into "Dealer_self" + "Dealer_Hedging"
    # but no combined "Dealer" row. Reconstruct dealer_net to match TWSE schema.
    if {"dealer_self_net", "dealer_hedge_net"}.issubset(wide.columns) and "dealer_net" not in wide.columns:
        wide["dealer_net"] = wide["dealer_self_net"].fillna(0) + wide["dealer_hedge_net"].fillna(0)
    # inst_total_net = sum across the three primary categories (matches TWSE schema).
    primary = [c for c in ("foreign_net", "trust_net", "dealer_net") if c in wide.columns]
    if primary:
        wide["inst_total_net"] = wide[primary].sum(axis=1)
    keep_cols = [
        "foreign_net", "trust_net", "dealer_net",
        "dealer_self_net", "dealer_hedge_net", "inst_total_net",
    ]
    keep = ["date", "instrument"] + [c for c in keep_cols if c in wide.columns]
    wide = wide[keep].sort_values(["date", "instrument"]).reset_index(drop=True)
    wide.to_csv(output_dir / "institutional.csv", index=False)
    print(f"  Saved institutional.csv  ({len(wide):,} rows, {len(keep)-2} signals)")


# ---------------------------------------------------------------------------
# 3. Margin — TaiwanStockMarginPurchaseShortSale
# ---------------------------------------------------------------------------

MARGIN_RENAME = {
    "stock_id": "instrument",
    "MarginPurchaseBuy": "margin_buy",
    "MarginPurchaseSell": "margin_sell",
    "MarginPurchaseCashRepayment": "margin_redeem",
    "MarginPurchaseYesterdayBalance": "margin_prev_balance",
    "MarginPurchaseTodayBalance": "margin_balance",
    "MarginPurchaseLimit": "margin_next_limit",
    "ShortSaleBuy": "short_buy",
    "ShortSaleSell": "short_sell",
    "ShortSaleCashRepayment": "short_redeem",
    "ShortSaleYesterdayBalance": "short_prev_balance",
    "ShortSaleTodayBalance": "short_balance",
    "ShortSaleLimit": "short_next_limit",
    "OffsetLoanAndShort": "offset",
}
MARGIN_KEEP = [
    "margin_buy", "margin_sell", "margin_redeem",
    "margin_prev_balance", "margin_balance", "margin_next_limit",
    "short_buy", "short_sell", "short_redeem",
    "short_prev_balance", "short_balance", "short_next_limit",
    "offset",
]


def download_margin(client, codes, start, end, output_dir):
    parts = output_dir / "_margin_parts"
    name = "融資融券 (MarginPurchaseShortSale)"
    dataset = "TaiwanStockMarginPurchaseShortSale"
    if codes is None:
        frames = _download_by_date(client, name, dataset, start, end, parts)
    else:
        frames = _download_per_stock(client, name, dataset, codes, start, end, parts)
    df = _concat_clean(frames, MARGIN_RENAME, MARGIN_KEEP, MARGIN_KEEP)
    if df.empty:
        print("  No margin data retrieved.")
        return
    df.to_csv(output_dir / "margin.csv", index=False)
    print(f"  Saved margin.csv  ({len(df):,} rows, {len(MARGIN_KEEP)} signals)")


# ---------------------------------------------------------------------------
# 4. Valuation — TaiwanStockPER
# ---------------------------------------------------------------------------

VALUATION_RENAME = {
    "stock_id": "instrument",
    "dividend_yield": "div_yield",
    "PER": "pe_ratio",
    "PBR": "pb_ratio",
}
VALUATION_KEEP = ["div_yield", "pe_ratio", "pb_ratio"]


def download_valuation(client, codes, start, end, output_dir):
    parts = output_dir / "_valuation_parts"
    name = "估值比率 (TaiwanStockPER)"
    dataset = "TaiwanStockPER"
    if codes is None:
        frames = _download_by_date(client, name, dataset, start, end, parts)
    else:
        frames = _download_per_stock(client, name, dataset, codes, start, end, parts)
    df = _concat_clean(frames, VALUATION_RENAME, VALUATION_KEEP, VALUATION_KEEP)
    if df.empty:
        print("  No valuation data retrieved.")
        return
    df.to_csv(output_dir / "valuation.csv", index=False)
    print(f"  Saved valuation.csv  ({len(df):,} rows, {len(VALUATION_KEEP)} signals)")


# ---------------------------------------------------------------------------
# 5. Foreign holdings — TaiwanStockShareholding
# ---------------------------------------------------------------------------

FOREIGN_RENAME = {
    "stock_id": "instrument",
    "NumberOfSharesIssued": "issued_shares",
    "ForeignInvestmentRemainingShares": "foreign_remaining",
    "ForeignInvestmentShares": "foreign_held",
    "ForeignInvestmentRemainRatio": "foreign_remaining_pct",
    "ForeignInvestmentSharesRatio": "foreign_held_pct",
    "ForeignInvestmentUpperLimitRatio": "foreign_limit_pct",
    "ChineseInvestmentUpperLimitRatio": "chinese_limit_pct",
}
FOREIGN_KEEP = [
    "issued_shares", "foreign_remaining", "foreign_held",
    "foreign_remaining_pct", "foreign_held_pct",
    "foreign_limit_pct", "chinese_limit_pct",
]


def download_foreign_holdings(client, codes, start, end, output_dir):
    parts = output_dir / "_foreign_holdings_parts"
    name = "外資持股 (TaiwanStockShareholding)"
    dataset = "TaiwanStockShareholding"
    if codes is None:
        frames = _download_by_date(client, name, dataset, start, end, parts)
    else:
        frames = _download_per_stock(client, name, dataset, codes, start, end, parts)
    df = _concat_clean(frames, FOREIGN_RENAME, FOREIGN_KEEP, FOREIGN_KEEP)
    if df.empty:
        print("  No foreign holdings data retrieved.")
        return
    df.to_csv(output_dir / "foreign_holdings.csv", index=False)
    print(f"  Saved foreign_holdings.csv  ({len(df):,} rows, {len(FOREIGN_KEEP)} signals)")


# ---------------------------------------------------------------------------
# 6. Monthly revenue — TaiwanStockMonthRevenue (sparse, monthly)
# ---------------------------------------------------------------------------

MONTH_REV_RENAME = {"stock_id": "instrument"}
MONTH_REV_KEEP = ["revenue", "revenue_year", "revenue_month"]


def download_month_revenue(client, codes, start, end, output_dir):
    parts = output_dir / "_month_revenue_parts"
    name = "月營收 (TaiwanStockMonthRevenue)"
    dataset = "TaiwanStockMonthRevenue"
    # Sparse data — per-stock-per-range is far cheaper than iterating business days,
    # so we always go per-stock. Caller passes the universe via codes.
    frames = _download_per_stock(client, name, dataset, codes, start, end, parts)
    df = _concat_clean(frames, MONTH_REV_RENAME, MONTH_REV_KEEP, MONTH_REV_KEEP)
    if df.empty:
        print("  No monthly revenue data retrieved.")
        return
    df.to_csv(output_dir / "month_revenue.csv", index=False)
    print(f"  Saved month_revenue.csv  ({len(df):,} rows, {len(MONTH_REV_KEEP)} signals)")


# ---------------------------------------------------------------------------
# 7. Dividend — TaiwanStockDividend (sparse, annual policy + ex-div events)
# ---------------------------------------------------------------------------

DIVIDEND_RENAME = {
    "stock_id": "instrument",
    "year": "div_year",
    "CashEarningsDistribution": "cash_div",
    "StockEarningsDistribution": "stock_div",
    "CashExDividendTradingDate": "cash_ex_date",
    "CashDividendPaymentDate": "cash_pay_date",
    "StockExDividendTradingDate": "stock_ex_date",
}
DIVIDEND_KEEP = [
    "div_year", "cash_div", "stock_div",
    "cash_ex_date", "cash_pay_date", "stock_ex_date",
]
DIVIDEND_NUM = ["cash_div", "stock_div"]


def download_dividend(client, codes, start, end, output_dir):
    parts = output_dir / "_dividend_parts"
    name = "股利政策 (TaiwanStockDividend)"
    dataset = "TaiwanStockDividend"
    # Sparse — always per-stock.
    frames = _download_per_stock(client, name, dataset, codes, start, end, parts)
    df = _concat_clean(frames, DIVIDEND_RENAME, DIVIDEND_KEEP, DIVIDEND_NUM)
    if df.empty:
        print("  No dividend data retrieved.")
        return
    df.to_csv(output_dir / "dividend.csv", index=False)
    print(f"  Saved dividend.csv  ({len(df):,} rows)")


# ---------------------------------------------------------------------------
# 8-10. Financial Statements (long-format, kept as-is)
# ---------------------------------------------------------------------------

def _download_long_format_fs(client, codes, start, end, output_dir, dataset, filename, label):
    parts = output_dir / f"_{Path(filename).stem}_parts"
    # Quarterly reports — always per-stock to avoid wasted by-date calls.
    frames = _download_per_stock(client, label, dataset, codes, start, end, parts)
    frames = [f for f in frames if not f.empty]
    if not frames:
        print(f"  No {label} data retrieved.")
        return
    raw = pd.concat(frames, ignore_index=True)
    raw["instrument"] = raw["stock_id"].astype(str).str.zfill(4)
    raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
    raw = raw.dropna(subset=["date"])
    raw["value"] = raw["value"].apply(_to_num)
    keep = ["date", "instrument", "type", "value"]
    if "origin_name" in raw.columns:
        keep.append("origin_name")
    raw = raw[keep].sort_values(["date", "instrument", "type"]).reset_index(drop=True)
    raw.to_csv(output_dir / filename, index=False)
    print(f"  Saved {filename}  ({len(raw):,} rows, {raw['type'].nunique()} unique types)")


def download_fs_income(client, codes, start, end, output_dir):
    _download_long_format_fs(
        client, codes, start, end, output_dir,
        dataset="TaiwanStockFinancialStatements",
        filename="fs_income.csv",
        label="綜合損益表 (FinancialStatements)",
    )


def download_fs_balance(client, codes, start, end, output_dir):
    _download_long_format_fs(
        client, codes, start, end, output_dir,
        dataset="TaiwanStockBalanceSheet",
        filename="fs_balance.csv",
        label="資產負債表 (BalanceSheet)",
    )


def download_fs_cashflow(client, codes, start, end, output_dir):
    _download_long_format_fs(
        client, codes, start, end, output_dir,
        dataset="TaiwanStockCashFlowsStatement",
        filename="fs_cashflow.csv",
        label="現金流量表 (CashFlowsStatement)",
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default=str(date.today()))
    parser.add_argument("--output_dir", default="tw_finmind_raw")
    parser.add_argument(
        "--codes",
        default="",
        help=(
            "Comma-separated stock codes. 'blue_chips' = 20 權值股. "
            "Empty = full TWSE universe (Backer/Sponsor: by-date mode)."
        ),
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=DEFAULT_SLEEP,
        help=f"Sleep between requests (default {DEFAULT_SLEEP}s). Increase if you hit 402.",
    )
    parser.add_argument("--skip_ohlcv", action="store_true")
    parser.add_argument("--skip_institutional", action="store_true")
    parser.add_argument("--skip_margin", action="store_true")
    parser.add_argument("--skip_valuation", action="store_true")
    parser.add_argument("--skip_foreign_holdings", action="store_true")
    parser.add_argument("--skip_month_revenue", action="store_true")
    parser.add_argument("--skip_dividend", action="store_true")
    parser.add_argument("--skip_fs_income", action="store_true")
    parser.add_argument("--skip_fs_balance", action="store_true")
    parser.add_argument("--skip_fs_cashflow", action="store_true")
    args = parser.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    client = FinMindClient(_load_token(), sleep=args.sleep)
    try:
        used, limit = client.usage()
        print(f"FinMind quota: {used:,} / {limit:,} used\n")
    except Exception as e:
        print(f"Could not fetch quota ({e}); continuing.\n", file=sys.stderr)

    if args.codes == "blue_chips":
        codes = BLUE_CHIPS
        print(f"Smoke-test mode: {len(codes)} 權值股")
    elif args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        print(f"Custom universe: {len(codes)} stocks")
    else:
        codes = None
        print("Full universe: by-date mode for daily datasets (all TWSE stocks per call)")

    # Sparse datasets always go per-stock-per-range, so we always need a stock list
    # for them. Resolve once via FinMind's TaiwanStockInfo when the user didn't pass --codes.
    sparse_needed = not all([
        args.skip_month_revenue, args.skip_dividend,
        args.skip_fs_income, args.skip_fs_balance, args.skip_fs_cashflow,
    ])
    if codes is None and sparse_needed:
        sparse_codes = get_finmind_stock_list(client)
        print(f"Resolved {len(sparse_codes)} TWSE codes for sparse datasets")
    else:
        sparse_codes = codes  # may be None if all sparse skipped — fine, downloaders won't run
    print()

    if not args.skip_ohlcv:
        download_ohlcv(client, codes, args.start, args.end, out)
    if not args.skip_institutional:
        download_institutional(client, codes, args.start, args.end, out)
    if not args.skip_margin:
        download_margin(client, codes, args.start, args.end, out)
    if not args.skip_valuation:
        download_valuation(client, codes, args.start, args.end, out)
    if not args.skip_foreign_holdings:
        download_foreign_holdings(client, codes, args.start, args.end, out)
    if not args.skip_month_revenue:
        download_month_revenue(client, sparse_codes, args.start, args.end, out)
    if not args.skip_dividend:
        download_dividend(client, sparse_codes, args.start, args.end, out)
    if not args.skip_fs_income:
        download_fs_income(client, sparse_codes, args.start, args.end, out)
    if not args.skip_fs_balance:
        download_fs_balance(client, sparse_codes, args.start, args.end, out)
    if not args.skip_fs_cashflow:
        download_fs_cashflow(client, sparse_codes, args.start, args.end, out)

    print(f"\nAll done. CSVs in {out.resolve()}.")
    print(
        "To dump core 5 into Qlib bin format:\n"
        f"  python dump_to_bin.py --input_dir {out} --out_dir ~/.qlib/qlib_data/tw_data_finmind"
    )


if __name__ == "__main__":
    main()
