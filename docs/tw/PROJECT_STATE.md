# TW Project State

> Living document. Whoever (human or model) changes the project updates this file in the same
> commit. Newest snapshot on top; keep older snapshots below as history.

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

1. **Refresh data & resume factor loop** on the full universe (refresh blocked on D3 as of
   2026-10-22); harvest/aggregate results from `log/` sessions into a findings doc (which
   factors reached SOTA, IC stats).
2. **Real VWAP**: `ohlcv.csv` carries `$amount`; compute VWAP = amount/volume in `dump_to_bin.py`
   instead of the OHLC4 proxy (follow-up noted in `collect_finmind.py` docstring).
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
- LLM: LiteLLM proxy → `deepseek/deepseek-chat`; keys + `FINMIND_TOKEN_API_KEY` in untracked
  `.env`. FinMind tier: Backer/Sponsor (affects collector request strategy); the token in `.env`
  was rejected on 2026-10-22.
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
