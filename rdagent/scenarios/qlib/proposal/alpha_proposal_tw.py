"""
Proposal side of the TW alpha factory loop: the TW factor loop's hypothesis and experiment
generators, with the trace history written from the alpha judge's verdicts.

Upstream's history templates print `exp.result.loc[...]` from a qrun metrics frame. The alpha
runner's result is a dict of judge rows (research window only), so both generators let the parent
build everything else from an empty trace and then put alpha_history() in its place.
"""

from typing import Tuple

from rdagent.core.proposal import Hypothesis, Trace
from rdagent.scenarios.qlib.developer.alpha_runner_tw import judge_line
from rdagent.scenarios.qlib.proposal.factor_proposal import (
    QlibTwFactorHypothesis2Experiment,
    QlibTwFactorHypothesisGen,
)

FIRST_ROUND = "No previous hypothesis and feedback available since it's the first round."
RECENT_TRIALS = 10  # in full; older trials shrink to one line per factor, so long campaigns stay cheap


def _trial(i: int, exp, feedback, last: bool = False) -> str:
    rows = (exp.result or {}).get("factors", []) if isinstance(exp.result, dict) else []
    lines = [
        f"# Trial {i}:",
        "## Hypothesis",
        str(exp.hypothesis),
        "## Specific task:",
        *(t.get_task_brief_information() for t in exp.sub_tasks if t is not None),
        "## Judge verdict (research period only; t = Newey-West t-statistic):",
        *([judge_line(r) for r in rows] or ["no factor produced values (coding or execution failed)"]),
        f"Observation: {feedback.observations}",
        f"Hypothesis Evaluation: {feedback.hypothesis_evaluation}",
        f"Decision (whether any factor was accepted into the library): {feedback.decision}",
    ]
    if last:
        lines += [
            f"New Hypothesis (given in feedback, for reference; accept or reject it): {feedback.new_hypothesis}",
            f"Reasoning (justification for the new hypothesis): {feedback.reason}",
        ]
    return "\n".join(lines)


def _brief(i: int, exp) -> list[str]:
    rows = (exp.result or {}).get("factors", []) if isinstance(exp.result, dict) else []
    if not rows:
        return [f"- trial {i}: no factor produced values"]
    out = []
    for r in rows:
        m = r.get("metrics") or {}
        why = "PASS" if r["passed"] else (r["reasons"][0] if r["reasons"] else "fail")
        nums = f"t5 {m['ic_t']['5']:+.1f}, max library corr {m['max_library_corr']:.2f}; " if m else ""
        out.append(f"- trial {i} {r['factor']}: {nums}{why}")
    return out


def alpha_history(trace: Trace) -> str:
    if not trace.hist:
        return FIRST_ROUND
    cut = max(0, len(trace.hist) - RECENT_TRIALS)
    parts = []
    if cut:
        lines = [f"# Trials 1-{cut} in brief (research period; one line per factor):"]
        for i, (exp, _) in enumerate(trace.hist[:cut]):
            lines += _brief(i + 1, exp)
        parts.append("\n".join(lines))
    parts += [_trial(i + 1, exp, fb) for i, (exp, fb) in enumerate(trace.hist) if i >= cut]
    sep = "\n=========================================================\n"
    return sep.join(parts)


def last_trial(trace: Trace) -> str:
    if not trace.hist:
        return FIRST_ROUND
    exp, fb = trace.hist[-1]
    return _trial(len(trace.hist), exp, fb, last=True)


class QlibTwAlphaHypothesisGen(QlibTwFactorHypothesisGen):
    # The parent's "RAG" hint then stays at "easiest and fastest factors first": the judge wants
    # plain formulas, not the machine-learning factors upstream asks for after 15 rounds.
    def prepare_context(self, trace: Trace) -> Tuple[dict, bool]:
        context, ok = super().prepare_context(Trace(trace.scen))
        context["hypothesis_and_feedback"] = alpha_history(trace)
        context["last_hypothesis_and_feedback"] = last_trial(trace)
        return context, ok


class QlibTwAlphaHypothesis2Experiment(QlibTwFactorHypothesis2Experiment):
    def prepare_context(self, hypothesis: Hypothesis, trace: Trace) -> Tuple[dict, bool]:
        context, ok = super().prepare_context(hypothesis, Trace(trace.scen))
        context["hypothesis_and_feedback"] = alpha_history(trace)
        return context, ok
