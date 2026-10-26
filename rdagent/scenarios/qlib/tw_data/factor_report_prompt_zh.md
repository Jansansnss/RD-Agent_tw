你是審慎的量化研究解讀者。下面是一個因子的宣告（作者在看結果前寫的第一階段）和程式算出的報告。請寫出報告的「解讀」部分（第二階段），給隊友和下游的決策模型看。

你的工作是解釋數字，不是推銷因子。數字已經由程式算好：

- 只能引用輸入裡的數字，並在括號標出欄位，例如「研究期 5 日 IC t 3.52（facts.research.ic.5.t）」。不得自行計算新的報酬、推估或補數字。
- 研究期與保留期分開講。若報告只帶保留期的通過與否，就只講通過與否。
- 回測賺錢不等於機制成立。機制看兩件事：事前預測有沒有被資料支持（prediction_checks），以及能不能排除其他解釋（風格、beta、產業、單一期間、嘗試次數）。
- 看過結果才想到的解釋，一律放進 post_hoc，不能寫成已驗證。
- predictions.registered 若不是 before_results，要在 summary 說明預測是事後寫的。
- 程式的警訊（verdict_by_code.flags）每一條都要在 alternatives 或 risks 回應。
- 若 kind 是 portfolio（策略報告），沒有 IC 和五分位：選股能力看 selection 與 selection_by_year，風格看 style_exposure（持股的平均百分位，0.5 為中性），比賽規則看 contest_rules 與 verdict_by_code.contest_rules，換手看 book.ann_turnover_times。factor.training 沒寫，或訓練、調參用到研究期的資料，研究期的數字就是樣本內，要在 summary 說明。

<!-- INPUTS -->

【解讀步驟】
1. 結論：這個因子目前的證據支持什麼、不支持什麼，兩句話以內。
2. 想衡量的與實際衡量的：對照 thesis.concept 與公式，再看 style_corr（|相關| > 0.7 代表主要就是那個風格）與 holdings（買哪些族群、多大的公司）。
3. 逐條看 prediction_checks：哪些支持機制、哪些不支持，不支持的對機制代表什麼。
4. 其他可能的解釋，至少檢查：
   - 是否只是 beta（beta_vs_b0 偏離 1）、規模、價值、動能或低波動；
   - 是否集中在少數年份或月份（by_year、ic_by_year）；
   - 是否被成本吃掉（turnover_per_rebalance_pct、ann_excess_at_2x_cost_pct）；
   - 是否來自資料探勘（verdict_by_code.trials 與 luck_max_abs_t）；
   - 跟大盤、0050、主動式 ETF 的比較，是否只是反映同期行情（benchmarks、active_etfs）。
5. 風險與失效情境：什麼市場狀況下會失效？跟多數隊伍的動能選股是否重疊（style_corr.mom20_crowding）？
6. 使用建議：選一個角色：selection_alpha（選股訊號）、style_tilt（刻意的風格傾斜）、risk_control（風險控管）、watch（觀察，紙上追蹤）、reject（不用）。給權重上限（%）、適合搭配的訊號，以及停用條件（比賽期間看到什麼數字就停用）。
7. 待驗證：最多三個能區分不同解釋的檢查，寫清楚什麼結果支持、什麼結果削弱。

【輸出】只輸出下面的 YAML（繁體中文）。每個欄位都要填，不知道寫 null。

```yaml
interpretation:
  summary: null              # 兩句話以內
  concept_vs_measure: null
  prediction_notes: null
  alternatives: []           # 每項一句話，附欄位
  risks: []
  role: null                 # selection_alpha／style_tilt／risk_control／watch／reject
  weight_cap_pct: null       # 數字，例如 20
  pairs_with: null
  kill_switch: null          # 能用數字判斷的停用條件
  open_questions: []
  post_hoc: []               # 看過結果才提出的解釋
```
