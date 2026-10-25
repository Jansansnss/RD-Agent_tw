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
8. Economic review (D25): answer the filled prompt `data/alpha_tw/reviews/<name>_prompt.md`
   (template `tw_data/alpha_review_prompt_zh.md`) with a model other than the loop's, and paste the
   A–F answer under the entry. Explanations formed after seeing the holdout are marked post hoc;
   fixes they suggest are scored on the research window and confirmed only on new data.

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

### 經濟推理審查（A–F；審查者 Claude，2026-10-25；輸入 `reviews/LiquiditySize_Rank_20d_prompt.md`）

**A. 判斷：有條件合理。** 公式確實衡量規模與流動性，研究期與保留期的 IC 都為正。但它幾乎就是市值（相關 0.92–0.94），原始解釋把「這段期間大型股表現好」說成「大型、流動性高的股票更有吸引力」，缺少為何會持續的機制。

**B. 對照**
- 想衡量：大型、流動性高的股票之後表現較好（迴圈 LLM 在第 10 輪發現小型與低流動性的 t 值為負後，把方向翻過來）。
- 實際衡量：市值排名與 20 日 Amihud 流動性排名的平均。在這 150 檔裡兩者相關 0.69–0.77，合起來跟市值相關 0.92–0.94，實質是「相對規模」。
- 缺少的條件：
  - 規模溢酬在比賽期間持續：可以檢驗（只能用新資料），尚未檢驗。
  - 不只是 beta：已有資料部分反對，保留期 beta 1.20。
  - 不是單一期間現象：已有資料反對一般性。研究期單純市值 t 2.46、24 日超額中位數 −0.15%；保留期超額集中在 5–6 月。
  - 流動性那一半有市值以外的資訊：可以檢驗，尚未檢驗。

**C. 最有力的反例**：分數幾乎只由大小決定，與公司前景無關。10/22 的前 24 名包括中華電（市值第 16 名、低成長電信股），只因為夠大、夠好交易；最高分的台積電則是因為市值第 1。若「大」本身就代表較有吸引力，那 2022 年空頭時也應成立，但這張因子當年的 5 日 IC 是負的（−0.004）。這屬於「需要限制適用範圍」，不推翻統計假說。

**D. 最小修正：保留公式，縮限解釋。** 改稱「規模傾斜」，不當選股訊號；要用時，當成「是否偏向權值股」的配置決定，並一起管 beta。代價：如果比賽期間中小型股反彈，這個傾斜會落後；也不能再說它是新 alpha。

**E. 區分性實驗（只用研究期）**
1. 流動性的資訊 vs 只是規模：每天把流動性對 log 市值做橫斷面回歸，只用殘差排名。殘差 t ≥ 2 支持「流動性有市值以外的資訊」；接近 0 則這張就是規模。
2. 持續溢酬 vs 單一行情：研究期分年度、分大盤上漲月與下跌月計算 IC。各狀態都為正，支持持續性；只在大型股領漲的年份為正，代表是期間現象。
3. 選股 vs beta：對等權大盤做回歸，看扣掉 beta 後的超額。仍為正代表不只是 beta；接近 0 代表主要是 beta。

**F. 修正後的解釋**
- 已知：分數是市值與流動性排名的平均，跟市值相關 0.92–0.94；研究期 5 日 t 3.01（單純市值 2.46，沒過）；保留期 t 3.09，beta 約 1.2。
- 假設：比賽期間大型權值股會繼續領先等權重的 150 檔。
- 待驗證：實驗 1–3，以及比賽期間的實盤。
- 事後假說（看過保留期）：5–6 月的超額來自當時的大型股行情。

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

**Margin check (2026-10-25):** sales yield = earnings yield ÷ net margin, so a thin margin looks
like a low price. Over 70 month ends (about 140 profitable names each), −log(net margin) explains
61% of the cross-sectional variance of log sales yield and log earnings yield only 21%; margins
spread about 9× across ±1 standard deviation, earnings yields 4.7×. On 2026-10-22 Wistron's sales
yield was 70× TSMC's while its P/E was only 1.8× lower (15.1 vs 27.6); the rest is margin (about
0.9% vs 36%). Ranked within industries, sales yield drops from research t 2.74 to 2.13 (holdout
2.34), so part of the plain signal is exposure to thin-margin industries.

**Verdict: candidate for a defensive or value sleeve; not yet an export.** Before use, score two
variants on the research window: trailing-12-month revenue, and a cap of about 25% per industry.
They were proposed after the holdout was seen, so the holdout cannot confirm them; only new data
(the contest period) can. Plain sales yield did not pass the research gate on its own, and the
holdout coincided with the AI-server rally, so the evidence covers one regime.

### 經濟推理審查（A–F；審查者 Claude，2026-10-25；輸入 `reviews/SalesYield_LiquiditySize_70_30_prompt.md`）

**A. 判斷：有條件合理。**「營收倍數低」的股票在研究期與保留期的 IC 都為正，作為統計訊號可以保留。但原始解釋「以營收來看較便宜」不能推到「被低估」：營收殖利率的差異約 61% 來自淨利率，排名前段主要是薄利產業。

**B. 對照**
- 想衡量：便宜，加上大型流動（迴圈 LLM：cheaper on sales plus larger/more liquid）。
- 實際衡量：70% 的「12 × 單月營收 ÷ 市值」排名，加 30% 的市值與流動性。前者等於「盈餘殖利率 ÷ 淨利率」，同時混了價格和利潤結構。
- 缺少的條件：
  - 比較對象的利潤結構相近：已有資料部分反對。全體比較時 61% 是淨利率差異；改成同產業內比較，研究期 t 從 2.74 降到 2.13。
  - 單月營收能代表一年：可以檢驗，尚未檢驗（季節性、拉貨月）。
  - 不是產業或題材的代理：已有資料部分反對。保留期持股有 34% 是電腦及週邊設備（AI 伺服器代工）。
  - 淨利率沒有在惡化：目前資料不足，面板沒有損益表，只能用本益比推算。

**C. 最有力的反例**：鴻海與長榮（10/22）。營收殖利率說鴻海比較便宜：4.03 對 1.06，差約 4 倍。但看本益比是長榮比較便宜：10.3 對 16.1，盈餘殖利率 9.7% 對 6.2%。兩者的推算淨利率約 1.5% 對 9.2%，鴻海的高分幾乎全來自薄利。這屬於「需要限制適用範圍」：跨產業比較時，高分不代表便宜；但不推翻「營收倍數低的股票表現較好」這個統計假說。

**D. 最小修正：保留公式，縮限解釋，並限制比較範圍。** 解釋改成「營收倍數較低」，不說「便宜」。若要宣稱同業間便宜，就改用同產業內排名；不加新的因子或權重。單月改成近 12 個月加總，只是修正量測，不是新因子。代價：同產業版的研究期訊號較弱（t 2.13），原版有一部分報酬來自薄利產業的行情。

**E. 區分性實驗（只用研究期；保留期已用過）**
1. 同業間便宜 vs 薄利產業曝險：比較原版、同產業內排名、減去產業平均三個版本，買法與期間固定。後兩者保留大部分訊號，支持「同業間便宜」；大幅減弱則主要是產業曝險（同產業排名已降到 2.13，目前偏向後者）。
2. 營收倍數 vs 季節性雜訊：單月 × 12 對比近 12 個月加總。近 12 月版不比單月版差，代表季節性不是訊號來源；明顯較差，代表單月版有一部分來自季節波動。
3. 便宜 vs 價值陷阱（利潤率下滑）：用推算淨利率的變化，把高營收殖利率的股票分成「改善」與「惡化」兩組，比較之後的報酬。改善組明顯較好，代表訊號其實是「便宜＋利潤率改善」；兩組相同，代表營收倍數本身就有資訊。

**F. 修正後的解釋**
- 已知：分數主要是營收倍數（跟單純營收殖利率相關 0.94）；營收倍數的差異約 61% 來自淨利率；同產業內排名的研究期 t 是 2.13。
- 假設：營收倍數低的股票在 5–20 日內表現較好，機制未知；同業間便宜可能是其中一部分。
- 待驗證：實驗 1–3，以及比賽期間的實盤。
- 事後假說（看過保留期）：保留期的超額可能來自 AI 伺服器營收大增，而不是便宜。
