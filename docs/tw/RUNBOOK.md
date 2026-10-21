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
```

Steps it chains (runnable individually, all `python -u -m rdagent.scenarios.qlib.tw_data.<mod>`):
`collect_finmind` (skip-flags for market-wide/macro/static/derivatives — weekly cadence is fine
for those) → `clean_csvs` → `dump_to_bin --input_dir data/tw_finmind_full_clean --out_dir
~/.qlib/qlib_data/tw_data` → `dump_factor_h5`.

**Verify data**: `python -m rdagent.scenarios.qlib.tw_data._verify_bin` and `_inspect.py`.
**Full re-collect from scratch**: `_run_full_collect.sh` in `tw_data/` (hours; FinMind quota).

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
