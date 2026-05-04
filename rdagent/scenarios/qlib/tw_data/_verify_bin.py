"""Verify the dumped bin format is readable by Qlib's expression engine."""
import qlib
from qlib.data import D

qlib.init(provider_uri="~/.qlib/qlib_data/tw_data", region="cn", expression_cache=None, dataset_cache=None)

from qlib.contrib.data.loader import Alpha158DL
expr, names = Alpha158DL.get_feature_config()

df = D.features(
    ["2330", "2317", "2454", "2412", "2882"],
    expr[:20] + ["Ref($close,-2)/Ref($close,-1)-1"],
    start_time="2024-12-01",
    end_time="2024-12-31",
)
print(f"shape: {df.shape}")
print(f"non-null cols: {(df.notna().sum() > 0).sum()} / {df.shape[1]}")
print(f"\nLast 3 rows of first 5 cols:\n{df.iloc[-3:, :5]}")
