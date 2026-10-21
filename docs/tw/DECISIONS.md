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
re-fetches the last verified day (`_ohlcv_parts/_verified_through.txt`); stocks whose close moved,
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

---

*Template for new entries:*

**Dn — Title** *(date)*
Decision. Why. Cost/alternatives. Revisit-when.
