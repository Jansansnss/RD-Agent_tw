# TW Alpha Factory — RD-Agent as the AI CUP 2026 research lab

> Design doc, started 2026-10-22. Status lives in PROJECT_STATE.md and the "Alpha factory" line of
> TODO_GRAPH.md (nodes T7–T10, G3–G4, S5–S6). Why-entries: DECISIONS D15–D17.

RD-Agent stays an **offline lab**. It never runs in the contest's daily decision path. Its job is
to find alphas that a 20–30-name, long-only book on the contest's 150 stocks can use, to prove them
with a code-only judge, and to hand them over as plain code ("alpha cards") to the contest repo
(`AI-cup-etf-agent`, local `G:\AI_cup_2026`). The team plan ("ETF Agent 作戰規劃 v2", a Claude Doc)
already calls RD-Agent its 離線研究實驗室.

## 1. Contest facts that shape an alpha

| Fact | Consequence for alphas |
|---|---|
| 150 fixed stocks: top 100 TWSE + top 50 TPEx by market cap, end of July 2026 | Research universe = the same rule applied each month end (point-in-time); live = the official list |
| Long-only, 20–30 names, cap 10% (2330: 25%), cash < 25% | What matters is the top ~10–24 of the ranking, not the whole cross-section |
| Orders are sent before 08:55 and fill at **that day's VWAP**, even at limit up or down | Label = VWAP(t+1) → VWAP(t+1+h); no limit-up/down blocking |
| 0.1425% commission per side + 0.3% tax on sells (0.585% round trip) | Horizons of 5–20 days; daily-turnover alphas die on costs |
| Ranked on final NAV on 11/27 (top 6 of the 社會人士組); ties broken by MDD | Tournament payoff, see §8 |
| Prospectus declares five factors: 營收獲利動能, 價格動能, 資金流向, 事件催化, 風險控管 | Every alpha carries one of these as its pod tag, or it cannot be used |
| Each D-Plan cites source → observation → inference → decision → order | Every alpha needs a one-line observation template and a source authority |
| Humans may not decide trades; parameters and prompts count as code | Alphas ship only at a scheduled version (v1.1 on 11/2) with a new `code_version` |

## 2. Why the existing factor loop cannot do this

The TW factor loop (`factor_tw.py`) judges a factor by adding it to LightGBM on Alpha158 and running
qrun. The template does not match the contest:

| | `factor_template_tw/conf_combined_factors.yaml` | Contest |
|---|---|---|
| Universe | `market: all` — every TW stock plus ETFs | 150 stocks |
| Backtest book | `topk: 3, n_drop: 1` (left over from the 5-stock smoke test) | 20–30 names |
| Fill | `deal_price: close`; `$vwap` is an OHLC4 proxy | day VWAP |
| Limits | `limit_threshold: 0.099` blocks trades at the limit | every order fills |
| Label | next-day close to close | 5–20 day hold after costs |
| Benchmark | 2330 | 150 equal-weight (B0) |
| Data | no monthly revenue, financials or industry in `daily_pv.h5` | revenue drives two declared factors |
| Who picks SOTA | the LLM, from **test-period** metrics (`developer/feedback.py`) | test period must stay unseen |

Because of `topk: 3`, the May 2026 "SOTA" factors were judged on a 3-stock book. Treat them as
unproven.

## 3. Pipeline

```
factor_ideas.md + "which pod is weak"
        │
① Hypothesis (LLM): anonymized, no tickers, names or dates
        │
② Alpha code (CoSTEER): reads alpha_panel.h5 only; no stock-code or date literals
        │
③ Fast judge, alpha_eval_tw.evaluate(): pandas, seconds, research window only
        │
④ Gate in code, alpha_eval_tw.gate() ──fail──► reject log (the LLM sees the reasons)
        │ pass
⑤ Alpha library ── holdout check, once per batch, by a script outside the loop
        │
⑥ Alpha card → pod module in AI-cup-etf-agent (plain pandas, no RD-Agent import)
```

Step 1 (data panel + judge) is built: `tw_data/dump_alpha_panel_tw.py`, `tw_data/alpha_eval_tw.py`,
checked by `tw_data/_smoke_alpha_eval_tw.py`. Steps ②–⑥ are TODO nodes T8–T10.

## 4. Data panel (`dump_alpha_panel_tw.py` → `data/alpha_tw/`)

Rule: every value at `(datetime, instrument)` was known by the evening of `datetime`.

| Fields | Source | Known |
|---|---|---|
| `$open $high $low $close` (back-adjusted), `$volume $amount` (raw) | `ohlcv.csv` | after the close |
| `$vwap`, `$vwap_exact` | see below | after the close |
| `$market_value` | `market_value.csv` | after the close |
| `$foreign_net $trust_net $dealer_net` (shares) | `institutional.csv` | after the close |
| `$margin_balance $short_balance` | `margin.csv` | after the close |
| `$div_yield $pe_ratio $pb_ratio` | `valuation.csv` | after the close |
| `$issued_shares $foreign_held_pct` | `foreign_holdings.csv` | after the close |
| `$rev $rev_yoy $rev_3m_yoy $rev_12m_yoy $rev_accel $rev_age` | `month_revenue.csv` | the 10th of the next month (legal deadline) |
| `$in_universe $is_twse` | month-end index weights (from 2024-10) or market caps; listing dates | from the next trading day |
| `$in_official` | `--official_list` (the contest's 150) | — |
| `$industry_id` | `stock_info.csv` (specific industry over 電子工業) | static |

- **Universe:** at each month end, the top 100 TWSE and top 50 TPEx common stocks (4-digit codes
  starting 1–9, no TDRs, at least 60 trading days listed), held until the next month end. Ranking
  follows the contest's method: by TWSE/TPEx index weight where a month-end weight snapshot
  exists (FinMind: from 2024-10-30), by raw market cap before that. On 2026-07-31 this matches
  149/150 of the official list; the miss is 5371 中光電 vs its new holding company 3718. Raw caps
  rank differently (台灣高鐵: 90th by cap, 145th by weight). The fixed July-2026 list is only used
  live and for `--universe official`.
- **Listing dates:** `stock_info.csv` board records are dated when FinMind recorded them, not when
  a stock changed board (7769 鴻勁 listed in January 2026 but reads "emerging" until the
  2026-10-22 record). For stocks that came off the emerging board, the first index-weight day is
  the listing date; market type follows the weights when one is within 95 days (DECISIONS D19).
- **Revenue timing:** FinMind's `date` (1st of the next month) is not the release day. The panel
  uses the 10th, so early publishers are seen late. That is conservative: it never looks ahead.
- **VWAP caveat:** prices in `ohlcv.csv` are back-adjusted, but `$amount`/`$volume` are raw. So
  `amount/volume` is a raw VWAP. With `--raw_ohlcv data/tw_raw_full/ohlcv.csv` (TWSE-direct raw
  closes: TWSE only, to 2026-05-05) the VWAP is rescaled exactly. Elsewhere the adjusted typical
  price `(H+L+C)/3` stands in. `$vwap_exact` marks exact rows. S5 (raw `TaiwanStockPrice`) fixes
  this for good.
- **No trades** → `$vwap` is NaN. The contest voids orders without a VWAP, and the judge cannot
  trade those names that day.

## 5. Fast judge (`alpha_eval_tw.py`)

An alpha is a `(datetime, instrument)` series known on the evening of `datetime`; higher = buy.

| Metric | Definition |
|---|---|
| RankIC at 1/5/10/20 days | Spearman of alpha vs VWAP(t+1)→VWAP(t+1+h), universe only, ≥ 20 names; Newey-West t (lags = h); per-year means |
| Book | equal-weight top 24, rebalance every 5 days at next-day VWAP, keep holdings ranked ≤ 36, real costs; untradable names neither bought nor sold |
| `windows_24d` | rolling 24-day (contest length) compounded book minus B0: median, 10th/90th percentile, win rate |
| `top10_24d` | top 10 names' 24-day forward return minus the universe mean (v2's 前 10 檔 24 日超額), median and hit rate |
| `beta_vs_b0` | book beta to B0; the tournament layer needs it (§8) |
| `library_corr` | mean daily rank correlation with each library alpha; `mom20` doubles as the crowding proxy |
| `coverage` | share of universe names with a value |

Windows: **research** 2021-01-01 → 2025-06-30; **holdout** 2025-07-01 → latest; **all**. Signals
whose exit day falls after the window end are dropped, so no label crosses the boundary.

```bash
python -m rdagent.scenarios.qlib.tw_data.alpha_eval_tw --alpha mom20 --alpha rev_accel --library mom20
python -m rdagent.scenarios.qlib.tw_data.alpha_eval_tw --alpha_file result.h5 --window research
```

Baselines (`BASELINES`): `mom20`, `mom60_skip5`, `rev5`, `lowvol60`, `rev_yoy`, `rev_accel`,
`trust_flow5`, `trust_flow20`, `foreign_flow20`. They are the v2 hypotheses as plain formulas.

## 6. Gate (fixed before any run; `DEFAULT_GATE`)

| Check | Threshold | Why |
|---|---|---|
| IC t-stat (Newey-West) at the declared horizon | ≥ 3.0 | the loop tests hundreds of ideas; 2.0 lets chance through |
| Calendar years (≥ 120 IC days) with positive mean IC | ≥ 3 of the research years | one lucky regime is not an alpha |
| Median rolling 24-day net excess of the top-24 book | > 0 | must survive costs over the contest's length |
| Max \|correlation\| with the library | ≤ 0.6 | new information, not a copy |
| Coverage | ≥ 80% | must rank most of the universe |

The LLM never decides pass or fail. It reads the reasons and proposes the next idea. Every
judged alpha is logged, so the number of trials is known when results are reported.

The holdout only **confirms** a research pass (`HOLDOUT_GATE`): t ≥ 2.0, same sign, 24-day median
> 0, no year count (it is under a year long). The v2 plan's own thresholds (t > 2) stay right for
the team's few pre-registered hypotheses; t ≥ 3 is for the loop's many trials.

### Baseline verdict (2026-10-23, data to 2026-10-22)

The v2 hypotheses as baselines, point-in-time universe, top-24 book. Research = 2021-01-04 →
2025-06-30; holdout = 2025-07-01 → 2026-10-22. (These are human, pre-registered ideas, so scoring
them on the holdout is allowed; the holdout is now spent for them.) IC is at 20 days, t in brackets.

| Alpha (pod) | IC research | 24d median research | IC holdout | 24d median holdout | book beta holdout |
|---|---|---|---|---|---|
| `rev_accel` (營收獲利動能) | +0.023 (1.7) | +0.63% | +0.085 (4.6) | +3.34% | 1.23 |
| `rev_yoy` (營收獲利動能) | +0.004 (0.2) | +0.22% | +0.113 (3.5) | +2.68% | 1.26 |
| `mom20` (價格動能) | −0.006 (−0.3) | −0.76% | +0.064 (1.8) | +5.09% | 1.36 |
| `mom60_skip5` (價格動能) | −0.001 (−0.0) | −0.10% | +0.083 (1.6) | +4.02% | 1.49 |
| `foreign_flow20` (資金流向) | −0.006 (−0.5) | −0.90% | +0.063 (2.8) | +1.91% | 1.22 |
| `trust_flow20` (資金流向) | +0.004 (0.3) | −0.42% | +0.015 (0.8) | +1.50% | 1.28 |
| `trust_flow5` (資金流向) | −0.001 (−0.1) | −0.83% | +0.017 (1.3) | −0.01% | 1.28 |
| `lowvol60` (風險控管) | +0.052 (1.7) | +0.00% | −0.087 (−1.4) | −4.03% | 0.15 |
| `rev5` (reversal; IC at 1 day +0.025, t 4.1) | +0.002 (0.2) | −1.13% | −0.033 (−1.7) | −2.78% | 1.17 |

- Nothing passes the strict research gate. `rev_accel` is the one candidate: research IC t 2.7
  and 2.2 at 1 and 5 days (v2's own t > 2 bar for a single pre-registered test), holdout t 4.6 at
  20 days with 78% of 24-day windows won. `rev_yoy` confirms the revenue pod on the holdout.
- Momentum: the first run (data to 2026-05-08) showed a holdout t of 4.0. With five more months it
  fell to 1.8, the worst 10% of 24-day windows lost 7.7%, and the book carries beta 1.4–1.5.
  It is mostly the AI rally, and it can reverse fast.
- 投信 buying (`trust_flow*`), a v2 hypothesis, has no support in either window. Foreign buying
  works only on the holdout, which is a regime, not a confirmation.
- Short-term reversal is real at 1 day, but its 75% turnover per rebalance eats it.
- The May-data panel is kept in `data/alpha_tw_2026-05-08/`. Its numbers differ also because
  prices were re-anchored and the universe now includes the 2025–26 listings.

## 7. LLM look-ahead protocol

**Anonymizing prompts does not make a backtest safe.** It removes some leaks, not all:

- LLMs recall exact pre-cutoff values (returns, macro numbers, headlines). Masking names is weak,
  because models rebuild names and dates from small clues; after the cutoff, recall disappears
  ([Lopez-Lira, Tang & Zhu, "The Memorization Problem"](https://arxiv.org/html/2504.14765v2)).
- Masking can be pushed far ("entity neutering": mask and paraphrase until a second LLM cannot
  name the firm), but that targets per-firm text, not strategy ideas
  ([Mullins et al. seminar abstract](https://negocios.uchile.cl/events/seminario-academico-entity-neutering/);
  [Glasserman & Lin](https://arxiv.org/abs/2309.17322)).
- Here the LLM writes formulas, not per-stock forecasts. The leak is **hindsight taste**: it knows
  which styles won in 2021–2025 (AI supply chain, foreign buying, momentum). Field names such as
  三大法人 and 月營收 tell it the market is Taiwan, whatever we hide.

So the protocol:

1. **Clean window = after the model's training data.** DeepSeek's EU training summary for V4
   says some data was "collected approximately no later than May 2025"
   ([DeepSeek, public summary of training content](https://cdn.deepseek.com/policies/DeepSeek_Template_for_the_Public_Summary_of_Training_Content_for_GeneralPurpose_AI_model.pdf)).
   Research window ends 2025-06-30; holdout runs 2025-07-01 → today (~16 months). "Approximately"
   and "some data" mean the holdout is probably clean, not certainly clean.
2. **The model must not move under us.** `deepseek-chat` was retired on 2026-07-24
   ([DeepSeek change log](https://api-docs.deepseek.com/updates)); it had mapped to V4-Flash since
   2026-04-24. The current names are `deepseek-flash` (V4.1-Flash-1017 as of 2026-10-17, and it
   "points to the latest model") and `deepseek-v4-pro`
   ([pricing page](https://api-docs.deepseek.com/quick_start/pricing)). Log the served model string
   for every run. If a newer model has a later cutoff, move the research/holdout boundary.
3. **Code judges; the LLM never sees holdout numbers.** The holdout is scored by a separate script
   (`--window holdout`), once per batch that already passed the gate.
4. **No literals:** reject alpha code containing 4-digit stock codes or dates; prompts show no
   tickers, names or calendar dates.
5. **The live contest (10/27–11/27) is the only fully clean test.** Report live results per alpha.

## 8. Tournament layer (competition theory)

This belongs to the contest repo's ranking module (排名策略官), not to RD-Agent. It is here because
it changes what the factory must measure (beta, crowding, the top of the ranking).

### What the research says

| Finding | Source |
|---|---|
| When fewer than half the field wins, prefer more variance and lower correlation with rivals; this is the dominant best response | Gaba, Tsetlin & Winkler 2004, [Operations Research 52(3)](https://pubsonline.informs.org/doi/10.1287/opre.1030.0098) |
| Maximize variability in a weak position, minimize it in a strong one | Tsetlin, Gaba & Winkler 2004, J. Risk & Uncertainty (summarized in the [INFORMS listing](https://pubsonline.informs.org/doi/10.1287/opre.1030.0098) search results) |
| Against a benchmark: bold play when the game is unfavorable, timid play when favorable | [Browne 1999, Finance & Stochastics 3](https://ideas.repec.org/a/spr/finsto/v3y1999i3p275-294.html) |
| With a deadline, the probability-maximizing policy replicates a digital option (risk swells as time runs out while behind) | [Browne 1999, Adv. Appl. Prob.](https://www.cambridge.org/core/journals/advances-in-applied-probability/article/abs/reaching-goals-by-a-deadline-digital-options-and-continuoustime-active-portfolio-management/73CD9DEE7FCCD733988A72AFCE62573A) |
| Laggards raise **tracking error**, not total volatility | [Chen & Pennacchi 2009, JFQA](https://ideas.repec.org/a/cup/jfinqa/v44y2009i04p745-775_99.html) |
| Rivals react: if laggards gamble, an interim leader may have to gamble too; when close, managers gamble in opposite directions | Taylor 2003 (via [EFMA 2009](https://www.efmaefm.org/0EFMAMEETINGS/EFMA%20ANNUAL%20MEETINGS/2009-Milan/papers/632.pdf)); [Basak & Makarov 2014, J. Finance](https://ideas.repec.org/a/bla/jfinan/v69y2014i1p179-217.html) |
| Teams just behind the leaders take the most risk; ranked leaders underperform | [Genakos & Pagliero 2012, JPE](https://econpapers.repec.org/RePEc:cca:wpaper:196) |
| Shown a ranking, underperforming finance professionals take more risk, even without money at stake | [Kirchler, Lindner & Weitzel 2018, J. Finance](https://research-portal.uu.nl/en/publications/rankings-and-risk-taking-in-the-finance-industry/) |
| Modelling what opponents pick turned +50% into +350% in top-heavy fantasy contests | [Haugh & Singal 2021, Management Science](https://papers.ssrn.com/abstract=3393127) |
| Funds that add risk do worse; add risk only where conviction is highest | [Huang, Sialm & Zhang 2011, RFS](https://nber.org/papers/w14903) |
| In the M6 investment contest, extreme Sharpe ratios were not beyond chance | [Benchmarking M6 competitors](https://arxiv.org/pdf/2406.19105) |

### The beta question: "leading → lower beta, losing → raise beta"

Not as stated. The ranking is relative. Every rival is long-only on the same 150 stocks, so the
field's beta is about 1. A leader who cuts beta to 0.6 **adds** relative risk: it is a bet that the
market falls. The leader's job is to **cover**: hold the beta and the big tilts of the teams just
behind. The laggard's job is to **separate**: more stock-specific risk, and bets that do not move
with the leaders. Raising beta only helps a laggard who also has a market view.

Toy Monte Carlo (2026-10-22): 100 rivals over 24 days, 20,000 contests; top 6 wins (base rate
5.9%). 70% of rivals are steady (beta 0.85–1.05, 0.8%/day stock-specific risk), 30% aggressive
(beta 1.1–1.4, 2.0%/day, heavy on a shared "crowd" theme). From day 13, rivals ranked 7–20 gamble
(×1.5 risk). Market 1.3%/day with a random drift per contest. Switching costs up to 0.25% of NAV.
No skill. Policies switch on v2's z-score (±1).

| Policy | P(top 6) | market down | flat | up |
|---|---|---|---|---|
| Fixed (beta 1, 1.5%/day) | 6.2% | 7.9% | 6.5% | 4.4% |
| **Beta rule** (lead: beta 0.6; lose: beta 1.4) | **5.3%** | 6.4% | 5.5% | 4.0% |
| only "lead → beta 0.6" | 5.9% | 7.6% | 6.1% | 3.9% |
| only "lose → beta 1.4" | 5.6% | 6.6% | 5.9% | 4.5% |
| v2 modes 守/攻/衝 on stock-specific risk (1.0/1.75/2.5%) | 13.3% | 16.0% | 13.5% | 10.5% |
| v2 + beta 1.35 and off-crowd when behind | 13.2% | 14.6% | 13.1% | 11.8% |
| Cover/bold (lead: hug the field; lose: beta 1.35, off-crowd, 2.5%) | 13.3% | 13.0% | 13.4% | 13.5% |
| Smooth digital-option dial (untuned) | 11.1% | 11.9% | 11.3% | 10.0% |

With a small skill (+0.05%/day) the order is the same (fixed 8.3%, beta rule 7.1%, v2 16.3%,
cover/bold 16.2%). Read it as direction, not calibration:

- The beta rule loses to doing nothing; both halves hurt.
- Switching **stock-specific** risk by standing (v2's modes) more than doubles the odds.
- Beta does not add odds on average. It only moves *which* market you win in. Cover/bold makes the
  result almost independent of market direction.

### Proposed rules for the ranking module

1. Keep v2's 守/攻/衝 on stock-specific (relative) risk as the core.
2. **守 (ahead):** hold beta near the teams ranked ~4–12, not lower. Copy their big tilts, swapping
   names inside the same industry to keep Active Share ≥ 30%.
3. **衝 (behind):** concentrate in the strongest, least crowded alphas (low `library_corr` to
   `mom20` and to the active-ETF top holdings). Add beta only with a market view.
4. **Estimate the cutoff, don't assume it.** Expect the 6th-place NAV to rise and get noisier late
   (Genakos & Pagliero; Kirchler et al.). If the leaderboard shows NAVs, measure the cutoff's daily
   volatility and its beta to TAIEX, and use them in z.
5. **Model the field** (Haugh & Singal): public repos (other teams lean on momentum and LLM picks
   of large caps), active-ETF holdings, and returns-based style analysis of leaderboard NAVs if
   published.
6. Endgame per v2: 11/26 is the last order with a full day of effect.

What the factory adds: `beta_vs_b0`, crowding (`library_corr` vs `mom20`), and `top10_24d` per alpha,
so 衝 mode can pick strong alphas that do not move with the crowd.

## 9. Alpha card (export format, T10)

```yaml
name: rev_accel_3v12
pod: 營收獲利動能            # one of the five declared factors
hypothesis: Last-3-month revenue growth above 12-month growth keeps outperforming for 1-4 weeks.
formula: "$rev_3m_yoy - $rev_12m_yoy"
inputs: [$rev_3m_yoy, $rev_12m_yoy]
horizon_days: 20
dplan_template: "{ticker} 近 3 月營收年增 {rev_3m_yoy:+.0%}，高於近 12 月的 {rev_12m_yoy:+.0%}"
source_authority: mops     # twse / tpex / mops, for the D-Plan source entry
judge: {window: research, panel_built_at: ..., ic_t_nw_20d: ..., gate: pass}
holdout: {checked_at: ..., ic_20d: ...}
model: {served_model: ..., run: log/<session>}
```

The contest code computes the formula from its own live data. Only fields with a live source
(TWSE/TPEx daily files, MOPS revenue) may appear in an exported alpha.

## 10. Timeline

| Date (2026) | Event | Factory |
|---|---|---|
| 10/22 | — | step 1 built (T7) |
| 10/23 | code freeze for the contest pipeline | factory is outside it |
| 10/26 19:30 | first D-Plan window | — |
| 10/24–10/27 | — | T8: judge into the loop (Architect first) |
| 10/27–10/30 | contest week 1 | T9 campaign (needs J5, J7) |
| 10/30 | — | G4: export gate for v1.1 |
| 11/2 | contest v1.1 | T10: alpha cards ship, or wait |
| 12/19 | Demo Day | the factory is "Agent 技術架構" material |
