"""
Render conf_baseline.yaml with default segments and qrun it against the bin data.
Standalone test, mirrors what RD-Agent's runner would do (minus Docker).
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import jinja2

HERE = Path(__file__).parent
TEMPLATE = HERE.parent / "experiment" / "factor_template_tw"
BASELINE = TEMPLATE / "conf_baseline.yaml"

# Render Jinja template with defaults (Qlib's qrun expects pure YAML, no jinja)
src = BASELINE.read_text(encoding="utf-8")
from qlib.contrib.data.loader import Alpha158DL
exprs, names = Alpha158DL.get_feature_config()
import json
rendered = jinja2.Template(src).render(
    train_start="2022-01-01", train_end="2023-06-30",
    valid_start="2023-07-01", valid_end="2023-12-31",
    test_start="2024-01-01",  test_end="2024-12-27",  # one trading day buffer for next-day fill
    feature_expressions=json.dumps(exprs),
    feature_names=json.dumps(names),
)

with tempfile.TemporaryDirectory() as tmp:
    work = Path(tmp)
    shutil.copy(TEMPLATE / "read_exp_res.py", work)
    cfg = work / "conf.yaml"
    cfg.write_text(rendered, encoding="utf-8")
    print(f"Running qrun in {work} ...")
    r = subprocess.run(["qrun", str(cfg)], cwd=work, capture_output=True, text=True)
    print("STDOUT tail:")
    print("\n".join(r.stdout.splitlines()[-25:]))
    if r.returncode != 0:
        print("STDERR tail:")
        print("\n".join(r.stderr.splitlines()[-25:]))
        sys.exit(r.returncode)
    # Try to read results
    r2 = subprocess.run(["python", "read_exp_res.py"], cwd=work, capture_output=True, text=True)
    print("\nread_exp_res.py output:")
    print(r2.stdout[-1500:])
    print(r2.stderr[-500:])
