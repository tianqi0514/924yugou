"""Pure-memory typed equivalents for three preregistered synthetic QA chapters.

The fixtures transcribe information from one frozen fictional corpus. They do
not create new historical records, project evidence, or a second independent
business sample. The renderer actually consumes each typed field and a mutation
probe demonstrates whether the claimed replacement information matters.
"""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any

from .writing import _direct_min_rule, fits_decimal_places, format_number


SCHEMA_VERSION = "writing-equivalent/v1"
SUPPORTED: dict[str, set[int]] = {
    "S4": {4, 9, 10, 13, 15, 18, 27},
    "S7.1": {4, 9, 10, 27},
    "S5.2": {4, 18, 23, 27},
}
FIELD_BY_CATEGORY = {
    4: "outline", 9: "locator", 10: "slots", 13: "rule",
    15: "claim", 18: "evidence", 23: "conflict", 27: "template",
}
KIND_BY_CATEGORY = {
    4: "section_structure", 9: "source_locator", 10: "slot_schema",
    13: "project_rule", 15: "restricted_claim", 18: "evidence_boundary",
    23: "two_source_conflict", 27: "section_template",
}


class TypedContractError(ValueError):
    """A typed equivalent lacks required semantics or overstates evidence."""


_INTEGER_DISPLAY = {"kind": "number", "multiplier": "1", "decimals": 0, "thousands": True}
_YUAN_DISPLAY = {"kind": "number", "multiplier": "10000", "decimals": 0, "thousands": True}
_WAN_DISPLAY = {"kind": "number", "multiplier": "1", "decimals": 2, "thousands": True}
_CAUTION = "上述需求仅作为本项目测算输入，不能据此认定已有已签订单或最低采购承诺。"
_CONFLICT_DISCLOSURE = "历史参考资料对同一接入点的配电余量存在未解决分歧；该历史数据不作为本项目参数，本章节暂不作配电适配结论。"

# Fixed before experiments. These are deliberately typed information contracts,
# not copies of record IDs or YAML/JSON document payloads. Source identity is
# reported separately as an audit mapping and never inserted in a report.
QA_CONTRACTS: dict[str, dict[str, Any]] = {
    "S4": {
        "section_id": "S4",
        "outline": {"section_id": "S4", "title": "四、产能测算与交付匹配", "level": 1},
        "template": {"section_id": "S4", "title": "四、产能测算与交付匹配",
                     "level": 1, "paragraph_roles": ["sales", "caution"], "coverage_specified": False},
        "locator": {"section_id": "S4", "page": 6, "start": 4381, "end": 4487,
                    "word_part": "word/document.xml", "word_xpath": "/w:document/w:body/w:p[57]",
                    "source_kind": "historical_transcribed_locator"},
        "slots": {
            "N017": {"unit": "套", "data_type": "integer", "display": _INTEGER_DISPLAY},
            "N034": {"unit": "套", "data_type": "integer", "display": _INTEGER_DISPLAY},
            "N035": {"unit": "套", "data_type": "integer", "display": _INTEGER_DISPLAY},
        },
        "rule": {"operator": "min", "inputs": ["N017", "N034"], "target": "N035", "unit": "套"},
        "claim": {"policy": "demand_not_order", "sentence": _CAUTION,
                  "independently_verified": False},
        "evidence": {"sources": [{"id": "EV-01", "source_type": "embedded_internal_excerpt",
                                  "fictional": True,
                                  "original_document_available": False,
                                  "independently_verified": False}],
                     "can_prove_new_project": False},
    },
    "S7.1": {
        "section_id": "S7.1",
        "outline": {"section_id": "S7.1", "title": "7.1 编制范围及购置费", "level": 2},
        "template": {"section_id": "S7.1", "title": "7.1 编制范围及购置费",
                     "level": 2, "paragraph_roles": ["supplier_quote"], "coverage_specified": False},
        "locator": {"section_id": "S7.1", "page": 9, "start": 7195, "end": 7334,
                    "word_part": "word/document.xml", "word_xpath": "/w:document/w:body/w:p[91]",
                    "source_kind": "historical_transcribed_locator"},
        "slots": {
            "supplier_name": {"unit": "", "data_type": "text"},
            "N080": {"unit": "万元/条", "data_type": "decimal",
                     "yuan_display": _YUAN_DISPLAY, "wan_display": _WAN_DISPLAY},
        },
        "quote_boundary": {"scope_verified": False, "historical_price_not_inherited": True},
    },
    "S5.2": {
        "section_id": "S5.2",
        "outline": {"section_id": "S5.2", "title": "5.2 配电资料分歧与用能预算", "level": 2},
        "template": {"section_id": "S5.2", "title": "5.2 配电资料分歧与用能预算",
                     "level": 2, "paragraph_roles": ["conflict_disclosure"], "coverage_specified": False},
        "evidence": {"sources": [
            {"id": "EV-06A", "source_type": "embedded_internal_excerpt",
             "fictional": True,
             "original_document_available": False, "independently_verified": False},
            {"id": "EV-06B", "source_type": "embedded_internal_excerpt",
             "fictional": True,
             "original_document_available": False, "independently_verified": False},
        ], "can_prove_new_project": False},
        "conflict": {"status": "unresolved", "same_caliber": True,
                     "unit": "kW", "sources": [
                         {"evidence_id": "EV-06A", "value": "800"},
                         {"evidence_id": "EV-06B", "value": "650"},
                     ], "allows_adaptation_conclusion": False},
    },
}


def typed_fixture(section_id: str) -> dict:
    if section_id not in QA_CONTRACTS:
        raise TypedContractError("本章没有类型化 QA 契约")
    return deepcopy(QA_CONTRACTS[section_id])


def _validate_structure(contract: dict) -> tuple[str, list[str]]:
    section_id = contract.get("section_id")
    outline = contract.get("outline") or {}
    template = contract.get("template") or {}
    if (outline.get("section_id") != section_id or template.get("section_id") != section_id
            or not outline.get("title") or outline.get("title") != template.get("title")
            or outline.get("level") not in {1, 2, 3}
            or template.get("level") != outline.get("level")):
        raise TypedContractError("章节层级、标题或模板不一致")
    required_roles = {"S4": {"sales", "caution"}, "S7.1": {"supplier_quote"},
                      "S5.2": {"conflict_disclosure"}}[section_id]
    if not required_roles.issubset(set(template.get("paragraph_roles") or [])):
        raise TypedContractError("模板缺少本章必需段落角色")
    if template.get("coverage_specified") is not False:
        raise TypedContractError("不能虚构模板关键覆盖已配置")
    return str(outline["title"]), ["TEMPLATE_COVERAGE_UNSPECIFIED"]


def _validate_locator(contract: dict) -> None:
    locator = contract.get("locator") or {}
    if (locator.get("section_id") != contract["section_id"]
            or locator.get("source_kind") != "historical_transcribed_locator"
            or not isinstance(locator.get("page"), int) or locator["page"] <= 0
            or not isinstance(locator.get("start"), int)
            or not isinstance(locator.get("end"), int)
            or locator["start"] < 0 or locator["end"] <= locator["start"]
            or locator.get("word_part") != "word/document.xml"
            or not str(locator.get("word_xpath") or "").startswith("/w:document/w:body/")):
        raise TypedContractError("缺少可定位的来源区间")


def _validate_evidence(contract: dict, expected_ids: set[str]) -> None:
    evidence = contract.get("evidence") or {}
    sources = evidence.get("sources") or []
    if {item.get("id") for item in sources} != expected_ids or len(sources) != len(expected_ids):
        raise TypedContractError("历史证据来源不齐或身份重复")
    if evidence.get("can_prove_new_project") is not False:
        raise TypedContractError("历史摘录不能证明新项目")
    for source in sources:
        if (source.get("source_type") != "embedded_internal_excerpt"
                or source.get("fictional") is not True
                or source.get("original_document_available") is not False
                or source.get("independently_verified") is not False):
            raise TypedContractError("证据完整性或核实状态被错误提升")


def _bound(state: dict[str, dict], bindings: dict[str, str], slot_id: str, spec: dict) -> tuple[str, dict]:
    key = bindings.get(slot_id)
    fact = state.get(key or "")
    if (not key or not fact or fact.get("value") is None
            or fact.get("status") in {"UNDEFINED", "UNEVALUABLE"}):
        raise TypedContractError(f"槽位 {slot_id} 缺少有效项目事实")
    if fact.get("unit") != spec.get("unit") or fact.get("data_type") != spec.get("data_type"):
        raise TypedContractError(f"槽位 {slot_id} 的类型或单位不匹配")
    return key, fact


def _same_format(actual: dict, expected: dict) -> bool:
    return all(actual.get(key) == value for key, value in expected.items())


def render_typed(contract: dict, state: dict[str, dict], rules: list[Any],
                 bindings: dict[str, str]) -> dict:
    """Render with typed fields and current project state; never consult corpus rows."""
    section_id = contract.get("section_id")
    if section_id not in QA_CONTRACTS:
        raise TypedContractError("本章没有类型化 QA 契约")
    title, boundaries = _validate_structure(contract)
    values: dict[str, str] = {}
    if section_id in {"S4", "S7.1"}:
        _validate_locator(contract)
    if section_id == "S4":
        slots = contract.get("slots") or {}
        if set(slots) != {"N017", "N034", "N035"}:
            raise TypedContractError("首年销量槽位不完整")
        for slot_id in ("N017", "N034", "N035"):
            spec = slots[slot_id]
            if (spec.get("unit") != "套" or spec.get("data_type") != "integer"
                    or not _same_format(spec.get("display") or {}, _INTEGER_DISPLAY)):
                raise TypedContractError("首年销量的单位或显示格式不等价")
            _, fact = _bound(state, bindings, slot_id, spec)
            values[slot_id] = str(fact["value"])
        rule = contract.get("rule") or {}
        if (rule.get("operator") != "min" or rule.get("inputs") != ["N017", "N034"]
                or rule.get("target") != "N035" or rule.get("unit") != "套"):
            raise TypedContractError("类型化规则不是已审核的较小值规则")
        demand_key, capacity_key, sales_key = (bindings[slot] for slot in ("N017", "N034", "N035"))
        if (not _direct_min_rule(rules, sales_key, {demand_key, capacity_key})
                or state[sales_key].get("status") != "COMPUTED"):
            raise TypedContractError("当前项目没有已确认的 min 计算结果")
        demand, capacity, sales = (Decimal(values[slot]) for slot in ("N017", "N034", "N035"))
        if sales != min(demand, capacity):
            raise TypedContractError("项目计算结果与 min 规则不一致")
        claim = contract.get("claim") or {}
        if (claim.get("policy") != "demand_not_order" or claim.get("sentence") != _CAUTION
                or claim.get("independently_verified") is not False):
            raise TypedContractError("缺少‘需求不是订单’的受限提醒")
        _validate_evidence(contract, {"EV-01"})
        shown_d = format_number(demand, slots["N017"]["display"])
        shown_c = format_number(capacity, slots["N034"]["display"])
        shown_s = format_number(sales, slots["N035"]["display"])
        body = f"首年客户需求{shown_d}套，规划合格能力{shown_c}套；按两者较小值，首年计划销售量为{shown_s}套。"
        text = "\n".join((title, body, claim["sentence"]))
        boundaries.extend(["HISTORICAL_EVIDENCE_INCOMPLETE", "HISTORICAL_CONTEXT_EXCLUDED"])
        if demand >= capacity:
            boundaries.append("HISTORICAL_CONDITION_FALSE")
    elif section_id == "S7.1":
        slots = contract.get("slots") or {}
        if set(slots) != {"supplier_name", "N080"}:
            raise TypedContractError("设备报价槽位不完整")
        supplier_spec, price_spec = slots["supplier_name"], slots["N080"]
        if (supplier_spec.get("unit") != "" or supplier_spec.get("data_type") != "text"
                or price_spec.get("unit") != "万元/条" or price_spec.get("data_type") != "decimal"
                or not _same_format(price_spec.get("yuan_display") or {}, _YUAN_DISPLAY)
                or not _same_format(price_spec.get("wan_display") or {}, _WAN_DISPLAY)):
            raise TypedContractError("设备报价槽位的类型、单位或格式不等价")
        _, supplier = _bound(state, bindings, "supplier_name", supplier_spec)
        _, price = _bound(state, bindings, "N080", price_spec)
        name = str(supplier["value"]).strip()
        amount = Decimal(str(price["value"]))
        if not name or not fits_decimal_places(amount, 2):
            raise TypedContractError("供应商或报价精度不适合当前段落")
        quote_boundary = contract.get("quote_boundary") or {}
        if (quote_boundary.get("scope_verified") is not False
                or quote_boundary.get("historical_price_not_inherited") is not True):
            raise TypedContractError("不能继承历史报价范围或采购判断")
        shown_yuan = format_number(amount, price_spec["yuan_display"])
        shown_wan = format_number(amount, price_spec["wan_display"])
        body = f"本项目设备供应商为{name}，自动焊接线单价为{shown_yuan}元/条，折合{shown_wan}万元/条。"
        text = "\n".join((title, body))
        values = {"supplier_name": name, "N080": str(price["value"])}
        boundaries.append("QUOTE_SCOPE_UNVERIFIED")
        if name == "禾进装备":
            boundaries.append("HISTORICAL_NAME_REUSED")
    else:
        _validate_evidence(contract, {"EV-06A", "EV-06B"})
        conflict = contract.get("conflict") or {}
        sources = conflict.get("sources") or []
        if (conflict.get("status") != "unresolved" or conflict.get("same_caliber") is not True
                or conflict.get("unit") != "kW" or conflict.get("allows_adaptation_conclusion") is not False
                or len(sources) != 2
                or {source.get("evidence_id") for source in sources} != {"EV-06A", "EV-06B"}
                or {source.get("value") for source in sources} != {"800", "650"}):
            raise TypedContractError("双来源配电冲突不完整或被错误解决")
        text = "\n".join((title, _CONFLICT_DISCLOSURE))
        boundaries.extend(["HISTORICAL_EVIDENCE_INCOMPLETE", "HISTORICAL_CONFLICT",
                           "POWER_CONFLICT_UNRESOLVED"])
    return {"text": text, "fact_values": values, "boundary_codes": sorted(set(boundaries)),
            "typed_source_refs": [{"kind": "typed_qa_contract", "section_id": section_id}],
            "project_facts_only": True}


def _negative_mutation(contract: dict, category_number: int) -> str:
    if category_number == 4:
        contract["outline"]["section_id"] = "S-INVALID"
        return "章节归属被改错"
    if category_number == 9:
        contract["locator"]["end"] = contract["locator"]["start"]
        return "来源区间为空"
    if category_number == 10:
        first = next(iter(contract["slots"]))
        contract["slots"][first]["unit"] = "台"
        return "槽位单位变为台"
    if category_number == 13:
        contract["rule"]["operator"] = "max"
        return "min 改为 max"
    if category_number == 15:
        contract["claim"]["sentence"] = "预测需求已成为签约订单。"
        return "需求被改写为订单"
    if category_number == 18:
        contract["evidence"]["sources"][0]["original_document_available"] = True
        return "仅摘录资料被宣称有完整原件"
    if category_number == 23:
        contract["conflict"]["sources"][1]["value"] = "800"
        return "双来源数值被改为相同"
    if category_number == 27:
        contract["template"]["paragraph_roles"] = []
        return "模板缺少段落角色"
    raise TypedContractError("此类别尚无可执行的负例")


def _baseline_boundary_codes(section_id: str, baseline: dict) -> list[str]:
    allowed = {
        "S4": {"TEMPLATE_COVERAGE_UNSPECIFIED", "HISTORICAL_EVIDENCE_INCOMPLETE",
               "HISTORICAL_CONTEXT_EXCLUDED", "HISTORICAL_CONDITION_FALSE"},
        "S7.1": {"TEMPLATE_COVERAGE_UNSPECIFIED", "QUOTE_SCOPE_UNVERIFIED",
                 "HISTORICAL_NAME_REUSED"},
        "S5.2": {"TEMPLATE_COVERAGE_UNSPECIFIED", "HISTORICAL_EVIDENCE_INCOMPLETE",
                 "HISTORICAL_CONFLICT", "POWER_CONFLICT_UNRESOLVED"},
    }[section_id]
    return sorted({item.get("code") for item in baseline.get("issues", []) if item.get("code") in allowed})


def _projection_matches(section_id: str, category_number: int, contract: dict,
                        payloads: list[dict]) -> bool:
    """Compare typed facts with the frozen rows whose IDs the experiment maps."""
    if not payloads or any(not isinstance(item, dict) for item in payloads):
        return False
    if category_number in {4, 27}:
        field = contract[FIELD_BY_CATEGORY[category_number]]
        source = payloads[0]
        source_section_id = source.get("id") if category_number == 4 else source.get("section_id")
        return (len(payloads) == 1 and source_section_id == field.get("section_id")
                and source.get("title") == field.get("title")
                and source.get("level") == field.get("level"))
    if category_number == 9:
        source = payloads[0]
        locator = contract["locator"]
        return (len(payloads) == 1 and source.get("subsection") == section_id
                and source.get("page") == locator["page"]
                and source.get("span") == [locator["start"], locator["end"]]
                and (source.get("source_locator") or {}).get("part") == locator["word_part"]
                and (source.get("source_locator") or {}).get("xpath") == locator["word_xpath"])
    if category_number == 10:
        mentions = payloads[0].get("node_slots") or []
        slots = contract["slots"]
        if section_id == "S4":
            return (len(payloads) == 1 and payloads[0].get("id") == "C0052"
                    and {item.get("slot") for item in mentions} == {"N017", "N035"}
                    and all(_same_format(item.get("format") or {}, slots[item["slot"]]["display"])
                            for item in mentions))
        return (len(payloads) == 1 and len(mentions) == 2 and payloads[0].get("id") == "C0083"
                and [item.get("slot") for item in mentions] == ["N080", "N080"]
                and _same_format(mentions[0].get("format") or {}, slots["N080"]["yuan_display"])
                and _same_format(mentions[1].get("format") or {}, slots["N080"]["wan_display"]))
    if category_number == 13:
        source = payloads[0]
        rule = contract["rule"]
        return (len(payloads) == 1 and source.get("id") == "R006"
                and source.get("target") == rule["target"]
                and source.get("deps") == rule["inputs"]
                and source.get("expr") == "min(N017,N034)")
    if category_number == 15:
        source = payloads[0]
        return (len(payloads) == 1 and source.get("id") == "L002"
                and source.get("pattern") == "客户预测不能改写为已签订单或最低采购承诺。"
                and source.get("status") == "source_asserted_not_independently_verified"
                and contract["claim"]["independently_verified"] is False)
    if category_number == 18:
        expected = {item["id"] for item in contract["evidence"]["sources"]}
        return (len(payloads) == len(expected)
                and {item.get("id") for item in payloads} == expected
                and all(item.get("source_type") == "embedded_internal_excerpt"
                        and item.get("fictional") is True
                        and item.get("original_document_available") is False for item in payloads))
    if category_number == 23:
        source = payloads[0]
        conflict = contract["conflict"]
        return (len(payloads) == 1 and source.get("id") == "X001"
                and source.get("type") == "same_caliber_different_value"
                and source.get("status") == "open" and source.get("resolution") is None
                and set(source.get("evidence") or []) == {item["evidence_id"] for item in conflict["sources"]}
                and "800kW" in str(source.get("what")) and "650kW" in str(source.get("what")))
    return False


def compare_equivalent(section_id: str, category_number: int, baseline: dict,
                       state: dict[str, dict], rules: list[Any], bindings: dict[str, str],
                       source_record_ids: list[str], source_payloads: list[dict]) -> dict:
    """Compare an actual candidate to a consumed typed contract and its negative probe."""
    category_id = f"CAT-{category_number:02d}"
    if section_id not in SUPPORTED or category_number not in SUPPORTED[section_id]:
        return {"category_id": category_id, "status": "not_supported",
                "reason": "本章没有该类别的可执行等价信息适配", "before_text": baseline["text"],
                "after_text": "", "used_records": [], "contract": None, "checks": None}
    if baseline["status"] != "ready" or not source_record_ids:
        return {"category_id": category_id, "status": "not_supported",
                "reason": "当前候选未形成，或本章未实际消费这类记录", "before_text": baseline["text"],
                "after_text": "", "used_records": source_record_ids, "contract": None, "checks": None}
    contract = typed_fixture(section_id)
    field_name = FIELD_BY_CATEGORY[category_number]
    try:
        rendered = render_typed(contract, state, rules, bindings)
        mutated = deepcopy(contract)
        mutation = _negative_mutation(mutated, category_number)
        try:
            altered = render_typed(mutated, state, rules, bindings)
            negative_passed = altered["text"] != rendered["text"] or (
                altered["fact_values"] != rendered["fact_values"]
                or altered["boundary_codes"] != rendered["boundary_codes"])
            negative_result = "changed" if negative_passed else "same"
        except TypedContractError:
            negative_passed, negative_result = True, "blocked"
    except (TypedContractError, KeyError, TypeError, ValueError) as exc:
        return {"category_id": category_id, "status": "blocked", "reason": str(exc),
                "before_text": baseline["text"], "after_text": "", "used_records": source_record_ids,
                "contract": None, "checks": None}
    relevant_slots = {"S4": ("N017", "N034", "N035"),
                      "S7.1": ("supplier_name", "N080"), "S5.2": ()}[section_id]
    expected_values = {slot_id: str(state[bindings[slot_id]]["value"]) for slot_id in relevant_slots}
    project_values_equal = rendered["fact_values"] == expected_values
    text_equal = rendered["text"] == baseline["text"]
    boundary_codes = _baseline_boundary_codes(section_id, baseline)
    boundaries_equal = rendered["boundary_codes"] == boundary_codes
    source_information_equal = _projection_matches(section_id, category_number, contract, source_payloads)
    checks = {"text_equal": text_equal, "project_values_equal": project_values_equal,
              "boundaries_equal": boundaries_equal, "legacy_refs_reused": False,
              "source_information_equal": source_information_equal,
              "negative_probe_passed": negative_passed}
    equivalent = (text_equal and project_values_equal and boundaries_equal
                  and source_information_equal and negative_passed)
    return {
        "category_id": category_id, "status": "equivalent" if equivalent else "changed",
        "reason": ("同等信息在类型化 QA 契约中被实际消费；只能说明原文件类别不是唯一承载形式"
                   if equivalent else "类型化信息与当前候选的正文、数值或边界不一致"),
        "before_text": baseline["text"], "after_text": rendered["text"],
        "used_records": source_record_ids,
        "contract": {"schema_version": SCHEMA_VERSION, "kind": KIND_BY_CATEGORY[category_number],
                     "fields": deepcopy(contract[field_name]), "validation": {
                         "negative_mutation": mutation, "negative_result": negative_result,
                         "baseline_boundary_codes": boundary_codes,
                         "typed_boundary_codes": rendered["boundary_codes"],
                     }, "source_record_ids": source_record_ids,
                     "replacement_scope": "full_chapter_typed_reconstruction_with_target_mutation",
                     "information_origin": "same_frozen_fictional_corpus",
                     "replacement_source": ("project_rule_contract" if category_number == 13 else
                                            "historical_information_transcribed_for_synthetic_QA"),
                     "simulation_only": True},
        "checks": checks,
    }
