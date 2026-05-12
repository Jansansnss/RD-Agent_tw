"""
Taiwan-market variant of QlibFactorExperiment / QlibFactorScenario.

Differs from the China version only in:
  - Template folder points to factor_template_tw/ (region: tw, blue-chip universe).
  - Source-data description mentions TWSE-derived signals (法人進出, 融資融券,
    估值, 外資持股) so the LLM proposes Taiwan-relevant factors.

Everything else (CoSTEER coder, runner, hypothesis generation, feedback) is reused
from QlibFactorScenario via inheritance — the runner is region-agnostic and just
calls `qrun conf_baseline.yaml` inside the workspace.
"""
from pathlib import Path

from rdagent.components.coder.factor_coder.factor import FactorExperiment
from rdagent.scenarios.qlib.experiment.factor_experiment import (
    QlibFactorExperiment,
    QlibFactorScenario,
)
from rdagent.scenarios.qlib.experiment.workspace import QlibFBWorkspace


class QlibTwFactorExperiment(QlibFactorExperiment):
    def __init__(self, *args, **kwargs) -> None:
        # Skip QlibFactorExperiment.__init__ (which binds to factor_template/);
        # call its parent FactorExperiment directly, then set the TW workspace.
        FactorExperiment.__init__(self, *args, **kwargs)
        self.experiment_workspace = QlibFBWorkspace(
            template_folder_path=Path(__file__).parent / "factor_template_tw"
        )
        self.stdout = ""
        self.base_features: dict[str, str] = {}
        self.base_feature_codes: dict[str, str] = {}


class QlibTwFactorScenario(QlibFactorScenario):
    """Taiwan-market scenario: overrides the source-data description so the LLM
    knows it's working with TWSE blue chips and the Taiwan-specific signal set.
    Skips the CN-data materialization (daily_pv_all.h5) the parent uses."""

    def _load_source_data(self) -> str:
        return _TW_SOURCE_DATA_DESC


_TW_SOURCE_DATA_DESC = """\
The data is for the Taiwan Stock Exchange (TWSE) market, fetched directly from
TWSE's open data endpoints and stored in Qlib's binary on-disk format at
~/.qlib/qlib_data/tw_data.

Universe: TWSE-listed ordinary stocks (4-digit codes, zfilled). Smoke runs use a
權值股 subset (2330 TSMC, 2317 Hon Hai, 2454 MediaTek, 2412 Chunghwa Telecom,
2882 Cathay Financial). The benchmark is currently 2330 as a placeholder until
TAIEX / 0050.TW is ingested.

Frequency: daily bars. Prices are RAW exchange prices that obey the TWSE tick
grid (5 TWD increments above 1000 TWD, 1 TWD between 500-1000, etc.) — they are
NOT back-adjusted, so any factor that does ratio comparisons over corporate
actions should use the $factor column to normalize.

Available fields per stock (Qlib expression-language $-prefix):
  Price/volume:      $open $high $low $close $volume $factor $vwap
                     ($vwap is OHLC4 proxy until 成交金額 is captured)
  Institutional:     $foreign_net $trust_net $dealer_net $dealer_self_net
                     $dealer_hedge_net $inst_total_net $total_inst_net
                     (net buy/sell shares by 三大法人 from TWSE T86)
  Margin/short:      $margin_buy $margin_sell $margin_redeem $margin_balance
                     $margin_prev_balance $margin_next_limit
                     $short_buy $short_sell $short_redeem $short_balance
                     $short_prev_balance $short_next_limit $offset
                     $short_margin_ratio (券資比 — contrarian sentiment)
  Valuation:         $div_yield $pe_ratio $pb_ratio
  Foreign holdings:  $issued_shares $foreign_remaining $foreign_held
                     $foreign_remaining_pct $foreign_held_pct $foreign_limit_pct

Taiwan market microstructure to keep in mind when proposing factors:
  - Three institutional categories track separately: 外資 (foreign), 投信 (trust /
    mutual fund), 自營商 (dealer). Their flows often diverge — the spread between
    them is a known alpha source.
  - 融資 (margin) and 融券 (short) balances reflect retail leverage. The
    short_margin_ratio is widely used as a contrarian sentiment indicator.
  - Foreign holdings ratio is meaningful: foreign-investment caps exist for
    certain industries; approaching the cap is itself a signal.
  - Daily price moves are clipped at ±10% (limit_threshold = 0.099 in backtest).
  - Sell-side transaction tax is 0.3% on top of 0.1425% brokerage, so
    close_cost = 0.4425% — much higher than US/CN. Favor lower-turnover signals.
"""
