"""
Holdout check for the alpha library (TODO G4, DECISIONS D16). Never part of the loop: its numbers
must not reach an LLM prompt.

Each library card's factor.py runs on the anonymized full panel (data/alpha_tw/coder_full, all
dates), then alpha_eval_tw scores the holdout window (2026-05-01 on) with the confirmation gate.

    python -m rdagent.scenarios.qlib.tw_data.alpha_holdout_tw
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

try:
    from rdagent.scenarios.qlib.tw_data import alpha_eval_tw as ev
    from rdagent.scenarios.qlib.tw_data._paths import DATA_DIR
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import alpha_eval_tw as ev
    from _paths import DATA_DIR

ALPHA_DIR = DATA_DIR / "alpha_tw"


def run_factor(code: Path, data: Path) -> pd.Series:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        shutil.copy(code, work / "factor.py")
        (work / "daily_pv.h5").symlink_to(data)
        subprocess.run([sys.executable, "factor.py"], cwd=work, check=True, capture_output=True, timeout=3600)
        return pd.read_hdf(work / "result.h5", key="data").iloc[:, 0].astype(float)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--library", default=str(ALPHA_DIR / "library"))
    args = parser.parse_args()
    data = ALPHA_DIR / "coder_full" / "daily_pv.h5"
    panel = pd.read_hdf(data, key="data")
    rows = []
    print("HOLDOUT window: never paste these numbers into an LLM prompt.")
    for card_path in sorted(Path(args.library).glob("*/card.json")):
        card = json.loads(card_path.read_text(encoding="utf-8"))
        h = card.get("gate_horizon_days") or 5
        try:
            values = run_factor(card_path.parent / "factor.py", data)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as err:
            rows.append({"name": card["name"], "passed": False, "reasons": [f"factor.py failed: {err}"]})
            continue
        m = ev.evaluate(values, panel, window="holdout")
        passed, reasons = ev.gate(m, {**ev.HOLDOUT_GATE, "horizon": h})
        rows.append({"name": card["name"], "horizon": h, "passed": passed, "reasons": reasons,
                     "ic": m["ic"][str(h)]["mean"], "t": m["ic"][str(h)]["t_nw"],
                     "window_median": m["windows_24d"]["median"], "beta": m["book"]["beta_vs_b0"]})
        print(f"{card['name']:<40} {h:>2}d IC {rows[-1]['ic']:+.3f} (t {rows[-1]['t']:+.1f}) "
              f"24d median {rows[-1]['window_median']:+.2%}  {'PASS' if passed else 'fail ' + '; '.join(reasons)}")
    (ALPHA_DIR / "holdout_report.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
