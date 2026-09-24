"""A conservative, database-backed writing package over the frozen corpus.

Only structural patterns leave this module as candidate input. Historical prose,
numbers, supplier names, and evidence excerpts remain review material.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .corpus_storage import CorpusArchive, CorpusRecord
from .db import ProjectFact, RuleRecord, WritingBinding, WritingReference
from .report_pipeline import model_section
from .writing import SLOTS, render_cases


GUIDED_SECTIONS = {"S4": "C0052", "S7.1": "C0083", "S5.2": None}


def accepted_reference(session: Session, project_id: str) -> tuple[WritingReference, CorpusArchive]:
    reference = session.get(WritingReference, project_id)
    if reference is None:
        raise ValueError("请先选择只读参考语料")
    archive = session.get(CorpusArchive, reference.corpus_id)
    if archive is None or archive.version != reference.corpus_version:
        raise ValueError("所选语料版本不可用，请重新选择")
    return reference, archive


def sections(session: Session, corpus_id: str) -> list[dict]:
    rows = session.scalars(select(CorpusRecord).where(
        CorpusRecord.corpus_id == corpus_id, CorpusRecord.category_number == 27
    ).order_by(CorpusRecord.ordinal)).all()
    output = []
    for row in rows:
        data = row.payload
        section_id = str(data.get("section_id") or "")
        guided = section_id in GUIDED_SECTIONS
        output.append({
            "id": section_id, "title": data.get("title", section_id),
            "level": data.get("level"), "reusable": bool(data.get("reusable")),
            "chunk_count": len(data.get("paragraph_order") or []),
            "guided_available": guided, "model_available": guided and section_id != "S5.2",
            "availability_reason": ("只支持历史冲突的预审披露，正式交付受阻" if section_id == "S5.2" else
                                    "已有项目事实映射与受限成稿校验" if guided else
                                    "尚未完成本章的项目事实映射和写作验证；目前可查看结构与来源"),
        })
    return output


def _record(session: Session, corpus_id: str, category: int, semantic_id: str) -> CorpusRecord | None:
    return session.scalar(select(CorpusRecord).where(
        CorpusRecord.corpus_id == corpus_id,
        CorpusRecord.category_number == category,
        CorpusRecord.semantic_id == semantic_id,
    ).order_by(CorpusRecord.ordinal))


def _item(row: CorpusRecord, role: str, decision: str, summary: str, reason: str) -> dict:
    return {
        "item_id": row.record_id,
        "category_id": f"CAT-{row.category_number:02d}",
        "category_number": row.category_number,
        "artifact_id": row.artifact_id,
        "record_id": row.record_id,
        "semantic_id": row.semantic_id,
        "role": role,
        "decision": decision,
        "review_status": "historical_unverified" if decision != "selectable" else "pattern_pending_project_review",
        "reason": reason,
        "location": row.location,
        "summary": summary,
    }


def _case_sources(session: Session, corpus_id: str) -> list[dict]:
    result = []
    for chunk_id, title in (("C0052", "首年需求与销售"), ("C0083", "设备单价与供应商")):
        chunk = _record(session, corpus_id, 9, chunk_id)
        skeleton = _record(session, corpus_id, 10, chunk_id)
        if chunk is None or skeleton is None:
            raise ValueError(f"语料缺少写作验证切块 {chunk_id}")
        result.append({"chunk_id": chunk_id, "title": title,
                       "section": chunk.payload["section"], "page": chunk.payload["page"],
                       "span": chunk.payload["span"], "original_text": chunk.payload["text"],
                       "skeleton": skeleton.payload["skeleton_md"],
                       "mentions": skeleton.payload["node_slots"],
                       "reuse_note": skeleton.payload["reuse_note"]})
    return result


def _inline_facts(text: str, references: list[dict]) -> list[dict]:
    """Bind each deterministic display to the exact project fact, in display order."""
    children: list[dict] = []
    cursor = 0
    for reference in references:
        display = str(reference["display"])
        found = text.find(display, cursor)
        if found < 0:
            raise ValueError("候选事实文字与引用位置不一致")
        if found > cursor:
            children.append({"text": text[cursor:found]})
        children.append({"type": "fact_ref", "fact_key": reference["fact_key"],
                         "display": display, "children": [{"text": ""}]})
        cursor = found + len(display)
    if cursor < len(text):
        children.append({"text": text[cursor:]})
    return children or [{"text": text}]


def _model_fact_references(text: str, keys: list[str], state: dict[str, dict]) -> list[dict]:
    """Locate model mentions using this project's values, never historical displays.

    Commas may differ from the saved decimal string. Each cited fact needs its
    own non-overlapping occurrence, including equal-valued input and result
    facts. Other numeric transformations remain unsupported here.
    """
    number_pattern = re.compile(r"(?<![\d.,])\d[\d,]*(?:\.\d+)?(?![\d.,])")
    spans: list[tuple[int, int, str]] = []
    for key in keys:
        fact = state.get(key)
        if fact is None or fact.get("value") is None:
            raise ValueError("模型候选引用了不可用的项目事实")
        value = str(fact["value"])
        if fact.get("data_type") in {"integer", "decimal"}:
            matches = [(match.start(), match.end(), match.group()) for match in number_pattern.finditer(text)
                       if match.group().replace(",", "") == value
                       and ("," not in match.group() or re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", match.group()))]
        else:
            matches = [(match.start(), match.end(), value) for match in re.finditer(re.escape(value), text)]
        available = next(((start, end, display) for start, end, display in matches
                          if all(end <= old_start or start >= old_end for old_start, old_end, _ in spans)), None)
        if available is None:
            raise ValueError(f"模型候选未保留所引用事实的可定位值：{key}")
        spans.append((*available[:2], key))
    return [{"fact_key": key, "display": text[start:end]}
            for start, end, key in sorted(spans)]


def build_candidate(session: Session, pack: dict, state: dict[str, dict], rules: list[RuleRecord],
                    bindings: dict[str, str], approved_item_ids: list[str], mode: str) -> dict:
    """Make a reviewable candidate; never substitute historical values into a project."""
    section_id = pack["section"]["id"]
    if section_id not in GUIDED_SECTIONS:
        raise ValueError("本章尚未完成写作映射与验证")
    if mode not in {"guided", "model"} or (mode == "model" and section_id == "S5.2"):
        raise ValueError("本章不支持所选起草方式")
    if len(approved_item_ids) != len(set(approved_item_ids)):
        raise ValueError("请勿重复选择写作包内容")
    by_id = {item["item_id"]: item for item in pack["items"]}
    if any(item_id not in by_id or by_id[item_id]["decision"] != "selectable" for item_id in approved_item_ids):
        raise ValueError("只能选择本章可复用的结构或受限模式")
    approved = set(approved_item_ids)
    corpus_id, version = pack["corpus_id"], pack["corpus_version"]

    def item(category: int, semantic_id: str) -> dict | None:
        return next((entry for entry in pack["items"] if entry["category_number"] == category and
                     entry["semantic_id"] == semantic_id), None)

    def ref(entry: dict, use: str) -> dict:
        return {**source_ref(corpus_id, version, entry), "use": use}

    structural = [entry for entry in (item(4, section_id), item(27, section_id)) if entry]
    if any(entry["item_id"] not in approved for entry in structural):
        raise ValueError("请先确认本章的目录与模板结构")
    heading_refs = [ref(entry, "section_structure") for entry in structural]
    heading = {"type": "h2", "id": str(uuid4()), "section_id": section_id,
               "source_refs": heading_refs, "origin": "guided",
               "children": [{"text": str(pack["section"]["title"])}]}

    issues = [dict(problem) for problem in pack["issues"] if problem["code"] != "PROJECT_FACT_MISSING"]
    output = [heading]
    used = {entry["item_id"] for entry in structural}
    if section_id == "S5.2":
        conflict = item(23, "X001")
        evidences = [item(18, key) for key in ("EV-06A", "EV-06B")]
        if conflict is None or any(entry is None for entry in evidences):
            raise ValueError("配电冲突的双来源未齐备，不能形成披露")
        limitation_refs = [ref(conflict, "historical_conflict_disclosure"),
                           *(ref(entry, "historical_evidence_only") for entry in evidences)]
        output.append({"type": "p", "id": str(uuid4()), "section_id": section_id,
                       "source_refs": limitation_refs, "origin": "guided",
                       "children": [{"text": "历史参考资料对同一接入点的配电余量存在未解决分歧；该历史数据不作为本项目参数，本章节暂不作配电适配结论。"}]})
        used.update([conflict["item_id"], *(entry["item_id"] for entry in evidences)])
    else:
        chunk_id = GUIDED_SECTIONS[section_id]
        skeleton = item(10, chunk_id)
        raw_chunk = item(9, chunk_id)
        if skeleton is None or raw_chunk is None or skeleton["item_id"] not in approved:
            raise ValueError("请先确认本章切块的可复用槽位")
        main_refs = [ref(raw_chunk, "source_location_only"), ref(skeleton, "slot_format_only")]
        used.update({raw_chunk["item_id"], skeleton["item_id"]})
        if section_id == "S4":
            rule = item(13, "R006")
            claim = item(15, "L002")
            if rule is None or claim is None or rule["item_id"] not in approved or claim["item_id"] not in approved:
                raise ValueError("请先确认 min 规则与‘需求不是订单’的受限写法")
            main_refs.append(ref(rule, "rule_pattern_only"))
            used.add(rule["item_id"])
        result = render_cases(state, rules, bindings, _case_sources(session, corpus_id))
        block = next(entry for entry in result["blocks"] if entry["chunk_id"] == chunk_id)
        case_issues = [entry for entry in result["issues"] if entry["chunk_id"] == chunk_id]
        blockers = [entry for entry in case_issues if entry["severity"] == "block"]
        if blockers or not block["references"]:
            raise ValueError("项目事实或规则尚不足以起草：" + "；".join(entry["message"] for entry in blockers))
        issues.extend(case_issues)
        fact_keys = list(dict.fromkeys(entry["fact_key"] for entry in block["references"]))
        if mode == "guided":
            body_texts = [(block["text"], block["references"], fact_keys)]
        else:
            selected_facts = []
            for key in fact_keys:
                project_fact = state[key]
                selected_facts.append({"key": key, "label": project_fact["label"],
                                       "value": project_fact["value"], "unit": project_fact["unit"],
                                       "source": project_fact["source"]})
            generated = model_section(str(pack["section"]["title"]), selected_facts)
            body_texts = []
            for paragraph in generated:
                text = str(paragraph["children"][0]["text"])
                refs = _model_fact_references(text, paragraph["fact_keys"], state)
                if "禾进装备" in text and state.get(bindings.get("supplier_name", ""), {}).get("value") != "禾进装备":
                    raise ValueError("模型候选残留历史供应商名称")
                if section_id == "S4" and ("已签订单" in text or "需求低于能力" in text):
                    raise ValueError("模型候选包含未核实订单或失效的条件句")
                body_texts.append((text, refs, paragraph["fact_keys"]))
        if mode == "model" and not set(fact_keys).issubset({key for _, _, paragraph_keys in body_texts
                                                            for key in paragraph_keys}):
            raise ValueError("模型候选未覆盖本章必需的项目事实")
        for text, references, keys in body_texts:
            paragraph = {"type": "p", "id": str(uuid4()), "section_id": section_id,
                         "source_refs": ([entry for entry in main_refs if entry.get("semantic_id") != "R006"]
                                         if section_id == "S4" and bindings["N035"] not in keys else main_refs),
                         "origin": mode, "fact_keys": keys,
                         "children": _inline_facts(text, references)}
            if section_id == "S4" and bindings["N035"] in keys:
                target_key = bindings["N035"]
                project_rule = next((rule for rule in rules if rule.target_key == target_key), None)
                if project_rule is None:
                    raise ValueError("当前项目缺少计划销售量规则")
                paragraph["project_rule_refs"] = [{
                    "rule_id": project_rule.id, "expression": project_rule.expression,
                    "target_key": project_rule.target_key, "deps": list(project_rule.deps),
                    "input_fact_revisions": [{"fact_key": key, "revision": state[key]["revision"],
                                              "value": str(state[key]["value"])} for key in project_rule.deps],
                    "target_fact_revision": state[target_key]["revision"],
                }]
            output.append(paragraph)
        if section_id == "S4":
            evidence = item(18, "EV-01")
            caution_refs = [ref(claim, "historical_claim_pattern_only")]
            if evidence is not None:
                caution_refs.append(ref(evidence, "historical_evidence_limitation"))
                used.add(evidence["item_id"])
            output.append({"type": "p", "id": str(uuid4()), "section_id": section_id,
                           "source_refs": caution_refs, "origin": "guided",
                           "children": [{"text": "上述需求仅作为本项目测算输入，不能据此认定已有已签订单或最低采购承诺。"}]})
            used.add(claim["item_id"])

    rejected = [{"item_id": entry["item_id"], "reason": (
                    "已勾选，但本次候选未消费" if entry["item_id"] in approved else
                    "操作者未选择" if entry["decision"] == "selectable" else entry["reason"]),
                 "decision": entry["decision"]}
                for entry in pack["items"] if entry["item_id"] not in used]
    return {"paragraphs": output, "issues": issues, "used_items": sorted(used),
            "rejected_items": rejected,
            "source_refs": [{"block_id": block["id"], "section_id": section_id,
                             "refs": block["source_refs"],
                             "project_rule_refs": block.get("project_rule_refs", [])} for block in output]}


def source_ref(corpus_id: str, version: str, item: dict) -> dict:
    return {key: value for key, value in {
        "corpus_id": corpus_id,
        "corpus_version": version,
        "category_id": item["category_id"],
        "artifact_id": item["artifact_id"],
        "record_id": item["record_id"],
        "semantic_id": item["semantic_id"],
    }.items() if value is not None}


def package(session: Session, project_id: str, section_id: str) -> dict:
    reference, archive = accepted_reference(session, project_id)
    template = _record(session, archive.id, 27, section_id)
    outline = _record(session, archive.id, 4, section_id)
    if template is None or outline is None:
        raise ValueError("章节不存在")

    items: list[dict] = []
    seen: set[str] = set()

    def append(row: CorpusRecord | None, role: str, decision: str, summary: str, reason: str) -> None:
        if row is not None and row.record_id not in seen:
            items.append(_item(row, role, decision, summary, reason))
            seen.add(row.record_id)

    append(outline, "outline", "selectable", f"{section_id} 的层级、段落与覆盖位置", "仅借用章节位置，不承认历史结论")
    append(template, "template", "selectable" if template.payload.get("reusable") else "review_only",
           f"{section_id} 的章节顺序与表格结构", "must_cover 与字数预算为空，需由当前项目核对")
    chunk_ids = [str(entry.get("chunk_id")) for entry in template.payload.get("paragraph_order", []) if entry.get("chunk_id")]
    node_ids: set[str] = set()
    for chunk_id in dict.fromkeys(chunk_ids):
        raw = _record(session, archive.id, 9, chunk_id)
        skeleton = _record(session, archive.id, 10, chunk_id)
        if raw is not None:
            node_ids.update(str(value) for value in raw.payload.get("nodes", []))
            append(raw, "historical_text", "review_only", f"历史正文切块 {chunk_id}，可回看位置", "历史文字、数值与专有名称不得直接进入新项目")
        if skeleton is not None:
            reusable = bool(skeleton.payload.get("reusable"))
            append(skeleton, "skeleton", "selectable" if reusable else "review_only",
                   f"切块 {chunk_id} 的槽位及段落形式", "仅复用槽位和格式，条件句须以本项目事实重写")

    # A ruled target may not be mentioned directly in the source paragraph.
    if section_id == "S4":
        node_ids.update({"N017", "N034", "N035"})
    if section_id == "S7.1":
        node_ids.add("N080")
    if section_id == "S5.2":
        node_ids.update({"N050", "N051"})
    for node_id in sorted(node_ids):
        row = _record(session, archive.id, 12, node_id)
        if row is not None:
            append(row, "historical_node", "review_only", f"语义节点 {node_id}：{row.payload.get('label', '')}",
                   "节点的历史 value 和审核状态不可继承；需要映射项目事实")

    rule_rows = session.scalars(select(CorpusRecord).where(
        CorpusRecord.corpus_id == archive.id, CorpusRecord.category_number == 13
    ).order_by(CorpusRecord.ordinal)).all()
    relevant_rules = [row for row in rule_rows if str(row.payload.get("target")) in node_ids]
    for row in relevant_rules:
        target = str(row.payload.get("target"))
        relevant = target == "N035" and section_id == "S4" and row.semantic_id == "R006"
        append(row, "rule", "selectable" if relevant else "review_only",
               f"规则 {row.semantic_id}：{row.payload.get('narrative_zh', '')}",
               "需确认当前项目有同单位、同依赖的受限规则；历史结果不得继承")
        node_ids.update(str(value) for value in row.payload.get("deps", []))

    claim_rows = session.scalars(select(CorpusRecord).where(
        CorpusRecord.corpus_id == archive.id, CorpusRecord.category_number == 15
    ).order_by(CorpusRecord.ordinal)).all()
    relevant_claims = [row for row in claim_rows if set(map(str, row.payload.get("supports", []))) & node_ids]
    # Only evidence behind a reusable claim belongs in the active writing
    # package. Evidence for adjacent, review-only claims remains reachable from
    # the claim's source detail; it must not generate a warning for this draft.
    evidence_ids: set[str] = set()
    for row in relevant_claims:
        selected = row.semantic_id == "L002" and section_id == "S4"
        if selected:
            evidence_ids.update(str(value) for value in row.payload.get("must_cite", []))
        append(row, "claim", "selectable" if selected else "review_only",
               f"论断 {row.semantic_id}：{row.payload.get('label', '')}",
               "仅作写法提醒；原论断未经独立核实，新项目须重新确认事实与证据")
    for evidence_id in sorted(evidence_ids):
        row = _record(session, archive.id, 18, evidence_id)
        if row is not None:
            append(row, "historical_evidence", "review_only", f"历史证据 {evidence_id}：{row.payload.get('title', '')}",
                   "仅供查证历史论断；不能证明当前项目的事实")
            for excerpt in row.payload.get("excerpts", []):
                path = excerpt.get("path")
                if path:
                    snippet = session.scalar(select(CorpusRecord).where(
                        CorpusRecord.corpus_id == archive.id, CorpusRecord.category_number == 19,
                        CorpusRecord.artifact_path == path,
                    ))
                    append(snippet, "historical_excerpt", "review_only", f"历史证据摘录 {excerpt.get('id', '')}",
                           "只有摘录，完整原件是否存在须单独核验")

    for category, role in ((23, "conflict"), (24, "gap")):
        rows = session.scalars(select(CorpusRecord).where(
            CorpusRecord.corpus_id == archive.id, CorpusRecord.category_number == category
        ).order_by(CorpusRecord.ordinal)).all()
        for row in rows:
            if set(map(str, row.payload.get("nodes", []))) & node_ids:
                append(row, role, "review_only", f"历史{ '冲突' if role == 'conflict' else '缺口' } {row.semantic_id}：{row.payload.get('what', '')}",
                       "不可视作当前项目已经解决或自动适用")
    if section_id == "S5.2":
        evidence_ids.update({"EV-06A", "EV-06B"})
        for evidence_id in ("EV-06A", "EV-06B"):
            evidence = _record(session, archive.id, 18, evidence_id)
            append(evidence, "historical_evidence", "review_only", f"历史证据 {evidence_id}：{evidence.payload.get('title', '')}" if evidence else "",
                   "历史双来源仅用于披露历史冲突，不能作为新项目适配依据")
    style = session.scalar(select(CorpusRecord).where(CorpusRecord.corpus_id == archive.id, CorpusRecord.category_number == 26))
    append(style, "style", "selectable", "语料的克制表述与数字格式", "只借用风格规范，不继承历史术语和业务判断")

    facts = session.scalars(select(ProjectFact).where(ProjectFact.project_id == project_id).order_by(ProjectFact.key)).all()
    fact_by_key = {fact.key: fact for fact in facts}
    bindings = {item.slot_id: item.fact_key for item in session.scalars(select(WritingBinding).where(
        WritingBinding.project_id == project_id)).all()}
    slots = []
    for slot in SLOTS:
        if (section_id == "S4" and slot["chunk_id"] != "C0052") or (section_id == "S7.1" and slot["chunk_id"] != "C0083"):
            continue
        key = bindings.get(slot["id"])
        fact = fact_by_key.get(key) if key else None
        slots.append({"slot_id": slot["id"], "label": slot["label"], "unit": slot["unit"],
                      "fact_key": key, "value": fact.value_text if fact else None,
                      "status": fact.value_status if fact else "UNBOUND"})

    issues = []
    if not template.payload.get("must_cover"):
        issues.append({"code": "TEMPLATE_COVERAGE_UNSPECIFIED", "severity": "review", "message": "模板缺少关键覆盖配置，需要人工确认", "item_ids": [template.record_id]})
    reported_evidence: set[str] = set()
    for item in items:
        if item["role"] == "conflict":
            issues.append({"code": "HISTORICAL_CONFLICT", "severity": "review", "message": f"{item['semantic_id']} 是历史资料中未解决的冲突；当前项目如论证同一问题，必须使用新证据核对", "item_ids": [item["item_id"]]})
        if (item["role"] == "historical_evidence" and item["semantic_id"] in evidence_ids
                and item["semantic_id"] not in reported_evidence):
            reported_evidence.add(item["semantic_id"])
            evidence = _record(session, archive.id, 18, item["semantic_id"] or "")
            if evidence and not evidence.payload.get("original_document_available"):
                issues.append({"code": "HISTORICAL_EVIDENCE_INCOMPLETE", "severity": "review", "message": f"{item['semantic_id']} 仅有历史摘录，没有完整原件；不能证明当前项目", "item_ids": [item["item_id"]]})
    if section_id not in GUIDED_SECTIONS:
        issues.append({"code": "SECTION_WRITING_UNVALIDATED", "severity": "block", "message": "本章尚未完成写作映射与验证", "item_ids": []})
    if section_id == "S5.2":
        conflicts = [item["item_id"] for item in items if item["role"] == "conflict" and item["semantic_id"] == "X001"]
        issues.append({"code": "POWER_CONFLICT_UNRESOLVED", "severity": "block", "message": "历史配电数据冲突未解决；新项目缺少独立核证，正式交付受阻", "item_ids": conflicts})
    if any(slot["status"] in {"UNBOUND", "UNDEFINED", "UNEVALUABLE"} for slot in slots):
        issues.append({"code": "PROJECT_FACT_MISSING", "severity": "block", "message": "当前项目事实或映射未齐备，不能形成数字结论", "item_ids": []})

    required_keys = {(4, section_id), (27, section_id)} if section_id in GUIDED_SECTIONS else set()
    if section_id in {"S4", "S7.1"}:
        required_keys.add((10, GUIDED_SECTIONS[section_id]))
    if section_id == "S4":
        required_keys.update({(13, "R006"), (15, "L002")})
    required_item_ids = []
    for entry in items:
        entry["required"] = (entry["category_number"], entry["semantic_id"]) in required_keys
        if entry["required"]:
            required_item_ids.append(entry["item_id"])
    if len(required_item_ids) != len(required_keys):
        issues.append({"code": "REQUIRED_PATTERN_MISSING", "severity": "block",
                       "message": "本章必需的结构或规则记录缺失，暂不能起草", "item_ids": required_item_ids})

    return {
        "section": {"id": section_id, "title": template.payload.get("title"), "level": template.payload.get("level")},
        "project_id": project_id,
        "corpus_id": archive.id,
        "corpus_version": archive.version,
        "items": items,
        "required_item_ids": required_item_ids,
        "slots": slots,
        "issues": issues,
        "facts": [{"key": fact.key, "label": fact.label, "value": fact.value_text,
                   "status": fact.value_status, "unit": fact.unit, "revision": fact.revision}
                  for fact in facts],
    }
