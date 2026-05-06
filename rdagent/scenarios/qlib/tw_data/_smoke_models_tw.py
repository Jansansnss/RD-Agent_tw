"""
Sweep qlib's model zoo against our TW bin data via qrun.

For each model entry below, render the TW conf_baseline.yaml, swap the task.model
(and dataset class for *_ts variants), run qrun in a tempdir, and report:
  - status: ok / import_error / runtime_error / no_metrics
  - IC, RankIC if SigAnaRecord produced them
  - one-line error excerpt on failure

Smoke run uses the existing 5-stock TW bin at ~/.qlib/qlib_data/tw_data — results
are plumbing-level only, NOT a quality comparison. Re-run on full universe later
for actual model selection.

Usage:
    python _smoke_models_tw.py            # run all models
    python _smoke_models_tw.py --only LGBModel,XGBModel   # subset
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import jinja2
import yaml

HERE = Path(__file__).parent
TEMPLATE = HERE.parent / "experiment" / "factor_template_tw"
BASELINE = TEMPLATE / "conf_baseline.yaml"

# ---------------------------------------------------------------------------
# Model zoo
#
# `ts` = needs TSDatasetH (sequence dataset) instead of DatasetH.
# `skip_reason` = mark models we know won't run on 5-stock smoke data.
# ---------------------------------------------------------------------------
MODELS: list[dict] = [
    # --- gradient boosting / classical ---
    {"name": "LGBModel", "class": "LGBModel", "module": "qlib.contrib.model.gbdt",
     "kwargs": {"loss": "mse", "learning_rate": 0.05, "max_depth": 4, "num_leaves": 15, "num_threads": 4}},
    {"name": "XGBModel", "class": "XGBModel", "module": "qlib.contrib.model.xgboost",
     "kwargs": {"eval_metric": "rmse", "max_depth": 4, "eta": 0.05, "n_estimators": 100}},
    {"name": "CatBoostModel", "class": "CatBoostModel", "module": "qlib.contrib.model.catboost_model",
     "kwargs": {"loss_function": "RMSE", "iterations": 200, "depth": 4, "learning_rate": 0.05, "verbose": False}},
    {"name": "DEnsembleModel", "class": "DEnsembleModel", "module": "qlib.contrib.model.double_ensemble",
     "kwargs": {"base_model": "gbm", "loss": "mse", "num_models": 3, "enable_sr": True, "enable_fs": False,
                "alpha1": 1.0, "alpha2": 1.0, "bins_sr": 5, "bins_fs": 10, "decay": 0.5,
                "sample_ratios": [0.8, 0.7, 0.6, 0.5, 0.4], "sub_weights": [1, 1, 1], "epochs": 10}},
    {"name": "LinearModel", "class": "LinearModel", "module": "qlib.contrib.model.linear",
     "kwargs": {"estimator": "ridge", "alpha": 0.05}},
    # --- pytorch (CPU) ---
    {"name": "MLP", "class": "DNNModelPytorch", "module": "qlib.contrib.model.pytorch_nn",
     "kwargs": {"loss": "mse", "lr": 0.002, "max_steps": 200, "batch_size": 64,
                "GPU": 0, "pt_model_kwargs": {"input_dim": 158}}},
    {"name": "LSTM_ts", "class": "LSTM", "module": "qlib.contrib.model.pytorch_lstm_ts",
     "kwargs": {"d_feat": 158, "hidden_size": 32, "num_layers": 1, "dropout": 0.0,
                "n_epochs": 5, "lr": 0.001, "early_stop": 5, "batch_size": 800, "GPU": 0}, "ts": True},
    {"name": "GRU_ts", "class": "GRU", "module": "qlib.contrib.model.pytorch_gru_ts",
     "kwargs": {"d_feat": 158, "hidden_size": 32, "num_layers": 1, "dropout": 0.0,
                "n_epochs": 5, "lr": 0.001, "early_stop": 5, "batch_size": 800, "GPU": 0}, "ts": True},
    {"name": "ALSTM_ts", "class": "ALSTM", "module": "qlib.contrib.model.pytorch_alstm_ts",
     "kwargs": {"d_feat": 158, "hidden_size": 32, "num_layers": 1, "dropout": 0.0,
                "n_epochs": 5, "lr": 0.001, "early_stop": 5, "batch_size": 800, "GPU": 0}, "ts": True},
    {"name": "Transformer_ts", "class": "TransformerModel", "module": "qlib.contrib.model.pytorch_transformer_ts",
     "kwargs": {"d_feat": 158, "d_model": 32, "nhead": 2, "num_layers": 2, "dropout": 0.1,
                "n_epochs": 3, "lr": 0.0005, "batch_size": 512, "GPU": 0}, "ts": True},
    {"name": "TCN_ts", "class": "TCN", "module": "qlib.contrib.model.pytorch_tcn_ts",
     "kwargs": {"d_feat": 158, "num_layers": 3, "n_chans": 32, "kernel_size": 3, "dropout": 0.1,
                "n_epochs": 5, "lr": 0.001, "batch_size": 800, "GPU": 0}, "ts": True},
    # --- tagged but skipped on 5-stock smoke (uncomment after full universe) ---
    {"name": "GATs_ts", "class": "GATs", "module": "qlib.contrib.model.pytorch_gats_ts",
     "kwargs": {"d_feat": 158, "hidden_size": 32, "num_layers": 1, "dropout": 0.0,
                "n_epochs": 3, "lr": 0.001, "batch_size": 800, "GPU": 0}, "ts": True,
     "skip_reason": "graph attention is meaningless with N=5; rerun after full-universe download"},
    {"name": "HIST", "class": "HIST", "module": "qlib.contrib.model.pytorch_hist",
     "skip_reason": "needs concept-graph CSV; not yet built for TW"},
    {"name": "IGMTF_ts", "class": "IGMTF", "module": "qlib.contrib.model.pytorch_igmtf",
     "skip_reason": "instance-graph needs N>>5"},
]


# ---------------------------------------------------------------------------
# Render baseline YAML once
# ---------------------------------------------------------------------------

def render_baseline() -> dict:
    from qlib.contrib.data.loader import Alpha158DL
    exprs, names = Alpha158DL.get_feature_config()
    src = BASELINE.read_text(encoding="utf-8")
    rendered = jinja2.Template(src).render(
        train_start="2022-01-01", train_end="2023-06-30",
        valid_start="2023-07-01", valid_end="2023-12-31",
        test_start="2024-01-01", test_end="2024-12-27",
        feature_expressions=json.dumps(exprs),
        feature_names=json.dumps(names),
    )
    return yaml.safe_load(rendered)


def swap_model(cfg: dict, entry: dict) -> dict:
    """Return a deep copy of cfg with task.model (and dataset class for ts) replaced."""
    import copy
    cfg = copy.deepcopy(cfg)
    cfg["task"]["model"] = {
        "class": entry["class"],
        "module_path": entry["module"],
        "kwargs": entry.get("kwargs", {}),
    }
    if entry.get("ts"):
        # Sequence models need TSDatasetH + step_len.
        cfg["task"]["dataset"]["class"] = "TSDatasetH"
        cfg["task"]["dataset"]["kwargs"]["step_len"] = 20
        # Handler stays DataHandlerLP — TSDatasetH wraps it.
    return cfg


# ---------------------------------------------------------------------------
# Run one model
# ---------------------------------------------------------------------------

METRIC_RE = re.compile(r"'(IC|Rank IC|ICIR|Rank ICIR)':\s*np\.float64\(([-\d.eE+]+)\)")


def run_one(entry: dict, base_cfg: dict) -> dict:
    if "skip_reason" in entry:
        return {"name": entry["name"], "status": "skipped", "msg": entry["skip_reason"]}

    cfg = swap_model(base_cfg, entry)
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        shutil.copy(TEMPLATE / "read_exp_res.py", work)
        cfg_path = work / "conf.yaml"
        cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
        proc = subprocess.run(
            ["qrun", str(cfg_path)], cwd=work, capture_output=True, text=True, timeout=900
        )
        out = proc.stdout + "\n" + proc.stderr
        if proc.returncode != 0:
            tail = "\n".join((proc.stderr or proc.stdout).splitlines()[-3:])
            status = "import_error" if "ModuleNotFoundError" in out or "ImportError" in out else "runtime_error"
            return {"name": entry["name"], "status": status, "msg": tail[:300]}

        metrics = {k: float(v) for k, v in METRIC_RE.findall(out)}
        if not metrics:
            return {"name": entry["name"], "status": "no_metrics", "msg": "qrun ok but no IC parsed"}
        return {
            "name": entry["name"],
            "status": "ok",
            "ic": metrics.get("IC"),
            "rank_ic": metrics.get("Rank IC"),
            "ic_ir": metrics.get("ICIR"),
            "rank_ic_ir": metrics.get("Rank ICIR"),
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="Comma-separated subset of model names")
    args = ap.parse_args()

    only = {s.strip() for s in args.only.split(",") if s.strip()}
    base_cfg = render_baseline()

    results = []
    for entry in MODELS:
        if only and entry["name"] not in only:
            continue
        print(f"\n>>> {entry['name']} ...", flush=True)
        r = run_one(entry, base_cfg)
        results.append(r)
        if r["status"] == "ok":
            print(f"    OK  IC={r['ic']:+.4f}  RankIC={r['rank_ic']:+.4f}  "
                  f"ICIR={r.get('ic_ir', float('nan')):+.3f}")
        else:
            print(f"    {r['status'].upper()}: {r.get('msg', '')[:200]}")

    # Final table
    print("\n" + "=" * 78)
    print(f"{'model':<18} {'status':<14} {'IC':>9} {'RankIC':>9} {'ICIR':>8} {'RankICIR':>9}")
    print("-" * 78)
    for r in results:
        if r["status"] == "ok":
            print(f"{r['name']:<18} {'ok':<14} "
                  f"{r['ic']:>+9.4f} {r['rank_ic']:>+9.4f} "
                  f"{r.get('ic_ir', float('nan')):>+8.3f} {r.get('rank_ic_ir', float('nan')):>+9.3f}")
        else:
            print(f"{r['name']:<18} {r['status']:<14} {r.get('msg','')[:40]}")
    print("=" * 78)

    # Persist as JSON next to the script for later diffing
    out_json = HERE / "smoke_models_results.json"
    out_json.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nSaved {out_json}")

    fails = [r for r in results if r["status"] not in ("ok", "skipped")]
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
