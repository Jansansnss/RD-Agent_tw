"""
Fast alpha judge for the TW contest universe (docs/tw/ALPHA_FACTORY.md).

Scores one alpha, a (datetime, instrument) series known by the evening of `datetime`, the
way the AI CUP contest would trade it: pick from the point-in-time 150-stock universe, buy
at the next day's VWAP, pay 0.1425% per side plus 0.3% tax on sells. Higher value = buy.
Pure pandas, seconds per alpha; no qlib, Docker or LLM.

Windows (DECISIONS D16): "research" ends 2025-06-30, because DeepSeek V4's training data
reaches about May 2025. "holdout" (2025-07-01 on) must never reach an LLM prompt; score it
only for alphas that already passed the gate on the research window.

    python -m rdagent.scenarios.qlib.tw_data.alpha_eval_tw --alpha mom20 --alpha rev_accel
    python -m rdagent.scenarios.qlib.tw_data.alpha_eval_tw --alpha_file result.h5 --library mom20
    python -m rdagent.scenarios.qlib.tw_data.alpha_eval_tw --alpha mom20 --window holdout
"""

import argparse
import json
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

try:
    from rdagent.scenarios.qlib.tw_data._paths import DATA_DIR
except ImportError:
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _paths import DATA_DIR

COST_BUY = 0.001425
COST_SELL = 0.001425 + 0.003
HORIZONS = (1, 5, 10, 20)
CONTEST_DAYS = 24
TRADING_DAYS_PER_YEAR = 244
MIN_NAMES = 20  # smallest cross-section an IC is computed on
WINDOWS = {
    "research": ("2021-01-01", "2025-06-30"),
    "holdout": ("2025-07-01", None),
    "all": ("2021-01-01", None),
}
DEFAULT_GATE = {
    "horizon": 5,  # the horizon the alpha card declares
    "min_t_nw": 3.0,  # Newey-West t of the mean RankIC; 3 not 2, because the loop tests many ideas
    "min_years_positive": 3,  # calendar years (>= 120 IC days) with a positive mean IC
    "min_window_median": 0.0,  # top-k book, rolling 24-day net excess vs B0, median
    "max_library_corr": 0.6,
    "min_coverage": 0.8,
}
# The holdout only confirms an alpha that passed on research: it is under a year long, so the
# multi-year rule cannot apply, and one pre-chosen test needs no multiple-testing margin.
HOLDOUT_GATE = {**DEFAULT_GATE, "min_t_nw": 2.0, "min_years_positive": 0}


# --------------------------------------------------------------------------- core metrics


def forward_returns(vwap: pd.DataFrame, h: int) -> pd.DataFrame:
    """Return from VWAP(t+1) to VWAP(t+1+h): the signal is known on the evening of t."""
    return vwap.shift(-1 - h) / vwap.shift(-1) - 1


def rank_corr(a: pd.DataFrame, b: pd.DataFrame, mask: pd.DataFrame) -> pd.Series:
    """Per-date Spearman correlation between two wide frames over masked names."""
    valid = mask & a.notna() & b.notna()
    ra = a.where(valid).rank(axis=1)
    rb = b.where(valid).rank(axis=1)
    ra = ra.sub(ra.mean(axis=1), axis=0)
    rb = rb.sub(rb.mean(axis=1), axis=0)
    den = np.sqrt((ra**2).sum(axis=1) * (rb**2).sum(axis=1))
    corr = (ra * rb).sum(axis=1) / den.replace(0, np.nan)
    return corr.where(valid.sum(axis=1) >= MIN_NAMES)


def nw_tstat(x: pd.Series, lags: int) -> float:
    """t-stat of the mean with Newey-West (Bartlett) standard errors."""
    v = x.dropna().to_numpy()
    n = len(v)
    if n < 30:
        return float("nan")
    d = v - v.mean()
    var = d @ d / n
    for lag in range(1, min(lags, n - 1) + 1):
        var += 2 * (1 - lag / (lags + 1)) * (d[lag:] @ d[:-lag]) / n
    if var <= 0:  # a constant series: only a perfect (oracle) alpha gets here
        return float(np.sign(v.mean()) * np.inf) if v.mean() else float("nan")
    return float(v.mean() / np.sqrt(var / n))


def ic_stats(ic: pd.Series, h: int) -> dict:
    ic = ic.dropna()
    years = ic.groupby(ic.index.year).agg(["mean", "count"])
    std = ic.std()
    return {
        "mean": float(ic.mean()),
        "icir": float(ic.mean() / std) if std > 0 else float("nan"),
        "t_nw": nw_tstat(ic, lags=max(h, 1)),
        "pos_rate": float((ic > 0).mean()),
        "days": int(len(ic)),
        "by_year": {int(y): {"mean": float(r["mean"]), "days": int(r["count"])} for y, r in years.iterrows()},
    }


def topk_book(alpha: pd.DataFrame, vwap: pd.DataFrame, mask: pd.DataFrame, start: int, end: int,
              k: int = 24, band: int = 12, rebalance: int = 5) -> pd.DataFrame:
    """Equal-weight top-k long-only book with contest timing and costs.

    Row j is the interval VWAP(j) -> VWAP(j+1). Every `rebalance` rows the book trades at
    VWAP(j) on the signal from the evening of j-1: holdings ranked within k + band stay, the
    best-ranked new names fill up to k. A name with no VWAP on the trade day can be neither
    bought nor sold (the contest voids such orders), so held ones stay. Weights drift between
    rebalances; prices carry over no-trade days. B0 is the universe equal-weighted every day,
    without costs.
    """
    px = vwap.ffill()
    step = (px.shift(-1) / px - 1).to_numpy()
    tradable = vwap.notna().to_numpy()
    alpha_v, mask_v = alpha.to_numpy(), mask.to_numpy()
    names = np.asarray(vwap.columns)
    w = pd.Series(dtype=float)
    rows = []
    for j in range(max(start, 1), end):
        cost = turnover = 0.0
        if (j - start) % rebalance == 0:
            s = j - 1
            ok = mask_v[s] & tradable[j] & ~np.isnan(alpha_v[s])
            score = pd.Series(alpha_v[s][ok], index=names[ok])
            rank = score.rank(ascending=False, method="first")
            can_trade = set(names[tradable[j]])
            keep = [c for c in w.index if c not in can_trade or rank.get(c, np.inf) <= k + band]
            fill = [c for c in rank.sort_values().index if c not in keep][: max(k - len(keep), 0)]
            new = pd.Series(1.0 / max(len(keep) + len(fill), 1), index=keep + fill)
            diff = new.sub(w, fill_value=0.0)
            buys, sells = diff.clip(lower=0).sum(), (-diff).clip(lower=0).sum()
            cost, turnover = buys * COST_BUY + sells * COST_SELL, (buys + sells) / 2
            w = new
        r = pd.Series(step[j], index=names).reindex(w.index).fillna(0.0)
        gross = float((w * r).sum())
        w = w * (1 + r) / (1 + gross) if len(w) else w
        uni = mask_v[j] & ~np.isnan(step[j])
        bench = float(step[j][uni].mean()) if uni.any() else np.nan
        rows.append((vwap.index[j], gross, cost, gross - cost, bench, turnover, len(w)))
    book = pd.DataFrame(rows, columns=["date", "gross", "cost", "net", "bench", "turnover", "held"])
    book = book.set_index("date")
    book["excess"] = book["net"] - book["bench"]
    return book


def window_stats(book: pd.DataFrame, days: int = CONTEST_DAYS) -> dict:
    """Rolling `days`-day compounded return of the book minus B0 (the contest's length)."""
    roll = lambda s: np.expm1(np.log1p(s).rolling(days).sum())  # noqa: E731
    ex = (roll(book["net"]) - roll(book["bench"])).dropna()
    if ex.empty:
        return {"median": float("nan"), "p10": float("nan"), "p90": float("nan"), "win_rate": float("nan")}
    return {"median": float(ex.median()), "p10": float(ex.quantile(0.1)), "p90": float(ex.quantile(0.9)),
            "win_rate": float((ex > 0).mean())}


def top_names_excess(alpha: pd.DataFrame, fwd: pd.DataFrame, mask: pd.DataFrame, n: int = 10) -> dict:
    """Top-n names' forward return minus the universe mean; v2's '前 10 檔 24 日超額'."""
    out, hits = [], []
    for dt in alpha.index:
        ok = mask.loc[dt] & alpha.loc[dt].notna() & fwd.loc[dt].notna()
        if ok.sum() < MIN_NAMES:
            continue
        a, f = alpha.loc[dt][ok], fwd.loc[dt][ok]
        top = a.nlargest(n).index
        out.append(f[top].mean() - f.mean())
        hits.append((f[top] > f.median()).mean())
    if not out:
        return {"median_excess": float("nan"), "hit_rate": float("nan")}
    return {"median_excess": float(np.median(out)), "hit_rate": float(np.mean(hits))}


def _ann(r: pd.Series) -> float:
    return float(np.expm1(np.log1p(r).sum() * TRADING_DAYS_PER_YEAR / max(len(r), 1)))


def _to_wide(alpha: pd.Series, like: pd.DataFrame) -> pd.DataFrame:
    alpha = alpha[~alpha.index.duplicated(keep="last")].replace([np.inf, -np.inf], np.nan)
    return alpha.unstack("instrument").reindex(index=like.index, columns=like.columns)


def evaluate(alpha: pd.Series, panel: pd.DataFrame, window: str = "research",
             library: dict[str, pd.Series] | None = None, k: int = 24, band: int = 12,
             rebalance: int = 5, universe: str = "pit") -> dict:
    """All judge metrics for one alpha. universe: "pit" (monthly top 100+50) or "official"."""
    vwap = panel["$vwap"].unstack("instrument")
    flag = {"pit": "$in_universe", "official": "$in_official"}[universe]
    mask = panel[flag].unstack("instrument").reindex_like(vwap).eq(1)
    a = _to_wide(alpha, vwap)
    lo, hi = WINDOWS[window]
    dates = vwap.index
    i0 = int(dates.searchsorted(pd.Timestamp(lo)))
    i1 = int(dates.searchsorted(pd.Timestamp(hi), side="right")) if hi else len(dates)
    in_window = pd.Series(False, index=dates)
    in_window.iloc[i0:i1] = True

    metrics: dict = {"window": window, "universe": universe,
                     "start": str(dates[i0].date()), "end": str(dates[i1 - 1].date())}
    uni_size = mask[in_window].sum(axis=1)
    covered = (mask & a.notna())[in_window].sum(axis=1)
    metrics["coverage"] = float((covered / uni_size.replace(0, np.nan)).mean())

    metrics["ic"] = {}
    for h in HORIZONS:
        # keep only signals whose exit day is still inside the window
        exit_pos = np.arange(len(dates)) + 1 + h
        usable = in_window & pd.Series(exit_pos < i1, index=dates)
        ic = rank_corr(a, forward_returns(vwap, h), mask)[usable]
        metrics["ic"][str(h)] = ic_stats(ic, h)

    book = topk_book(a, vwap, mask, i0, i1 - 1, k=k, band=band, rebalance=rebalance)
    excess_ann = _ann(book["net"]) - _ann(book["bench"])
    beta = book["net"].cov(book["bench"]) / book["bench"].var()
    rebal = book["turnover"][book["turnover"] > 0]
    metrics["book"] = {
        "k": k, "band": band, "rebalance_days": rebalance,
        "ann_excess_net": float(excess_ann),
        "ann_cost": float(book["cost"].sum() * TRADING_DAYS_PER_YEAR / max(len(book), 1)),
        "turnover_per_rebalance": float(rebal.mean()) if len(rebal) else 0.0,
        "beta_vs_b0": float(beta),
    }
    metrics["windows_24d"] = window_stats(book)
    usable24 = in_window & pd.Series(np.arange(len(dates)) + 1 + CONTEST_DAYS < i1, index=dates)
    metrics["top10_24d"] = top_names_excess(a[usable24], forward_returns(vwap, CONTEST_DAYS)[usable24],
                                            mask[usable24])
    corr = {}
    for name, other in (library or {}).items():
        corr[name] = float(rank_corr(a, _to_wide(other, vwap), mask)[in_window].mean())
    metrics["library_corr"] = corr
    return metrics


def gate(metrics: dict, rules: dict = DEFAULT_GATE) -> tuple[bool, list[str]]:
    """Pass/fail on fixed thresholds. The LLM explains results; it never makes this call."""
    reasons = []
    ic = metrics["ic"][str(rules["horizon"])]
    if not metrics["coverage"] >= rules["min_coverage"]:
        reasons.append(f"coverage {metrics['coverage']:.0%} < {rules['min_coverage']:.0%}")
    if not ic["t_nw"] >= rules["min_t_nw"]:
        reasons.append(f"IC t-stat {ic['t_nw']:.2f} at {rules['horizon']}d < {rules['min_t_nw']}")
    years_pos = sum(1 for v in ic["by_year"].values() if v["days"] >= 120 and v["mean"] > 0)
    if years_pos < rules["min_years_positive"]:
        reasons.append(f"positive IC in {years_pos} full years < {rules['min_years_positive']}")
    med = metrics["windows_24d"]["median"]
    if not med > rules["min_window_median"]:
        reasons.append(f"median 24-day net excess {med:+.2%} <= {rules['min_window_median']:+.2%}")
    worst = max((abs(v) for v in metrics["library_corr"].values()), default=0.0)
    if worst > rules["max_library_corr"]:
        reasons.append(f"correlation {worst:.2f} with the library > {rules['max_library_corr']}")
    return not reasons, reasons


# --------------------------------------------------------------------------- baseline alphas
# The v2 plan's hypotheses, written as plain formulas. They double as judge sanity checks.


def _wide(panel: pd.DataFrame, col: str) -> pd.DataFrame:
    return panel[col].unstack("instrument")


def _long(wide: pd.DataFrame) -> pd.Series:
    return wide.stack(future_stack=True).dropna().rename("alpha")


def _flow(panel: pd.DataFrame, col: str, days: int) -> pd.Series:
    """Net buying over `days` days as a share of issued shares (no row = no net trade)."""
    traded = _wide(panel, "$close").notna()
    shares = _wide(panel, "$issued_shares").replace(0, np.nan)
    net = _wide(panel, col).fillna(0.0).rolling(days, min_periods=days).sum()
    return _long((net / shares).where(traded))


BASELINES: dict[str, Callable[[pd.DataFrame], pd.Series]] = {
    "mom20": lambda p: _long(_wide(p, "$close").pct_change(20, fill_method=None)),
    "mom60_skip5": lambda p: _long(_wide(p, "$close").shift(5) / _wide(p, "$close").shift(60) - 1),
    "rev5": lambda p: _long(-_wide(p, "$close").pct_change(5, fill_method=None)),
    "lowvol60": lambda p: _long(-_wide(p, "$close").pct_change(fill_method=None).rolling(60, min_periods=50).std()),
    "rev_yoy": lambda p: p["$rev_yoy"].rename("alpha"),
    "rev_accel": lambda p: p["$rev_accel"].rename("alpha"),
    "trust_flow5": lambda p: _flow(p, "$trust_net", 5),
    "trust_flow20": lambda p: _flow(p, "$trust_net", 20),
    "foreign_flow20": lambda p: _flow(p, "$foreign_net", 20),
}


def load_alpha_file(path: Path) -> pd.Series:
    obj = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_hdf(path)
    if isinstance(obj, pd.DataFrame):
        if obj.shape[1] != 1:
            raise ValueError(f"{path}: expected one column, got {list(obj.columns)}")
        obj = obj.iloc[:, 0]
    obj.index = obj.index.set_names(["datetime", "instrument"])
    return obj.astype(float)


# --------------------------------------------------------------------------- CLI


def _row(name: str, m: dict, passed: bool) -> str:
    ics = "  ".join(f"{m['ic'][str(h)]['mean']:+.3f}({m['ic'][str(h)]['t_nw']:+.1f})" for h in HORIZONS)
    w = m["windows_24d"]
    corr = max((abs(v) for v in m["library_corr"].values()), default=float("nan"))
    return (f"{name:<16} {ics}  {m['book']['ann_excess_net']:+7.1%} {w['median']:+6.2%} {w['p10']:+6.2%} "
            f"{w['win_rate']:5.0%} {m['top10_24d']['median_excess']:+6.2%} {m['book']['turnover_per_rebalance']:5.0%} "
            f"{m['book']['beta_vs_b0']:5.2f} {corr:5.2f}  {'PASS' if passed else 'fail'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--panel", default=str(DATA_DIR / "alpha_tw" / "alpha_panel.h5"))
    parser.add_argument("--alpha", action="append", default=[], choices=sorted(BASELINES),
                        help="Built-in baseline alpha (repeatable)")
    parser.add_argument("--alpha_file", action="append", default=[],
                        help="H5/parquet with one (datetime, instrument) column, e.g. a CoSTEER result.h5")
    parser.add_argument("--library", action="append", default=[], choices=sorted(BASELINES),
                        help="Baselines treated as the existing library for the correlation check")
    parser.add_argument("--window", default="research", choices=sorted(WINDOWS))
    parser.add_argument("--universe", default="pit", choices=["pit", "official"],
                        help="pit = monthly top 100 TWSE + 50 TPEx; official = the contest's list")
    parser.add_argument("--horizon", type=int, default=DEFAULT_GATE["horizon"], choices=HORIZONS)
    parser.add_argument("--json", default=None, help="Write full metrics to this path")
    args = parser.parse_args()

    panel = pd.read_hdf(args.panel, key="data")
    alphas = {name: BASELINES[name](panel) for name in args.alpha}
    alphas.update({Path(f).stem: load_alpha_file(Path(f)) for f in args.alpha_file})
    library = {name: BASELINES[name](panel) for name in args.library}
    rules = {**(HOLDOUT_GATE if args.window == "holdout" else DEFAULT_GATE), "horizon": args.horizon}
    if args.window == "holdout":
        print("HOLDOUT window: never paste these numbers into an LLM prompt. Confirmation gate only.")

    header = "  ".join(f"IC{h:<2}(t)     " for h in HORIZONS)
    print(f"{'alpha':<16} {header}  ann.ex  24d-md  24d-p10 win  top10   turn  beta  corr  gate")
    results = {}
    for name, alpha in alphas.items():
        lib = {k: v for k, v in library.items() if k != name}
        m = evaluate(alpha, panel, window=args.window, library=lib, universe=args.universe)
        passed, reasons = gate(m, rules)
        m["gate"] = {"passed": passed, "reasons": reasons, "rules": rules}
        results[name] = m
        print(_row(name, m, passed))
        for reason in reasons:
            print(f"{'':<18}- {reason}")
    if results:
        any_m = next(iter(results.values()))
        print(f"window {any_m['window']}: {any_m['start']} -> {any_m['end']}; "
              f"book = top {any_m['book']['k']}, rebalance every {any_m['book']['rebalance_days']} days")
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
