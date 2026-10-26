# RD-Agent_tw — Taiwan-market quant fork of microsoft/RD-Agent

Read this first, every session. It is the contract between past and future work on this repo.

## What this repo is

A fork of [microsoft/RD-Agent](https://github.com/microsoft/RD-Agent) customized to run automated
factor/model R&D loops on the **Taiwan stock market (TWSE/TPEx)** instead of the Chinese A-share
market the upstream qlib scenarios assume.

- `origin`   = git@github.com:Jansansnss/RD-Agent_tw.git (our fork, branch `main`)
- `upstream` = https://github.com/microsoft/RD-Agent.git
- All TW work is a small set of commits on top of upstream. Inspect the full customization surface with:
  `git diff $(git merge-base upstream/main main)..main --stat`

Deeper docs (keep them current):

| Doc | Purpose |
|---|---|
| `docs/tw/PROJECT_STATE.md` | Current status, what works, roadmap. **Update at end of any session that changes things.** |
| `docs/tw/DECISIONS.md` | Why-log. Append when making a non-obvious choice. |
| `docs/tw/RUNBOOK.md` | How to run everything (data pipeline, loops, smoke tests). |
| `docs/tw/AGENT_PLAYBOOK.md` | Team design for model sessions: roles, model routing, context budgets, comms protocol. |
| `docs/tw/TODO_GRAPH.md` | Executable branching roadmap (gates, retry ladders, jump rules). **Pick work from here; move markers.** |
| `docs/tw/ALPHA_FACTORY.md` | AI CUP 2026 alpha factory: contest-aligned panel, fast judge + gate, LLM look-ahead protocol, tournament notes. |
| `docs/tw/ALPHA_REVIEWS.md` | Written review of every holdout-confirmed alpha (formula from code, audit, exposures, verdict). Required before use (D24). Written in Traditional Chinese. |
| `docs/tw/reports/` | Reports for Jans and the team (campaign results, assessments), one dated file each, in Traditional Chinese Markdown. |
| `data/DATA_AUDIT_REPORT.md` | Data-quality audit of the FinMind collection; cleaning rules rationale. |
| `rdagent/scenarios/qlib/tw_data/MODEL_SMOKE_RESULTS.md` | Which qlib models pass plumbing on TW data. |

## Golden rules

1. **Isolation**: TW code lives in `*_tw*` files/dirs (`tw_data/`, `factor_tw*.py`, `model_tw.py`,
   `*_template_tw/`). Never change CN/upstream behavior to get TW behavior — subclass instead.
2. **Minimal upstream patches**: a few upstream files carry small hook patches (list below). When
   touching an upstream file, keep the diff as small as possible and add a hook (overridable class
   attribute / method) rather than forking logic inline.
3. **Never commit data or secrets**: `.env` (holds `FINMIND_TOKEN_API_KEY`, LLM keys), `data/`
   raw/clean CSVs, `*.h5`, qlib bin, `log*/`, caches. Check `git status` before committing; if a
   data file shows up, fix `.gitignore` instead of committing it.
4. **Runtime ≠ dev sandbox**: the loops run on the user's machine under **WSL2**
   (`/mnt/g/Github/RD-Agent`, conda env `rdagent`, Python 3.10, CPU-only torch, Docker for qrun).
   Claude's sandbox has no conda env, no qlib data, no Docker, no FinMind/LLM keys — write and
   review code here, but never expect to execute loops here. Verify by asking the user to run
   the smoke scripts (see RUNBOOK).
5. **Loops cost money**: R&D loops call a paid LLM (LiteLLM proxy → deepseek). Never launch or
   resume a loop unless the user asked.
6. **Upstream sync**: `git fetch upstream && git merge upstream/main` (merge, don't rebase —
   `main` is pushed). After a sync, re-run the diff-stat above and confirm every changed upstream
   file in the list below still carries its patch; then have the user run the smoke chain.
7. **Session protocol**: start — read this file, `docs/tw/PROJECT_STATE.md`, then pick the next
   node from `docs/tw/TODO_GRAPH.md` (check its jump rules first) and work it per
   `docs/tw/AGENT_PLAYBOOK.md` role budgets. End — update `PROJECT_STATE.md` + TODO_GRAPH
   markers, append `DECISIONS.md` if choices were made, commit (imperative subject, e.g. "Add TW
   model loop; iterate FinMind collector"), push to `origin main`.

## Architecture map

### Data pipeline (all in `rdagent/scenarios/qlib/tw_data/`)

```
collect_finmind.py  FinMind API (75+ TW datasets; token tier: Backer/Sponsor)
      │             → data/tw_finmind_full/*.csv   (~35 CSVs, ~9 GB, 2010→today, per-day cached)
      ▼
clean_csvs.py       TWSE securities-coding universe filter + per-file fixes (non-destructive)
      │             → data/tw_finmind_full_clean/
      ▼
dump_to_bin.py      → ~/.qlib/qlib_data/tw_data/   (qlib bin: calendars/instruments/features)
      ▼
dump_factor_h5.py   → git_ignore_folder/factor_implementation_source_data/daily_pv.h5
                      (the H5 the factor-coder LLM reads — CN parity, no qlib.init needed)
```

- `daily_update.sh` chains all four steps (run after ~18:00 TPE; safe to re-run; see header).
- `collect.py` is the older TWSE-direct collector (raw exchange data, `$factor`=1.0). FinMind is
  the primary source; keep `collect.py` schema-compatible as fallback.
- Smoke/verify helpers: `_verify_bin.py`, `_inspect.py`, `_smoke_qlib.py`, `_smoke_qrun_tw.py`,
  `_smoke_models_tw.py`.
- Alpha factory (AI CUP 2026, `docs/tw/ALPHA_FACTORY.md`): `dump_alpha_panel_tw.py` reads the clean
  CSVs → `data/alpha_tw/alpha_panel.h5` (point-in-time 150-stock universe); `alpha_eval_tw.py`
  judges alphas on it (pandas only, no qlib); `_smoke_alpha_eval_tw.py` checks both.

### R&D loops

| Loop | Entry (`rdagent/app/qlib_rd_loop/`) | Setting class (`conf.py`) | env prefix |
|---|---|---|---|
| TW factor | `factor_tw.py` | `FactorTwBasePropSetting` | `QLIB_FACTOR_TW_` |
| TW factor from PDF reports | `factor_tw_from_report.py` | `FactorFromReportTwPropSetting` | `QLIB_FACTOR_FROM_REPORT_TW_` |
| TW model | `model_tw.py` | `ModelTwBasePropSetting` | `QLIB_MODEL_TW_` |
| TW alpha factory (AI CUP 2026) | `alpha_tw.py` | `AlphaTwBasePropSetting` | `QLIB_ALPHA_TW_` |

The alpha loop needs no Docker: its runner (`developer/alpha_runner_tw.py`) scores factors with
`tw_data/alpha_eval_tw.py` on `data/alpha_tw/coder/` (anonymized IDs, research window only) and
its feedback (`developer/alpha_feedback_tw.py`) gates in code and writes `data/alpha_tw/library/`.
Its proposal (`proposal/alpha_proposal_tw.py`) writes the trace history from the judge's verdicts.
The library starts with the three signals the contest agent trades (`library/traded_*`,
correlation references only, D22). Its idea file is `data/alpha_ideas.md` (local-only, like
`factor_ideas.md`; research-period numbers only). Its research/holdout split is
`alpha_eval_tw.WINDOWS` (D21), not the date window below. Holdout check:
`tw_data/alpha_holdout_tw.py`, never inside the loop. Every confirmed card then gets
`tw_data/alpha_review_tw.py` and an entry in `docs/tw/ALPHA_REVIEWS.md` before anyone uses it (D24),
including an A–F review answered from the filled `data/alpha_tw/reviews/<card>_prompt.md` by a model
other than the loop's (D25). Teammates' ideas are scored as fixed formulas first
(`tw_data/alpha_team_ideas_tw.py`), then go into the idea file with their research results (D29).
Signals are combined with fixed weights: the LightGBM combiner (`tw_data/alpha_combiner_tw.py`) lost
to an equal-weight average (D28). The coder's quick runs use `coder_debug` (two years, D26).
`$industry_id` is each stock's team product-exposure family (V2, `product_groups_v2.csv`) or else its
primary FinMind industry-chain group (`tw_data/chain_groups_tw.py`, `industry_chain.csv`; D30-D31),
not the exchange category.

Date window (all TW settings): train 2021-01-01..2023-12-31, valid 2024, test 2025-01-01..rolling
`test_end`. `test_end` MUST stay ≤ (qlib calendar end − 1 trading day) — see pitfalls.

### Extension pattern (how TW hooks into upstream)

Upstream classes got overridable attributes; TW subclasses set them:

- `QlibFactorRunner.prop_setting_cls` / `QlibModelRunner.prop_setting_cls` → `QlibTw*Runner` (date windows)
- `QlibFactorHypothesis2Experiment.experiment_cls` → `QlibTwFactorHypothesis2Experiment` (TW workspace template)
- `QlibFactorScenario._load_source_data()` → TW scenario swaps in TW data description
- `QlibTwFactorHypothesisGen` injects `data/factor_ideas.md` (user-editable, re-read every
  iteration, path override `FACTOR_TW_IDEAS_PATH`) into the hypothesis prompt
- Workspace templates: `experiment/factor_template_tw/`, `experiment/model_template_tw/`
  (provider_uri `tw_data`, TW universe/costs); scenarios in `experiment/factor_tw_experiment.py`,
  `factor_tw_from_report_experiment.py`, `model_experiment.py::QlibTwModelScenario`

### Upstream files carrying TW patches (keep this list current)

`app/qlib_rd_loop/conf.py` (TW setting classes) · `scenarios/qlib/developer/factor_runner.py`,
`model_runner.py` (prop_setting_cls hook + `QLIB_DATASET_NAME` passthrough + TW subclasses) ·
`experiment/factor_experiment.py` (`_load_source_data` hook) · `experiment/model_experiment.py`
(TW experiment+scenario) · `proposal/factor_proposal.py`, `model_proposal.py` (experiment_cls hook
+ TW subclasses) · `utils/env.py` (`QTDockerEnv.prepare` no longer auto-downloads CN data) ·
`oai/backend/litellm.py` (embedding `encoding_format="float"`, chat `timeout`) · `oai/llm_conf.py`
(`chat_timeout`, default 600s) · `app/utils/health_check.py` (embedding `encoding_format`)

## Known pitfalls (learned the hard way — do not re-learn)

- **httpx streaming deadlock**: LLM streaming + connection-pool close can deadlock on an internal
  `threading.Lock` — the loop hangs forever with no traceback (log mtime frozen, process in S
  state). Mitigations already in place: run loops with `CHAT_STREAM=false`; `chat_timeout=600`
  patch in `llm_conf.py` makes stuck requests raise and retry.
- **qlib `test_end` off-by-one**: qlib's signal strategy peeks at `calendar[last_idx + 1]`. If
  `test_end` ≥ calendar end → `IndexError` in `qlib/backtest/utils.py:get_step_time`. Set
  `QLIB_FACTOR_TW_TEST_END` / `QLIB_MODEL_TW_TEST_END` to the penultimate calendar day (the
  wrapper script auto-trims from `~/.qlib/qlib_data/tw_data/calendars/day.txt`).
- **WSL 9p cwd flakiness**: on `/mnt/g`, `getcwd()` can transiently ENOENT, crashing
  `link_all_files_in_folder_to_workspace`. Export `FACTOR_COSTEER_DATA_FOLDER` (and `_DEBUG`) as
  ABSOLUTE paths so `Path.absolute()` never calls `getcwd()`.
- **Embeddings**: the proxy/deepseek path requires `encoding_format="float"` (already patched).
- **No auto-download**: `QTDockerEnv.prepare` verifies the dataset exists but never downloads.
  TW data is only ever produced by our pipeline.
- **Factor-coder H5 contract**: LLM factor code reads `daily_pv.h5` with pandas (never qlib API).
  If you add fields to the bin, regenerate the H5 (`dump_factor_h5.py`) and keep the `FIELDS` list
  there in sync.
- **FinMind adjusted prices move**: `TaiwanStockPriceAdj` is back-adjusted to the fetch date, so
  adjusted closes fetched at different times must never be spliced. The collector's anchor
  check + `_ohlcv_restated/` handles it (D13); after a crashed or long-gap update run
  `--refresh_ohlcv`. Never hand-edit `_ohlcv_parts/_verified_through.txt`.
- **FinMind renames datasets without notice**: the old spelling `TaiwanstockGovernmentBankBuySell`
  now returns HTTP 422, and the May pull silently cached every failure as an empty day (D14). The
  collector now never caches failures, fails fast on 4xx and skips a dataset after 5 straight
  failures — after any collection run, grep its log for `WARN` and `skipping it this run`.
- **FinMind token rotation**: tokens are non-expiring JWTs with a `token_version`; once rotated,
  every call says "Token is illegal" (anonymous calls: "Your level is free"). The collector
  preflight aborts — get a fresh token from finmindtrade.com into `.env`. Never "fix" it by
  dropping to per-stock/free mode: `TaiwanStockPriceAdj` and by-date mode are Sponsor-only.
- **Adjusted prices, raw amounts**: in `ohlcv.csv` the prices are back-adjusted but `$amount` and
  `$volume` are raw, so `amount/volume` is a raw VWAP (2330, 2021-06-01: 596.9 vs adjusted close
  545.3). Rescale with raw closes or don't mix them (D17).
- **FinMind `stock_info` dates are record dates**: a board change (emerging → listed, TPEx →
  TWSE) appears only when FinMind re-records the stock, sometimes many months later. Date
  listings by the first `market_value_weight.csv` row instead (from 2024-10-30; D19).
- **`deepseek-chat` is deprecated**: DeepSeek announced its end for 2026-07-24, yet it still
  answered on 2026-10-24. It can stop without notice, and which model serves it is unknown (D16
  needs the training cutoff). Prefer `deepseek-flash` or `deepseek-v4-pro`; log the served model (J7).
- **Run the alpha loop with `conda activate rdagent`**, not the env's python by path: the CoSTEER
  coder reads `CONDA_DEFAULT_ENV` to pick the env for factor code, and fails without it.
- **Upstream proposal templates assume a qrun result**: `hypothesis_and_feedback` prints
  `exp.result.loc[...]`. A runner whose `exp.result` is anything else needs its own history
  rendering (`proposal/alpha_proposal_tw.py`), or round 2 crashes with jinja2 `'dict object' has no
  attribute 'loc'`. A one-round canary never reaches that code (first 10-round run, 2026-10-24).
- **LiteLLM's cost line undercounts DeepSeek**: on 2026-10-25 it said $1.18 for a run that cost
  $1.93. For real spend read `GET https://api.deepseek.com/user/balance` before and after (a script
  that sources `.env`; never print the key). When the balance hits 0 the loop dies with "Insufficient
  Balance" after 10 retries per call; the last saved step resumes it.
- **Resuming a loop: `--loop_n` is the total, not the remainder.** `LoopBase.run()` restarts
  `loop_idx` at 0 and each finished round uses up one count, so `--path <session>/7/4_record
  --loop_n 2` runs nothing; for rounds 8 and 9 pass `--loop_n 10`. With `checkout` (the default in
  `alpha_tw.py`) the resume deletes the logs of every step after the loaded one.
- **Instrument codes are text**: read every CSV with `dtype={"instrument": str}` (and
  `stock_id`). pandas infers dtypes per chunk, and `zfill(4)` cannot restore `006201` once it
  became int 6201, a real stock. Until 2026-10-22 `dump_to_bin.py` did this: 6,353 margin rows of
  9 ETFs (0062xx, 008201) collided with real stocks in the bin.
- **WSL has ~11 GB RAM, and an OOM kills the whole tmux job**: systemd stops the entire tmux scope,
  so `set -e`/ERR traps never run and the job just vanishes (check `dmesg`). Never `pd.concat`
  every part of a multi-GB dataset (option_daily ≈ 2 GB of CSV); stream to disk
  (`collect_finmind._stream_to_csv`). Don't run heavy analysis alongside `dump_to_bin`.

## Gitignore quirks to know

Root-level `*.sh` and `scripts/` are **ignored** — the operator wrapper scripts
(`_run_factor_tw.sh`, `_launch_rd_loop.sh`) exist only on the user's disk; their logic is
documented in `docs/tw/RUNBOOK.md`, keep that in sync. `AGENTS.md` is ignored; `CLAUDE.md` is
tracked. `.env` is ignored; `.env.example` documents the required keys. `data/` is ignored except
`data/DATA_AUDIT_REPORT.md` (explicit negation) — notably **`data/factor_ideas.md` is local-only
by choice** (user's alpha ideas); it steers the factor loop and has no backup in git.
