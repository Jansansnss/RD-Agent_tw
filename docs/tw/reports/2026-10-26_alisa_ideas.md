# Alisa 策略建議：因子測試與第三輪因子挖掘

2026-10-26（迴圈已結束：22:12 餘額降到保留線，自動停止。這是最終版）

## 結論

1. **Alisa 對原策略的批評大多成立**：原策略的核心是 5–40 日動能，在這 150 檔裡 1–5 日的漲跌會反轉，這個核心在研究期（2021 → 2026-04）沒有優勢。加上她建議刪除的四個硬性門檻，結果更差。
2. **她提的改良也救不回來**：上漲天數、漲幅集中度、族群輪動、反彈確認等 14 個寫法，沒有一個通過研究期門檻。族群由弱轉強只在最近半年（保留期）很好，研究期五年完全不支持，而且每次調整要換掉約九成持股。
3. **反彈要等確認，結果相反**：跌深股「沒確認」就買，之後 5 日平均贏 150 檔平均 0.43%（未扣成本）；「確認後」才買反而是 −0.04%。等到確認時，反彈已經發生了。
4. **LightGBM 組合器不建議用**：在研究期的滾動測試，它輸給 5 個訊號的等權平均，也輸給現用的 50/20/30（§3）。
5. **第三輪因子挖掘已結束**：
   - 規模：第 30–103 輪，共 74 輪、128 個因子，花 9.53 美元（04:17–22:12）。
   - 結果：15 張通過研究期，0 張通過保留期；保留期確認的仍然是原本 2 張。
   - 混合型：15 張裡有 9 張是「借用資料庫已有的強訊號」：2 張把已有訊號混在一起，7 張是「某個弱訊號 × 股東權益報酬率（ROE）水準」。它們研究期好看，保留期全部沒過。
   - 最接近確認的：第 67 輪「營收成長加速度」，保留期 t 1.6、24 日超額中位數 +4.38%。比賽期間值得紙上追蹤。
   - 進度與逐輪結果見 §4 和附錄。

## 1. Alisa 的建議怎麼用

Alisa 的文件（`G:\AI_cup_2026\Alisa策略建議.docx`）檢討一個以短期動能為主的八因子策略，提出很多修改。依能不能寫成「每天每檔一個分數」的因子，分成兩類：

- **能寫成因子的**：直接用判分程式測，也放進迴圈的想法清單（`data/alpha_ideas.md`，編號 B1–B14），讓 LLM 繼續變化。
- **交易規則**（門檻、換股、停損停利、持股上限）：迴圈測不了，要在每日系統的回測裡測。

| Alisa 的建議 | 編號 | 怎麼處理 |
|---|---|---|
| 五日動能加「上漲天數」 | B1 | 因子：直接測、放進迴圈 |
| 五日動能看「最大單日漲幅占比」 | B2 | 因子：直接測、放進迴圈 |
| 暴漲後是否守住漲幅、量能是否延續 | B3 | 因子：放進迴圈 |
| 族群相對大盤強弱 | B4 | 因子：直接測、放進迴圈 |
| 個股相對所屬族群強弱（扣掉自己） | B5 | 因子：直接測、放進迴圈 |
| 族群由弱轉強（最近 5 日比前 5 日改善） | B6 | 因子：直接測、放進迴圈 |
| 族群參與度（上漲家數比例）與其變化 | B7 | 因子：直接測、放進迴圈 |
| 族群成交金額占比的變化 | B8 | 因子：直接測、放進迴圈 |
| 強動能＋高波動不扣分 | B9 | 因子：直接測、放進迴圈 |
| 外資、投信分開看，不要加總 | B10 | 因子：直接測、放進迴圈 |
| 趨勢階段：加速、減速、距近期高點 | B11 | 因子：直接測、放進迴圈 |
| 跌深反彈要等確認，不能只看跌很多 | B12 | 事件研究＋因子：直接測、放進迴圈 |
| 20 日均線當狀態指標，不當門檻 | B13 | 因子：直接測、放進迴圈 |
| 用市場廣度切換動能或反彈 | B14 | 迴圈可以試簡單版；完整的「市場狀態辨識器＋策略選擇器」屬於每日系統 |
| 刪除四個硬性門檻（五日、二十日上漲、站上 20 日均線、成交金額） | — | 用原策略的近似版比較「有門檻」和「沒門檻」 |
| 買賣用不同排名門檻（前 20 買、前 35 內不賣） | — | 判分程式本來就這樣做：買前 24 名，還在前 36 名就不賣，跟她提的 20/35 接近 |
| 換股要明顯更好才換、權重的不交易區間 | — | 交易規則，要在每日系統回測 |
| 移動停利、趨勢失效出場 | — | 交易規則，要在每日系統回測（而且要用隔天成交均價，不能假設碰到停損價就成交） |
| 持股 20–30 檔、台積電上限 25%、單一產業上限 60% | — | 規則限制，不影響因子研究 |

**產業分類**：原本用證交所產業別。10/26 改了兩次，最後以隊上的完整分類（`full_classification_relations_T20261021_preview.pdf`）為準，過去和產業分類有關的測試都重跑了，見 §2.4。它的產品曝險族群（光通訊、記憶體、PCB、高速電互連、半導體設備、測試介面）接近 Alisa 說的主題；其他股票用 FinMind 產業鏈族群。

## 2. 直接測試 Alisa 的想法（不用 LLM）

每個想法先寫成一個固定公式，再交給判分程式評分（`rdagent/scenarios/qlib/tw_data/alpha_team_ideas_tw.py`，結果在 `data/alpha_tw/team_ideas.json`）。評分方式和迴圈一樣：

- **IC**：每天把因子排名和之後的報酬排名做相關，取平均；t 是它的顯著程度，研究期門檻是 t ≥ 3。
- **24 日中位數**：等權持有前 24 名、每 5 日調整一次、扣成本，任一段 24 個交易日贏過 150 檔等權的幅度，取中位數。
- **換手**：每次調整換掉的持股比例。

### 2.1 結果

研究期 = 2021-01 → 2026-04；保留期 = 2026-05 → 10-22。

| 編號 | 測的公式 | 研究期 5 日 IC（t） | 研究期 24 日中位數 | 換手 | 保留期 5 日 IC（t） | 保留期 24 日中位數 |
|---|---|---|---|---|---|---|
| 對照 | 五日漲幅 | −0.009（−1.2） | −2.16% | 71% | −0.029（−0.9） | −3.67% |
| 對照 | 二十日漲幅 | +0.004（0.4） | −0.11% | 29% | −0.057（−1.7） | −3.42% |
| B1 | 五日漲幅＋上漲天數 | −0.006（−0.8） | −2.10% | 74% | −0.013（−0.4） | −4.76% |
| B2 | 五日漲幅＋漲幅不集中 | −0.002（−0.3） | −1.88% | 75% | −0.015（−0.5） | −1.71% |
| B1 | 二十日漲幅＋上漲天數 | +0.008（0.9） | −0.12% | 32% | −0.044（−1.4） | −1.85% |
| B4 | 族群二十日漲幅減大盤 | +0.002（0.2） | −0.08% | 30% | −0.055（−1.7） | −1.60% |
| B5 | 個股二十日漲幅減同族群其他股 | +0.004（0.6） | −0.13% | 29% | −0.026（−1.1） | −0.10% |
| B6 | 族群由弱轉強 | −0.001（−0.1） | −2.81% | 90% | +0.040（1.2） | +1.33% |
| B7 | 族群上漲家數比例的變化 | −0.004（−0.6） | −2.62% | 92% | +0.053（2.0） | +3.77% |
| B8 | 族群成交金額占比的變化 | −0.006（−0.9） | −1.60% | 76% | +0.009（0.5） | −2.76% |
| B6＋B7 | Alisa 的首選組合 | −0.003（−0.4） | −3.04% | 91% | +0.047（1.4） | +2.47% |
| B9 | 二十日漲幅＋高波動 | −0.006（−0.6） | −0.25% | 24% | −0.093（−2.1） | +1.29% |
| B10 | 外資和投信同時買 | +0.000（0.0） | −1.08% | 57% | +0.030（1.0） | +0.46% |
| B11 | 漲勢加速 | −0.017（−2.4） | −3.23% | 92% | −0.003（−0.1） | −2.26% |
| B12 | 跌深後反彈確認 | −0.004（−0.4） | −1.80% | 34% | +0.056（1.7） | −1.96% |
| B13 | 站上 20 日均線的幅度＋均線斜率 | +0.004（0.4） | −0.78% | 30% | −0.049（−1.3） | −2.61% |
| 原策略 | 八因子分數（不加門檻） | +0.000（0.0） | −1.04% | 43% | −0.051（−1.4） | −0.19% |
| 原策略 | 八因子分數＋四個硬性門檻 | −0.014（−1.2） | −1.60% | 55% | −0.086（−2.1） | −0.46% |

- **原策略是近似版**：Alisa 的文件只給權重，沒給公式。成交量異常、法人買賣超、族群強度三項的寫法是照文字推的。
- **B5 在 20 日比較好**：20 日 IC +0.023（t 2.0），但還不到門檻；而且資料庫已經有四張「產業中性相對強弱」卡片，跟它是近親。

### 2.2 反彈要不要等確認（B12 事件研究）

跌深 = 二十日漲幅在 150 檔的後 30%，而且收盤比 20 日最高點低 15% 以上。確認 = 收盤突破前三日的最高收盤。表中是之後的報酬減 150 檔平均（未扣成本），括號是次數。

| | 研究期 5 日 | 研究期 10 日 | 保留期 5 日 | 保留期 10 日 |
|---|---|---|---|---|
| 跌深，沒確認 | +0.43%（9,916） | +0.62% | +0.30%（1,844） | +0.17% |
| 跌深，確認後 | −0.04%（1,448） | +0.47% | −0.91%（335） | −1.27% |

等確認反而比較差，兩個期間都一樣。跌深股本身短期會反彈，等到突破前三日高點時，這段反彈已經走完一部分。

### 2.3 怎麼解讀

1. **短期漲跌會反轉**：五日漲幅、漲勢加速（B11）的 1 日 IC 都顯著為負（t −3.9、−4.6）。上漲天數、漲幅不集中讓負值變小，但沒有翻正。
2. **族群輪動只在最近半年有效**：B6、B7 在保留期很好（B7 +3.77%），研究期五年卻接近零，再扣掉九成的換手成本就是 −2.6% 到 −3.0%。這比較像這半年的行情。而且這份建議是看過這半年才寫的，保留期的好成績不能當作驗證。
3. **硬性門檻有害**：原策略加上四個門檻後，研究期 1 日 IC 變成 t −3.4。這支持 Alisa「刪除硬性門檻」的建議。但刪掉門檻也只是從「明顯輸」變成「沒有優勢」。
4. **對團隊的意思**：在這 150 檔，20 日以內的價格動能不適合當選股核心。至今通過研究期的，都是 20–250 日的慢訊號和營收資訊。

### 2.4 產業分類改版後重跑（10/26）

產業分類 10/26 改了兩次：

1. 先改成族群檢查表（`relations_groups_review_T2026-10-21_v001.pdf`），也就是 FinMind 產業鏈（D30）。
2. 再改成完整分類（`full_classification_relations_T20261021_preview.pdf`，D31），以這版為準。

**完整分類有兩部分**：

- **產品曝險族群（V2）**：6 個母類、38 個子類，171 檔：光通訊、記憶體、PCB、高速電互連、半導體設備、測試介面。代表「有這類產品」，不代表主業或營收占比；目前是預覽版。
- **FinMind 產業鏈族群**：跟第一版相同。重抓的資料在 150 檔上跟檢查表完全一致（559 筆）。

**怎麼變成一檔一個族群**：

- 有產品曝險族群的股票（面板 305 檔中有 62 檔）用它的母類。同時屬於兩個母類的 3 檔，取子類較多的；平手時取較小、較專門的母類，例如旺矽歸測試介面。
- 其他股票用產業鏈的主要產業：在「和證交所產業別相符」的產業裡，取子產業筆數最多的。例如中華電歸通信網路。
- 中租-KY 手動歸金融；7 檔已下市、沒有資料的，用證交所產業別對應。
- 結果共 42 個族群。最新的 150 檔裡，47 檔有產品曝險族群（PCB 14、記憶體 10、半導體設備 8、光通訊 6、高速電互連 5、測試介面 4），產業鏈的半導體剩 29 檔。

另外也照檢查表的算法跑「多重歸屬」：一檔算進它所有的族群，族群數字由所有成員一起算，個股取它各族群的平均。

| 編號 | 測的公式 | 證交所產業別 | 產業鏈族群（D30） | 完整分類（D31） | 完整分類，多重歸屬 |
|---|---|---|---|---|---|
| B4 | 族群二十日漲幅減大盤 | +0.2／−0.08% | −0.4／+0.17% | −0.6／+0.63% | +0.5／+0.03% |
| B5 | 個股二十日漲幅減同族群其他股 | +0.6／−0.13% | +0.7／−0.20% | +0.4／−0.35% | +0.4／+0.01% |
| B6 | 族群由弱轉強 | −0.1／−2.81% | −0.3／−2.76% | −0.0／−2.56% | −0.5／−2.80% |
| B7 | 族群上漲家數比例的變化 | −0.6／−2.62% | −0.7／−2.81% | −0.3／−2.68% | −0.8／−2.87% |
| B8 | 族群成交金額占比的變化 | −0.9／−1.60% | −1.3／−1.66% | −0.8／−1.84% | −1.0／−1.90% |
| B6＋B7 | Alisa 的首選組合 | −0.4／−3.04% | −0.5／−2.68% | +0.1／−2.38% | −0.6／−2.93% |
| 原策略 | 八因子分數（不加門檻） | 0.0／−1.04% | −0.0／−0.91% | −0.0／−1.03% | +0.1／−0.77% |
| 原策略 | 八因子分數＋四個硬性門檻 | −1.2／−1.60% | −1.4／−1.51% | −1.3／−1.57% | −1.3／−1.42% |

格式是「研究期 5 日 IC 的 t 值／研究期 24 日超額中位數」。B5 的 20 日 t 值四欄分別是 2.0、1.7、1.7、1.7。

- **結論不變**：三種分類下，族群輪動在研究期都沒有效果。
- **保留期仍然只在最近半年好**：例如多重歸屬版的 B8 在保留期 24 日超額中位數 +6.34%、B7 +3.35%，但兩者研究期都是負的。保留期只用來確認研究期通過的因子，這不算確認。

**迴圈因子也重跑了**：迴圈到第 67 輪評分過的因子裡，有 73 個用到產業分類。用完整分類重新評分研究期（先不看和資料庫的相關），t 值平均變動約 0.5。結果：

- **多 2 個通過**：
  - 第 4 輪「營收加速的同族群排名」（t 3.12）：和現在交易的營收加速相關 0.79，會被擋下。保留期 t 0.82。
  - 第 6 輪「盈餘殖利率相對族群」（t 3.85）：和已收的「盈餘修正 × 便宜」卡片相關 0.71，會被擋下。保留期 t 0.81。
- **少 1 個**：第 38 輪的產業中性資金流向，t 還在 3.56，但 24 日中位數變成 −0.04%，所以不過。
- **保留期**：確認的仍然是原本 2 張。它們的公式沒有用到產業分類，數字不變。

**檢視紀錄裡的產業數字也更新了**（`ALPHA_REVIEWS.md`）：

- 營收殖利率改成同族群內排名後，研究期 t 是 2.92，比全體比較的 2.74 還高；保留期降到 1.68（全體 2.28）。也就是說，研究期的訊號不是靠押薄利產業，但保留期的超額有一部分來自族群之間的差異。
- 營收殖利率＋大型流動的保留期持股：電腦及週邊設備 25%、產品記憶體 14%、半導體 12%、雲端運算 9%。

**注意**：兩份清單都是 2026-10 的快照，產品曝險族群還是預覽版。拿來算 2021–2026，等於假設公司業務沒變。而且挑哪些主題當產品族群（光通訊、記憶體、PCB……）本身就反映了 2025–26 年 AI 供應鏈的行情：這些股票 2021 年只佔 150 檔的 14%，2026 年佔 29%。

## 3. LightGBM 組合器（不用 LLM）

照模型報告 §3 的設計做了一次，結果寫在 `2026-10-25_model_research.md` §4。重點：

- 研究期內滾動測試（2024-01 → 2026-04）：LightGBM 的 5 日 IC t 1.2、24 日中位數 −0.31%；等權平均 t 5.1、+1.19%；現用 50/20/30 t 4.1、+2.44%。
- 學出來的權重（LightGBM 和線性組合）都輸給固定權重。模型把最多比重給 2021–2023 年最好用的營收殖利率，它在 2024 年後就沒效了。
- 建議：不用 LightGBM。比賽期間紙上追蹤「等權平均」和「50/20/30」。

## 4. 迴圈進度（第 30 輪起）

- 從 flash 長跑的第 29 輪接著跑，log 在 `log/alpha_tw_alisa_20261026`，模型 deepseek-flash。
- 10/26 04:17 開始，DeepSeek 餘額 9.98 美元。
- 跑到餘額剩 0.50 美元自動停：比賽的每日系統用同一個 DeepSeek 帳號寫 D-Plan 文字，要留一點給它。
- 迴圈的想法清單放了 B1–B14 和上面的研究期結果（保留期的數字不放，LLM 看不到）。

| 時間 | 跑完的輪 | 判分因子數 | 通過研究期 | 通過保留期 | 餘額（美元） |
|---|---|---|---|---|---|
| 10/26 04:17 | 開始（第 30 輪） | — | — | — | 9.98 |
| 10/26 05:45 | 第 30–34 輪（5 輪） | 10 | 1 | 0 | 約 9.45 |
| 10/26 06:25 | 第 30–40 輪（11 輪） | 20 | 3 | 0 | 9.19 |
| 10/26 08:50 | 第 30–49 輪（20 輪） | 35 | 3 | 0 | 8.32（08:33） |
| 10/26 10:25 | 第 30–54 輪（25 輪） | 44 | 3 | 0 | 7.10（10:23） |
| 10/26 12:40 | 第 30–64 輪（35 輪） | 65 | 4 | 0 | 5.98（12:39） |
| 10/26 13:50 | 第 30–68 輪（39 輪） | 72 | 5 | 0 | 5.63（13:34） |
| 10/26 14:40 | 第 30–73 輪（44 輪） | 81 | 7 | 0 | 4.91（14:39） |
| 10/26 22:12（結束） | 第 30–103 輪（74 輪） | 128 | 15 | 0 | 0.45 |

**第 30 輪白費一輪**：兩個因子都用 252 日的窗格，但 LLM 寫程式時用的除錯資料只有一年，算出來全是空值，連評分都沒進去。已把除錯資料延長成兩年（DECISIONS D26），之後的長窗格因子都能正常評分。

### 4.1 通過研究期的因子

**IndustrySizeNeutralMom125Skip20_ForeignOwnershipResid_50_50（第 34 輪）**

- **公式**：0.5 × 排名（125 日動能，跳過最近 20 日，減掉同產業、同規模三分位的其他股平均）＋ 0.5 × 排名（20 日平均外資持股比例，同樣減掉同產業、同規模的平均）− 0.5。
- **研究期**：5 日 IC +0.029（t 4.49），六個年度都是正的；換手 8%，beta 1.17；24 日中位數 +0.44%，勝率 59%。
- **保留期：沒通過**。5 日 IC −0.006（t −0.3），24 日中位數 −1.19%。
- **怎麼來的**：第 31–34 輪一直在改「產業中性 125 日動能」：單獨用最高到 t 2.93，差一點過門檻。第 34 輪把它和已收的外資持股卡片（研究期 t 3.11）各半混合，兩者相關低，合起來 t 4.49。所以它通過主要是靠「混合」，新的資訊只有動能那一半。它跟資料庫的最高相關 0.569，剛好低於 0.6 的上限。
- **門檻漏洞**：把已收的卡片和一個差一點的新訊號各半混合，就能用低於 0.6 的相關通過。之後考慮加一條規則：新因子要在扣掉資料庫已有因子之後，仍然有顯著的預測力。

**SlowComposite5（第 37 輪）**

- **公式**：五個排名等權平均：營收加速、殖利率、20 日平均外資持股、20 日高低點區間位置、產業＋規模中性的 125 日動能。
- **研究期**：5 日 IC +0.050（t 6.6），六個年度都是正的；24 日中位數 +1.20%，勝率 70%；beta 0.96。
- **保留期：沒通過**。5 日 IC +0.029（t 1.1，門檻 2），24 日中位數 +1.06%。
- **問題**：五個部分全是資料庫已有的訊號。混合越多個，跟任何一張卡片的相關就越低（這張最高 0.565），所以能通過門檻，但沒有新資訊。這種組合本來就該在迴圈外用固定權重做（DECISIONS D28）。發現後在想法清單加了一條「不要混合資料庫已有的訊號」，下一輪（第 38 輪）的假設就主動拒絕了再混一次的建議。

**IndustryNeutralMoneyFlow_60d_Skip5（第 38 輪）**

- **公式**：過去第 5 到第 64 個交易日，每天收盤在當天高低區間的位置（收在最高 = 1、最低 = 0），用成交金額加權平均，再減掉同產業的平均，取排名。意思是「這段期間的成交，多半收在當天的高檔還是低檔」。
- **研究期**：5 日 IC +0.019（t 3.44），六個年度都是正的；換手 11%，beta 0.82；24 日中位數 +0.44%，勝率 56%。跟資料庫最高相關 0.49（20 日高低點位置卡片）。
- **保留期：沒通過**。5 日 IC +0.015（t 0.9），24 日中位數 −0.33%。
- 第 39、40 輪試了它的變化型（趨勢、帶正負號的量能壓力、盤中與隔夜報酬），都沒過。

**UpDayConsistency_40d_Skip5（第 61 輪，來自 Alisa 的 B1）**

- **公式**：過去第 5 到第 44 個交易日（40 天，跳過最近 5 天），收盤上漲的天數比例，取排名。也就是「這段期間是不是穩穩地漲」。
- **來源**：Alisa 的「上漲天數」想法（B1）。她原本用五日，研究期是負的；想法清單請 LLM 避開 1–5 日的反轉，它就改成 40 日並跳過最近 5 天。學術上叫「溫水煮青蛙」效應：連續小漲比一次大漲更容易延續（Da, Gurun & Warachka 2014）。
- **研究期**：5 日 IC +0.024（t 3.02，剛好過門檻），6 個年度有 5 個是正的（2022 年負）；換手 20%，beta 1.17；24 日中位數 +0.51%，勝率 56%。跟資料庫最高相關 0.55。
- **保留期：沒通過，而且反轉**。5 日 IC −0.054（t −2.0），24 日中位數 −0.90%。跟所有動能類一樣，最近半年反過來。
- 依 D29，就算它通過保留期也只能當參考（Alisa 的想法是看過這半年才寫的）；它沒通過，所以這點不影響結論。

**IndustryNeutral_Revenue3mGrowthRevision_21dLag_VolScaled（第 67 輪）**

- **公式**：近 3 個月營收年增率，跟 21 個交易日前比的變化（營收成長在加快還是放慢）；除以這檔股票自己過去 12 次同樣變化的標準差（夾在 ±3 之間）；再減掉同族群其他股的平均，取排名。
- **研究期**：5 日 t 4.90、20 日 t 3.92，24 日中位數 +0.50%，勝率 61%；換手 18%，beta 1.06；跟資料庫最高相關 0.38。同一輪的另一個版本相關 0.98，被當成重複擋下。
- **保留期：沒通過**。5 日 IC t 1.6（門檻 2），但 24 日中位數 +4.38%。這是目前最接近確認的一張，比賽期間值得紙上追蹤。

**第 69、70 輪：盈餘與營收的修正（2 張）**

- 第 69 輪「產業中性盈餘修正（126 日，除以自身波動）」：研究期通過；保留期 5 日 t −1.0，24 日中位數 +0.19%，沒通過。
- 第 70 輪「近 12 個月營收年增率的修正」：和第 67 輪同一類；研究期通過；保留期 t 1.0，+0.47%，沒通過。
- 第 53–58、65–71 輪都在試盈餘或營收的修正，已在想法清單把這一類列為「已試過」。

**第 74–103 輪：8 張，全部沒通過保留期**

| 輪 | 因子 | 保留期 5 日 t，24 日中位數 |
|---|---|---|
| 74 | 營收成長修正（63 日，扣掉第 67 輪那張的部分） | t 1.5，+1.64% |
| 86 | ROE 變化 × ROE 水準 | t −1.1，−1.52% |
| 87 | ROE 穩定度 × ROE 水準 | t −0.6，−2.74% |
| 89 | 庫藏股 × ROE 水準 × 盈餘殖利率 | t 0.5，+0.21% |
| 93 | 庫藏股意外 × ROE 水準 | t −0.3，−0.72% |
| 95 | 融資減少意外 × ROE 水準 | t −0.5，−2.23% |
| 97 | 外資買超意外 × ROE 水準 | t −0.6，−0.89% |
| 103 | 投信賣超意外 × ROE 水準 | t −1.2，−0.82% |

- **ROE 水準是主角**：第 86 輪起，LLM 發現「任何訊號 × ROE 水準」都容易過研究期門檻。同樣的訊號不乘 ROE 時，研究期都沒過，例如外資買超意外 t 1.43。因為另一半每次不同，跟資料庫的相關都低於 0.6，所以一張接一張通過。這跟第 34、37 輪的混合型是同一個漏洞。
- **保留期一致地輸**：7 張 ROE 型有 6 張的 24 日中位數是負的。ROE 這類「品質」因子最近半年跟動能一樣不靈。

### 4.2 被相關上限擋下的營收驚喜

第 44、47 輪有 4 個因子，研究期的 t 值都過了（5 日 t 4.08、20 日 t 3.1），卻因為和現在交易的「單月營收年增」相關 0.64 被擋下。它們是同一個想法：單月營收年增和自己過去 24 個月比的 z 分數（產業中性）。第 47 輪的三個「依新鮮度加權」版本，分數和第 44 輪完全一樣，等於同一個因子測了四次。

| | 5 日 IC（t） | 20 日 t | 24 日中位數 | 勝率 |
|---|---|---|---|---|
| 現用的單月營收年增 | +0.026（3.1） | 1.8 | +0.84% | — |
| 營收驚喜（z 分數） | +0.023（4.08） | 3.12 | +0.33% | 55% |

它的 t 值比較高，但 24 日中位數比較低，不算明顯更好，所以就算規則允許「更好的變化型取代現用訊號」，它也不夠格。已在想法清單請 LLM 不要再試這一類。

### 4.3 其他

- **第 46 輪白費**：因子能跑，但寫程式的檢查員一直認為程式和規格不符，6 次修正後放棄。這是 flash 長跑時就有的現象。
- **收盤位置類**（第 41–43、48–49 輪）：從第 38 輪通過的資金流向延伸，試了 VWAP 支撐、下跌日收盤位置等 9 個寫法。有兩個 5 日 t 超過 3（4.03、3.78），但前 24 檔的 24 日中位數是負的，其他都不到門檻。

### 4.4 Alisa 的想法用了多少

到第 64 輪，有三輪直接用了 B 編號：第 35 輪 B3（暴漲後守住多少漲幅：5 日 t 0.85，沒過）、第 39 輪 B10（引用投信買超的研究期結果，決定不再測投信）、第 61 輪 B1（40 日上漲天數比例，通過研究期，見 §4.1）。第 62–63 輪接著試了上漲與下跌幅度的不對稱，都沒過。其他輪多半在修上一輪差一點的因子，這是想法清單的規則允許的。產業中性動能、產業中性資金流向都和 B5（個股相對族群）同一類。

### 4.5 這次學到的

- **想法清單管不太住 LLM**：
  - 每輪的下一個假設主要跟著上一輪回饋的建議走，而回饋那一步看不到想法清單。
  - 所以「同一類最多修三輪」「不要再做營收」這類規則，常常要隔好幾輪才生效，或被繞過（改名、取殘差、乘上 ROE）。
- **門檻擋不住混合型**：
  - 現在只檢查新因子跟每一張已收卡片的相關要 ≤ 0.6。
  - 「已有的強訊號 × 新的弱訊號」能過這一關，卻沒有新資訊。
  - 建議加一條：新因子扣掉資料庫已有因子之後，仍要有顯著的預測力。這要 Jans 決定。
- **花費**：
  - DeepSeek 實扣 9.53 美元，LiteLLM 自己算的是 4.79 美元，又少算一半。
  - 1,155 次呼叫，平均每輪約 14.5 分鐘、0.13 美元。
- **停止機制**：餘額到 0.50 美元時自動停，實際停在 0.45 美元，比賽每日系統的 D-Plan 文字仍可用。

## 5. 注意事項

- **保留期對這些想法不乾淨**：Alisa 是看過這半年才寫的，所以從她的想法來的因子，就算通過保留期，也只能當參考；真正的驗收是比賽期間。
- **測了很多次**：直接測試 18 個公式，加上迴圈裡的每一輪，偶然通過的機會會累積。這就是研究期門檻用 t ≥ 3 的原因。
- **產業分類**：10/26 起用完整分類（§2.4）。它是今天的快照，而且產品曝險族群是預覽版；套用到過去有事後偏差，挑哪些主題本身就反映了 2025–26 年的行情。

## 附錄：第三輪評分過的因子（第 30–103 輪）

「t」是研究期 IC 的 t 值，「24 日中位數」是研究期前 24 檔、每 5 日調整、扣成本後，24 個交易日贏 150 檔等權的幅度。

| 輪 | 因子 | 5 日 t | 20 日 t | 24 日中位數 | 結果 |
|---|---|---|---|---|---|
| 30 | （沒有因子進入評分） | – | – | – | 寫程式失敗 |
| 31 | `IndustryNeutralMomentum_125d_Skip20_Rank` | +2.39 | +1.99 | +0.83% | t 不足 |
| 31 | `ForeignOwnershipTrend_250d_Rank` | +0.52 | -0.07 | +0.54% | t 不足、正的年度太少 |
| 31 | `TurnoverShock_20d_250d_Rank` | -0.05 | -0.39 | -0.68% | t 不足、24 日中位數 ≤ 0、正的年度太少 |
| 32 | `IndustryNeutralMomentum_125d_Skip20_VolScaled60d_Rank` | +2.09 | +1.69 | +0.57% | t 不足 |
| 32 | `NetShareIssuance_252d_Rank` | +1.10 | +0.62 | -0.10% | t 不足、24 日中位數 ≤ 0 |
| 33 | `IndustryNeutralMomentum_125d_Skip20_SizeNeutral_Rank` | +2.93 | +2.15 | +0.83% | t 不足 |
| 33 | `IndustryNeutralMomentum_125d_Skip20_SizeNeutral_VolScaled60d_Rank` | +2.40 | +1.72 | +0.34% | t 不足 |
| 34 | `IndustrySizeNeutralMom125Skip20_ForeignOwnershipResid_85_15_Rank` | +3.30 | +2.38 | +0.73% | 同輪重複 |
| 34 | `IndustrySizeNeutralMom125Skip20_ForeignOwnershipResid_70_30_Rank` | +3.72 | +2.71 | +0.31% | 同輪重複 |
| 34 | `IndustrySizeNeutralMom125Skip20_ForeignOwnershipResid_50_50_Rank` | +4.49 | +3.11 | +0.44% | 通過 |
| 35 | `PostJumpRetention_10d_Rank` | +0.85 | +2.10 | -1.87% | t 不足、24 日中位數 ≤ 0 |
| 36 | `RevAccel_DivYield_ForeignOwnership_40_30_30_Rank` | +7.13 | +6.52 | +1.18% | 和已有因子太像 |
| 37 | `SlowComposite5_RevAccel_DivYield_ForeignOwn_HighLowPos_Mom125SizeNeutral_Rank` | +6.60 | +6.53 | +1.20% | 通過 |
| 38 | `IndustryNeutralMoneyFlow_60d_Skip5_Rank` | +3.44 | +2.49 | +0.44% | 通過 |
| 38 | `IndustryNeutralIntradayDemand_60d_Skip5_Rank` | +1.76 | +1.97 | +0.08% | t 不足 |
| 39 | `IndustryNeutralMoneyFlowTrend_20d_60d_Skip5_Rank` | +2.28 | +2.51 | +0.11% | t 不足 |
| 39 | `IndustryNeutralSignedVolumePressure_60d_Skip5_Rank` | +2.05 | +1.57 | -0.22% | t 不足、24 日中位數 ≤ 0 |
| 39 | `IndustryNeutralIntradayDemand_60d_Rank` | +2.48 | +2.25 | +0.03% | t 不足 |
| 40 | `AmountWeightedIntradayDemand_60d_Rank` | +1.53 | +1.68 | -0.41% | t 不足、24 日中位數 ≤ 0 |
| 40 | `AmountWeightedIntradayDemand_120d_Rank` | +1.51 | +1.60 | -0.68% | t 不足、24 日中位數 ≤ 0 |
| 41 | `VWAP_SupportBreadth_60d_Rank` | +4.03 | +3.03 | -0.17% | 和已有因子太像、24 日中位數 ≤ 0 |
| 42 | `VWAPSupportBreadth_Residual_PriceBlock_60d_Rank` | +3.78 | +2.58 | -0.64% | t 不足、24 日中位數 ≤ 0 |
| 43 | `IndustryNeutral_CloseVWAP_Support_60d_Rank` | +2.32 | +1.55 | +0.42% | 和已有因子太像、t 不足 |
| 43 | `IndustryNeutral_CloseVWAP_Support_120d_Rank` | +2.42 | +1.44 | +0.31% | t 不足 |
| 44 | `IndustryNeutral_RevYoy_ZScore24m_Rank` | +4.08 | +3.12 | +0.33% | 和已有因子太像 |
| 44 | `RevAccel_PriceUnderreaction_125dSkip20_Rank` | -0.29 | +0.12 | -0.20% | t 不足、24 日中位數 ≤ 0 |
| 45 | `RevenueSurprise_Residual_24m_Rank` | +1.57 | +1.43 | +0.07% | t 不足 |
| 46 | （沒有因子進入評分） | – | – | – | 寫程式失敗 |
| 47 | `RevenueSurprise_FreshWeight15_DistinctObs_Rank` | +4.08 | +3.13 | +0.33% | 和已有因子太像 |
| 47 | `RevenueSurprise_FreshWeight10_DistinctObs_Rank` | +4.08 | +3.13 | +0.33% | 和已有因子太像 |
| 47 | `RevenueSurprise_FreshWeight20_DistinctObs_Rank` | +4.08 | +3.13 | +0.33% | 和已有因子太像 |
| 48 | `DownDayCloseLocation_60d_Skip5_Rank` | +2.68 | +2.14 | +0.10% | t 不足 |
| 48 | `AmountCLVCovariance_60d_Skip5_Rank` | -0.08 | +0.53 | -0.14% | t 不足、24 日中位數 ≤ 0、正的年度太少 |
| 49 | `AbnormalDownDayCLV_60d_Skip5_Rank` | -0.35 | +0.52 | -0.34% | t 不足、24 日中位數 ≤ 0、正的年度太少 |
| 49 | `AbnormalDownDayCLV_120d_Skip5_Rank` | -2.31 | -1.44 | -0.72% | t 不足、24 日中位數 ≤ 0、正的年度太少 |
| 49 | `DownDayCLV_AbnormalZ_60d_Skip5_Rank` | -0.73 | +0.17 | -0.21% | t 不足、24 日中位數 ≤ 0、正的年度太少 |
| 50 | `IndustryNeutral_WeakDayCLV_60d_Skip5_Rank` | +1.70 | +1.31 | -0.35% | t 不足、24 日中位數 ≤ 0 |
| 50 | `IndustryNeutral_WeakDayCLV_Breadth_60d_Skip5_Rank` | +1.53 | +1.06 | -0.27% | t 不足、24 日中位數 ≤ 0 |
| 51 | `IndustryNeutralResidualMomentum_100d_Skip20_Rank` | +2.79 | +1.97 | +0.73% | t 不足 |
| 52 | `IndustryNeutralResidualMomentum_100d_Skip20_SizeLiquidityNeutral_Rank` | +2.58 | +1.54 | +0.35% | t 不足 |
| 52 | `IndustryNeutralResidualMomentum_100d_Skip20_SizeLiquidityBetaNeutral_Rank` | +2.51 | +1.42 | +0.26% | t 不足 |
| 53 | `IndustryNeutral_EarningsRevisionYield_126d_Rank` | +2.70 | +2.27 | +0.46% | t 不足 |
| 53 | `IndustryNeutral_EarningsRevisionYield_252d_Rank` | +2.42 | +1.97 | +0.50% | t 不足 |
| 54 | `IndustryNeutral_ImpliedEarningsGrowth_126d_Rank` | +2.43 | +2.26 | +0.74% | t 不足 |
| 54 | `IndustryNeutral_ImpliedEarningsGrowth_63d_Rank` | +2.47 | +1.80 | +0.24% | t 不足 |
| 55 | `IndustryNeutral_EarningsRevisionYield_63d_Rank` | +2.77 | +2.22 | +0.32% | t 不足 |
| 55 | `IndustryNeutral_EarningsRevisionAccel_63d_Rank` | +2.33 | +1.57 | +0.14% | t 不足 |
| 55 | `IndustryNeutral_EarningsRevisionYieldZ_63d_Rank` | +1.96 | +2.18 | +0.25% | t 不足 |
| 55 | `IndustryNeutral_MarginExpansion_252d_Rank` | -0.03 | -0.07 | -0.10% | t 不足、24 日中位數 ≤ 0、正的年度太少 |
| 56 | `IndustryNeutral_DividendPayoutRatio_SizeNeutral_Rank` | -0.64 | +0.01 | +0.23% | t 不足 |
| 57 | `IndustryNeutral_ShortInterestIncrease_20d_Skip5_SizeNeutral_Rank` | +0.20 | -0.53 | -0.35% | t 不足、24 日中位數 ≤ 0、正的年度太少 |
| 57 | `IndustryNeutral_ShortInterestIncrease_10d_Skip5_SizeNeutral_Rank` | -0.14 | -0.69 | -0.32% | t 不足、24 日中位數 ≤ 0、正的年度太少 |
| 57 | `IndustryNeutral_ShortInterestIncrease_40d_Skip5_SizeNeutral_Rank` | +0.26 | -0.00 | -0.07% | t 不足、24 日中位數 ≤ 0、正的年度太少 |
| 58 | `IndustryNeutral_EarningsRevisionYield_63d_SizeLiquidityNeutral_Rank` | +2.76 | +2.35 | +0.13% | t 不足 |
| 58 | `IndustryNeutral_EarningsRevisionYield_126d_SizeLiquidityNeutral_Rank` | +2.45 | +2.18 | +0.03% | t 不足 |
| 59 | `IndustryNeutral_EarningsHighProximity_252d_Rank` | +1.77 | +0.90 | +0.10% | t 不足 |
| 59 | `IndustryNeutral_EarningsRangePosition_252d_Rank` | +2.15 | +1.70 | +0.47% | t 不足 |
| 60 | `IndustryNeutral_ForeignAccumulationConsistency_60d_Skip5_Rank` | +0.88 | +0.24 | +0.00% | t 不足 |
| 60 | `IndustryNeutral_ForeignAccumulationConsistency_120d_Skip5_Rank` | +1.90 | +1.00 | +0.18% | t 不足 |
| 61 | `UpDayConsistency_40d_Skip5_Rank` | +3.02 | +2.35 | +0.51% | 通過 |
| 61 | `UpDayConsistency_20d_Skip5_Rank` | +2.08 | +1.84 | +0.38% | t 不足 |
| 62 | `UpDownPayoffAsymmetry_40d_Skip5_Rank` | +2.45 | +2.24 | +0.34% | 和已有因子太像、t 不足 |
| 62 | `UpDownMeanMagnitudeRatio_40d_Skip5_Rank` | +0.21 | +0.48 | +0.17% | t 不足、正的年度太少 |
| 63 | `UpDownPayoffAsymmetry_40d_Skip5_SizeIndMomResid_Rank` | +2.30 | +2.12 | -0.10% | t 不足、24 日中位數 ≤ 0 |
| 64 | `IndustryNeutral_ReturnSkewShape_120d_Skip5_Rank` | -2.18 | -1.40 | -0.35% | t 不足、24 日中位數 ≤ 0、正的年度太少 |
| 64 | `IndustryNeutral_ReturnSkewShape_60d_Skip5_Rank` | -0.79 | -0.75 | -0.34% | t 不足、24 日中位數 ≤ 0、正的年度太少 |
| 65 | `IndustryNeutral_RevenueGrowthRevision_1m_Rank` | +1.19 | -0.18 | -0.22% | t 不足、24 日中位數 ≤ 0 |
| 65 | `IndustryNeutral_Revenue3mGrowthRevision_1m_Rank` | +2.80 | +1.31 | +0.50% | t 不足 |
| 66 | `PublicationAligned_Revenue3mGrowthRevision_IndustryNeutral_Rank` | +2.61 | +1.28 | +0.64% | t 不足 |
| 67 | `PublicationAligned_Revenue3mGrowthRevision_VolScaled_IndustryNeutral_Rank` | +4.70 | +3.68 | +0.74% | 同輪重複 |
| 67 | `IndustryNeutral_Revenue3mGrowthRevision_21dLag_VolScaled_Rank` | +4.90 | +3.92 | +0.50% | 通過 |
| 68 | `IntradayReturnDominance_60d_Skip5_IndustryNeutral_Rank` | +1.40 | +0.91 | -0.11% | t 不足、24 日中位數 ≤ 0 |
| 68 | `IntradayReturnDominance_120d_Skip5_IndustryNeutral_Rank` | – | – | – | 程式含代號 |
| 69 | `IndustryNeutral_EarningsRevision_63d_OwnVolScaled_Rank` | +2.91 | +2.61 | +0.03% | t 不足 |
| 69 | `IndustryNeutral_EarningsRevision_126d_OwnVolScaled_Rank` | +3.57 | +3.01 | +0.49% | 通過 |
| 70 | `IndustryNeutral_Revenue12mGrowthRevision_21dLag_OwnVolScaled_Rank` | +3.48 | +2.22 | +0.15% | 通過 |
| 70 | `IndustryNeutral_Revenue3mGrowthRevision_63dLag_OwnVolScaled_Rank` | +4.19 | +3.97 | +0.89% | 和已有因子太像 |
| 71 | `IndustryNeutral_Revenue3mGrowthRevision_63dLag_ResidualToFastRevisionRanks_Rank` | +1.76 | +2.46 | -0.21% | t 不足、24 日中位數 ≤ 0 |
| 71 | `IndustryNeutral_Revenue12mGrowthRevision_63dLag_ResidualToFastRevisionRanks_Rank` | +2.14 | +2.57 | +0.06% | t 不足 |
| 72 | `IndustryNeutral_DividendGrowth_252d_Rank` | +2.27 | +1.16 | +0.11% | t 不足 |
| 72 | `IndustryNeutral_DividendGrowth_126d_Rank` | +1.68 | +0.46 | -0.62% | t 不足、24 日中位數 ≤ 0 |
| 73 | `IndustryNeutral_EarningsSupportedDividendGrowth_252d_Rank` | +2.29 | +1.65 | +0.08% | t 不足 |
| 74 | `IndustryNeutral_Revenue3mGrowthRevision_63dLag_PartialResidualToFastRanks_Rank` | +3.97 | +3.86 | +0.72% | 通過 |
| 74 | `IndustryNeutral_Revenue3mGrowthRevision_63dLag_HardPartialResidualToFastRanks_Rank` | +3.62 | +3.61 | +0.43% | 同輪重複 |
| 74 | `IndustryNeutral_Revenue12mGrowthRevision_63dLag_PartialResidualToFastRanks_Rank` | +3.12 | +2.70 | +0.55% | 和已有因子太像、t 不足 |
| 75 | `IndustryNeutral_MarginDaysToCover_60d_Rank` | +1.01 | +0.49 | -0.56% | t 不足、24 日中位數 ≤ 0 |
| 75 | `IndustryNeutral_ShortDaysToCover_60d_Rank` | +0.90 | +0.54 | +0.01% | t 不足 |
| 76 | `IndustryNeutral_ReturnSkewAversion_252d_Skip5_Rank` | +0.97 | +0.89 | -0.37% | t 不足、24 日中位數 ≤ 0 |
| 76 | `IndustryNeutral_ReturnSkewAversion_189d_Skip5_Rank` | +1.86 | +1.01 | -0.36% | t 不足、24 日中位數 ≤ 0 |
| 77 | `IndustryNeutral_ReturnMAX3_21d_Skip5_VolScaled_Rank` | -1.66 | -1.99 | -1.68% | t 不足、24 日中位數 ≤ 0、正的年度太少 |
| 78 | `IndustryNeutral_RevenueRevisionBreadth_252d_Rank` | +2.85 | +2.46 | +0.22% | 和已有因子太像、t 不足 |
| 79 | `IndustryNeutral_RevenuePublicationEarlinessDeviation_252d_Rank` | +nan | +nan | -0.23% | t 不足、24 日中位數 ≤ 0、正的年度太少 |
| 79 | `IndustryNeutral_RevenuePublicationEarlinessLevel_Rank` | +nan | +nan | -0.23% | t 不足、24 日中位數 ≤ 0、正的年度太少 |
| 80 | `IndustryNeutral_RevenueGrowthTrendTStat_252d_Rank` | +3.24 | +2.54 | +0.42% | 和已有因子太像、t 不足 |
| 80 | `IndustryNeutral_RevenueGrowthTrendTStatExFresh_252d_Rank` | +2.30 | +1.94 | +0.21% | 和已有因子太像、t 不足 |
| 81 | `IndustryNeutral_RevenueTrendTStat_252d_ResidualToAcceptedRevision_Rank` | +2.34 | +1.62 | +0.42% | 和已有因子太像、t 不足、正的年度太少 |
| 82 | `IndustryNeutral_RevenueGrowthStability_252d_Rank` | -1.91 | -1.63 | -0.61% | t 不足、24 日中位數 ≤ 0、正的年度太少 |
| 82 | `IndustryNeutral_ROEChange_63d_Rank` | +3.06 | +2.97 | -0.04% | t 不足、24 日中位數 ≤ 0 |
| 83 | `IndustryNeutral_ROEChange_63d_BetaResid_Rank` | +2.96 | +2.86 | -0.30% | t 不足、24 日中位數 ≤ 0 |
| 84 | `IndustryNeutral_ROEChange_63d_OwnVolScaled_Rank` | +3.36 | +3.36 | -0.07% | 24 日中位數 ≤ 0 |
| 85 | `IndustryNeutral_ROEChange_63d_OwnVolScaled_PBResidual_Rank` | +3.15 | +2.96 | -0.20% | t 不足、24 日中位數 ≤ 0 |
| 86 | `IndustryNeutral_ROEChange63_OwnVolScaled_x_ROELevel_Rank` | +4.09 | +3.50 | +0.58% | 通過 |
| 87 | `IndustryNeutral_ROEPersistence_315d_x_ROELevel_Rank` | +3.38 | +2.56 | +0.79% | 通過 |
| 88 | `IndustryNeutral_DividendGrowth_252d_x_ROELevel_Rank` | +2.54 | +1.98 | +0.04% | 和已有因子太像、t 不足 |
| 88 | `IndustryNeutral_BuybackIntensity_252d_x_ROELevel_Rank` | +2.91 | +2.28 | +0.43% | t 不足 |
| 89 | `IndustryNeutral_BuybackIntensity_252d_x_ROELevel_x_EarningsYield_Rank` | +3.88 | +2.88 | +0.36% | 通過 |
| 90 | `IndustryNeutral_BuybackIntensity_252d_Residual_vs_ROELevel_EarningsYield_Rank` | -1.13 | -1.00 | -0.44% | t 不足、24 日中位數 ≤ 0、正的年度太少 |
| 91 | `IndustryNeutral_ForeignHoldingLowBaseAccumulation_120d_Rank` | +0.87 | +0.32 | -0.01% | t 不足、24 日中位數 ≤ 0 |
| 91 | `IndustryNeutral_ForeignHoldingLowBaseAccumulation_60d_Rank` | +0.55 | -0.40 | +0.20% | t 不足、正的年度太少 |
| 91 | `IndustryNeutral_ForeignHoldingLowBaseAccumulation_240d_Rank` | +0.46 | -0.29 | +0.31% | t 不足、正的年度太少 |
| 92 | （沒有因子進入評分） | – | – | – | 寫程式失敗 |
| 93 | `IndustryNeutral_BuybackSurprise_63d_OwnNoiseScaled_x_ROELevel_Rank` | +4.29 | +3.47 | +0.34% | 通過 |
| 93 | `IndustryNeutral_MarginBalanceDeleveraging_63d_x_ROELevel_Rank` | +2.93 | +2.24 | +0.25% | t 不足 |
| 94 | `IndustryNeutral_SpeculativePositionRelease_63d_x_ROELevel_Rank` | +2.92 | +2.30 | +0.09% | t 不足 |
| 94 | `IndustryNeutral_DealerNetBuying_60d_x_ROELevel_Rank` | +1.43 | +1.93 | +0.27% | t 不足 |
| 95 | `IndustryNeutral_MarginDeleveragingSurprise_63d_OwnNoiseScaled_x_ROELevel_Rank` | +3.56 | +2.93 | +0.26% | 通過 |
| 95 | `IndustryNeutral_MarginOverhangRelease_63d_x_ROELevel_x_Overhang_Rank` | +2.84 | +2.81 | +0.33% | t 不足 |
| 96 | `IndustryNeutral_MarginDeleveragingSurprise_63d_OwnNoiseScaled_ROEResid_Rank` | +1.51 | +1.79 | +0.25% | 和已有因子太像、t 不足 |
| 97 | `IndustryNeutral_ForeignNetBuyingSurprise_63d_OwnNoiseScaled_x_ROELevel_Rank` | +3.56 | +2.86 | +0.33% | 通過 |
| 97 | `IndustryNeutral_ForeignNetBuyingSurprise_63d_OwnNoiseScaled_Rank` | +1.43 | +0.94 | +0.07% | t 不足 |
| 98 | `IndustryNeutral_ForeignNetBuyingPressure_63d_OwnNoiseScaled_x_ROELevel_Rank` | +3.19 | +2.67 | +0.14% | 和已有因子太像、t 不足 |
| 98 | `IndustryNeutral_ForeignNetBuyingPressure_63d_OwnNoiseScaled_x_EarningsYield_Rank` | +3.43 | +3.18 | -0.01% | 24 日中位數 ≤ 0 |
| 99 | `IndustryNeutral_DealerNetBuyingSurprise_63d_OwnNoiseScaled_x_ROELevel_Rank` | +2.03 | +1.82 | +0.26% | t 不足 |
| 99 | `IndustryNeutral_DealerNetBuyingSurprise_63d_OwnNoiseScaled_Rank` | -1.54 | -0.95 | -0.98% | t 不足、24 日中位數 ≤ 0、正的年度太少 |
| 100 | `IndustryNeutral_ReturnSkewAversion_120d_Skip5_Rank` | +4.18 | +3.89 | -0.43% | 24 日中位數 ≤ 0 |
| 100 | `IndustryNeutral_DownsideUpsideDeviationRatio_120d_Skip5_Rank` | -2.19 | -2.41 | -0.68% | t 不足、24 日中位數 ≤ 0、正的年度太少 |
| 101 | `IndustryNeutral_ReturnSkewAversion_120d_Skip5_BetaResidual_Rank` | +3.91 | +3.13 | -0.02% | 24 日中位數 ≤ 0 |
| 102 | `IndustryNeutral_SkewAversionBetaResid_x_20dMomSkip5_Rank` | +3.12 | +2.41 | -0.02% | t 不足、24 日中位數 ≤ 0 |
| 103 | `IndustryNeutral_TrustNetSellingSurprise_63d_OwnNoiseScaled_x_ROELevel_Rank` | +4.38 | +4.06 | +0.55% | 通過 |
| 103 | `IndustryNeutral_TrustNetSellingSurprise_63d_OwnNoiseScaled_Rank` | +1.54 | +1.59 | +0.04% | t 不足 |
