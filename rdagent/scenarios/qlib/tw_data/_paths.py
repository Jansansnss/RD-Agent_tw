"""Single source of truth for TW data paths.

All scripts in tw_data/ import DATA_DIR from here so the location of raw
collector outputs, intermediate H5s, and other artifacts can be moved by
editing one line. Module path layout (4 levels up from this file to the
repo root): tw_data -> qlib -> scenarios -> rdagent -> repo root.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]
DATA_DIR = REPO_ROOT / "data"
