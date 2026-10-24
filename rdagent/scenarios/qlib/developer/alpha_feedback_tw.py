"""
Feedback for the TW alpha factory loop: the judge has already decided; the LLM only explains.

decision = at least one factor of the experiment passed the code gate (alpha_runner_tw). Passing
factors are written to the library as alpha cards (code + research metrics + values), which the
next rounds see as "already accepted" for the correlation check, and which the holdout script and
the export to the contest repo read. The LLM gets research-period numbers only.
"""

import datetime as dt
import json
import re
import shutil
from typing import Dict

from rdagent.core.proposal import Experiment2Feedback, HypothesisFeedback, Trace
from rdagent.log import rdagent_logger as logger
from rdagent.oai.llm_utils import APIBackend
from rdagent.scenarios.qlib.developer.alpha_runner_tw import LIBRARY_DIR

SYSTEM = """\
You review one round of a factor-research loop for a short stock-picking contest. A fixed program
has already scored the factors and decided which pass; you cannot change that. Explain the result
and propose the next hypothesis. Use only the numbers given. Answer in JSON with the keys
"Observations", "Feedback for Hypothesis", "New Hypothesis", "Reasoning".

{scenario}"""

USER = """\
Hypothesis of this round:
{hypothesis}

Factors and the judge's verdict (research period only; t = Newey-West t-statistic; horizons in days):
{table}

Factors already accepted in the library: {library}

Write the four fields. "New Hypothesis" must be one concrete, testable idea that is different from
the factors above; say which signal family it belongs to and why it could rank the top ~20 stocks
better over 5 to 20 days after costs."""


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]+", "_", name).strip("_")[:60] or "alpha"


def save_card(exp, row: dict, hypothesis: str) -> str:
    """Copy a passing factor into the library; returns its folder name."""
    LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
    name, n = _slug(row["factor"]), 1
    while (LIBRARY_DIR / name).exists():
        n += 1
        name = f"{_slug(row['factor'])}_{n}"
    folder = LIBRARY_DIR / name
    folder.mkdir()
    task, ws = next(((t, w) for t, w in zip(exp.sub_tasks, exp.sub_workspace_list) if t.factor_name == row["factor"]),
                    (None, None))
    if ws is not None:
        (folder / "factor.py").write_text(ws.file_dict.get("factor.py", ""), encoding="utf-8")
    shutil.copy(row["values_path"], folder / "values.parquet")
    card = {
        "name": name,
        "factor": row["factor"],
        "hypothesis": hypothesis,
        "description": getattr(task, "factor_description", None),
        "formulation": getattr(task, "factor_formulation", None),
        "variables": getattr(task, "variables", None),
        "gate_horizon_days": row["horizon"],
        "research_metrics": row["metrics"],
        "accepted_at": dt.datetime.now().isoformat(timespec="seconds"),
        "pod": None,  # set at export: one of the five declared factor families (theme rule)
    }
    (folder / "card.json").write_text(json.dumps(card, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return name


class QlibTwAlphaFeedback(Experiment2Feedback):
    def generate_feedback(self, exp, trace: Trace) -> HypothesisFeedback:
        rows = (exp.result or {}).get("factors", [])
        hypothesis = exp.hypothesis.hypothesis if exp.hypothesis else ""
        library_before = sorted(p.name for p in LIBRARY_DIR.glob("*") if p.is_dir())
        accepted = [save_card(exp, r, hypothesis) for r in rows if r["passed"]]
        table = "\n".join(
            f"- {r['factor']}: {'PASS at ' + str(r['horizon']) + 'd' if r['passed'] else 'fail'}; "
            f"metrics={json.dumps(r['metrics'])}; reasons={r['reasons'][:6]}"
            for r in rows
        )
        response = APIBackend().build_messages_and_create_chat_completion(
            user_prompt=USER.format(hypothesis=hypothesis, table=table, library=library_before or "none"),
            system_prompt=SYSTEM.format(scenario=self.scen.get_scenario_all_desc()),
            json_mode=True,
            json_target_type=Dict[str, str],
        )
        answer = json.loads(response)
        if accepted:
            logger.info(f"alpha library: accepted {accepted}")
        return HypothesisFeedback(
            observations=answer.get("Observations", ""),
            hypothesis_evaluation=answer.get("Feedback for Hypothesis", ""),
            new_hypothesis=answer.get("New Hypothesis", ""),
            reason=(answer.get("Reasoning", "") + (f" Accepted into the library: {accepted}." if accepted else "")),
            decision=bool(accepted),
        )
