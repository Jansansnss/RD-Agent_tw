"""
Scenario for the TW alpha factory loop (docs/tw/ALPHA_FACTORY.md, DECISIONS D15-D16).

Same CoSTEER coder and hypothesis generator as the TW factor loop; what changes is what the LLM is
told: the contest it researches for, the anonymized panel it codes against (stock IDs S001..,
research window only), and the fixed code-only judge that decides acceptance.
"""

from rdagent.scenarios.qlib.experiment.factor_tw_experiment import QlibTwFactorScenario


class QlibTwAlphaScenario(QlibTwFactorScenario):
    def __init__(self) -> None:
        super().__init__()
        self._background = _BACKGROUND + "\n\n" + self._background
        self._output_format = _OUTPUT_FORMAT
        self._simulator = _JUDGE
        self._experiment_setting = _SETTING

    def _load_source_data(self) -> str:
        return _SOURCE_DATA


_BACKGROUND = """\
You research stock-selection factors for a 24-trading-day portfolio contest. Each evening the
portfolio picks about 23 long-only names out of a fixed universe of 150 large-cap stocks of one
equity market, buys at the next day's volume-weighted average price, and pays 0.1425% per side
plus 0.3% tax on sells (0.585% round trip). Teams are ranked only by final net asset value, and
only the top few win, so what matters is how good the very top of the ranking is over 1 to 4
weeks, after costs. Factors that need daily turnover will lose to costs. A higher factor value
must mean "more attractive to buy"."""

_SOURCE_DATA = """\
== Where to read it ==
A single HDF5 file `daily_pv.h5` is in the working directory. Read it with pandas only:

    import pandas as pd
    df = pd.read_hdf("daily_pv.h5", key="data")   # MultiIndex (datetime, instrument)

== Index ==
  - datetime: trading days (pd.Timestamp)
  - instrument: anonymized stock IDs "S001", "S002", ... (about 300 stocks: everyone that was in
    the 150-stock universe at some month end). The same ID is the same stock on every date.

== Timing rule ==
Every value at (datetime, instrument) was known by that day's close. Use only the current and
earlier rows of a stock (rolling windows, shift(+n) per instrument). Never use shift(-n), never
center a window, never normalize with statistics over the whole sample (use rolling windows or a
per-date cross-section instead).

== Columns (float64) ==
Prices (back-adjusted for dividends and splits; use them for returns):
  $open $high $low $close $vwap   ($vwap = the day's volume-weighted average price)
Trading activity (raw, not adjusted):
  $volume (shares)  $amount (money traded)
  $market_value     market capitalization
Institutional net buying, in shares (divide by $issued_shares to compare stocks):
  $foreign_net (foreign investors)  $trust_net (domestic investment trusts)  $dealer_net (dealers)
Margin trading, in lots of 1000 shares: $margin_balance (margin buying)  $short_balance (short selling)
Valuation: $pe_ratio $pb_ratio $div_yield
Ownership: $issued_shares  $foreign_held_pct (foreign holding, percent of issued shares)
Monthly revenue, already lagged to its publication date (no extra lag needed):
  $rev          latest published monthly revenue
  $rev_yoy      latest month vs the same month a year earlier
  $rev_3m_yoy   last 3 months vs the same 3 months a year earlier
  $rev_12m_yoy  last 12 months vs the 12 months before
  $rev_accel    $rev_3m_yoy - $rev_12m_yoy
  $rev_age      calendar days since the latest month was published (0 = published that day)
Universe and groups:
  $in_universe  1 if the stock is in the 150-stock universe that month (only these rows are scored)
  $is_twse      1 = main board, 0 = second board (only on universe rows)
  $industry_id  integer industry group code, one per stock, from the industry value chain
                (semiconductors, computers and peripherals, cloud computing, PCBs, ...); for
                industry-relative factors

== Notes ==
  - A day with no trades has NaN prices and $vwap.
  - Daily price moves are limited to +/-10% on listed stocks.
  - Rows outside the universe are still useful for rolling windows; the judge ignores them.
  - Do not write any instrument ID, stock code or calendar date in the code. Code that does is
    rejected without being scored."""

_OUTPUT_FORMAT = """\
Save a pandas DataFrame as `result.h5` with key="data": the same MultiIndex (datetime,
instrument) as the input and exactly one float column named after the factor. NaN is fine where
the factor is undefined (warm-up periods, missing inputs).

datetime    instrument
2019-03-29  S001         0.0123
            S002        -0.0041
..."""

_JUDGE = """\
Each factor is scored by a fixed program, not by a person or an LLM, on the research period only:
  1. Rank IC: per-date Spearman correlation between the factor and the return from the next day's
     average price to the average price 1, 5, 10 and 20 days later, within the universe; mean, a
     Newey-West t-statistic, and the mean per calendar year.
  2. A long-only book: the top 24 stocks, equal weight, rebalanced every 5 days at the next day's
     average price, keeping holdings that still rank within the top 36, with the contest's costs;
     compared with the universe equal-weighted, over every rolling 24-day window.
  3. The top 10 names' 24-day return minus the universe average.
  4. Correlation with factors already accepted.
A factor is accepted only if all of these hold: Newey-West t of the mean Rank IC >= 3.0 at the 5-
or 20-day horizon; a positive mean IC in at least 3 calendar years; a positive median 24-day net
excess return of the book; correlation <= 0.6 with every accepted factor; values for >= 80% of the
universe. The thresholds are fixed; many ideas will fail them, and that is expected."""

_SETTING = """\
| Universe | Judge | Data |
|---|---|---|
| point-in-time 150 large caps | Rank IC + top-24 book with contest costs | research period only, anonymized IDs |"""
