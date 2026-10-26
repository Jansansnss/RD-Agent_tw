"""
Formal factor report for the TW alpha lab (docs/tw/FACTOR_REPORT.md): one factor, two outputs from
the same numbers.

  report.json  for an LLM (the team's decision model): fixed keys, percentages as plain numbers,
               the code's facts kept apart from the author's claims and the reviewer's view, no
               tickers or names.
  report.html  for people: a tear sheet with charts, tables, the reasoning and the verdict.

Every number comes from code: the alpha loop's own judge (alpha_eval_tw) on the contest-aligned
panel (point-in-time 150-stock universe, next-day VWAP fills, real costs, equal-weight top-24 book
against B0 = the universe equal-weighted), plus 0050, TAIEX, TPEx and the active ETFs that hold
Taiwan stocks. The author writes the words in a declaration (YAML): stage 1 before seeing any
result (factor_declare_prompt_zh.md), stage 2 after (factor_report_prompt_zh.md; this script fills
its inputs). Without --declaration a pre-filled template is written next to the report.

    python -m rdagent.scenarios.qlib.tw_data.factor_report_tw --baseline rev_accel
    python -m rdagent.scenarios.qlib.tw_data.factor_report_tw --card SalesYield_LiquiditySize_70_30 --holdout status
    python -m rdagent.scenarios.qlib.tw_data.factor_report_tw --alpha_file mine.csv --declaration mine.yaml

--holdout none (default) scores the research window only, so the report may go to any LLM.
status: the holdout is scored too; people see its numbers, report.json gets only pass/fail.
full: report.json carries the holdout numbers as well, for a model that is not being tested on
that period (ALPHA_FACTORY §7). The research gate is applied at the declared horizon only.
"""

import argparse
import datetime as dt
import html
import json
import math
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

try:
    from rdagent.scenarios.qlib.tw_data import alpha_eval_tw as ev
    from rdagent.scenarios.qlib.tw_data._paths import DATA_DIR, REPO_ROOT
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import alpha_eval_tw as ev
    from _paths import DATA_DIR, REPO_ROOT

HERE = Path(__file__).resolve().parent
ALPHA_DIR = DATA_DIR / "alpha_tw"
CLEAN_DIR = DATA_DIR / "tw_finmind_full_clean"
DECLARE_PROMPT = HERE / "factor_declare_prompt_zh.md"
REPORT_PROMPT = HERE / "factor_report_prompt_zh.md"
SCHEMA = "tw-factor-report/1"
TD = ev.TRADING_DAYS_PER_YEAR
PODS = ("營收獲利動能", "價格動能", "資金流向", "事件催化", "風險控管")
MOSTLY = 0.7  # |correlation| with a plain style above this: the factor is mostly that style (D24)
# An active ETF counts as holding Taiwan stocks by its daily correlation with TPEx: on 2026-10 data the
# Taiwan funds sit at 0.86-0.94 and the US/global ones at 0.81 or less (with TAIEX they overlap, 0.75-0.86,
# because the 2025-26 AI rally moved both markets).
ETF_MIN_DAYS, ETF_MIN_CORR = 60, 0.85
INDEXES = {"0050": "0050（含息）", "TAIEX": "加權指數（不含息）", "TPEx": "櫃買指數（不含息）"}
WINDOW_ZH = {"research": "研究期", "holdout": "保留期"}

BASELINE_TEXT = {  # alpha_eval_tw.BASELINES: display name, pod, formula
    "mom20": ("20 日動能", "價格動能", "close(t) / close(t−20) − 1"),
    "mom60_skip5": ("60 日動能（略過近 5 日）", "價格動能", "close(t−5) / close(t−60) − 1"),
    "rev5": ("5 日反轉", "價格動能", "−(close(t) / close(t−5) − 1)"),
    "lowvol60": ("60 日低波動", "風險控管", "−std(日報酬, 60 日)"),
    "rev_yoy": ("單月營收年增", "營收獲利動能", "$rev_yoy"),
    "rev_accel": ("營收加速", "營收獲利動能", "$rev_3m_yoy − $rev_12m_yoy"),
    "trust_flow5": ("投信 5 日買超", "資金流向", "Σ 投信買賣超(5 日) ÷ 發行股數"),
    "trust_flow20": ("投信 20 日買超", "資金流向", "Σ 投信買賣超(20 日) ÷ 發行股數"),
    "foreign_flow20": ("外資 20 日買超", "資金流向", "Σ 外資買賣超(20 日) ÷ 發行股數"),
}
STYLES = {  # alpha_review_tw.exposures() key -> (JSON key, label)
    "size (log market value)": ("size", "規模（log 市值）"),
    "liquidity (-Amihud 20d)": ("liquidity", "流動性（−Amihud 20 日）"),
    "sales yield (12 x revenue / market value)": ("sales_yield", "營收殖利率（12×月營收÷市值）"),
    "earnings yield (1 / PE)": ("earnings_yield", "盈餘殖利率（1÷本益比）"),
    "book to price (1 / PB)": ("book_to_price", "淨值市價比（1÷股價淨值比）"),
    "momentum (mom60_skip5)": ("momentum_60", "中期動能（60 日，略過近 5 日）"),
    "low volatility (lowvol60)": ("low_vol_60", "低波動（60 日）"),
    "revenue growth (rev_yoy)": ("revenue_growth", "營收成長（單月年增）"),
}
STYLE_LABEL = {**{k: v for k, v in STYLES.values()}, "mom20_crowding": "短期動能 mom20（擁擠度代理）"}
FAILED_CHECK = (("coverage", "coverage"), ("IC t-stat", "ic_t"), ("positive IC", "years_positive"),
                ("median 24-day", "window_median"), ("correlation", "library_corr"))
REASON_ZH = (  # alpha_eval_tw.gate() reasons, in the report's language
    (r"coverage (\S+) < (\S+)", r"覆蓋率 \1 < \2"),
    (r"IC t-stat (\S+) at (\d+)d < (\S+)", r"\2 日 IC t 值 \1 < \3"),
    (r"positive IC in (\d+) full years < (\d+)", r"IC 為正的完整年度 \1 個 < \2"),
    (r"median 24-day net excess (\S+) <= (\S+)", r"24 日超額中位數 \1 ≤ \2"),
    (r"correlation (\S+) with the library > (\S+)", r"與因子庫相關 \1 > \2"),
)
HOW_TO_READ = [  # first lines of report.json, so a model that never saw FACTOR_REPORT.md reads it right
    "facts 與 prediction_checks 由程式計算（alpha_eval_tw，和因子迴圈同一套判分）；claims 是作者的說法；"
    "review 是另一個模型或隊友的審查。互相衝突時以 facts 為準。",
    "*_pct 是百分比數字（1.5 代表 1.5%）；ic 是 Spearman 相關；t 是 Newey-West t 值；beta、corr、ir 是比值。",
    "研究期用來找因子；保留期只驗一次；比賽期間的實盤才是完全乾淨的證據。"
    "不滿一年的期間不年化（值為 null），請看累積。",
    "book：每天選分數最高的 24 檔等權、隔日 VWAP 成交、每 5 日調整、前 36 名內續抱、扣實際成本。"
    "B0：當月 150 檔等權。",
]
STATUS_ZH = {"supported": "支持", "weak": "方向對、不顯著", "not_supported": "沒有支持",
             "contradicted": "相反", "manual": "程式無法檢查"}
ROLE_ZH = {"selection_alpha": "選股訊號", "style_tilt": "刻意的風格傾斜", "risk_control": "風險控管",
           "watch": "觀察（紙上追蹤）", "reject": "不用"}
MECH_ZH = {"risk_premium": "風險溢酬", "behavioral": "行為偏誤", "structural": "結構與交易摩擦",
           "unknown": "機制未知（統計訊號）"}


# --------------------------------------------------------------------------- factor values


def code_map() -> pd.Series:
    return pd.read_csv(ALPHA_DIR / "coder_map.csv", dtype=str).set_index("sid")["instrument"]


def to_codes(s: pd.Series) -> pd.Series:
    """Values on the anonymized coder panels (S001..) back on stock codes; codes pass through."""
    s = s.rename_axis(["datetime", "instrument"])
    inst = s.index.get_level_values(1).astype(str)
    if not inst.str.fullmatch(r"S\d+").all():
        return s
    s.index = pd.MultiIndex.from_arrays([s.index.get_level_values(0), inst.map(code_map())],
                                        names=["datetime", "instrument"])
    return s[s.index.get_level_values(1).notna()]


def read_values(path: Path) -> pd.Series:
    """A teammate's values: CSV (date, stock code, one value column), parquet or HDF5."""
    if path.suffix != ".csv":
        return ev.load_alpha_file(path)
    df = pd.read_csv(path, dtype={"instrument": str, "stock_id": str})
    df = df.rename(columns={"date": "datetime", "stock_id": "instrument"})
    value = [c for c in df.columns if c not in ("datetime", "instrument")]
    if len(value) != 1:
        raise SystemExit(f"{path}: need columns datetime, instrument and one value column, got {list(df.columns)}")
    df["datetime"] = pd.to_datetime(df["datetime"])
    return df.set_index(["datetime", "instrument"])[value[0]].astype(float)


def load_factor(args, panel: pd.DataFrame) -> tuple[pd.Series, dict]:
    """Factor values on (datetime, stock code), and what the source already says about the factor."""
    coder_data = (ALPHA_DIR / "coder_full" / "daily_pv.h5").resolve()
    if args.baseline:
        name, pod, formula = BASELINE_TEXT.get(args.baseline, (args.baseline, None, None))
        return ev.BASELINES[args.baseline](panel), {
            "id": args.baseline, "name": name, "pod": pod, "formula": formula,
            "source": f"alpha_eval_tw.BASELINES['{args.baseline}']"}
    if args.idea:
        try:
            from rdagent.scenarios.qlib.tw_data.alpha_team_ideas_tw import IDEAS, Wide
        except ImportError:
            from alpha_team_ideas_tw import IDEAS, Wide
        label, formula = IDEAS[args.idea]
        values = ev._long(formula(Wide(pd.read_hdf(coder_data, key="data"))))
        return to_codes(values), {"id": args.idea, "name": label,
                                  "source": f"alpha_team_ideas_tw.IDEAS['{args.idea}']"}
    try:
        from rdagent.scenarios.qlib.tw_data.alpha_holdout_tw import run_factor
    except ImportError:
        from alpha_holdout_tw import run_factor
    if args.card:
        folder = ALPHA_DIR / "library" / args.card
        if not (folder / "card.json").exists():
            raise SystemExit(f"{args.card}: no card.json (a seed? use --baseline {args.card.removeprefix('traded_')})")
        card = json.loads((folder / "card.json").read_text(encoding="utf-8"))
        return to_codes(run_factor((folder / "factor.py").resolve(), coder_data)), {
            "id": args.card, "name": args.card, "formula": card.get("formulation"),
            "one_liner": card.get("description"), "horizon_days": card.get("gate_horizon_days"),
            "source": f"data/alpha_tw/library/{args.card}/factor.py", "loop_hypothesis": card.get("hypothesis")}
    if args.factor_py:
        path = Path(args.factor_py)
        return to_codes(run_factor(path.resolve(), coder_data)), {"id": path.stem, "source": str(path)}
    path = Path(args.alpha_file)
    return to_codes(read_values(path)), {"id": path.stem, "source": str(path)}


def library_values(skip: set[str]) -> dict[str, pd.Series]:
    """The alpha library the loop checks correlation against (cards and seeds, research window)."""
    lib = {}
    for f in sorted((ALPHA_DIR / "library").glob("*/values.parquet")):
        if f.parent.name in skip:
            continue
        v = pd.read_parquet(f).iloc[:, 0].astype(float)
        lib[f.parent.name] = to_codes(v)
    return lib


def style_values(panel: pd.DataFrame) -> dict[str, pd.Series]:
    """Plain styles a factor may be a disguised copy of (alpha_review_tw), plus mom20 for crowding."""
    try:
        from rdagent.scenarios.qlib.tw_data.alpha_review_tw import exposures
    except ImportError:
        from alpha_review_tw import exposures
    out = {STYLES[k][0]: v for k, v in exposures(panel).items() if k in STYLES}
    out["mom20_crowding"] = ev.BASELINES["mom20"](panel)
    return out


# --------------------------------------------------------------------------- benchmarks


class Bench:
    """Daily closes of 0050, TAIEX, TPEx and the active ETFs (codes 00xxxA), with names.

    ETF closes are FinMind's back-adjusted prices, so dividends are in and fees are out (an ETF's
    price is after its fees); TAIEX and TPEx are price indexes without dividends. Cached in
    data/alpha_tw/benchmarks.csv, rebuilt when the clean CSVs are newer.
    """

    def __init__(self, refresh: bool = False):
        cache = ALPHA_DIR / "benchmarks.csv"
        sources = [CLEAN_DIR / "benchmark.csv", CLEAN_DIR / "ohlcv.csv"]
        stale = not cache.exists() or cache.stat().st_mtime < max(p.stat().st_mtime for p in sources)
        if refresh or stale:
            cols = ["date", "instrument", "$close"]
            parts = [pd.read_csv(sources[0], usecols=cols, dtype={"instrument": str})]
            for chunk in pd.read_csv(sources[1], usecols=cols, dtype={"instrument": str}, chunksize=2_000_000):
                parts.append(chunk[chunk["instrument"].str.fullmatch(r"00\d{3}A")])
            long = pd.concat(parts)
            long["date"] = pd.to_datetime(long["date"])
            closes = long.pivot_table(index="date", columns="instrument", values="$close", aggfunc="last")
            closes.sort_index().to_csv(cache)
        self.closes = pd.read_csv(cache, index_col=0, parse_dates=True)
        self.returns = self.closes.pct_change(fill_method=None)
        info = pd.read_csv(CLEAN_DIR / "stock_info.csv", dtype={"stock_id": str}).sort_values("date")
        self.names = info.drop_duplicates("stock_id", keep="last").set_index("stock_id")["stock_name"].to_dict()
        self.etfs, self.etf_corr = [], {}
        for code in self.closes.columns:
            if not re.fullmatch(r"00\d{3}A", code):
                continue
            both = self.returns[[code, "TPEx"]].dropna()
            if len(both) >= ETF_MIN_DAYS:
                self.etf_corr[code] = float(both[code].corr(both["TPEx"]))
                if self.etf_corr[code] >= ETF_MIN_CORR:
                    self.etfs.append(code)


# --------------------------------------------------------------------------- analysis


class Ctx:
    """Wide (date x stock) frames shared by every analysis."""

    def __init__(self, panel: pd.DataFrame, values: pd.Series):
        wide = lambda col: panel[col].unstack("instrument")  # noqa: E731
        self.panel = panel
        self.vwap = wide("$vwap")
        self.dates = self.vwap.index
        like = lambda col: wide(col).reindex_like(self.vwap)  # noqa: E731
        self.mask = like("$in_universe").eq(1)
        self.mv = like("$market_value").where(lambda x: x > 0)
        self.ind = like("$industry_id")
        self.a = ev._to_wide(values, self.vwap)
        meta = pd.read_csv(ALPHA_DIR / "instruments_meta.csv", dtype={"instrument": str}).set_index("instrument")
        self.name = meta["name"].to_dict()
        self.group = meta["group" if "group" in meta else "industry"].to_dict()


def bounds(dates: pd.DatetimeIndex, window: str) -> tuple[int, int]:
    lo, hi = ev.WINDOWS[window]
    i0 = int(dates.searchsorted(pd.Timestamp(lo)))
    i1 = int(dates.searchsorted(pd.Timestamp(hi), side="right")) if hi else len(dates)
    return i0, i1


def end_dated(book: pd.DataFrame, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """Row j of topk_book is VWAP(j) -> VWAP(j+1); date it j+1, the day it is earned, so it lines up
    with close-to-close benchmark returns (about half a day apart)."""
    out = book.copy()
    out.index = dates[dates.get_indexer(book.index) + 1]
    return out


def compound(r: pd.Series) -> float:
    r = r.dropna()
    return float(np.expm1(np.log1p(r).sum())) if len(r) else float("nan")


def perf(r: pd.Series) -> dict:
    """Return statistics of daily returns. Under a year, annual return is left out: annualizing
    a few strong months reads like a +150% fund."""
    r = r.dropna()
    if len(r) < 20:
        return {}
    wealth = np.exp(np.log1p(r).cumsum())
    peak = np.maximum.accumulate(np.r_[1.0, wealth.to_numpy()])[1:]
    ann = float(wealth.iloc[-1] ** (TD / len(r)) - 1) if len(r) >= TD else float("nan")
    vol = float(r.std() * np.sqrt(TD))
    return {"total": float(wealth.iloc[-1] - 1), "ann_return": ann, "ann_vol": vol,
            "ret_to_vol": ann / vol if vol > 0 else float("nan"),
            "max_dd": float((wealth / peak - 1).min()), "from": r.index[0], "to": r.index[-1], "days": len(r)}


def relative(r: pd.Series, b: pd.Series) -> dict:
    """r against b on their common days. Beta, correlation and tracking error use weekly returns,
    which absorbs the half-day gap between VWAP and close timing."""
    df = pd.concat({"r": r, "b": b}, axis=1).dropna()
    if len(df) < 20:
        return {}
    wk = np.expm1(np.log1p(df).resample("W-FRI").sum(min_count=1)).dropna()
    mo = np.expm1(np.log1p(df).resample("ME").sum(min_count=1)).dropna()
    te = float((wk["r"] - wk["b"]).std() * np.sqrt(52))
    pr, pb = perf(df["r"]), perf(df["b"])
    ann_ex = pr["ann_return"] - pb["ann_return"]
    return {"total_excess": pr["total"] - pb["total"], "ann_excess": ann_ex, "tracking_error": te,
            "ir": ann_ex / te if te > 0 else float("nan"),
            "beta": float(wk["r"].cov(wk["b"]) / wk["b"].var()), "corr": float(wk["r"].corr(wk["b"])),
            "month_win": float((mo["r"] > mo["b"]).mean()), "months": len(mo)}


def ic_summary(ic: pd.Series, h: int) -> dict:
    ic = ic.dropna()
    return {"mean": float(ic.mean()) if len(ic) else float("nan"), "t": ev.nw_tstat(ic, lags=max(h, 1)),
            "days": int(len(ic))}


def quintiles(ctx: Ctx, fwd: pd.DataFrame, usable: pd.Series, h: int, q: int = 5) -> dict:
    """Mean h-day forward return of each score quintile minus the universe mean (Q5 = highest)."""
    valid = ctx.mask & ctx.a.notna() & fwd.notna()
    bucket = np.ceil(ctx.a.where(valid).rank(axis=1, pct=True) * q).clip(1, q)
    f = fwd.where(valid)
    ex = f.sub(f.mean(axis=1), axis=0)
    rows = usable & (valid.sum(axis=1) >= ev.MIN_NAMES)
    per = pd.DataFrame({k: ex.where(bucket == k).mean(axis=1) for k in range(1, q + 1)})[rows]
    means = per.mean()
    spread = (per[q] - per[1]).dropna()
    return {"excess": [float(x) for x in means], "spread": float(spread.mean()),
            "t": ev.nw_tstat(spread, lags=max(h, 1)),
            "monotonic": float(pd.Series(means.to_numpy()).corr(pd.Series(np.arange(q)), method="spearman"))}


def industry_neutral(ctx: Ctx) -> pd.DataFrame:
    """The factor's universe rank minus its industry group's mean rank (groups of 3+ names)."""
    rank = ctx.a.where(ctx.mask).rank(axis=1, pct=True)
    df = pd.DataFrame({"v": rank.stack(future_stack=True),
                       "g": ctx.ind.where(ctx.mask).stack(future_stack=True)}).dropna()
    grp = df.groupby([df.index.get_level_values(0), df["g"]])["v"]
    out = (df["v"] - grp.transform("mean")).where(grp.transform("count") >= 3)
    return out.unstack().reindex_like(ctx.a)


def holdings(ctx: Ctx, i0: int, i1: int, k: int = 24) -> dict:
    """What the top-k book buys: industry mix and size percentile over the window (every 5th day),
    and the top k on the window's last signal day."""
    mix, size_pct = {}, []
    for j in range(i0, i1, 5):
        ok = ctx.mask.iloc[j] & ctx.a.iloc[j].notna()
        top = ctx.a.iloc[j][ok].nlargest(k).index
        size_pct.append(float(ctx.mv.iloc[j][ctx.mask.iloc[j]].rank(pct=True).reindex(top).mean()))
        for code in top:
            g = ctx.group.get(code, "?")
            mix[g] = mix.get(g, 0) + 1
    total = sum(mix.values()) or 1
    last = i1 - 1
    ok = ctx.mask.iloc[last] & ctx.a.iloc[last].notna()
    size_rank = ctx.mv.iloc[last][ctx.mask.iloc[last]].rank(ascending=False)
    top = [{"code": c, "name": ctx.name.get(c, "?"), "group": ctx.group.get(c, "?"),
            "size_rank": int(size_rank.get(c, -1))} for c in ctx.a.iloc[last][ok].nlargest(k).index]
    return {"industries": sorted(((g, n / total) for g, n in mix.items()), key=lambda kv: -kv[1]),
            "size_percentile": float(np.nanmean(size_pct)) if size_pct else float("nan"),
            "last_day": str(ctx.dates[last].date()), "top": top}


def analyze(ctx: Ctx, values: pd.Series, window: str, h: int, library: dict, styles: dict,
            bench: Bench) -> dict | None:
    """Every number of one window: the judge's metrics and gate, plus the report's extras."""
    i0, i1 = bounds(ctx.dates, window)
    if i1 - i0 < 60:
        return None
    pos = pd.Series(np.arange(len(ctx.dates)), index=ctx.dates)
    in_win = (pos >= i0) & (pos < i1)
    m = ev.evaluate(values, ctx.panel, window=window, library=library)
    rules = {**(ev.HOLDOUT_GATE if window == "holdout" else ev.DEFAULT_GATE), "horizon": h}
    passed, reasons = ev.gate(m, rules)

    fwd = ev.forward_returns(ctx.vwap, h)
    usable = in_win & (pos + 1 + h < i1)
    ic = ev.rank_corr(ctx.a, fwd, ctx.mask)
    median_mv = ctx.mv.where(ctx.mask).median(axis=1)
    small = ev.rank_corr(ctx.a, fwd, ctx.mask & ctx.mv.lt(median_mv, axis=0))[usable]
    large = ev.rank_corr(ctx.a, fwd, ctx.mask & ctx.mv.ge(median_mv, axis=0))[usable]
    up = fwd.where(ctx.mask).mean(axis=1) > 0
    robust = {
        "all": ic_summary(ic[usable], h),
        "small": ic_summary(small, h), "large": ic_summary(large, h),
        "small_minus_large_t": ev.nw_tstat((small - large).dropna(), lags=max(h, 1)),
        "industry_neutral": ic_summary(ev.rank_corr(industry_neutral(ctx), fwd, ctx.mask)[usable], h),
        "market_up": ic_summary(ic[usable & up], h), "market_down": ic_summary(ic[usable & ~up], h),
    }

    book = end_dated(ev.topk_book(ctx.a, ctx.vwap, ctx.mask, i0, i1 - 1), ctx.dates)
    bret = bench.returns.reindex(book.index)
    etf_avg = bret[bench.etfs].mean(axis=1).where(bret[bench.etfs].notna().sum(axis=1) >= 3)
    series = {"book": book["net"], "b0": book["bench"], **{c: bret[c] for c in INDEXES}, "etf_avg": etf_avg}
    rows = []
    for key, s in series.items():
        p = perf(s)
        if not p:
            continue
        vs_taiex = relative(s, bret["TAIEX"]) if key != "TAIEX" else {"beta": 1.0, "corr": 1.0}
        row = {"key": key, **p, "beta_taiex": vs_taiex.get("beta"), "corr_taiex": vs_taiex.get("corr")}
        if key != "book":
            row["book_total"] = compound(book["net"].reindex(s.dropna().index))
            row["book_vs"] = relative(book["net"], s)
        rows.append(row)
    etf_rows = []
    for code in bench.etfs:
        r = bret[code].dropna()
        if len(r) >= 20:
            etf_rows.append({"code": code, "name": bench.names.get(code, code), "from": r.index[0], "days": len(r),
                             "etf": compound(r), "book": compound(book["net"].reindex(r.index))})

    year = lambda s: np.expm1(np.log1p(s).groupby(s.index.year).sum(min_count=1))  # noqa: E731
    yearly = pd.DataFrame({"book": year(book["net"]), "b0": year(book["bench"]),
                           "0050": year(bret["0050"]), "TAIEX": year(bret["TAIEX"])})
    yearly["excess"] = yearly["book"] - yearly["b0"]
    by_year_ic = m["ic"][str(h)]["by_year"]
    yearly["ic"] = [by_year_ic.get(int(y), {}).get("mean", np.nan) for y in yearly.index]
    yearly["months"] = book.groupby(book.index.year).apply(lambda b: b.index.month.nunique())
    month = book.groupby(book.index.to_period("M"))
    monthly = month.apply(lambda b: compound(b["net"]) - compound(b["bench"]))
    roll = lambda s: np.expm1(np.log1p(s).rolling(ev.CONTEST_DAYS).sum())  # noqa: E731
    roll24 = (roll(book["net"]) - roll(book["bench"])).dropna()

    corr_styles = {k: float(ev.rank_corr(ctx.a, ev._to_wide(v, ctx.vwap), ctx.mask)[in_win].mean())
                   for k, v in styles.items()}
    return {
        "window": window, "start": m["start"], "end": m["end"], "days": int(i1 - i0), "judge": m,
        "gate": {"passed": passed, "reasons": reasons, "rules": rules}, "robust": robust,
        "quintiles": quintiles(ctx, fwd, usable, h), "book": book, "bench_rows": rows, "etf_rows": etf_rows,
        "bench_ret": bret, "perf_book": perf(book["net"]), "perf_b0": perf(book["bench"]),
        "vs_b0": relative(book["net"], book["bench"]),
        "ann_excess_2x_cost": perf(book["net"] - book["cost"])["ann_return"] - perf(book["bench"])["ann_return"],
        "yearly": yearly, "monthly": monthly, "roll24": roll24, "style_corr": corr_styles,
        "mostly": [k for k, v in corr_styles.items() if abs(v) > MOSTLY and k != "mom20_crowding"],
        "holdings": holdings(ctx, i0, i1),
    }


def check_predictions(pred: dict, r: dict, h: int) -> list[dict]:
    """The author's stage-1 predictions against the research window, by fixed rules."""
    want = {"positive": 1, "negative": -1}.get(str(pred.get("sign", "")).lower())
    ic, rb = r["judge"]["ic"], r["robust"]
    t_main = ic[str(h)]["t_nw"]
    sign_of = want or (1 if t_main >= 0 else -1)
    out = []

    def grade(t: float) -> str:
        if not np.isfinite(t):
            return "not_supported"
        return "supported" if t >= 2 else "weak" if t > 0 else "contradicted" if t <= -2 else "not_supported"

    def add(key: str, predicted, status: str, observed: str | None) -> None:
        out.append({"key": key, "predicted": predicted, "status": status, "observed": observed})

    if want:
        add("sign", pred["sign"], grade(want * t_main), f"{h} 日 IC {ic[str(h)]['mean']:+.4f}（t {t_main:+.2f}）")
    peak = pred.get("peak_horizon_days")
    if peak in ev.HORIZONS:
        # Mean IC, not t: a drift that lasts D days makes IC grow up to h = D; t falls with h anyway,
        # because overlapping h-day returns leave fewer independent observations.
        ics = {hh: sign_of * ic[str(hh)]["mean"] for hh in ev.HORIZONS}
        best = max(ics, key=lambda hh: ics[hh] if np.isfinite(ics[hh]) else -np.inf)
        gap = abs(ev.HORIZONS.index(best) - ev.HORIZONS.index(peak))
        curve = "、".join(f"{hh} 日 {ics[hh] * sign_of:+.4f}" for hh in ev.HORIZONS)
        add("peak_horizon_days", peak, "supported" if gap == 0 else "weak" if gap == 1 else "not_supported",
            f"平均 IC 最高在 {best} 日（{curve}）")
    stronger = pred.get("stronger_in")
    if stronger in ("small", "large", "none"):
        t = sign_of * rb["small_minus_large_t"]
        status = {"small": grade(t), "large": grade(-t)}.get(stronger, "supported" if abs(t) < 2 else "not_supported")
        add("stronger_in", stronger, status, f"小型半 IC {rb['small']['mean']:+.4f}、大型半 {rb['large']['mean']:+.4f}；"
            f"差的 t {rb['small_minus_large_t']:+.2f}")
    neutral = pred.get("industry_neutral")
    if neutral in ("survives", "weakens"):
        t_n = sign_of * rb["industry_neutral"]["t"]
        weakened = t_n < 0.5 * sign_of * t_main
        status = grade(t_n) if neutral == "survives" else "supported" if weakened else "not_supported"
        add("industry_neutral", neutral, status, f"產業中性 IC {rb['industry_neutral']['mean']:+.4f}"
            f"（t {rb['industry_neutral']['t']:+.2f}）；原始 t {t_main:+.2f}")
    regime = pred.get("regime")
    if regime in ("up", "down", "both"):
        up, down = rb["market_up"], rb["market_down"]
        tu, td = sign_of * up["t"], sign_of * down["t"]
        mu, md = sign_of * up["mean"], sign_of * down["mean"]
        if regime == "both":
            status = "supported" if min(tu, td) >= 2 else "weak" if min(mu, md) > 0 else "not_supported"
        else:
            hit, other, t_hit = (mu, md, tu) if regime == "up" else (md, mu, td)
            status = "supported" if hit > other and t_hit >= 2 else "weak" if hit > other else "not_supported"
        add("regime", regime, status,
            f"大盤上漲時 IC {up['mean']:+.4f}（t {up['t']:+.2f}）；下跌時 {down['mean']:+.4f}（t {down['t']:+.2f}）")
    for text in pred.get("other") or []:
        add("other", text, "manual", None)
    return out


def trials_of(f: dict) -> int:
    """Versions of the idea tried, as declared (1 when missing or unreadable)."""
    try:
        return max(int(f.get("trials") or 1), 1)
    except (TypeError, ValueError):
        return 1


def code_flags(res: dict, decl: dict, h: int) -> list[dict]:
    """Warnings the code can see; the decision model should weigh them before the story."""
    r, f = res["research"], decl.get("factor", {})
    book = r["judge"]["book"]
    out = []

    def add(code: str, detail: str) -> None:
        out.append({"code": code, "detail": detail})

    for k in r["mostly"]:
        add(f"mostly_{k}", f"與{STYLE_LABEL[k]}的平均相關 {r['style_corr'][k]:+.2f}（> {MOSTLY}），主要就是這個風格")
    crowd = r["style_corr"].get("mom20_crowding", 0.0)
    if abs(crowd) > 0.5:
        add("crowded_momentum", f"與 mom20 相關 {crowd:+.2f}：和多數隊伍的動能選股重疊")
    if not 0.8 <= book["beta_vs_b0"] <= 1.2:
        add("beta_tilt", f"組合對 B0 的 beta {book['beta_vs_b0']:.2f}：超額有一部分是大盤方向的押注")
    if book["turnover_per_rebalance"] > 0.5:
        add("high_turnover", f"每次調整換手 {book['turnover_per_rebalance']:.0%}，成本吃掉大半優勢")
    if book["ann_excess_net"] > 0 >= r["ann_excess_2x_cost"]:
        add("cost_sensitive", "成本加倍後年化超額轉負")
    lib, limit = r["judge"]["library_corr"], ev.DEFAULT_GATE["max_library_corr"]
    if lib:
        name, v = max(lib.items(), key=lambda kv: abs(kv[1]))
        if abs(v) > limit:
            add("redundant", f"與因子庫的 {name} 相關 {v:+.2f}（> {limit}）")
    trials = trials_of(f)
    hurdle = math.sqrt(2 * math.log(trials)) if trials > 1 else 0.0
    t = r["judge"]["ic"][str(h)]["t_nw"]
    if trials > 1 and abs(t) < hurdle + 1:
        add("data_mining_risk", f"試了 {trials} 個版本，純運氣的最大 |t| 約 {hurdle:.1f}；本因子 t {t:+.2f} 離它不到 1")
    if (decl.get("predictions") or {}).get("registered") != "before_results":
        add("predictions_not_preregistered", "預測不是在看結果之前寫下的，邏輯檢查只能當描述")
    if f.get("pod") not in PODS:
        add("pod_missing", "沒有標五大因子之一，公開說明書的規則下不能使用")
    if "holdout" in res and not res["holdout"]["gate"]["passed"]:
        add("holdout_not_confirmed", "保留期沒有通過確認門檻")
    return out


# --------------------------------------------------------------------------- report.json


def reason_zh(text: str) -> str:
    for pattern, repl in REASON_ZH:
        text = re.sub(pattern, repl, text)
    return text


def P(x, nd: int = 2):
    """Fraction -> percent number for the LLM version (0.0123 -> 1.23)."""
    return None if x is None or not np.isfinite(x) else round(float(x) * 100, nd)


def R(x, nd: int = 3):
    return None if x is None or not np.isfinite(x) else round(float(x), nd)


def bench_json(row: dict, names: dict) -> dict:
    out = {"name": names[row["key"]], "from": str(row["from"].date()), "total_pct": P(row["total"], 1),
           "ann_return_pct": P(row["ann_return"], 1), "ann_vol_pct": P(row["ann_vol"], 1),
           "return_to_vol": R(row["ret_to_vol"], 2), "max_drawdown_pct": P(row["max_dd"], 1),
           "beta_to_taiex": R(row["beta_taiex"], 2)}
    if row["key"] != "book":
        vs = row["book_vs"]
        out.update({"book_total_same_days_pct": P(row["book_total"], 1),
                    "book_minus_this_total_pct": P(vs.get("total_excess"), 1), "book_ir": R(vs.get("ir"), 2),
                    "book_month_win_pct": P(vs.get("month_win"), 0)})
    return out


def window_json(r: dict, h: int) -> dict:
    m, b = r["judge"], r["judge"]["book"]
    names = {"book": "因子前 24 名（扣成本）", "b0": "B0：150 檔等權（不扣成本）", **INDEXES,
             "etf_avg": "主動式 ETF 平均（台股型）"}
    etfs = r["etf_rows"]
    gaps = [e["book"] - e["etf"] for e in etfs]
    return {
        "window": [r["start"], r["end"]], "trading_days": r["days"], "coverage_pct": P(m["coverage"], 1),
        "ic": {k: {"mean": R(v["mean"], 4), "t": R(v["t_nw"], 2), "icir": R(v["icir"], 3),
                   "positive_days_pct": P(v["pos_rate"], 1)} for k, v in m["ic"].items()},
        "ic_by_year": {str(y): R(v["mean"], 4) for y, v in m["ic"][str(h)]["by_year"].items()},
        "quintiles": {"horizon_days": h, "excess_pct": [P(x, 3) for x in r["quintiles"]["excess"]],
                      "q5_minus_q1_pct": P(r["quintiles"]["spread"], 3), "t": R(r["quintiles"]["t"], 2),
                      "monotonicity": R(r["quintiles"]["monotonic"], 2)},
        "top10_24d": {"median_excess_pct": P(m["top10_24d"]["median_excess"]),
                      "hit_rate_pct": P(m["top10_24d"]["hit_rate"], 1)},
        "book": {"total_return_pct": P(r["perf_book"]["total"]),
                 "total_excess_vs_b0_pct": P(r["vs_b0"]["total_excess"]),
                 "ann_return_pct": P(r["perf_book"]["ann_return"]), "ann_vol_pct": P(r["perf_book"]["ann_vol"]),
                 "return_to_vol": R(r["perf_book"]["ret_to_vol"], 2), "max_drawdown_pct": P(r["perf_book"]["max_dd"]),
                 "ann_excess_vs_b0_pct": P(b["ann_excess_net"]) if r["days"] >= TD else None,
                 "tracking_error_pct": P(r["vs_b0"]["tracking_error"]),
                 "ir_vs_b0": R(r["vs_b0"]["ir"], 2), "beta_vs_b0": R(b["beta_vs_b0"], 2),
                 "turnover_per_rebalance_pct": P(b["turnover_per_rebalance"], 1), "ann_cost_pct": P(b["ann_cost"]),
                 "ann_excess_at_2x_cost_pct": P(r["ann_excess_2x_cost"])},
        "windows_24d": {"median_pct": P(m["windows_24d"]["median"]), "p10_pct": P(m["windows_24d"]["p10"]),
                        "p90_pct": P(m["windows_24d"]["p90"]), "win_rate_pct": P(m["windows_24d"]["win_rate"], 1)},
        "by_year": [{"year": int(y), "months": int(row["months"]), "book_pct": P(row["book"], 1),
                     "b0_pct": P(row["b0"], 1), "0050_pct": P(row["0050"], 1), "taiex_pct": P(row["TAIEX"], 1),
                     "excess_pct": P(row["excess"], 1), "ic": R(row["ic"], 4)} for y, row in r["yearly"].iterrows()],
        "benchmarks": [bench_json(row, names) for row in r["bench_rows"]],
        "active_etfs": {"compared": len(etfs), "book_beat": sum(e["book"] > e["etf"] for e in etfs),
                        "median_book_minus_etf_pct": P(float(np.median(gaps)), 1) if gaps else None,
                        "note": "每檔 ETF 從上市（或期間開始）起，跟因子組合比同一段日子的累積報酬；ETF 用含息市價，已扣管理費"},
        "style_corr": {k: R(v, 2) for k, v in r["style_corr"].items()},
        "library_corr_top": dict(sorted(((k, R(v, 2)) for k, v in m["library_corr"].items() if np.isfinite(v)),
                                        key=lambda kv: -abs(kv[1]))[:5]),
        "robustness": {k: ({"ic": R(v["mean"], 4), "t": R(v["t"], 2)} if isinstance(v, dict) else R(v, 2))
                       for k, v in r["robust"].items()},
        "holdings": {"size_percentile": R(r["holdings"]["size_percentile"], 2),
                     "industries_pct": {g: P(s, 1) for g, s in r["holdings"]["industries"][:6]}},
    }


def build_json(res: dict, decl: dict, h: int, holdout_mode: str, provenance: dict, checks: list,
               flags: list) -> dict:
    r = res["research"]
    f = decl.get("factor", {})
    trials = trials_of(f)
    out = {
        "schema": SCHEMA,
        "how_to_read": HOW_TO_READ,
        "factor": {k: f.get(k) for k in ("id", "name", "author", "pod", "one_liner", "formula", "inputs", "data_timing",
                                         "direction", "horizon_days", "trials", "declared_on", "source",
                                         "dplan_template", "source_authority")},
        "setup": {
            "universe": "每月底依指數權重（2024-10 前用市值）選上市前 100＋上櫃前 50，"
                        "point-in-time",
            "label": f"VWAP(t+1) → VWAP(t+1+h)；宣告持有期 h = {h} 日",
            "costs": "買 0.1425%；賣 0.1425% + 0.3% 證交稅",
            "data_through": provenance["data_through"],
            "holdout": holdout_mode,
        },
        "verdict_by_code": {
            "research_gate": {"passed": r["gate"]["passed"], "horizon_days": h,
                              "failed": [reason_zh(x) for x in r["gate"]["reasons"]]},
            "trials": trials, "luck_max_abs_t": R(math.sqrt(2 * math.log(trials)), 2) if trials > 1 else 0.0,
            "flags": flags,
        },
        "facts": {"research": window_json(r, h)},
        "prediction_checks": [{**c, "status_zh": STATUS_ZH[c["status"]]} for c in checks],
        "claims": {"thesis": decl.get("thesis"), "predictions": decl.get("predictions"),
                   "interpretation": decl.get("interpretation")},
        "review": decl.get("review"),
        "provenance": provenance,
    }
    if "holdout" in res:
        hg = res["holdout"]["gate"]
        failed = [code for word, code in FAILED_CHECK for reason in hg["reasons"] if word in reason]
        out["verdict_by_code"]["holdout"] = {"status": "confirmed" if hg["passed"] else "not_confirmed",
                                             "failed_checks": failed}
        if holdout_mode == "full":
            out["facts"]["holdout"] = window_json(res["holdout"], h)
        else:
            out["facts"]["holdout"] = {"withheld": "保留期數字只給人看；本檔只帶通過與否"}
    else:
        out["verdict_by_code"]["holdout"] = {"status": "not_scored"}
    return out


# --------------------------------------------------------------------------- report.html: charts


def esc(x) -> str:
    return html.escape(str(x), quote=True)


def fpct(x, nd: int = 1, sign: bool = True) -> str:
    if x is None or not np.isfinite(x):
        return "—"
    return (f"{x * 100:+.{nd}f}%" if sign else f"{x * 100:.{nd}f}%").replace("-", "−")


def fnum(x, nd: int = 2, sign: bool = False) -> str:
    if x is None or not np.isfinite(x):
        return "—"
    return (f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}").replace("-", "−")


def _ticks(lo: float, hi: float, n: int = 5) -> list[float]:
    """Round tick values that cover [lo, hi]."""
    if not (np.isfinite(lo) and np.isfinite(hi)):
        return [0.0, 1.0]
    if hi - lo < 1e-12:
        pad = abs(lo) * 0.1 or 0.01
        lo, hi = lo - pad, hi + pad
    raw = (hi - lo) / n
    mag = 10 ** math.floor(math.log10(raw))
    step = next(s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw * 0.999)
    first, last = math.floor(lo / step + 1e-9), math.ceil(hi / step - 1e-9)
    return [round(i * step, 12) for i in range(first, last + 1)]


def _tick_label(v: float, kind: str, step: float) -> str:
    if kind == "nav":
        return f"{v:.2f}" if step < 0.1 else f"{v:.1f}"
    if kind == "ic":
        return fnum(v, 3 if step < 0.01 else 2)
    if kind == "corr":
        return fnum(v, 2 if step < 0.1 else 1)
    nd = 0 if step >= 0.01 - 1e-12 else 1 if step >= 0.001 - 1e-12 else 2
    return fpct(v, nd, sign=False)


def _col_path(x: float, w: float, y0: float, y1: float, r: float = 4) -> str:
    """Column from the baseline y0 to y1, rounded only at the data end."""
    r = min(r, abs(y1 - y0), w / 2)
    k = -1 if y1 < y0 else 1
    return (f"M{x:.1f},{y0:.1f}V{y1 - k * r:.1f}Q{x:.1f},{y1:.1f} {x + r:.1f},{y1:.1f}"
            f"H{x + w - r:.1f}Q{x + w:.1f},{y1:.1f} {x + w:.1f},{y1 - k * r:.1f}V{y0:.1f}Z")


def _row_path(x0: float, x1: float, y: float, h: float, r: float = 4) -> str:
    """Bar from the baseline x0 to x1, rounded only at the data end."""
    r = min(r, abs(x1 - x0), h / 2)
    k = 1 if x1 > x0 else -1
    return (f"M{x0:.1f},{y:.1f}H{x1 - k * r:.1f}Q{x1:.1f},{y:.1f} {x1:.1f},{y + r:.1f}"
            f"V{y + h - r:.1f}Q{x1:.1f},{y + h:.1f} {x1 - k * r:.1f},{y + h:.1f}H{x0:.1f}Z")


def line_chart(cid: str, title: str, series: list[tuple[str, str, pd.Series]], kind: str, height: int = 270,
               marks: tuple = (), zero: float | None = None, area: str | None = None, width: int = 720) -> str:
    """Multi-line time chart (dates on x) with a crosshair tooltip; series = (label, color token, values)."""
    W, L, R_, T, B = width, 52, 96, 16, 26
    clean = [(n, c, s.dropna()) for n, c, s in series if s.dropna().size > 1]
    idx = pd.DatetimeIndex(sorted(set().union(*[set(s.index) for *_, s in clean])))
    t0, t1 = idx[0].value, idx[-1].value
    X = lambda d: L + (pd.Timestamp(d).value - t0) / (t1 - t0) * (W - L - R_)  # noqa: E731
    vals = np.concatenate([s.to_numpy() for *_, s in clean] + ([np.array([zero])] if zero is not None else []))
    ticks = _ticks(float(vals.min()), float(vals.max()))
    lo, hi = ticks[0], ticks[-1]
    Y = lambda v: T + (hi - v) / (hi - lo) * (height - T - B)  # noqa: E731
    step = ticks[1] - ticks[0] if len(ticks) > 1 else 1
    out = [f'<svg class="chart" viewBox="0 0 {W} {height}" role="img" aria-label="{esc(title)}" data-line="d-{cid}">']
    for t in ticks:
        out.append(f'<line class="grid" x1="{L}" x2="{W - R_}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>'
                   f'<text x="{L - 6}" y="{Y(t) + 4:.1f}" text-anchor="end">{esc(_tick_label(t, kind, step))}</text>')
    if zero is not None:
        out.append(f'<line class="base" x1="{L}" x2="{W - R_}" y1="{Y(zero):.1f}" y2="{Y(zero):.1f}"/>')
    years = [pd.Timestamp(y, 1, 1) for y in range(idx[0].year + 1, idx[-1].year + 1)]
    for d in years:
        out.append(f'<line class="grid" x1="{X(d):.1f}" x2="{X(d):.1f}" y1="{height - B}" y2="{height - B + 5}"/>'
                   f'<text x="{X(d) + 3:.1f}" y="{height - 6}">{d.year}</text>')
    if not years or X(years[0]) - L > 44:
        out.append(f'<text x="{L + 2}" y="{height - 6}">{idx[0].year}</text>')
    for d, label in marks:
        x = X(d)
        out.append(f'<line class="marker" x1="{x:.1f}" x2="{x:.1f}" y1="{T}" y2="{height - B}"/>'
                   + (f'<text class="lbl" x="{x + 4:.1f}" y="{T + 10}">{esc(label)}</text>' if label else ""))
    for n, c, s in clean:
        pts = " ".join(f"{X(d):.1f},{Y(v):.1f}" for d, v in s.items())
        if area == n:
            base = Y(zero if zero is not None else lo)
            out.append(f'<polygon class="area" style="fill:var(--{c})" points="{X(s.index[0]):.1f},{base:.1f} {pts} '
                       f'{X(s.index[-1]):.1f},{base:.1f}"/>')
        out.append(f'<polyline class="ln" style="stroke:var(--{c})" points="{pts}"/>')
    ends = sorted((Y(s.iloc[-1]), n, c, s.iloc[-1]) for n, c, s in clean)
    roomy = all(b[0] - a[0] >= 13 for a, b in zip(ends, ends[1:]))
    for y, n, c, v in ends:
        out.append(f'<circle class="dot" cx="{X(idx[-1]):.1f}" cy="{y:.1f}" r="4" style="fill:var(--{c})"/>')
        if roomy:
            text = f"{n} {fnum(v, 2) if kind == 'nav' else fpct(v, 1, kind != 'dd')}"
            out.append(f'<text class="lbl" x="{X(idx[-1]) + 8:.1f}" y="{y + 4:.1f}">{esc(text)}</text>')
    out.append(f'<line class="xh" x1="0" x2="0" y1="{T}" y2="{height - B}" visibility="hidden"/>'
               f'<rect class="hit" x="{L}" y="{T}" width="{W - L - R_}" height="{height - T - B}"/></svg>')
    data = {"f": kind, "x": [d.strftime("%Y-%m-%d") for d in idx], "px": [round(X(d), 1) for d in idx],
            "s": [{"n": n, "c": c, "v": [None if pd.isna(v) else round(float(v), 5) for v in s.reindex(idx)]}
                  for n, c, s in clean]}
    out.append(f'<script type="application/json" id="d-{cid}">{json.dumps(data, ensure_ascii=False)}</script>')
    return "".join(out)


def column_chart(title: str, cats: list[str], vals: list[float], kind: str, colors: list[str],
                 tips: list[str] | None = None, height: int = 230, width: int = 520) -> str:
    """Vertical columns from a zero baseline, value at each tip, hover/focus tooltip per column."""
    L, R_, T, B = 52, 10, 20, 30
    finite = [v for v in vals if v is not None and np.isfinite(v)] or [0.0]
    ticks = _ticks(min(0.0, min(finite)), max(0.0, max(finite)), n=4)
    lo, hi = ticks[0], ticks[-1]
    step = ticks[1] - ticks[0] if len(ticks) > 1 else 1
    Y = lambda v: T + (hi - v) / (hi - lo) * (height - T - B)  # noqa: E731
    band = (width - L - R_) / len(cats)
    bw = min(24.0, band * 0.56)
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}">']
    for t in ticks:
        out.append(f'<line class="grid" x1="{L}" x2="{width - R_}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>'
                   f'<text x="{L - 6}" y="{Y(t) + 4:.1f}" text-anchor="end">{esc(_tick_label(t, kind, step))}</text>')
    out.append(f'<line class="base" x1="{L}" x2="{width - R_}" y1="{Y(0):.1f}" y2="{Y(0):.1f}"/>')
    for i, (cat, v, col) in enumerate(zip(cats, vals, colors)):
        cx = L + band * (i + 0.5)
        out.append(f'<text x="{cx:.1f}" y="{height - 10}" text-anchor="middle">{esc(cat)}</text>')
        if v is None or not np.isfinite(v):
            continue
        label = fnum(v, 3, True) if kind == "ic" else fpct(v, 2)
        tip = f"{cat}|{label}" + (f"|{tips[i]}" if tips else "")
        out.append(f'<path class="mark" tabindex="0" data-tip="{esc(tip)}" style="fill:var(--{col})" '
                   f'd="{_col_path(cx - bw / 2, bw, Y(0), Y(v))}"/>')
        ty = Y(v) - 6 if v >= 0 else Y(v) + 14
        out.append(f'<text class="lbl" x="{cx:.1f}" y="{ty:.1f}" text-anchor="middle">{esc(label)}</text>')
    out.append("</svg>")
    return "".join(out)


def bar_chart(title: str, cats: list[str], vals: list[float], kind: str, colors: list[str],
              tips: list[str] | None = None, width: int = 520, label_w: int = 190, row: int = 26,
              strong: set | None = None) -> str:
    """Horizontal bars from a zero baseline (category labels left, value at each tip)."""
    L, R_, T, B = label_w, 64, 6, 24
    height = T + B + row * len(cats)
    finite = [v for v in vals if v is not None and np.isfinite(v)] or [0.0]
    ticks = _ticks(min(0.0, min(finite)), max(0.0, max(finite)), n=4)
    lo, hi = ticks[0], ticks[-1]
    step = ticks[1] - ticks[0] if len(ticks) > 1 else 1
    pad = 46 if lo < 0 else 0  # room for the value label of the most negative bar
    X = lambda v: L + pad + (v - lo) / (hi - lo) * (width - L - pad - R_)  # noqa: E731
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}">']
    for t in ticks:
        out.append(f'<line class="grid" x1="{X(t):.1f}" x2="{X(t):.1f}" y1="{T}" y2="{height - B}"/>'
                   f'<text x="{X(t):.1f}" y="{height - 8}" text-anchor="middle">'
                   f"{esc(_tick_label(t, kind, step))}</text>")
    out.append(f'<line class="base" x1="{X(0):.1f}" x2="{X(0):.1f}" y1="{T}" y2="{height - B}"/>')
    for i, (cat, v, col) in enumerate(zip(cats, vals, colors)):
        y = T + row * i
        cls = "lbl strong" if strong and cat in strong else "lbl"
        out.append(f'<text class="{cls}" x="{L - 8}" y="{y + row / 2 + 4:.1f}" text-anchor="end">{esc(cat)}</text>')
        if v is None or not np.isfinite(v):
            continue
        label = fnum(v, 2, True) if kind == "corr" else fpct(v, 1)
        tip = f"{cat}|{label}" + (f"|{tips[i]}" if tips else "")
        bh = min(14.0, row * 0.56)
        out.append(f'<path class="mark" tabindex="0" data-tip="{esc(tip)}" style="fill:var(--{col})" '
                   f'd="{_row_path(X(0), X(v), y + (row - bh) / 2, bh)}"/>')
        tx, anchor = (X(v) + 6, "start") if v >= 0 else (X(v) - 6, "end")
        out.append(f'<text class="lbl" x="{tx:.1f}" y="{y + row / 2 + 4:.1f}" text-anchor="{anchor}">'
                   f"{esc(label)}</text>")
    out.append("</svg>")
    return "".join(out)


def hist_chart(title: str, s: pd.Series, width: int = 520, height: int = 230) -> str:
    """Distribution of rolling 24-day excess returns, colored by sign, with median and worst-10% markers."""
    L, R_, T, B = 44, 10, 22, 26
    v = s.dropna().to_numpy()
    lo, hi = np.floor(np.quantile(v, 0.005) * 100) / 100, np.ceil(np.quantile(v, 0.995) * 100) / 100
    width_bin = max(0.005, round((hi - lo) / 24 / 0.005) * 0.005)
    edges = np.arange(lo, hi + width_bin, width_bin)
    counts, _ = np.histogram(np.clip(v, edges[0], edges[-1] - 1e-12), bins=edges)
    share = counts / counts.sum()
    X = lambda x: L + (x - edges[0]) / (edges[-1] - edges[0]) * (width - L - R_)  # noqa: E731
    yt = _ticks(0.0, float(share.max()), n=3)
    Y = lambda y: T + (yt[-1] - y) / (yt[-1] - yt[0]) * (height - T - B)  # noqa: E731
    out = [f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" aria-label="{esc(title)}">']
    for t in yt:
        out.append(f'<line class="grid" x1="{L}" x2="{width - R_}" y1="{Y(t):.1f}" y2="{Y(t):.1f}"/>'
                   f'<text x="{L - 6}" y="{Y(t) + 4:.1f}" text-anchor="end">{t * 100:.0f}%</text>')
    for a, b, c, sh in zip(edges[:-1], edges[1:], counts, share):
        if not c:
            continue
        x0, x1 = X(a) + 1, X(b) - 1
        col = "pos" if (a + b) / 2 > 0 else "neg"
        tip = f"{fpct(a, 1)} 到 {fpct(b, 1)}|{sh:.1%} 的 24 日區間"
        out.append(f'<path class="mark" data-tip="{esc(tip)}" style="fill:var(--{col})" '
                   f'd="{_col_path(x0, max(x1 - x0, 1), Y(0), Y(sh), 2)}"/>')
    tick_step = 0.05 if hi - lo > 0.2 else 0.02
    for t in np.arange(np.ceil(lo / tick_step) * tick_step, hi + 1e-9, tick_step):
        out.append(f'<text x="{X(t):.1f}" y="{height - 8}" text-anchor="middle">{esc(fpct(t, 0))}</text>')
    out.append(f'<line class="base" x1="{L}" x2="{width - R_}" y1="{Y(0):.1f}" y2="{Y(0):.1f}"/>')
    for x, label in ((float(np.median(v)), "中位數"), (float(np.quantile(v, 0.1)), "最差 10%")):
        out.append(f'<line class="marker" x1="{X(x):.1f}" x2="{X(x):.1f}" y1="{T - 6}" y2="{height - B}"/>'
                   f'<text class="lbl" x="{X(x) + 4:.1f}" y="{T}">{esc(label)} {esc(fpct(x, 1))}</text>')
    out.append("</svg>")
    return "".join(out)


def figure(cap: str, sub: str, body: str, legend: list[tuple[str, str]] = (), table: str = "",
           wide: bool = False) -> str:
    keys = "".join(f'<span><i style="background:var(--{c})"></i>{esc(n)}</span>' for n, c in legend)
    return (f'<figure class="{"wide" if wide else ""}">'
            f'<figcaption>{esc(cap)}<span class="sub">{esc(sub)}</span></figcaption>'
            + (f'<div class="legend">{keys}</div>' if legend else "") + body
            + (f'<details class="tv"><summary>表格檢視</summary>{table}</details>' if table else "")
            + "</figure>")


def table(head: list[str], rows: list[list], align: str | None = None) -> str:
    """align: one letter per column: r (right), l (left), w (left and wrapping); default l then r."""
    align = align or "l" + "r" * (len(head) - 1)
    cls = lambda i: {"l": ' class="l"', "w": ' class="w"'}.get(align[i], "")  # noqa: E731
    th = "".join(f"<th{cls(i)}>{esc(h)}</th>" for i, h in enumerate(head))
    body = "".join("<tr>" + "".join(f"<td{cls(i)}>{c if str(c).startswith('<') else esc(c)}</td>"
                                    for i, c in enumerate(r)) + "</tr>" for r in rows)
    return f'<div class="tblwrap"><table><thead><tr>{th}</tr></thead><tbody>{body}</tbody></table></div>'


def heat_table(monthly: pd.Series, year_excess: pd.Series, holdout_from: pd.Period | None) -> str:
    """Monthly excess (book minus B0) as a year x month table shaded red (up) / green (down)."""
    scale = max(0.02, float(np.nanquantile(np.abs(monthly.to_numpy()), 0.9))) if len(monthly) else 0.02
    head = "<th>年</th>" + "".join(f"<th>{m} 月</th>" for m in range(1, 13)) + "<th>全年</th>"
    rows = []
    for y in sorted({p.year for p in monthly.index}):
        cells = []
        for mth in range(1, 13):
            period = pd.Period(year=y, month=mth, freq="M")
            v = monthly.get(period, np.nan)
            if not np.isfinite(v):
                cells.append("<td></td>")
                continue
            k = min(abs(v) / scale, 1.0) * 62
            col = "pos" if v > 0 else "neg"
            ho = ' class="ho"' if holdout_from is not None and period >= holdout_from else ""
            cells.append(f'<td{ho} style="background:color-mix(in oklab, var(--{col}) {k:.0f}%, var(--surface))">'
                         f"{esc(fpct(v, 1))}</td>")
        total = esc(fpct(year_excess.get(y, np.nan), 1))
        rows.append(f'<tr><th>{y}</th>{"".join(cells)}<td class="tot">{total}</td></tr>')
    note = "；底線＝保留期的月份" if holdout_from is not None else ""
    return (f'<div class="tblwrap"><table class="heat"><thead><tr>{head}</tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
            f'<p class="notes">紅＝贏 B0、綠＝輸；顏色越深差越多{note}。全年＝組合全年報酬減 B0 全年報酬。</p>')


# --------------------------------------------------------------------------- report.html: page


CSS = """
:root {
  /* Layout: a research tear sheet: masthead with the verdict, a KPI strip, nine numbered sections, charts in pairs. */
  --bg: #f3f4f1; --surface: #fcfcfb; --ink: #15171a; --ink-2: #4a4d54; --muted: #7c7f87;
  --rule: #e1e2dc; --axis: #c3c4bc; --accent: #1f4f8f;
  --s1: #2a78d6; --s2: #eb6834; --s3: #1baf7a; --pos: #e34948; --neg: #008300;
  --good: #0ca30c; --bad: #d03b3b; --warn: #fab219;
  --good-ink: #006300; --bad-ink: #b3302f; --warn-ink: #8a6100;
  --q1: #b7d3f6; --q2: #86b6ef; --q3: #5598e7; --q4: #2a78d6; --q5: #1c5cab;
  --font-body: "Noto Sans TC", "PingFang TC", "Microsoft JhengHei", system-ui, sans-serif;
  --font-head: "Noto Serif TC", "Songti TC", "PMingLiU", serif;
  --font-mono: "IBM Plex Mono", ui-monospace, "Cascadia Mono", Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0f1012; --surface: #18191b; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #8b8d93;
    --rule: #2c2d30; --axis: #3d3e42; --accent: #8fb5e8;
    --s1: #3987e5; --s2: #d95926; --s3: #199e70; --pos: #e66767; --neg: #2f9e2f;
    --good-ink: #0ca30c; --bad-ink: #e66767; --warn-ink: #fab219;
    --q1: #184f95; --q2: #1c5cab; --q3: #2a78d6; --q4: #5598e7; --q5: #86b6ef;
    color-scheme: dark;
  }
}
:root[data-theme="dark"] {
  --bg: #0f1012; --surface: #18191b; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #8b8d93;
  --rule: #2c2d30; --axis: #3d3e42; --accent: #8fb5e8;
  --s1: #3987e5; --s2: #d95926; --s3: #199e70; --pos: #e66767; --neg: #2f9e2f;
  --good-ink: #0ca30c; --bad-ink: #e66767; --warn-ink: #fab219;
  --q1: #184f95; --q2: #1c5cab; --q3: #2a78d6; --q4: #5598e7; --q5: #86b6ef;
  color-scheme: dark;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.7 var(--font-body); }
.wrap { max-width: 1120px; margin: 0 auto; padding-block: 28px 72px;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
.eyebrow { font: 500 12px/1.5 var(--font-mono); letter-spacing: .03em; color: var(--muted); }
h1 { font: 700 clamp(28px, 4.2vw, 42px)/1.2 var(--font-head); margin: 8px 0 2px; text-wrap: balance; }
.fid { font: 13px var(--font-mono); color: var(--ink-2); }
.thesis { font-size: 17px; max-width: 68ch; margin: 14px 0 16px; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; }
.chip { display: inline-flex; align-items: center; gap: 6px; padding: 2px 11px; border-radius: 999px;
  border: 1px solid var(--rule); background: var(--surface); font-size: 13px; }
.chip b { font-weight: 700; }
.good b, .st.good { color: var(--good-ink); }
.bad b, .st.bad { color: var(--bad-ink); }
.warn b, .st.warn { color: var(--warn-ink); }
.kpis { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 1px; margin-top: 22px;
  background: var(--rule); border: 1px solid var(--rule); border-radius: 8px; overflow: hidden; }
.kpi { background: var(--surface); padding: 14px 16px 12px; min-width: 0; }
.kpi .l { font-size: 12.5px; color: var(--ink-2); }
.kpi .v { font-size: 26px; font-weight: 600; line-height: 1.25; margin-top: 4px; }
.kpi .s { font-size: 12px; color: var(--muted); }
@media (max-width: 920px) { .kpis { grid-template-columns: repeat(3, minmax(0, 1fr)); } }
@media (max-width: 520px) { .kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
section { margin-top: 48px; }
h2 { font: 700 22px/1.35 var(--font-head); margin: 0 0 6px; display: flex; gap: 12px; align-items: baseline;
  text-wrap: balance; }
h2 .n { font: 500 13px var(--font-mono); color: var(--accent); }
h3 { font-size: 15px; margin: 22px 0 8px; }
.lede { color: var(--ink-2); max-width: 74ch; margin: 0 0 18px; }
.grid2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
.grid2 > .wide { grid-column: 1 / -1; }
.stack { display: grid; gap: 18px; }
@media (max-width: 860px) { .grid2 { grid-template-columns: minmax(0, 1fr); } }
figure { margin: 0; min-width: 0; background: var(--surface); border: 1px solid var(--rule); border-radius: 8px;
  padding: 14px 16px 12px; }
figcaption { font-weight: 600; font-size: 14.5px; }
figcaption .sub { display: block; font-weight: 400; font-size: 12.5px; color: var(--muted); }
.legend { display: flex; flex-wrap: wrap; gap: 16px; font-size: 12.5px; color: var(--ink-2); margin: 8px 0 2px; }
.legend i { display: inline-block; width: 16px; height: 3px; border-radius: 2px; vertical-align: middle;
  margin-right: 6px; }
svg.chart { display: block; width: 100%; height: auto; margin-top: 6px; overflow: visible; }
svg text { fill: var(--muted); font: 11px var(--font-body); font-variant-numeric: tabular-nums; }
svg text.lbl { fill: var(--ink-2); } svg text.strong { fill: var(--ink); font-weight: 700; }
svg .grid { stroke: var(--rule); stroke-width: 1; } svg .base { stroke: var(--axis); stroke-width: 1; }
svg .marker { stroke: var(--ink-2); stroke-width: 1; } svg .xh { stroke: var(--ink-2); stroke-width: 1; }
svg .ln { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round; }
svg .area { opacity: .1; } svg .dot { stroke: var(--surface); stroke-width: 2; }
svg .hit { fill: transparent; } svg .mark { cursor: default; }
svg .mark:hover, svg .mark:focus { opacity: .78; outline: none; }
details.tv { margin-top: 8px; font-size: 12.5px; } details.tv summary { cursor: pointer; color: var(--accent); }
details.tv summary:focus-visible, .mark:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.tblwrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 13px; font-variant-numeric: tabular-nums; }
th, td { padding: 6px 9px; border-bottom: 1px solid var(--rule); text-align: right; white-space: nowrap; }
th:first-child, td:first-child, th.l, td.l, th.w, td.w { text-align: left; }
td.w { white-space: normal; min-width: 12em; }
thead th { color: var(--ink-2); font-weight: 600; border-bottom-color: var(--axis); }
table.heat td.ho { box-shadow: inset 0 -2px 0 var(--ink-2); }
table.heat td { text-align: center; min-width: 56px; } table.heat tbody th { text-align: left; }
table.heat .tot { font-weight: 600; }
.box { background: var(--surface); border: 1px solid var(--rule); border-radius: 8px; padding: 16px 18px;
  min-width: 0; }
dl.def { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 8px 20px; margin: 0; }
dl.def dt { color: var(--ink-2); } dl.def dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
@media (max-width: 560px) {
  dl.def { grid-template-columns: minmax(0, 1fr); gap: 2px; } dl.def dd { margin-bottom: 10px; }
}
code, pre { font-family: var(--font-mono); font-size: 13px; }
pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.missing { color: var(--muted); font-style: italic; }
ul.tight { margin: 0; padding-left: 1.2em; } ul.tight li { margin: 2px 0; }
.notes { font-size: 12.5px; color: var(--muted); margin-top: 10px; max-width: 90ch; }
#tip { position: fixed; z-index: 20; pointer-events: none; min-width: 120px; max-width: 300px; padding: 8px 10px;
  background: var(--surface); border: 1px solid var(--rule); border-radius: 6px;
  box-shadow: 0 6px 18px rgba(0,0,0,.14); font-size: 12.5px; }
#tip .h { color: var(--muted); } #tip .r { display: flex; align-items: center; gap: 8px; }
#tip .r i { width: 12px; height: 3px; border-radius: 2px; flex: none; } #tip b { color: var(--ink); font-size: 13.5px; }
#tip span { color: var(--ink-2); }
"""

JS = """
(() => {
  const tip = document.getElementById('tip');
  const F = {
    nav: v => v.toFixed(3),
    pct: v => (v >= 0 ? '+' : '\\u2212') + Math.abs(v * 100).toFixed(1) + '%',
    dd: v => (v < 0 ? '\\u2212' : '') + Math.abs(v * 100).toFixed(1) + '%',
  };
  function place(x, y) {
    const r = tip.getBoundingClientRect();
    let left = x + 14, top = y + 14;
    if (left + r.width > innerWidth - 8) left = x - r.width - 14;
    if (top + r.height > innerHeight - 8) top = y - r.height - 14;
    tip.style.left = Math.max(8, left) + 'px'; tip.style.top = Math.max(8, top) + 'px';
  }
  function show(head, rows) {
    tip.replaceChildren();
    if (head) { const h = document.createElement('div'); h.className = 'h'; h.textContent = head; tip.append(h); }
    for (const [name, value, color] of rows) {
      const r = document.createElement('div'); r.className = 'r';
      if (color) { const k = document.createElement('i'); k.style.background = 'var(--' + color + ')'; r.append(k); }
      const b = document.createElement('b'); b.textContent = value;
      const s = document.createElement('span'); s.textContent = name;
      r.append(b, s); tip.append(r);
    }
    tip.hidden = false;
  }
  const hide = () => { tip.hidden = true; };
  document.querySelectorAll('[data-tip]').forEach(el => {
    const [name, value, extra] = el.dataset.tip.split('|');
    const on = e => {
      show(extra || '', [[name, value]]);
      const r = el.getBoundingClientRect();
      place(e.clientX ?? r.right, e.clientY ?? r.top);
    };
    el.addEventListener('pointerenter', on);
    el.addEventListener('pointermove', e => place(e.clientX, e.clientY));
    el.addEventListener('pointerleave', hide);
    el.addEventListener('focus', on); el.addEventListener('blur', hide);
  });
  document.querySelectorAll('svg[data-line]').forEach(svg => {
    const d = JSON.parse(document.getElementById(svg.dataset.line).textContent);
    const hit = svg.querySelector('.hit'), xh = svg.querySelector('.xh'), f = F[d.f];
    const move = e => {
      const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(svg.getScreenCTM().inverse());
      let lo = 0, hi = d.px.length - 1;
      while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (d.px[mid] < p.x) lo = mid; else hi = mid; }
      const i = Math.abs(d.px[lo] - p.x) <= Math.abs(d.px[hi] - p.x) ? lo : hi;
      xh.setAttribute('x1', d.px[i]); xh.setAttribute('x2', d.px[i]); xh.setAttribute('visibility', 'visible');
      show(d.x[i], d.s.filter(s => s.v[i] !== null).map(s => [s.n, f(s.v[i]), s.c]));
      place(e.clientX, e.clientY);
    };
    hit.addEventListener('pointermove', move);
    hit.addEventListener('pointerleave', () => { xh.setAttribute('visibility', 'hidden'); hide(); });
  });
})();
"""

FONTS = ("https://fonts.googleapis.com/css2?family=Noto+Sans+TC:wght@400;500;700"
         "&family=Noto+Serif+TC:wght@600;700&family=IBM+Plex+Mono:wght@400;500&display=swap")


def _text(x, empty: str = "作者尚未填寫") -> str:
    """Author text, or a visible gap so a missing claim is never mistaken for a weak one."""
    if x is None or (isinstance(x, (str, list, dict)) and not x):
        return f'<span class="missing">{esc(empty)}</span>'
    if isinstance(x, list):
        return '<ul class="tight">' + "".join(f"<li>{esc(i)}</li>" for i in x) + "</ul>"
    if isinstance(x, dict):
        return '<ul class="tight">' + "".join(f"<li>{esc(k)}：{esc(v)}</li>" for k, v in x.items()) + "</ul>"
    return esc(x)


def _defs(items: list[tuple[str, str]]) -> str:
    return '<dl class="def">' + "".join(f"<dt>{esc(k)}</dt><dd>{v}</dd>" for k, v in items) + "</dl>"


def _chip(kind: str, icon: str, text: str) -> str:
    return f'<span class="chip {kind}"><b aria-hidden="true">{icon}</b>{esc(text)}</span>'


def _kpi(label: str, value: str, sub: str) -> str:
    return (f'<div class="kpi"><div class="l">{esc(label)}</div><div class="v">{esc(value)}</div>'
            f'<div class="s">{esc(sub)}</div></div>')


def build_html(res: dict, decl: dict, h: int, holdout_mode: str, checks: list, flags: list, prov: dict) -> str:
    f, th, pred = decl.get("factor", {}), decl.get("thesis") or {}, decl.get("predictions") or {}
    it, rv = decl.get("interpretation") or {}, decl.get("review") or {}
    r = res["research"]
    m, bk = r["judge"], r["judge"]["book"]
    ho = res.get("holdout")
    name = f.get("name") or f.get("id")
    wins = [w for w in ("research", "holdout") if w in res]

    # masthead
    gate_chip = (_chip("good", "✓", f"通過研究期門檻（{h} 日）") if r["gate"]["passed"]
                 else _chip("bad", "✗", f"未通過研究期門檻（{h} 日）"))
    if ho:
        hold_chip = _chip("good", "✓", "保留期確認") if ho["gate"]["passed"] else _chip("bad", "✗", "保留期未確認")
    else:
        hold_chip = _chip("", "—", "保留期未評分")
    role = it.get("role")
    chips = [_chip("", "◆", f.get("pod") or "未標五大因子"), gate_chip, hold_chip,
             _chip("", "→", f"作者建議：{ROLE_ZH.get(role, role)}") if role else _chip("warn", "!", "作者尚未下結論"),
             _chip("good", "✓", "預測於看結果前登錄") if pred.get("registered") == "before_results"
             else _chip("warn", "!", "預測未事前登錄")]
    icm, w24, pb = m["ic"][str(h)], m["windows_24d"], r["perf_book"]
    kpis = [
        _kpi(f"{h} 日 IC t 值", fnum(icm["t_nw"], 2),
             f"IC {fnum(icm['mean'], 4, True)} · 門檻 {ev.DEFAULT_GATE['min_t_nw']:.1f}"),
        _kpi("24 日超額中位數", fpct(w24["median"], 2),
             f"勝率 {fpct(w24['win_rate'], 0, False)} · 最差 10% {fpct(w24['p10'], 1)}"),
        _kpi("年化超額（對 B0）", fpct(bk["ann_excess_net"], 1), f"扣成本；IR {fnum(r['vs_b0']['ir'], 2)}"),
        _kpi("報酬／波動", fnum(pb["ret_to_vol"], 2),
             f"年化 {fpct(pb['ann_return'], 1)} · 波動 {fpct(pb['ann_vol'], 1, False)}"),
        _kpi("最大回撤", fpct(pb["max_dd"], 1), f"B0 {fpct(r['perf_b0']['max_dd'], 1)}"),
        _kpi("Beta（對 B0）", fnum(bk["beta_vs_b0"], 2), f"每次調整換手 {fpct(bk['turnover_per_rebalance'], 0, False)}"),
    ]
    period = f"研究期 {r['start']} → {r['end']}" + (f" · 保留期 {ho['start']} → {ho['end']}" if ho else "")
    head = (f'<header><div class="eyebrow">因子報告 · {SCHEMA} · 資料至 {esc(prov["data_through"])} · {esc(period)}</div>'
            f"<h1>{esc(name)}</h1><div class=\"fid\">{esc(f.get('id'))}"
            + (f" · {esc(f.get('author'))}" if f.get("author") else "") + "</div>"
            f'<p class="thesis">{_text(f.get("one_liner"), "作者尚未寫一句話主張")}</p>'
            f'<div class="chips">{"".join(chips)}</div><div class="kpis">{"".join(kpis)}</div>'
            '<p class="notes">KPI 皆為研究期；book＝每天選分數最高的 24 檔等權、隔日 VWAP 成交、每 5 日調整、'
            "前 36 名內續抱、扣實際成本；B0＝當月 150 檔等權。</p></header>")

    # 1 definition
    s1 = _defs([
        ("代號／名稱", esc(f"{f.get('id')} · {name}")),
        ("五大因子", _text(f.get("pod"), "未標")),
        ("公式", f"<pre>{esc(f.get('formula'))}</pre>" if f.get("formula") else _text(None)),
        ("輸入欄位", _text(f.get("inputs"))),
        ("資料時點", _text(f.get("data_timing"))),
        ("方向", _text(f.get("direction"), "高分＝買進（預設）")),
        ("宣告持有期", esc(f"{h} 日")),
        ("程式來源", f"<code>{esc(f.get('source'))}</code>"),
        ("試過幾個版本", esc(trials_of(f))),
        ("D-Plan 句型", _text(f.get("dplan_template"), "未填（匯出到比賽 repo 前要填）")),
        ("資料來源機構", _text(f.get("source_authority"), "未填")),
    ])
    s1 += ('<p class="notes">比賽規則下的設定：股票池每月底依指數權重選上市前 100＋上櫃前 50（point-in-time）；'
           "訊號在 t 日收盤後算好，t+1 日以 VWAP 成交；買 0.1425%、賣 0.1425%＋0.3% 證交稅。</p>")

    # 2 economic logic
    mech = th.get("mechanism_type")
    s2 = _defs([
        ("機制類型", esc(MECH_ZH.get(mech, mech)) if mech else _text(None)),
        ("想衡量的概念", _text(th.get("concept"))),
        ("機制", _text(th.get("story"))),
        ("誰在另一邊", _text(th.get("counterparty"))),
        ("為何沒被套利掉", _text(th.get("why_persists"))),
        ("台股特有的條件", _text(th.get("taiwan_specifics"), "未填")),
        ("文獻或前例", _text(th.get("references"), "未填")),
    ])
    look = {"supported": ("good", "✓"), "contradicted": ("bad", "✗"), "not_supported": ("warn", "✗"),
            "weak": ("warn", "~"), "manual": ("warn", "~")}
    rows = [[k["key"], esc(k["predicted"]), esc(k["observed"] or "—"),
             f'<span class="st {look[k["status"]][0]}">{look[k["status"]][1]} {esc(STATUS_ZH[k["status"]])}</span>']
            for k in checks]
    s2 += "<h3>事前預測與程式檢查（研究期）</h3>" + (
        table(["預測", "作者預測", "研究期實際", "判定"], rows, "lwwl") if rows
        else '<p class="missing">宣告檔沒有 predictions，邏輯無從檢查。</p>')
    s2 += ('<p class="notes">判定規則固定：支持＝方向對且 |t| ≥ 2；方向對、不顯著＝0 < t < 2；相反＝反向且 |t| ≥ 2。'
           "最強持有期看平均 IC 在哪個持有期最高（相鄰一格算方向對）。"
           "小型／大型以當天股票池市值中位數切；產業中性＝分數減同族群平均；大盤上漲＝股票池同期平均報酬 > 0。"
           "預測若在看過結果後才寫，這張表只能當描述。</p>")

    # 3 predictive power
    ic_cats = [f"{hh} 日" for hh in ev.HORIZONS]
    ic_vals = [m["ic"][str(hh)]["mean"] for hh in ev.HORIZONS]
    ic_tips = [f"t {fnum(m['ic'][str(hh)]['t_nw'], 2)} · ICIR {fnum(m['ic'][str(hh)]['icir'], 3)}"
               for hh in ev.HORIZONS]
    ic_table = table(["持有期", "平均 IC", "ICIR", "t（NW）", "IC > 0 的比例", "天數"],
                     [[f"{hh} 日", fnum(m["ic"][str(hh)]["mean"], 4, True), fnum(m["ic"][str(hh)]["icir"], 3),
                       fnum(m["ic"][str(hh)]["t_nw"], 2), fpct(m["ic"][str(hh)]["pos_rate"], 1, False),
                       m["ic"][str(hh)]["days"]] for hh in ev.HORIZONS])
    by_year = m["ic"][str(h)]["by_year"]
    yr_cats = [str(y) for y in by_year]
    yr_vals = [v["mean"] for v in by_year.values()]
    q = r["quintiles"]
    s3 = '<div class="grid2">' + figure(
        "IC 衰減", "研究期；各持有期的平均 RankIC，滑過看 t 值（數字見右下表）",
        column_chart("IC 衰減", ic_cats, ic_vals, "ic", ["s1"] * 4, ic_tips))
    s3 += figure(f"逐年 IC（{h} 日）", "研究期；紅＝正、綠＝負（台股慣例）",
                 column_chart("逐年 IC", yr_cats, yr_vals, "ic", ["pos" if (v or 0) > 0 else "neg" for v in yr_vals],
                              [f"{by_year[int(y)]['days']} 天" for y in yr_cats]),
                 table=table(["年", "平均 IC", "天數"],
                             [[y, fnum(v["mean"], 4, True), v["days"]] for y, v in by_year.items()]))
    s3 += figure(f"五分位 {h} 日超額報酬", f"研究期；分數由低（Q1）到高（Q5），減去股票池平均；Q5−Q1 {fpct(q['spread'], 2)}（t {fnum(q['t'], 2)}）",
                 column_chart("五分位", [f"Q{i}" for i in range(1, 6)], q["excess"], "pct",
                              [f"q{i}" for i in range(1, 6)]),
                 table=table(["分位", f"{h} 日超額"], [[f"Q{i + 1}", fpct(v, 3)] for i, v in enumerate(q["excess"])]))
    t10 = m["top10_24d"]
    s3 += ('<div class="box"><h3 style="margin-top:0">各持有期的 IC（研究期）</h3>'
           f'{ic_table}<h3>其他</h3>' + _defs([
               ("前 10 檔 24 日超額", esc(f"中位數 {fpct(t10['median_excess'], 2)}；"
                                         f"跑贏股票池中位數的比例 {fpct(t10['hit_rate'], 1, False)}")),
               ("五分位單調性", esc(f"{fnum(q['monotonic'], 2)}（1＝由低到高完全遞增）")),
               ("覆蓋率", esc(f"{fpct(m['coverage'], 1, False)}（股票池中有分數的比例）")),
           ]) + "</div></div>")

    # 4 backtest
    books = pd.concat([res[w]["book"] for w in wins])
    brets = pd.concat([res[w]["bench_ret"] for w in wins])
    nav = lambda s: np.exp(np.log1p(s.dropna()).cumsum())  # noqa: E731
    marks = [(pd.Timestamp(ho["start"]), "保留期 →")] if ho else []
    eq = line_chart("eq", "淨值走勢", [("因子 24 檔", "s1", nav(books["net"])), ("B0 150 等權", "s2", nav(books["bench"])),
                                    ("0050 含息", "s3", nav(brets["0050"]))], "nav", marks=marks, zero=1.0)
    dd = lambda s: nav(s) / np.maximum.accumulate(nav(s)) - 1  # noqa: E731
    ddc = line_chart("dd", "回撤", [("因子 24 檔", "s1", dd(books["net"])), ("B0", "s2", dd(books["bench"]))], "dd",
                     height=300, marks=[(d, "") for d, _ in marks], zero=0.0, area="因子 24 檔", width=520)
    rows = []
    for w in wins:
        for y, row in res[w]["yearly"].iterrows():
            label = f"{y}（{int(row['months'])} 個月）" if row["months"] < 12 else str(y)
            rows.append([f"{WINDOW_ZH[w]} {label}", fpct(row["book"], 1), fpct(row["b0"], 1), fpct(row["0050"], 1),
                         fpct(row["TAIEX"], 1), fpct(row["excess"], 1), fnum(row["ic"], 4, True)])
    year_table = table(["年", "因子 24 檔", "B0", "0050 含息", "加權（不含息）", "超額（對 B0）", f"IC（{h} 日）"], rows)
    roll = pd.concat([res[w]["roll24"] for w in wins])
    cost_rows = [[WINDOW_ZH[w], fpct(res[w]["judge"]["book"]["turnover_per_rebalance"], 0, False),
                  fpct(res[w]["judge"]["book"]["ann_cost"], 2, False), fpct(res[w]["vs_b0"]["total_excess"], 1),
                  fpct(res[w]["judge"]["book"]["ann_excess_net"] if res[w]["days"] >= TD else None, 1),
                  fpct(res[w]["ann_excess_2x_cost"], 1)] for w in wins]
    s4 = ('<div class="grid2">'
          + figure("淨值走勢", "起點 1.0；因子組合扣成本，B0 不扣成本；滑過看每天的值", eq,
                   [("因子 24 檔", "s1"), ("B0 150 等權", "s2"), ("0050 含息", "s3")], table=year_table, wide=True)
          + figure("回撤", "相對前高的跌幅", ddc, [("因子 24 檔", "s1"), ("B0", "s2")])
          + figure("24 日超額分布", "任一段 24 個交易日（比賽長度），組合減 B0；紅＝贏、綠＝輸", hist_chart("24 日超額", roll),
                   table=table(["", "中位數", "最差 10%", "最好 10%", "勝率"],
                               [[WINDOW_ZH[w], *(fpct(res[w]["judge"]["windows_24d"][k], 2)
                                                 for k in ("median", "p10", "p90")),
                                 fpct(res[w]["judge"]["windows_24d"]["win_rate"], 0, False)] for w in wins]))
          + "</div>")
    year = lambda s: np.expm1(np.log1p(s).groupby(s.index.year).sum(min_count=1))  # noqa: E731
    s4 += "<h3>每月超額（組合減 B0）</h3>" + heat_table(pd.concat([res[w]["monthly"] for w in wins]),
                                                year(books["net"]) - year(books["bench"]),
                                                pd.Period(ho["start"], "M") if ho else None)
    s4 += "<h3>逐年</h3>" + year_table
    s4 += "<h3>成本</h3>" + table(["期間", "每次調整換手", "年化成本", "累積超額", "年化超額", "成本加倍後年化超額"], cost_rows)
    s4 += '<p class="notes">不滿一年的期間不年化。</p>'

    # 5 benchmarks
    names = {"book": "因子 24 檔（扣成本）", "b0": "B0 150 檔等權", **INDEXES, "etf_avg": "主動式 ETF 平均（台股型）"}
    s5 = ""
    for w in wins:
        rows = []
        for row in res[w]["bench_rows"]:
            vs = row.get("book_vs") or {}
            rows.append([names[row["key"]], str(row["from"].date()), fpct(row["total"], 1), fpct(row["ann_return"], 1),
                         fpct(row["ann_vol"], 1, False), fnum(row["ret_to_vol"], 2), fpct(row["max_dd"], 1),
                         fnum(row["beta_taiex"], 2), "—" if row["key"] == "book" else fpct(row["book_total"], 1),
                         "—" if row["key"] == "book" else fpct(vs.get("total_excess"), 1),
                         "—" if row["key"] == "book" else fpct(vs.get("month_win"), 0, False)])
        s5 += (f"<h3>{WINDOW_ZH[w]}：{res[w]['start']} → {res[w]['end']}</h3>"
               + table(["", "起", "累積", "年化", "波動（年化）", "報酬／波動", "最大回撤", "對加權 β",
                        "因子同期累積", "因子多賺（累積）", "因子月勝率"], rows, "llrrrrrrrrr"))
    etf_all = [(w, e) for w in wins for e in res[w]["etf_rows"]]
    if etf_all:
        w_last = wins[-1]
        etfs = sorted(res[w_last]["etf_rows"], key=lambda e: e["book"] - e["etf"])
        cats = [f"{e['code']} {e['name'].replace('主動', '')}" for e in etfs]
        diffs = [e["book"] - e["etf"] for e in etfs]
        tips = [f"ETF {fpct(e['etf'], 1)} · 因子 {fpct(e['book'], 1)} · 自 {e['from'].date()}" for e in etfs]
        beat = sum(d > 0 for d in diffs)
        s5 += '<div class="grid2">' + figure(
            "因子組合減主動式 ETF", f"{WINDOW_ZH[w_last]}；每檔從上市（或期間開始）起比同一段日子的累積報酬；因子贏 {beat}／{len(etfs)} 檔",
            bar_chart("因子減主動式 ETF", cats, diffs, "pct", ["pos" if d > 0 else "neg" for d in diffs], tips,
                      width=1000, label_w=230, row=24),
            table=table(["ETF", "起", "交易日", "ETF 累積", "因子同期", "差"],
                        [[f"{e['code']} {e['name']}", str(e["from"].date()), e["days"], fpct(e["etf"], 1),
                          fpct(e["book"], 1), fpct(e["book"] - e["etf"], 1)]
                         for e in sorted(etfs, key=lambda e: e["from"])], "llrrrr"),
            wide=True) + "</div>"
    s5 += ('<p class="notes">0050 與主動式 ETF 用 FinMind 還原（含息）收盤價，已扣管理費；加權與櫃買是價格指數，不含股息（台股每年約少 3%）。'
           "因子組合扣交易成本、不扣管理費，以 VWAP 計價，跟收盤價約差半天，所以 β 與追蹤誤差用週報酬算。"
           f"主動式 ETF 只收投資台股的：上市滿 {ETF_MIN_DAYS} 個交易日、和櫃買指數日報酬相關 ≥ {ETF_MIN_CORR}"
           "（投資美國或全球的都在 0.81 以下）。主動式 ETF 2025 年 5 月起才陸續上市，樣本短，只能當參考。</p>")

    # 6 what it is
    sc = r["style_corr"]
    cats = [STYLE_LABEL[k] for k in sc]
    vals = list(sc.values())
    ho_sc = ho["style_corr"] if ho else {}
    style_table = table(["風格", "研究期"] + (["保留期"] if ho else []),
                        [[STYLE_LABEL[k], fnum(v, 2, True)] + ([fnum(ho_sc.get(k), 2, True)] if ho else [])
                         for k, v in sc.items()])
    s6 = '<div class="grid2">' + figure(
        "跟常見風格的相關", f"研究期每天的排名相關取平均；|相關| > {MOSTLY} 代表它主要就是那個風格",
        bar_chart("風格相關", cats, vals, "corr", ["s1"] * len(vals), label_w=210), table=style_table)
    lib = sorted(((k, v) for k, v in m["library_corr"].items() if np.isfinite(v)), key=lambda kv: -abs(kv[1]))[:8]
    hold = r["holdings"]
    s6 += ('<div class="box"><h3 style="margin-top:0">跟因子庫的相關（研究期）</h3>'
           + (table(["卡片", "相關"], [[k, fnum(v, 2, True)] for k, v in lib]) if lib else '<p class="missing">因子庫是空的。</p>')
           + f'<p class="notes">門檻 {ev.DEFAULT_GATE["max_library_corr"]}：超過代表沒有新資訊。</p></div></div>')
    ind_rows = [[g, fpct(s, 1, False)] for g, s in hold["industries"][:8]]
    top_rows = [[t["code"], t["name"], t["group"], t["size_rank"]] for t in hold["top"]]
    s6 += ('<div class="grid2"><div class="box"><h3 style="margin-top:0">它買什麼（研究期，每 5 天取前 24 名）</h3>'
           + _defs([("平均市值百分位", esc(f"{fnum(hold['size_percentile'], 2)}（1＝最大）"))])
           + table(["族群", "持股占比"], ind_rows) + "</div>"
           + f'<div class="box"><h3 style="margin-top:0">{esc(hold["last_day"])} 的前 24 名</h3>'
           + table(["代號", "名稱", "族群", "市值排名"], top_rows, "lllr") + "</div></div>")

    # 7 robustness
    rob_rows = []
    labels = [("all", "全部"), ("small", "小型半（市值低於中位數）"), ("large", "大型半"), ("industry_neutral", "產業中性"),
              ("market_up", "大盤上漲時"), ("market_down", "大盤下跌時")]
    for key, label in labels:
        row = [label]
        for w in wins:
            v = res[w]["robust"][key]
            row += [fnum(v["mean"], 4, True), fnum(v["t"], 2)]
        rob_rows.append(row)
    head7 = ["切法"] + [x for w in wins for x in (f"{WINDOW_ZH[w]} IC", "t")]
    trials = trials_of(f)
    luck = math.sqrt(2 * math.log(trials)) if trials > 1 else 0.0
    gate = ev.DEFAULT_GATE
    gate_rows = [[WINDOW_ZH[w], "✓ 通過" if res[w]["gate"]["passed"] else "✗ 未通過",
                  "；".join(reason_zh(x) for x in res[w]["gate"]["reasons"]) or "—"] for w in wins]
    s7 = (f'{table(head7, rob_rows)}'
          f'<p class="notes">{h} 日 IC。小型半減大型半的 t（研究期）：{fnum(r["robust"]["small_minus_large_t"], 2)}。</p>'
          f'<h3>門檻</h3>{table(["期間", "結果", "沒過的項目"], gate_rows, "llw")}'
          f'<p class="notes">研究期門檻（{h} 日）：t ≥ {gate["min_t_nw"]}、'
          f'至少 {gate["min_years_positive"]} 個完整年度 IC 為正、24 日超額中位數 > 0、'
          f'與因子庫相關 ≤ {gate["max_library_corr"]}、覆蓋率 ≥ {gate["min_coverage"]:.0%}。'
          f"保留期只確認：t ≥ {ev.HOLDOUT_GATE['min_t_nw']}、同方向、24 日中位數 > 0。"
          f"這個想法試過 {trials} 個版本；{trials} 次嘗試下，純運氣的最大 |t| 約 {luck:.1f}（√(2 ln N)）。</p>")

    # 8 interpretation, risks, review
    items = "".join(f"<li><code>{esc(x['code'])}</code> {esc(x['detail'])}</li>" for x in flags)
    flag_list = f'<ul class="tight">{items}</ul>' if flags else '<p class="notes">程式沒有發現警訊。</p>'
    s8 = '<div class="stack"><div class="box">' + _defs([
        ("結論", _text(it.get("summary"))),
        ("公式實際衡量的", _text(it.get("concept_vs_measure"))),
        ("預測的解讀", _text(it.get("prediction_notes"))),
        ("其他可能的解釋", _text(it.get("alternatives"))),
        ("風險與失效情境", _text(it.get("risks"))),
        ("建議角色", esc(ROLE_ZH.get(role, role)) if role else _text(None)),
        ("權重上限", _text(it.get("weight_cap_pct") and f"{it['weight_cap_pct']}%", "未填")),
        ("適合搭配", _text(it.get("pairs_with"), "未填")),
        ("停用條件", _text(it.get("kill_switch"))),
        ("待驗證", _text(it.get("open_questions"), "未填")),
        ("事後假說", _text(it.get("post_hoc"), "無")),
    ]) + "</div>"
    s8 += ('<div class="grid2"><div class="box"><h3 style="margin-top:0">程式看到的警訊</h3>' + flag_list + "</div>"
           + '<div class="box"><h3 style="margin-top:0">獨立審查（A–F）</h3>' + (_defs([
               ("審查者", _text(rv.get("reviewer"))), ("日期", _text(rv.get("date"), "—")),
               ("判斷", _text(rv.get("judgment"))), ("摘要", _text(rv.get("summary"))),
               ("最有力的反例", _text(rv.get("counterexample"))), ("建議修正", _text(rv.get("fix"))),
           ]) if rv else '<p class="missing">尚未審查（tw_data/alpha_review_prompt_zh.md，用跟作者不同的模型）。</p>')
           + "</div></div></div>")

    # 9 provenance
    s9 = _defs([(k, f"<code>{esc(v)}</code>") for k, v in (
        ("產生時間", prov["generated_at"]), ("程式版本", prov["git_commit"]), ("面板建立", prov["panel_built_at"]),
        ("資料截至", prov["data_through"]), ("保留期模式", holdout_mode), ("指令", prov["command"]), ("報告格式", SCHEMA))])

    sections = [("definition", "1", "定義與資料時點", "", s1),
                ("logic", "2", "經濟邏輯", "回測賺錢不代表機制成立。這裡看作者在看結果前寫下的機制與預測，再由程式逐條核對。", s2),
                ("power", "3", "預測能力", "分數高的股票之後是否真的比較好，以及效果在多長的持有期最強。", s3),
                ("backtest", "4", "回測：比賽規則下的前 24 名", "隔日 VWAP 成交、扣實際成本、每 5 日調整；跟同一批 150 檔的等權（B0）比。", s4),
                ("benchmarks", "5", "同期比較：大盤與主動式 ETF", "同一段日子，跟 0050、加權指數、櫃買指數與投資台股的主動式 ETF 比。", s5),
                ("anatomy", "6", "它其實是什麼", "分數高是因為選股能力，還是只是偏向某種風格、某些產業，或跟已有的因子重複。", s6),
                ("robustness", "7", "穩健性", "換個切法還在不在：大小型、產業內、多空頭、各年度。", s7),
                ("verdict", "8", "解讀、風險與使用建議", "作者看過結果後的解讀（第二階段）、程式警訊與獨立審查。", s8),
                ("provenance", "9", "可重現性", "", s9)]
    body = "".join(f'<section id="{sid}"><h2><span class="n">§{n}</span>{esc(t)}</h2>'
                   + (f'<p class="lede">{esc(lede)}</p>' if lede else "") + content + "</section>"
                   for sid, n, t, lede, content in sections)
    return ('<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
            f"<title>{esc(name)} 因子報告</title>"
            '<meta name="claude:viewport-fit-sides" content="cover">'
            '<link rel="preconnect" href="https://fonts.googleapis.com">'
            '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            f'<link rel="stylesheet" href="{FONTS}"><style>{CSS}</style></head>'
            f'<body><div class="wrap">{head}<main>{body}</main></div><div id="tip" role="tooltip" hidden></div>'
            f"<script>{JS}</script></body></html>")


# --------------------------------------------------------------------------- declaration and prompts


def declaration_template(known: dict, h: int) -> str:
    """The stage-1 YAML block from factor_declare_prompt_zh.md, with what the source already knows."""
    text = DECLARE_PROMPT.read_text(encoding="utf-8")
    block = re.search(r"```yaml\n(.*?)```", text, re.S).group(1)
    fill = {"id": known.get("id"), "name": known.get("name"), "pod": known.get("pod"),
            "formula": known.get("formula"), "one_liner": known.get("one_liner"), "horizon_days": h}
    for key, value in fill.items():
        if value is None:
            continue
        if isinstance(value, str):
            value = " ".join(value.split())  # one line, so the template keeps its indentation
        dumped = yaml.safe_dump({key: value}, allow_unicode=True, width=1000).strip()
        block = re.sub(rf"^  {key}:.*$", "  " + dumped.replace("\\", "\\\\"), block, count=1, flags=re.M)
    return block


def fill_report_prompt(report: dict, decl: dict) -> str:
    """Stage-2 prompt with its inputs: the declaration (stage 1) and the LLM version of the report."""
    stage1 = {k: decl.get(k) for k in ("factor", "thesis", "predictions")}
    inputs = ("【輸入一：作者的宣告（第一階段，看結果前寫的）】\n```yaml\n"
              + yaml.safe_dump(stage1, allow_unicode=True, sort_keys=False, width=1000)
              + "```\n\n【輸入二：程式算出的報告（report.json）】\n```json\n"
              + json.dumps({k: v for k, v in report.items() if k not in ("claims", "review")}, ensure_ascii=False,
                           separators=(",", ":"), default=str)
              + "\n```")
    return REPORT_PROMPT.read_text(encoding="utf-8").replace("<!-- INPUTS -->", inputs)


def provenance(panel: pd.DataFrame) -> dict:
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True,
                                text=True, timeout=10).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        commit = "unknown"
    meta = json.loads((ALPHA_DIR / "panel_meta.json").read_text(encoding="utf-8"))
    return {"generated_at": dt.datetime.now().isoformat(timespec="seconds"), "git_commit": commit,
            "panel_built_at": meta.get("built_at"), "data_through": str(panel.index.get_level_values(0).max().date()),
            "command": "python -m rdagent.scenarios.qlib.tw_data.factor_report_tw " + " ".join(sys.argv[1:]),
            "generator": "tw_data/factor_report_tw.py"}


# --------------------------------------------------------------------------- CLI


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--baseline", choices=sorted(ev.BASELINES), help="a built-in formula of alpha_eval_tw")
    src.add_argument("--card", help="an alpha-library card (data/alpha_tw/library/<card>)")
    src.add_argument("--idea", help="a teammate idea in alpha_team_ideas_tw.IDEAS")
    src.add_argument("--factor_py", help="factor.py in the loop's format (reads daily_pv.h5, writes result.h5)")
    src.add_argument("--alpha_file", help="values: CSV (datetime, instrument, value), parquet or HDF5")
    parser.add_argument("--declaration", help="the author's YAML (factor_declare_prompt_zh.md, then stage 2)")
    parser.add_argument("--horizon", type=int, choices=ev.HORIZONS,
                        help="default: the declaration's horizon_days, else 5")
    parser.add_argument("--holdout", default="none", choices=["none", "status", "full"])
    parser.add_argument("--as_of", help="cut the data at this date (YYYY-MM-DD) for a point-in-time report")
    parser.add_argument("--out", help="output folder; default data/alpha_tw/reports/<id>")
    parser.add_argument("--refresh_benchmarks", action="store_true")
    args = parser.parse_args()

    panel = pd.read_hdf(ALPHA_DIR / "alpha_panel.h5", key="data")
    if args.as_of:
        panel = panel[panel.index.get_level_values(0) <= pd.Timestamp(args.as_of)]
    values, known = load_factor(args, panel)
    decl = yaml.safe_load(Path(args.declaration).read_text(encoding="utf-8")) if args.declaration else {}
    decl = decl or {}
    decl["factor"] = {**{k: v for k, v in known.items() if v is not None}, **(decl.get("factor") or {})}
    h = args.horizon or int(decl["factor"].get("horizon_days") or 5)
    if h not in ev.HORIZONS:
        raise SystemExit(f"horizon_days must be one of {ev.HORIZONS}, got {h}")
    decl["factor"]["horizon_days"] = h
    fid = re.sub(r"[^\w.-]", "_", str(decl["factor"].get("id") or "factor"))
    out = Path(args.out) if args.out else ALPHA_DIR / "reports" / fid
    out.mkdir(parents=True, exist_ok=True)

    print(f"{fid}: {values.notna().sum():,} values; horizon {h} days; holdout {args.holdout}")
    ctx = Ctx(panel, values)
    bench = Bench(args.refresh_benchmarks)
    print("active ETFs holding Taiwan stocks: " + ", ".join(f"{c} {bench.names.get(c, '')}" for c in bench.etfs))
    skipped = {c: round(v, 2) for c, v in bench.etf_corr.items() if c not in bench.etfs}
    if skipped:
        print(f"  left out (daily corr with TPEx < {ETF_MIN_CORR}): "
              + ", ".join(f"{c} {bench.names.get(c, '')} {v}" for c, v in skipped.items()))
    skip = {known["id"], f"traded_{known['id']}"}
    library = library_values(skip)
    styles = style_values(panel)
    res = {"research": analyze(ctx, values, "research", h, library, styles, bench)}
    if args.holdout != "none":
        ho = analyze(ctx, values, "holdout", h, None, styles, bench)
        if ho:
            res["holdout"] = ho
            print("HOLDOUT scored: people may read it; report.json carries "
                  + ("its numbers (--holdout full)." if args.holdout == "full" else "pass/fail only."))
    checks = check_predictions(decl.get("predictions") or {}, res["research"], h)
    flags = code_flags(res, decl, h)
    prov = provenance(panel)
    report = build_json(res, decl, h, args.holdout, prov, checks, flags)

    # compact: the LLM reads it, and every space is a token
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, separators=(",", ":"), default=str),
                                     encoding="utf-8")
    (out / "report.html").write_text(build_html(res, decl, h, args.holdout, checks, flags, prov), encoding="utf-8")
    (out / "prompt_stage2.md").write_text(fill_report_prompt(report, decl), encoding="utf-8")
    if not args.declaration:
        (out / "declaration.yaml").write_text(declaration_template(known, h), encoding="utf-8")
    r = res["research"]
    print(f"research {r['start']} -> {r['end']}: IC{h} {r['judge']['ic'][str(h)]['mean']:+.4f} "
          f"(t {r['judge']['ic'][str(h)]['t_nw']:+.2f}); 24d median {r['judge']['windows_24d']['median']:+.2%}; "
          f"gate {'PASS' if r['gate']['passed'] else 'fail: ' + '; '.join(r['gate']['reasons'])}")
    for x in flags:
        print(f"  flag {x['code']}: {x['detail']}")
    print(f"Wrote {out}: report.json (LLM), report.html (people), prompt_stage2.md"
          + ("" if args.declaration else ", declaration.yaml (fill it in, then rerun with --declaration)"))


if __name__ == "__main__":
    main()
