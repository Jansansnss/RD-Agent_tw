# Alpha reviews

> Every card that passes the holdout gets an entry here before anyone uses or exports it
> (DECISIONS D24; Jans, 2026-10-25: 以後有通過保留期的都要寫出來加以檢視). A holdout pass is a
> candidate, not an alpha, until it is reviewed. Holdout numbers in this file never go into an LLM
> prompt.

## How to review a card

1. Run `python -m rdagent.scenarios.qlib.tw_data.alpha_review_tw --card <name>` (WSL, `rdagent`
   env). It prints the numbers used below and writes `data/alpha_tw/reviews/<name>.json`.
2. Read `data/alpha_tw/library/<name>/factor.py` and write the formula from the code, not from the
   card text.
3. Audit the code: every input known by the evening of t (revenue from its publication day, D17;
   no `shift(-n)`, centered windows or whole-sample statistics); per-instrument shifts and rolling
   windows on a date-sorted index; ranks per date inside the universe; units; NaN and zero handling.
4. What it buys: the top 24 on the last day (names, industries, size ranks), and the holdout book's
   industry mix and size percentile.
5. What it is: correlation with plain exposures (size, liquidity, sales yield, earnings yield,
   book-to-price, momentum, low volatility, revenue growth). Above 0.7 with one of them means the
   card is mostly that exposure; then score the plain exposure on its own as well.
6. Where the holdout money came from: monthly excess, beta, and the contest's official 150 names.
7. Verdict: alpha / deliberate tilt / watch (paper-track) / reject, and the checks still open.

## LiquiditySize_Rank_20d — reviewed 2026-10-25

Accepted in round 11 of the flash campaign (TODO T9); holdout-confirmed (G4).

**Formula (from the code):** score = 0.5 × rank(log market value) + 0.5 × rank(−ILLIQ20), with
ranks taken per date inside the point-in-time universe. ILLIQ20 is the 20-day mean of
|close-to-close return| ÷ traded amount (NT$), `min_periods=20`, amount ≤ 0 treated as missing.
Higher = buy.

**Audit:** inputs are known at the close of t (`$close` back-adjusted, `$amount` raw NT$,
`$market_value`, `$in_universe`). Per-instrument `shift(1)` and `rolling(20)` run on a date-sorted
index; ranks are per date within the universe. Nothing looks ahead.

| Score | 5-day t | 24-day median excess | Annualized excess | Beta |
|---|---|---|---|---|
| Card, research | 3.01 | +0.14% | +0.8% | 0.98 |
| Card, holdout | 3.09 | +1.23% | +42.0% | 1.20 |
| Card, holdout, official 150 | 2.29 | +0.91% | +31.1% | 1.24 |
| Size alone, research | 2.46 | −0.15% | −2.0% | 0.79 |
| Size alone, holdout | 3.53 | +1.45% | +28.1% | 1.10 |

**What it is:** correlation 0.92 (research) and 0.94 (holdout) with log market value, and the same
with its liquidity half; no other exposure above 0.22. The top 24 on 2026-10-22 were the largest
names (2330, 2454, 2308, 2317, 1303, 2303, 3711, …); the holdout book sat at the 90th size
percentile, 30% semiconductors and 26% electronic components. Holdout excess by month: May +12.1%,
June +6.5%, then −1.8%, −1.2%, +0.9%, −1.0%.

**Verdict: a size tilt, not stock selection.** Plain size failed the research gate; the card passed
on its liquidity half, and the holdout paid for the mega-cap run of May–June 2026. Use it only as
an explicit team decision to lean toward the largest names (beta about 1.2). Open: whether the
size premium holds over a 24-day contest; the active-share effect of holding the index leaders.

## SalesYield_LiquiditySize_70_30 — reviewed 2026-10-25

Accepted in round 18 of the flash campaign; holdout-confirmed.

**Formula (from the code):** score = 0.7 × rank(12 × latest monthly revenue ÷ market value) +
0.3 × [0.5 × rank(log market value) + 0.5 × rank(−ILLIQ20)] − 1, ranks per date inside the
universe, values outside the universe masked. Higher = buy.

**Audit:** `$rev` is the latest published monthly revenue (known from its `create_time`, else the
10th of the next month; D17), so there is no look-ahead; the liquidity code is the one above.
Weakness: one month × 12, so seasonal or lumpy revenue reshuffles the ranking every month; a
trailing-12-month version is untested. Revenue and market value are both NT$, and the scale does
not matter for ranks.

| Score | 5-day t | 24-day median excess | Annualized excess | Beta |
|---|---|---|---|---|
| Card, research | 3.15 | +0.40% | +6.1% | 0.85 |
| Card, holdout | 2.63 | +2.50% | +58.6% | 0.72 |
| Card, holdout, official 150 | 2.54 | +1.66% | +49.6% | 0.76 |
| Sales yield alone, research | 2.74 | +0.41% | +5.6% | 0.84 |
| Sales yield alone, holdout | 2.28 | +1.48% | +53.5% | 0.64 |

**What it is:** correlation 0.94 with plain sales yield; in the holdout also 0.63 with earnings
yield, 0.61 with book-to-price and 0.43 with low volatility; momentum about 0. It buys high-revenue,
thin-margin names: AI-server ODMs (2317, 2382, 3231, 6669, 2356, 2376), distributors (3036, 3702,
2347), shipping and steel. Computers and peripherals were 34% of the holdout book, at the 60th size
percentile. Holdout excess by month: +9.9%, −0.9%, +11.1%, −7.1%, +0.7%, +3.8%; it won the July
drop and lost the August rebound, like a value or low-beta book.

**Verdict: candidate for a defensive or value sleeve; not yet an export.** Before use, score two
variants on research and holdout: trailing-12-month revenue, and a cap of about 25% per industry.
Plain sales yield did not pass the research gate on its own, and the holdout coincided with the
AI-server rally, so the evidence covers one regime.
