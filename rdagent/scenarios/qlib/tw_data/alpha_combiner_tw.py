"""
LightGBM signal combiner, walk-forward (docs/tw/reports/2026-10-25_model_research.md §3).

Inputs: the universe percentile ranks of the three signals the daily agent trades (rev_accel,
rev_yoy, mom60_skip5) and of the two holdout-confirmed cards (LiquiditySize_Rank_20d,
SalesYield_LiquiditySize_70_30), on the anonymized full panel. Target: the rank of the 5-day
forward VWAP return within the day's universe. Folds stay inside the research window: train
2021-2023 -> predict 2024, 2021-2024 -> 2025, 2021-2025 -> 2026-01..04. Each fold leaves out the
6 trading days before its test period (their labels reach into it) and stops early on the last
year of its training span. The out-of-sample predictions are scored by alpha_eval_tw next to each
signal alone, the equal-weight rank average, a learned linear combination and the agent's 50/20/30
blend.

The holdout comparison is for reference only: the two cards were picked because they passed the
holdout, so every combination that holds them looks better there than it would on new data.

    python -m rdagent.scenarios.qlib.tw_data.alpha_combiner_tw --json data/alpha_tw/combiner.json
"""

import argparse
import json
import sys
from pathlib import Path

import lightgbm as lgb
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
SEEDS = ("rev_accel", "rev_yoy", "mom60_skip5")
CARDS = ("LiquiditySize_Rank_20d", "SalesYield_LiquiditySize_70_30")
BLEND = {"rev_accel": 0.5, "rev_yoy": 0.2, "mom60_skip5": 0.3}  # the daily agent's weights
TRAIN_START = "2021-01-01"
FOLDS = [("2024-01-01", "2024-12-31"), ("2025-01-01", "2025-12-31"), ("2026-01-01", "2026-04-30")]
HORIZON = 5
EMBARGO = HORIZON + 1  # rows whose label window reaches the next period
PARAMS = {"objective": "regression", "learning_rate": 0.03, "num_leaves": 7, "max_depth": 3,
          "min_data_in_leaf": 500, "lambda_l2": 10.0, "bagging_fraction": 0.8, "bagging_freq": 1,
          "verbose": -1, "seed": 7, "deterministic": True, "num_threads": 4}
MAX_TREES, PATIENCE = 500, 50


def train_lgb(X: pd.DataFrame, y: pd.Series, pos: np.ndarray, start: int, end: int) -> tuple[lgb.Booster, int]:
    """Rows start <= pos < end: pick the tree count on the span's last year (after a gap), then refit
    on the whole span with it."""
    v0 = end - ev.TRADING_DAYS_PER_YEAR
    fit = (pos >= start) & (pos < v0 - EMBARGO) & y.notna().to_numpy()
    val = (pos >= v0) & (pos < end) & y.notna().to_numpy()
    probe = lgb.train(PARAMS, lgb.Dataset(X[fit], y[fit]), MAX_TREES,
                      valid_sets=[lgb.Dataset(X[val], y[val])],
                      callbacks=[lgb.early_stopping(PATIENCE, verbose=False)])
    trees = max(probe.best_iteration, 10)
    rows = (pos >= start) & (pos < end) & y.notna().to_numpy()
    return lgb.train(PARAMS, lgb.Dataset(X[rows], y[rows]), trees), trees


def train_linear(X: pd.DataFrame, y: pd.Series, pos: np.ndarray, start: int, end: int) -> np.ndarray:
    rows = (pos >= start) & (pos < end) & y.notna().to_numpy()
    A = np.column_stack([np.ones(rows.sum()), X[rows].fillna(0.5).to_numpy()])
    return np.linalg.lstsq(A, y[rows].to_numpy(), rcond=None)[0]


def predict_linear(coef: np.ndarray, X: pd.DataFrame) -> np.ndarray:
    return coef[0] + X.fillna(0.5).to_numpy() @ coef[1:]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--json", default=None)
    args = parser.parse_args()
    data = ALPHA_DIR / "coder_full" / "daily_pv.h5"
    panel = pd.read_hdf(data, key="data")
    vwap = panel["$vwap"].unstack("instrument")
    uni = panel["$in_universe"].unstack("instrument").reindex_like(vwap).eq(1)
    dates = vwap.index

    signals = {k: ev.BASELINES[k](panel) for k in SEEDS}
    for card in CARDS:
        signals[card] = run_factor(ALPHA_DIR / "library" / card / "factor.py", data)
    ranks = {k: ev._to_wide(s, vwap).where(uni).rank(axis=1, pct=True) for k, s in signals.items()}
    keep = uni.stack(future_stack=True)
    X = pd.concat({k: r.stack(future_stack=True) for k, r in ranks.items()}, axis=1)[keep]
    y = ev.forward_returns(vwap, HORIZON).where(uni).rank(axis=1, pct=True).stack(future_stack=True)[keep]
    pos = dates.get_indexer(X.index.get_level_values("datetime"))
    at = lambda d, side="left": int(dates.searchsorted(pd.Timestamp(d), side=side))  # noqa: E731
    start = at(TRAIN_START)

    preds = {"lightgbm": [], "linear": []}
    folds = []
    for lo, hi in FOLDS:
        t0, t1 = at(lo), at(hi, "right")
        booster, trees = train_lgb(X, y, pos, start, t0 - EMBARGO)
        coef = train_linear(X, y, pos, start, t0 - EMBARGO)
        test = (pos >= t0) & (pos < t1)
        preds["lightgbm"].append(pd.Series(booster.predict(X[test]), index=X.index[test]))
        preds["linear"].append(pd.Series(predict_linear(coef, X[test]), index=X.index[test]))
        gain = booster.feature_importance("gain")
        folds.append({"test": [lo, hi], "trees": trees, "train_rows": int(((pos >= start) & (pos < t0 - EMBARGO)).sum()),
                      "gain_share": dict(zip(X.columns, (gain / gain.sum()).round(3).tolist())),
                      "linear_weights": dict(zip(X.columns, coef[1:].round(3).tolist()))})
        print(f"fold {lo}..{hi}: {trees} trees; gain share {folds[-1]['gain_share']}; linear {folds[-1]['linear_weights']}")

    # Simple combinations, the same for every period; a missing signal counts as the median (0.5)
    ew = (sum(r.fillna(0.5) for r in ranks.values()) / len(ranks)).where(uni)
    blend = sum(w * ranks[k].fillna(0.5) for k, w in BLEND.items()).where(uni)
    candidates = {k: ev._long(r) for k, r in ranks.items()}
    candidates["equal_weight_5"] = ev._long(ew)
    candidates["blend_50_20_30"] = ev._long(blend)

    ev.WINDOWS["walk_forward"] = (FOLDS[0][0], FOLDS[-1][1])
    results = {"folds": folds, "walk_forward": {}, "holdout_reference": {}}
    library = {k: signals[k] for k in SEEDS}
    for name, alpha in {**{k: pd.concat(v) for k, v in preds.items()}, **candidates}.items():
        m = ev.evaluate(alpha.rename("alpha"), panel, window="walk_forward", library=library)
        results["walk_forward"][name] = m
    # Final model on the whole research window, scored on the holdout (reference only, see docstring)
    h0 = at(ev.WINDOWS["holdout"][0])
    booster, trees = train_lgb(X, y, pos, start, h0 - EMBARGO)
    coef = train_linear(X, y, pos, start, h0 - EMBARGO)
    test = pos >= h0
    final = {"lightgbm": pd.Series(booster.predict(X[test]), index=X.index[test]),
             "linear": pd.Series(predict_linear(coef, X[test]), index=X.index[test])}
    for name, alpha in {**final, **candidates}.items():
        results["holdout_reference"][name] = ev.evaluate(alpha.rename("alpha"), panel, window="holdout", library=library)
    gain = booster.feature_importance("gain")
    results["final"] = {"trees": trees, "gain_share": dict(zip(X.columns, (gain / gain.sum()).round(3).tolist())),
                        "linear_weights": dict(zip(X.columns, coef[1:].round(3).tolist()))}

    # Per-stock reasons on the last date: SHAP contributions of the final model's top names
    last = X.index.get_level_values("datetime").max()
    xl = X.xs(last, level="datetime")
    contrib = pd.DataFrame(booster.predict(xl, pred_contrib=True), index=xl.index, columns=[*X.columns, "base"])
    contrib["score"] = contrib.sum(axis=1)
    codes = pd.read_csv(ALPHA_DIR / "coder_map.csv", dtype={"instrument": str}).set_index("sid")["instrument"]
    top = contrib.sort_values("score", ascending=False).head(8)
    top.index = [codes.get(i, i) for i in top.index]
    results["reasons_example"] = {"date": str(last.date()), "top": top.round(4).to_dict(orient="index")}
    mean_abs = contrib[list(X.columns)].abs().mean()
    results["final"]["mean_abs_contrib"] = (mean_abs / mean_abs.sum()).round(3).to_dict()

    for part in ("walk_forward", "holdout_reference"):
        print(f"\n== {part}")
        print(f"{'alpha':<32} IC5(t)         IC20(t)        ann.ex  24d-md  24d-p10  win  turn  beta")
        for name, m in results[part].items():
            ic = m["ic"]
            w = m["windows_24d"]
            print(f"{name:<32} {ic['5']['mean']:+.3f}({ic['5']['t_nw']:+.1f})  {ic['20']['mean']:+.3f}({ic['20']['t_nw']:+.1f})"
                  f"  {m['book']['ann_excess_net']:+6.1%} {w['median']:+.2%} {w['p10']:+.2%} {w['win_rate']:4.0%} "
                  f"{m['book']['turnover_per_rebalance']:4.0%} {m['book']['beta_vs_b0']:5.2f}")
    print("\nfinal model:", results["final"])
    print(f"top names on {results['reasons_example']['date']}:")
    print(top.round(3).to_string())
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2, default=float), encoding="utf-8")


if __name__ == "__main__":
    main()
