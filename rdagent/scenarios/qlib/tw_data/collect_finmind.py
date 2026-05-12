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
    from rdagent.scenarios.qlib.tw_data.collect import BLUE_CHIPS, _to_num_vec
    from rdagent.scenarios.qlib.tw_data._paths import DATA_DIR
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from collect import BLUE_CHIPS, _to_num_vec
    from _paths import DATA_DIR


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

    def get(self, dataset: Optional[str], retries: int = 3, endpoint: Optional[str] = None, **params) -> list[dict]:
        """Fetch /data and return the ``data`` array. ``[]`` on failure.

        402 (quota exhausted) is retried forever with exponential backoff —
        we never give up on rate-limit, otherwise an empty-list return would
        get cached as a "no data" part file and that stock/date would never
        be re-fetched.

        ``endpoint`` overrides the URL — used for FinMind's special endpoints
        (TaiwanStockTradingDailyReport, TaiwanStockTradingDailyReportSecIdAgg)
        which don't go through ``/api/v4/data``. When ``endpoint`` is set,
        ``dataset`` is omitted from query params.
        """
        url = endpoint or FINMIND_URL
        p = {k: v for k, v in params.items() if v is not None}
        if dataset and not endpoint:
            p["dataset"] = dataset
        params = p
        rl_attempt = 0  # rate-limit attempts (separate from network retries)
        attempt = 0
        while attempt < retries:
            try:
                resp = self.session.get(url, params=params, timeout=30)
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


def _ensure_parts_by_date(
    client: FinMindClient,
    name: str,
    dataset: str,
    start: str,
    end: str,
    parts_dir: Path,
    extra_params: Optional[dict] = None,
) -> pd.DatetimeIndex:
    """Download phase only: fetch any missing per-day CSVs into ``parts_dir``.

    Returns the full DatetimeIndex of business days covered. No frames are
    materialized — pair with ``_iter_parts_by_date`` for streaming consumers
    or ``_download_by_date`` for the materialized-list workflow.
    """
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
    return dates


def _iter_parts_by_date(parts_dir: Path, dates: pd.DatetimeIndex) -> Iterable[pd.DataFrame]:
    """Yield per-day cached frames one at a time. Caller is expected to consume
    and discard each frame to keep peak memory bounded — used by downloaders
    that would otherwise OOM on ``pd.concat`` of thousands of long-format parts.
    """
    for d in dates:
        yield _read_part(parts_dir / f"{d.strftime('%Y%m%d')}.csv")


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
    dates = _ensure_parts_by_date(client, name, dataset, start, end, parts_dir, extra_params)
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
            df[col] = _to_num_vec(df[col])
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


def _prep_institutional_part(f: pd.DataFrame) -> pd.DataFrame:
    """Per-day cleanup before bucketing/pivot. Drops rows with bad dates or
    unmapped investor names, returns a slim [date, instrument, signal, net]
    frame. Returns empty if nothing usable in this part."""
    if f.empty:
        return f
    f = f.assign(
        instrument=f["stock_id"].astype(str).str.zfill(4),
        date=pd.to_datetime(f["date"], errors="coerce"),
    ).dropna(subset=["date"])
    f["signal"] = f["name"].map(INST_NAME_MAP)
    f = f.dropna(subset=["signal"])
    if f.empty:
        return f
    f["buy"] = _to_num_vec(f["buy"])
    f["sell"] = _to_num_vec(f["sell"])
    f["net"] = f["buy"].fillna(0) - f["sell"].fillna(0)
    return f[["date", "instrument", "signal", "net"]]


def _pivot_year_long(year_long_list: list[pd.DataFrame]) -> Optional[pd.DataFrame]:
    """Concat one year's slim frames, pivot to wide, return the wide frame.
    Returns None if the input is empty."""
    if not year_long_list:
        return None
    year_long = pd.concat(year_long_list, ignore_index=True)
    return year_long.pivot_table(
        index=["date", "instrument"],
        columns="signal",
        values="net",
        aggfunc="sum",
    ).reset_index()


def download_institutional(client, codes, start, end, output_dir):
    parts = output_dir / "_institutional_parts"
    name = "法人進出 (InstitutionalInvestorsBuySell)"
    dataset = "TaiwanStockInstitutionalInvestorsBuySell"

    # The full 16y by-date long-format frame is ~27 GB (260 days × 100K rows ×
    # 16 years × 66 bytes/row). Even chunked-by-year, holding all 16 years'
    # slim parts in memory before any pivoting still OOMs. So we flush each
    # year eagerly the moment iteration crosses into the next year — peak
    # memory is bounded to one year's worth (~1.7 GB) plus the small wide
    # frames already accumulated.
    wide_chunks: list[pd.DataFrame] = []
    unmapped_set: set[str] = set()

    if codes is None:
        dates = _ensure_parts_by_date(client, name, dataset, start, end, parts)
        current_year: Optional[int] = None
        current_bucket: list[pd.DataFrame] = []
        for raw_part in _iter_parts_by_date(parts, dates):
            if raw_part.empty:
                continue
            unmapped_set.update(
                raw_part.loc[~raw_part["name"].isin(INST_NAME_MAP), "name"].dropna().unique()
            )
            slim = _prep_institutional_part(raw_part)
            if slim.empty:
                continue
            year = int(slim["date"].iat[0].year)
            if current_year is not None and year != current_year:
                pivoted = _pivot_year_long(current_bucket)
                if pivoted is not None:
                    wide_chunks.append(pivoted)
                current_bucket = []
            current_year = year
            current_bucket.append(slim)
        # final year
        pivoted = _pivot_year_long(current_bucket)
        if pivoted is not None:
            wide_chunks.append(pivoted)
        del current_bucket
    else:
        # Per-stock mode: ``codes`` is typically small (blue_chips = 20).
        # Each frame is one stock's full multi-year history; bucket by year
        # then pivot per year.
        frames = _download_per_stock(client, name, dataset, codes, start, end, parts)
        per_year: dict[int, list[pd.DataFrame]] = {}
        for raw_part in frames:
            if raw_part.empty:
                continue
            unmapped_set.update(
                raw_part.loc[~raw_part["name"].isin(INST_NAME_MAP), "name"].dropna().unique()
            )
            slim = _prep_institutional_part(raw_part)
            if slim.empty:
                continue
            for year, sub in slim.groupby(slim["date"].dt.year):
                per_year.setdefault(int(year), []).append(sub)
        for year in sorted(per_year):
            pivoted = _pivot_year_long(per_year[year])
            per_year[year] = []  # release as we go
            if pivoted is not None:
                wide_chunks.append(pivoted)
        del per_year

    if not wide_chunks:
        print("  No institutional data retrieved.")
        return
    if unmapped_set:
        print(
            f"  WARNING institutional: unmapped name values dropped: {sorted(unmapped_set)}",
            file=sys.stderr,
        )

    wide = pd.concat(wide_chunks, ignore_index=True)
    del wide_chunks

    # FinMind splits dealer activity into "Dealer_self" + "Dealer_Hedging" most
    # days, but occasionally returns a combined "Dealer" row instead. The pivot
    # produces a sparse `dealer_net` from those days; gating reconstruction on
    # `"dealer_net" not in wide.columns` made us keep that sparse column for
    # the whole dataset (97% NaN — see data/DATA_AUDIT_REPORT.md). Always
    # recompute from self+hedge — the combined value equals self+hedge by
    # definition and self/hedge have ~97% coverage vs the sparse Dealer row's
    # ~3%.
    if {"dealer_self_net", "dealer_hedge_net"}.issubset(wide.columns):
        wide["dealer_net"] = wide["dealer_self_net"].fillna(0) + wide["dealer_hedge_net"].fillna(0)
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
    raw["value"] = _to_num_vec(raw["value"])
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


# ===========================================================================
# Sponsor expansion: daily per-stock aggregates
# ===========================================================================
# Generic spec-driven downloader. Daily-cadence per-stock data with one row
# per (date, instrument) — or, when ``long_format`` is True, multiple rows
# per (date, instrument) (e.g. holding-bracket breakdown).

DAILY_STOCK_AGG_SPECS: list[dict] = [
    {
        "label": "市值 (TaiwanStockMarketValue)",
        "dataset": "TaiwanStockMarketValue",
        "filename": "market_value.csv",
        "rename": {"stock_id": "instrument"},
        "keep": ["market_value"],
        "num": ["market_value"],
    },
    {
        "label": "市值權重 (TaiwanStockMarketValueWeight)",  # range starts 2024-10-30
        "dataset": "TaiwanStockMarketValueWeight",
        "filename": "market_value_weight.csv",
        "rename": {"stock_id": "instrument"},
        "keep": ["rank", "stock_name", "weight_per", "type"],
        "num": ["rank", "weight_per"],
    },
    {
        "label": "股權持股分級 (HoldingSharesPer)",
        "dataset": "TaiwanStockHoldingSharesPer",
        "filename": "holding_shares_per.csv",
        "rename": {"stock_id": "instrument"},
        "keep": ["HoldingSharesLevel", "people", "percent", "unit"],
        "num": ["people", "percent", "unit"],
        "long_format": True,  # 15 brackets per stock per date
    },
    {
        "label": "借券成交 (SecuritiesLending)",
        "dataset": "TaiwanStockSecuritiesLending",
        "filename": "securities_lending.csv",
        "rename": {"stock_id": "instrument"},
        "keep": ["transaction_type", "volume", "fee_rate", "close",
                 "original_return_date", "original_lending_period"],
        "num": ["volume", "fee_rate", "close"],
        "long_format": True,  # multiple lending events per day
    },
    {
        "label": "當沖 (DayTrading)",
        "dataset": "TaiwanStockDayTrading",
        "filename": "day_trading.csv",
        "rename": {"stock_id": "instrument"},
        "keep": ["BuyAfterSale", "Volume", "BuyAmount", "SellAmount"],
        "num": ["Volume", "BuyAmount", "SellAmount"],
    },
    {
        "label": "鉅額交易 (BlockTrade)",
        "dataset": "TaiwanStockBlockTrade",
        "filename": "block_trade.csv",
        "rename": {"stock_id": "instrument"},
        "keep": ["trade_type", "price", "volume", "trading_money"],
        "num": ["price", "volume", "trading_money"],
        "long_format": True,  # multiple block trades per stock-day possible
    },
    {
        "label": "借貸擔保品 (LoanCollateralBalance)",
        "dataset": "TaiwanStockLoanCollateralBalance",
        "filename": "loan_collateral.csv",
        "rename": {"stock_id": "instrument"},
        # 35-column wide schema (margin / SBL / unrestricted / SF / settlement
        # × prev_balance/buy/sell/redemption/replacement/current/quota). Kept
        # as-is rather than renamed; consumers can pick what they need.
        "keep": ["market", "MarginPreviousDayBalance", "MarginBuy", "MarginSell",
                 "MarginCashRedemption", "MarginCurrentDayBalance", "MarginNextDayQuota",
                 "SecuritiesFirmLoanPreviousDayBalance", "SecuritiesFirmLoanBuy",
                 "SecuritiesFirmLoanSell", "SecuritiesFirmLoanCashRedemption",
                 "SecuritiesFirmLoanReplacement", "SecuritiesFirmLoanCurrentDayBalance",
                 "SecuritiesFirmLoanNextDayQuota", "UnrestrictedLoanPreviousDayBalance",
                 "UnrestrictedLoanBuy", "UnrestrictedLoanSell",
                 "UnrestrictedLoanCashRedemption", "UnrestrictedLoanReplacement",
                 "UnrestrictedLoanCurrentDayBalance", "UnrestrictedLoanNextDayQuota",
                 "SecuritiesFinanceSecuredLoanPreviousDayBalance",
                 "SecuritiesFinanceSecuredLoanBuy", "SecuritiesFinanceSecuredLoanSell",
                 "SecuritiesFinanceSecuredLoanCashRedemption",
                 "SecuritiesFinanceSecuredLoanReplacement",
                 "SecuritiesFinanceSecuredLoanCurrentDayBalance",
                 "SecuritiesFinanceSecuredLoanNextDayQuota",
                 "SettlementMarginPreviousDayBalance", "SettlementMarginBuy",
                 "SettlementMarginSell", "SettlementMarginCashRedemption",
                 "SettlementMarginReplacement", "SettlementMarginCurrentDayBalance",
                 "SettlementMarginNextDayQuota"],
        "num": [],  # FinMind returns these as numerics already
        "long_format": True,  # `market` distinguishes TWSE vs TPEx — multiple rows
    },
    {
        # NOTE: dataset name has lowercase 's' in 'Taiwanstock' — typo in FinMind's
        # API, must match exactly.
        "label": "八大行庫 (GovernmentBankBuySell)",
        "dataset": "TaiwanstockGovernmentBankBuySell",
        "filename": "gov_bank.csv",
        "rename": {"stock_id": "instrument"},
        "keep": ["buy_amount", "sell_amount", "buy", "sell", "bank_name"],
        "num": ["buy_amount", "sell_amount", "buy", "sell"],
        "long_format": True,  # one row per bank
    },
]


def _run_daily_stock_agg(client, codes, start, end, output_dir, spec):
    """Download one daily per-stock aggregate. ``codes is None`` => by-date mode."""
    parts = output_dir / f"_{Path(spec['filename']).stem}_parts"
    if codes is None:
        frames = _download_by_date(client, spec["label"], spec["dataset"], start, end, parts)
    else:
        frames = _download_per_stock(client, spec["label"], spec["dataset"], codes, start, end, parts)

    if spec.get("long_format"):
        # Skip the (date, instrument) dedup so multi-row-per-key schemas
        # (holding brackets, lend events, block trades, gov-bank breakdown,
        # market split) keep all rows.
        frames = [f for f in frames if not f.empty]
        if not frames:
            print(f"  No {spec['label']} data retrieved.")
            return
        df = pd.concat(frames, ignore_index=True)
        df = df.rename(columns={k: v for k, v in spec["rename"].items() if k in df.columns})
        if "instrument" in df.columns:
            df["instrument"] = df["instrument"].astype(str).str.zfill(4)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
        keep = ["date"]
        if "instrument" in df.columns:
            keep.append("instrument")
        keep += [c for c in spec["keep"] if c in df.columns]
        df = df[keep].copy()
        for col in spec["num"]:
            if col in df.columns:
                df[col] = _to_num_vec(df[col])
        sort_cols = [c for c in ("date", "instrument") if c in df.columns]
        df = df.sort_values(sort_cols).reset_index(drop=True)
    else:
        df = _concat_clean(frames, spec["rename"], spec["keep"], spec["num"])

    if df.empty:
        print(f"  No {spec['label']} data retrieved.")
        return
    df.to_csv(output_dir / spec["filename"], index=False)
    print(f"  Saved {spec['filename']}  ({len(df):,} rows)")


# ===========================================================================
# Sponsor expansion: per-stock fundamentals (sparse, range-friendly)
# ===========================================================================

DIVIDEND_RESULT_RENAME = {"stock_id": "instrument"}
DIVIDEND_RESULT_KEEP = [
    "before_price", "after_price", "stock_and_cache_dividend",
    "stock_or_cache_dividend", "max_price", "min_price",
    "open_price", "reference_price",
]
DIVIDEND_RESULT_NUM = [
    "before_price", "after_price", "stock_and_cache_dividend",
    "max_price", "min_price", "open_price", "reference_price",
]


def download_dividend_result(client, codes, start, end, output_dir):
    parts = output_dir / "_dividend_result_parts"
    name = "除權息結果 (DividendResult)"
    dataset = "TaiwanStockDividendResult"
    frames = _download_per_stock(client, name, dataset, codes, start, end, parts)
    df = _concat_clean(frames, DIVIDEND_RESULT_RENAME, DIVIDEND_RESULT_KEEP, DIVIDEND_RESULT_NUM)
    if df.empty:
        print(f"  No {name} data retrieved.")
        return
    df.to_csv(output_dir / "dividend_result.csv", index=False)
    print(f"  Saved dividend_result.csv  ({len(df):,} rows)")


# Trading daily report (per-stock cumulative broker aggregation) — special
# endpoint (not /api/v4/data). One call per stock covering the full range.
def download_trading_daily_report_secid_agg(client, codes, start, end, output_dir):
    if not codes:
        print("  Skipping trading_daily_report_secid_agg: no codes available.")
        return
    parts = output_dir / "_trading_daily_report_secid_agg_parts"
    parts.mkdir(parents=True, exist_ok=True)
    label = "分點累積 (TradingDailyReportSecIdAgg)"
    endpoint = "https://api.finmindtrade.com/api/v4/taiwan_stock_trading_daily_report_secid_agg"
    pending = [c for c in codes if not (parts / f"{c}.csv").exists()]
    print(
        f"Downloading {label} for {len(codes)} stocks "
        f"({len(codes) - len(pending)} cached, {len(pending)} pending) ..."
    )
    if pending:
        with _make_progress() as progress:
            task = progress.add_task(label, total=len(pending), postfix="")
            for code in pending:
                rows = client.get(None, endpoint=endpoint, data_id=code,
                                   start_date=start, end_date=end)
                df = pd.DataFrame(rows) if rows else pd.DataFrame()
                df.to_csv(parts / f"{code}.csv", index=False)
                time.sleep(client.sleep)
                progress.update(task, advance=1, postfix=code)

    frames = [_read_part(parts / f"{c}.csv") for c in codes]
    frames = [f for f in frames if not f.empty]
    if not frames:
        print(f"  No {label} data retrieved.")
        return
    df = pd.concat(frames, ignore_index=True)
    if "stock_id" in df.columns:
        df = df.rename(columns={"stock_id": "instrument"})
    df["instrument"] = df["instrument"].astype(str).str.zfill(4)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    df.to_csv(output_dir / "trading_daily_report_secid_agg.csv", index=False)
    print(f"  Saved trading_daily_report_secid_agg.csv  ({len(df):,} rows)")


# ===========================================================================
# Sponsor expansion: market-wide aggregates (no per-stock instrument)
# ===========================================================================
# These accept a date range in a single call — no parts cache needed.

def _download_market_wide_range(client, label, dataset, start, end, output_dir, filename, **extra):
    rows = client.get(dataset, start_date=start, end_date=end, **extra)
    df = pd.DataFrame(rows) if rows else pd.DataFrame()
    if df.empty:
        print(f"  No {label} data retrieved.")
        return
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
    df.to_csv(output_dir / filename, index=False)
    print(f"  Saved {filename}  ({len(df):,} rows)")


def download_total_margin(client, start, end, output_dir):
    _download_market_wide_range(
        client, "整體市場融資融券 (TotalMarginPurchaseShortSale)",
        "TaiwanStockTotalMarginPurchaseShortSale",
        start, end, output_dir, "total_margin.csv",
    )


def download_total_institutional(client, start, end, output_dir):
    _download_market_wide_range(
        client, "整體三大法人 (TotalInstitutionalInvestors)",
        "TaiwanStockTotalInstitutionalInvestors",
        start, end, output_dir, "total_institutional.csv",
    )


def download_business_indicator(client, start, end, output_dir):
    _download_market_wide_range(
        client, "景氣對策信號 (TaiwanBusinessIndicator)",
        "TaiwanBusinessIndicator",
        start, end, output_dir, "business_indicator.csv",
    )


def download_total_exchange_margin(client, start, end, output_dir):
    _download_market_wide_range(
        client, "大盤融資維持率 (TotalExchangeMarginMaintenance)",
        "TaiwanTotalExchangeMarginMaintenance",
        start, end, output_dir, "total_exchange_margin.csv",
    )


# ===========================================================================
# Sponsor expansion: macro / international (per-data_id × range)
# ===========================================================================

EXCHANGE_RATE_CCY = ["USD", "EUR", "JPY", "GBP", "CNY", "HKD", "AUD", "CAD", "CHF",
                     "IDR", "KRW", "MYR", "NZD", "PHP", "SEK", "SGD", "THB", "VND", "ZAR"]
INTEREST_RATE_CB = ["FED", "BOE", "RBA", "PBOC", "BOC", "ECB", "RBNZ", "RBI",
                    "CBR", "BCB", "BOJ", "SNB"]
CRUDE_OIL_KINDS = ["WTI", "Brent"]
US_BONDS_MATURITIES = [
    "United States 1-Month", "United States 2-Month", "United States 3-Month",
    "United States 6-Month", "United States 1-Year", "United States 2-Year",
    "United States 3-Year", "United States 5-Year", "United States 7-Year",
    "United States 10-Year", "United States 20-Year", "United States 30-Year",
]


def _download_macro_per_data_id(client, label, dataset, data_ids, start, end, output_dir, filename):
    parts = output_dir / f"_{Path(filename).stem}_parts"
    parts.mkdir(parents=True, exist_ok=True)
    safe = lambda s: s.replace(" ", "_").replace("/", "_")
    pending = [d for d in data_ids if not (parts / f"{safe(d)}.csv").exists()]
    print(
        f"Downloading {label} for {len(data_ids)} data_ids "
        f"({len(data_ids) - len(pending)} cached, {len(pending)} pending) ..."
    )
    if pending:
        with _make_progress() as progress:
            task = progress.add_task(label, total=len(pending), postfix="")
            for did in pending:
                rows = client.get(dataset, data_id=did, start_date=start, end_date=end)
                df = pd.DataFrame(rows) if rows else pd.DataFrame()
                df.to_csv(parts / f"{safe(did)}.csv", index=False)
                time.sleep(client.sleep)
                progress.update(task, advance=1, postfix=did)

    frames = [_read_part(parts / f"{safe(d)}.csv") for d in data_ids]
    frames = [f for f in frames if not f.empty]
    if not frames:
        print(f"  No {label} data retrieved.")
        return
    df = pd.concat(frames, ignore_index=True)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
    df.to_csv(output_dir / filename, index=False)
    print(f"  Saved {filename}  ({len(df):,} rows)")


def download_exchange_rate(client, start, end, output_dir):
    _download_macro_per_data_id(client, "外幣匯率 (TaiwanExchangeRate)", "TaiwanExchangeRate",
                                 EXCHANGE_RATE_CCY, start, end, output_dir, "exchange_rate.csv")


def download_interest_rate(client, start, end, output_dir):
    _download_macro_per_data_id(client, "央行利率 (InterestRate)", "InterestRate",
                                 INTEREST_RATE_CB, start, end, output_dir, "interest_rate.csv")


def download_crude_oil(client, start, end, output_dir):
    _download_macro_per_data_id(client, "原油 (CrudeOilPrices)", "CrudeOilPrices",
                                 CRUDE_OIL_KINDS, start, end, output_dir, "crude_oil.csv")


def download_bonds_yield(client, start, end, output_dir):
    _download_macro_per_data_id(client, "美債殖利率 (GovernmentBondsYield)", "GovernmentBondsYield",
                                 US_BONDS_MATURITIES, start, end, output_dir, "bonds_yield.csv")


def download_gold_price(client, start, end, output_dir):
    _download_market_wide_range(client, "黃金 (GoldPrice)", "GoldPrice",
                                 start, end, output_dir, "gold_price.csv")


def download_fear_greed(client, start, end, output_dir):
    _download_market_wide_range(client, "恐懼貪婪 (CnnFearGreedIndex)", "CnnFearGreedIndex",
                                 start, end, output_dir, "fear_greed.csv")


# ===========================================================================
# Sponsor expansion: static / reference lookups (one-shot, no params)
# ===========================================================================

def _download_one_shot(client, label, dataset, output_dir, filename):
    rows = client.get(dataset)
    if not rows:
        print(f"  No {label} data retrieved.")
        return
    df = pd.DataFrame(rows)
    df.to_csv(output_dir / filename, index=False)
    print(f"  Saved {filename}  ({len(df):,} rows)")


def download_stock_info(client, output_dir):
    _download_one_shot(client, "上市櫃清單 (TaiwanStockInfo)",
                        "TaiwanStockInfo", output_dir, "stock_info.csv")


def download_us_stock_info(client, output_dir):
    _download_one_shot(client, "美股清單 (USStockInfo)",
                        "USStockInfo", output_dir, "us_stock_info.csv")


def download_futopt_info(client, output_dir):
    _download_one_shot(client, "期權日成交清單 (TaiwanFutOptDailyInfo)",
                        "TaiwanFutOptDailyInfo", output_dir, "futopt_info.csv")


def download_trader_info(client, output_dir):
    _download_one_shot(client, "證券商清單 (TaiwanSecuritiesTraderInfo)",
                        "TaiwanSecuritiesTraderInfo", output_dir, "trader_info.csv")


# ===========================================================================
# Sponsor expansion: derivatives daily (futures / options)
# ===========================================================================
# By-date all-contracts mode (Backer/Sponsor). One call per business day
# returns every contract's row. ``instrument`` column is the contract id
# (TX, TXO, MTX, etc.) — these CSVs are independent of the stock universe.

def _download_derivatives_by_date(client, label, dataset, start, end, output_dir, filename, id_col):
    parts = output_dir / f"_{Path(filename).stem}_parts"
    frames = _download_by_date(client, label, dataset, start, end, parts)
    frames = [f for f in frames if not f.empty]
    if not frames:
        print(f"  No {label} data retrieved.")
        return
    df = pd.concat(frames, ignore_index=True)
    if id_col in df.columns:
        df = df.rename(columns={id_col: "instrument"})
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
    df.to_csv(output_dir / filename, index=False)
    print(f"  Saved {filename}  ({len(df):,} rows)")


def download_futures_daily(client, start, end, output_dir):
    _download_derivatives_by_date(client, "期貨日成交 (TaiwanFuturesDaily)",
                                    "TaiwanFuturesDaily", start, end, output_dir,
                                    "futures_daily.csv", id_col="futures_id")


def download_option_daily(client, start, end, output_dir):
    _download_derivatives_by_date(client, "選擇權日成交 (TaiwanOptionDaily)",
                                    "TaiwanOptionDaily", start, end, output_dir,
                                    "option_daily.csv", id_col="option_id")


def download_futures_institutional(client, start, end, output_dir):
    _download_derivatives_by_date(client, "期貨三大法人 (FuturesInstitutionalInvestors)",
                                    "TaiwanFuturesInstitutionalInvestors", start, end, output_dir,
                                    "futures_institutional.csv", id_col="futures_id")


def download_option_institutional(client, start, end, output_dir):
    _download_derivatives_by_date(client, "選擇權三大法人 (OptionInstitutionalInvestors)",
                                    "TaiwanOptionInstitutionalInvestors", start, end, output_dir,
                                    "option_institutional.csv", id_col="option_id")


# ===========================================================================
# Optional / expensive: per-stock per-day single-day-per-call datasets
# ===========================================================================
# At Sponsor (6000 req/hr), 2511 stocks × 4266 trading days from 2010-01-01 ≈
# 10.7M calls per dataset ≈ 75 days wall-clock per dataset. Behind explicit
# --include flags so the default run isn't accidentally locked into a
# multi-month commitment.

def _download_per_stock_per_day(client, label, dataset, codes, start, end, output_dir, filename):
    parts_root = output_dir / f"_{Path(filename).stem}_parts"
    parts_root.mkdir(parents=True, exist_ok=True)
    days = pd.date_range(start, end, freq="B")
    pending: list[tuple[str, pd.Timestamp]] = []
    for code in codes:
        sd = parts_root / code
        sd.mkdir(parents=True, exist_ok=True)
        for d in days:
            if not (sd / f"{d.strftime('%Y%m%d')}.csv").exists():
                pending.append((code, d))
    total = len(codes) * len(days)
    print(
        f"Downloading {label} for {len(codes)} stocks × {len(days)} days "
        f"= {total:,} requests ({total - len(pending):,} cached, {len(pending):,} pending) ..."
    )
    if pending:
        with _make_progress() as progress:
            task = progress.add_task(label, total=len(pending), postfix="")
            for code, d in pending:
                ymd = d.strftime("%Y-%m-%d")
                rows = client.get(dataset, data_id=code, start_date=ymd)
                df = pd.DataFrame(rows) if rows else pd.DataFrame()
                df.to_csv(parts_root / code / f"{d.strftime('%Y%m%d')}.csv", index=False)
                time.sleep(client.sleep)
                progress.update(task, advance=1, postfix=f"{code} {ymd}")

    # Stream-concat all parts to keep memory bounded for the ~10M-row case.
    frames = []
    for code in codes:
        sd = parts_root / code
        if not sd.exists():
            continue
        for p in sd.iterdir():
            if not (p.is_file() and p.stat().st_size > 0):
                continue
            try:
                f = pd.read_csv(p, dtype={"stock_id": str, "instrument": str},
                                low_memory=False)
                if not f.empty:
                    frames.append(f)
            except pd.errors.EmptyDataError:
                pass
    if not frames:
        print(f"  No {label} data retrieved.")
        return
    df = pd.concat(frames, ignore_index=True)
    if "stock_id" in df.columns:
        df = df.rename(columns={"stock_id": "instrument"})
    if "instrument" in df.columns:
        df["instrument"] = df["instrument"].astype(str).str.zfill(4)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.dropna(subset=["date"])
    df.to_csv(output_dir / filename, index=False)
    print(f"  Saved {filename}  ({len(df):,} rows)")


def download_kbar(client, codes, start, end, output_dir):
    """1-min K-bars per stock per day. Single day per call. Default skipped."""
    if not codes:
        print("  Skipping kbar: requires resolved codes (full universe = 10M+ calls).")
        return
    _download_per_stock_per_day(client, "1分K (TaiwanStockKBar)", "TaiwanStockKBar",
                                  codes, start, end, output_dir, "kbar.csv")


def download_news(client, codes, start, end, output_dir):
    """News per stock per day. Single day per call. Default skipped."""
    if not codes:
        print("  Skipping news: requires resolved codes (full universe = 10M+ calls).")
        return
    _download_per_stock_per_day(client, "新聞 (TaiwanStockNews)", "TaiwanStockNews",
                                  codes, start, end, output_dir, "news.csv")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

DEFAULT_OUTPUT_DIR = DATA_DIR / "tw_finmind_full"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default=str(date.today()))
    parser.add_argument(
        "--output_dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR}).",
    )
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

    # Original 10 datasets
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

    # Sponsor expansion: category-level skip flags to avoid 30 individual --skip_X.
    parser.add_argument("--skip_aggregates", action="store_true",
                         help="Skip 8 daily per-stock aggregates (market_value, holding_shares_per, "
                              "securities_lending, day_trading, block_trade, loan_collateral, "
                              "gov_bank, market_value_weight).")
    parser.add_argument("--skip_dividend_result", action="store_true")
    parser.add_argument("--skip_secid_agg", action="store_true",
                         help="Skip TaiwanStockTradingDailyReportSecIdAgg (per-stock cumulative broker).")
    parser.add_argument("--skip_market_wide", action="store_true",
                         help="Skip total_margin, total_institutional, business_indicator, "
                              "total_exchange_margin.")
    parser.add_argument("--skip_macro", action="store_true",
                         help="Skip exchange_rate, interest_rate, gold, crude_oil, bonds_yield, "
                              "fear_greed.")
    parser.add_argument("--skip_static", action="store_true",
                         help="Skip stock_info, us_stock_info, futopt_info, trader_info.")
    parser.add_argument("--skip_derivatives", action="store_true",
                         help="Skip futures/options daily + institutional.")

    # Optional / very expensive: opt-in via --include_*. Default skipped.
    parser.add_argument("--include_kbar", action="store_true",
                         help="DOWNLOAD 1-min K-bars per (stock, day). Single-day-per-call: "
                              "10M+ requests for full universe ≈ 75 days @ 6000/hr.")
    parser.add_argument("--include_news", action="store_true",
                         help="DOWNLOAD news per (stock, day). Single-day-per-call. "
                              "Sparse (~5%% non-empty) but pays full quota cost. "
                              "Full universe 2010+ ≈ 95 days; 2021+ ≈ 28-32 days.")
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

    # Sparse / per-stock datasets need a code list. Resolve once via FinMind's
    # TaiwanStockInfo when the user didn't pass --codes. Used by:
    #   - existing sparse fundamentals (month_rev, dividend, fs_*)
    #   - new dividend_result, secid_agg, kbar (--include_kbar), news (--include_news)
    needs_universe = (codes is None) and not all([
        args.skip_month_revenue, args.skip_dividend,
        args.skip_fs_income, args.skip_fs_balance, args.skip_fs_cashflow,
        args.skip_dividend_result, args.skip_secid_agg,
        not args.include_kbar, not args.include_news,
    ])
    if needs_universe:
        sparse_codes = get_finmind_stock_list(client)
        print(f"Resolved {len(sparse_codes)} TWSE codes for sparse / per-stock datasets")
    else:
        sparse_codes = codes  # may be None if everything that needs codes is skipped
    print()

    # ---------- Original 10 datasets ----------
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

    # ---------- Sponsor expansion: 8 daily per-stock aggregates ----------
    if not args.skip_aggregates:
        for spec in DAILY_STOCK_AGG_SPECS:
            _run_daily_stock_agg(client, codes, args.start, args.end, out, spec)

    # ---------- Sponsor expansion: per-stock fundamentals (sparse) ----------
    if not args.skip_dividend_result:
        download_dividend_result(client, sparse_codes, args.start, args.end, out)
    if not args.skip_secid_agg:
        download_trading_daily_report_secid_agg(client, sparse_codes, args.start, args.end, out)

    # ---------- Sponsor expansion: market-wide aggregates ----------
    if not args.skip_market_wide:
        download_total_margin(client, args.start, args.end, out)
        download_total_institutional(client, args.start, args.end, out)
        download_business_indicator(client, args.start, args.end, out)
        download_total_exchange_margin(client, args.start, args.end, out)

    # ---------- Sponsor expansion: macro / international ----------
    if not args.skip_macro:
        download_exchange_rate(client, args.start, args.end, out)
        download_interest_rate(client, args.start, args.end, out)
        download_crude_oil(client, args.start, args.end, out)
        download_bonds_yield(client, args.start, args.end, out)
        download_gold_price(client, args.start, args.end, out)
        download_fear_greed(client, args.start, args.end, out)

    # ---------- Sponsor expansion: static / reference ----------
    if not args.skip_static:
        download_stock_info(client, out)
        download_us_stock_info(client, out)
        download_futopt_info(client, out)
        download_trader_info(client, out)

    # ---------- Sponsor expansion: derivatives daily ----------
    if not args.skip_derivatives:
        download_futures_daily(client, args.start, args.end, out)
        download_option_daily(client, args.start, args.end, out)
        download_futures_institutional(client, args.start, args.end, out)
        download_option_institutional(client, args.start, args.end, out)

    # ---------- Optional / expensive (opt-in) ----------
    if args.include_kbar:
        download_kbar(client, sparse_codes, args.start, args.end, out)
    if args.include_news:
        download_news(client, sparse_codes, args.start, args.end, out)

    print(f"\nAll done. CSVs in {out.resolve()}.")
    print(
        "To dump core 5 into Qlib bin format:\n"
        f"  python dump_to_bin.py --input_dir {out} --out_dir ~/.qlib/qlib_data/tw_data_finmind"
    )


if __name__ == "__main__":
    main()
