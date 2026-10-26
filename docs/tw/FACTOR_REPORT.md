# 因子報告規格（tw-factor-report/1）

> 2026-10-26 起。每個要交給隊伍或決策模型（「大腦」）判斷的因子，都交一份這種報告。
> 數字由程式算，文字由作者寫；同一份內容輸出兩個版本：給人看的 `report.html`，給大腦讀的 `report.json`。
> 範例：營收加速（`docs/tw/factor_reports/rev_accel.yaml` → `data/alpha_tw/reports/rev_accel/`）。

## 0. 為什麼要固定格式

- **大腦要比較不同人做的因子。** 只有股票池、成交規則、成本、期間和比較基準都相同，數字才能放在一起比。所以所有報告用同一個判分程式（`alpha_eval_tw`，因子迴圈用的也是它）。
- **LLM 寫數字會編。** 數字一律由程式算；LLM 只寫推理文字，而且要標出引用的欄位。
- **回測賺錢不代表邏輯成立。** 作者要在看結果之前寫下機制和可以被推翻的預測，程式再逐條核對（§2）。

## 1. 一份報告的九節

| § | 內容 | 誰產生 | 為什麼需要 |
|---|---|---|---|
| 1 定義與資料時點 | 公式、輸入欄位、每個輸入何時已知、方向、宣告持有期、五大因子類別、D-Plan 句型、資料來源機構 | 作者（第一階段） | 不能重算就不能用；資料時點錯就是偷看未來 |
| 2 經濟邏輯 | 機制類型、想衡量的概念、機制、誰在另一邊、為何沒被套利、台股特有條件、文獻、事前預測與程式核對 | 作者（第一階段）＋程式 | 說明公式為什麼該有效，並讓它可以被推翻 |
| 3 預測能力 | 各持有期的 RankIC（衰減）、逐年 IC、五分位報酬、前 10 檔 24 日超額、覆蓋率 | 程式 | 分數高的股票之後是不是真的比較好 |
| 4 回測 | 比賽規則下的前 24 名：淨值、回撤、24 日超額分布、每月超額、逐年、成本 | 程式 | 在比賽的成交與成本下能不能賺到錢 |
| 5 同期比較 | B0（150 檔等權）、0050、加權指數、櫃買指數、投資台股的主動式 ETF | 程式 | 是選股能力，還是同期行情 |
| 6 它其實是什麼 | 與常見風格的相關、與因子庫的相關、持股族群與市值 | 程式 | 是不是規模、價值、動能等舊東西的偽裝 |
| 7 穩健性 | 大型／小型、產業中性、大盤漲／跌、門檻結果、嘗試次數 | 程式 | 換個切法還在不在 |
| 8 解讀與建議 | 結論、其他解釋、風險、建議角色、權重上限、停用條件、待驗證、事後假說；程式警訊；獨立審查 | 作者（第二階段）＋程式＋審查者 | 給大腦可以照做、也可以停用的建議 |
| 9 可重現性 | 產生時間、程式版本、面板建立時間、資料截至、指令 | 程式 | 12/2 code review 要能重跑 |

## 2. 怎麼證明公式合乎邏輯與經濟學

回測只能說明「過去這樣算會賺」，不能證明原因。能做的是把原因寫成會被資料推翻的預測，再看資料有沒有推翻它。

**(1) 定義正確**：單位一致、方向正確、每個輸入在 t 日收盤時已知（月營收從次月 10 日起才能用）、沒有 `shift(-n)` 或用全樣本統計量、排名在當天股票池內做、缺值和 0 有處理。檢查清單同 `ALPHA_REVIEWS.md`。

**(2) 說清楚機制**，四選一：

| 類型 | 意思 | 台股的例子 | 通常會看到 |
|---|---|---|---|
| 風險溢酬 `risk_premium` | 承擔某種風險的補償 | 小型、低流動性、價值股 | 空頭時虧、要長期才兌現 |
| 行為偏誤 `behavioral` | 投資人反應不足或過度 | 營收加速、短線過度反應 | 幾天到幾週內逐步修正；注意力低的股票較明顯 |
| 結構與摩擦 `structural` | 被迫或受限的交易 | 投信季底作帳、指數調整、融資斷頭、除權息 | 有固定時點；壓力結束後價格回來 |
| 未知 `unknown` | 只有統計關係 | — | 要更強的統計證據、更小的權重 |

**(3) 誰在另一邊、為什麼沒被套利掉**：沒有對手方的超額報酬很少能持續（Shleifer & Vishny 1997 的套利限制）。

**(4) 事前預測**：從機制推出 5 條固定預測（加上自由填寫的其他預測），由程式在研究期自動核對：

| 預測 | 選項 | 程式怎麼判定 |
|---|---|---|
| `sign` | positive／negative | 宣告持有期的 IC 方向對且 \|t\| ≥ 2 為支持 |
| `peak_horizon_days` | 1／5／10／20 | 平均 IC 在哪個持有期最高（衰減形狀）；相鄰一格算方向對 |
| `stronger_in` | small／large／none | 當天以市值中位數切兩半，比較兩半的 IC，差的 t ≥ 2 為支持 |
| `industry_neutral` | survives／weakens | 分數減掉同族群平均後再算 IC；t ≥ 2 為 survives |
| `regime` | up／down／both | 依同期股票池平均報酬分漲跌，各算 IC |

判定結果只有四種：支持（方向對且 \|t\| ≥ 2）、方向對但不顯著、沒有支持、相反（反向且 \|t\| ≥ 2）。**預測要在看結果之前寫，宣告檔進 git，commit 時間就是證明。** 看過結果才寫的，要標 `registered: after_results`，這張表就只能當描述。

**(5) 排除其他解釋**：程式自動算並列成警訊（flags）：

- 跟某個常見風格的相關超過 0.7（主要就是那個風格）；跟因子庫相關超過 0.6（重複）。
- 組合對 B0 的 beta 在 0.8–1.2 之外（超額有一部分是押大盤方向）。
- 換手過高、成本加倍後超額轉負。
- 嘗試次數：試了 N 個版本，純運氣的最大 |t| 約 √(2 ln N)；研究期門檻用 t ≥ 3，是因為因子迴圈一次試上百個想法（Harvey, Liu & Zhu 2016 也建議新因子的 t 至少 3）。
- 只在單一年份或單一行情有效（逐年表、每月超額、大盤漲跌）。

**(6) 反例與獨立審查**：入選的候選因子再用 `tw_data/alpha_review_prompt_zh.md`（A–F），交給**跟作者不同的模型**審，摘要貼到宣告檔的 `review` 區塊。

研究顯示學術上發表的因子，樣本外報酬平均明顯縮水（McLean & Pontiff 2016），而且很多換個做法就重現不了（Hou, Xue & Zhang 2020）。所以這份報告寧可讓因子看起來比較弱，也不要讓它看起來比實際強。

## 3. 固定的回測與比較規則

所有報告共用，不能各自改：

| 項目 | 規則 | 為什麼 |
|---|---|---|
| 股票池 | 每月底依指數權重（2024-10 前用市值）選上市前 100＋上櫃前 50，point-in-time | 跟比賽的 150 檔同一個選法，又不偷看 2026 年 7 月的名單 |
| 標籤 | VWAP(t+1) → VWAP(t+1+h)，h = 1／5／10／20 | 比賽每筆以下單當天的 VWAP 成交 |
| 組合（book） | 分數最高 24 檔等權，每 5 日調整，仍在前 36 名的續抱 | 接近比賽的 20–30 檔 |
| 成本 | 買 0.1425%，賣 0.1425%＋0.3% 證交稅 | 比賽規則 |
| 期間 | 研究期 2021-01-01 → 2026-04-30；保留期 2026-05-01 起 | DECISIONS D21；保留期在 DeepSeek 訓練資料之後 |
| 主要基準 | B0：當月 150 檔等權，每日再平衡，不扣成本 | 同一批股票、不選股的結果 |
| 門檻 | 研究期 t ≥ 3、至少 3 個完整年度 IC 為正、24 日超額中位數 > 0、與因子庫相關 ≤ 0.6、覆蓋率 ≥ 80%（只看宣告的持有期）；保留期只確認：t ≥ 2、同方向、24 日中位數 > 0 | `alpha_eval_tw.DEFAULT_GATE`、`HOLDOUT_GATE` |

**比較基準**（`data/alpha_tw/benchmarks.csv`，從清理後的 CSV 建立）：

| 基準 | 說明與限制 |
|---|---|
| B0 | 最公平的比較：同一批股票、同樣用 VWAP |
| 0050 | 還原（含息）收盤價，已扣管理費；代表「買大型權值股」 |
| 加權指數、櫃買指數 | 價格指數，**不含股息**（台股每年約少 3%），只為了大家熟悉 |
| 主動式 ETF | 只收投資台股的：上市滿 60 個交易日、跟櫃買指數日報酬相關 ≥ 0.85（2026-10 的資料裡台股型都在 0.86 以上，美國／全球型都在 0.81 以下）。用還原市價，已扣管理費、含折溢價。2025 年 5 月起才陸續上市，每檔從上市起跟因子組合比同一段日子，樣本短，只能參考 |

因子組合以 VWAP 計價，基準以收盤價計價，差約半天，所以 β、相關和追蹤誤差用週報酬算。**不滿一年的期間不年化**，只看累積。

## 4. 兩個版本

### 給人看：`report.html`

- 第一屏就是結論：五大因子類別、研究期與保留期的門檻結果、作者建議、預測是否事前登錄，以及六個關鍵數字。
- 九節依序排列；每張圖都有「表格檢視」；滑鼠移到圖上看每天的數值。
- 紅漲綠跌（台股慣例）；保留期在圖上以分隔線、在每月表以底線標出。
- 作者沒填的欄位寫「作者尚未填寫」，不會留白讓人以為沒問題。
- 單一檔案，離線可開（字型連不到網路時改用系統字型）。

### 給大腦讀：`report.json`

設計原則：

1. **事實和主張分開。** `facts`、`prediction_checks`、`verdict_by_code` 是程式算的；`claims` 是作者說的；`review` 是審查者說的。衝突時以程式為準。
2. **固定欄位、固定單位。** `*_pct` 是百分比數字（1.5 代表 1.5%）；ic 是 Spearman 相關；t 是 Newey-West t 值。不滿一年的年化欄位是 null。
3. **每個數字都在某個期間底下**（`facts.research`、`facts.holdout`）。
4. **不放股票代號與名稱**，只放族群與市值分布，避免模型用記憶判斷個股（ALPHA_FACTORY §7）。
5. **不放圖**，圖要說的事用數字給（逐年表、五分位陣列、24 日超額分布摘要）。
6. **保留期預設不給數字。** `--holdout none`：只有研究期；`status`：人看得到保留期，JSON 只有通過與否；`full`：JSON 也帶保留期數字，只能給不在該期間被測試的模型。審查者看過保留期的觀察寫在 `holdout_notes`，同樣只有 `full` 才會進 JSON。
7. **精簡。** 含作者與審查文字約 10–15 KB，一次比較 10 個因子約 5 萬 tokens。

頂層欄位：

| 欄位 | 內容 |
|---|---|
| `schema` | `tw-factor-report/1`；欄位改了就升版，大腦要檢查 |
| `how_to_read` | 四句閱讀說明，讓沒看過這份規格的模型也讀得懂 |
| `factor` | 宣告的定義（§1） |
| `setup` | 股票池、標籤、成本、資料截至、保留期模式 |
| `verdict_by_code` | 研究期門檻、保留期狀態、嘗試次數與運氣 t、警訊 `flags` |
| `facts.research` | §3–§7 的所有數字 |
| `facts.holdout` | 同上（`full`），或只有一句「刻意不給」 |
| `prediction_checks` | 每條事前預測的程式判定 |
| `claims` | `thesis`、`predictions`、`interpretation`（作者） |
| `review` | 獨立審查摘要 |
| `provenance` | 產生時間、程式版本、指令 |

**建議放進大腦系統提示的閱讀規則**：

- 判斷以 `verdict_by_code` 與 `facts` 為準；`claims` 只是主張，敘事寫得好不加分。
- 證據強弱：比賽期間的實盤 ＞ 保留期 ＞ 研究期 ＞ 作者主張。
- 每條 `flags` 都要考慮；`prediction_checks` 支持少、或預測是事後登錄的，機制的可信度就低。
- 比較因子時用同一個欄位，例如 `facts.research.windows_24d.median_pct`。
- 沒通過保留期確認的因子，最多當觀察或小權重。

## 5. 流程

1. **第一階段（作者，看結果之前）**：把想法貼進 `tw_data/factor_declare_prompt_zh.md`，交給任何 LLM，得到宣告 YAML。自己檢查後存成 `docs/tw/factor_reports/<id>.yaml` 並 commit。
2. **算數字（有 RD-Agent 環境的人，WSL `rdagent` 環境）**：

   ```bash
   python -m rdagent.scenarios.qlib.tw_data.factor_report_tw --baseline rev_accel --declaration docs/tw/factor_reports/rev_accel.yaml
   python -m rdagent.scenarios.qlib.tw_data.factor_report_tw --card <因子庫卡片>        # data/alpha_tw/library/<card>/factor.py
   python -m rdagent.scenarios.qlib.tw_data.factor_report_tw --idea <想法代號>          # alpha_team_ideas_tw.IDEAS
   python -m rdagent.scenarios.qlib.tw_data.factor_report_tw --factor_py <factor.py>    # 迴圈格式：讀 daily_pv.h5、寫 result.h5
   python -m rdagent.scenarios.qlib.tw_data.factor_report_tw --alpha_file <values.csv>  # 自己算好的分數
   ```

   - 沒有環境的隊友，交一個 CSV 就好：欄位 `datetime,instrument,value`，股票代號當文字、值越高越想買、每個值在當天收盤時已知（或交公式給 Jans 代算）。
   - 選項：`--horizon` 改宣告持有期；`--holdout none|status|full`；`--as_of YYYY-MM-DD` 把資料截在某天，做那天看得到的報告（回測大腦時要用）。
   - 輸出在 `data/alpha_tw/reports/<id>/`（不進 git，隨時可以重算）：`report.json`、`report.html`、`prompt_stage2.md`；沒給宣告時另有預先填好的 `declaration.yaml`。一次約 1 分鐘、0.6 GB 記憶體。
3. **第二階段（作者或任何 LLM）**：把 `prompt_stage2.md` 整份貼給 LLM（輸入已自動填好），把回覆的 `interpretation` 區塊貼回宣告檔，再跑一次第 2 步。
4. **獨立審查（要給大腦採用的候選才需要）**：`tw_data/alpha_review_prompt_zh.md`，用跟作者不同的模型，把 A–F 的摘要填進宣告檔的 `review`：

   ```yaml
   review:
     reviewer: deepseek-v4-pro   # 跟作者不同
     date: 2026-10-27
     judgment: 有條件合理          # 合理／有條件合理／解釋不成立／資訊不足
     summary: ...                 # 不寫股票名稱
     counterexample: ...
     fix: ...
     holdout_notes: []            # 看過保留期的觀察；只給人看（--holdout full 才進 JSON）
   ```

5. **交付**：`report.json` 給大腦；`report.html` 給人。

**因子迴圈產生的卡片**（範例：`LiquiditySize_Rank_20d`、`SalesYield_LiquiditySize_70_30`）：作者是迴圈的 LLM，它的假說在 `card.json`。第一階段照實轉述，作者沒寫的欄位寫「作者未說明」；迴圈沒有登錄五條預測，所以由審查者依作者的主張推得，標 `after_results`；`trials` 填迴圈累計評分到這張為止的因子數。審查用跟迴圈不同的模型（D25），完整 A–F 記在 `ALPHA_REVIEWS.md`。

## 6. 給隊友的提示

| 提示 | 用在 | 怎麼用 |
|---|---|---|
| `tw_data/factor_declare_prompt_zh.md` | 第一階段：想法 → 宣告 | 整份複製，在【隊友的想法】貼上自己的想法、規則或程式 |
| `tw_data/factor_report_prompt_zh.md` | 第二階段：數字 → 解讀 | 不用手填：產生器寫出的 `prompt_stage2.md` 已帶入宣告和 report.json |
| `tw_data/alpha_review_prompt_zh.md` | 獨立審查（A–F） | 交給跟作者不同的模型 |

三份提示的共同規則：不准自己生數字、研究期與保留期分開講、看過結果才想到的解釋標成事後假說、不知道就寫 null。

## 7. 參考文獻

- Harvey, Liu & Zhu (2016), "…and the Cross-Section of Expected Returns", *Review of Financial Studies*：新因子的 t 至少 3。
- McLean & Pontiff (2016), "Does Academic Research Destroy Stock Return Predictability?", *Journal of Finance*：發表後報酬縮水。
- Hou, Xue & Zhang (2020), "Replicating Anomalies", *Review of Financial Studies*：多數異常換個做法就消失。
- Bailey & López de Prado (2014), "The Deflated Sharpe Ratio", *Journal of Portfolio Management*：多次嘗試要折扣。
- Grinold (1989), "The Fundamental Law of Active Management", *Journal of Portfolio Management*：IR ≈ IC × √（獨立下注次數）。
- Shleifer & Vishny (1997), "The Limits of Arbitrage", *Journal of Finance*：沒被套利掉的原因。
