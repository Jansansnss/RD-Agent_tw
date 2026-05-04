#!/bin/bash
source ~/miniconda3/etc/profile.d/conda.sh
conda activate rdagent
cd /mnt/g/Github/RD-Agent/rdagent/scenarios/qlib/tw_data
python -u collect.py --start 2015-01-01 --end 2026-05-05 --output_dir tw_raw_full > tw_raw_full.log 2>&1
