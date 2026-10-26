#!/bin/bash
# Second, non-Claude A-F review of every holdout-confirmed card that DeepSeek has not reviewed yet
# (docs/tw/FACTOR_REPORT.md §5, factor_review_llm_tw.py). Takes no arguments on purpose: Jans allowed
# exactly this command for Claude sessions in .claude/settings.local.json (2026-10-26), so a session
# can start a review without reading .env. The API key is loaded inside the Python process and never
# printed; the run stops under the $0.50 DeepSeek reserve (DECISIONS D27).
set -euo pipefail
cd /mnt/g/Github/RD-Agent
exec /home/jansa/miniconda3/envs/rdagent/bin/python -m rdagent.scenarios.qlib.tw_data.factor_review_llm_tw --confirmed
