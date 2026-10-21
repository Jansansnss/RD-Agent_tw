# TW Project State

> Living document. Whoever (human or model) changes the project updates this file in the same
> commit. Newest snapshot on top; keep older snapshots below as history.

## Snapshot — 2026-07-09

**Last real activity:** 2026-05-17 (factor_tw loop run). Project has been idle since; local data
(`~/.qlib/qlib_data/tw_data`) and the rolling `test_end` are ~2 months stale. First action of the
next working session: run `daily_update.sh`, then re-check `test_end` handling (the wrapper
auto-trims it).

### Works end-to-end

- **FinMind data pipeline**: full collection (35 datasets, ~9 GB, 2010→2026-05) → audited &
  cleaned (`clean_csvs.py`, rules in `data/DATA_AUDIT_REPORT.md`) → qlib bin → factor-coder H5.
  Daily update orchestration in `tw_data/daily_update.sh`.
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
  No pipeline code committed for these yet.

### Roadmap (rough priority order)

> Executable form with gates, retry ladders and jump rules: **`docs/tw/TODO_GRAPH.md`** (added
> 2026-07-09; that file is authoritative for "what next"). Team/model assignments:
> `docs/tw/AGENT_PLAYBOOK.md`.

1. **Refresh data & resume factor loop** on the full universe; harvest/aggregate results from
   `log/` sessions into a findings doc (which factors reached SOTA, IC stats).
2. **Real VWAP**: `ohlcv.csv` carries `$amount`; compute VWAP = amount/volume in `dump_to_bin.py`
   instead of the OHLC4 proxy (follow-up noted in `collect_finmind.py` docstring).
3. **Fundamentals into bin**: pivot curated `type` values from long-format `fs_income/balance/
   cashflow.csv` into bin features; month_revenue/dividend already merge sparse.
4. **Corporate-action `$factor`** for the TWSE-direct collector (currently 1.0; FinMind's
   `TaiwanStockPriceAdj` already adjusted — only matters if `collect.py` becomes primary again).
5. **Model loop hardening**: fix kwarg mismatches (CatBoost/DEnsemble/MLP) in the sweep; retest
   ts-models (LSTM/GRU/ALSTM/TCN/GATs) on the full universe where N is no longer tiny.
6. **Broker/news alternative data**: decide whether to productionize the exploratory pulls into
   `collect_finmind.py` datasets + cleaning rules.
7. **Upstream sync**: last merge base is upstream commit `0ca16096` (~May 2026). Periodically
   `git fetch upstream` and merge (see CLAUDE.md rule 6).

### Environment facts (user's machine)

- Windows + WSL2; repo at `G:\Github\RD-Agent` = `/mnt/g/Github/RD-Agent` in WSL.
- Conda env `rdagent` (miniconda3), Python 3.10, pyqlib 0.9.7, CPU-only torch; Docker available
  for qrun.
- LLM: LiteLLM proxy → `deepseek/deepseek-chat`; keys + `FINMIND_TOKEN_API_KEY` in untracked
  `.env`. FinMind tier: Backer/Sponsor (affects collector request strategy).
- Operator wrappers (untracked, root): `_run_factor_tw.sh` (preferred; sets CHAT_STREAM=false,
  absolute data folders, auto-trims test_end), `_launch_rd_loop.sh` (older). Recreate from
  `docs/tw/RUNBOOK.md` if lost.
- Local-only valuables (in gitignored `data/`, no git backup): `factor_ideas.md` (alpha steering
  file), raw/clean FinMind CSVs (~9 GB×2, recollectable), exploratory broker/news pulls.

## History

*(append superseded snapshots here when updating)*
