"""
Checks the files a teammate hands in for a formal report (docs/tw/REPORT_HANDIN.md), and a finished
report.json, against tw-factor-report/1. Runs on its own (pandas and PyYAML; jsonschema when
installed), so it can be copied with factor_report_schema_tw.json into any repo and run before
handing anything in.

    python factor_report_check_tw.py --weights weights.csv --declaration mine.yaml --custom_tests tests.yaml
    python factor_report_check_tw.py --values scores.csv --declaration mine.yaml
    python factor_report_check_tw.py --report data/alpha_tw/reports/<id>/report.json

Errors must be fixed before handing in; warnings are what the report will flag anyway. Exit code 1
on any error.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

# Kept in step with alpha_eval_tw and factor_report_tw; this file must run without them.
SCHEMA = "tw-factor-report/1"
PODS = ("營收獲利動能", "價格動能", "資金流向", "事件催化", "風險控管")
HORIZONS = (1, 5, 10, 20)
RESEARCH = (pd.Timestamp("2021-01-01"), pd.Timestamp("2026-04-30"))
MECHANISMS = ("risk_premium", "behavioral", "structural", "unknown")
ROLES = ("selection_alpha", "style_tilt", "risk_control", "watch", "reject")
JUDGMENTS = ("合理", "有條件合理", "解釋不成立", "資訊不足")
PREDICTIONS = {"sign": ("positive", "negative"), "peak_horizon_days": HORIZONS,
               "stronger_in": ("small", "large", "none"), "industry_neutral": ("survives", "weakens"),
               "regime": ("up", "down", "both")}
CUSTOM_KEYS = ("name", "method", "results", "window", "universe", "costs", "code_ref", "notes")
TOP_KEYS = ("schema", "kind", "how_to_read", "factor", "setup", "verdict_by_code", "facts", "facts_custom",
            "prediction_checks", "claims", "review", "provenance")
HOLDOUT_RULE = ("保留期模式（setup.holdout）不是 full：facts.holdout 只能是 {\"withheld\": ...}，"
                "claims.interpretation 與 review 不能帶 holdout_notes")
HERE = Path(__file__).resolve().parent
META = HERE.parents[3] / "data" / "alpha_tw" / "instruments_meta.csv"  # in RD-Agent; elsewhere pass --meta


class Findings:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, where: str, text: str) -> None:
        self.errors.append(f"{where}：{text}")

    def warn(self, where: str, text: str) -> None:
        self.warnings.append(f"{where}：{text}")


# --------------------------------------------------------------------------- tables


def read_table(path: Path, f: Findings, strict_codes: bool) -> pd.DataFrame | None:
    """A CSV keyed by datetime and instrument (date and stock_id accepted), codes kept as text.
    strict_codes: a code that is not 4 digits is an error (weights), else a warning (values)."""
    where = path.name
    try:
        df = pd.read_csv(path, dtype={"instrument": str, "stock_id": str})
    except (OSError, ValueError) as e:
        f.error(where, f"讀不到 CSV：{e}")
        return None
    df = df.rename(columns={"date": "datetime", "stock_id": "instrument"})
    missing = [c for c in ("datetime", "instrument") if c not in df.columns]
    if missing:
        f.error(where, f"少了欄位 {missing}；前兩欄要是 datetime, instrument")
        return None
    when = pd.to_datetime(df["datetime"], errors="coerce", format="mixed")
    bad = when.isna()
    if bad.any():
        f.error(where, f"{int(bad.sum())} 列的 datetime 讀不懂（第一個在第 {int(bad.to_numpy().argmax()) + 2} 列："
                       f"{df['datetime'][bad].iloc[0]!r}）；請用 YYYY-MM-DD")
    codes = df["instrument"].astype(str).str.strip()
    odd = ~codes.str.fullmatch(r"\d{4}")
    if odd.any():
        text = (f"{int(odd.sum())} 列的股票代號不是 4 位數字（例如 {', '.join(sorted(set(codes[odd]))[:5])}）："
                "只寫代號本身，例如 2330；不要 .TW，也別讓 Excel 吃掉開頭的 0")
        (f.error if strict_codes else f.warn)(where, text if strict_codes else text + "；這些列會被忽略")
    df = df.assign(datetime=when, instrument=codes)[~bad]
    dup = df.duplicated(["datetime", "instrument"])
    if dup.any():
        first = df[dup].iloc[0]
        f.error(where, f"{int(dup.sum())} 列重複（同一天同一檔出現兩次），例如 {first['datetime'].date()} {first['instrument']}")
    return df


def coverage(where: str, dates: pd.DatetimeIndex, f: Findings) -> None:
    """The research window is 2021-01-01 to 2026-04-30; the holdout starts the day after."""
    start, end = RESEARCH
    if not len(dates):
        f.error(where, "沒有任何可用的列")
        return
    if dates.min() > end:
        f.error(where, f"最早的日期是 {dates.min().date()}，沒有 2026-04-30 以前的資料，研究期無法評分")
        return
    if (end - max(dates.min(), start)).days < 3 * 365:
        f.warn(where, f"研究期從 {max(dates.min(), start).date()} 才開始；門檻要 3 個完整年度為正，期間越短證據越弱")
    if dates.max() <= end:
        f.warn(where, f"資料只到 {dates.max().date()}；沒有 2026-05-01 之後的資料，就無法做保留期確認")


def check_weights(path: Path, f: Findings) -> None:
    """Daily target weights of a strategy: datetime, instrument, weight (share of NAV)."""
    where = path.name
    df = read_table(path, f, strict_codes=True)
    if df is None:
        return
    if "weight" not in df.columns:
        f.error(where, "少了欄位 weight（占淨值的比例，0.042 代表 4.2%）")
        return
    extra = [c for c in df.columns if c not in ("datetime", "instrument", "weight")]
    if extra:
        f.warn(where, f"這些欄位會被忽略：{extra}")
    w = pd.to_numeric(df["weight"], errors="coerce")
    if w.isna().any():
        f.error(where, f"{int(w.isna().sum())} 列的 weight 是空的或不是數字")
    if (w < 0).any():
        f.error(where, f"{int((w < 0).sum())} 列的權重是負的；比賽只能做多")
    if (w > 1).any():
        f.error(where, "有權重大於 1：weight 是占淨值的比例（4.2% 寫 0.042），不是百分比")
    df = df.assign(weight=w).dropna(subset=["weight"])
    total = df.groupby("datetime")["weight"].sum()
    over = total > 1 + 1e-6
    if over.any():
        f.error(where, f"{int(over.sum())} 天的權重加總超過 1（第一次 {over.idxmax().date()}：{total[over].iloc[0]:.3f}）；"
                       "加總以外的部分才是現金")
    held = df[df["weight"] > 0]
    names = held.groupby("datetime").size().reindex(total.index, fill_value=0)
    is_2330 = held["instrument"] == "2330"
    w2330 = held[is_2330].groupby("datetime")["weight"].sum().reindex(total.index, fill_value=0.0)
    largest = held[~is_2330].groupby("datetime")["weight"].max().reindex(total.index, fill_value=0.0)
    rules = {"持股 20–30 檔": (names < 20) | (names > 30), "單檔 ≤ 10%（2330 以外）": largest > 0.10 + 1e-6,
             "2330 ≤ 25%": w2330 > 0.25 + 1e-6, "現金 < 25%": 1 - total >= 0.25 - 1e-6}
    for rule, bad in rules.items():
        if bad.any():
            f.warn(where, f"比賽規則「{rule}」：{bad.mean():.0%} 的決策日違反（第一次 {bad.idxmax().date()}）；報告會標出來")
    coverage(where, pd.DatetimeIndex(total.index), f)
    gaps = pd.Series(total.index).diff().dt.days.median() if len(total) > 1 else 0
    print(f"{where}: {len(df):,} 列、{len(total)} 個決策日（{total.index.min().date()} → {total.index.max().date()}，"
          f"間隔中位數 {gaps:.0f} 天）；平均 {names.mean():.1f} 檔、現金 {1 - total.mean():.1%}")


def check_values(path: Path, f: Findings) -> None:
    """A factor's scores: datetime, instrument and one value column (higher = buy)."""
    where = path.name
    df = read_table(path, f, strict_codes=False)
    if df is None:
        return
    value = [c for c in df.columns if c not in ("datetime", "instrument")]
    if len(value) != 1:
        f.error(where, f"除了 datetime, instrument 要剛好一個數值欄，現在是 {value}")
        return
    v = pd.to_numeric(df[value[0]], errors="coerce")
    text = v.isna() & df[value[0]].notna()
    if text.any():
        f.error(where, f"{int(text.sum())} 列的值不是數字（例如 {df[value[0]][text].iloc[0]!r}）")
    if np.isinf(v).any():
        f.error(where, f"{int(np.isinf(v).sum())} 列是 inf；請改成空值")
    df = df.assign(value=v.replace([np.inf, -np.inf], np.nan)).dropna(subset=["value"])
    per_day = df.groupby("datetime")["value"]
    names = per_day.size()
    if len(names) and names.median() < 120:
        f.warn(where, f"每天有值的股票中位數 {names.median():.0f} 檔；股票池 150 檔，覆蓋率低於 80% 過不了門檻")
    flat = per_day.nunique() <= 1
    if flat.mean() > 0.05:
        f.warn(where, f"{flat.mean():.0%} 的日子所有股票的值都一樣，那幾天無法排名")
    coverage(where, pd.DatetimeIndex(names.index), f)
    print(f"{where}: {len(df):,} 個值、{len(names)} 天（{names.index.min().date()} → {names.index.max().date()}），"
          f"每天中位數 {names.median():.0f} 檔")


# --------------------------------------------------------------------------- YAML


def read_yaml(path: Path, f: Findings):
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as e:
        f.error(path.name, f"YAML 讀不懂：{e}")
        return None


def check_declaration(path: Path, f: Findings, kind: str) -> None:
    """Stage 1 (factor, thesis, predictions) must be complete; stage 2 and the review when present."""
    where = path.name
    decl = read_yaml(path, f)
    if decl is None:
        return
    if not isinstance(decl, dict):
        f.error(where, "最外層要是 factor、thesis、predictions 這幾個區塊")
        return
    fac, th, pred = (decl.get(k) if isinstance(decl.get(k), dict) else {} for k in ("factor", "thesis", "predictions"))

    def need(block: dict, key: str, label: str) -> None:
        if block.get(key) in (None, "", [], {}):
            f.error(where, f"{label} 沒填")

    for key in ("id", "name", "author", "one_liner", "formula", "inputs", "data_timing", "declared_on"):
        need(fac, key, f"factor.{key}")
    if fac.get("id") and not re.fullmatch(r"[A-Za-z0-9_.-]+", str(fac["id"])):
        f.error(where, "factor.id 只能用英數字、底線、點和減號")
    if fac.get("pod") not in PODS:
        f.error(where, f"factor.pod 要是五大因子之一：{'、'.join(PODS)}")
    if fac.get("horizon_days") not in HORIZONS:
        f.error(where, "factor.horizon_days 要是 1、5、10 或 20")
    if not (isinstance(fac.get("trials"), int) and fac["trials"] >= 1):
        f.error(where, "factor.trials 要是 ≥ 1 的整數（這個想法試過的版本數，含失敗的）")
    if kind == "portfolio":
        need(fac, "training", "factor.training（策略要寫模型或參數用哪段資料訓練、多久重訓）")
    elif not fac.get("training"):
        f.warn(where, "factor.training 沒填：機器學習的分數一定要寫用哪段資料訓練；固定公式寫「無（固定公式）」")
    if th.get("mechanism_type") not in MECHANISMS:
        f.error(where, f"thesis.mechanism_type 要是 {'／'.join(MECHANISMS)}")
    for key in ("concept", "story", "counterparty", "why_persists"):
        need(th, key, f"thesis.{key}")
    if pred.get("registered") not in ("before_results", "after_results"):
        f.error(where, "predictions.registered 要是 before_results（看結果前寫）或 after_results")
    elif pred["registered"] == "after_results":
        f.warn(where, "預測是看過結果才寫的，報告的邏輯檢查只能當描述")
    for key, choices in PREDICTIONS.items():
        if pred.get(key) is None:
            f.warn(where, f"predictions.{key} 沒填，這條無法檢查")
        elif pred[key] not in choices:
            f.error(where, f"predictions.{key} 只能是 {'／'.join(map(str, choices))}，現在是 {pred[key]!r}")
    it = decl.get("interpretation")
    if isinstance(it, dict) and it:
        if it.get("role") not in ROLES:
            f.error(where, f"interpretation.role 要是 {'／'.join(ROLES)}")
        for key in ("summary", "risks", "kill_switch"):
            need(it, key, f"interpretation.{key}")
    rv = decl.get("review")
    if isinstance(rv, dict) and rv:
        for key in ("reviewer", "summary"):
            need(rv, key, f"review.{key}")
        if rv.get("judgment") not in JUDGMENTS:
            f.error(where, f"review.judgment 要是 {'／'.join(JUDGMENTS)}")
        if rv.get("reviewer") and str(rv["reviewer"]) == str(fac.get("author")):
            f.warn(where, "審查者跟作者相同；獨立審查要用不同的模型或人")


def check_custom(path: Path, f: Findings) -> None:
    """The author's own tests: a list of {name, method, results, window, universe, costs, code_ref, notes}."""
    tests = read_yaml(path, f)
    if tests is None:
        return
    if not isinstance(tests, list) or not tests:
        f.error(path.name, "要是一個清單，每一項是一個測試（- name: ...）")
        return
    for i, t in enumerate(tests, 1):
        where = f"{path.name} 第 {i} 項"
        if not isinstance(t, dict):
            f.error(where, "每一項要有 name、method、results 等欄位")
            continue
        for key in ("name", "method"):
            if not t.get(key):
                f.error(where, f"{key} 沒填")
        if not isinstance(t.get("results"), dict) or not t["results"]:
            f.error(where, "results 要是「項目: 數值」的對照，例如 年化報酬: 18.2%")
        unknown = sorted(set(t) - set(CUSTOM_KEYS))
        if unknown:
            f.warn(where, f"這些欄位不在格式裡，報告不會顯示：{unknown}")
        for key in ("window", "universe", "costs", "code_ref"):
            if not t.get(key):
                f.warn(where, f"{key} 沒填；別人無法判斷這個數字能不能跟共用評分比，或無法重現")


# --------------------------------------------------------------------------- report.json


def stock_words(meta: Path) -> dict[str, str]:
    """Stock codes and names from the panel's list. Codes that read like years are left out."""
    df = pd.read_csv(meta, dtype={"instrument": str})
    words = {name: code for code, name in zip(df["instrument"], df["name"]) if isinstance(name, str) and len(name) >= 2}
    words.update({code: code for code in df["instrument"] if code.isdigit() and not 1990 <= int(code) <= 2035})
    return words


def check_report(path: Path, f: Findings, schema_path: Path, meta: Path | None) -> None:
    where = f"{path.parent.name}/{path.name}"
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        f.error(where, f"JSON 讀不懂：{e}")
        return
    try:
        import jsonschema
    except ImportError:
        jsonschema = None
    if jsonschema is not None:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        by_path: dict[str, list] = {}
        for e in jsonschema.Draft202012Validator(schema).iter_errors(report):
            by_path.setdefault("/".join(map(str, e.absolute_path)) or "（最外層）", []).append(e)
        mode = (report.get("setup") or {}).get("holdout")
        for at, errors in sorted(by_path.items()):  # one message per place: the schema's branches repeat it
            e = max(errors, key=jsonschema.exceptions.relevance)
            leak = at == "facts/holdout" or "holdout_notes" in json.dumps(e.validator_value, default=str)
            if mode != "full" and leak:
                text = HOLDOUT_RULE
            elif e.validator in ("oneOf", "anyOf") and e.context:
                sub = jsonschema.exceptions.best_match([e])  # descends to the deepest error of the closest branch
                rel = list(sub.absolute_path)[len(e.absolute_path):]
                text = f"{'/'.join(map(str, rel))}：{sub.message}" if rel else sub.message
            else:
                text = e.message
            f.error(where, f"{at}：{text[:300]}")
    else:  # the essentials without jsonschema, the holdout rule included
        f.warn(where, "沒有安裝 jsonschema，只做基本檢查（pip install jsonschema 可做完整檢查）")
        if report.get("schema") != SCHEMA:
            f.error(where, f"schema 要是 {SCHEMA}")
        for key in TOP_KEYS:
            if key not in report:
                f.error(where, f"少了 {key}")
        mode = (report.get("setup") or {}).get("holdout")
        held = (report.get("facts") or {}).get("holdout")
        if mode != "full" and held is not None and set(held) != {"withheld"}:
            f.error(where, f"保留期模式是 {mode}，facts.holdout 卻有數字")
        notes = [k for k, block in (("claims.interpretation", (report.get("claims") or {}).get("interpretation")),
                                    ("review", report.get("review")))
                 if isinstance(block, dict) and "holdout_notes" in block]
        if mode != "full" and notes:
            f.error(where, f"{notes} 帶了 holdout_notes；只有 --holdout full 才能給模型")
    pod = (report.get("factor") or {}).get("pod")
    if pod not in PODS:
        f.warn(where, f"factor.pod 是 {pod!r}，不是五大因子之一")
    if meta is None or not meta.exists():
        f.warn(where, "找不到 instruments_meta.csv（--meta），沒有檢查 claims 與 review 是否提到個股")
        return
    text = json.dumps({k: report.get(k) for k in ("claims", "review")}, ensure_ascii=False)
    hits = sorted({f"{w}（{code}）" for w, code in stock_words(meta).items()
                   if (re.search(rf"(?<!\d){w}(?!\d)", text) if w.isdigit() else w in text)})
    if hits:
        f.warn(where, f"claims 或 review 可能提到個股：{'、'.join(hits[:10])}；大腦讀的版本不放個股，若只是一般用語可忽略")


# --------------------------------------------------------------------------- CLI


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--weights", help="strategy: CSV datetime, instrument, weight (share of NAV)")
    parser.add_argument("--values", help="factor: CSV datetime, instrument, one value column")
    parser.add_argument("--declaration", help="the declaration YAML (stage 1, and stage 2 when written)")
    parser.add_argument("--custom_tests", help="YAML list of the author's own tests")
    parser.add_argument("--report", action="append", default=[], help="a report.json (repeatable)")
    parser.add_argument("--kind", choices=["factor", "portfolio"],
                        help="default: portfolio with --weights, else factor")
    parser.add_argument("--schema", default=str(HERE / "factor_report_schema_tw.json"))
    parser.add_argument("--meta", default=str(META), help="instruments_meta.csv, for stock names in claims and review")
    args = parser.parse_args()
    if not (args.weights or args.values or args.declaration or args.custom_tests or args.report):
        parser.error("give at least one file to check")
    if hasattr(sys.stdout, "reconfigure"):  # a Big5 (cp950) Windows console cannot print ≤ and would crash
        sys.stdout.reconfigure(errors="replace")

    f = Findings()
    if args.weights:
        check_weights(Path(args.weights), f)
    if args.values:
        check_values(Path(args.values), f)
    if args.declaration:
        check_declaration(Path(args.declaration), f, args.kind or ("portfolio" if args.weights else "factor"))
    if args.custom_tests:
        check_custom(Path(args.custom_tests), f)
    for path in args.report:
        check_report(Path(path), f, Path(args.schema), Path(args.meta) if args.meta else None)

    for line in f.errors:
        print(f"錯誤  {line}")
    for line in f.warnings:
        print(f"提醒  {line}")
    print(f"{len(f.errors)} 個錯誤、{len(f.warnings)} 個提醒" + ("；請先修正錯誤再交件" if f.errors else "；可以交件"))
    sys.exit(1 if f.errors else 0)


if __name__ == "__main__":
    main()
