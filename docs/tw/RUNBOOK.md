# TW Runbook

Everything runs on the user's machine in **WSL2**: `cd /mnt/g/Github/RD-Agent`,
`conda activate rdagent`, `set -a; source .env; set +a`. Claude's sandbox cannot run any of this
(no conda/qlib-data/Docker/keys) — treat commands below as instructions for the user's terminal.

## 0. Prerequisites

- `.env` at repo root (untracked). Required keys — see `.env.example` plus:
  `CHAT_MODEL`, `EMBEDDING_MODEL`, `LITELLM_PROXY_API_BASE`, `LITELLM_PROXY_API_KEY`,
  `DEEPSEEK_API_KEY`, `LITELLM_CHAT_MODEL_MAP`, `FINMIND_TOKEN_API_KEY`, `MAX_RETRY`,
  `RETRY_WAIT_SECONDS`, `REASONING_THINK_RM`.
- Health check: `rdagent health_check` (needs the embedding `encoding_format` patch — already in).
- qlib bin present at `~/.qlib/qlib_data/tw_data` (else run the data pipeline first).

## 1. Data pipeline

**Daily refresh** (after ~18:00 TPE; 15–40 min; idempotent, per-day cached):

```bash
bash rdagent/scenarios/qlib/tw_data/daily_update.sh
# detached: tmux new -s tw_daily -d "bash /mnt/g/Github/RD-Agent/rdagent/scenarios/qlib/tw_data/daily_update.sh"
# extra collector flags: COLLECT_ARGS="--sparse_max_age_days 20" bash .../daily_update.sh
```

Steps it chains (runnable individually, all `python -u -m rdagent.scenarios.qlib.tw_data.<mod>`):
`collect_finmind` (skips market-wide/macro/static/derivatives — weekly cadence is fine for those
— and the broker pull) → `clean_csvs` → `dump_to_bin --input_dir data/tw_finmind_full_clean
--out_dir ~/.qlib/qlib_data/tw_data` → `dump_factor_h5`.

Collector cache rules (why: DECISIONS D13/D14):
- A startup preflight aborts on a rejected token or lost Backer/Sponsor tier → fix `.env` (D3).
- Failed requests, and empty answers for the last 7 days (not published yet), are never cached;
  re-running retries them.
- OHLCV self-heals FinMind's back-adjustment: each run re-checks the last verified day
  (`_ohlcv_parts/_verified_through.txt`) and re-fetches full history for stocks whose adjusted
  close moved (`_ohlcv_restated/`). After a crashed update or a long gap, use `--refresh_ohlcv`
  (~4.4K calls).
- Per-stock fundamentals (`month_revenue`, `dividend*`, `fs_*`) never refresh by themselves: once
  a month after the 10th (revenue posting deadline) run with
  `COLLECT_ARGS="--sparse_max_age_days 20"` (~15K calls, resumable).

**Catch-up after a long gap** — two phases so the bin is usable before the slow fundamentals:

```bash
collect() { python -u -m rdagent.scenarios.qlib.tw_data.collect_finmind --start 2010-01-01 --skip_secid_agg "$@"; }
# 1) bin inputs + everything cheap (~6.5K calls, ~2 h), then rebuild bin + H5
collect --refresh_ohlcv --skip_month_revenue --skip_dividend --skip_dividend_result \
    --skip_fs_income --skip_fs_balance --skip_fs_cashflow
python -u -m rdagent.scenarios.qlib.tw_data.clean_csvs
python -u -m rdagent.scenarios.qlib.tw_data.dump_to_bin --input_dir data/tw_finmind_full_clean --out_dir ~/.qlib/qlib_data/tw_data
python -u -m rdagent.scenarios.qlib.tw_data.dump_factor_h5
# 2) fundamentals (~15K calls, ~3 h), then re-clean
collect --sparse_max_age_days 1 --skip_ohlcv --skip_institutional --skip_margin --skip_valuation \
    --skip_foreign_holdings --skip_aggregates --skip_market_wide --skip_macro --skip_static --skip_derivatives
python -u -m rdagent.scenarios.qlib.tw_data.clean_csvs
```

Delete `data/tw_finmind_full/_ohlcv_parts.stale/` and `_ohlcv_restated.stale/` once phase 1 has
produced a good bin.

**Verify data**: `python -m rdagent.scenarios.qlib.tw_data._verify_bin` and `_inspect.py`.
**Full FinMind re-collect from scratch**: move `data/tw_finmind_full/` aside and run the catch-up
(many hours of quota). `tw_data/_run_full_collect.sh` is the legacy TWSE-direct `collect.py`
pull, not FinMind.

## 2. Factor loop (main workhorse)

Preferred: the root wrapper `_run_factor_tw.sh` (untracked!). What it does, in case it must be
recreated:

```bash
mkdir -p logs; LOG="logs/factor_tw_$(date +%Y%m%d_%H%M%S).log"
ln -sf "$(basename "$LOG")" logs/factor_tw_latest.log
conda activate rdagent; set -a; source .env; set +a
export CHAT_STREAM=false                                   # httpx deadlock guard (CLAUDE.md pitfalls)
export FACTOR_COSTEER_DATA_FOLDER="$PWD/git_ignore_folder/factor_implementation_source_data"
export FACTOR_COSTEER_DATA_FOLDER_DEBUG="$PWD/git_ignore_folder/factor_implementation_source_data_debug"  # absolute → WSL 9p getcwd guard
TW_TEST_END=$(tail -n 2 ~/.qlib/qlib_data/tw_data/calendars/day.txt | head -n 1)
export QLIB_FACTOR_TW_TEST_END="$TW_TEST_END"              # penultimate trading day
python -u -m rdagent.app.qlib_rd_loop.factor_tw 2>&1 | tee "$LOG"
```

- Bounded run: append `--loop_n 1` (or `--step_n`, `--all_duration`).
- **Resume a session**: `python -m rdagent.app.qlib_rd_loop.factor_tw --path "log/<session_dir>/__session__/<loop_id>/<step>"`.
- Steer proposals: edit `data/factor_ideas.md` (live, re-read each iteration).

## 3. Other loops

```bash
python -u -m rdagent.app.qlib_rd_loop.model_tw                    # model R&D (same env guards apply; use QLIB_MODEL_TW_TEST_END)
python -u -m rdagent.app.qlib_rd_loop.factor_tw_from_report --report_folder <pdf_dir>   # factors from research PDFs
```

## 4. Monitoring

- Live log: `tail -f logs/factor_tw_latest.log`. Cost lines: `Accumulated Cost: $…`.
- **Hang detection**: if log mtime freezes for >30 min with the process in `S` state, it's the
  streaming deadlock — kill and restart with `CHAT_STREAM=false` (should not happen with the
  wrapper).
- Trace UI (upstream tooling): `rdagent ui --port 19899 --log_dir log/`.
- Session artifacts: `log/<timestamp>/` (traces, `__session__` checkpoints for resume).

## 5. Smoke tests (run after env/data/upstream changes)

```bash
python rdagent/scenarios/qlib/tw_data/_smoke_qlib.py        # qlib reads TW bin
python rdagent/scenarios/qlib/tw_data/_smoke_qrun_tw.py     # qrun end-to-end on TW template
python rdagent/scenarios/qlib/tw_data/_smoke_models_tw.py   # model zoo sweep (results → MODEL_SMOKE_RESULTS.md)
```

## 6. Git

```bash
git fetch upstream && git merge upstream/main   # sync (then: re-verify patches per CLAUDE.md rule 6, run smokes)
git push origin main
```
