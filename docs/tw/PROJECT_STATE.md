# TW Project State

> Living document. Whoever (human or model) changes the project updates this file in the same
> commit. Newest snapshot on top; keep older snapshots below as history.

## Snapshot — 2026-10-25: flash campaign; 2 alphas confirmed on the holdout

**Flash campaign (T9, Jans: every step on `deepseek-flash`, run until the balance is gone):**
rounds 10–29 from 03:36 to 08:29, $1.93 billed. 48 factors judged, 11 passed the research gate,
but round 29's four were variants of one idea; same-round passes are now deduplicated (D23).
**Holdout (G4):** 2 of the 12 library cards confirm, both built on "large and liquid":
`LiquiditySize_Rank_20d` and `SalesYield_LiquiditySize_70_30`. Every momentum-type card failed or
reversed, like the agent's own momentum leg. The DeepSeek balance is 0. Report for the team:
Claude Doc "RD-Agent 因子研究 flash 長跑報告".

## Snapshot — 2026-10-24 (evening): split moved; first 10-round campaign done

**Split (D21):** research 2021-01 → 2026-04-30, holdout 2026-05-01 → latest (117 trading days to
10-22), set once in `alpha_eval_tw.WINDOWS`. Both revenue baselines now pass the research gate at
5 days. On the holdout every signal the contest agent trades loses: its 50/20/30 blend trails the
equal-weight universe in 5 of 6 months (B0 +15.6%, blend top-24 book −0.6%). Short sample (no
holdout t above 2.5), but it agrees with the agent's 9/1–10/22 paper run (−8.7%).

**Library seeded (D22)** with `traded_rev_accel`, `traded_rev_yoy`, `traded_mom60_skip5`.

**Campaign (T9):** 10 rounds in ~39 minutes for ~$0.58 (rounds 8–9 after a balance top-up). 30
factors, 1 research pass (value + revenue acceleration), not confirmed on the holdout (G4). The
best research t (5.25, dividend yield + revenue acceleration) was blocked by correlation 0.63, so
a "replaces a library card" rule is the next decision (D22 revisit). The first launch had crashed
entering round 2; fixed in `proposal/alpha_proposal_tw.py` (smoke 21/21).
Report for Jans and the team: Claude Doc "RD-Agent 因子研究十輪試跑報告".

## Snapshot — 2026-10-24: alpha loop wired; contest agent built

**Alpha loop (T8 `[x]`, D20):** RD-Agent now proposes and codes factors for the contest and the code
judge decides acceptance (`app/qlib_rd_loop/alpha_tw.py`). Canary, approved by Jans for one round:
4 minutes, 28 LLM calls, about $0.03; 4 factors, none passed (best: revenue surprise vs its own
year, t 3.32 at 1 day, 2.30 at 5). The campaign (T9) waits for Jans to set the number of rounds.

**Correction:** `deepseek-chat` did not stop working. DeepSeek announced its end for 2026-07-24,
but the canary's 26 chat calls used it without error (J7 rewritten: deprecated, not dead).

**Contest repo:** the daily agent lives in `G:\AI_cup_2026\agent\` (branch `add-daily-agent`, not in
this repo): it recomputes the same revenue and momentum formulas on the team's own FinMind data,
so no RD-Agent code runs in the contest's daily path (D15).

## Snapshot — 2026-10-23: data current through 2026-10-22

**Data refreshed** (T0 `[x]`, G1 `[x]`): clean CSVs, qlib bin (3,041 instruments, 4,103 days) and
`daily_pv.h5` run through 2026-10-22, so loops get `test_end` 2026-10-21 (the wrapper derives it).
Fundamentals are refreshed (revenue through September), and `gov_bank` exists for the first time.
Full write-up: `data/DATA_AUDIT_REPORT.md` → "Refresh 2026-10-23". The alpha-factory session
rebuilds its panel from this data.

**Bugs fixed during the run** (code, not data edits): adjusted-price drift confirmed live (58% of
instruments moved since May; D13 now checks two anchor days) · OOM kill of a 2 GB in-memory merge
(merges now stream) · `gov_bank` never collected (renamed dataset; D14) · `dump_to_bin` mangled 9
ETF codes into real stocks' margin rows in the May bin · cleaner skipped two stock files.

**Decisions waiting for Jans:**
1. **Emerging-board (興櫃) stocks in the universe**: ~13% of instruments, ~9% of 2021+ rows, 93% of
   the >10.5% daily moves. Board membership can be dated from 2024-10 via first index weight, not
   from `stock_info` (D19, TODO D1).
2. **Upstream v1.0.0 merge** (T6): still clean, still unmerged; follow-ups in the 2026-10-22
   snapshot below.
3. **S5 raw `TaiwanStockPrice`**: exact VWAP, and the basis for a proper `$factor` (D13 deferred).
4. **Push**: `main` has several local-only commits; Windows git lacks the GitHub host key, so push
   from WSL.

Also: FinMind deleted 5.7% of `fs_balance` history (D18, May copy archived), and qlib can't read
`$amount` (written as `$amount.day.bin`).

**Alpha factory** (T7 `[x]`, G3 `[x]`): panel rebuilt on this data. Its universe now ranks by
index weight where FinMind has it (from 2024-10) and dates listings by first index weight (D19):
149/150 match with the official list (the miss is 3718, 5371's new holding company). Refreshed
verdict (ALPHA_FACTORY §6): revenue acceleration is the one candidate; momentum's holdout edge
faded with five more months; 投信 flow still shows nothing. For decision 1: `stock_info` record
dates can't date listings (D19).

## Snapshot — 2026-10-22 (evening): alpha factory for AI CUP 2026

**New line of work:** RD-Agent becomes the offline alpha lab for the AI CUP 2026 ETF-agent contest
(design: `docs/tw/ALPHA_FACTORY.md`; TODO line T7–T10; DECISIONS D15–D17). Step 1 is built:
`tw_data/dump_alpha_panel_tw.py` (point-in-time 150-stock universe, VWAP, revenue known on the
10th → `data/alpha_tw/`) and `tw_data/alpha_eval_tw.py` (fast judge + gate; smoke test 15/15).
First panel from the May clean data (301 stocks, to 2026-05-08); its May selection already matches
141/150 of the official list. First verdict on the v2 hypotheses: none passes the strict gate;
revenue acceleration is positive in both windows; momentum flipped from slightly negative
(2021 to mid-2025) to very strong (holdout, book beta 1.7); 投信 flow shows nothing.

**LLM model name is dead:** DeepSeek retired `deepseek-chat`/`deepseek-reasoner` on 2026-07-24.
This file records the loop on `deepseek/deepseek-chat`, so any loop now fails until `CHAT_MODEL`
moves to `deepseek-flash` (V4.1, follows the latest model) or `deepseek-v4-pro` (jump rule J7).

**VWAP finding:** `ohlcv.csv` prices are back-adjusted but `$amount`/`$volume` are raw, so the
planned "real VWAP = amount/volume" (S1) would mix scales. Exact VWAP needs raw closes (S5, D17).

**Data refresh:** the catch-up (RUNBOOK §1, phase 1) is running and downloading, so the FinMind
token works again; rebuild the alpha panel after phase 2 (revenue).

## Snapshot — 2026-10-22

**Data is still stale** (qlib calendar ends 2026-05-08). The refresh is blocked on D3: FinMind
rejects the `.env` token ("Token is illegal" — the JWT carries a `token_version`, so it was
rotated or revoked), and anonymous probes confirm by-date mode and `TaiwanStockPriceAdj` are
Sponsor-only. Next: Jans puts a fresh token in `.env` (and confirms the tier), then run the
catch-up in RUNBOOK §1 (phase 1 ≈ 2 h → G1; phase 2 fundamentals ≈ 3 h).

**Collector hardened** while preparing that 5-month catch-up — each of these would have corrupted
or silently frozen data:
- Adjusted-price seam: FinMind back-adjusts to the fetch date; appending to the May cache would
  have put a fake ex-dividend "return" on 2026-05-11 for most stocks. Now self-healing (D13).
- Failures (e.g. the dead token) were cached as empty days forever; unpublished recent days
  likewise; per-stock fundamentals and macro series never refreshed (D14).
- `daily_update.sh` would have rewritten the 49-broker `broker_secid_agg.csv` with one broker.
Checked offline against a simulated FinMind (re-adjustments, a capital reduction during a
suspension, a re-anchor mid-refresh, failed requests); not yet run against the live API.

**Upstream:** microsoft/RD-Agent **v1.0.0** is out — 15 commits past our merge base `0ca16096`
(2026-04-28 → 2026-09-23), mostly security hardening. A trial merge is clean; the only file both
sides touched is `utils/env.py`, and our `QTDockerEnv.prepare` patch survives. **Not merged yet**
(T6, awaiting go-ahead). TW follow-ups that must ship with the merge:
- `factor_template_tw/` and `model_template_tw/` `read_exp_res.py` must write `ret.parquet`:
  upstream's `QlibFBWorkspace` now reads parquet, so the backtest chart would silently vanish.
  (Upstream's own copy dedents that line out of its `else:`; don't copy that bug.)
- Session, cache and log pickles become HMAC-signed (`~/.rdagent/artifact_signing.key`).
  Resuming or harvesting (T3) pre-merge `log/` sessions needs `ALLOW_UNSAFE_LEGACY_PICKLE=true`;
  the web trace server needs `UI_LOAD_LEGACY_PICKLE_TRACES=true` for old traces.
- `requirements.txt` pins `pydantic-ai-slim==1.66.0`; check the `rdagent` env before reinstalling.

**Housekeeping:** the 2026-07-09 governance docs are now committed (a stale `.git/index.lock`
left by that session had blocked the commit).

### Works end-to-end

- **FinMind data pipeline**: full collection (35 datasets, ~9 GB, 2010→2026-05) → audited &
  cleaned (`clean_csvs.py`, rules in `data/DATA_AUDIT_REPORT.md`) → qlib bin → factor-coder H5.
  Daily update orchestration in `tw_data/daily_update.sh`; long-gap catch-up in RUNBOOK §1.
- **TW factor loop** (`factor_tw.py`): runs multi-day sessions on DeepSeek via LiteLLM proxy;
  proposes/implements/backtests factors on TW data; user-curated idea seeding via
  `data/factor_ideas.md`. Multiple sessions completed May 13–17, 2026 (see `log/`).
- **TW model loop** (`model_tw.py`) and **factor-from-report loop**
  (`factor_tw_from_report.py`): wired and committed (commit 15dd4467); less battle-tested than
  the factor loop.
- **Model plumbing smoke** (`_smoke_models_tw.py`): 4/14 qlib zoo models pass on a 5-stock bin
  (LGB, XGB, Linear, Transformer_ts). Failures are model/sweep-side, not TW-data-side. Details in
  `MODEL_SMOKE_RESULTS.md`.

### In progress / exploratory (uncommitted, data/ dir only)

- Broker branch-level data pull (FinMind `TaiwanStockTradingDailyReport`-family), news download,
  instrument inventory review — logs in `data/_brokers_pull_*.log`, `data/_news_download.log`.
  The broker pull **completed 2026-05-15**: `broker_secid_agg.csv`, 29.25M rows, 46 of 49
  watchlist brokers, 2,389 stocks, 2021-07-07 → 2026-05-13. Its per-(broker, stock) cache never
  refreshes, so productionizing it (S4) needs a delta-refresh design first.

### Roadmap (rough priority order)

> Executable form with gates, retry ladders and jump rules: **`docs/tw/TODO_GRAPH.md`** (added
> 2026-07-09; that file is authoritative for "what next"). Team/model assignments:
> `docs/tw/AGENT_PLAYBOOK.md`.

1. **Resume factor loop** on the full universe (data refreshed 2026-10-23; loops first need the
   J7 model-name switch); harvest/aggregate results from `log/` sessions into a findings doc
   (which factors reached SOTA, IC stats).
2. **Real VWAP**: `ohlcv.csv` carries `$amount`, but amount/volume is a raw VWAP against
   back-adjusted prices; collect raw `TaiwanStockPrice` first (TODO S5, D17).
3. **Fundamentals into bin**: pivot curated `type` values from long-format `fs_income/balance/
   cashflow.csv` into bin features; month_revenue/dividend already merge sparse.
4. **Corporate-action `$factor`** for the TWSE-direct collector (currently 1.0; FinMind's
   `TaiwanStockPriceAdj` already adjusted — only matters if `collect.py` becomes primary again).
   Same machinery would let FinMind switch to raw prices + own `$factor` (D13 "deferred").
5. **Model loop hardening**: fix kwarg mismatches (CatBoost/DEnsemble/MLP) in the sweep; retest
   ts-models (LSTM/GRU/ALSTM/TCN/GATs) on the full universe where N is no longer tiny.
6. **Broker/news alternative data**: decide whether to productionize the exploratory pulls into
   `collect_finmind.py` datasets + cleaning rules.
7. **Upstream sync**: last merge base is upstream commit `0ca16096` (2026-04-28); v1.0.0 is
   available and merges cleanly (see snapshot for the TW follow-ups). CLAUDE.md rule 6.

### Environment facts (user's machine)

- Windows + WSL2; repo at `G:\Github\RD-Agent` = `/mnt/g/Github/RD-Agent` in WSL.
- Conda env `rdagent` (miniconda3), Python 3.10, pyqlib 0.9.7, CPU-only torch; Docker available
  for qrun.
- LLM: LiteLLM → `deepseek/deepseek-chat` (deprecated: announced end 2026-07-24, still answering
  2026-10-24), with `LITELLM_CHAT_MODEL_MAP` sending some calls to `deepseek/deepseek-v4-pro`;
  prefer `deepseek-flash` before long campaigns (J7); keys + `FINMIND_TOKEN_API_KEY` in untracked
  `.env`. FinMind tier: Sponsor (account level 3, 6,000 calls/hour rolling); the token was
  rotated and replaced on 2026-10-22.
- WSL2 has ~11 GB RAM; the OHLCV merge (~8M rows) peaks at a few GB.
- Operator wrappers (untracked, root): `_run_factor_tw.sh` (preferred; sets CHAT_STREAM=false,
  absolute data folders, auto-trims test_end), `_launch_rd_loop.sh` (older). Recreate from
  `docs/tw/RUNBOOK.md` if lost.
- Local-only valuables (in gitignored `data/`, no git backup): `factor_ideas.md` (alpha steering
  file), raw/clean FinMind CSVs (~9 GB×2, recollectable), exploratory broker/news pulls.

## History

### 2026-07-09

**Last real activity:** 2026-05-17 (factor_tw loop run). Project has been idle since; local data
(`~/.qlib/qlib_data/tw_data`) and the rolling `test_end` are ~2 months stale. First action of the
next working session: run `daily_update.sh`, then re-check `test_end` handling (the wrapper
auto-trims it).
