# TW FinMind Data Audit Report

Source: `data/tw_finmind_full/` (35 CSVs, ~9 GB, 2010-01-01 → 2026-05-08)
Audited: 2026-05-11 (revised 2026-05-12 to follow TWSE coding rules)
Cleaned output: `data/tw_finmind_full_clean/` (produced by `rdagent/scenarios/qlib/tw_data/clean_csvs.py`)
Benchmark series: `data/tw_finmind_full_clean/benchmark.csv` (TAIEX, TPEx, 0050)

---

## Refresh 2026-10-23 — data through 2026-10-22

Catch-up after 5.5 idle months (RUNBOOK §1), then one regular `daily_update.sh` run. Every file in
`tw_finmind_full_clean/` (38 CSVs) was compared with the six months before the cut (2026-05-11).
All fixes are in code, and the data was regenerated, never hand-edited.

**Pipeline bugs found and fixed**

| # | Finding | Impact | Fix |
|---|---|---|---|
| 1 | FinMind back-adjusts prices to the fetch date: vs the May cache, 58% of instruments had a different 2026-05-08 close (median −3.2%, range ×0.04–×3.6) | appending new days would have planted fake returns at the seam (e.g. −66% for 6669's 3:1 split on 2026-09-02) | prices rebuilt (`--refresh_ohlcv`); collector re-checks two anchor days and restates moved stocks (DECISIONS D13) |
| 2 | `gov_bank` requested under an old dataset name (now HTTP 422); every May part was a failure cached as an empty day | the dataset had never been collected | name fixed, failures never cached (D14); backfilled: 18.4M rows, coverage from ~2021 |
| 3 | `dump_to_bin.py` parsed instrument codes as numbers in some chunks: `006201`→`6201` | 6,353 margin rows of 9 ETFs (0062xx, 008201) collided with real stocks 6201–6208/8201 in the May bin (the "6,344 duplicates dropped") | codes read as text; new bin merges with 0 duplicates |
| 4 | `option_daily` (~2 GB) merged in memory | OOM kill on the 11 GB WSL box; systemd silently killed the whole job | long-format merges stream to disk |
| 5 | Cleaner's stock-file list had a stale name; `broker_secid_agg.csv` and `gov_bank.csv` skipped the universe filter | unfiltered codes | list fixed (gov_bank: −18,166 rows) |
| 6 | Two cached empty days FinMind now serves (institutional 2012-02-29: 45 rows; block_trade 2011-09-20: 14 rows) | holes | re-fetched |

**Source-side changes (not our bugs)**

- `fs_balance`: FinMind now serves 474,567 fewer rows (5.7%, mostly 2012 quarters, the IFRS
  switch). Mirrored; the May copy is in `data/tw_finmind_full_clean_2026-05-backup/` (D18). The
  other fundamentals lost nothing and gained back history.
- Institutional coverage varies at the source: median rows/day ~3,400 (H1 2025) → ~2,200
  (Sep 2025–Apr 2026) → ~3,100 (Oct 2026). Re-fetching a dip day returns +0.4% codes only, so it
  is not a stale cache. Factors on `foreign_net`/`trust_net` see a changing cross-section.

**Open: 興櫃 emerging-board stocks are in the universe.** Their 4-digit codes pass the coding
rules, but the board has no price limit and trades thinly through dealers: about 390 of 3,041 bin
instruments by FinMind's latest `stock_info` records (which can lag a board change), ~9% of 2021+
rows, and 93% of the >10.5% daily moves in the new window. FinMind's price history also includes
the pre-listing emerging months of stocks that listed later (e.g. 77xx/78xx codes moving −20% to
−40% on 2025-04-07). `stock_info.csv` dates are record dates, not board-change dates, so they
can't date listings; the first `market_value_weight.csv` row can, from 2024-10-30 (D19).
Filtering is a modelling decision, not applied.

**New-window checks (2026-05-11 → 2026-10-22, 114 trading days)**: no seam at the old cache edge
(2026-05-11 median return +0.00%, TAIEX +0.45%) · core-5 coverage ≥ the prior six months on every
day · no duplicate keys · no NaN drift > 5 pp · 76 new listings all classified by the coding rules
(0 dropped) · flagged values traced to real events (6669/5274 splits and rights in
`dividend_result`; one 10,000 replacement in `loan_collateral`; 42 identical block-trade rows).

**Also found**: `$amount` is written to the bin as `$amount.day.bin`, so qlib's `$amount` reads
nothing (relevant to S1/S5).

---

## Executive Summary

The full FinMind collection (35 datasets, ~9 GB) is in place. Two rounds of audit identified data-quality issues and one reconstruction bug in our own pipeline. Cleaning is non-destructive: corrected files land in `tw_finmind_full_clean/`; the raw collection stays untouched. The collector itself is patched so future runs are correct from the start.

The most important findings:
1. **`institutional.dealer_net` was 97.3% NaN** due to a conditional-reconstruction bug we wrote. Fixed in both cleaner and collector.
2. **`ohlcv.csv` mixes 32 sector indices with stock data**; they appear as instruments named `Electronic`, `IronSteel`, `TAIEX`, etc. The `high < low` invariant violations we initially saw all came from these. Filtered out.
3. **Negative futures prices are real, not sentinels** — they're calendar-spread contracts (e.g., `contract_date = 201001/201003`). Don't NaN them.
4. **`loan_collateral.csv` has 14 mostly-zero columns**; only 3 are truly dead. The 1-2% non-zero ones (`SecuritiesFirmLoanBuy`, etc.) are rare-event signals worth keeping.

## Methodology

For each CSV, we computed per-column NaN%, zero%, negative%, min/max. Large files streamed in 500K-row chunks. The deeper audit examined:
- Cross-column invariants (high≥low, percent ≤ 100, etc.) to detect column-shift mis-fills
- Distribution of negative values to differentiate sentinels (few discrete values) from real data (spread)
- Non-zero distribution in sparse columns to determine signal value
- Per-instrument anomaly rates to spot structurally-broken stocks

Reports flag:
- **!N** — column is >50% NaN after numeric coercion
- **!P** — negatives in a price/volume column
- **!Z** — column is >95% zero

A flag is not automatically a bug. Below we interpret each one.

## Universe filter — follows TWSE official coding rules

Source: **中華民國證券市場編碼原則** (2022-01-18, 金管證券字第1100375967號).

### KEEP — tradable equities and equity-like trusts

| Pattern | Type | Examples | Doc §|
|---|---|---|---|
| `^\d{4}$` | Stocks + classic ETFs | `1101`, `2330`, `0050`, `9955` | §1-4 |
| `^\d{4}[A-Z]$` | 特別股 preferred | `2882A` | (preferred variants) |
| `^\d{4}[A-Z]\d$` | Preferred variants | `2887Z1` | |
| `^00\d{2,4}[A-Z]?$` | ETF / ETN / 受益證券 | `00878`, `006208`, `00625K`, `00631L` | §17-18 |
| `^\d{2}\d{3}[TPLRBS]$` | REIT (T) / REAT (P) / ETN-L / ETN-R / ETN-B / ETN-S | `01001T`, `02001L`, `02001R`, `03001P` | §16-18 |
| `^02\d{4}$` | ETN base 6-digit no letter | `020001`-`020041` | §18 |

### DROP

| Pattern | Type | Examples | Doc § |
|---|---|---|---|
| `^[A-Za-z]+$` | Sector indices (not in TWSE coding doc) | `Electronic`, `TAIEX`, `TPEx`, `IronSteel`, ... | n/a |
| `^[ABGFH]\d{5}$` | Government / corporate / financial / foreign bonds | `A12345`, `B12345`, `G12345`, `F12345` | §19-22 |
| `^T\d{5}$` | Open-end mutual funds | `T12345` | §24 |
| `^ST\d{3,4}D?$` | STO (security tokens) | `ST1234`, `ST123D` | §25 |
| `^\d{4}\d{1,2}$` | Convertible / exchange bonds | `23301`, `233012` | §5-6 |
| `^\d{4}\d[EW]$` | Equity-linked FX bonds | `23301E`, `23311W` | §23 |
| `^\d{6}$` not starting `00` or `02` | Warrants | `708785`, `911868` | (not in doc) |

### Result on `ohlcv.csv`

- **Raw**: 3,058 unique instruments, 8.1M rows
- **Kept**: ~3,000 (4-digit stocks/ETFs + preferred + REITs + ETN + new ETFs)
- **Dropped**: 32 sector indices (128K rows) + ~75 warrants (~93K rows) — about 220K rows / 2.7%
- **Extracted to `benchmark.csv`**: TAIEX, TPEx, 0050 (3 instruments × ~4,000 days)

The universe filter applies to every "stock-instrument" CSV (`ohlcv`, `institutional`, `margin`, `valuation`, `foreign_holdings`, `holding_shares_per`, `fs_*`, `month_revenue`, `dividend*`, `market_value*`, `day_trading`, `block_trade`, `loan_collateral`, `securities_lending`). It does **not** apply to contract-code files (`futures_*`, `option_*`) or macro/FX/bond files.

## Per-file cleaning rules

### `ohlcv.csv` — zero-volume placeholder + invariant violations

**Issue 1**: ~60K rows with `$volume == 0 AND $high < $open` are placeholders from corporate-action days where FinMind adjusted `$high`/`$low`/`$close` but left `$open` un-adjusted (e.g., `5287` on `2013-09-03`: `$open=296.75` while `$high=$low=$close=83.57`).

**Issue 2**: 1,522 rows where `$high < $low` (column corruption — all came from sector-index rows, now filtered out anyway).

**Rule**: drop rows where (`$volume == 0` AND `$high < $open`) OR (`$high < $low`).

Note: ~130K rows have `$high < $open` *with* non-zero volume. These are concentrated in specific low-liquidity stocks (e.g., `2248`: 56.9% bad rate, `8438`: 53.4%, `3597`: 52.8%) and represent FinMind's post-adjustment quirks on ex-dates. We do NOT drop these; they're noisy but not strictly wrong, and qlib's Fillna + RobustZScoreNorm absorbs them.

### `institutional.csv` — dealer_net reconstruction bug (HIGH)

**Symptom**: `dealer_net` 97.3% NaN; `inst_total_net` missing the dealer contribution for ~97% of rows.

**Root cause**: FinMind splits dealer activity into `Dealer_self` + `Dealer_Hedging` rows most days, but occasionally returns a combined `Dealer` row. The pivot produces a sparse `dealer_net` column from those occasional rows. Our reconstruction logic was gated on `"dealer_net" not in wide.columns` and thus skipped because the column technically existed.

**Rule**: recompute `dealer_net = dealer_self_net.fillna(0) + dealer_hedge_net.fillna(0)` unconditionally; recompute `inst_total_net = foreign_net + trust_net + dealer_net`.

**Collector patch**: same logic applied in `download_institutional` so future runs are correct from the start.

### `exchange_rate.csv` — `−1` sentinel (HIGH)

**Symptom**: `cash_buy`, `cash_sell`, `spot_buy`, `spot_sell` have 3-20% rows at exactly `−1.0`.

**Root cause**: Deep audit confirmed `−1.0` is the **only** distinct negative value (15,523 occurrences of `spot_buy = −1.0`, zero other negative values). It's FinMind's "no quote today" sentinel for thinly-traded currency pairs (MYR/IDR/PHP/KRW/VND).

**Rule**: Set FX rate columns to NaN where `≤ 0`.

### `valuation.csv` — `pe_ratio = −1` sentinel

**Rule**: set `pe_ratio` to NaN where `< 0` (FinMind sentinel for loss-making firms).

### `holding_shares_per.csv` — drop negative percent / unit rows

**Rule**: drop rows where `percent < 0` or `unit < 0`. Distribution shape (-35.9 to -0.01 spread, not discrete) suggests real data errors on corporate-action days, not a sentinel. Affects ~21K rows out of 22.5M.

### `foreign_holdings.csv` — clip pcts to [0, 100]

**Rule**: clip `foreign_held_pct` and `foreign_remaining_pct` to `[0, 100]`. Only 91 rows out of 7M violate; corporate-action denominator-snapshotting artifacts.

### `dividend.csv` — convert 民國 year to Western

**Rule**: `div_year += 1911` (99 → 2010, 115 → 2026).

### `loan_collateral.csv` — drop only truly-dead cols (REVISED)

Original plan: drop 14 mostly-zero columns. **Revised** based on user challenge: drop only the 3 truly-dead (0 non-zero across 5M rows). Keep the rare-event signals.

| Column | Non-zero count | Verdict |
|---|---|---|
| `SecuritiesFinanceSecuredLoanNextDayQuota` | **0** | **DROP** |
| `SettlementMarginSell` | **0** | **DROP** |
| `SettlementMarginReplacement` | **0** | **DROP** |
| `SecuritiesFirmLoanBuy` | 58,242 (1.14%) | KEEP — rare signal |
| `SecuritiesFirmLoanSell` | 57,001 (1.12%) | KEEP |
| `SecuritiesFinanceSecuredLoanBuy` | 120,821 (2.37%) | KEEP |
| `SecuritiesFinanceSecuredLoanCashRedemption` | 53,434 (1.05%) | KEEP |
| `SecuritiesFinanceSecuredLoanSell` | 42,660 (0.84%) | KEEP |
| `SecuritiesFinanceSecuredLoanReplacement` | 42,363 (0.83%) | KEEP |
| Others (<500 non-zero) | very rare | KEEP (let model decide) |

## Legitimately weird values (NOT bugs, NOT cleaned)

| File | Pattern | Why it's real |
|---|---|---|
| `futures_daily.csv` | 5% negative `open`/`close` (2,333 distinct values, range −1026 to −0.0014) | **Calendar-spread contracts** — `contract_date` like `201001/201003` means long Mar / short Jan, where the price is naturally a difference that can be negative. NOT sentinels. |
| `futures_daily.csv` | `spread`/`spread_per` 15.6% negative | Inter-contract spreads naturally go either way |
| `crude_oil.csv` | `price` min = −37 | April 2020 WTI crash |
| `bonds_yield.csv` | 0.4% negative | Japan/EU sovereign bonds went sub-zero |
| `interest_rate.csv` | 6.1% negative, min = −0.75 | Swiss/Japan negative policy rates |
| `margin.csv` | High zero% on `margin_redeem` (78%), `short_*` (~64%) | Most stocks lack margin/short activity most days |
| `ohlcv.csv` | `$volume` 2.5% zero | Suspended-trading days; zero volume is correct |
| `ohlcv.csv` | `$high < $open` 2.37% (non-zero volume) | FinMind's post-adjustment quirk on ex-dates for low-liquidity stocks; concentrated in specific instruments (`2248`/`8438`/`3597`). qlib processors absorb. |

## Pipeline overview after cleaning

```
data/tw_finmind_full/         (raw, untouched)
        │
        │  clean_csvs.py — universe filter + per-file rules
        │      • benchmark.csv = {TAIEX, TPEx, 0050} extracted BEFORE filter
        ▼
data/tw_finmind_full_clean/
        ├── ohlcv.csv         ← consume from here
        ├── institutional.csv     (dealer_net fixed)
        ├── ... 33 more CSVs
        └── benchmark.csv     (for backtest config)
        │
        │  dump_to_bin.py --input_dir data/tw_finmind_full_clean
        ▼
~/.qlib/qlib_data/tw_data/    (qlib bin — expression engine reads here)
        │
        │  dump_factor_h5.py
        ▼
git_ignore_folder/factor_implementation_source_data/daily_pv.h5
        │
        │  factor R&D loop (factor_tw.py)
        ▼
LLM-generated factor.py + qlib model training/backtest
```

## Why two formats (qlib bin + HDF5)

The model reads from **qlib bin** (`~/.qlib/qlib_data/tw_data/features/<code>/<field>.day.bin`) — this is the only format qlib's expression engine (`Ref()`, `Mean()`, etc.) and `Alpha158DL` can ingest.

The factor coder's LLM-generated `factor.py` reads from **HDF5** (`daily_pv.h5`) — `pd.read_hdf` gives the LLM a single MultiIndex DataFrame to work with, without forcing every factor to call `qlib.init()` first.

Same source data, two consumers, two formats. The HDF5 is derived from the bin (single source of truth) via `dump_factor_h5.py`.

## Raw audit output

Full per-column breakdown: [_audit_report.txt](_audit_report.txt)
Deep audit (challenges): [_deep_audit.log](_deep_audit.log)
OHLCV investigation: [_ohlcv_investigate.log](_ohlcv_investigate.log)
Instrument inventory: [_instrument_inventory.csv](_instrument_inventory.csv)
Latest clean run: [_clean_run.log](_clean_run.log)

## Re-running

```bash
conda activate rdagent
cd /mnt/g/Github/RD-Agent
python -m rdagent.scenarios.qlib.tw_data.clean_csvs
```

Then dump to qlib bin:
```bash
python -m rdagent.scenarios.qlib.tw_data.dump_to_bin \
    --input_dir data/tw_finmind_full_clean \
    --out_dir ~/.qlib/qlib_data/tw_data
```
