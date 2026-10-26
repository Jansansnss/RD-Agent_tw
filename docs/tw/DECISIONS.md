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

**D19 — Alpha universe ranks by index weight; listings dated by first index weight** *(2026-10-23)*
The contest's 150 match TWSE/TPEx index-weight ranks on 2026-07-31 (149/150), not raw market
caps (台灣高鐵 is 90th by cap, 145th by weight). So `select_universe` ranks by
`market_value_weight.csv` where a month-end snapshot exists (from 2024-10-30; TWSE lacks Nov 2024
to Jan 2025 and Mar 2025) and by market cap before that. FinMind's `stock_info.csv` board records
are dated when FinMind recorded them, not when the board changed: 8 of the first build's 9 misses
were 2025–26 listings still marked emerging (7769 listed Jan 2026, record dated 2026-10-22), and
6472 showed TPEx two years after moving to TWSE. For the 160 stocks with an emerging record, the
first index-weight day is the listing date (seasoning counts from it); market type follows the
weights when one is within 95 days. Before 2024-10 FinMind has no emerging records, so this
can't help there; only 2 member-months since 2021 show a > 10.5% daily move (no price limit) in
the 60 days before selection. Revisit if exchange listing dates are collected; they would also
settle the bin-universe question under TODO D1.

**D20 — How the alpha loop is wired** *(2026-10-24)*
The TW factor loop's proposal and CoSTEER coder are reused unchanged; a new runner and feedback
replace qrun and the LLM's SOTA call. Choices: the coder reads `data/alpha_tw/coder/`, which has
stock codes replaced by S001.. (dates kept, so revenue timing stays easy to code) and stops at the
research end, so loop code never runs on holdout data; `$in_official` is dropped (the July-2026
list is future information); code with an ID, stock-code or date literal is rejected unscored; an
alpha passes at the 5- or 20-day horizon (two looks, under the t >= 3 bar); passing factors go to
an on-disk library (card + code + values) that is also the correlation reference; the holdout
runs only in `alpha_holdout_tw.py`. Research scope is any factor that could win a short contest
(Jans, 2026-10-24); mapping to one of the five declared families happens at export, to keep the
prospectus theme rule. Coder repair rounds default to 6 (cost). Revisit after the canary measures
cost per round.

**D21 — Alpha split moved: research to 2026-04-30, holdout from 2026-05-01** *(2026-10-24)*
Jans asked for both windows to move to recent data. Research is now 2021-01-01 → 2026-04-30 and the
holdout 2026-05-01 → latest (2026-05-04 → 2026-10-22, about 117 trading days); the coder's quick-run
slice is 2025-05 → 2026-04. Why: the old research window ended 16 months before the contest and
missed the 2025–26 market; the holdout still starts a year after DeepSeek V4's data (~May 2025), so
D16's clean-window rule holds better than before. Cost: six months confirm with less power
(HOLDOUT_GATE t ≥ 2 unchanged), and the baselines' new holdout is not virgin (their old holdout
covered it). Effect: `rev_accel` and `rev_yoy` now pass the research gate at 5 days (t 3.5, 3.1),
while on the new holdout all three traded signals lose (ALPHA_FACTORY §6). The split lives once,
in `alpha_eval_tw.WINDOWS`; `dump_alpha_panel_tw.RESEARCH_END` reads it. Revisit if the served
model's training data reaches past 2026-04, and before the last pre-contest refresh.

**D22 — Alpha library seeded with the three traded signals** *(2026-10-24)*
`data/alpha_tw/library/traded_{rev_accel,rev_yoy,mom60_skip5}/values.parquet`, computed with
`alpha_eval_tw.BASELINES` on the anonymized research panel, no `card.json`: the loop's correlation
rule (≤ 0.6) now rejects copies of what the daily agent already trades, and the holdout script and
export skip them. Needed because under D21 both revenue baselines pass on their own, so the loop's
first passes would be rediscoveries. Cost: a better variant that stays close to a traded signal is
rejected too (first case: `rev_yoy` z-scored against its own year, t 3.2 at 5 days, correlation
0.73). Delete a folder to drop its seed. Revisit when the agent's strategy weights change, or add a
"replaces a seed" rule if near-variants keep winning.

**D23 — Passes of one round are compared with each other too** *(2026-10-25)*
The runner gated every factor against the library as it stood before the round, so round 29 of
the flash campaign put four window variants of one idea into the library at once (correlation
0.72–0.92 with each other). `alpha_runner_tw.dedupe_round` now ranks a round's passes by their best
t; each later pass must also stay within the 0.6 correlation limit of the ones kept, or it fails
with that reason. Highest t first because the gate already treats t as the evidence; the holdout
showed research t does not predict holdout t, so this is a tie-break, not a quality claim. The four
round-29 cards stay in the library as correlation references (none passed the holdout). Revisit
with the "replaces a card" rule (D22).

**D24 — Every holdout pass gets a written review before it is used** *(2026-10-25)*
Jans: 以後有通過保留期的都要寫出來加以檢視. A confirmed card is a candidate until
`docs/tw/ALPHA_REVIEWS.md` has its entry: the formula read from `factor.py`, a code audit (timing,
look-ahead, units), what it buys, its correlation with plain exposures, the official-list check, the
monthly pattern of the holdout excess, and a verdict. `tw_data/alpha_review_tw.py` produces the
numbers. Why: the first two confirmations were a size tilt and a sales-yield tilt (correlation 0.94
with the plain exposure each), which the gate cannot see. Revisit: extend the exposure list when a
review finds a new disguise.

**D25 — Where the factor-reviewer prompt sits** *(2026-10-25)*
Jans supplied a reviewer prompt (what the formula measures vs what is claimed, necessary
conditions, a counterexample, mixed effects, the smallest fix, discriminating experiments; output
A–F). The full version is `tw_data/alpha_review_prompt_zh.md`, plus two project rules (the holdout
confirms once; research and holdout numbers are quoted apart). `alpha_review_tw.py` fills its inputs
for each confirmed card, and a model other than the loop's (Claude) writes the A–F review into
ALPHA_REVIEWS.md. A three-sentence version sits in the feedback prompt (`alpha_feedback_tw.py`):
what the formula measures, what the hypothesis claims, the missing condition, so the next idea can
test it. Review lessons go back into `data/alpha_ideas.md` only if they rest on research-period data.
Not at hypothesis generation or coding: a screen that rejects weak stories would have flagged both
holdout-confirmed cards (a flipped size premium; thin margins read as cheap), and LLMs judge factor
quality poorly without a backtest (AlphaBench, ICLR 2026). The waste is in code repair, not ideas:
76 of the 78 factors that ever coded did so within two repair passes, while passes 3–6 on the 9
that never coded took about 60% of the flash run's calls and time. Revisit if the in-loop critique
slows rounds or proposals parrot it without better scores.

**D26 — The coder's debug panel holds two years, not one** *(2026-10-26)*
CoSTEER checks factor code on `coder_debug` before the full research run. With one year
(2025-05 → 2026-04, 245 days) every 250-day window was all NaN there, so round 30 of the third
campaign never scored its two 252-day factors, and round 31 proposed two more. `DEBUG_START` is now
2024-05-01 (484 days); the rebuilt file matches the old one on the overlap. Cost: debug runs take
about twice as long, still seconds. Revisit if windows longer than a year become common.

**D27 — The loop keeps a $0.50 reserve on the DeepSeek account** *(2026-10-26)*
Jans asked to run until the balance is gone. The contest's daily agent (`AI_cup_2026/agent/llm.py`)
calls deepseek-flash on the same account for its D-Plan texts and falls back to templates when a
call fails. A watchdog in the run script reads the balance every 5 minutes and stops the loop below
$0.50, enough for the agent's few small calls a day through the contest month. Revisit if the
agent gets its own key.

**D28 — Signals are combined with fixed weights, not a trained model** *(2026-10-26)*
`alpha_combiner_tw.py` ran the model report's walk-forward test. On 2024-01 → 2026-04, LightGBM
(IC5 t 1.2, 24-day median −0.31%) and learned linear weights (t 1.6, −0.03%) both lost to the
equal-weight rank average of the same five signals (t 5.1, +1.19%) and to the agent's 50/20/30
(t 4.1, +2.44%). Every fold gave its largest share to sales yield (29–67% of the gain), 2021–2023's
best signal, which had no IC after 2023 (t 0.1). So additions to the agent's mix use fixed weights,
and the contest period decides between equal weights and 50/20/30 by paper tracking. Revisit with
several more years of data or a much stronger input.

**D29 — Teammates' ideas enter the loop with their research results** *(2026-10-26)*
Alisa's strategy review became B1–B14 in `data/alpha_ideas.md`, first scored as fixed formulas
(`alpha_team_ideas_tw.py`): none passes the research gate. Each idea carries its research-period
result, so the LLM tests variants instead of resubmitting failed plain forms, and tags hypotheses
with the idea number. Holdout numbers stay out of the file. The ideas were written after the
holdout months, so a card derived from them that passes the holdout is a reference only; the
contest period confirms it. Revisit when more teammate ideas arrive (C1.., same rules).

**D30 — Industry groups come from the industry chain, not the exchange category** *(2026-10-26)*
Jans: 未來產業分類先以 relations_groups_review_T2026-10-21_v001.pdf 為主；過去有關產業分類的測試要
重新跑. The PDF is the team's review of FinMind TaiwanStockIndustryChain (TPEx 產業價值鏈) for the 150
contest stocks. The same dataset fetched on 10/26 (`collect_finmind` now saves it as
`industry_chain.csv`) matches the PDF's 559 rows exactly and covers 298 of the panel's 305 stocks. A
stock can carry several chain industries (台達電 21), so `chain_groups_tw` picks one primary group:
the industry with the most sub-industry rows among those fitting the exchange category, ties to the
category's main industry, one override (中租-KY → 金融); the 7 stocks without chain rows (delisted)
take their category's main industry. 37 groups. `$industry_id` was swapped in all four panels in
place (backup `data/alpha_tw/_industry_twse_backup/`), so prices and windows are unchanged; the
builder does the same on a rebuild. Re-run: Alisa's group ideas (also with every tagged industry
counted, `alpha_team_ideas_tw --multi`, as the PDF counts them), the 61 judged loop factors that
read `$industry_id`, the holdout check and the two reviews. No verdict improved; sales yield ranked
within groups rose from t 2.13 to 2.35. Cost: the tags are a 2026-10 snapshot, so group factors over
2021–2026 assume industries did not change, and themes such as 雲端運算 or 人工智慧 were tagged with
hindsight. Revisit with dated chain snapshots for point-in-time groups.

**D31 — Groups follow the team's full classification: V2 product groups first** *(2026-10-26)*
Jans, two hours after D30: 更新改為 full_classification_relations_T20261021_preview.pdf 為主；過去有關
產業分類的測試要重新跑. Part 1 of that PDF is a product-exposure taxonomy (V2, provisional): 6
families / 38 sub-groups, 217 memberships of 171 stocks (光通訊, 記憶體, PCB, 高速電互連, 半導體設備,
測試介面); part 2 is the industry chain of D30. `chain_groups_tw --product_pdf` parses the member
table (the counts match the PDF's own: 217 / 171 / 38) into `product_groups_v2.csv`; a stock with a
V2 membership takes its family ("V2:PCB"; most sub-groups, ties to the more specific family, so 旺矽
→ 測試介面), every other stock keeps its D30 chain group. 62 of the panel's 305 stocks get a V2
family; 42 groups; in the latest universe 47 of 150 names have a V2 family and 半導體 keeps 29. Re-run as for D30
(team ideas with and without multi-membership, 73 judged loop factors, holdout, reviews): no new
library card (two research passes appear but correlate 0.79 / 0.71 with existing cards); sales yield
ranked within groups now has research t 2.92 (raw 2.74), holdout 1.68 (raw 2.28). Cost: stronger
hindsight than D30; the six V2 families are the 2025-26 AI-hardware themes, and their members were
14% of the universe in 2021 and 29% in 2026. Revisit when the frozen (non-preview) V2 is published.

---

*Template for new entries:*

**Dn — Title** *(date)*
Decision. Why. Cost/alternatives. Revisit-when.
