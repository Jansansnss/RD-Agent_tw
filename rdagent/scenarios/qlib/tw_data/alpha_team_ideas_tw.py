"""
Teammates' alpha ideas as plain formulas, scored by the alpha judge (alpha_eval_tw).

Alisa's strategy review (2026-10-26) proposes refinements of a short-term momentum strategy:
smoother momentum, industry-group rotation, rebounds that wait for confirmation, flows split by
investor type. Each idea is one formula fixed before scoring (tags B1-B14 as in
data/alpha_ideas.md), plus an approximation of the eight-factor score the review starts from. Like
alpha_eval_tw.BASELINES these are human ideas, so both windows are reported; they were written
after the holdout months had happened, so their holdout numbers are not a clean confirmation
(ALPHA_FACTORY §7). Holdout numbers never go into data/alpha_ideas.md.

Runs on the anonymized full panel (data/alpha_tw/coder_full), the data the loop's coder sees.
Groups are the panel's $industry_id (each stock's primary industry-chain group, D30); with --multi
every chain industry a stock is tagged with counts, as in the team's group review: each industry
is computed over all its tagged members, and a stock gets the mean over its industries.

    python -m rdagent.scenarios.qlib.tw_data.alpha_team_ideas_tw --json data/alpha_tw/team_ideas.json
    python -m rdagent.scenarios.qlib.tw_data.alpha_team_ideas_tw --multi --json data/alpha_tw/team_ideas_multi.json
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
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import alpha_eval_tw as ev
    from _paths import DATA_DIR

MIN_GROUP = 3  # an industry needs this many universe members with a value to have a group number
FLOWS = ("$foreign_net", "$trust_net", "$dealer_net")


class Wide:
    """Wide (date x instrument) views shared by the formulas."""

    def __init__(self, panel: pd.DataFrame, members: pd.DataFrame | None = None):
        col = lambda c: panel[c].unstack("instrument")  # noqa: E731
        self.close = col("$close")
        like = lambda c: col(c).reindex_like(self.close)  # noqa: E731
        self.uni = like("$in_universe").eq(1)
        self.ind = like("$industry_id").where(self.uni)
        self.ret = self.close.pct_change(fill_method=None)
        self.volume, self.amount = like("$volume"), like("$amount")
        self.shares = like("$issued_shares").replace(0, np.nan)
        self.flow = {c: like(c).fillna(0.0) for c in FLOWS}
        # instrument x industry, 1 = tagged: every chain industry counts (--multi)
        self.members = None if members is None else members.reindex(self.close.columns).fillna(0.0)

    def mom(self, n: int) -> pd.DataFrame:
        return self.close.pct_change(n, fill_method=None)

    def rank(self, x: pd.DataFrame) -> pd.DataFrame:
        """Percentile rank within the day's universe."""
        return x.where(self.uni).rank(axis=1, pct=True)

    def market(self, x: pd.DataFrame) -> pd.Series:
        return x.where(self.uni).mean(axis=1)

    def group(self, x: pd.DataFrame, how: str = "mean", leave_out: bool = False) -> pd.DataFrame:
        """Industry mean (or sum) per day over universe members with a value; NaN for small groups."""
        if self.members is not None:
            return self._group_multi(x, how, leave_out)
        df = pd.DataFrame({"v": x.where(self.uni).stack(future_stack=True),
                           "g": self.ind.stack(future_stack=True)}).dropna()
        keys = [df.index.get_level_values(0), df["g"]]
        total = df.groupby(keys)["v"].transform("sum")
        n = df.groupby(keys)["v"].transform("count")
        out = total if how == "sum" else (total - df["v"]) / (n - 1) if leave_out else total / n
        return out.where(n >= MIN_GROUP).unstack().reindex_like(x)

    def _group_multi(self, x: pd.DataFrame, how: str, leave_out: bool) -> pd.DataFrame:
        """group() when a stock can be in several industries: the mean over its industries."""
        v = x.where(self.uni)
        ok = v.notna().to_numpy(dtype=float)
        vals = v.fillna(0.0).to_numpy()
        total = np.zeros_like(vals)
        count = np.zeros_like(vals)
        for g in self.members.columns:
            cols = self.members[g].to_numpy() > 0
            s = vals[:, cols].sum(axis=1, keepdims=True)
            n = ok[:, cols].sum(axis=1, keepdims=True)
            if how == "sum":
                gv = np.repeat(s, cols.sum(), axis=1)
            elif leave_out:
                with np.errstate(divide="ignore", invalid="ignore"):
                    gv = (s - vals[:, cols]) / (n - ok[:, cols])
                gv[ok[:, cols] == 0] = np.nan
            else:
                gv = np.repeat(s / np.where(n > 0, n, np.nan), cols.sum(), axis=1)
            gv[np.repeat(n, cols.sum(), axis=1) < MIN_GROUP] = np.nan
            has = ~np.isnan(gv)
            total[:, cols] += np.where(has, gv, 0.0)
            count[:, cols] += has
        with np.errstate(divide="ignore", invalid="ignore"):
            out = np.where(count > 0, total / count, np.nan)
        return pd.DataFrame(out, index=x.index, columns=x.columns)

    def excess(self, x: pd.DataFrame) -> pd.DataFrame:
        """Group mean minus the market; stocks in small groups count as neutral (0)."""
        return self.group(x).sub(self.market(x), axis=0).fillna(0.0).where(self.uni)


def up_days(w: Wide, n: int) -> pd.DataFrame:
    up = (w.ret > 0).astype(float).where(w.ret.notna())
    return up.rolling(n, min_periods=n).sum()


def concentration(w: Wide, n: int) -> pd.DataFrame:
    """Largest daily gain / sum of daily gains over n days (1 = the whole rise came in one day)."""
    gain = w.ret.clip(lower=0)
    top, total = gain.rolling(n, min_periods=n).max(), gain.rolling(n, min_periods=n).sum()
    return (top / total).where(total > 0, 1.0).where(top.notna())


def flow(w: Wide, col: str, n: int) -> pd.DataFrame:
    return (w.flow[col].rolling(n, min_periods=n).sum() / w.shares).where(w.close.notna())


def group_turn(w: Wide) -> pd.DataFrame:
    """B6: the group's excess return over the last 5 days minus the 5 days before."""
    g5 = w.excess(w.mom(5))
    return g5 - g5.shift(5)


def breadth_change(w: Wide) -> pd.DataFrame:
    """B7: change over 5 days in the share of group members up over 5 days."""
    m5 = w.mom(5)
    breadth = w.group((m5 > 0).astype(float).where(m5.notna()))
    return (breadth - breadth.shift(5)).fillna(0.0).where(w.uni)


def amount_share_change(w: Wide) -> pd.DataFrame:
    """B8: log change over 5 days of the group's share of the universe's 5-day trading value."""
    a5 = w.amount.rolling(5, min_periods=5).sum()
    share = w.group(a5, how="sum").div(a5.where(w.uni).sum(axis=1), axis=0)
    return np.log(share / share.shift(5)).fillna(0.0).where(w.uni)


def ma20(w: Wide) -> pd.DataFrame:
    return w.close.rolling(20, min_periods=20).mean()


def deep_fall(w: Wide) -> pd.DataFrame:
    """B12 candidate: 20-day return in the bottom 30% and the close 15% or more below its 20-day high."""
    return (w.rank(w.mom(20)) <= 0.3) & (w.close <= 0.85 * w.close.rolling(20, min_periods=20).max())


def rebound_confirm(w: Wide) -> pd.DataFrame:
    """B12 confirmation: the close is above the highest close of the previous 3 days."""
    return w.close > w.close.shift(1).rolling(3, min_periods=3).max()


def orig8(w: Wide) -> pd.DataFrame:
    """The reviewed eight-factor score, as read from the review's table (weights given there; the
    exact formulas of the volume and flow parts are not, so these are approximations)."""
    m5, m10, m20, m40 = (w.mom(n) for n in (5, 10, 20, 40))
    volume_ratio = w.volume.rolling(5, min_periods=5).mean() / w.volume.rolling(60, min_periods=40).mean()
    parts = [
        (0.20, m5), (0.15, m10), (0.20, m20), (0.05, m40),
        (0.10, m20.sub(w.market(m20), axis=0)),  # 20-day strength against the market
        (0.10, (volume_ratio - 1) * np.sign(m5)),  # volume surge in the direction of the 5-day move
        (0.10, sum(flow(w, c, 5) for c in FLOWS)),  # the three institutions' net buying, summed
        (0.10, w.group(m20)),  # the industry's 20-day return
    ]
    return sum(k * w.rank(x).fillna(0.5) for k, x in parts).where(w.uni)


def orig_filters(w: Wide) -> pd.DataFrame:
    """The review's four hard conditions: up over 5 and 20 days, above the 20-day average,
    average daily trading value at least NT$20 million."""
    m5, m20 = w.mom(5), w.mom(20)
    liquid = w.amount.rolling(20, min_periods=20).mean() >= 2e7
    return (m5 > 0) & (m20 > 0) & (w.close > ma20(w)) & liquid


IDEAS = {
    "mom5": ("plain 5-day momentum (reference)", lambda w: w.mom(5)),
    "mom20": ("plain 20-day momentum (reference)", lambda w: w.mom(20)),
    "B1_mom5_updays": ("5-day return rank + up days in 5", lambda w: w.rank(w.mom(5)) + w.rank(up_days(w, 5))),
    "B2_mom5_spread": ("5-day return rank + low gain concentration",
                       lambda w: w.rank(w.mom(5)) + w.rank(-concentration(w, 5))),
    "B1_mom20_updays": ("20-day return rank + up days in 20", lambda w: w.rank(w.mom(20)) + w.rank(up_days(w, 20))),
    "B4_group_rs20": ("industry 20-day return minus the market", lambda w: w.excess(w.mom(20))),
    "B5_stock_vs_group20": ("20-day return minus the other members' mean",
                            lambda w: w.mom(20) - w.group(w.mom(20), leave_out=True)),
    "B6_group_turn": ("industry excess return, last 5 days minus the 5 before", group_turn),
    "B7_breadth_change": ("change in the industry's share of members up over 5 days", breadth_change),
    "B8_amount_share_change": ("change in the industry's share of trading value", amount_share_change),
    "B6B7_weak_to_strong": ("B6 rank + B7 rank (the review's pick)",
                            lambda w: w.rank(group_turn(w)) + w.rank(breadth_change(w))),
    "B9_mom20_highvol": ("20-day return rank + 20-day volatility rank",
                         lambda w: w.rank(w.mom(20)) + w.rank(w.ret.rolling(20, min_periods=15).std())),
    "B10_foreign_trust_agree5": ("lower of the foreign and trust 5-day buying ranks",
                                 lambda w: np.fmin(w.rank(flow(w, "$foreign_net", 5)),
                                                   w.rank(flow(w, "$trust_net", 5)))),
    "B11_accel5": ("5-day return minus the 5 days before", lambda w: w.mom(5) - w.mom(5).shift(5)),
    "B12_rebound_confirmed": ("deep fall with a confirmed rebound on top, then the deepest falls",
                              lambda w: 2.0 * (deep_fall(w) & rebound_confirm(w)) + w.rank(-w.mom(20))),
    "B13_ma20_state": ("distance above the 20-day average + its 5-day slope",
                       lambda w: w.rank(w.close / ma20(w) - 1) + w.rank(ma20(w) / ma20(w).shift(5) - 1)),
    "orig8": ("the reviewed eight-factor score, no hard conditions", orig8),
    "orig8_filtered": ("the eight-factor score, only names passing the four conditions",
                       lambda w: orig8(w).where(orig_filters(w))),
}


def rebound_events(w: Wide, vwap: pd.DataFrame, window: str) -> dict:
    """B12 as an event study: forward VWAP return minus the universe mean, for deep falls with and
    without the confirmation, pooled over the window's days."""
    lo, hi = ev.WINDOWS[window]
    i0 = int(vwap.index.searchsorted(pd.Timestamp(lo)))
    i1 = int(vwap.index.searchsorted(pd.Timestamp(hi), side="right")) if hi else len(vwap.index)
    deep, conf = deep_fall(w), rebound_confirm(w)
    out = {}
    for h in (5, 10):
        days = slice(i0, max(i0, i1 - 1 - h))  # the exit day stays inside the window
        fwd = ev.forward_returns(vwap, h).where(w.uni)
        ex = fwd.sub(fwd.mean(axis=1), axis=0).iloc[days]
        for name, sel in (("confirmed", deep & conf), ("not_confirmed", deep & ~conf)):
            v = ex.where(sel.iloc[days]).stack(future_stack=True).dropna()
            out[f"{name}_{h}d"] = {"events": int(len(v)), "mean_excess": float(v.mean()) if len(v) else float("nan"),
                                   "hit_rate": float((v > 0).mean()) if len(v) else float("nan")}
    return out


def chain_members() -> pd.DataFrame:
    """Anonymized instrument x chain industry (1 = tagged), from data/alpha_tw/groups_chain.csv."""
    groups = pd.read_csv(DATA_DIR / "alpha_tw" / "groups_chain.csv", dtype={"instrument": str})
    sid = pd.read_csv(DATA_DIR / "alpha_tw" / "coder_map.csv", dtype={"instrument": str}).set_index("instrument")["sid"]
    tags = groups["all_groups"].fillna("").str.split("|")
    tags = [t if t != [""] else [g] for t, g in zip(tags, groups["group"])]
    long = pd.DataFrame({"instrument": groups["instrument"].map(sid), "group": tags}).explode("group")
    return pd.crosstab(long["instrument"], long["group"]).clip(upper=1).astype(float)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--panel", default=str(DATA_DIR / "alpha_tw" / "coder_full" / "daily_pv.h5"))
    parser.add_argument("--json", default=None)
    parser.add_argument("--multi", action="store_true", help="Every chain industry a stock is tagged with counts")
    args = parser.parse_args()
    panel = pd.read_hdf(args.panel, key="data")
    w = Wide(panel, chain_members() if args.multi else None)
    library = {k: ev.BASELINES[k](panel) for k in ("rev_accel", "rev_yoy", "mom60_skip5")}
    results = {}
    print("HOLDOUT numbers below are not a clean test (ideas written after it) and never go into a prompt.")
    for window in ("research", "holdout"):
        print(f"\n== {window}")
        print(f"{'idea':<26} IC1(t)        IC5(t)        IC20(t)       ann.ex  24d-md  win  turn  beta  corr")
        for name, (label, formula) in IDEAS.items():
            m = ev.evaluate(ev._long(formula(w)), panel, window=window, library=library)
            passed, reasons = ev.gate(m, ev.HOLDOUT_GATE if window == "holdout" else ev.DEFAULT_GATE)
            m["gate"] = {"passed": passed, "reasons": reasons}
            m["label"] = label
            results.setdefault(name, {})[window] = m
            ic = m["ic"]
            corr = max((abs(v) for v in m["library_corr"].values()), default=float("nan"))
            print(f"{name:<26} " + "  ".join(f"{ic[h]['mean']:+.3f}({ic[h]['t_nw']:+.1f})" for h in ("1", "5", "20"))
                  + f"  {m['book']['ann_excess_net']:+6.1%} {m['windows_24d']['median']:+.2%} "
                  f"{m['windows_24d']['win_rate']:4.0%} {m['book']['turnover_per_rebalance']:4.0%} "
                  f"{m['book']['beta_vs_b0']:5.2f} {corr:5.2f}  {'PASS' if passed else ''}")
        events = rebound_events(w, panel["$vwap"].unstack("instrument").reindex_like(w.close), window)
        results.setdefault("B12_events", {})[window] = events
        print("B12 events:", {k: f"n={v['events']} mean={v['mean_excess']:+.2%} hit={v['hit_rate']:.0%}"
                              for k, v in events.items()})
    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
