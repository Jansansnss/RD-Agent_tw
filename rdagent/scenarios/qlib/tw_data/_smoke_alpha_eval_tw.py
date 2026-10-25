"""
Synthetic-data checks for the alpha factory (alpha_eval_tw.py, dump_alpha_panel_tw.py).

No real data, qlib or HDF5 needed; runs in seconds:

    python -m rdagent.scenarios.qlib.tw_data._smoke_alpha_eval_tw
"""

import numpy as np
import pandas as pd

from rdagent.scenarios.qlib.tw_data import alpha_eval_tw as ev
from rdagent.scenarios.qlib.tw_data import dump_alpha_panel_tw as dp

RNG = np.random.default_rng(0)


def synthetic_panel(days: int = 800, names: int = 60, in_universe: int = 50) -> pd.DataFrame:
    dates = pd.bdate_range("2021-01-04", periods=days, name="datetime")
    codes = [str(1001 + i) for i in range(names)]
    close = pd.DataFrame(100 * np.exp(np.cumsum(RNG.normal(0, 0.02, (days, names)), axis=0)),
                         index=dates, columns=pd.Index(codes, name="instrument"))
    vwap = close * (1 + RNG.normal(0, 0.002, close.shape))
    member = pd.DataFrame(0.0, index=dates, columns=close.columns)
    member.iloc[:, :in_universe] = 1.0
    long = lambda w: w.stack(future_stack=True)  # noqa: E731
    return pd.DataFrame({"$close": long(close), "$vwap": long(vwap),
                         "$in_universe": long(member), "$in_official": long(member)})


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'ok' if ok else 'FAIL'}] {name}{': ' + detail if detail else ''}")
    return ok


def main() -> None:
    panel = synthetic_panel()
    vwap = panel["$vwap"].unstack("instrument")
    results = []

    # 1. An alpha that knows the 5-day future must score IC ~ 1 and beat B0.
    oracle = ev.forward_returns(vwap, 5).stack(future_stack=True).dropna()
    m = ev.evaluate(oracle, panel, window="all")
    results.append(check("oracle IC5 > 0.95", m["ic"]["5"]["mean"] > 0.95, f"{m['ic']['5']['mean']:.3f}"))
    results.append(check("oracle book beats B0", m["book"]["ann_excess_net"] > 0.5,
                         f"{m['book']['ann_excess_net']:+.0%}/yr"))
    results.append(check("oracle passes the gate", ev.gate(m)[0], "; ".join(ev.gate(m)[1])))

    # 2. Noise must look like noise and fail the gate.
    noise = pd.Series(RNG.normal(size=len(panel)), index=panel.index)
    m = ev.evaluate(noise, panel, window="all", library={"oracle": oracle})
    results.append(check("noise |IC5| < 0.03", abs(m["ic"]["5"]["mean"]) < 0.03, f"{m['ic']['5']['mean']:+.4f}"))
    results.append(check("noise fails the gate", not ev.gate(m)[0], "; ".join(ev.gate(m)[1])))

    # 3. Costs: building the book from cash costs exactly one buy commission.
    mask = panel["$in_universe"].unstack("instrument").eq(1)
    alpha_w = mask.astype(float).cumsum(axis=1)  # same ranking every day
    i0 = 1
    book = ev.topk_book(alpha_w, vwap, mask, i0, len(vwap) - 1, k=24, rebalance=5)
    results.append(check("first trade costs COST_BUY", np.isclose(book["cost"].iloc[0], ev.COST_BUY),
                         f"{book['cost'].iloc[0]:.6f}"))
    results.append(check("book holds 24 names", int(book["held"].iloc[-1]) == 24))

    # 4. Window edge: no 20-day label may end after the window's last day.
    ev.WINDOWS["edge"] = ("2021-03-01", "2021-12-31")
    m = ev.evaluate(oracle, panel, window="edge")
    dates = vwap.index
    last = dates.searchsorted(pd.Timestamp("2021-12-31"), side="right") - 1
    first = dates.searchsorted(pd.Timestamp("2021-03-01"))
    expected = max(0, (last - 20 - 1) - first + 1)
    results.append(check("IC20 days stop before the window end", m["ic"]["20"]["days"] == expected,
                         f"{m['ic']['20']['days']} vs {expected}"))

    # 5. Revenue timing: January revenue is unknown on Feb 9 and known on Feb 10.
    months = pd.period_range("2023-01", "2024-03", freq="M")
    rev = pd.DataFrame({"instrument": "2330", "revenue": np.arange(len(months)) * 10.0 + 100,
                        "revenue_year": months.year, "revenue_month": months.month})
    monthly = dp.revenue_monthly(rev)
    idx = pd.MultiIndex.from_product([pd.to_datetime(["2024-02-09", "2024-02-10"]), ["2330"]],
                                     names=["datetime", "instrument"])
    daily = dp.revenue_daily(monthly, idx)
    dec, jan = 100 + 11 * 10.0, 100 + 12 * 10.0
    results.append(check("Feb 9 still sees December revenue", daily["$rev"].iloc[0] == dec,
                         f"{daily['$rev'].iloc[0]}"))
    results.append(check("Feb 10 sees January revenue", daily["$rev"].iloc[1] == jan, f"{daily['$rev'].iloc[1]}"))
    results.append(check("January YoY vs Jan 2023", np.isclose(daily["$rev_yoy"].iloc[1], jan / 100 - 1),
                         f"{daily['$rev_yoy'].iloc[1]:.3f}"))

    # 6. Universe: top-n per market at each month end, used from the next trading day.
    cal = pd.bdate_range("2024-01-01", "2024-02-29")
    stocks = {"1101": "twse", "1102": "twse", "1103": "twse", "6101": "tpex", "6102": "tpex"}
    caps = {"1101": 300, "1102": 200, "1103": 100, "6101": 50, "6102": 80}
    mv = pd.DataFrame([(d, c, caps[c] * (3 if (c == "1103" and d.month == 2) else 1))
                       for d in cal for c in stocks], columns=["date", "instrument", "market_value"])
    snaps = pd.DataFrame([(c, pd.Timestamp("2020-01-01"), t) for c, t in stocks.items()],
                         columns=["instrument", "date", "type"])
    first_seen = pd.Series(pd.Timestamp("2024-01-01"), index=list(stocks))
    uni = dp.select_universe(mv, first_seen, pd.DatetimeIndex(cal), snaps, n_twse=2, n_tpex=1, min_history_days=0)
    jan = set(uni.loc[uni["month_end"] == pd.Timestamp("2024-01-31"), "instrument"])
    feb = set(uni.loc[uni["month_end"] == pd.Timestamp("2024-02-29"), "instrument"])
    results.append(check("January picks", jan == {"1101", "1102", "6102"}, str(sorted(jan))))
    results.append(check("February picks follow caps", feb == {"1101", "1103", "6102"}, str(sorted(feb))))
    idx = pd.MultiIndex.from_product([pd.to_datetime(["2024-01-31", "2024-02-01"]), ["1102"]],
                                     names=["datetime", "instrument"])
    flags = dp.membership(uni, idx)["$in_universe"].tolist()
    results.append(check("Jan pick starts Feb 1, not on Jan 31", flags == [0.0, 1.0], str(flags)))

    # 7. Market moves: a TPEx stock that moved to TWSE is TPEx before its new snapshot.
    snaps = pd.DataFrame({"instrument": ["6438", "6438"], "date": pd.to_datetime(["2021-01-21", "2026-05-11"]),
                          "type": ["tpex", "twse"]})
    keys = pd.DataFrame({"month_end": pd.to_datetime(["2020-12-31", "2023-06-30", "2026-06-30"]),
                         "instrument": "6438"})
    got = dp.market_at(snaps, keys).tolist()
    results.append(check("market as of each date", got == ["tpex", "tpex", "twse"], str(got)))

    # 8. Index weights: a stock off the emerging board counts from its first weight day, not
    #    from FinMind's snapshot date (which lagged ~9 months for 7769 in the real data); ranks
    #    follow weights, not raw market caps; weights fix a stale market type.
    cal = pd.bdate_range("2025-01-01", "2025-07-31")
    caps = {"1101": 100, "2633": 900, "6472": 50, "7769": 500}
    mv = pd.DataFrame([(d, c, cap) for d in cal for c, cap in caps.items()],
                      columns=["date", "instrument", "market_value"])
    snaps = pd.DataFrame({"instrument": ["1101", "2633", "6472", "7769", "7769"],
                          "date": pd.to_datetime(["2020-01-01"] * 3 + ["2025-01-02", "2026-10-22"]),
                          "type": ["twse", "twse", "tpex", "emerging", "twse"]})
    first_seen = pd.Series(pd.Timestamp("2024-06-03"), index=list(caps))  # emerging rows count here
    ends = cal.to_series().groupby(cal.to_period("M")).max()
    weights = [(d, c, wt, "twse") for d in ends for c, wt in {"1101": 3.0, "2633": 1.0, "6472": 2.0}.items()]
    weights += [(d, "7769", 5.0, "twse") for d in [pd.Timestamp("2025-03-03"), *ends[ends >= "2025-03-03"]]]
    weights = pd.DataFrame(weights, columns=["date", "instrument", "weight_per", "type"])
    uni = dp.select_universe(mv, first_seen, pd.DatetimeIndex(cal), snaps, n_twse=2, n_tpex=1,
                             min_history_days=60, index_weights=weights)
    picks = uni.groupby("month_end")["instrument"].apply(set)
    apr, jun = picks.get(pd.Timestamp("2025-04-30"), set()), picks.get(pd.Timestamp("2025-06-30"), set())
    results.append(check("ranked by weight; 7769 listed 43 days: not yet", apr == {"1101", "6472"},
                         str(sorted(apr))))
    results.append(check("7769 listed 86 days: in, as TWSE", jun == {"7769", "1101"} and
                         set(uni.loc[uni["instrument"] == "7769", "market"]) == {"twse"}, str(sorted(jun))))
    results.append(check("stale TPEx snapshot overridden by weights",
                         set(uni.loc[uni["instrument"] == "6472", "market"]) == {"twse"}))

    # 9. Coder data for the alpha loop: anonymized IDs, research window only, no contest-list flag.
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        research_end = pd.Timestamp(dp.RESEARCH_END)
        idx = pd.MultiIndex.from_product([[research_end, research_end + pd.Timedelta(days=1)], ["2330", "2454"]],
                                         names=["datetime", "instrument"])
        tiny = pd.DataFrame({"$close": [1.0, 2.0, 3.0, 4.0], "$in_official": 1.0, "$vwap_exact": 1.0}, index=idx)
        dp.write_coder_data(tiny, Path(tmp))
        coder = pd.read_hdf(Path(tmp) / "coder" / "daily_pv.h5", key="data")
        full = pd.read_hdf(Path(tmp) / "coder_full" / "daily_pv.h5", key="data")
    ok = (set(coder.index.get_level_values("instrument")) == {"S001", "S002"}
          and coder.index.get_level_values("datetime").max() == research_end
          and len(full) == 4 and "$in_official" not in coder.columns)
    results.append(check("coder data anonymized and cut at the research end", ok))

    # 10. The loop rejects code with literals (needs the rdagent package, i.e. the WSL env).
    try:
        from rdagent.scenarios.qlib.developer.alpha_runner_tw import literal_problems
    except ImportError as err:
        print(f"[skip] literal check: {err}")
    else:
        bad = literal_problems("x = df.xs('2330', level=1); y = df.loc['2024-01-02']; z = df.xs(\"S012\", level=1)")
        clean = literal_problems("df.groupby(level='instrument')['$close'].pct_change(20)")
        results.append(check("literal check", len(bad) == 3 and not clean, str(bad)))

    # 11. Round 2 onward: the proposal history prints the judge's dict result (upstream templates
    #     call exp.result.loc[...] and crashed the first 10-round run, 2026-10-24).
    try:
        from types import SimpleNamespace

        from rdagent.scenarios.qlib.proposal.alpha_proposal_tw import alpha_history, last_trial
    except ImportError as err:
        print(f"[skip] proposal history: {err}")
    else:
        metrics = {"ic": {"5": 0.03}, "ic_t": {"5": 3.4}, "windows_24d": {"median": 0.01},
                   "book_beta": 1.1, "max_library_corr": 0.2}
        rows = [{"factor": "a", "passed": True, "horizon": 5, "reasons": [], "metrics": metrics},
                {"factor": "b", "passed": False, "horizon": None, "reasons": ["the code contains a date"], "metrics": None}]
        exp = SimpleNamespace(hypothesis="h", sub_tasks=[], result={"factors": rows, "library_size": 3})
        fb = SimpleNamespace(observations="o", hypothesis_evaluation="e", decision=True, new_hypothesis="n", reason="r")
        trace = SimpleNamespace(hist=[(exp, fb), (SimpleNamespace(hypothesis="h2", sub_tasks=[], result=None), fb)])
        text, last = alpha_history(trace), last_trial(trace)
        results.append(check("proposal history from judge rows",
                             "a: PASS at 5d" in text and "b: fail; the code contains a date" in text
                             and "no factor produced values" in last and "New Hypothesis" in last))
        long_text = alpha_history(SimpleNamespace(hist=[(exp, fb)] * 12))
        results.append(check("old trials shrink to one line per factor",
                             long_text.count("# Trial ") == 10 and "trial 2 a: t5 +3.4" in long_text
                             and "trial 2 b: the code contains a date" in long_text))

    # 12. Two passes of one round that copy each other: only the higher-t one stays.
    try:
        from rdagent.scenarios.qlib.developer.alpha_runner_tw import dedupe_round
    except ImportError as err:
        print(f"[skip] same-round copies: {err}")
    else:
        base = pd.Series(RNG.normal(size=len(panel)), index=panel.index)
        other = pd.Series(RNG.normal(size=len(panel)), index=panel.index)
        passing = lambda name, t: {"factor": name, "passed": True, "horizon": 5, "reasons": [],  # noqa: E731
                                   "metrics": {"ic_t": {"5": t, "20": t}}}
        rows = [passing("copy", 3.5), passing("orig", 4.0), passing("new", 3.2)]
        dedupe_round(rows, {"copy": base * 2 + 1, "orig": base, "new": other}, panel)
        results.append(check("same-round copies are rejected",
                             [r["passed"] for r in rows] == [False, True, True] and "orig" in rows[0]["reasons"][0],
                             str([r["reasons"] for r in rows])))

    print(f"\n{sum(results)}/{len(results)} checks passed")
    if not all(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
