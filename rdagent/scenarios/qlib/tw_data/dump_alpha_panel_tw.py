"""
Build the contest-aligned daily panel for the TW alpha factory (docs/tw/ALPHA_FACTORY.md).

The AI CUP 2026 contest trades a fixed list of 150 stocks: the top 100 TWSE and the top 50
TPEx common stocks by market cap at the end of July 2026. Using that fixed list on old data
flatters momentum, because the winners grew into it. Research therefore uses a point-in-time
copy of the rule: at every month end, rank by market cap, and hold that selection for the
next month.

Timing rule: every value at (datetime, instrument) was known by the evening of `datetime`.
  - daily price, flow, margin, valuation data: known after that day's close
  - monthly revenue: known from the 10th of the following month (the legal deadline).
    FinMind's `date` column (1st of the following month) is NOT the release day.
  - universe membership: chosen from the previous month end's market caps

$vwap: ohlcv.csv prices are back-adjusted (TaiwanStockPriceAdj) but $amount/$volume are raw,
so amount/volume is a raw VWAP. With --raw_ohlcv (raw closes, e.g. the TWSE-direct
data/tw_raw_full/ohlcv.csv) it is rescaled exactly; elsewhere the adjusted typical price
(high+low+close)/3 stands in. $vwap_exact marks the exact rows. No trades -> $vwap is NaN
(the contest voids orders for stocks without a VWAP that day).

Outputs (default data/alpha_tw/):
  alpha_panel.h5        key "data": (datetime, instrument) x float fields, for every stock
                        ever in the point-in-time universe (plus --official_list)
  universe_pit.csv      month_end, instrument, market, rank, market_value
  instruments_meta.csv  instrument, name, market, industry, industry_id
  panel_meta.json       sources, date range, VWAP coverage
  coder/daily_pv.h5     for the alpha loop's LLM coder (DECISIONS D16): stock codes replaced by
                        S001.., research window only, no contest-list flag
  coder_debug/          the same, last year of the research window (fast coding rounds)
  coder_full/           the same, all dates: only the holdout script reads it, never the loop
  coder_map.csv         S-ID -> stock code (kept outside the coder folders)

    python -m rdagent.scenarios.qlib.tw_data.dump_alpha_panel_tw
    python -m rdagent.scenarios.qlib.tw_data.dump_alpha_panel_tw \
        --raw_ohlcv data/tw_raw_full/ohlcv.csv \
        --official_list /mnt/g/AI_cup_2026/backtesting/universe_150.csv
"""

import argparse
import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from rdagent.scenarios.qlib.tw_data._paths import DATA_DIR
except ImportError:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _paths import DATA_DIR

# 4-digit codes starting 1-9 are common stocks (00xx = ETFs); 91xx are TDRs, not stocks.
STOCK_CODE = re.compile(r"^[1-9]\d{3}$")
REVENUE_DEADLINE_DAY = 10
UMBRELLA_INDUSTRY = "電子工業"  # FinMind lists electronics names under it and a specific one
OFFICIAL_SELECTION_MONTH = "2026-07"  # the contest list uses end-of-July 2026 market caps

# (file, columns kept); every file is keyed by (date, instrument)
DAILY_SOURCES = [
    ("institutional.csv", ["foreign_net", "trust_net", "dealer_net"]),
    ("margin.csv", ["margin_balance", "short_balance"]),
    ("valuation.csv", ["div_yield", "pe_ratio", "pb_ratio"]),
    ("foreign_holdings.csv", ["issued_shares", "foreign_held_pct"]),
]


def is_stock(code: str) -> bool:
    return bool(STOCK_CODE.match(code)) and not code.startswith("91")


def _read_filtered(path: Path, usecols: list[str], start: str | None = None,
                   instruments: set[str] | None = None, chunksize: int = 2_000_000) -> pd.DataFrame:
    """Chunked read of a (date, instrument, ...) CSV, keeping only the rows we need."""
    parts = []
    for chunk in pd.read_csv(path, usecols=usecols, dtype={"instrument": str}, chunksize=chunksize):
        if start is not None:
            chunk = chunk[chunk["date"] >= start]  # ISO strings compare like dates
        if instruments is not None:
            chunk = chunk[chunk["instrument"].isin(instruments)]
        if len(chunk):
            parts.append(chunk)
    df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=usecols)
    df["date"] = pd.to_datetime(df["date"])
    return df


def load_market_value(path: Path, start: str) -> tuple[pd.DataFrame, pd.Series, pd.DatetimeIndex]:
    """Market caps from one month before `start`, first listed date per code, trading calendar."""
    first_seen: dict[str, str] = {}
    dates: set[str] = set()
    keep_from = (pd.Timestamp(start) - pd.DateOffset(months=1)).strftime("%Y-%m-%d")
    parts = []
    for chunk in pd.read_csv(path, dtype={"instrument": str}, chunksize=2_000_000):
        firsts = chunk.groupby("instrument")["date"].min()
        for code, d in firsts.items():
            if code not in first_seen or d < first_seen[code]:
                first_seen[code] = d
        dates.update(chunk["date"].unique())
        parts.append(chunk[chunk["date"] >= keep_from])
    mv = pd.concat(parts, ignore_index=True)
    mv["date"] = pd.to_datetime(mv["date"])
    calendar = pd.DatetimeIndex(sorted(pd.to_datetime(list(dates))))
    return mv, pd.to_datetime(pd.Series(first_seen)), calendar


def load_stock_info(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-stock name / current market / primary industry, plus dated market snapshots."""
    info = pd.read_csv(path, dtype={"stock_id": str}).rename(columns={"stock_id": "instrument"})
    info = info[info["instrument"].map(is_stock)].copy()
    info["date"] = pd.to_datetime(info["date"], errors="coerce")
    info = info.sort_values("date", na_position="first")

    def primary(cats: pd.Series) -> str:
        unique = list(dict.fromkeys(cats))
        specific = [c for c in unique if c != UMBRELLA_INDUSTRY]
        return (specific or unique)[0]

    meta = info.groupby("instrument").agg(
        name=("stock_name", "last"), market=("type", "last"), industry=("industry_category", primary)
    )
    industries = sorted(meta["industry"].dropna().unique())
    meta["industry_id"] = meta["industry"].map({name: i for i, name in enumerate(industries)})
    snapshots = info.dropna(subset=["date"])[["instrument", "date", "type"]].drop_duplicates()
    return meta, snapshots


def load_index_weights(path: Path) -> pd.DataFrame:
    """TWSE/TPEx index weight snapshots: date, instrument, weight_per, type.

    FinMind has them from 2024-10-30 (TWSE at month ends, with gaps; TPEx more often). Only
    listed stocks carry a weight, and the contest list matches these weights' ranks.
    """
    cols = ["date", "instrument", "weight_per", "type"]
    if not path.exists():
        return pd.DataFrame(columns=cols)
    weights = pd.read_csv(path, usecols=cols, dtype={"instrument": str})
    weights = weights[weights["instrument"].map(is_stock)].copy()
    weights["date"] = pd.to_datetime(weights["date"])
    return weights


def market_at(snapshots: pd.DataFrame, keys: pd.DataFrame) -> pd.Series:
    """Market (twse/tpex/emerging) of each (month_end, instrument) row in `keys`.

    Uses the latest snapshot on or before month_end; before the first snapshot, the earliest
    one (FinMind snapshots are sparse, most are dated at fetch time).
    """
    snaps = snapshots.sort_values("date")
    left = keys[["month_end", "instrument"]].reset_index().sort_values("month_end")
    hit = pd.merge_asof(left, snaps, left_on="month_end", right_on="date", by="instrument",
                        direction="backward")
    earliest = snaps.groupby("instrument")["type"].first()
    market = hit["type"].fillna(hit["instrument"].map(earliest))
    return pd.Series(market.to_numpy(), index=hit["index"].to_numpy()).reindex(keys.index)


def select_universe(mv: pd.DataFrame, first_seen: pd.Series, calendar: pd.DatetimeIndex,
                    snapshots: pd.DataFrame, n_twse: int = 100, n_tpex: int = 50,
                    min_history_days: int = 60, index_weights: pd.DataFrame | None = None) -> pd.DataFrame:
    """Top-n TWSE + top-n TPEx stocks at every month end in `mv`.

    The contest list matches index-weight ranks, not raw market caps (149/150 on 2026-07-31;
    台灣高鐵 is 90th by market cap but 145th by weight). So where a month-end weight snapshot
    exists (from 2024-10-30), stocks are ranked by weight; earlier, by market cap.

    Market type comes from the weights when one is within 95 days (they catch TPEx -> TWSE
    moves), else from FinMind's snapshots. Those snapshots are dated when FinMind recorded
    them, not when the type changed, so a stock that came off the emerging board stays
    "emerging" there until FinMind's next refresh. For those stocks the first weight day is the
    listing date: before it they are emerging, and seasoning counts from it.
    """
    month_ends = calendar.to_series().groupby(calendar.to_period("M")).max()
    cand = mv[mv["date"].isin(month_ends.to_numpy()) & mv["instrument"].map(is_stock)]
    cand = cand.rename(columns={"date": "month_end"}).reset_index(drop=True)
    cand["market"] = market_at(snapshots, cand)
    cand["weight"] = np.nan
    listed = cand["instrument"].map(first_seen)
    if index_weights is not None and len(index_weights):
        w = index_weights.sort_values("date")
        keys = cand[["month_end", "instrument"]].reset_index().sort_values("month_end")

        def latest(col: str, days: int) -> pd.Series:
            hit = pd.merge_asof(keys, w, left_on="month_end", right_on="date", by="instrument",
                                direction="backward", tolerance=pd.Timedelta(days=days))
            return pd.Series(hit[col].to_numpy(), index=hit["index"].to_numpy()).reindex(cand.index)

        cand["market"] = latest("type", 95).fillna(cand["market"])
        cand["weight"] = latest("weight_per", 5)
        debuts = w.groupby("instrument")["date"].min()
        movers = set(snapshots.loc[snapshots["type"] == "emerging", "instrument"]) & set(debuts.index)
        is_mover = cand["instrument"].isin(movers)
        debut = cand["instrument"].map(debuts)
        cand.loc[is_mover & ~(cand["month_end"] >= debut), "market"] = "emerging"
        listed = listed.where(~is_mover, debut)
    cal = calendar.to_numpy()
    age = np.searchsorted(cal, cand["month_end"].to_numpy()) - np.searchsorted(cal, listed.to_numpy())
    cand = cand[age >= min_history_days].copy()
    picks = []
    for (_, market), grp in cand[cand["market"].isin(["twse", "tpex"])].groupby(["month_end", "market"]):
        key = "weight" if grp["weight"].notna().mean() >= 0.8 else "market_value"
        top = grp.dropna(subset=[key]).nlargest(n_twse if market == "twse" else n_tpex, key).copy()
        top["rank"] = np.arange(1, len(top) + 1)
        top["ranked_by"] = key
        picks.append(top)
    cols = ["month_end", "instrument", "market", "rank", "ranked_by", "market_value"]
    return pd.concat(picks)[cols].sort_values(["month_end", "market", "rank"]).reset_index(drop=True)


def membership(universe: pd.DataFrame, index: pd.MultiIndex) -> pd.DataFrame:
    """In-universe flag and market for each (datetime, instrument).

    A month-end selection applies to the trading days after it, up to the next month end.
    """
    month_ends = np.sort(universe["month_end"].unique())
    dt = index.get_level_values("datetime").to_numpy()
    slot = np.searchsorted(month_ends, dt, side="left") - 1  # last month end strictly before dt
    sel = pd.DataFrame({
        "month_end": np.where(slot >= 0, month_ends[np.clip(slot, 0, None)], np.datetime64("NaT")),
        "instrument": index.get_level_values("instrument"),
    })
    sel = sel.merge(universe[["month_end", "instrument", "market"]], on=["month_end", "instrument"],
                    how="left")
    return pd.DataFrame({
        "$in_universe": sel["market"].notna().astype(float).to_numpy(),
        "$is_twse": sel["market"].map({"twse": 1.0, "tpex": 0.0}).to_numpy(),
    }, index=index)


def revenue_monthly(rev: pd.DataFrame) -> pd.DataFrame:
    """Monthly revenue features per instrument, with the date each month became known."""
    rev = rev.copy()
    rev["period"] = pd.to_datetime(
        pd.DataFrame({"year": rev["revenue_year"], "month": rev["revenue_month"], "day": 1})
    ).dt.to_period("M")
    rows = []
    for code, grp in rev.groupby("instrument"):
        s = grp.drop_duplicates("period", keep="last").set_index("period")["revenue"].astype(float)
        s = s.reindex(pd.period_range(s.index.min(), s.index.max(), freq="M"))  # gaps stay NaN

        def growth(cur: pd.Series, lags: int) -> pd.Series:
            base = cur.shift(lags)
            return (cur / base - 1).where(base > 0)

        r3 = s.rolling(3, min_periods=3).sum()
        r12 = s.rolling(12, min_periods=12).sum()
        out = pd.DataFrame({
            "$rev": s,
            "$rev_yoy": growth(s, 12),
            "$rev_3m_yoy": growth(r3, 12),
            "$rev_12m_yoy": growth(r12, 12),
        })
        out["$rev_accel"] = out["$rev_3m_yoy"] - out["$rev_12m_yoy"]
        out = out[s.notna()]  # a missing month never becomes "known"
        out["known_date"] = (out.index + 1).to_timestamp() + pd.Timedelta(days=REVENUE_DEADLINE_DAY - 1)
        out["instrument"] = code
        rows.append(out.reset_index(drop=True))
    return pd.concat(rows, ignore_index=True)


def revenue_daily(monthly: pd.DataFrame, index: pd.MultiIndex) -> pd.DataFrame:
    """As-of join: each trading day sees the latest revenue month whose deadline has passed."""
    left = pd.DataFrame({
        "datetime": index.get_level_values("datetime"),
        "instrument": index.get_level_values("instrument"),
    }).reset_index().sort_values("datetime")
    right = monthly.sort_values("known_date")
    hit = pd.merge_asof(left, right, left_on="datetime", right_on="known_date", by="instrument",
                        direction="backward")
    hit["$rev_age"] = (hit["datetime"] - hit["known_date"]).dt.days.astype(float)
    cols = ["$rev", "$rev_yoy", "$rev_3m_yoy", "$rev_12m_yoy", "$rev_accel", "$rev_age"]
    return hit.set_index("index").sort_index()[cols].set_axis(index)


def add_vwap(panel: pd.DataFrame, raw_close: pd.Series | None) -> pd.DataFrame:
    traded = (panel["$volume"] > 0) & (panel["$close"] > 0)
    vwap_raw = (panel["$amount"] / panel["$volume"]).where(traded)
    typical = (panel["$high"] + panel["$low"] + panel["$close"]) / 3
    exact = pd.Series(np.nan, index=panel.index)
    if raw_close is not None:
        ratio = panel["$close"] / raw_close.reindex(panel.index)
        exact = vwap_raw * ratio
        exact = exact.where(exact.between(panel["$low"] * 0.95, panel["$high"] * 1.05))
    panel["$vwap"] = exact.fillna(typical).where(traded)
    panel["$vwap_exact"] = exact.notna().astype(float).where(traded)
    return panel


RESEARCH_END = "2025-06-30"  # alpha_eval_tw.WINDOWS["research"]; DeepSeek V4 data reaches ~May 2025
DEBUG_START = "2024-07-01"
CODER_DROP = ["$in_official", "$vwap_exact"]  # the contest list is chosen in July 2026: future info


def write_coder_data(panel: pd.DataFrame, out_dir: Path) -> None:
    """Anonymized copies of the panel for the alpha loop (see module docstring)."""
    codes = sorted(panel.index.get_level_values("instrument").unique())
    ids = {code: f"S{i + 1:03d}" for i, code in enumerate(codes)}
    pd.Series(ids, name="sid").rename_axis("instrument").to_csv(out_dir / "coder_map.csv")
    anon = panel.drop(columns=[c for c in CODER_DROP if c in panel.columns])
    anon = anon.rename(index=ids, level="instrument").sort_index()
    dates = anon.index.get_level_values("datetime")
    for folder, rows in (("coder", dates <= RESEARCH_END),
                         ("coder_debug", (dates >= DEBUG_START) & (dates <= RESEARCH_END)),
                         ("coder_full", dates == dates)):
        (out_dir / folder).mkdir(parents=True, exist_ok=True)
        anon[rows].to_hdf(out_dir / folder / "daily_pv.h5", key="data", mode="w")
    print(f"Coder data: {len(ids)} anonymized stocks; research rows {int((dates <= RESEARCH_END).sum()):,}")


def build(input_dir: Path, out_dir: Path, start: str, raw_ohlcv: Path | None,
          official_list: Path | None, n_twse: int, n_tpex: int, min_history_days: int) -> None:
    mv, first_seen, calendar = load_market_value(input_dir / "market_value.csv", start)
    meta, snapshots = load_stock_info(input_dir / "stock_info.csv")
    weights = load_index_weights(input_dir / "market_value_weight.csv")
    universe = select_universe(mv, first_seen, calendar, snapshots, n_twse, n_tpex, min_history_days, weights)
    by_weight = universe.loc[universe["ranked_by"] == "weight", "month_end"].nunique()
    print(f"Ranking: {by_weight} month ends use index weights for at least one market; the rest market cap")
    universe = universe[universe["month_end"] >= pd.Timestamp(start) - pd.DateOffset(months=1)]
    official: set[str] = set()
    if official_list is not None:
        official = set(pd.read_csv(official_list, dtype={"stock_id": str})["stock_id"])
    codes = set(universe["instrument"]) | official
    print(f"Universe: {universe['month_end'].nunique()} month ends, {len(codes)} stocks ever selected")

    px = _read_filtered(input_dir / "ohlcv.csv",
                        ["date", "instrument", "$open", "$high", "$low", "$close", "$volume", "$amount"],
                        start, codes)
    panel = px.rename(columns={"date": "datetime"}).set_index(["datetime", "instrument"]).sort_index()
    bad = panel["$close"] <= 0
    panel.loc[bad, ["$open", "$high", "$low", "$close"]] = np.nan

    raw_close = None
    if raw_ohlcv is not None:
        raw = _read_filtered(raw_ohlcv, ["date", "instrument", "$close"], start, codes)
        raw_close = raw.rename(columns={"date": "datetime"}).set_index(["datetime", "instrument"])["$close"]
        raw_close = raw_close[~raw_close.index.duplicated(keep="last")].where(lambda s: s > 0)
    panel = add_vwap(panel, raw_close)

    mv_daily = mv[mv["instrument"].isin(codes)].rename(columns={"date": "datetime"})
    mv_daily = mv_daily.set_index(["datetime", "instrument"])["market_value"]
    panel["$market_value"] = mv_daily[~mv_daily.index.duplicated(keep="last")].reindex(panel.index)
    for fname, cols in DAILY_SOURCES:
        df = _read_filtered(input_dir / fname, ["date", "instrument", *cols], start, codes)
        df = df.rename(columns={"date": "datetime"}).set_index(["datetime", "instrument"])
        df = df[~df.index.duplicated(keep="last")]
        panel = panel.join(df.rename(columns={c: f"${c}" for c in cols}), how="left")
        print(f"  joined {fname}: {cols}")

    rev = pd.read_csv(input_dir / "month_revenue.csv", dtype={"instrument": str})
    panel = panel.join(revenue_daily(revenue_monthly(rev[rev["instrument"].isin(codes)]), panel.index))
    panel = panel.join(membership(universe, panel.index))
    panel["$in_official"] = panel.index.get_level_values("instrument").isin(official).astype(float)
    panel["$industry_id"] = panel.index.get_level_values("instrument").map(meta["industry_id"]).astype(float)
    panel = panel.astype("float64")

    out_dir.mkdir(parents=True, exist_ok=True)
    panel.to_hdf(out_dir / "alpha_panel.h5", key="data", mode="w")
    write_coder_data(panel, out_dir)
    universe.to_csv(out_dir / "universe_pit.csv", index=False)
    meta.loc[meta.index.isin(codes)].to_csv(out_dir / "instruments_meta.csv")

    dates = panel.index.get_level_values("datetime")
    in_uni = panel["$in_universe"] == 1
    summary = {
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "input_dir": str(input_dir),
        "sources_mtime": {p.name: datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
                          for p in sorted(input_dir.glob("*.csv"))
                          if p.name in {"ohlcv.csv", "market_value.csv", "month_revenue.csv", "stock_info.csv",
                                        *(f for f, _ in DAILY_SOURCES)}},
        "raw_ohlcv": str(raw_ohlcv) if raw_ohlcv else None,
        "official_list": str(official_list) if official_list else None,
        "start": str(dates.min().date()),
        "end": str(dates.max().date()),
        "rows": int(len(panel)),
        "instruments": int(panel.index.get_level_values("instrument").nunique()),
        "fields": list(panel.columns),
        "vwap_exact_share_in_universe": float(panel.loc[in_uni, "$vwap_exact"].mean()),
        "universe": {"n_twse": n_twse, "n_tpex": n_tpex, "min_history_days": min_history_days},
    }
    (out_dir / "panel_meta.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"Panel: {len(panel):,} rows x {panel.shape[1]} fields, {summary['start']} -> {summary['end']}, "
          f"exact VWAP on {summary['vwap_exact_share_in_universe']:.0%} of universe rows -> {out_dir}")
    if official:
        # The official list was drawn at the end of July 2026; compare with that month if we have it.
        ends = universe["month_end"]
        in_july = ends[ends.dt.to_period("M") == pd.Period(OFFICIAL_SELECTION_MONTH)]
        me = in_july.max() if len(in_july) else ends.max()
        picked = set(universe.loc[ends == me, "instrument"])
        print(f"Official list vs PIT selection at {me.date()}: {len(picked & official)}/{len(official)} overlap")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--input_dir", default=str(DATA_DIR / "tw_finmind_full_clean"))
    parser.add_argument("--out_dir", default=str(DATA_DIR / "alpha_tw"))
    parser.add_argument("--start", default="2019-01-01",
                        help="First panel date; 2019 leaves two years of look-back before 2021")
    parser.add_argument("--raw_ohlcv", default=None,
                        help="CSV with raw (unadjusted) $close by date/instrument, for exact VWAP")
    parser.add_argument("--official_list", default=None,
                        help="CSV with a stock_id column (the contest's 150 names), flagged as $in_official")
    parser.add_argument("--n_twse", type=int, default=100)
    parser.add_argument("--n_tpex", type=int, default=50)
    parser.add_argument("--min_history_days", type=int, default=60,
                        help="Skip stocks listed fewer trading days than this at selection")
    args = parser.parse_args()
    build(Path(args.input_dir), Path(args.out_dir), args.start,
          Path(args.raw_ohlcv) if args.raw_ohlcv else None,
          Path(args.official_list) if args.official_list else None,
          args.n_twse, args.n_tpex, args.min_history_days)


if __name__ == "__main__":
    main()
