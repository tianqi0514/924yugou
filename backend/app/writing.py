"""章节级写作适配验证：只读取冻结语料结构，用项目事实生成安全候选。"""

from __future__ import annotations

import ast
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


SLOTS = (
    {"id": "N017", "label": "首年客户需求", "data_type": "integer", "unit": "套", "suggested_key": "first_year_demand", "chunk_id": "C0052"},
    {"id": "N034", "label": "合格能力", "data_type": "integer", "unit": "套", "suggested_key": "qualified_capacity", "chunk_id": "C0052"},
    {"id": "N035", "label": "首年计划销售量", "data_type": "integer", "unit": "套", "suggested_key": "planned_sales", "chunk_id": "C0052"},
    {"id": "supplier_name", "label": "本项目设备供应商", "data_type": "text", "unit": "", "suggested_key": "supplier_name", "chunk_id": "C0083", "origin": "历史正文未绑定的专有名称，必须由本项目提供"},
    {"id": "N080", "label": "自动焊接线购置单价", "data_type": "decimal", "unit": "万元/条", "suggested_key": "equipment_unit_price", "chunk_id": "C0083"},
)
SLOT_BY_ID = {item["id"]: item for item in SLOTS}
CASE_IDS = ("C0052", "C0083")


def source_cases(corpus: Any) -> list[dict]:
    """从真实切块和骨架读取验证源，不改写语料内容。"""
    chunks = {item["id"]: item for item in corpus.parsed("chunks/chunks.jsonl") if item["id"] in CASE_IDS}
    skeletons = {item["id"]: item for item in corpus.parsed("chunks/skeletons.jsonl") if item["id"] in CASE_IDS}
    return [{
        "chunk_id": chunk_id,
        "title": "首年需求与销售" if chunk_id == "C0052" else "设备单价与供应商",
        "section": chunks[chunk_id]["section"],
        "page": chunks[chunk_id]["page"],
        "span": chunks[chunk_id]["span"],
        "original_text": chunks[chunk_id]["text"],
        "skeleton": skeletons[chunk_id]["skeleton_md"],
        "mentions": skeletons[chunk_id]["node_slots"],
        "reuse_note": skeletons[chunk_id]["reuse_note"],
    } for chunk_id in CASE_IDS]


def format_number(value: Decimal, specification: dict) -> str:
    """遵循原 mention 的倍率、千分位和小数位；仅负责显示。"""
    scaled = value * Decimal(str(specification.get("multiplier", "1")))
    decimals = int(specification.get("decimals", 0))
    quantum = Decimal(1).scaleb(-decimals)
    rounded = scaled.quantize(quantum, rounding=ROUND_HALF_UP)
    return f"{rounded:,.{decimals}f}" if specification.get("thousands") else f"{rounded:.{decimals}f}"


def _direct_min_rule(rules: list[Any], target: str, inputs: set[str]) -> bool:
    for rule in rules:
        if rule.target_key != target:
            continue
        try:
            expression = ast.parse(rule.expression, mode="eval").body
        except SyntaxError:
            continue
        if (isinstance(expression, ast.Call) and isinstance(expression.func, ast.Name)
                and expression.func.id == "min" and len(expression.args) == 2
                and not expression.keywords and all(isinstance(arg, ast.Name) for arg in expression.args)
                and {arg.id for arg in expression.args} == inputs):
            return True
    return False


def render_cases(state: dict[str, dict], rules: list[Any], bindings: dict[str, str], sources: list[dict]) -> dict:
    """从项目事实快照生成两个可追溯候选；缺值或条件失效不复用历史句子。"""
    by_id = {source["chunk_id"]: source for source in sources}
    issues: list[dict] = []
    blocks: list[dict] = []

    def issue(chunk_id: str, code: str, message: str, severity: str = "review", slot_id: str | None = None) -> None:
        issues.append({"chunk_id": chunk_id, "code": code, "message": message, "severity": severity, "slot_id": slot_id})

    def bound(slot_id: str, chunk_id: str) -> dict | None:
        key = bindings.get(slot_id)
        if not key:
            issue(chunk_id, "UNBOUND_SLOT", f"{SLOT_BY_ID[slot_id]['label']}尚未映射到项目事实", "block", slot_id)
            return None
        fact = state.get(key)
        if not fact or fact["value"] is None or fact["status"] in ("UNDEFINED", "UNEVALUABLE"):
            issue(chunk_id, "UNDEFINED_FACT", f"{SLOT_BY_ID[slot_id]['label']}尚无可用的项目值", "block", slot_id)
            return None
        return fact

    demand = bound("N017", "C0052")
    capacity = bound("N034", "C0052")
    sales = bound("N035", "C0052")
    sales_refs = []
    sales_text = "首年需求、合格能力或计划销售量尚未齐备，暂不能形成首年销售结论。"
    if demand and capacity and sales:
        demand_key, capacity_key, sales_key = (bindings[item] for item in ("N017", "N034", "N035"))
        if not _direct_min_rule(rules, sales_key, {demand_key, capacity_key}) or sales["status"] != "COMPUTED":
            issue("C0052", "RULE_NOT_CONFIRMED", "计划销售量必须由已确认的 min(需求, 合格能力) 项目规则计算", "block", "N035")
        else:
            d, c, s = (Decimal(item["value"]) for item in (demand, capacity, sales))
            if s != min(d, c):
                issue("C0052", "RULE_RESULT_MISMATCH", "项目计划销售量与较小值规则不一致", "block", "N035")
            else:
                mentions = {item["slot"]: item for item in by_id["C0052"]["mentions"]}
                shown_d = format_number(d, mentions["N017"]["format"])
                shown_c = format_number(c, mentions["N017"]["format"])
                shown_s = format_number(s, mentions["N035"]["format"])
                sales_text = f"首年客户需求{shown_d}套，规划合格能力{shown_c}套；按两者较小值，首年计划销售量为{shown_s}套。"
                sales_refs = [
                    {"slot_id": "N017", "fact_key": demand_key, "display": shown_d, "mention_id": mentions["N017"]["mention_id"]},
                    {"slot_id": "N034", "fact_key": capacity_key, "display": shown_c, "mention_id": None},
                    {"slot_id": "N035", "fact_key": sales_key, "display": shown_s, "mention_id": mentions["N035"]["mention_id"]},
                ]
                if d >= c:
                    issue("C0052", "HISTORICAL_CONDITION_FALSE", "历史句子“需求低于能力”在本项目不成立；已排除该句，需复核新的业务判断")
    issue("C0052", "HISTORICAL_CONTEXT_EXCLUDED", "历史“爬坡率”和设备可用时长叙述未在本项目核对，候选正文未继承", "info")
    blocks.append({"id": "validation-C0052", "chunk_id": "C0052", "title": by_id["C0052"]["title"], "text": sales_text, "references": sales_refs})

    supplier = bound("supplier_name", "C0083")
    price = bound("N080", "C0083")
    quote_refs = []
    quote_text = "设备供应商或单价尚未齐备，暂不能形成报价段落。"
    if supplier and price:
        name = str(supplier["value"]).strip()
        amount = Decimal(price["value"])
        mentions = [item for item in by_id["C0083"]["mentions"] if item["slot"] == "N080"]
        if not name:
            issue("C0083", "EMPTY_SUPPLIER", "本项目供应商名称不能为空", "block", "supplier_name")
        elif amount != amount.quantize(Decimal("0.01")):
            issue("C0083", "FORMAT_LOSS", "单价超过两位小数，按历史 mention 格式显示会丢失精度", "block", "N080")
        else:
            yuan = format_number(amount, mentions[0]["format"])
            wan = format_number(amount, mentions[1]["format"])
            quote_text = f"本项目设备供应商为{name}，自动焊接线单价为{yuan}元/条，折合{wan}万元/条。"
            quote_refs = [
                {"slot_id": "supplier_name", "fact_key": bindings["supplier_name"], "display": name, "mention_id": None},
                {"slot_id": "N080", "fact_key": bindings["N080"], "display": yuan, "mention_id": mentions[0]["mention_id"]},
                {"slot_id": "N080", "fact_key": bindings["N080"], "display": wan, "mention_id": mentions[1]["mention_id"]},
            ]
            if name == "禾进装备":
                issue("C0083", "HISTORICAL_NAME_REUSED", "输入的供应商与历史语料相同，请确认是本项目真实来源")
    issue("C0083", "QUOTE_SCOPE_UNVERIFIED", "历史报价包含范围和有效期尚未由本项目证据确认，候选正文未继承", "review")
    blocks.append({"id": "validation-C0083", "chunk_id": "C0083", "title": by_id["C0083"]["title"], "text": quote_text, "references": quote_refs})

    return {"blocks": blocks, "issues": issues, "blocked": any(item["severity"] == "block" for item in issues)}
