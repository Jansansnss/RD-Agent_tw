"""
Taiwan stock market data collector.

All data is pulled directly from TWSE official APIs — raw exchange prices
that respect the tick grid. No third-party adjustment.

Endpoints used:
  - STOCK_DAY  : per-stock per-month OHLCV  (raw, tick-grid)
  - T86        : daily 三大法人買賣超       (all stocks per call)
  - MI_MARGN   : daily 融資融券總表         (all stocks; nested 'tables' format)
  - BWIBBU_d   : daily 本益比 / 股價淨值比 / 殖利率 (all stocks per call)
  - MI_QFIIS   : daily 外資及陸資持股統計   (all stocks per call) — true holdings %

The $factor column is currently 1.0 (no corporate-action adjustment).
A separate module will populate it from TWSE dividend/split announcements.

TODO future endpoints (lower priority):
  - SBL short balance — TWT94U returns 404, correct path TBD; may live under
    another sub-domain (e.g. www.tpex.org.tw for TPEx, sbl.twse.com.tw for SBL)
  - BFI82U   : per-stock 三大法人買賣金額 (gross TWD amounts, not just net shares)
  - TWT38U / TWT43U : finer-grained 外資自營商 vs 主要外資 buy/sell breakdowns
    (overlap with T86 net flows; revisit if signal warrants the extra columns)
  - TWT93U   : 借券每日成交量值
  - STOCK_DAY_AVG_ALL : 月平均收盤價
  - FMSRFK_ALL : 每月成交資訊

Usage:
    python collect.py --codes blue_chips --start 2023-01-01 --end 2024-12-31
    python generate.py --input_dir tw_raw_data --output tw_daily.h5
"""

import argparse
import time
from datetime import date
from io import StringIO
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)


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


# ---------------------------------------------------------------------------
# HTTP / parsing helpers
# ---------------------------------------------------------------------------

# TWSE rate-limits roughly 3 req/sec from a single IP. 0.5s is safe.
TWSE_SLEEP = 0.5
TWSE_HEADERS = {"User-Agent": "Mozilla/5.0 (rdagent-tw-collector)"}


def _twse_get(url: str, retries: int = 3) -> dict:
    """GET a TWSE JSON endpoint with retries. Returns {} on persistent failure."""
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=TWSE_HEADERS, timeout=30)
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
        time.sleep(TWSE_SLEEP * (attempt + 1))
    return {}


def _roc_to_iso(roc_date: str) -> Optional[str]:
    """民國日期 '113/01/02' -> Western '2024-01-02'. Returns None on error."""
    try:
        parts = roc_date.replace("/", "-").split("-")
        year = int(parts[0]) + 1911
        return f"{year:04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
    except Exception:
        return None


def _to_num(s) -> Optional[float]:
    """Parse TWSE numeric strings: '1,234,567' -> 1234567.0; '--' / '' -> None."""
    if s is None or pd.isna(s):
        return None
    s = str(s).strip()
    if s in ("", "--", "X", "N/A"):
        return None
    try:
        return float(s.replace(",", "").replace("+", ""))
    except ValueError:
        return None


def _payload_to_frame(payload: dict, table_key: str = "data") -> pd.DataFrame:
    """Generic 'fields + data' payload -> DataFrame. Returns empty on missing data."""
    if payload.get("stat") != "OK":
        return pd.DataFrame()
    fields = payload.get("fields") or []
    rows = payload.get(table_key) or []
    if not fields or not rows:
        return pd.DataFrame()
    # TWSE occasionally returns a payload where len(fields) != len(row)
    # (mid-period schema drift — e.g. T86 added 3 sub-columns in 2022).
    # Pad to the wider width so the day's known signals are retained; unknown
    # extras land under '_extra_N' and are dropped by keep_cols downstream.
    width = max(len(fields), max(len(r) for r in rows))
    if len(fields) != width or any(len(r) != width for r in rows):
        print(f"  WARNING schema drift: fields={len(fields)} row={len(rows[0])} -> padded to {width}")
    if len(fields) < width:
        fields = list(fields) + [f"_extra_{i}" for i in range(width - len(fields))]
    rows = [list(r) + [None] * (width - len(r)) for r in rows]
    return pd.DataFrame(rows, columns=fields)


# ---------------------------------------------------------------------------
# TWSE stock universe
# ---------------------------------------------------------------------------

def get_twse_stock_list() -> list[str]:
    """Return all TWSE ordinary stock codes (4-digit numeric only)."""
    url = "https://isin.twse.com.tw/isin/C_public.jsp?strMode=2"
    resp = requests.get(url, headers=TWSE_HEADERS, timeout=30)
    tables = pd.read_html(StringIO(resp.text))
    df = tables[0]
    df.columns = df.iloc[0]
    df = df.iloc[1:].reset_index(drop=True)
    col = df.columns[0]
    df[["code", "name"]] = df[col].str.split("　", n=1, expand=True)
    df["code"] = df["code"].str.strip()
    mask = df["code"].str.match(r"^\d{4}$")
    return df.loc[mask, "code"].tolist()


# ---------------------------------------------------------------------------
# OHLCV — STOCK_DAY (per stock, per month)
# ---------------------------------------------------------------------------

def _fetch_stock_month(code: str, year_month: str) -> pd.DataFrame:
    """year_month: 'YYYYMM01'. Returns one month of raw OHLCV for a single stock."""
    url = (
        "https://www.twse.com.tw/exchangeReport/STOCK_DAY"
        f"?response=json&date={year_month}&stockNo={code}"
    )
    payload = _twse_get(url)
    df = _payload_to_frame(payload)
    if df.empty:
        return df

    rename = {
        "日期": "date_roc",
        "成交股數": "$volume",
        "開盤價": "$open",
        "最高價": "$high",
        "最低價": "$low",
        "收盤價": "$close",
    }
    df = df.rename(columns=rename)
    df["date"] = df["date_roc"].apply(_roc_to_iso)
    df = df.dropna(subset=["date"])
    for col in ["$open", "$high", "$low", "$close", "$volume"]:
        df[col] = df[col].apply(_to_num)
    df["instrument"] = code
    df["$factor"] = 1.0
    return df[["date", "instrument", "$open", "$close", "$high", "$low", "$volume", "$factor"]]


def download_ohlcv(codes: list[str], start: str, end: str, output_dir: Path) -> None:
    months = pd.date_range(start, end, freq="MS")
    if len(months) == 0:
        months = pd.DatetimeIndex([pd.Timestamp(start).replace(day=1)])

    parts_dir = output_dir / "_ohlcv_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)

    # Resume: skip (code, month) pairs whose part file already exists.
    pending: list[tuple[str, pd.Timestamp]] = []
    for code in codes:
        for m in months:
            if not (parts_dir / f"{code}_{m.strftime('%Y%m')}.csv").exists():
                pending.append((code, m))
    total_all = len(codes) * len(months)
    print(
        f"Downloading OHLCV for {len(codes)} stocks × {len(months)} months "
        f"= {total_all} requests ({total_all - len(pending)} cached, {len(pending)} pending) ..."
    )

    if pending:
        with _make_progress() as progress:
            task = progress.add_task("OHLCV", total=len(pending), postfix="")
            for code, m in pending:
                ym = m.strftime("%Y%m01")
                df = _fetch_stock_month(code, ym)
                # Always write a part file (empty marker prevents re-fetch).
                df.to_csv(parts_dir / f"{code}_{m.strftime('%Y%m')}.csv", index=False)
                time.sleep(TWSE_SLEEP)
                progress.update(task, advance=1, postfix=f"{code} {ym}")

    frames = []
    for code in codes:
        for m in months:
            p = parts_dir / f"{code}_{m.strftime('%Y%m')}.csv"
            if p.exists() and p.stat().st_size > 0:
                df = pd.read_csv(p)
                if not df.empty:
                    frames.append(df)

    if not frames:
        print("  No OHLCV data retrieved.")
        return

    ohlcv = pd.concat(frames, ignore_index=True)
    ohlcv["date"] = pd.to_datetime(ohlcv["date"])
    ohlcv = ohlcv[(ohlcv["date"] >= pd.Timestamp(start)) & (ohlcv["date"] <= pd.Timestamp(end))]
    ohlcv.to_csv(output_dir / "ohlcv.csv", index=False)
    print(f"  Saved ohlcv.csv  ({len(ohlcv):,} rows)")


# ---------------------------------------------------------------------------
# Generic per-day all-stocks endpoints
# ---------------------------------------------------------------------------

def _download_daily(
    name: str,
    url_template: str,
    column_map: dict,
    keep_cols: list[str],
    start: str,
    end: str,
    output_dir: Path,
    filename: str,
) -> None:
    """Fetch a per-day all-stocks endpoint over a date range and save to CSV."""
    dates = pd.date_range(start, end, freq="B")
    parts_dir = output_dir / f"_{Path(filename).stem}_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)

    pending = [d for d in dates if not (parts_dir / f"{d.strftime('%Y%m%d')}.csv").exists()]
    print(
        f"Downloading {name} for {len(dates)} business days "
        f"({len(dates) - len(pending)} cached, {len(pending)} pending) ..."
    )
    if pending:
        with _make_progress() as progress:
            task = progress.add_task(name, total=len(pending), postfix="")
            for d in pending:
                ymd = d.strftime("%Y%m%d")
                payload = _twse_get(url_template.format(date=ymd))
                df = _payload_to_frame(payload)
                if not df.empty:
                    df["date"] = pd.to_datetime(ymd, format="%Y%m%d")
                df.to_csv(parts_dir / f"{ymd}.csv", index=False)
                time.sleep(TWSE_SLEEP)
                progress.update(task, advance=1, postfix=ymd)

    frames = []
    for d in dates:
        p = parts_dir / f"{d.strftime('%Y%m%d')}.csv"
        if p.exists() and p.stat().st_size > 0:
            df = pd.read_csv(p)
            if not df.empty:
                frames.append(df)

    if not frames:
        print(f"  No {name} data retrieved.")
        return

    raw = pd.concat(frames, ignore_index=True)
    raw = raw.rename(columns={k: v for k, v in column_map.items() if k in raw.columns})

    # Identify the instrument column (each endpoint uses slightly different name)
    if "instrument" not in raw.columns:
        print(f"  WARNING {name}: no instrument column found. Available: {raw.columns.tolist()[:8]}")
        return

    keep = ["date", "instrument"] + [c for c in keep_cols if c in raw.columns]
    raw = raw[keep].copy()
    for col in keep_cols:
        if col in raw.columns:
            raw[col] = raw[col].apply(_to_num)

    raw.to_csv(output_dir / filename, index=False)
    print(f"  Saved {filename}  ({len(raw):,} rows, {len(keep)-2} signals)")


# 1. T86 — 三大法人買賣超日報
def download_institutional(start, end, output_dir):
    _download_daily(
        name="法人進出 (T86)",
        url_template=(
            "https://www.twse.com.tw/fund/T86"
            "?response=json&date={date}&selectType=ALLBUT0999"
        ),
        column_map={
            "證券代號": "instrument",
            "外陸資買賣超股數(不含外資自營商)": "foreign_net",
            "外資及陸資買賣超股數": "foreign_net",       # older field name
            "投信買賣超股數": "trust_net",
            "自營商買賣超股數": "dealer_net",
            "自營商買賣超股數(自行買賣)": "dealer_self_net",
            "自營商買賣超股數(避險)": "dealer_hedge_net",
            "三大法人買賣超股數": "inst_total_net",
        },
        keep_cols=[
            "foreign_net", "trust_net", "dealer_net",
            "dealer_self_net", "dealer_hedge_net", "inst_total_net",
        ],
        start=start, end=end, output_dir=output_dir,
        filename="institutional.csv",
    )


# 2. MI_MARGN — 融資融券總表 (nested 'tables' response, table 1 has per-stock data)
# Custom handler because column names are duplicated (買進/賣出 appear twice for
# 融資 then 融券) so positional assignment is needed.
MARGIN_COLS = [
    "code", "name",
    "margin_buy", "margin_sell", "margin_redeem",
    "margin_prev_balance", "margin_balance", "margin_next_limit",
    "short_buy", "short_sell", "short_redeem",
    "short_prev_balance", "short_balance", "short_next_limit",
    "offset", "note",
]
MARGIN_NUM_COLS = [c for c in MARGIN_COLS if c.startswith(("margin_", "short_", "offset"))]


def download_margin(start, end, output_dir):
    dates = pd.date_range(start, end, freq="B")
    parts_dir = output_dir / "_margin_parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    pending = [d for d in dates if not (parts_dir / f"{d.strftime('%Y%m%d')}.csv").exists()]
    print(
        f"Downloading 融資融券 (MI_MARGN) for {len(dates)} business days "
        f"({len(dates) - len(pending)} cached, {len(pending)} pending) ..."
    )
    if pending:
        with _make_progress() as progress:
            task = progress.add_task("MI_MARGN", total=len(pending), postfix="")
            for d in pending:
                ymd = d.strftime("%Y%m%d")
                url = (
                    "https://www.twse.com.tw/exchangeReport/MI_MARGN"
                    f"?response=json&date={ymd}&selectType=ALL"
                )
                payload = _twse_get(url)
                df = pd.DataFrame()
                if payload.get("stat") == "OK":
                    tables = payload.get("tables") or []
                    if len(tables) >= 2 and tables[1].get("data"):
                        rows = tables[1]["data"]
                        df = pd.DataFrame(rows, columns=MARGIN_COLS[: len(rows[0])])
                        df["date"] = pd.to_datetime(ymd, format="%Y%m%d")
                df.to_csv(parts_dir / f"{ymd}.csv", index=False)
                time.sleep(TWSE_SLEEP)
                progress.update(task, advance=1, postfix=ymd)

    frames = []
    for d in dates:
        p = parts_dir / f"{d.strftime('%Y%m%d')}.csv"
        if p.exists() and p.stat().st_size > 0:
            df = pd.read_csv(p)
            if not df.empty:
                frames.append(df)

    if not frames:
        print("  No 融資融券 data retrieved.")
        return

    raw = pd.concat(frames, ignore_index=True)
    raw = raw.rename(columns={"code": "instrument"})
    keep = ["date", "instrument"] + [c for c in MARGIN_NUM_COLS if c in raw.columns]
    raw = raw[keep].copy()
    for col in MARGIN_NUM_COLS:
        if col in raw.columns:
            raw[col] = raw[col].apply(_to_num)

    raw.to_csv(output_dir / "margin.csv", index=False)
    print(f"  Saved margin.csv  ({len(raw):,} rows, {len(keep)-2} signals)")


# 3. BWIBBU_d — 個股 P/E、P/B、殖利率
def download_valuation(start, end, output_dir):
    _download_daily(
        name="估值比率 (BWIBBU_d)",
        url_template=(
            "https://www.twse.com.tw/exchangeReport/BWIBBU_d"
            "?response=json&date={date}&selectType=ALL"
        ),
        column_map={
            "證券代號": "instrument",
            "殖利率(%)": "div_yield",
            "本益比": "pe_ratio",
            "股價淨值比": "pb_ratio",
        },
        keep_cols=["div_yield", "pe_ratio", "pb_ratio"],
        start=start, end=end, output_dir=output_dir,
        filename="valuation.csv",
    )


# 4. MI_QFIIS — 外資及陸資持股統計 (true holdings %)
def download_foreign_holdings(start, end, output_dir):
    # NOTE on selectType: TWSE endpoints differ in how they interpret "ALL".
    # MI_QFIIS treats `selectType=ALL` as a literal category that matches 0 stocks
    # (returns empty data), while `ALLBUT0999` (everything except warrants/derivatives)
    # returns the full per-stock table. T86 and BWIBBU_d also use ALLBUT0999.
    # Always default to ALLBUT0999 across TWSE endpoints unless documented otherwise.
    _download_daily(
        name="外資持股 (MI_QFIIS)",
        url_template=(
            "https://www.twse.com.tw/fund/MI_QFIIS"
            "?response=json&date={date}&selectType=ALLBUT0999"
        ),
        column_map={
            "證券代號": "instrument",
            "發行股數": "issued_shares",
            "外資及陸資尚可投資股數": "foreign_remaining",
            "全體外資及陸資持有股數": "foreign_held",
            "外資及陸資尚可投資比率": "foreign_remaining_pct",
            "全體外資及陸資持股比率": "foreign_held_pct",
            "外資及陸資共用法令投資上限比率": "foreign_limit_pct",
        },
        keep_cols=[
            "issued_shares", "foreign_remaining", "foreign_held",
            "foreign_remaining_pct", "foreign_held_pct", "foreign_limit_pct",
        ],
        start=start, end=end, output_dir=output_dir,
        filename="foreign_holdings.csv",
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

# Default smoke-test universe: 權值股 (Taiwan 50 heavyweights)
BLUE_CHIPS = [
    "2330",  # 台積電 TSMC
    "2317",  # 鴻海 Hon Hai
    "2454",  # 聯發科 MediaTek
    "2308",  # 台達電 Delta Electronics
    "2382",  # 廣達 Quanta
    "2412",  # 中華電 Chunghwa Telecom
    "2891",  # 中信金 CTBC
    "2882",  # 國泰金 Cathay
    "2881",  # 富邦金 Fubon
    "1301",  # 台塑 Formosa Plastics
    "1303",  # 南亞 Nan Ya
    "2002",  # 中鋼 China Steel
    "1216",  # 統一 Uni-President
    "2207",  # 和泰車 Hotai
    "3711",  # 日月光投控 ASE
    "2303",  # 聯電 UMC
    "2886",  # 兆豐金 Mega
    "2884",  # 玉山金 E.SUN
    "2885",  # 元大金 Yuanta
    "6505",  # 台塑化 Formosa Petrochem
]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2015-01-01")
    parser.add_argument("--end", default=str(date.today()))
    parser.add_argument("--output_dir", default="tw_raw_data")
    parser.add_argument(
        "--codes",
        default="",
        help="Comma-separated stock codes. Use 'blue_chips' for default 權值股 list. Empty = full TWSE.",
    )
    # Per-stock-per-month
    parser.add_argument("--skip_ohlcv", action="store_true")
    # Per-day all-stocks
    parser.add_argument("--skip_institutional", action="store_true")
    parser.add_argument("--skip_margin", action="store_true")
    parser.add_argument("--skip_valuation", action="store_true")
    parser.add_argument("--skip_foreign_holdings", action="store_true")
    args = parser.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    if args.codes == "blue_chips":
        codes = BLUE_CHIPS
        print(f"Smoke-test mode: {len(codes)} 權值股")
    elif args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        print(f"Custom universe: {len(codes)} stocks")
    else:
        codes = get_twse_stock_list()
        print(f"Full universe: {len(codes)} TWSE stocks")
    print()

    # OHLCV is per-stock-per-month → pass codes
    if not args.skip_ohlcv:
        download_ohlcv(codes, args.start, args.end, out)

    # Per-day endpoints fetch all stocks; filtering to universe happens in generate.py
    if not args.skip_institutional:
        download_institutional(args.start, args.end, out)
    if not args.skip_margin:
        download_margin(args.start, args.end, out)
    if not args.skip_valuation:
        download_valuation(args.start, args.end, out)
    if not args.skip_foreign_holdings:
        download_foreign_holdings(args.start, args.end, out)

    print("\nAll done. Run generate.py to build tw_daily.h5")


if __name__ == "__main__":
    main()
