from typing import Optional

from pydantic_settings import SettingsConfigDict

from rdagent.components.workflow.conf import BasePropSetting


class ModelBasePropSetting(BasePropSetting):
    model_config = SettingsConfigDict(env_prefix="QLIB_MODEL_", protected_namespaces=())

    # 1) override base settings
    scen: str = "rdagent.scenarios.qlib.experiment.model_experiment.QlibModelScenario"
    """Scenario class for Qlib Model"""

    hypothesis_gen: str = "rdagent.scenarios.qlib.proposal.model_proposal.QlibModelHypothesisGen"
    """Hypothesis generation class"""

    hypothesis2experiment: str = "rdagent.scenarios.qlib.proposal.model_proposal.QlibModelHypothesis2Experiment"
    """Hypothesis to experiment class"""

    coder: str = "rdagent.scenarios.qlib.developer.model_coder.QlibModelCoSTEER"
    """Coder class"""

    runner: str = "rdagent.scenarios.qlib.developer.model_runner.QlibModelRunner"
    """Runner class"""

    summarizer: str = "rdagent.scenarios.qlib.developer.feedback.QlibModelExperiment2Feedback"
    """Summarizer class"""

    evolving_n: int = 10
    """Number of evolutions"""

    train_start: str = "2008-01-01"
    """Start date of the training segment"""

    train_end: str = "2014-12-31"
    """End date of the training segment"""

    valid_start: str = "2015-01-01"
    """Start date of the validation segment"""

    valid_end: str = "2016-12-31"
    """End date of the validation segment"""

    test_start: str = "2017-01-01"
    """Start date of the test / backtest segment"""

    test_end: Optional[str] = "2020-08-01"
    """End date of the test / backtest segment"""


class FactorBasePropSetting(BasePropSetting):
    model_config = SettingsConfigDict(env_prefix="QLIB_FACTOR_", protected_namespaces=())

    # 1) override base settings
    scen: str = "rdagent.scenarios.qlib.experiment.factor_experiment.QlibFactorScenario"
    """Scenario class for Qlib Factor"""

    hypothesis_gen: str = "rdagent.scenarios.qlib.proposal.factor_proposal.QlibFactorHypothesisGen"
    """Hypothesis generation class"""

    hypothesis2experiment: str = "rdagent.scenarios.qlib.proposal.factor_proposal.QlibFactorHypothesis2Experiment"
    """Hypothesis to experiment class"""

    coder: str = "rdagent.scenarios.qlib.developer.factor_coder.QlibFactorCoSTEER"
    """Coder class"""

    runner: str = "rdagent.scenarios.qlib.developer.factor_runner.QlibFactorRunner"
    """Runner class"""

    summarizer: str = "rdagent.scenarios.qlib.developer.feedback.QlibFactorExperiment2Feedback"
    """Summarizer class"""

    evolving_n: int = 10
    """Number of evolutions"""

    train_start: str = "2008-01-01"
    """Start date of the training segment"""

    train_end: str = "2014-12-31"
    """End date of the training segment"""

    valid_start: str = "2015-01-01"
    """Start date of the validation segment"""

    valid_end: str = "2016-12-31"
    """End date of the validation segment"""

    test_start: str = "2017-01-01"
    """Start date of the test / backtest segment"""

    test_end: Optional[str] = "2020-08-01"
    """End date of the test / backtest segment"""


class FactorFromReportPropSetting(FactorBasePropSetting):
    # 1) override the scen attribute
    scen: str = "rdagent.scenarios.qlib.experiment.factor_from_report_experiment.QlibFactorFromReportScenario"
    """Scenario class for Qlib Factor from Report"""

    # 2) sub task specific:
    report_result_json_file_path: str = "git_ignore_folder/report_list.json"
    """Path to the JSON file listing research reports for factor extraction"""

    max_factors_per_exp: int = 6
    """Maximum number of factors implemented per experiment"""

    report_limit: int = 20
    """Maximum number of reports to process"""


class QuantBasePropSetting(BasePropSetting):
    model_config = SettingsConfigDict(env_prefix="QLIB_QUANT_", protected_namespaces=())

    # 1) override base settings
    scen: str = "rdagent.scenarios.qlib.experiment.quant_experiment.QlibQuantScenario"
    """Scenario class for Qlib Model"""

    quant_hypothesis_gen: str = "rdagent.scenarios.qlib.proposal.quant_proposal.QlibQuantHypothesisGen"
    """Hypothesis generation class"""

    model_hypothesis2experiment: str = "rdagent.scenarios.qlib.proposal.model_proposal.QlibModelHypothesis2Experiment"
    """Hypothesis to experiment class"""

    model_coder: str = "rdagent.scenarios.qlib.developer.model_coder.QlibModelCoSTEER"
    """Coder class"""

    model_runner: str = "rdagent.scenarios.qlib.developer.model_runner.QlibModelRunner"
    """Runner class"""

    model_summarizer: str = "rdagent.scenarios.qlib.developer.feedback.QlibModelExperiment2Feedback"
    """Summarizer class"""

    factor_hypothesis2experiment: str = (
        "rdagent.scenarios.qlib.proposal.factor_proposal.QlibFactorHypothesis2Experiment"
    )
    """Hypothesis to experiment class"""

    factor_coder: str = "rdagent.scenarios.qlib.developer.factor_coder.QlibFactorCoSTEER"
    """Coder class"""

    factor_runner: str = "rdagent.scenarios.qlib.developer.factor_runner.QlibFactorRunner"
    """Runner class"""

    factor_summarizer: str = "rdagent.scenarios.qlib.developer.feedback.QlibFactorExperiment2Feedback"
    """Summarizer class"""

    evolving_n: int = 10
    """Number of evolutions"""

    action_selection: str = "bandit"
    """Action selection strategy: 'bandit' for bandit-based selection, 'llm' for LLM-based selection, 'random' for random selection"""

    train_start: str = "2008-01-01"
    """Start date of the training segment"""

    train_end: str = "2014-12-31"
    """End date of the training segment"""

    valid_start: str = "2015-01-01"
    """Start date of the validation segment"""

    valid_end: str = "2016-12-31"
    """End date of the validation segment"""

    test_start: str = "2017-01-01"
    """Start date of the test / backtest segment"""

    test_end: Optional[str] = "2020-08-01"
    """End date of the test / backtest segment"""


class FactorTwBasePropSetting(FactorBasePropSetting):
    """Taiwan-market overrides — same loop, different scenario class and dates
    aligned to our TWSE bin data window."""

    model_config = SettingsConfigDict(env_prefix="QLIB_FACTOR_TW_", protected_namespaces=())

    scen: str = "rdagent.scenarios.qlib.experiment.factor_tw_experiment.QlibTwFactorScenario"
    """Scenario class for Qlib Taiwan Factor"""

    hypothesis_gen: str = "rdagent.scenarios.qlib.proposal.factor_proposal.QlibTwFactorHypothesisGen"
    """TW hypothesis gen — injects data/factor_ideas.md into the spec (env: FACTOR_TW_IDEAS_PATH to override)."""

    hypothesis2experiment: str = (
        "rdagent.scenarios.qlib.proposal.factor_proposal.QlibTwFactorHypothesis2Experiment"
    )
    """TW converter — wraps tasks in QlibTwFactorExperiment (factor_template_tw/)."""

    runner: str = "rdagent.scenarios.qlib.developer.factor_runner.QlibTwFactorRunner"
    """TW runner — feeds qrun the TW date window."""

    train_start: str = "2021-01-01"
    train_end: str = "2023-12-31"
    valid_start: str = "2024-01-01"
    valid_end: str = "2024-12-31"
    test_start: str = "2025-01-01"
    # Must be at least 1 trading day INSIDE the qlib calendar — qlib's signal
    # strategy peeks at [last_idx + 1] for next-day execution; if test_end is
    # the last calendar entry (or past it, after snapping) it raises
    # IndexError in qlib/backtest/utils.py:get_step_time.
    test_end: Optional[str] = "2026-05-07"  # rolling — keep <= calendar_end - 1


class ModelTwBasePropSetting(ModelBasePropSetting):
    """Taiwan-market overrides for the model R&D loop. Same loop as
    ModelBasePropSetting, but points at the TW scenario/runner and the
    TWSE date window."""

    model_config = SettingsConfigDict(env_prefix="QLIB_MODEL_TW_", protected_namespaces=())

    scen: str = "rdagent.scenarios.qlib.experiment.model_experiment.QlibTwModelScenario"
    """Scenario class for Qlib Taiwan Model"""

    hypothesis2experiment: str = (
        "rdagent.scenarios.qlib.proposal.model_proposal.QlibTwModelHypothesis2Experiment"
    )
    """TW converter — wraps tasks in QlibTwModelExperiment (model_template_tw/)."""

    runner: str = "rdagent.scenarios.qlib.developer.model_runner.QlibTwModelRunner"
    """TW model runner — feeds qrun the TW date window."""

    train_start: str = "2021-01-01"
    train_end: str = "2023-12-31"
    valid_start: str = "2024-01-01"
    valid_end: str = "2024-12-31"
    test_start: str = "2025-01-01"
    # Must be at least 1 trading day INSIDE the qlib calendar — same constraint
    # as FactorTwBasePropSetting (qlib peeks at last_idx+1 in get_step_time).
    test_end: Optional[str] = "2026-05-07"  # rolling — keep <= calendar_end - 1


class FactorFromReportTwPropSetting(FactorTwBasePropSetting):
    """Taiwan-market PDF-driven factor loop. Same TW scenario/runner/dates as
    FactorTwBasePropSetting, but the loop pulls factor specs from PDFs instead
    of LLM-only proposal."""

    model_config = SettingsConfigDict(env_prefix="QLIB_FACTOR_FROM_REPORT_TW_", protected_namespaces=())

    scen: str = "rdagent.scenarios.qlib.experiment.factor_tw_from_report_experiment.QlibTwFactorFromReportScenario"
    """TW from-report scenario class"""

    report_result_json_file_path: str = "git_ignore_folder/report_list.json"
    """Path to a JSON list of report PDFs (used when no report_folder is passed)"""

    max_factors_per_exp: int = 6
    """Maximum number of factors implemented per experiment"""

    report_limit: int = 20
    """Maximum number of reports to process"""


class AlphaTwBasePropSetting(FactorTwBasePropSetting):
    """TW alpha factory loop (docs/tw/ALPHA_FACTORY.md): same proposal and coder as the TW factor
    loop, judged by tw_data/alpha_eval_tw on the anonymized research panel instead of qrun."""

    model_config = SettingsConfigDict(env_prefix="QLIB_ALPHA_TW_", protected_namespaces=())

    scen: str = "rdagent.scenarios.qlib.experiment.alpha_tw_experiment.QlibTwAlphaScenario"
    runner: str = "rdagent.scenarios.qlib.developer.alpha_runner_tw.QlibTwAlphaRunner"
    summarizer: str = "rdagent.scenarios.qlib.developer.alpha_feedback_tw.QlibTwAlphaFeedback"


FACTOR_PROP_SETTING = FactorBasePropSetting()
FACTOR_TW_PROP_SETTING = FactorTwBasePropSetting()
FACTOR_FROM_REPORT_PROP_SETTING = FactorFromReportPropSetting()
FACTOR_FROM_REPORT_TW_PROP_SETTING = FactorFromReportTwPropSetting()
ALPHA_TW_PROP_SETTING = AlphaTwBasePropSetting()
MODEL_PROP_SETTING = ModelBasePropSetting()
MODEL_TW_PROP_SETTING = ModelTwBasePropSetting()
QUANT_PROP_SETTING = QuantBasePropSetting()
