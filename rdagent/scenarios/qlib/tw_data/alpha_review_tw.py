"""
Review of holdout-confirmed alpha cards (TODO G4, DECISIONS D24): what a card really buys, before
anyone trades it. Never part of the loop; its numbers must not reach an LLM prompt.

For each card: run factor.py on the full anonymized panel, map the IDs back to stock codes, and
report research / holdout / official-list metrics, the correlation with plain exposures (size,
liquidity, value, momentum, low volatility, revenue growth), the top 24 names on the last day with
industry and size rank, and the holdout book's industry mix, size percentile and monthly excess.
The code audit (look-ahead, timing, units) stays a human step; record it with these numbers in
docs/tw/ALPHA_REVIEWS.md.

    python -m rdagent.scenarios.qlib.tw_data.alpha_review_tw                  # every confirmed card
    python -m rdagent.scenarios.qlib.tw_data.alpha_review_tw --card LiquiditySize_Rank_20d
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    from rdagent.scenarios.qlib.tw_data import alpha_eval_tw as ev
    from rdagent.scenarios.qlib.tw_data._paths import DATA_DIR
    from rdagent.scenarios.qlib.tw_data.alpha_holdout_tw import run_factor
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import alpha_eval_tw as ev
    from _paths import DATA_DIR
    from alpha_holdout_tw import run_factor

ALPHA_DIR = DATA_DIR / "alpha_tw"
MOSTLY = 0.7  # |correlation| above this with a plain exposure: the card is mostly that exposure


def exposures(panel: pd.DataFrame) -> dict[str, pd.Series]:
    """Plain characteristics a card may be a disguised copy of (higher = the usual 'buy' side)."""
    wide = lambda col: panel[col].unstack("instrument")  # noqa: E731
    close, amount, mv = wide("$close"), wide("$amount"), wide("$market_value")
    mv = mv.where(mv > 0)
    illiq = (close.pct_change(fill_method=None).abs() / amount.where(amount > 0)).rolling(20, min_periods=20).mean()
    long = lambda w: w.stack(future_stack=True).dropna()  # noqa: E731
    return {
        "size (log market value)": long(np.log(mv)),
        "liquidity (-Amihud 20d)": long(-illiq),
        "sales yield (12 x revenue / market value)": long(12 * wide("$rev") / mv),
        "earnings yield (1 / PE)": long(1 / wide("$pe_ratio").where(lambda x: x > 0)),
        "book to price (1 / PB)": long(1 / wide("$pb_ratio").where(lambda x: x > 0)),
        "momentum (mom60_skip5)": ev.BASELINES["mom60_skip5"](panel),
        "low volatility (lowvol60)": ev.BASELINES["lowvol60"](panel),
        "revenue growth (rev_yoy)": ev.BASELINES["rev_yoy"](panel),
    }


def card_values(name: str, sid2code: pd.Series) -> pd.Series:
    data = (ALPHA_DIR / "coder_full" / "daily_pv.h5").resolve()
    s = run_factor((ALPHA_DIR / "library" / name / "factor.py").resolve(), data)
    codes = s.index.get_level_values(1).map(sid2code)
    s.index = pd.MultiIndex.from_arrays([s.index.get_level_values(0), codes], names=["datetime", "instrument"])
    return s.dropna()


def review(name: str, panel: pd.DataFrame, exp: dict[str, pd.Series], sid2code: pd.Series,
           meta: pd.DataFrame) -> dict:
    s = card_values(name, sid2code)
    out: dict = {"card": name, "metrics": {}, "exposure_corr": {}, "top24_last_day": [], "holdout_book": {}}
    for label, window, universe in (("research", "research", "pit"), ("holdout", "holdout", "pit"),
                                    ("holdout, official list", "holdout", "official")):
        m = ev.evaluate(s, panel, window=window, universe=universe)
        out["metrics"][label] = {"t5": m["ic"]["5"]["t_nw"], "t20": m["ic"]["20"]["t_nw"],
                                 "median_24d": m["windows_24d"]["median"], "p10_24d": m["windows_24d"]["p10"],
                                 "ann_excess": m["book"]["ann_excess_net"], "beta": m["book"]["beta_vs_b0"],
                                 "turnover": m["book"]["turnover_per_rebalance"]}
    for window in ("research", "holdout"):
        corr = ev.evaluate(s, panel, window=window, library=exp)["library_corr"]
        for k, v in corr.items():
            out["exposure_corr"].setdefault(k, {})[window] = v
    out["mostly"] = [k for k, v in out["exposure_corr"].items() if abs(v["research"]) > MOSTLY]

    last = panel.index.get_level_values(0).max()
    snap = panel.xs(last, level=0)
    uni = snap.index[snap["$in_universe"] == 1]
    size_rank = snap.loc[uni, "$market_value"].rank(ascending=False)
    top = s.xs(last, level=0).reindex(uni).dropna().sort_values(ascending=False).head(24)
    for code in top.index:
        out["top24_last_day"].append({"code": code, "name": meta["name"].get(code, "?"),
                                      "industry": meta["industry"].get(code, "?"),
                                      "size_rank": int(size_rank.get(code, -1)),
                                      "official": bool(snap.loc[code, "$in_official"] == 1)})

    vwap = panel["$vwap"].unstack("instrument")
    mask = panel["$in_universe"].unstack("instrument").reindex_like(vwap).eq(1)
    i0 = int(vwap.index.searchsorted(pd.Timestamp(ev.WINDOWS["holdout"][0])))
    book = ev.topk_book(ev._to_wide(s, vwap), vwap, mask, i0, len(vwap.index) - 1)
    month = book.groupby(book.index.to_period("M"))
    monthly = month.apply(lambda b: np.expm1(np.log1p(b["net"]).sum()) - np.expm1(np.log1p(b["bench"]).sum()))
    industries, size_pct = {}, []
    for day in vwap.index[i0::5]:
        snap = panel.xs(day, level=0)
        uni = snap.index[snap["$in_universe"] == 1]
        pick = s.xs(day, level=0).reindex(uni).dropna().sort_values(ascending=False).head(24).index
        size_pct.append(float(snap.loc[uni, "$market_value"].rank(pct=True).reindex(pick).mean()))
        for code in pick:
            ind = meta["industry"].get(code, "?")
            industries[ind] = industries.get(ind, 0) + 1
    total = sum(industries.values()) or 1
    out["holdout_book"] = {
        "monthly_excess": {str(k): float(v) for k, v in monthly.items()},
        "size_percentile": float(np.mean(size_pct)) if size_pct else float("nan"),
        "industries": sorted(((k, v / total) for k, v in industries.items()), key=lambda kv: -kv[1])[:6],
    }
    return out


def show(r: dict) -> None:
    print(f"\n=== {r['card']}  (HOLDOUT numbers: never paste into an LLM prompt)")
    for label, m in r["metrics"].items():
        print(f"  {label:<24} t5 {m['t5']:+.2f} t20 {m['t20']:+.2f} | 24d median {m['median_24d']:+.2%} "
              f"p10 {m['p10_24d']:+.2%} | ann. excess {m['ann_excess']:+.1%} beta {m['beta']:.2f} "
              f"turnover {m['turnover']:.0%}")
    print("  correlation with plain exposures (research | holdout):")
    for k, v in sorted(r["exposure_corr"].items(), key=lambda kv: -abs(kv[1]["research"])):
        print(f"    {k:<42} {v['research']:+.2f} | {v['holdout']:+.2f}")
    print(f"  mostly: {', '.join(r['mostly']) or 'none above ' + str(MOSTLY)}")
    hb = r["holdout_book"]
    print(f"  holdout book: size percentile {hb['size_percentile']:.2f}; industries "
          + ", ".join(f"{k} {v:.0%}" for k, v in hb["industries"]))
    print("  monthly excess vs the equal-weighted universe: "
          + ", ".join(f"{k} {v:+.1%}" for k, v in hb["monthly_excess"].items()))
    print("  top 24 on the last day: " + ", ".join(
        f"{t['code']} {t['name']} (#{t['size_rank']}{'' if t['official'] else ', not official'})" for t in r["top24_last_day"]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--card", action="append", default=[], help="library card name; default: every confirmed card")
    args = parser.parse_args()
    names = args.card
    if not names:
        report = json.loads((ALPHA_DIR / "holdout_report.json").read_text(encoding="utf-8"))
        names = [r["name"] for r in report if r.get("passed")]
    if not names:
        raise SystemExit("No confirmed card in holdout_report.json; run alpha_holdout_tw first.")
    panel = pd.read_hdf(ALPHA_DIR / "alpha_panel.h5", key="data")
    sid2code = pd.read_csv(ALPHA_DIR / "coder_map.csv", dtype=str).set_index("sid")["instrument"]
    meta = pd.read_csv(ALPHA_DIR / "instruments_meta.csv", dtype={"instrument": str}).set_index("instrument")
    exp = exposures(panel)
    out_dir = ALPHA_DIR / "reviews"
    out_dir.mkdir(exist_ok=True)
    for name in names:
        r = review(name, panel, exp, sid2code, meta)
        show(r)
        (out_dir / f"{name}.json").write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(f"\nWrote {out_dir}. Record the code audit and a verdict in docs/tw/ALPHA_REVIEWS.md.")


if __name__ == "__main__":
    main()
