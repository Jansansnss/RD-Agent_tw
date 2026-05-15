"""
Taiwan-market PDF-driven factor R&D loop.

Mirrors `factor_from_report.py` (CN), but:
  - Uses FACTOR_FROM_REPORT_TW_PROP_SETTING (TW dates, TW runner, TW scenario).
  - Rebuilds the experiment returned by the PDF loader into a
    QlibTwFactorExperiment so the workspace is materialized from
    factor_template_tw/ (provider_uri tw_data, market all) — the loader itself
    is region-agnostic but hardcodes QlibFactorExperiment (CN) as its return
    type, so we re-wrap here.

Usage:
    # ingest every PDF under data/papers/
    python rdagent/app/qlib_rd_loop/factor_tw_from_report.py --report_folder data/papers

    # or feed a JSON list (path set via report_result_json_file_path)
    python rdagent/app/qlib_rd_loop/factor_tw_from_report.py
"""
import asyncio
import json
import os
from pathlib import Path
from typing import Any, Optional

import fire

from rdagent.app.qlib_rd_loop.conf import FACTOR_FROM_REPORT_TW_PROP_SETTING
from rdagent.app.qlib_rd_loop.factor import FactorRDLoop
from rdagent.components.document_reader.document_reader import (
    extract_first_page_screenshot_from_pdf,
    load_and_process_pdfs_by_langchain,
)
from rdagent.core.conf import RD_AGENT_SETTINGS
from rdagent.log import rdagent_logger as logger
from rdagent.oai.llm_utils import APIBackend
from rdagent.scenarios.qlib.experiment.factor_tw_experiment import QlibTwFactorExperiment
from rdagent.scenarios.qlib.factor_experiment_loader.pdf_loader import (
    FactorExperimentLoaderFromPDFfiles,
)
from rdagent.utils.agent.tpl import T
from rdagent.utils.workflow import LoopMeta


def _generate_hypothesis(factor_result: dict, report_content: str):
    """Re-uses the CN prompt templates — they are region-agnostic and just
    ask the LLM to turn factor specs from a paper into an RD-Agent hypothesis."""
    from rdagent.core.proposal import Hypothesis
    from typing import Dict

    system_prompt = T(".prompts:hypothesis_generation.system").r()
    user_prompt = T(".prompts:hypothesis_generation.user").r(
        factor_descriptions=json.dumps(factor_result), report_content=report_content
    )

    response = APIBackend().build_messages_and_create_chat_completion(
        user_prompt=user_prompt,
        system_prompt=system_prompt,
        json_mode=True,
        json_target_type=Dict[str, str],
    )
    response_json = json.loads(response)
    return Hypothesis(
        hypothesis=response_json.get("hypothesis", "No hypothesis provided"),
        reason=response_json.get("reason", "No reason provided"),
        concise_reason=response_json.get("concise_reason", "No concise reason provided"),
        concise_observation=response_json.get("concise_observation", "No concise observation provided"),
        concise_justification=response_json.get("concise_justification", "No concise justification provided"),
        concise_knowledge=response_json.get("concise_knowledge", "No concise knowledge provided"),
    )


def _extract_tw_experiment_from_report(report_file_path: str) -> Optional[QlibTwFactorExperiment]:
    """Load a PDF, extract factor specs, generate a hypothesis, and wrap
    everything in a QlibTwFactorExperiment (so the workspace template is
    factor_template_tw/, not the CN one the loader defaults to)."""
    exp = FactorExperimentLoaderFromPDFfiles().load(report_file_path)
    if exp is None or not exp.sub_tasks:
        return None

    pdf_screenshot = extract_first_page_screenshot_from_pdf(report_file_path)
    logger.log_object(pdf_screenshot, tag="load_pdf_screenshot")

    docs_dict = load_and_process_pdfs_by_langchain(report_file_path)
    factor_result = {
        task.factor_name: {
            "description": task.factor_description,
            "formulation": task.factor_formulation,
            "variables": task.variables,
            "resources": task.factor_resources,
        }
        for task in exp.sub_tasks
    }
    report_content = "\n".join(docs_dict.values())
    hypothesis = _generate_hypothesis(factor_result, report_content)

    # Re-wrap as TW experiment carrying the same sub_tasks.
    tw_exp = QlibTwFactorExperiment(sub_tasks=exp.sub_tasks, hypothesis=hypothesis)
    # Preserve sub_workspace_list if the loader populated it
    if hasattr(exp, "sub_workspace_list") and exp.sub_workspace_list:
        tw_exp.sub_workspace_list = exp.sub_workspace_list
    return tw_exp


class FactorTwReportLoop(FactorRDLoop, metaclass=LoopMeta):
    def __init__(self, report_folder: Optional[str] = None):
        super().__init__(PROP_SETTING=FACTOR_FROM_REPORT_TW_PROP_SETTING)
        if report_folder is None:
            json_path = FACTOR_FROM_REPORT_TW_PROP_SETTING.report_result_json_file_path
            if not Path(json_path).exists():
                raise FileNotFoundError(
                    f"No report_folder supplied and {json_path} does not exist. "
                    f"Pass --report_folder=<path-with-pdfs> or create the JSON list."
                )
            self.judge_pdf_data_items = json.load(open(json_path, "r"))
        else:
            self.judge_pdf_data_items = sorted(Path(report_folder).rglob("*.pdf"))
            if not self.judge_pdf_data_items:
                raise FileNotFoundError(f"No PDFs found under {report_folder}")
            logger.info(f"Found {len(self.judge_pdf_data_items)} PDF(s) under {report_folder}")

        self.loop_n = min(len(self.judge_pdf_data_items), FACTOR_FROM_REPORT_TW_PROP_SETTING.report_limit)
        self.shift_report = 0

    async def direct_exp_gen(self, prev_out: dict[str, Any]):
        while True:
            if self.get_unfinished_loop_cnt(self.loop_idx) < RD_AGENT_SETTINGS.get_max_parallel():
                report_file_path = self.judge_pdf_data_items[self.loop_idx + self.shift_report]
                logger.info(f"Processing report {self.loop_idx}: {report_file_path}")
                exp = _extract_tw_experiment_from_report(str(report_file_path))
                if exp is None:
                    self.shift_report += 1
                    self.loop_n -= 1
                    if self.loop_n < 0:
                        raise self.LoopTerminationError("Reach stop criterion and stop loop")
                    continue
                exp.based_experiments = [QlibTwFactorExperiment(sub_tasks=[], hypothesis=exp.hypothesis)] + [
                    t[0] for t in self.trace.hist if t[1]
                ]
                exp.sub_workspace_list = exp.sub_workspace_list[: FACTOR_FROM_REPORT_TW_PROP_SETTING.max_factors_per_exp]
                exp.sub_tasks = exp.sub_tasks[: FACTOR_FROM_REPORT_TW_PROP_SETTING.max_factors_per_exp]
                exp.base_features = self.plan["features"]
                if exp.based_experiments:
                    exp.based_experiments[-1].base_features = self.plan["features"]
                logger.log_object(exp.hypothesis, tag="hypothesis generation")
                logger.log_object(exp.sub_tasks, tag="experiment generation")
                return exp
            await asyncio.sleep(1)

    def coding(self, prev_out: dict[str, Any]):
        exp = self.coder.develop(prev_out["direct_exp_gen"])
        logger.log_object(exp.sub_workspace_list, tag="coder result")
        return exp


def main(
    report_folder: Optional[str] = None,
    path: Optional[str] = None,
    all_duration: Optional[str] = None,
    checkout: bool = True,
    dataset_name: str = "tw_data",
):
    """Auto R&D evolving loop for TW factors, driven by factor PDFs.

    Args:
        report_folder: directory containing factor-research PDFs (recursive glob).
        path: resume a previous session by path.
        all_duration: optional wall-clock budget (e.g. "1h").
        checkout: forwarded to FactorRDLoop.load when resuming.
        dataset_name: qlib provider_uri name (default "tw_data").
    """
    os.environ["QLIB_DATASET_NAME"] = dataset_name

    if path is None and report_folder is None:
        loop = FactorTwReportLoop()
    elif path is not None:
        loop = FactorTwReportLoop.load(path, checkout=checkout)
    else:
        loop = FactorTwReportLoop(report_folder=report_folder)

    asyncio.run(loop.run(all_duration=all_duration))


if __name__ == "__main__":
    fire.Fire(main)
