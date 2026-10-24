"""
Runner for the TW alpha factory loop: score coded factors with the fast judge, no qrun/Docker.

For each factor the coder produced: reject code that hard-codes an instrument ID, stock code or
date (DECISIONS D16); otherwise compute its values on the anonymized research panel (the coder's
own data folder) and score them with alpha_eval_tw on the research window, against the factors
already in the library. exp.result is a small dict (metrics and the gate verdict per factor), so
nothing from the holdout period can reach the LLM.
"""

import re
from pathlib import Path

import pandas as pd

from rdagent.components.coder.factor_coder.config import FACTOR_COSTEER_SETTINGS
from rdagent.core.exception import FactorEmptyError
from rdagent.log import rdagent_logger as logger
from rdagent.scenarios.qlib.developer.factor_runner import QlibTwFactorRunner
from rdagent.scenarios.qlib.developer.utils import process_factor_data
from rdagent.scenarios.qlib.tw_data import alpha_eval_tw as ev
from rdagent.scenarios.qlib.tw_data._paths import DATA_DIR

LIBRARY_DIR = DATA_DIR / "alpha_tw" / "library"
GATE_HORIZONS = (5, 20)  # an alpha may qualify at either holding period
FORBIDDEN = [
    (re.compile(r"""['"]S\d{3}['"]"""), "an instrument ID"),
    (re.compile(r"""['"]\d{4}['"]"""), "a stock code"),
    (re.compile(r"(?<!\d)(19|20)\d{2}-?\d{2}-?\d{2}(?!\d)"), "a calendar date"),
]

_panel: pd.DataFrame | None = None


def judge_panel() -> pd.DataFrame:
    global _panel
    if _panel is None:
        _panel = pd.read_hdf(Path(FACTOR_COSTEER_SETTINGS.data_folder) / "daily_pv.h5", key="data")
    return _panel


def library() -> dict[str, pd.Series]:
    out = {}
    for path in sorted(LIBRARY_DIR.glob("*/values.parquet")):
        out[path.parent.name] = pd.read_parquet(path).iloc[:, 0]
    return out


def literal_problems(code: str) -> list[str]:
    return [what for pattern, what in FORBIDDEN if pattern.search(code or "")]


def gate_any(metrics: dict) -> tuple[bool, list[str], int | None]:
    """Pass at the first horizon in GATE_HORIZONS that clears every threshold."""
    reasons = []
    for h in GATE_HORIZONS:
        passed, why = ev.gate(metrics, {**ev.DEFAULT_GATE, "horizon": h})
        if passed:
            return True, [], h
        reasons += [f"{h}d: {w}" for w in why]
    return False, reasons, None


def summary(metrics: dict) -> dict:
    """The few numbers the feedback LLM sees."""
    ic = metrics["ic"]
    return {
        "ic": {h: round(ic[h]["mean"], 4) for h in ic},
        "ic_t": {h: round(ic[h]["t_nw"], 2) for h in ic},
        "ic_by_year_5d": {y: round(v["mean"], 4) for y, v in ic["5"]["by_year"].items()},
        "book_ann_excess_net": round(metrics["book"]["ann_excess_net"], 4),
        "book_turnover_per_rebalance": round(metrics["book"]["turnover_per_rebalance"], 3),
        "book_beta": round(metrics["book"]["beta_vs_b0"], 2),
        "windows_24d": {k: round(v, 4) for k, v in metrics["windows_24d"].items()},
        "top10_24d": {k: round(v, 4) for k, v in metrics["top10_24d"].items()},
        "max_library_corr": round(max((abs(v) for v in metrics["library_corr"].values()), default=0.0), 3),
        "coverage": round(metrics["coverage"], 3),
    }


def judge_line(row: dict) -> str:
    """One factor's verdict in a line, for the trace history the next rounds read."""
    verdict = f"PASS at {row['horizon']}d" if row["passed"] else "fail"
    m = row.get("metrics")
    if not m:
        return f"- {row['factor']}: {verdict}; " + "; ".join(row["reasons"][:3])
    hs = list(m["ic"])
    ic = "/".join(f"{m['ic'][h]:+.3f}" for h in hs)
    t = "/".join(f"{m['ic_t'][h]:+.1f}" for h in hs)
    line = (f"- {row['factor']}: {verdict}; Rank IC at {'/'.join(hs)} days {ic} (t {t}); "
            f"24-day median excess {m['windows_24d']['median']:+.2%}; book beta {m['book_beta']:.2f}; "
            f"max library corr {m['max_library_corr']:.2f}")
    return line + (f"; why it failed: {'; '.join(row['reasons'][:4])}" if row["reasons"] else "")


class QlibTwAlphaRunner(QlibTwFactorRunner):
    def develop(self, exp):
        rejected = {}
        for task, ws in zip(exp.sub_tasks, exp.sub_workspace_list):
            code = (ws.file_dict or {}).get("factor.py", "") if ws is not None else ""
            bad = literal_problems(code)
            if bad:
                rejected[task.factor_name] = bad
        values = process_factor_data(exp)
        if values.empty:
            raise FactorEmptyError("No factor produced values on the research data.")
        panel, lib = judge_panel(), library()
        rows = []
        for col in values.columns:
            row = {"factor": str(col), "passed": False, "horizon": None, "reasons": [], "metrics": None}
            if col in rejected:
                row["reasons"] = [f"the code contains {what}; rewrite it without literals" for what in rejected[col]]
            else:
                series = values[col].astype(float).dropna()
                m = ev.evaluate(series, panel, window="research", library=lib)
                row["passed"], row["reasons"], row["horizon"] = gate_any(m)
                row["metrics"] = summary(m)
                path = Path(exp.experiment_workspace.workspace_path) / f"alpha_{col}.parquet"
                path.parent.mkdir(parents=True, exist_ok=True)
                series.to_frame(str(col)).to_parquet(path)
                row["values_path"] = str(path)
            logger.info(f"alpha judge: {col} -> {'PASS' if row['passed'] else 'fail'} {row['reasons'][:3]}")
            rows.append(row)
        exp.result = {"factors": rows, "library_size": len(lib)}
        return exp
