"""
TW alpha factory loop (AI CUP 2026): RD-Agent proposes and codes factors, the fixed judge
(tw_data/alpha_eval_tw) scores them on the anonymized research panel, passing factors go to
data/alpha_tw/library/. Design: docs/tw/ALPHA_FACTORY.md. Costs LLM money (CLAUDE.md rule 5).

Usage (WSL, `conda activate rdagent`, .env loaded; prefer deepseek-flash, deepseek-chat is deprecated, J7):
    python rdagent/app/qlib_rd_loop/alpha_tw.py --loop_n 1                  # canary
    python rdagent/app/qlib_rd_loop/alpha_tw.py --path log/<session>/__session__/<loop>/<step>   # resume
"""

import os
from pathlib import Path

# Absolute data paths before any rdagent setting object is created (CLAUDE.md: WSL 9p cwd pitfall).
_REPO = Path(__file__).resolve().parents[3]
os.environ.setdefault("FACTOR_CoSTEER_DATA_FOLDER", str(_REPO / "data" / "alpha_tw" / "coder"))
os.environ.setdefault("FACTOR_CoSTEER_DATA_FOLDER_DEBUG", str(_REPO / "data" / "alpha_tw" / "coder_debug"))
os.environ.setdefault("FACTOR_CoSTEER_MAX_LOOP", "6")  # code-repair rounds per factor (default 10)
os.environ.setdefault("FACTOR_TW_IDEAS_PATH", str(_REPO / "data" / "alpha_ideas.md"))

import asyncio  # noqa: E402
from typing import Optional  # noqa: E402

import fire  # noqa: E402

from rdagent.app.qlib_rd_loop.conf import ALPHA_TW_PROP_SETTING  # noqa: E402
from rdagent.app.qlib_rd_loop.factor import FactorRDLoop  # noqa: E402


def main(
    path: Optional[str] = None,
    step_n: Optional[int] = None,
    loop_n: Optional[int] = None,
    all_duration: str | None = None,
    checkout: bool = True,
    checkout_path: Optional[str] = None,
):
    for folder in ("FACTOR_CoSTEER_DATA_FOLDER", "FACTOR_CoSTEER_DATA_FOLDER_DEBUG"):
        if not (Path(os.environ[folder]) / "daily_pv.h5").exists():
            raise SystemExit(f"{os.environ[folder]}/daily_pv.h5 missing: run tw_data/dump_alpha_panel_tw first")
    if checkout_path is not None:
        checkout = Path(checkout_path)
    loop = FactorRDLoop(ALPHA_TW_PROP_SETTING) if path is None else FactorRDLoop.load(path, checkout=checkout)
    loop._init_base_features(None)
    asyncio.run(loop.run(step_n=step_n, loop_n=loop_n, all_duration=all_duration))


if __name__ == "__main__":
    fire.Fire(main)
