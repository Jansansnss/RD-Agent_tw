"""Quick inspection of the smoke-test HDF5. Used for manual verification."""
import sys
from pathlib import Path

import pandas as pd

try:
    from rdagent.scenarios.qlib.tw_data._paths import DATA_DIR
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _paths import DATA_DIR

pd.set_option("display.max_columns", 12)
pd.set_option("display.width", 200)
pd.set_option("display.float_format", lambda x: f"{x:,.2f}")

path = Path(sys.argv[1] if len(sys.argv) > 1 else str(DATA_DIR / "tw_daily_smoke.h5"))
df = pd.read_hdf(path)

for code in df.index.get_level_values("instrument").unique()[:1]:
    sub = df.xs(code, level="instrument")
    print(f"=== {code} — tick grid check ===")
    print(sub[["$open", "$high", "$low", "$close", "$volume"]].to_string())
    print()
    print(f"=== {code} — institutional + margin ===")
    cols = ["foreign_net", "trust_net", "dealer_net", "margin_balance", "short_balance"]
    print(sub[[c for c in cols if c in sub.columns]].to_string())
    print()
    print(f"=== {code} — valuation + holdings ===")
    cols = ["div_yield", "pe_ratio", "pb_ratio", "foreign_held_pct"]
    print(sub[[c for c in cols if c in sub.columns]].to_string())
