# 交件說明：把你的因子或策略交給大腦（tw-factor-report/1）

> 給隊友，也給隊友的 LLM。你的系統不用改成我們的架構：交下面三個檔，報告由同一支程式、同一套規則算出來，
> 大家的數字才能放在一起比。完整規格見 `FACTOR_REPORT.md`。

## 交什麼

| 檔案 | 必交 | 內容 |
|---|---|---|
| `weights.csv`（策略）或 `values.csv`（因子） | 二選一 | 每天收盤後決定的目標權重，或每檔股票的分數 |
| `<id>.yaml`（宣告） | 必交 | 怎麼算、為什麼有效、哪些預測可以被推翻。**看結果之前寫** |
| `<id>_tests.yaml`（自行測試） | 選交 | 你用自己的系統做的測試，報告會標成「自評」 |

策略＝每天直接決定持有哪些股票、各多少權重（例如模型加上選股與配置規則）；因子＝每天給每檔股票一個分數。

## 1. `weights.csv`（策略）

```csv
datetime,instrument,weight
2026-04-29,2330,0.20
2026-04-29,2454,0.06
```

- `datetime`：做決定的那天（YYYY-MM-DD），只能用那天收盤後就拿得到的資料；下一個交易日以當天 VWAP 成交。
- `instrument`：4 位數股票代號，當文字存，不要 `.TW`，也別讓 Excel 吃掉開頭的 0。
- `weight`：占淨值的比例（4.2% 寫 0.042）。每個決定日都列出**完整的**目標持股：沒列的股票＝賣掉；加總 ≤ 1，剩下的是現金。
- 沒出現的日子＝不交易，持股隨價格漂移。可以每天給，也可以只在調整的日子給。
- 期間至少涵蓋研究期 2021-01-01 → 2026-04-30，最好一路給到最近一天（2026-05-01 起是保留期，只用來確認）。
- 比賽規則程式會逐日檢查：只做多、20–30 檔、單檔 ≤ 10%（2330 ≤ 25%）、現金 < 25%、只買當月 150 檔。違反的會標在報告上。

## 2. `values.csv`（因子）

```csv
datetime,instrument,value
2026-04-29,2330,0.83
```

值越高越想買；每個值在 `datetime` 當天收盤時已知；一檔一天一個值，沒有值就不要列。

## 3. 宣告 `<id>.yaml`

- 把 `factor_declare_prompt_zh.md` 整份交給你的 LLM，在【隊友的想法】貼上你的模型、規則或程式，它會回一份 YAML。
- 看結果之前寫，寫完就 commit，commit 時間就是事前登錄的證明。看過結果才寫的，請誠實標 `registered: after_results`。
- 策略一定要填 `training`：模型或參數用哪段資料訓練、調整，多久重訓一次。訓練或調參用到 2021–2026 年的資料，研究期就是樣本內，報告會照你寫的標出來；沒寫會被標 `training_unstated`。
- `trials`：試過的模型、特徵組合、參數都要算進去。試越多次，純運氣能做出的最好 t 值越高，報告會拿你的 t 值跟它比。

## 4. 自行測試 `<id>_tests.yaml`（選交）

```yaml
- name: 自家回測（含滑價）
  method: 每天收盤後重算分數，買前 20 檔等權，隔日開盤價成交
  window: 2022-01-01 → 2026-04-30
  universe: 上市櫃市值前 300
  costs: 0.1425% 手續費、0.3% 證交稅、0.1% 滑價
  code_ref: https://github.com/<you>/<repo>/blob/<commit>/backtest.py
  results:
    年化報酬: 28.1%
    夏普: 1.4
    最大回撤: -22%
```

報告照你寫的原樣列在「自行測試（自評）」，給大腦的版本放在 `facts_custom`，份量比共用評分低。股票池、成本、成交價不同，數字就不能直接比，所以 `window`、`universe`、`costs` 都要寫。

## 5. 交件前自己檢查

```bash
python factor_report_check_tw.py --weights weights.csv --declaration mine.yaml --custom_tests mine_tests.yaml
python factor_report_check_tw.py --values values.csv --declaration mine.yaml
```

只需要 pandas 和 PyYAML（`pip install pyyaml`），檔案可以單獨複製出去用（連同 `factor_report_schema_tw.json`）。
RD-Agent 裡在 `rdagent/scenarios/qlib/tw_data/`，比賽 repo 裡在 `docs/rdagent/tools/`。「錯誤」修好才交，「提醒」是報告會標出來的事。

## 6. 交件之後

1. Jans 用 RD-Agent 跑 `portfolio_report_tw`（策略）或 `factor_report_tw`（因子），產生 `report.html`（給人看）、`report.json`（給大腦）和 `prompt_stage2.md`。
2. 你把 `prompt_stage2.md` 整份交給 LLM，把回覆的 `interpretation` 貼回宣告檔（第二階段），再算一次。
3. 要給大腦採用的，再交給跟作者不同的模型做獨立審查（A–F），摘要貼進宣告檔的 `review`。
4. `report.json` 進大腦。大腦以程式算的 `facts` 為準，宣告裡的文字只是主張。

## 不要做

- 不要用 `datetime` 當天晚上還拿不到的資料：當天收盤價和當天的法人買賣超可以用，月營收從次月 10 日起才算已知，季報以法定公布期限為準。
- 不要看 2026-05-01 之後的表現回頭調參數；調過就在 `training` 寫出來。
- 不要在宣告裡填回測數字；所有數字一律由程式算。
