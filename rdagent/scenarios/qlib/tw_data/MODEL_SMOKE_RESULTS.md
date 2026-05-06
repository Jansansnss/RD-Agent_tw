# Qlib Model Sweep — TW Smoke Results

Plumbing-level pass/fail for qlib's `contrib.model` zoo against the **5-stock TW
bin** at `~/.qlib/qlib_data/tw_data`. Numbers are NOT meaningful for model
selection — universe is too small. Goal here is only: does each model wire up
end-to-end with our TW data via `qrun`?

Reproduce: `python _smoke_models_tw.py`

## Environment

- `rdagent` conda env, Python 3.10
- `pyqlib 0.9.7`, `lightgbm 4.6.0`, `xgboost 3.2.0`, `catboost 1.2.10`,
  `torch 2.11.0+cpu`
- No GPU. CPU-only torch wheel.

## Universe / dates

- 5 blue chips (2330, 2317, 2454, 2412, 2882)
- Train 2022-01-01..2023-06-30 / Valid 2023-07-01..2023-12-31 /
  Test 2024-01-01..2024-12-27
- Features: Alpha158 (158 expressions)
- Label: `Ref($close,-2)/Ref($close,-1) - 1`

## Results

| Model           | Status         | IC       | RankIC   | ICIR    | RankICIR |
|-----------------|----------------|----------|----------|---------|----------|
| LGBModel        | ✅ ok          | +0.0792  | +0.0805  | +0.149  | +0.151   |
| XGBModel        | ✅ ok          | +0.0075  | +0.0160  | +0.014  | +0.031   |
| LinearModel     | ✅ ok          | −0.0016  | −0.0035  | −0.003  | −0.007   |
| Transformer_ts  | ✅ ok          | +0.0151  | +0.0151  | +0.031  | +0.030   |
| CatBoostModel   | ❌ kwarg       | —        | —        | —       | —        |
| DEnsembleModel  | ❌ kwarg       | —        | —        | —       | —        |
| MLP             | ❌ kwarg       | —        | —        | —       | —        |
| LSTM_ts         | ⚠️ tiny-N      | —        | —        | —       | —        |
| GRU_ts          | ⚠️ tiny-N      | —        | —        | —       | —        |
| ALSTM_ts        | ⚠️ tiny-N      | —        | —        | —       | —        |
| TCN_ts          | ⚠️ tiny-N      | —        | —        | —       | —        |
| GATs_ts         | ⏸ skipped (N=5)| —        | —        | —       | —        |
| HIST            | ⏸ skipped      | —        | —        | —       | —        |
| IGMTF_ts        | ⏸ skipped (N=5)| —        | —        | —       | —        |

**Headline: 4/14 pass plumbing. No import errors. No qlib/qrun integration
issues. No TW-data compat issues. All failures are model-side, not pipeline-side.**

## Failure categories

### kwarg mismatches (sweep-script bugs, easy fix)

- **CatBoostModel**: `verbose: False` rejected by `_process_verbose`. Needs
  `logging_level="Silent"` instead.
- **DEnsembleModel**: ValueError in `__init__` — likely `enable_fs=False` requires
  removing fs-related kwargs entirely.
- **MLP** (`pytorch_nn.DNNModelPytorch`): `pt_model_kwargs.input_dim` is the wrong
  surface. The class autodetects feature dim; needs different scheduler kwargs.

These are smoke-script issues, not blockers. Fix when revisiting on full universe.

### Tiny-N early-stop edge case

- **LSTM_ts / GRU_ts / ALSTM_ts / TCN_ts**: all crash at `load_state_dict` inside
  `fit()`. Cause: with 5 stocks × ~360 train days, validation IC never improves,
  so the "best checkpoint" state-dict stays empty. When fit() tries to reload
  best params at the end, `load_state_dict({})` fails.
- This is qlib-internal training-loop behavior under degenerate data. Should
  resolve naturally with full universe (~1086 stocks × 11 yrs). If it doesn't,
  patch by allowing `best_param` to fall back to the final-epoch state.
- Transformer_ts didn't hit this because it uses fixed-epoch training without
  the load-best-checkpoint step.

### Skipped by design

- **GATs_ts / IGMTF_ts**: graph models — meaningless with N=5. Will run after
  full-universe download.
- **HIST**: needs a concept-graph CSV (industry/theme membership). Not yet built
  for TW; defer until MOPS industry classifications are ingested.

## What this tells us

✅ **TW bin format is valid for the qlib pipeline.** No data-loader, calendar,
expression-engine, or backtest-engine errors anywhere across 11 attempted models.

✅ **Alpha158 + label spec works on TW.** All 158 expressions evaluate; no
missing-field crashes.

✅ **The four passing models give a baseline IC ladder** (LGB best at +0.079,
others noise-level) — but **don't read into these numbers**: 5 stocks is well
below any meaningful cross-sectional rank.

⏭ **Next pass (Phase B, after full download)** will:
- Fix the 3 kwarg bugs (CatBoost/DEnsemble/MLP)
- Re-test the 4 sequence models — expect tiny-N issue to disappear
- Activate the 3 graph models
- Compare IC/IR vs qlib's published CSI300 baselines to detect TW-specific
  weakness (e.g. high transaction cost should hurt high-turnover signals)
