"""
Materialize the factor-coder source-data H5 from a TW qlib bin dataset.

The factor coder symlinks every file in FACTOR_COSTEER_SETTINGS.data_folder
into the LLM workspace and runs the LLM-generated factor.py there. CN factors
read daily_pv.h5 with pandas and never touch qlib's data API. TW factors must
do the same, otherwise the LLM falls back to D.features() which needs
qlib.init() — and CoSTEER's factor_execution_template.txt does not call init.

Canonical TW data window: 2021-01-01 -> today (rolling end). The full dump
pulls whatever the bin actually has; the debug dump trims to a shorter recent
window for fast iteration.

Outputs:
  <data_folder>/daily_pv.h5         full window, all instruments in the bin
  <data_folder_debug>/daily_pv.h5   recent window, all instruments (TW
                                    universe is already small, only dates trim)

Run from the conda env that has qlib installed (no Docker required):

    python -m rdagent.scenarios.qlib.tw_data.dump_factor_h5
    python -m rdagent.scenarios.qlib.tw_data.dump_factor_h5 --dataset_name tw_data
"""

import argparse
from pathlib import Path

from rdagent.components.coder.factor_coder.config import FACTOR_COSTEER_SETTINGS

FIELDS = [
    # Price / volume (Alpha158 base)
    "$open", "$close", "$high", "$low", "$volume", "$factor",
    "$vwap", "$offset",
    # Valuation
    "$div_yield", "$pe_ratio", "$pb_ratio",
    # Institutional net flows (三大法人 — TWSE T86)
    "$foreign_net", "$trust_net", "$dealer_net",
    "$dealer_self_net", "$dealer_hedge_net",
    "$inst_total_net", "$total_inst_net",
    # Margin / 融資
    "$margin_buy", "$margin_sell", "$margin_redeem",
    "$margin_balance", "$margin_prev_balance", "$margin_next_limit",
    # Short / 融券
    "$short_buy", "$short_sell", "$short_redeem",
    "$short_balance", "$short_prev_balance", "$short_next_limit",
    "$short_margin_ratio",
    # Foreign-holdings ratios
    "$issued_shares",
    "$foreign_remaining", "$foreign_held",
    "$foreign_remaining_pct", "$foreign_held_pct", "$foreign_limit_pct",
]


def dump(
    dataset_name: str = "tw_data",
    provider_uri: str | None = None,
    full_start: str = "2021-01-01",
    debug_start: str = "2024-01-01",
) -> None:
    import qlib
    from qlib.data import D

    uri = provider_uri or f"~/.qlib/qlib_data/{dataset_name}"
    qlib.init(provider_uri=uri, region="cn")

    instruments = D.instruments(market="all")

    full = (
        D.features(instruments, FIELDS, start_time=full_start, freq="day")
        .swaplevel()
        .sort_index()
    )
    if full.empty:
        raise RuntimeError(
            f"No rows returned from qlib for dataset '{dataset_name}' at {uri} "
            f"(start_time={full_start}). Check the bin dump succeeded and the "
            "calendar covers this window."
        )

    full_path = Path(FACTOR_COSTEER_SETTINGS.data_folder) / "daily_pv.h5"
    full_path.parent.mkdir(parents=True, exist_ok=True)
    full.to_hdf(full_path, key="data", mode="w")
    print(f"[full]  {full.shape[0]:>8} rows x {full.shape[1]} cols -> {full_path}")

    debug = (
        D.features(instruments, FIELDS, start_time=debug_start, freq="day")
        .swaplevel()
        .sort_index()
    )
    if debug.empty:
        debug = full.tail(min(len(full), 5000))
        print(
            f"[debug] window from {debug_start} empty; "
            f"falling back to last {len(debug)} rows of full data"
        )

    debug_path = Path(FACTOR_COSTEER_SETTINGS.data_folder_debug) / "daily_pv.h5"
    debug_path.parent.mkdir(parents=True, exist_ok=True)
    debug.to_hdf(debug_path, key="data", mode="w")
    print(f"[debug] {debug.shape[0]:>8} rows x {debug.shape[1]} cols -> {debug_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dataset_name", default="tw_data")
    parser.add_argument(
        "--provider_uri",
        default=None,
        help="Override the qlib bin path (default ~/.qlib/qlib_data/<dataset_name>)",
    )
    parser.add_argument(
        "--full_start",
        default="2021-01-01",
        help="Start date for the full dump (canonical TW window starts 2021-01-01)",
    )
    parser.add_argument(
        "--debug_start",
        default="2024-01-01",
        help="Start date for the debug dump (recent window for fast iteration)",
    )
    args = parser.parse_args()
    dump(
        dataset_name=args.dataset_name,
        provider_uri=args.provider_uri,
        full_start=args.full_start,
        debug_start=args.debug_start,
    )


if __name__ == "__main__":
    main()
