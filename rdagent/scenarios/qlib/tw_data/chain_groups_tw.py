"""
Industry groups for the alpha panel: the team's product-exposure groups, then FinMind's industry
chain (DECISIONS D30, D31).

Jans, 2026-10-26: classification follows full_classification_relations_T20261021_preview.pdf (it
replaced relations_groups_review_T2026-10-21_v001.pdf the same day). Its part 1 is the
product-exposure taxonomy V2: 6 families / 38 sub-groups, 217 memberships of 171 stocks (光通訊,
記憶體, PCB, 高速電互連, 半導體設備, 測試介面); `--product_pdf` parses its member table into
data/alpha_tw/product_groups_v2.csv. Its part 2 is FinMind TaiwanStockIndustryChain (TPEx
產業價值鏈), the same as the earlier review and `industry_chain.csv`.

The panel's `$industry_id` needs one group per stock. A stock with a V2 membership takes its V2
family ("V2:PCB"): the family with the most of its sub-group memberships, ties to the more
specific (smaller) family. Every other stock takes a primary chain industry: among its chain
industries that fit its exchange category (COMPATIBLE), the one with the most sub-industry rows;
without a fitting one, the one with the most rows overall. Ties go to the earlier entry in
COMPATIBLE (each category's main industry first), then the larger industry, then the name.
OVERRIDES fixes the few ties this rule gets wrong. Stocks without chain rows (delisted) take their
exchange category's first compatible industry, or the exchange category itself.

Both lists are today's snapshot (V2 is marked provisional). Using them for 2021-2026 assumes a
stock's business did not change, and the V2 families and chain themes such as 人工智慧 or 雲端運算
were chosen with hindsight.

    python -m rdagent.scenarios.qlib.tw_data.chain_groups_tw --product_pdf <pdf>   # parse the V2 members
    python -m rdagent.scenarios.qlib.tw_data.chain_groups_tw            # writes data/alpha_tw/groups_chain.csv
    python -m rdagent.scenarios.qlib.tw_data.chain_groups_tw --apply    # also swaps $industry_id in the panels
"""

import argparse
import os
import re
import sys
from pathlib import Path

import pandas as pd

try:
    from rdagent.scenarios.qlib.tw_data._paths import DATA_DIR
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _paths import DATA_DIR

ALPHA_DIR = DATA_DIR / "alpha_tw"
CHAIN_CSV = DATA_DIR / "tw_finmind_full" / "industry_chain.csv"
GROUPS_CSV = ALPHA_DIR / "groups_chain.csv"
PRODUCT_CSV = ALPHA_DIR / "product_groups_v2.csv"
# V2 sub-group id prefix -> family
FAMILIES = {"V2_OPT_": "光通訊", "V2_MEM_": "記憶體", "V2_PCB_": "PCB", "V2_HS_": "高速電互連",
            "V2_EQ_": "半導體設備", "V2_TEST_": "測試介面"}
PANELS = ("alpha_panel.h5", "coder/daily_pv.h5", "coder_debug/daily_pv.h5", "coder_full/daily_pv.h5")

# Exchange category (TaiwanStockInfo) -> chain industries that describe the same business,
# read off which chain industries the panel's stocks of each category carry.
COMPATIBLE = {
    "半導體業": ("半導體",),
    "電子通路業": ("半導體",),
    "電腦及週邊設備業": ("電腦及週邊設備", "雲端運算"),
    "通信網路業": ("通信網路",),
    "光電業": ("平面顯示器", "LED照明產業", "太陽能產業", "觸控面板", "電腦及週邊設備"),
    "電子零組件業": ("印刷電路板", "被動元件", "連接器", "能源元件", "電腦及週邊設備"),
    "金融保險": ("金融",),
    "金融業": ("金融",),
    "航運業": ("交通運輸及航運",),
    "電機機械": ("電機機械",),
    "鋼鐵工業": ("鋼鐵",),
    "電器電纜": ("鋼鐵",),
    "塑膠工業": ("石化及塑橡膠",),
    "橡膠工業": ("石化及塑橡膠",),
    "紡織纖維": ("紡織",),
    "水泥工業": ("水泥",),
    "汽車工業": ("汽車",),
    "油電燃氣業": ("油電燃氣",),
    "生技醫療業": ("製藥", "醫療器材"),
    "化學生技醫療": ("製藥", "醫療器材"),
    "食品工業": ("食品",),
    "貿易百貨": ("貿易百貨",),
    "建材營造": ("建材營造",),
    "玻璃陶瓷": ("建材營造",),
    "文化創意業": ("文化創意業", "休閒娛樂"),
    "電子商務業": ("電子商務",),
    "資訊服務業": ("軟體服務",),
    "造紙工業": ("造紙",),
}
# Ties the rule settles wrongly: code -> group, with the reason.
OVERRIDES = {
    "5871": "金融",  # 中租-KY, a leasing company; the chain tags 太陽能產業 and 金融 once each
}


def load_chain(path: Path = CHAIN_CSV) -> pd.DataFrame:
    chain = pd.read_csv(path, dtype={"stock_id": str})
    return chain.rename(columns={"stock_id": "instrument"})[["instrument", "industry", "sub_industry"]].drop_duplicates()


def parse_product_pdf(path: Path) -> pd.DataFrame:
    """V2 memberships from the PDF's member table (part 1, section 二, up to section 三)."""
    import fitz  # PyMuPDF, in the rdagent env

    text = "\n".join(page.get_text("text") for page in fitz.open(path))
    table = text.split("二、成員表", 1)[1].split("三、舊分類", 1)[0]
    rows, sub = [], None
    for line in (s.strip() for s in table.split("\n")):
        if re.fullmatch(r"V2_[A-Z0-9_]+", line):
            sub = line
        elif sub and (m := re.fullmatch(r"(\d{4}) (.+?)(★?)", line)):
            family = next((f for prefix, f in FAMILIES.items() if sub.startswith(prefix)), None)
            rows.append({"instrument": m.group(1), "name": m.group(2), "sub_group": sub, "family": family,
                         "in_fixed_150": bool(m.group(3))})
    return pd.DataFrame(rows)


def load_products(path: Path = PRODUCT_CSV) -> pd.DataFrame | None:
    return pd.read_csv(path, dtype={"instrument": str}) if path.exists() else None


def primary_groups(meta: pd.DataFrame, chain: pd.DataFrame, products: pd.DataFrame | None = None) -> pd.DataFrame:
    """meta: indexed by instrument with the exchange category in 'industry'. Returns, per
    instrument, the primary group, all its groups (V2 families, then chain industries), and where
    the group came from."""
    rows = chain.groupby(["instrument", "industry"]).size().rename("rows").reset_index()
    size = rows.groupby("industry")["instrument"].nunique()
    fam = pd.DataFrame(columns=["instrument", "family", "n"]) if products is None else (
        products.groupby(["instrument", "family"]).size().rename("n").reset_index())
    fam_size = fam.groupby("family")["instrument"].nunique()
    out = []
    for code, twse in meta["industry"].items():
        mine = rows[rows["instrument"] == code]
        fams = fam[fam["instrument"] == code].assign(size=lambda d: d["family"].map(fam_size))
        fams = fams.sort_values(["n", "size", "family"], ascending=[False, True, True])
        fits = COMPATIBLE.get(twse, ())
        if len(fams):
            group, source = "V2:" + fams["family"].iloc[0], "product_v2"
        elif code in OVERRIDES:
            group, source = OVERRIDES[code], "override"
        elif len(mine):
            pool = mine[mine["industry"].isin(fits)] if mine["industry"].isin(fits).any() else mine
            order = {name: i for i, name in enumerate(fits)}
            pool = pool.assign(fit=pool["industry"].map(order).fillna(len(fits)), size=pool["industry"].map(size))
            pool = pool.sort_values(["rows", "fit", "size", "industry"], ascending=[False, True, False, True])
            group, source = pool["industry"].iloc[0], "chain"
        else:
            group, source = (fits[0], "exchange_fit") if fits else (f"({twse})", "exchange")
        tags = ["V2:" + f for f in fams["family"]] + list(mine.sort_values("rows", ascending=False)["industry"])
        out.append({"instrument": code, "industry_twse": twse, "group": group, "source": source,
                    "all_groups": "|".join(tags)})
    groups = pd.DataFrame(out).set_index("instrument")
    ids = {name: i for i, name in enumerate(sorted(groups["group"].unique()))}
    groups["group_id"] = groups["group"].map(ids)
    return groups


def _replace(df: pd.DataFrame, path: Path) -> None:
    """Write next to the target, then rename over it (a running loop may be reading it)."""
    tmp = path.with_suffix(".h5.tmp")
    df.to_hdf(tmp, key="data", mode="w")
    os.replace(tmp, path)


def apply_to_panels(groups: pd.DataFrame) -> None:
    """Swap $industry_id in the existing panels without a rebuild (prices and windows stay as scored)."""
    sid = pd.read_csv(ALPHA_DIR / "coder_map.csv", dtype={"instrument": str}).set_index("instrument")["sid"]
    by_code = groups["group_id"].astype(float)
    by_sid = by_code.rename(index=sid)
    for rel in PANELS:
        path = ALPHA_DIR / rel
        panel = pd.read_hdf(path, key="data")
        mapping = by_sid if rel.startswith("coder") else by_code
        new = panel.index.get_level_values("instrument").map(mapping).astype(float)
        changed = int((pd.Series(new, index=panel.index) != panel["$industry_id"]).sum())
        panel["$industry_id"] = new
        _replace(panel, path)
        print(f"  {rel}: $industry_id replaced ({changed:,} of {len(panel):,} rows changed)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--chain", default=str(CHAIN_CSV))
    parser.add_argument("--product_pdf", default=None, help="Parse the V2 member table into product_groups_v2.csv")
    parser.add_argument("--apply", action="store_true", help="Swap $industry_id in the alpha and coder panels")
    args = parser.parse_args()
    if args.product_pdf:
        products = parse_product_pdf(Path(args.product_pdf))
        products.to_csv(PRODUCT_CSV, index=False, encoding="utf-8")
        print(f"V2: {len(products)} memberships, {products['instrument'].nunique()} stocks, "
              f"{products['sub_group'].nunique()} sub-groups -> {PRODUCT_CSV}")
    meta = pd.read_csv(ALPHA_DIR / "instruments_meta.csv", dtype={"instrument": str}).set_index("instrument")
    groups = primary_groups(meta, load_chain(Path(args.chain)), load_products())
    groups.to_csv(GROUPS_CSV, encoding="utf-8")
    print(f"{len(groups)} stocks -> {groups['group'].nunique()} groups; source: {groups['source'].value_counts().to_dict()}")
    print("largest groups:", groups["group"].value_counts().head(12).to_dict())
    if args.apply:
        apply_to_panels(groups)
        meta["group"] = groups["group"]
        meta["industry_id"] = groups["group_id"]
        meta.to_csv(ALPHA_DIR / "instruments_meta.csv", encoding="utf-8")


if __name__ == "__main__":
    main()
