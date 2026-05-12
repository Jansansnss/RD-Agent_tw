#!/bin/bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate rdagent
REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
DATA_DIR="$REPO_ROOT/data"
mkdir -p "$DATA_DIR"
cd "$REPO_ROOT/rdagent/scenarios/qlib/tw_data"
python -u collect.py --start 2015-01-01 --end 2026-05-05 \
    --output_dir "$DATA_DIR/tw_raw_full" > "$DATA_DIR/tw_raw_full.log" 2>&1
