"""
Plain-Qlib smoke test for tw_daily_mini.h5.

Bypasses RD-Agent. Verifies:
  - StaticDataLoader can ingest our HDF5
  - DataHandlerLP processes (zscore, fillna) work on Taiwan data
  - LGBModel trains end-to-end and produces IC / RankIC

Usage:
    python _smoke_qlib.py [path_to_h5]
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import qlib
from qlib.contrib.data.handler import DataHandlerLP
from qlib.contrib.model.gbdt import LGBModel
from qlib.data.dataset import DatasetH

H5 = Path(sys.argv[1] if len(sys.argv) > 1 else "tw_daily_mini.h5")

# 1. Load + add next-day return as label
df = pd.read_hdf(H5)
df = df.sort_index()
close = df["$close"].unstack("instrument")
label = close.shift(-2) / close.shift(-1) - 1  # next-day open-to-open proxy
label_series = label.stack().reindex(df.index)

# Build column MultiIndex (feature, name) + (label, LABEL0) — Qlib convention
feat_cols = list(df.columns)
combined = df.copy()
combined["LABEL0"] = label_series
combined.columns = pd.MultiIndex.from_tuples(
    [("feature", c) for c in feat_cols] + [("label", "LABEL0")]
)
print(f"Loaded: {combined.shape}, dates {combined.index.get_level_values('datetime').min().date()} -> {combined.index.get_level_values('datetime').max().date()}")

# 3. Init qlib (region irrelevant — StaticDataLoader doesn't use bin data)
qlib.init(provider_uri="~/.qlib/qlib_data/cn_data", region="cn", expression_cache=None, dataset_cache=None)

# 4. Build handler
handler = DataHandlerLP(
    instruments=df.index.get_level_values("instrument").unique().tolist(),
    start_time="2022-01-01",
    end_time="2024-12-31",
    data_loader={
        "class": "StaticDataLoader",
        "module_path": "qlib.data.dataset.loader",
        "kwargs": {"config": combined},
    },
    infer_processors=[
        {"class": "RobustZScoreNorm", "kwargs": {"fields_group": "feature", "clip_outlier": True,
                                                  "fit_start_time": "2022-01-01", "fit_end_time": "2023-06-30"}},
        {"class": "Fillna", "kwargs": {"fields_group": "feature"}},
    ],
    learn_processors=[
        {"class": "DropnaLabel"},
        {"class": "CSZScoreNorm", "kwargs": {"fields_group": "label"}},
    ],
)

dataset = DatasetH(
    handler=handler,
    segments={
        "train": ("2022-01-01", "2023-06-30"),
        "valid": ("2023-07-01", "2023-12-31"),
        "test":  ("2024-01-01", "2024-12-31"),
    },
)

# 5. Train a small LGBModel (5 stocks: shrink hyperparams)
model = LGBModel(loss="mse", learning_rate=0.05, num_leaves=15, max_depth=4,
                 colsample_bytree=0.8, subsample=0.8, lambda_l1=0.1, lambda_l2=0.1)
model.fit(dataset)

# 6. Predict + compute IC / RankIC on test
pred = model.predict(dataset, segment="test")
y = dataset.prepare("test", col_set="label", data_key="infer")
y = y.iloc[:, 0]
joined = pd.concat([pred.rename("pred"), y.rename("label")], axis=1).dropna()
print(f"\nTest rows: {len(joined)}")
ic = joined.groupby(level="datetime").apply(lambda x: x["pred"].corr(x["label"]))
rank_ic = joined.groupby(level="datetime").apply(lambda x: x["pred"].corr(x["label"], method="spearman"))
print(f"IC      mean={ic.mean():.4f}  std={ic.std():.4f}  IR={ic.mean()/ic.std():.3f}")
print(f"RankIC  mean={rank_ic.mean():.4f}  std={rank_ic.std():.4f}  IR={rank_ic.mean()/rank_ic.std():.3f}")
print("\nSmoke test PASSED — Qlib pipeline works on Taiwan HDF5.")
