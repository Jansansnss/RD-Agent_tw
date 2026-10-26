"""
Independent A-F review of a factor by an LLM other than the one that wrote the first review
(DECISIONS D25, D32; docs/tw/FACTOR_REPORT.md §5).

The prompt is tw_data/alpha_review_prompt_zh.md as alpha_review_tw.py filled it for the card (code,
the author's hypothesis, research and holdout numbers, holdings), plus the facts of the formal
report (report.json: prediction checks, size and market splits, benchmarks, number of trials).
The interpretation and earlier reviews are left out, so the model reviews the factor, not another
reviewer.

Run it yourself in WSL: the API key is read from .env inside this process and never printed. Each
review costs a few cents on the DeepSeek account that the alpha loop and the contest agent share,
so it stops when the balance is under the $0.50 reserve (D27) unless --allow_reserve.
--prompt_only writes the prompt without calling any model (no key needed), e.g. to paste by hand.

    python -m rdagent.scenarios.qlib.tw_data.factor_review_llm_tw --card LiquiditySize_Rank_20d --card SalesYield_LiquiditySize_70_30
    python -m rdagent.scenarios.qlib.tw_data.factor_review_llm_tw --card LiquiditySize_Rank_20d --prompt_only
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

try:
    from rdagent.scenarios.qlib.tw_data._paths import DATA_DIR, REPO_ROOT
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _paths import DATA_DIR, REPO_ROOT

REVIEWS = DATA_DIR / "alpha_tw" / "reviews"
REPORTS = DATA_DIR / "alpha_tw" / "reports"
RESERVE_USD = 0.50  # the contest agent's D-Plan calls run on the same account (D27)
FACT_KEYS = ("factor", "verdict_by_code", "facts", "prediction_checks")  # no interpretation, no review
MARKER = "若關鍵資訊缺失"  # first line after the filled 【輸入】 section of the reviewer prompt


def build_prompt(card: str) -> str:
    filled = REVIEWS / f"{card}_prompt.md"
    if not filled.exists():
        raise SystemExit(f"{filled} is missing: run python -m rdagent.scenarios.qlib.tw_data.alpha_review_tw "
                         f"--card {card}")
    text = filled.read_text(encoding="utf-8")
    report = REPORTS / card / "report.json"
    if not report.exists():
        print(f"{card}: no formal report at {report}; the prompt has the review inputs only")
        return text
    facts = {k: v for k, v in json.loads(report.read_text(encoding="utf-8")).items() if k in FACT_KEYS}
    extra = ("【補充：正式因子報告的程式數字（report.json）】\n"
             "facts 是研究期（保留期只有通過與否；保留期數字見上面的輸入）。prediction_checks 是依作者主張推得的預測，"
             "看過結果後才寫，由程式依固定規則判定；verdict_by_code.trials 是迴圈累計評分到這張為止的因子數。\n"
             "```json\n" + json.dumps(facts, ensure_ascii=False, separators=(",", ":")) + "\n```\n\n")
    return text.replace(MARKER, extra + MARKER, 1) if MARKER in text else text + "\n\n" + extra


def deepseek_balance() -> tuple[float, str] | None:
    """The account balance, as the loop's watchdog reads it; None when it cannot be read."""
    import requests

    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        return None
    resp = requests.get("https://api.deepseek.com/user/balance", headers={"Authorization": f"Bearer {key}"},
                        timeout=30)
    resp.raise_for_status()
    infos = resp.json().get("balance_infos") or []
    info = next((i for i in infos if i.get("currency") == "USD"), infos[0] if infos else None)
    return (float(info["total_balance"]), info["currency"]) if info else None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--card", action="append", required=True, help="library card name (repeatable)")
    parser.add_argument("--model", default="deepseek/deepseek-v4-pro",
                        help="litellm model name; it must differ from the first reviewer's and the author's")
    parser.add_argument("--prompt_only", action="store_true", help="write the prompt, call no model")
    parser.add_argument("--allow_reserve", action="store_true", help="spend below the $0.50 reserve")
    args = parser.parse_args()
    slug = re.sub(r"[^\w.-]", "_", args.model.split("/")[-1])
    prompts = {card: build_prompt(card) for card in args.card}
    for card, prompt in prompts.items():
        path = REVIEWS / f"{card}_review_{slug}_prompt.md"
        path.write_text(prompt, encoding="utf-8")
        print(f"{card}: prompt {len(prompt):,} characters -> {path}")
    if args.prompt_only:
        return

    from dotenv import load_dotenv

    load_dotenv(REPO_ROOT / ".env")
    import litellm

    for card, prompt in prompts.items():
        try:
            balance = deepseek_balance() if args.model.startswith("deepseek/") else None
        except Exception as err:  # noqa: BLE001
            if not args.allow_reserve:
                raise SystemExit(f"Could not read the DeepSeek balance ({type(err).__name__}); "
                                 "pass --allow_reserve to review anyway")
            balance = None
        if balance:
            print(f"{card}: DeepSeek balance {balance[0]:.2f} {balance[1]}")
            if balance[0] < RESERVE_USD and not args.allow_reserve:
                raise SystemExit(f"Balance under the {RESERVE_USD:.2f} reserve the contest agent needs (D27); "
                                 "top up first, or pass --allow_reserve")
        try:
            resp = litellm.completion(model=args.model, messages=[{"role": "user", "content": prompt}], timeout=600)
        except Exception as err:  # noqa: BLE001 - report the type, never the request
            raise SystemExit(f"{card}: {type(err).__name__}: {str(err)[:300]}\n"
                             "deepseek/ models read DEEPSEEK_API_KEY from .env, the same variable the loop uses")
        answer = resp.choices[0].message.content or ""
        try:
            cost = f"；LiteLLM 估計 ${litellm.completion_cost(completion_response=resp):.4f}"
        except Exception:  # noqa: BLE001 - no price table for this model
            cost = ""
        out = REVIEWS / f"{card}_review_{slug}.md"
        out.write_text("\n".join([
            f"# {card}：獨立審查（{args.model}）", "",
            f"- 時間：{dt.datetime.now().isoformat(timespec='seconds')}",
            f"- 要求的模型：{args.model}；實際回應的模型：{resp.model}",
            f"- tokens：輸入 {resp.usage.prompt_tokens:,}、輸出 {resp.usage.completion_tokens:,}{cost}",
            f"- 審查前餘額：{f'{balance[0]:.2f} {balance[1]}' if balance else '未讀取'}",
            f"- 提示全文：{out.with_name(f'{card}_review_{slug}_prompt.md').name}",
            "", "---", "", answer.strip(), ""]), encoding="utf-8")
        print(f"{card}: review by {resp.model} -> {out}")


if __name__ == "__main__":
    main()
