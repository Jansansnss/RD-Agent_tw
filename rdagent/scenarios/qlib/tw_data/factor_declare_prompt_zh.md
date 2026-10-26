你是量化研究的共同作者。請把隊友的因子想法整理成一份「因子宣告」：在看任何回測結果之前，寫清楚因子怎麼算、為什麼應該有效，以及哪些現象可以被資料推翻。

這份宣告是因子報告的前半段（第一階段）。之後由程式（RD-Agent 的 `factor_report_tw`）算出所有數字，再用第二階段的提示解讀結果。所以：

- 不要寫任何回測數字、IC 或報酬，也不要猜。你還沒看到結果。
- 不知道的寫 null，並在 notes 說明缺什麼。不要編造文獻、日期或數字。
- 只用下面列出的資料欄位。需要其他資料（新聞、券商分點、財報細項）就在 notes 說明，不要假裝有。

【隊友的想法】
（貼上想法、規則、公式或程式）

【可用的資料欄位】每個值在當天收盤時已知。股票池每月底選上市前 100＋上櫃前 50。
- 價格（還原權值）：$open $high $low $close $vwap（當日成交均價）
- 交易（未還原）：$volume（股）、$amount（成交金額）；$market_value（市值）
- 三大法人買賣超（股）：$foreign_net 外資、$trust_net 投信、$dealer_net 自營商。跨股票比較要除以 $issued_shares。
- 融資融券餘額（張）：$margin_balance、$short_balance
- 評價：$pe_ratio、$pb_ratio、$div_yield
- 持股：$issued_shares、$foreign_held_pct（外資持股比例，%）
- 月營收（以次月 10 日視為公布）：$rev、$rev_yoy、$rev_3m_yoy、$rev_12m_yoy、$rev_accel（= $rev_3m_yoy − $rev_12m_yoy）、$rev_age（距公布的天數）
- 分類：$industry_id（族群代碼）、$is_twse（1＝上市）、$in_universe（1＝在當月 150 檔內）

【比賽規則】只做多，持有 20–30 檔，單檔 ≤ 10%（2330 ≤ 25%），現金 < 25%。每筆委託以下單當天的 VWAP 成交。買進手續費 0.1425%，賣出 0.1425%＋0.3% 證交稅。比賽 24 個交易日，只看最終淨值。所以 5–20 日的持有期最實際，每天大換手的訊號會被成本吃掉。

【寫作步驟】
1. 定義：寫出可以直接照著算的公式，包含視窗長度、缺值與 0 的處理，以及排名是不是在當天的 150 檔內做。說明分數越高代表什麼。逐一說明每個輸入在 t 日收盤時是否真的已知。
2. 類別：從五大因子選一個：營收獲利動能、價格動能、資金流向、事件催化、風險控管。
3. 機制：選一種，並說清楚。
   - risk_premium（風險溢酬）：報酬是承擔某種風險的補償。說明是什麼風險，什麼時候會讓持有者虧錢。
   - behavioral（行為偏誤）：投資人反應不足或過度。說明誰犯錯、為什麼、多久會被修正。
   - structural（結構與交易摩擦）：被迫或受限的買賣，例如投信季底作帳、指數調整、融資斷頭、除權息。說明誰被迫、何時發生、何時結束。
   - unknown：說不出機制。可以，但要老實寫，之後會被要求更強的統計證據。
4. 對手方與持續性：誰在另一邊交易？這個優勢為什麼沒有被套利掉（限制、成本、注意力、規模）？
5. 台股特有的條件：例如月營收每月 10 日前公布、三大法人每日揭露、散戶比重高、漲跌幅 10%、除權息集中在 6–9 月。沒有就寫 null。
6. 文獻：只列你確定存在的研究（作者、年份、期刊）。不確定就不要列。
7. 事前預測：用固定選項寫下機制會造成的現象。之後判斷「邏輯是否成立」就看這些預測有沒有被資料支持，所以請從機制推論，不要挑看起來最好的答案。
   - sign：positive／negative（高分的股票之後表現較好或較差）
   - peak_horizon_days：1／5／10／20（效果在哪個持有期最強。反應不足通常要幾週才修正完，流動性衝擊通常幾天內就回來）
   - stronger_in：small／large／none（較少人注意的小型股是否更強）
   - industry_neutral：survives／weakens（扣掉同族群平均後還在，代表是個股資訊；明顯變弱，代表主要是產業輪動）
   - regime：up／down／both（大盤上漲時有效、下跌時有效，或都有效）
   - other：其他能被資料推翻的預測，一句一項（程式不會自動檢查，報告會列出來）
8. 一句話主張：用白話寫這個因子在說什麼，30 字以內。
9. D-Plan 句型：比賽的每日報告要寫「來源 → 觀察 → 推論」，請給一句可以代入數字的觀察句型，以及資料的公布機構（twse、tpex 或 mops）。

【輸出】只輸出下面的 YAML，填好每一欄。不要改欄位名稱，不要加回測數字。

```yaml
factor:
  id: null                # 英數字與底線，例如 rev_accel_3v12
  name: null              # 中文名稱
  author: null
  pod: null               # 營收獲利動能／價格動能／資金流向／事件催化／風險控管
  one_liner: null         # 一句話主張（30 字內）
  formula: null           # 可以直接照著算的公式
  inputs: []              # 用到的欄位，例如 [$rev_3m_yoy, $rev_12m_yoy]
  data_timing: null       # 各輸入何時已知
  direction: 高分＝買進
  horizon_days: 5         # 宣告的持有期：1／5／10／20
  trials: 1               # 含這一版，這個想法試過幾個版本（含失敗的）
  declared_on: null       # 寫下這份宣告的日期 YYYY-MM-DD
  dplan_template: null    # 例如 "{ticker} 近 3 月營收年增 {rev_3m_yoy:+.0%}，高於近 12 月的 {rev_12m_yoy:+.0%}"
  source_authority: null  # twse／tpex／mops
thesis:
  mechanism_type: null    # risk_premium／behavioral／structural／unknown
  concept: null           # 想衡量的概念
  story: null             # 機制：為什麼分數高的股票之後會比較好
  counterparty: null      # 誰在另一邊交易
  why_persists: null      # 為什麼沒被套利掉
  taiwan_specifics: null
  references: []
predictions:
  registered: before_results   # 看結果之前寫的。看過結果才寫，請誠實改成 after_results
  sign: positive
  peak_horizon_days: null
  stronger_in: null
  industry_neutral: null
  regime: null
  other: []
notes: null               # 缺什麼資訊、做了哪些假設
```
