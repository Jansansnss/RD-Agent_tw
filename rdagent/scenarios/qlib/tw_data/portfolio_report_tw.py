"""
Formal report for a strategy that hands over daily holdings instead of a score per stock: kind
"portfolio" of tw-factor-report/1 (docs/tw/FACTOR_REPORT.md). Same two outputs, windows, benchmarks
and rules as factor_report_tw, whose helpers it uses.

A teammate sends weights.csv with datetime, instrument, weight: target weights (share of NAV)
decided after the close of `datetime`. The book trades to them at the next trading day's VWAP,
pays 0.1425% per side plus 0.3% tax on sells, and keeps the rest as cash. Days missing from the
file mean no trade, so holdings drift. A name without a VWAP on the trade day can be neither bought
nor sold, as in the contest.

There is no score, so no IC. Its place is taken by the selection test: on every day, the held
names' VWAP-to-VWAP return over 1/5/10/20 days, weighted as held, minus the universe mean, with a
Newey-West t; split by size, within industry and by market direction like the factor report. Each
decision day is also checked against the contest's rules (20-30 names, 10% per stock, 2330 25%,
cash under 25%, universe names only) and the v2 plan's buffers.

    python -m rdagent.scenarios.qlib.tw_data.portfolio_report_tw --weights mine.csv --declaration mine.yaml \
        --custom_tests mine_tests.yaml --holdout status
"""

import argparse
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

try:
    from rdagent.scenarios.qlib.tw_data import alpha_eval_tw as ev
    from rdagent.scenarios.qlib.tw_data import factor_report_tw as fr
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import alpha_eval_tw as ev
    import factor_report_tw as fr

GATE = {"horizon": 5, "min_t_nw": 3.0, "min_years_positive": 3, "min_window_median": 0.0}
HOLDOUT_GATE = {**GATE, "min_t_nw": 2.0, "min_years_positive": 0}
TILT = 0.3  # holdings' average percentile beyond 0.5 +/- this: a clear style tilt
MAX_TURNOVER = 0.5 * fr.TD / 5  # times a year: the factor report's flag, 50% every 5 days
CHECK_ZH = {"names": "持股 20–30 檔", "max_weight": "單檔 ≤ 10%（2330 以外）", "max_weight_2330": "2330 ≤ 25%",
            "cash": "現金 < 25%", "universe": "只買當月 150 檔"}
BUFFER_ZH = {"names": "22–28 檔", "max_weight": "單檔 ≤ 9%", "max_weight_2330": "2330 ≤ 23%", "cash": "現金 2–12%"}
RULE_NOTE = ("判定規則固定：支持＝方向對且 |t| ≥ 2；方向對、不顯著＝0 < t < 2；相反＝反向且 |t| ≥ 2。"
             "這裡的效果是持股之後相對股票池的超額（依持股權重），不是 IC。"
             "最強持有期看持股超額在哪個持有期最高（相鄰一格算方向對）。"
             "小型／大型：持股在各半股票池裡的超額；產業中性＝持股減同族群平均；大盤上漲＝股票池同期平均報酬 > 0。"
             "預測若在看過結果後才寫，這張表只能當描述。")
HOW_TO_READ = [
    fr.HOW_TO_READ[0],
    fr.HOW_TO_READ[1].replace("ic 是 Spearman 相關；", ""),
    fr.HOW_TO_READ[2],
    "這是策略報告（kind = portfolio）：book 照作者交的每日目標權重，隔日 VWAP 成交、扣實際成本，其餘是現金。"
    "沒有 IC；選股能力看 selection（持股依權重的 h 日報酬減當月 150 檔平均），風格看 style_exposure"
    "（持股的平均百分位，0.5 為中性），比賽規則看 contest_rules。",
    fr.HOW_TO_READ[4],
]


# --------------------------------------------------------------------------- weights and the book


def read_weights(path: Path, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """weights.csv -> decision day x stock code target weights (0 = not held). A decision dated on a
    non-trading day counts as made on the last trading day before it."""
    df = pd.read_csv(path, dtype={"instrument": str, "stock_id": str})
    df = df.rename(columns={"date": "datetime", "stock_id": "instrument"})
    missing = {"datetime", "instrument", "weight"} - set(df.columns)
    if missing:
        raise SystemExit(f"{path}: missing columns {sorted(missing)}; need datetime, instrument, weight")
    df["datetime"] = pd.to_datetime(df["datetime"])
    df["weight"] = df["weight"].astype(float)
    if (df["weight"] < 0).any():
        raise SystemExit(f"{path}: negative weights; the contest is long-only")
    at = dates.searchsorted(df["datetime"].to_numpy(), side="right") - 1
    df = df[at >= 0].assign(day=dates[at[at >= 0]].to_numpy())
    df = df[df["datetime"] == df.groupby("day")["datetime"].transform("max")]  # the day's last decision
    W = df.pivot_table(index="day", columns="instrument", values="weight", aggfunc="sum").fillna(0.0)
    over = W.sum(axis=1) > 1 + 1e-6
    if over.any():
        raise SystemExit(f"{path}: weights add up to more than 1 on {int(over.sum())} days (first "
                         f"{over.idxmax().date()}); a weight is a share of NAV, e.g. 0.042 for 4.2%")
    return W.rename_axis(index="datetime")


def simulate(W: pd.DataFrame, ctx: fr.Ctx, i0: int, i1: int) -> pd.DataFrame | None:
    """Daily book from the target weights, contest timing and costs. Each row is earned from VWAP(j)
    to VWAP(j+1) and dated j+1 like factor_report_tw.end_dated; B0 is the universe equal-weighted.
    The window starts fresh: it trades into the last decision made before it."""
    dates, names = ctx.dates, ctx.vwap.columns
    px = ctx.vwap.ffill()
    step = (px.shift(-1) / px - 1).to_numpy()
    tradable = ctx.vwap.notna().to_numpy()
    mask_v = ctx.mask.to_numpy()
    col = {c: k for k, c in enumerate(names)}
    targets = {dates.get_loc(d): row[row > 0] for d, row in W.iterrows()}
    before = [k for k in targets if k < i0]
    inside = sorted(k for k in targets if i0 <= k < i1 - 1)
    if before:
        start, first = i0, max(before)
    elif inside:
        start, first = inside[0] + 1, inside[0]
    else:
        return None
    w = pd.Series(dtype=float)
    rows = []
    for j in range(max(start, 1), i1 - 1):
        cost = turnover = 0.0
        k = j - 1 if (j - 1) in targets else (first if j == start else None)
        if k is not None:
            target = targets[k][[c in col for c in targets[k].index]]  # codes outside the panel cannot trade
            ok = {c: bool(tradable[j, col[c]]) for c in target.index.union(w.index)}
            stuck = w[[not ok[c] for c in w.index]]  # held but no VWAP today: cannot sell
            buy = target[[ok[c] for c in target.index]]
            room, total = max(1.0 - float(stuck.sum()), 0.0), float(buy.sum())
            if total > room:
                buy = buy * (room / total)
            new = pd.concat([buy, stuck])
            new = new[new > 0]
            diff = new.sub(w, fill_value=0.0)
            buys, sells = float(diff.clip(lower=0).sum()), float((-diff).clip(lower=0).sum())
            cost, turnover = buys * ev.COST_BUY + sells * ev.COST_SELL, (buys + sells) / 2
            w = new
        r = pd.Series(step[j], index=names).reindex(w.index).fillna(0.0)
        gross = float((w * r).sum())
        if len(w):
            w = w * (1 + r) / (1 + gross)
        uni = mask_v[j] & ~np.isnan(step[j])
        bench = float(step[j][uni].mean()) if uni.any() else np.nan
        rows.append((dates[j + 1], gross, cost, gross - cost, bench, turnover, len(w), 1.0 - float(w.sum())))
    if not rows:
        return None
    book = pd.DataFrame(rows, columns=["date", "gross", "cost", "net", "bench", "turnover", "held", "cash"])
    book = book.set_index("date")
    book["excess"] = book["net"] - book["bench"]
    return book


# --------------------------------------------------------------------------- what the holdings did


def held_daily(W: pd.DataFrame, ctx: fr.Ctx) -> pd.DataFrame:
    """Target weights carried from each decision to every trading day until the next one."""
    return W.reindex(ctx.dates, method="ffill").reindex(columns=ctx.vwap.columns).fillna(0.0)


def group_mean(F: pd.DataFrame, ctx: fr.Ctx) -> pd.DataFrame:
    """Each stock's industry-group mean of F over the day's universe (groups of 3+ names). Groups are
    one per stock, a 2026-10 snapshot (D30, D31)."""
    labels = ctx.ind.ffill().iloc[-1].reindex(F.columns).to_numpy()
    by = F.where(ctx.mask).T.groupby(labels)
    mean = by.mean().where(by.count() >= 3)
    out = mean.reindex(labels).T
    out.columns = F.columns
    return out


def selection(H: pd.DataFrame, ctx: fr.Ctx, i0: int, i1: int, h: int) -> tuple[dict, dict]:
    """Per horizon, the held names' forward return weighted as held, minus the universe mean, on each
    day whose exit stays in the window (Newey-West lags = horizon, as for the IC). At the declared
    horizon also the factor report's robustness cuts."""
    pos = pd.Series(np.arange(len(ctx.dates)), index=ctx.dates)
    out, robust = {}, {}
    for hh in ev.HORIZONS:
        F = ev.forward_returns(ctx.vwap, hh)
        M = ctx.mask & F.notna()
        Fu, Fz = F.where(M), F.fillna(0.0)
        base = Fu.mean(axis=1)
        V = (H > 0) & F.notna()
        Hv = H.where(V, 0.0)
        wsum = Hv.sum(axis=1).replace(0, np.nan)
        rows = (pos >= i0) & (pos + 1 + hh < i1) & wsum.notna() & (M.sum(axis=1) >= ev.MIN_NAMES)
        s = ((Hv * Fz).sum(axis=1) / wsum - base)[rows]
        ew = (F.where(V).mean(axis=1) - base)[rows]
        hit = ((F.gt(Fu.median(axis=1), axis=0) & V).sum(axis=1) / V.sum(axis=1).replace(0, np.nan))[rows]
        years = s.groupby(s.index.year).agg(["mean", "count"])
        std = s.std()
        out[str(hh)] = {"mean": float(s.mean()), "t_nw": ev.nw_tstat(s, lags=max(hh, 1)), "ew": float(ew.mean()),
                        "hit": float(hit.mean()), "days": int(len(s)),
                        "icir": float(s.mean() / std) if std > 0 else float("nan"),
                        "pos_rate": float((s > 0).mean()) if len(s) else float("nan"),
                        "by_year": {int(y): {"mean": float(v["mean"]), "days": int(v["count"])}
                                    for y, v in years.iterrows()}}
        if hh != h:
            continue
        median_mv = ctx.mv.where(ctx.mask).median(axis=1)

        def half(sel: pd.DataFrame) -> pd.Series:
            Hs = Hv.where(sel, 0.0)
            return ((Hs * Fz).sum(axis=1) / Hs.sum(axis=1).replace(0, np.nan) - F.where(M & sel).mean(axis=1))[rows]

        small, large = half(ctx.mv.lt(median_mv, axis=0)), half(ctx.mv.ge(median_mv, axis=0))
        rel = F - group_mean(F, ctx)
        Hr = Hv.where(V & rel.notna(), 0.0)
        neutral = ((Hr * rel.fillna(0.0)).sum(axis=1) / Hr.sum(axis=1).replace(0, np.nan))[rows]
        up = (base > 0)[rows]
        summary = lambda x: {"mean": float(x.mean()), "t": ev.nw_tstat(x.dropna(), lags=max(h, 1)),  # noqa: E731
                             "days": int(x.notna().sum())}
        robust = {"all": summary(s), "small": summary(small), "large": summary(large),
                  "small_minus_large_t": ev.nw_tstat((small - large).dropna(), lags=max(h, 1)),
                  "industry_neutral": summary(neutral), "market_up": summary(s[up]), "market_down": summary(s[~up])}
    return out, robust


def style_exposure(H: pd.DataFrame, ctx: fr.Ctx, styles: dict, i0: int, i1: int) -> dict[str, float]:
    """Where the holdings sit in each plain style: weighted mean percentile in the day's universe
    (0.5 = neutral), averaged over the window's days."""
    pos = pd.Series(np.arange(len(ctx.dates)), index=ctx.dates)
    rows = (pos >= i0) & (pos < i1) & (H.sum(axis=1) > 0)
    out = {}
    for key, values in styles.items():
        pct = ev._to_wide(values, ctx.vwap).where(ctx.mask).rank(axis=1, pct=True)
        Hk = H.where((H > 0) & pct.notna(), 0.0)
        exposure = (Hk * pct.fillna(0.0)).sum(axis=1) / Hk.sum(axis=1).replace(0, np.nan)
        out[key] = float(exposure[rows].mean())
    return out


def decision_days(W: pd.DataFrame, ctx: fr.Ctx, i0: int, i1: int) -> list[pd.Timestamp]:
    return [d for d in W.index if i0 <= ctx.dates.get_loc(d) < i1]


def holdings(W: pd.DataFrame, ctx: fr.Ctx, days: list, k: int = 30) -> dict:
    """Names, cash, largest weight, size percentile and industry mix over the decision days, and the
    holdings of the last decision."""
    mix, size, names, cash, largest = {}, [], [], [], []
    for d in days:
        held = W.loc[d][W.loc[d] > 0]
        names.append(len(held))
        cash.append(1.0 - float(held.sum()))
        largest.append(float(held.max()) if len(held) else 0.0)
        pct = ctx.mv.loc[d].where(ctx.mask.loc[d]).rank(pct=True).reindex(held.index)
        ok = pct.notna()
        if ok.any():
            size.append(float((held[ok] * pct[ok]).sum() / held[ok].sum()))
        for code, weight in held.items():
            group = ctx.group.get(code, "?")
            mix[group] = mix.get(group, 0.0) + float(weight)
    total = sum(mix.values()) or 1.0
    last = days[-1]
    held = W.loc[last][W.loc[last] > 0].sort_values(ascending=False)
    size_rank = ctx.mv.loc[last].where(ctx.mask.loc[last]).rank(ascending=False)
    top = [{"code": c, "name": ctx.name.get(c, "?"), "group": ctx.group.get(c, "?"), "weight": float(w),
            "size_rank": int(size_rank[c]) if c in size_rank.index and pd.notna(size_rank[c]) else -1}
           for c, w in held.head(k).items()]
    return {"avg_names": float(np.mean(names)), "avg_cash": float(np.mean(cash)), "max_weight": float(max(largest)),
            "size_percentile": float(np.mean(size)) if size else float("nan"),
            "industries": sorted(((g, s / total) for g, s in mix.items()), key=lambda kv: -kv[1]),
            "last_day": str(last.date()), "top": top}


def contest_check(W: pd.DataFrame, ctx: fr.Ctx, days: list) -> dict:
    """Each decision day against the contest's rules and the v2 plan's buffers."""
    rows = []
    for d in days:
        held = W.loc[d][W.loc[d] > 0]
        other = held.drop("2330", errors="ignore")
        outside = sum(c not in ctx.mask.columns or not bool(ctx.mask.at[d, c]) for c in held.index)
        rows.append({"n": len(held), "cash": 1.0 - float(held.sum()),
                     "largest": float(other.max()) if len(other) else 0.0,
                     "w2330": float(held.get("2330", 0.0)), "outside": outside})
    df = pd.DataFrame(rows, index=pd.DatetimeIndex(days))
    tol = 1e-6
    rules = {"names": (df["n"] < 20) | (df["n"] > 30), "max_weight": df["largest"] > 0.10 + tol,
             "max_weight_2330": df["w2330"] > 0.25 + tol, "cash": df["cash"] >= 0.25 - tol,
             "universe": df["outside"] > 0}
    buffers = {"names": (df["n"] < 22) | (df["n"] > 28), "max_weight": df["largest"] > 0.09 + tol,
               "max_weight_2330": df["w2330"] > 0.23 + tol,
               "cash": (df["cash"] < 0.02 - tol) | (df["cash"] > 0.12 + tol)}
    summary = lambda bad: {"days_pct": float(bad.mean()),  # noqa: E731
                           "first": str(bad.idxmax().date()) if bad.any() else None}
    return {"decision_days": len(df), "rules": {k: summary(v) for k, v in rules.items()},
            "buffers": {k: summary(v) for k, v in buffers.items()}, "passed": not any(v.any() for v in rules.values())}


def gate(r: dict, rules: dict) -> tuple[bool, list[str], list[str]]:
    """The factor gate's counterpart: selection t, positive full years, median 24-day excess."""
    h = rules["horizon"]
    sel = r["judge"]["ic"][str(h)]
    reasons, codes = [], []
    if not sel["t_nw"] >= rules["min_t_nw"]:
        reasons.append(f"{h} 日持股超額 t 值 {sel['t_nw']:.2f} < {rules['min_t_nw']}")
        codes.append("selection_t")
    years = sum(1 for v in sel["by_year"].values() if v["days"] >= 120 and v["mean"] > 0)
    if years < rules["min_years_positive"]:
        reasons.append(f"持股超額為正的完整年度 {years} 個 < {rules['min_years_positive']}")
        codes.append("years_positive")
    median = r["judge"]["windows_24d"]["median"]
    if not median > rules["min_window_median"]:
        reasons.append(f"24 日超額中位數 {median:+.2%} ≤ +0.00%")
        codes.append("window_median")
    return not reasons, reasons, codes


def analyze(ctx: fr.Ctx, W: pd.DataFrame, window: str, h: int, styles: dict, bench: fr.Bench) -> dict | None:
    i0, i1 = fr.bounds(ctx.dates, window)
    if i1 - i0 < 60:
        return None
    book = simulate(W, ctx, i0, i1)
    days = decision_days(W, ctx, i0, i1)
    if book is None or len(book) < 60 or not days:
        return None
    H = held_daily(W, ctx)
    sel, robust = selection(H, ctx, i0, i1, h)
    rebalances = book["turnover"][book["turnover"] > 0]
    judge = {"ic": sel, "windows_24d": ev.window_stats(book),
             "book": {"ann_excess_net": float(ev._ann(book["net"]) - ev._ann(book["bench"])),
                      "ann_cost": float(book["cost"].sum() * fr.TD / len(book)),
                      "turnover_per_rebalance": float(rebalances.mean()) if len(rebalances) else 0.0,
                      "ann_turnover": float(book["turnover"].sum() * fr.TD / len(book)),
                      "beta_vs_b0": float(book["net"].cov(book["bench"]) / book["bench"].var())}}
    facts = fr.book_facts(book, bench)
    by_year = sel[str(h)]["by_year"]
    facts["yearly"]["sel"] = [by_year.get(int(y), {}).get("mean", np.nan) for y in facts["yearly"].index]
    r = {"window": window, "start": str(book.index[0].date()), "end": str(book.index[-1].date()), "days": len(book),
         "decision_days": len(days), "judge": judge, "robust": robust, **facts,
         "style_exposure": style_exposure(H, ctx, styles, i0, i1), "holdings": holdings(W, ctx, days),
         "contest": contest_check(W, ctx, days)}
    rules = {**(HOLDOUT_GATE if window == "holdout" else GATE), "horizon": h}
    passed, reasons, codes = gate(r, rules)
    r["gate"] = {"passed": passed, "reasons": reasons, "codes": codes, "rules": rules}
    return r


def code_flags(res: dict, decl: dict, h: int) -> list[dict]:
    """The factor report's warnings, read off the holdings; plus the contest's rules."""
    r, f = res["research"], decl.get("factor", {})
    book = r["judge"]["book"]
    out = []

    def add(code: str, detail: str) -> None:
        out.append({"code": code, "detail": detail})

    for key, v in r["style_exposure"].items():
        if key != "mom20_crowding" and np.isfinite(v) and abs(v - 0.5) > TILT:
            add(f"tilt_{key}", f"持股的{fr.STYLE_LABEL[key]}平均在第 {v:.0%} 百分位（0.5 為中性），明顯偏向"
                               f"{'高' if v > 0.5 else '低'}的一邊")
    crowd = r["style_exposure"].get("mom20_crowding", float("nan"))
    if np.isfinite(crowd) and crowd > 0.5 + TILT:
        add("crowded_momentum", f"持股的短期動能平均在第 {crowd:.0%} 百分位：和多數隊伍的動能選股重疊")
    if not 0.8 <= book["beta_vs_b0"] <= 1.2:
        add("beta_tilt", f"組合對 B0 的 beta {book['beta_vs_b0']:.2f}：超額有一部分是大盤方向的押注")
    if book["ann_turnover"] > MAX_TURNOVER:
        add("high_turnover", f"年化換手 {book['ann_turnover']:.0f} 倍（警戒線 {MAX_TURNOVER:.0f} 倍＝因子組合每 5 日換手 50%），"
                             "成本吃掉大半優勢")
    if book["ann_excess_net"] > 0 >= r["ann_excess_2x_cost"]:
        add("cost_sensitive", "成本加倍後年化超額轉負")
    trials = fr.trials_of(f)
    hurdle = math.sqrt(2 * math.log(trials)) if trials > 1 else 0.0
    t = r["judge"]["ic"][str(h)]["t_nw"]
    if trials > 1 and abs(t) < hurdle + 1:
        add("data_mining_risk", f"嘗試 {trials} 次，純運氣的最大 |t| 約 {hurdle:.1f}；本策略 t {t:+.2f} 離它不到 1")
    broken = {k: v for k, v in r["contest"]["rules"].items() if v["days_pct"] > 0}
    if broken:
        add("contest_rule_violations", "；".join(f"{CHECK_ZH[k]}：{v['days_pct']:.0%} 的決策日違反（第一次 {v['first']}）"
                                               for k, v in broken.items()))
    if r["decision_days"] < 50:
        add("few_decisions", f"研究期只有 {r['decision_days']} 個決策日，證據有限")
    if not f.get("training"):
        add("training_unstated", "沒有說明模型或參數用哪段資料訓練、調整；若用到回測期間的資料，研究期的數字就是樣本內")
    if (decl.get("predictions") or {}).get("registered") != "before_results":
        add("predictions_not_preregistered", "預測不是在看結果之前寫下的，邏輯檢查只能當描述")
    if f.get("pod") not in fr.PODS:
        add("pod_missing", "沒有標五大因子之一，公開說明書的規則下不能使用")
    if "holdout" in res and not res["holdout"]["gate"]["passed"]:
        add("holdout_not_confirmed", "保留期沒有通過確認門檻")
    return out


# --------------------------------------------------------------------------- report.json


def window_json(r: dict, h: int) -> dict:
    P, R = fr.P, fr.R
    sel, hd, c = r["judge"]["ic"], r["holdings"], r["contest"]
    market = fr.market_json(r, "策略持股（扣成本）", "策略")
    return {
        "window": [r["start"], r["end"]], "trading_days": r["days"], "decision_days": r["decision_days"],
        "selection": {k: {"weighted_excess_pct": P(v["mean"], 3), "equal_weight_excess_pct": P(v["ew"], 3),
                          "hit_rate_pct": P(v["hit"], 1), "t": R(v["t_nw"], 2), "days": v["days"]}
                      for k, v in sel.items()},
        "selection_by_year": {str(y): P(v["mean"], 3) for y, v in sel[str(h)]["by_year"].items()},
        "book": {**fr.book_json(r), "ann_turnover_times": R(r["judge"]["book"]["ann_turnover"], 1)},
        "windows_24d": market["windows_24d"],
        "by_year": [{"year": int(y), "months": int(row["months"]), "book_pct": P(row["book"], 1),
                     "b0_pct": P(row["b0"], 1), "0050_pct": P(row["0050"], 1), "taiex_pct": P(row["TAIEX"], 1),
                     "excess_pct": P(row["excess"], 1), "selection_excess_pct": P(row["sel"], 2)}
                    for y, row in r["yearly"].iterrows()],
        "benchmarks": market["benchmarks"],
        "active_etfs": market["active_etfs"],
        "style_exposure": {k: R(v, 2) for k, v in r["style_exposure"].items()},
        "robustness": {k: ({"excess_pct": P(v["mean"], 3), "t": R(v["t"], 2)} if isinstance(v, dict) else R(v, 2))
                       for k, v in r["robust"].items()},
        "holdings": {"avg_names": R(hd["avg_names"], 1), "avg_cash_pct": P(hd["avg_cash"], 1),
                     "max_weight_pct": P(hd["max_weight"], 1), "size_percentile": R(hd["size_percentile"], 2),
                     "industries_pct": {g: P(s, 1) for g, s in hd["industries"][:6]}},
        "contest_rules": {"decision_days": c["decision_days"],
                          "violation_days_pct": {k: P(v["days_pct"], 1) for k, v in c["rules"].items()},
                          "buffer_days_pct": {k: P(v["days_pct"], 1) for k, v in c["buffers"].items()}},
    }


def build_json(res: dict, decl: dict, h: int, holdout_mode: str, prov: dict, checks: list, flags: list,
               custom: list) -> dict:
    r = res["research"]
    f = decl.get("factor", {})
    trials = fr.trials_of(f)
    c = r["contest"]
    out = {
        "schema": fr.SCHEMA,
        "kind": "portfolio",
        "how_to_read": HOW_TO_READ,
        "factor": {k: f.get(k) for k in fr.FACTOR_KEYS},
        "setup": {
            "universe": "每月底依指數權重（2024-10 前用市值）選上市前 100＋上櫃前 50，point-in-time",
            "input": "作者交的每日目標權重：t 日收盤後決定，t+1 日以 VWAP 成交；沒寫的日子不交易，其餘是現金",
            "label": f"VWAP(t+1) → VWAP(t+1+h)；宣告持有期 h = {h} 日",
            "costs": "買 0.1425%；賣 0.1425% + 0.3% 證交稅",
            "data_through": prov["data_through"],
            "holdout": holdout_mode,
        },
        "verdict_by_code": {
            "research_gate": {"passed": r["gate"]["passed"], "horizon_days": h, "failed": r["gate"]["reasons"]},
            "contest_rules": {"passed": c["passed"],
                              "violation_days_pct": {k: fr.P(v["days_pct"], 1) for k, v in c["rules"].items()}},
            "trials": trials,
            "luck_max_abs_t": fr.R(math.sqrt(2 * math.log(trials)), 2) if trials > 1 else 0.0,
            "flags": flags,
        },
        "facts": {"research": window_json(r, h)},
        "facts_custom": custom,
        "prediction_checks": [{**k, "status_zh": fr.STATUS_ZH[k["status"]]} for k in checks],
        "claims": {"thesis": decl.get("thesis"), "predictions": decl.get("predictions"),
                   "interpretation": fr.public(decl.get("interpretation"), holdout_mode)},
        "review": fr.public(decl.get("review"), holdout_mode),
        "provenance": prov,
    }
    if "holdout" in res:
        g = res["holdout"]["gate"]
        out["verdict_by_code"]["holdout"] = {"status": "confirmed" if g["passed"] else "not_confirmed",
                                             "failed_checks": g["codes"]}
        out["facts"]["holdout"] = (window_json(res["holdout"], h) if holdout_mode == "full"
                                   else {"withheld": "保留期數字只給人看；本檔只帶通過與否"})
    else:
        out["verdict_by_code"]["holdout"] = {"status": "not_scored"}
    return out


# --------------------------------------------------------------------------- report.html


def build_html(res: dict, decl: dict, h: int, holdout_mode: str, checks: list, flags: list, prov: dict,
               custom: list) -> str:
    esc, fpct, fnum, table, figure, _defs = fr.esc, fr.fpct, fr.fnum, fr.table, fr.figure, fr._defs
    f, th, pred = decl.get("factor", {}), decl.get("thesis") or {}, decl.get("predictions") or {}
    it, rv = decl.get("interpretation") or {}, decl.get("review") or {}
    r = res["research"]
    ho = res.get("holdout")
    name = f.get("name") or f.get("id")
    wins = [w for w in ("research", "holdout") if w in res]
    sel, w24, bk = r["judge"]["ic"], r["judge"]["windows_24d"], r["judge"]["book"]
    pb, hd = r["perf_book"], r["holdings"]

    kpis = [
        fr._kpi(f"{h} 日持股超額 t 值", fnum(sel[str(h)]["t_nw"], 2),
                f"超額 {fpct(sel[str(h)]['mean'], 2)} · 門檻 {GATE['min_t_nw']:.1f}"),
        fr._kpi("24 日超額中位數", fpct(w24["median"], 2),
                f"勝率 {fpct(w24['win_rate'], 0, False)} · 最差 10% {fpct(w24['p10'], 1)}"),
        fr._kpi("年化超額（對 B0）", fpct(bk["ann_excess_net"], 1), f"扣成本；IR {fnum(r['vs_b0']['ir'], 2)}"),
        fr._kpi("報酬／波動", fnum(pb["ret_to_vol"], 2),
                f"年化 {fpct(pb['ann_return'], 1)} · 波動 {fpct(pb['ann_vol'], 1, False)}"),
        fr._kpi("最大回撤", fpct(pb["max_dd"], 1), f"B0 {fpct(r['perf_b0']['max_dd'], 1)}"),
        fr._kpi("Beta（對 B0）", fnum(bk["beta_vs_b0"], 2),
                f"平均 {hd['avg_names']:.0f} 檔 · 現金 {fpct(hd['avg_cash'], 0, False)}"),
    ]
    chips = fr.chips_common(f, it, pred, r, ho, h)
    chips.insert(3, fr._chip("good", "✓", "符合比賽規則") if r["contest"]["passed"]
                 else fr._chip("bad", "✗", "違反比賽規則"))
    head = fr.masthead("策略報告", f, name, res, prov, chips, kpis,
                       "KPI 皆為研究期；組合照作者交的每日目標權重，隔日 VWAP 成交、扣實際成本，其餘是現金；B0＝當月 150 檔等權。")

    # 3 selection
    cats = [f"{hh} 日" for hh in ev.HORIZONS]
    tips = [f"t {fnum(sel[str(hh)]['t_nw'], 2)} · 等權 {fpct(sel[str(hh)]['ew'], 2)}" for hh in ev.HORIZONS]
    sel_rows = [[f"{hh} 日", fpct(sel[str(hh)]["mean"], 3), fpct(sel[str(hh)]["ew"], 3),
                 fpct(sel[str(hh)]["hit"], 1, False), fnum(sel[str(hh)]["t_nw"], 2), sel[str(hh)]["days"]]
                for hh in ev.HORIZONS]
    by_year = sel[str(h)]["by_year"]
    yr_cats, yr_vals = [str(y) for y in by_year], [v["mean"] for v in by_year.values()]
    s3 = '<div class="grid2">' + figure(
        "持股之後的超額", "研究期；持股依權重的報酬減當月 150 檔平均，滑過看 t 值（數字見右下表）",
        fr.column_chart("持股超額", cats, [sel[str(hh)]["mean"] for hh in ev.HORIZONS], "pct", ["s1"] * 4, tips))
    s3 += figure(f"逐年持股超額（{h} 日）", "研究期；紅＝正、綠＝負（台股慣例）",
                 fr.column_chart("逐年持股超額", yr_cats, yr_vals, "pct",
                                 ["pos" if (v or 0) > 0 else "neg" for v in yr_vals],
                                 [f"{by_year[int(y)]['days']} 天" for y in yr_cats]),
                 table=table(["年", "持股超額", "天數"], [[y, fpct(v["mean"], 3), v["days"]] for y, v in by_year.items()]))
    s3 += ('<div class="box wide"><h3 style="margin-top:0">各持有期（研究期）</h3>'
           + table(["持有期", "依權重超額", "等權超額", "贏過股票池中位數", "t（NW）", "天數"], sel_rows)
           + '<p class="notes">每天用最近一次決策的目標權重；超額＝持股之後的 VWAP 報酬減當月 150 檔平均，'
             "沒有扣成本（成本在 §4）。「贏過股票池中位數」是持股裡報酬高於當天股票池中位數的比例。</p></div></div>")

    # 6 holdings
    exp = r["style_exposure"]
    ho_exp = ho["style_exposure"] if ho else {}
    s6 = '<div class="grid2">' + figure(
        "持股的風格位置", "研究期；持股在當月 150 檔裡的加權平均百分位，0.5 為中性、1 為最高",
        fr.bar_chart("風格位置", [fr.STYLE_LABEL[k] for k in exp], [v - 0.5 for v in exp.values()], "tilt",
                     ["s1"] * len(exp), [f"平均第 {v:.0%} 百分位" for v in exp.values()], label_w=210),
        table=table(["風格", "研究期"] + (["保留期"] if ho else []),
                    [[fr.STYLE_LABEL[k], fnum(v, 2)] + ([fnum(ho_exp.get(k), 2)] if ho else [])
                     for k, v in exp.items()]))
    s6 += ('<div class="box"><h3 style="margin-top:0">持股概況（研究期的決策日）</h3>' + _defs([
        ("決策日", esc(f"{r['decision_days']} 天")),
        ("平均持股", esc(f"{hd['avg_names']:.1f} 檔")),
        ("平均現金", esc(fpct(hd["avg_cash"], 1, False))),
        ("單檔最大權重", esc(fpct(hd["max_weight"], 1, False))),
        ("年化換手", esc(f"{bk['ann_turnover']:.1f} 倍（有交易的日子平均換手 {fpct(bk['turnover_per_rebalance'], 0, False)}）")),
        ("平均市值百分位", esc(f"{fnum(hd['size_percentile'], 2)}（1＝最大）")),
    ]) + table(["族群", "權重占比"], [[g, fpct(s, 1, False)] for g, s in hd["industries"][:8]]) + "</div></div>")
    s6 += (f'<div class="box"><h3 style="margin-top:0">{esc(hd["last_day"])} 的持股（研究期最後一次決策）</h3>'
           + table(["代號", "名稱", "族群", "權重", "市值排名"],
                   [[t["code"], t["name"], t["group"], fpct(t["weight"], 1, False), t["size_rank"]] for t in hd["top"]],
                   "lllrr") + "</div>")

    # 7 robustness and the contest's rules
    labels = [("all", "全部"), ("small", "小型半（市值低於中位數）"), ("large", "大型半"), ("industry_neutral", "產業中性"),
              ("market_up", "大盤上漲時"), ("market_down", "大盤下跌時")]
    rob_rows = [[label] + [x for w in wins
                           for x in (fpct(res[w]["robust"][key]["mean"], 3), fnum(res[w]["robust"][key]["t"], 2))]
                for key, label in labels]
    head7 = ["切法"] + [x for w in wins for x in (f"{fr.WINDOW_ZH[w]} 持股超額", "t")]
    gate_rows = [[fr.WINDOW_ZH[w], "✓ 通過" if res[w]["gate"]["passed"] else "✗ 未通過",
                  "；".join(res[w]["gate"]["reasons"]) or "—"] for w in wins]
    rule_rows = [[CHECK_ZH[k]] + [fpct(res[w]["contest"]["rules"][k]["days_pct"], 0, False) for w in wins]
                 + [r["contest"]["rules"][k]["first"] or "—"] for k in CHECK_ZH]
    buffer_rows = [[BUFFER_ZH[k]] + [fpct(res[w]["contest"]["buffers"][k]["days_pct"], 0, False) for w in wins]
                   for k in BUFFER_ZH]
    trials = fr.trials_of(f)
    luck = math.sqrt(2 * math.log(trials)) if trials > 1 else 0.0
    s7 = (table(head7, rob_rows)
          + f'<p class="notes">{h} 日持股超額。小型半減大型半的 t（研究期）：{fnum(r["robust"]["small_minus_large_t"], 2)}。</p>'
          + "<h3>門檻</h3>" + table(["期間", "結果", "沒過的項目"], gate_rows, "llw")
          + f'<p class="notes">研究期門檻（{h} 日）：持股超額 t ≥ {GATE["min_t_nw"]}、至少 {GATE["min_years_positive"]} '
            "個完整年度持股超額為正、24 日超額中位數 > 0。保留期只確認：t ≥ "
            f"{HOLDOUT_GATE['min_t_nw']}、24 日中位數 > 0。嘗試次數 N = {trials}；N 次嘗試下，純運氣的最大 |t| 約 "
            f"{luck:.1f}（√(2 ln N)）。</p>"
          + "<h3>比賽規則（每個決策日的目標權重）</h3>"
          + table(["規則"] + [f"{fr.WINDOW_ZH[w]}違反的決策日" for w in wins] + ["研究期第一次"], rule_rows)
          + "<h3>v2 作戰規劃的緩衝</h3>"
          + table(["緩衝"] + [f"{fr.WINDOW_ZH[w]}超出的決策日" for w in wins], buffer_rows)
          + '<p class="notes">比賽規則：只做多、20–30 檔、單檔 ≤ 10%（2330 ≤ 25%）、現金 < 25%、只買當月股票池'
            "（歷史期間用每月重選的 150 檔；比賽期間是官方名單）。緩衝是 v2 作戰規劃自己留的安全邊際，超出不算違規。</p>")

    sections = [("definition", "1", "定義與資料時點", "", fr.section_definition(f, name, h, kind="portfolio")),
                ("logic", "2", "經濟邏輯", "回測賺錢不代表機制成立。這裡看作者在看結果前寫下的機制與預測，再由程式逐條核對。",
                 fr.section_logic(th, checks, RULE_NOTE)),
                ("selection", "3", "選股能力", "持股之後是否真的比同一批 150 檔好，以及效果在多長的持有期最強。", s3),
                ("backtest", "4", "回測：照作者的持股權重", "隔日 VWAP 成交、扣實際成本、沒交易的日子持股隨價格漂移；跟同一批 150 檔的等權（B0）比。",
                 fr.section_backtest(res, wins, ho, "策略持股", "策略組合", f"持股超額（{h} 日）",
                                     lambda row: fpct(row["sel"], 2))),
                ("benchmarks", "5", "同期比較：大盤與主動式 ETF", "同一段日子，跟 0050、加權指數、櫃買指數與投資台股的主動式 ETF 比。",
                 fr.section_benchmarks(res, wins, "策略持股（扣成本）", "策略")),
                ("holdings", "6", "它買什麼", "持股偏向哪些風格與族群，平均持有幾檔、留多少現金。", s6),
                ("robustness", "7", "穩健性與比賽規則", "換個切法還在不在，以及每個決策日是否符合比賽規則。", s7),
                *([("custom", "7b", "自行測試（自評）", fr.CUSTOM_LEDE, fr.section_custom(custom))] if custom else []),
                ("verdict", "8", "解讀、風險與使用建議", "作者看過結果後的解讀（第二階段）、程式警訊與獨立審查。",
                 fr.section_verdict(it, rv, flags, ho)),
                ("provenance", "9", "可重現性", "", fr.section_provenance(prov, holdout_mode))]
    return fr.page(f"{name} 策略報告", head, sections)


# --------------------------------------------------------------------------- CLI


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--weights", required=True, help="CSV: datetime, instrument, weight (share of NAV)")
    parser.add_argument("--declaration", help="the author's YAML (factor_declare_prompt_zh.md, then stage 2)")
    parser.add_argument("--custom_tests", help="YAML/JSON list of the author's own tests (self-reported)")
    parser.add_argument("--horizon", type=int, choices=ev.HORIZONS,
                        help="default: the declaration's horizon_days, else 5")
    parser.add_argument("--holdout", default="none", choices=["none", "status", "full"])
    parser.add_argument("--as_of", help="cut the data at this date (YYYY-MM-DD) for a point-in-time report")
    parser.add_argument("--out", help="output folder; default data/alpha_tw/reports/<id>")
    parser.add_argument("--refresh_benchmarks", action="store_true")
    args = parser.parse_args()

    custom = fr.load_custom(args.custom_tests)
    panel = pd.read_hdf(fr.ALPHA_DIR / "alpha_panel.h5", key="data")
    if args.as_of:
        panel = panel[panel.index.get_level_values(0) <= pd.Timestamp(args.as_of)]
    ctx = fr.Ctx(panel)
    path = Path(args.weights)
    W = read_weights(path, ctx.dates)
    known = {"id": path.stem, "source": str(path)}
    decl = (yaml.safe_load(Path(args.declaration).read_text(encoding="utf-8")) if args.declaration else {}) or {}
    decl["factor"] = {**known, **(decl.get("factor") or {})}
    h = args.horizon or int(decl["factor"].get("horizon_days") or 5)
    if h not in ev.HORIZONS:
        raise SystemExit(f"horizon_days must be one of {ev.HORIZONS}, got {h}")
    decl["factor"]["horizon_days"] = h
    fid = re.sub(r"[^\w.-]", "_", str(decl["factor"].get("id") or "portfolio"))
    out = Path(args.out) if args.out else fr.ALPHA_DIR / "reports" / fid
    out.mkdir(parents=True, exist_ok=True)

    print(f"{fid}: {len(W)} decision days {W.index[0].date()} -> {W.index[-1].date()}, "
          f"{int((W > 0).sum(axis=1).mean())} names on average; horizon {h} days; holdout {args.holdout}")
    bench = fr.Bench(args.refresh_benchmarks)
    styles = fr.style_values(panel)
    res = {}
    for window in ("research", "holdout") if args.holdout != "none" else ("research",):
        r = analyze(ctx, W, window, h, styles, bench)
        if r:
            res[window] = r
    if "research" not in res:
        raise SystemExit("No usable research window: the weights need decisions before 2026-04-30 and 60+ trading days")
    checks = fr.check_predictions(decl.get("predictions") or {}, res["research"], h, label="持股超額",
                                  fmt=lambda v: fr.fpct(v, 2))
    flags = code_flags(res, decl, h)
    prov = fr.provenance(panel)
    prov["command"] = "python -m rdagent.scenarios.qlib.tw_data.portfolio_report_tw " + " ".join(sys.argv[1:])
    prov["generator"] = "tw_data/portfolio_report_tw.py"
    report = build_json(res, decl, h, args.holdout, prov, checks, flags, custom)

    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, separators=(",", ":"), default=str),
                                     encoding="utf-8")
    (out / "report.html").write_text(build_html(res, decl, h, args.holdout, checks, flags, prov, custom),
                                     encoding="utf-8")
    (out / "prompt_stage2.md").write_text(fr.fill_report_prompt(report, decl), encoding="utf-8")
    if not args.declaration:
        (out / "declaration.yaml").write_text(fr.declaration_template({**known, "direction": "權重由策略決定"}, h),
                                              encoding="utf-8")
    r = res["research"]
    print(f"research {r['start']} -> {r['end']}: {h}d selection {r['judge']['ic'][str(h)]['mean']:+.3%} "
          f"(t {r['judge']['ic'][str(h)]['t_nw']:+.2f}); 24d median {r['judge']['windows_24d']['median']:+.2%}; "
          f"gate {'PASS' if r['gate']['passed'] else 'fail: ' + '; '.join(r['gate']['reasons'])}; "
          f"contest rules {'ok' if r['contest']['passed'] else 'VIOLATED'}")
    if "holdout" in res:
        print("HOLDOUT scored: people may read it; report.json carries "
              + ("its numbers (--holdout full)." if args.holdout == "full" else "pass/fail only."))
    for x in flags:
        print(f"  flag {x['code']}: {x['detail']}")
    print(f"Wrote {out}: report.json (LLM), report.html (people), prompt_stage2.md"
          + ("" if args.declaration else ", declaration.yaml (fill it in, then rerun with --declaration)"))


if __name__ == "__main__":
    main()
