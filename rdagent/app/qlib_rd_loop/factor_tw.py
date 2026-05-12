"""
Taiwan-market factor R&D loop. Mirrors factor.py — only the prop setting changes.

Usage:
    python rdagent/app/qlib_rd_loop/factor_tw.py --dataset_name tw_data
"""
import asyncio
import os
from pathlib import Path
from typing import Any, Optional

import fire

from rdagent.app.qlib_rd_loop.conf import FACTOR_TW_PROP_SETTING
from rdagent.app.qlib_rd_loop.factor import FactorRDLoop


def main(
    path: Optional[str] = None,
    step_n: Optional[int] = None,
    loop_n: Optional[int] = None,
    all_duration: str | None = None,
    checkout: bool = True,
    checkout_path: Optional[str] = None,
    base_features_path: Optional[str] = None,
    dataset_name: str = "tw_data",
    **kwargs,
):
    os.environ["QLIB_DATASET_NAME"] = dataset_name

    if checkout_path is not None:
        checkout = Path(checkout_path)

    if path is None:
        loop = FactorRDLoop(FACTOR_TW_PROP_SETTING)
    else:
        loop = FactorRDLoop.load(path, checkout=checkout)

    loop._init_base_features(base_features_path)
    if "user_interaction_queues" in kwargs and kwargs["user_interaction_queues"] is not None:
        loop._set_interactor(*kwargs["user_interaction_queues"])
        loop._interact_init_params()
    asyncio.run(loop.run(step_n=step_n, loop_n=loop_n, all_duration=all_duration))


if __name__ == "__main__":
    fire.Fire(main)
