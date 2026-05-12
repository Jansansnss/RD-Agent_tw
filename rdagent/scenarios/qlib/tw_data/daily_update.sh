#!/bin/bash
# Daily TW data update — pulls the latest day from FinMind, cleans, and
# refreshes the qlib bin + factor-coder H5.
#
# When to run:
#   TW market closes at 13:30 TPE; FinMind refreshes same-day data by ~18:00
#   TPE. Run this any time after that. Safe defaults: ~22:00 TPE / ~10:00 ET
#   / ~14:00 UTC.
#
# Usage (foreground):
#   bash rdagent/scenarios/qlib/tw_data/daily_update.sh
#
# Usage (detached tmux so disconnects don't kill it):
#   tmux new -s tw_daily -d "bash /mnt/g/Github/RD-Agent/rdagent/scenarios/qlib/tw_data/daily_update.sh"
#   tmux attach -t tw_daily        # to check progress
#
# Cron (post-market US-east, 6am ET):
#   0 6 * * 1-5 cd /mnt/g/Github/RD-Agent && bash rdagent/scenarios/qlib/tw_data/daily_update.sh
#
# Expected runtime: 15-40 min total
#   - collect       : 2-10 min (one new day per by-date dataset; cached otherwise)
#   - clean         : 5-10 min (full rebuild of cleaned dir)
#   - dump_to_bin   : 10-25 min (full rebuild of all bin files)
#   - dump_factor_h5: 2-5 min (full H5 rewrite)
#
# Exits on first failure (set -e). Re-running is safe — each step is
# idempotent and the collector's per-day cache prevents re-downloads.

set -euo pipefail

REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Conda activate (assumes miniconda3 at ~; adjust if anaconda3)
if [ -f "$HOME/miniconda3/etc/profile.d/conda.sh" ]; then
    source "$HOME/miniconda3/etc/profile.d/conda.sh"
elif [ -f "$HOME/anaconda3/etc/profile.d/conda.sh" ]; then
    source "$HOME/anaconda3/etc/profile.d/conda.sh"
else
    echo "ERROR: cannot find conda init script in ~/miniconda3 or ~/anaconda3" >&2
    exit 1
fi
conda activate rdagent

START="${START:-2010-01-01}"
END="${END:-$(date +%Y-%m-%d)}"  # override with `END=2026-05-11 bash daily_update.sh`
LOG_DIR="$REPO_ROOT/data"
LOG="$LOG_DIR/_daily_update_$(date +%Y%m%d).log"
mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG") 2>&1

echo "================================================================"
echo "TW data daily update — $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "  repo:  $REPO_ROOT"
echo "  range: $START → $END"
echo "  log:   $LOG"
echo "================================================================"

step() {
    echo
    echo "----------------------------------------------------------------"
    echo "[$(date '+%H:%M:%S')] $1"
    echo "----------------------------------------------------------------"
}

step "1/4  Collect latest day from FinMind (cached → only today fetches)"
python -u -m rdagent.scenarios.qlib.tw_data.collect_finmind \
    --start "$START" --end "$END" \
    --skip_market_wide --skip_macro --skip_static --skip_derivatives
# Notes on skip flags above:
#   --skip_market_wide/--skip_macro: weekly-update cadence is enough for these;
#     no point hammering daily.
#   --skip_static: stock_info / trader_info / us_stock_info / futopt_info change
#     monthly; skipped here to save quota.
#   --skip_derivatives: futures / option daily; uncomment-remove if you trade them.
# Drop these flags if you want everything updated daily.

step "2/4  Clean (TWSE-rule universe filter + per-file fixes)"
python -u -m rdagent.scenarios.qlib.tw_data.clean_csvs

step "3/4  Dump cleaned CSVs → qlib bin format"
python -u -m rdagent.scenarios.qlib.tw_data.dump_to_bin \
    --input_dir "$REPO_ROOT/data/tw_finmind_full_clean" \
    --out_dir "$HOME/.qlib/qlib_data/tw_data"

step "4/4  Materialize factor-coder daily_pv.h5 from qlib bin"
python -u -m rdagent.scenarios.qlib.tw_data.dump_factor_h5

echo
echo "================================================================"
echo "Done — $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "  qlib bin: $HOME/.qlib/qlib_data/tw_data"
echo "  factor H5: git_ignore_folder/factor_implementation_source_data/daily_pv.h5"
echo "================================================================"
