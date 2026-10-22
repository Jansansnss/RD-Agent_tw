# TW Decision Log

Append-only. One entry per non-obvious decision: what, why, and what would make us revisit.
Reconstructed 2026-07-09 from code comments and commit history; entries before that date are
back-filled.

---

**D1 — FinMind is the primary data source; TWSE-direct kept as fallback** *(May 2026)*
FinMind covers 75+ datasets behind one token (fundamentals, revenue, dividends — things TWSE APIs
don't expose) and `TaiwanStockPriceAdj` is already split-adjusted. `collect.py` (TWSE official
endpoints, raw tick-grid prices, `$factor`=1.0) stays schema-compatible for the core 5 CSVs so
`dump_to_bin.py` accepts either. Revisit if: FinMind pricing/tier changes or data quality drifts
vs. exchange truth.

**D2 — Non-destructive cleaning** *(2026-05-11)*
`clean_csvs.py` writes to `tw_finmind_full_clean/`, never mutates the raw pull. Cost: ~9 GB
duplicated. Justification: two audit rounds found bugs in our own reconstruction logic
(`dealer_net` 97.3% NaN) — being able to re-clean from untouched raw data caught it.

**D3 — Universe filter follows the official TWSE securities-coding rules** *(2026-05-12)*
Keep 4-digit stocks/ETFs, preferred shares, ETF/ETN/REIT patterns; drop warrants, convertible
bonds, sector-index pseudo-instruments (`TAIEX`, `Electronic`, …) that FinMind mixes into ohlcv.
Regex table + statute reference in `clean_csvs.py`. The `high<low` "violations" that triggered
the audit were all sector indices.

**D4 — TW integrates via subclass hooks, not forked modules** *(May 2026)*
Upstream classes gained overridable attrs (`prop_setting_cls`, `experiment_cls`,
`_load_source_data()`); TW subclasses live in `*_tw` files. Alternative — copying whole
scenario modules — would rot on every upstream sync. Cost: a dozen upstream files carry small
patches (list in CLAUDE.md). Revisit if upstream adds native multi-region support.

**D5 — `QTDockerEnv.prepare` no longer auto-downloads CN data** *(May 2026)*
Upstream silently downloads the CN dataset when missing; for TW that would mask a broken mount
and waste GBs. Now it verifies-only. Datasets are always produced out-of-band by our pipeline.

**D6 — Loops run with `CHAT_STREAM=false` + finite `chat_timeout`** *(May 2026)*
httpx streaming + pool-close deadlocks on a `threading.Lock` — hangs are silent and unrecoverable
(hours of frozen logs). Non-streaming avoids the path; the `chat_timeout=600` patch converts any
residual stall into a retryable exception. Revisit when httpx/httpcore fix lands upstream.

**D7 — Rolling `test_end` = penultimate calendar day, via env override** *(May 2026)*
qlib peeks one step past `test_end` for next-day execution. Rather than hand-bumping the setting,
the wrapper script derives it from `calendars/day.txt` and exports `QLIB_FACTOR_TW_TEST_END`
(pydantic env prefix does the rest).

**D8 — Factor ideas seeded from a user-editable file, re-read every iteration** *(May 2026)*
`data/factor_ideas.md` (WorldQuant-style DSL + guidance) is appended to the hypothesis prompt by
`QlibTwFactorHypothesisGen`. Redirecting the search = edit a text file, no restart. Path override:
`FACTOR_TW_IDEAS_PATH`.

**D9 — Financial statements stay long-format for now** *(May 2026)*
`fs_income/balance/cashflow.csv` keep FinMind's `(date, instrument, type, value)` layout;
`dump_to_bin.py` skips them (assumes wide). Pivoting a curated subset into bin features is
roadmap item 3 — deferred until the factor loop demonstrates value on price/flow signals first.

**D10 — Factor-coder reads a materialized `daily_pv.h5`, mirroring CN** *(May 2026)*
CN factor code reads an H5 with pandas and never calls qlib. TW does the same
(`dump_factor_h5.py`) rather than teaching CoSTEER templates to `qlib.init()` — zero prompt/
template divergence from upstream. Cost: one more dump step after every data refresh.

**D11 — Governance docs in-repo, English, model-first** *(2026-07-09)*
CLAUDE.md (harness) + docs/tw/ (state, decisions, runbook), committed and pushed, because the
operator scripts and session knowledge previously lived only on one machine. English to match
codebase and maximize model parsing reliability.

**D12 — Multi-agent operating model with tiered model routing** *(2026-07-09)*
Sessions run as a role-based team with fixed context budgets (AGENT_PLAYBOOK.md): frontier models
(Fable/Opus) plan and judge, Sonnet builds, Haiku reads and grinds; the RD-loop inner LLM stays
deepseek (volume pricing). Comms are hub-and-spoke through the PM plus the tracked docs —
workers never exchange raw context. Roadmap is a branching state machine (TODO_GRAPH.md) with
retry ladders and jump rules instead of a linear list. Chosen because full-repo context per agent
is unaffordable; silo risk is mitigated by bounded reports and docs-as-single-source-of-truth.
Revisit if long-context pricing collapses or the harness gains shared memory between subagents.

**D13 — OHLCV cache self-heals FinMind's back-adjustment** *(2026-10-22)*
`TaiwanStockPriceAdj` re-anchors all history at every ex-right/ex-dividend event (verified: the
closes cached in May equal raw closes on their newest day and sit 1–9% below raw a year earlier).
Appending new days to an old per-day cache therefore puts a fake dividend-sized return at the seam
— after a Jun–Sep dividend season, for most stocks, inside the test window. Each by-date run now
re-fetches the last verified day (`_ohlcv_parts/_verified_through.txt`) and the trading day before
it (an ex-date on the anchor day only rescales earlier days); stocks whose close moved,
or that resumed after a gap, get their full history re-fetched per stock into `_ohlcv_restated/`,
which overrides the parts. Cost: 1 call/run + 1 per re-adjusted stock. Rejected: re-downloading
the whole history every run (~4.4K calls/day). Deferred: raw `TaiwanStockPrice` + our own `$factor`
from `TaiwanStockDividendResult` (proper qlib semantics, but must reproduce FinMind's adjustment
math; ties into roadmap item 4). Revisit if FinMind exposes adjustment factors directly.

**D14 — Never cache a failure; ranged caches get explicit refresh** *(2026-10-22)*
`FinMindClient.get` returned `[]` on any error, so a rejected token ("Token is illegal", seen
2026-10-22) would have written months of empty parts that are never re-fetched. Failures now
return `None` and are skipped; a startup preflight aborts on a dead token or lost tier; empty
answers for the last 7 days (not yet published) are left uncached. Per-stock fundamentals were
cached forever — frozen at the first pull — so `--sparse_max_age_days` re-fetches by part mtime
(resumable); macro per-id series (45 calls) always re-fetch. `daily_update.sh` now skips the broker
pull, which with the default `--brokers` would have rewritten the 49-broker
`broker_secid_agg.csv` with broker 1440 only. Known gap: fundamentals cover only stocks listed at
fetch time (TaiwanStockInfo), so delisted names are missing — fix before S3 puts them in the bin.
Proof it had already happened: `gov_bank` was requested as `TaiwanstockGovernmentBankBuySell`
(an old FinMind typo spelling, now HTTP 422); all 4,266 May parts were failures cached as empty
days (forensic: ~14.4 s between part mtimes = 3 retries + backoff), so the dataset was never
collected. HTTP 4xx (except 402/429 rate limits) now fails fast with a WARN instead of silent
retries, and a dataset whose first 5 requests all fail is skipped for the run.

**D15 — Contest alphas are judged by an in-process pandas judge, not qrun + LightGBM** *(2026-10-22)*
For AI CUP 2026 (150 fixed stocks, 20–30 long-only names, next-day VWAP fills, 0.585% round
trip), the factor loop's qrun template cannot tell whether an alpha helps: `market: all`, a top-3
book, close fills, next-day label, 2330 benchmark, and the LLM picking SOTA from test-period
metrics. `alpha_eval_tw.py` scores in seconds on a point-in-time universe (monthly top 100 TWSE +
50 TPEx; the fixed July-2026 list would flatter momentum) and gates in code: IC t ≥ 3 at the
declared horizon, ≥ 3 positive years, 24-day median net excess > 0, |corr| ≤ 0.6 with the library,
coverage ≥ 80%. New TODO line T7–T10, G3–G4, S5–S6 and jump rule J7. Revisit if the contest
rules change or a model-combination step needs qrun again.

**D16 — LLM look-ahead protocol for the alpha factory** *(2026-10-22)*
Research window 2021-01-01 → 2025-06-30, because DeepSeek V4's training data reaches
"approximately no later than May 2025" (DeepSeek's EU training summary). Holdout 2025-07-01 →
today is scored only by a script outside the loop, once per batch that passed the gate, and never
shown to the LLM. Prompts carry no tickers, names or dates; alpha code with stock-code or date
literals is rejected; the served model string is logged per run. Anonymizing alone is not enough:
LLMs rebuild names and dates from small clues (Lopez-Lira, Tang & Zhu 2025), and the main leak
here is hindsight about which styles won. `deepseek-flash` follows DeepSeek's latest model, so a
newer model with a later cutoff moves the boundary. The live contest is the only fully clean test.
Revisit when a point-in-time LLM (training cutoff before the research window) is affordable.

**D17 — VWAP from raw closes where we have them; revenue known on the 10th** *(2026-10-22)*
`ohlcv.csv` prices are back-adjusted (TaiwanStockPriceAdj) but `$amount`/`$volume` are raw, so
amount/volume is a raw VWAP. The alpha panel rescales it with TWSE-direct raw closes
(`data/tw_raw_full/ohlcv.csv`: TWSE only, to 2026-05-05) and falls back to the adjusted typical
price (H+L+C)/3 elsewhere; `$vwap_exact` flags rows. TODO S1 as written would have mixed scales.
Monthly revenue becomes known on the 10th of the following month (legal deadline), not on
FinMind's `date` (1st of that month). Revisit with S5 (collect raw `TaiwanStockPrice`).

**D18 — Fundamentals refresh mirrors FinMind, deletions included; May copy archived** *(2026-10-23)*
The first `--sparse_max_age_days` refresh found FinMind serving 474,567 fewer `fs_balance` rows
(5.7%, 2,331 stocks) than in May. 75% of them are 2012 quarters (the ROC GAAP → IFRS switch), the
rest scattered; every surviving value is identical. The other five fundamentals lost nothing.
Merging the old rows back was rejected for now: 98% of the dropped quarters satisfy
assets = liabilities + equity, but 1.6% are corrupt (Equity = TotalAssets), and the 2012 ones may
mix accounting standards. So a refresh mirrors the source, and the May copy stays in
`data/tw_finmind_full_clean_2026-05-backup/`. Revisit at S3: restore identity-checked quarters if
history depth matters there.

---

*Template for new entries:*

**Dn — Title** *(date)*
Decision. Why. Cost/alternatives. Revisit-when.
