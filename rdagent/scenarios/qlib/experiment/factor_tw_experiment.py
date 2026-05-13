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
    Skips the CN-data materialization (daily_pv_all.h5) the parent uses.

    Also overrides the output-format example so the LLM sees TW instrument codes
    (4-digit zfilled like 2330, ETFs like 0050) and the canonical TW date window
    instead of the CN SH/SZ example baked into the shared prompt."""

    def __init__(self) -> None:
        super().__init__()
        self._output_format = _TW_OUTPUT_FORMAT_DESC

    def _load_source_data(self) -> str:
        return _TW_SOURCE_DATA_DESC


_TW_SOURCE_DATA_DESC = """\
The data is for the Taiwan Stock Exchange (TWSE) market, fetched from TWSE's
open data endpoints (T86 institutional, MI_MARGN margin/short, BWIBBU valuation,
MI_QFIIS foreign holdings) and merged with adjusted price/volume bars.

== Where to read it ==
A single HDF5 file `daily_pv.h5` is provided in the same working directory as
your factor script. Read it with pandas — DO NOT call `qlib.init()` or use
`D.features()`; the runtime has no qlib initialized:

    import pandas as pd
    df = pd.read_hdf("daily_pv.h5", key="data")
    # df has a MultiIndex (datetime, instrument) and 37 float columns.

== Index ==
  - Level 0: `datetime` (pd.Timestamp, daily bars, TWSE trading calendar)
  - Level 1: `instrument` (str)
    Universe: TWSE-listed ordinary stocks (4-digit codes like "2330", "2317",
    "1101") plus ETFs/ETNs ("0050", "006208", "00878", "00631l", ...). All codes
    are lowercase letters where present and zfilled to ≥4 digits.

== Date range (debug window — what your factor sees) ==
  2024-01-02 to 2026-05-08, ~580 trading days, ~2,700 instruments,
  ~1.45M rows. The full backtest window upstream is 2021-01-01 onward.

== Columns (37 float64) ==
Prices are RAW exchange prices. They are NOT back-adjusted, so any ratio
comparison across corporate actions should be normalized by `$factor`. `$factor`
is the cumulative adjustment factor (multiply raw price by it to get the
back-adjusted price).

Price / volume (Alpha158 base, ~99.9% non-null):
  $open $high $low $close $volume $factor $vwap $offset

Valuation (~73% non-null — only published for listed equities, not ETFs):
  $div_yield $pe_ratio $pb_ratio

Institutional net flows — 三大法人 daily net buy/sell shares from T86
(~80% non-null on individual stocks, NaN on ETFs):
  $foreign_net       net buy from 外資 (foreign investors)
  $trust_net         net buy from 投信 (domestic mutual funds)
  $dealer_net        net buy from 自營商 (dealer, total)
  $dealer_self_net   dealer proprietary book
  $dealer_hedge_net  dealer hedging book
  $inst_total_net    sum of foreign + trust + dealer (T86 line item)
  $total_inst_net    same as inst_total_net, kept for redundancy

Margin trading / 融資 (~78% non-null, NaN on stocks ineligible for margin):
  $margin_buy        new margin-buy volume (shares)
  $margin_sell       margin-sell volume
  $margin_redeem     forced redemptions
  $margin_balance    outstanding margin balance (end of day)
  $margin_prev_balance, $margin_next_limit

Short selling / 融券 (~78% non-null):
  $short_buy $short_sell $short_redeem
  $short_balance $short_prev_balance $short_next_limit
  $short_margin_ratio  券資比 = short_balance / margin_balance —
                       widely used as a CONTRARIAN sentiment indicator
                       (high ratio = retail bearish = squeeze risk).

Foreign-holdings ratios (~85% non-null):
  $issued_shares          total issued shares
  $foreign_remaining      shares still available to foreign buyers
  $foreign_held           shares currently held by foreigners
  $foreign_remaining_pct  remaining / issued
  $foreign_held_pct       held / issued
  $foreign_limit_pct      regulatory cap (1.0 for most stocks; lower for
                          regulated industries — approaching the cap is itself
                          a signal because it forces forced-selling pressure).

== Taiwan microstructure to remember when proposing factors ==
  - 三大法人 flows often diverge — the spread between foreign and trust/dealer
    is a documented alpha source.
  - 融資 and 融券 balances reflect retail leverage. Their changes lead price.
  - Daily price moves are clipped at ±10% (limit_threshold = 0.099 in backtest).
  - Round-trip cost = 0.4425% (0.1425% brokerage each side + 0.3% sell-side
    transaction tax) — much higher than US/CN. Favor LOWER-TURNOVER signals.
  - Prices respect TWSE tick grid (5 TWD increments above 1000 TWD, 1 TWD in
    500-1000, ...). Sub-tick fractional moves do not happen.
  - Many columns are sparse for ETFs (no margin, no valuation, no T86). Either
    drop NaN rows per-factor or design factors that gracefully degrade.
"""


_TW_OUTPUT_FORMAT_DESC = """\
Your output should be a pandas DataFrame saved as `result.h5` with key="data",
matching the index layout of the input `daily_pv.h5`. Example structure:

<class 'pandas.core.frame.DataFrame'>
MultiIndex: 1453876 entries, (Timestamp('2024-01-02 00:00:00'), '0050') to (Timestamp('2026-05-08 00:00:00'), '9962')
Data columns (total 1 columns):
 #   Column            Non-Null Count    Dtype
---  ------            --------------    -----
 0   your_factor_name  1453876 non-null  float64
dtypes: float64(1)
memory usage: <ignore>

Notice: The non-null count is OK to be smaller than the total number of entries
(some instruments lack the factor on some days, e.g. NaN inputs, warm-up
period). One possible layout of `result.h5`:

datetime    instrument
2024-01-02  0050         0.001824
            0051         0.000337
            2330         0.005641
            2317         0.002108
            2454        -0.000994
                           ...
2026-05-08  9955         0.001209
            9957         0.000582

Instrument identifiers are TWSE codes (4-digit zfilled stocks like "2330",
"2317", "1101"; ETFs/ETNs like "0050", "006208", "00878", "00631l"). Datetimes
are pandas Timestamps on the TWSE trading calendar (no weekends, no Taiwan
public holidays). Do NOT emit SH/SZ-prefixed codes — those are mainland China
identifiers and will fail the universe check.
"""
