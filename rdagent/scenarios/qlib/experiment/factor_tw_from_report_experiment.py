from copy import deepcopy

from rdagent.scenarios.qlib.experiment.factor_tw_experiment import QlibTwFactorScenario
from rdagent.utils.agent.tpl import T


class QlibTwFactorFromReportScenario(QlibTwFactorScenario):
    """Taiwan factor-from-report scenario. Inherits the TW source-data
    description and output-format example from QlibTwFactorScenario, but
    swaps the rich-style description so the dashboard/log shows we're in
    paper-driven mode."""

    def __init__(self) -> None:
        super().__init__()
        self._rich_style_description = deepcopy(
            T(".prompts:qlib_factor_from_report_rich_style_description").r()
        )

    @property
    def rich_style_description(self) -> str:
        return self._rich_style_description
