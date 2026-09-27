"""Run-bound chapter candidates and explicit report adoption."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from copy import deepcopy
from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .analysis import _report, _run, _source_ref
from .analysis_evidence import section_evidence
from .corpus_storage import CorpusRecord
from .db import AnalysisCandidate, AnalysisConfig, AnalysisWritingEvent, ProjectEvidence, ProjectFact, ProjectIssue, ReportVersion, SessionLocal, SourceDocument, SourceParseRevision, WorkTask, WritingCommitEvent
from .document_pipeline import source_supports
from .model_settings import chat_json
from .report_pipeline import change_impact, content_hash, plain, validate_content
from .chapter_materials import chapter_pack


router = APIRouter(prefix="/api/projects/{project_id}/analysis/reports/{report_id}/draft", tags=["推演写作"])
_secret = secrets.token_bytes(32)
_sections = {"S4": "产能与交付", "S7.1": "设备购置与报价", "S5.2": "配电资料分歧"}


class DraftRequest(BaseModel):
    run_id: str | None = None
    config_id: str | None = None
    section_id: str = Field(max_length=80)
    mode: str = Field(pattern="^(computed|model|excerpt)$")
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class DraftCommit(DraftRequest):
    candidate_id: str | None = None
    base_version: int = Field(ge=0)
    content: list[dict]
    replace_section: bool = False
    selected_block_ids: list[str] | None = None
    model_audit: dict = Field(default_factory=dict)
    expires_at: int
    preview_token: str


def _signature(project_id: str, report_id: str, data: dict) -> str:
    signed = {key: value for key, value in data.items() if key not in {"preview_token", "candidate_id"}}
    payload = json.dumps([project_id, report_id, signed], ensure_ascii=False,
                         sort_keys=True, separators=(",", ":"))
    return hmac.new(_secret, payload.encode(), hashlib.sha256).hexdigest()


def _candidate_digest(project_id: str, report_id: str, content: list[dict],
                      audit: dict, section_id: str, base_version: int) -> str:
    raw = json.dumps([project_id, report_id, section_id, base_version, content, audit],
                     ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _adoption_digest(body: DraftCommit) -> str:
    raw = json.dumps({"replace_section": body.replace_section,
                      "selected_block_ids": body.selected_block_ids},
                     ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _ref(run_id: str, result: dict) -> dict:
    return {"run_id": run_id, "result_key": result["key"],
            "value": result["value"], "unit": result["unit"]}


def _source(session, run, category: int, semantic_id: str) -> dict:
    corpus_id = run.snapshot.get("corpus_id")
    version = run.snapshot.get("corpus_version")
    if not corpus_id or not version:
        raise HTTPException(409, "该章节需要明确选择可用的历史资料")
    row = session.query(CorpusRecord).filter_by(corpus_id=corpus_id,
                category_number=category, semantic_id=semantic_id).one_or_none()
    if row is None:
        raise HTTPException(409, f"写作来源 {semantic_id} 缺失")
    ref = _source_ref(corpus_id, version, row)
    ref.pop("location", None)
    return ref


def _p(section_id: str, text: str, *, refs: list[dict] | None = None,
       source_refs: list[dict] | None = None, fact_keys: list[str] | None = None,
       project_evidence_refs: list[dict] | None = None,
       origin: str = "guided") -> dict:
    return {"type": "p", "id": str(uuid4()), "section_id": section_id,
            "origin": origin, "children": [{"text": text}],
            **({"analysis_refs": refs} if refs else {}),
            **({"fact_keys": fact_keys} if fact_keys else {}),
            **({"project_evidence_refs": project_evidence_refs} if project_evidence_refs else {}),
            **({"source_refs": source_refs} if source_refs else {})}


def _table(section_id: str, run_id: str, rows: list[dict]) -> dict:
    def cell(text: str, result: dict | None = None, header: bool = False) -> dict:
        return {"type": "th" if header else "td", "id": str(uuid4()),
                "children": [_p(section_id, text, refs=[_ref(run_id, result)] if result else None)]}
    return {"type": "table", "id": str(uuid4()), "section_id": section_id,
            "origin": "guided", "analysis_refs": [_ref(run_id, row) for row in rows],
            "children": [
                {"type": "tr", "id": str(uuid4()), "children": [cell("指标", header=True), cell("本方案", header=True)]},
                *[{"type": "tr", "id": str(uuid4()), "children": [cell(row["label"]),
                   cell(f"{row['value']}{row['unit']}", row)]} for row in rows],
            ]}


def _model_paragraph(section_id: str, run, facts: list[dict], rule: str, old_supplier: str | None = None) -> tuple[dict, dict]:
    relevant_issues = {"S4": {"HISTORICAL_INPUT"},
                       "S7.1": {"HISTORICAL_INPUT", "QUOTE_MISSING"},
                       "S5.2": {"X001"}}[section_id]
    context = {"section": _sections[section_id], "condition": run.snapshot.get("condition"),
               "values": [{"key": row["key"], "label": row["label"],
                           "value": row["value"], "unit": row["unit"]} for row in facts],
               "rule": rule,
               "limitations": [issue["message"] for issue in run.snapshot["issues"]
                               if issue["code"] in relevant_issues]}
    response = chat_json("writing", [
        {"role": "system", "content": "你撰写专业报告的一个待人工核对段落。只使用所给当前方案计算结果和限制。"
         "不得声称预测需求为已签订单；不得改变计算值、补造报价或裁决配电冲突。"
         "只返回 JSON：{\"text\":\"中文段落\",\"used_result_keys\":[\"实际用到的key\"]}。"
         "段落不超过240字；不写标题、表号、年份或来源外数字。"},
        {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
    ], max_tokens=1000)
    text = response.get("text")
    keys = response.get("used_result_keys")
    permitted = {item["key"]: item for item in facts}
    if (not isinstance(text, str) or not text.strip() or len(text) > 240
            or not isinstance(keys, list) or any(key not in permitted for key in keys)
            or len(set(keys)) != len(keys)):
        raise HTTPException(422, "模型段落缺少可核对的结果引用")
    visible = {x.replace(",", "") for x in re.findall(r"(?<!\d)\d[\d,]*(?:\.\d+)?(?!\d)", text)}
    allowed = {permitted[key]["value"] for key in keys if permitted[key]["value"] is not None}
    if visible - allowed:
        raise HTTPException(422, "模型段落出现当前运行结果以外的数字：" + "、".join(sorted(visible - allowed)))
    def asserted(phrase: str) -> bool:
        return any(not re.search(r"不(?:是|等于|能|可|应|足|属于|视为|代表)?|非|尚未|未|不得|不能", text[max(0, match.start()-8):match.start()])
                   for match in re.finditer(re.escape(phrase), text))

    if (asserted("已签订单") or asserted("已签订订单")
            or section_id == "S4" and run.snapshot["condition"] == "需求高于产能" and asserted("需求低于产能")
            or section_id == "S4" and run.snapshot["condition"] == "需求低于产能" and asserted("产能不足")
            or old_supplier and old_supplier not in [permitted[key]["value"] for key in keys]
               and old_supplier in text):
        raise HTTPException(422, "模型段落含已失效的身份、条件或订单判断")
    refs = [_ref(run.id, permitted[key]) for key in keys if permitted[key]["value"] is not None]
    return _p(section_id, text.strip(), refs=refs, origin="model"), dict(getattr(response, "model_call", {}))


def _configured_model(run, section: dict, rows: list[dict], conditions: list[dict], evidence: list[dict]) -> tuple[dict, dict]:
    if not conditions or not evidence:
        raise HTTPException(409, "模型起草需要已配置的条件与项目原件证据")
    if any(item["status"] != "VERIFIED" for item in evidence):
        raise HTTPException(409, "本章要求的项目原件证据尚未核对，暂不能模型起草")
    allowed = {row["key"]: row for row in rows}
    for item in conditions:
        for key in item["deps"]:
            allowed[key] = run.snapshot["results"][key]
    context = {"section": section["title"],
               "values": [{"key": row["key"], "label": row["label"],
                           "value": row["value"], "unit": row["unit"]} for row in allowed.values()],
               "approved_condition_sentences": [item["text"] for item in conditions],
               "verified_sources": [{"key": item["key"], "label": item["label"],
                                     "status": "reviewed_original"} for item in evidence],
               "forbidden_terms": section.get("forbidden_terms", [])}
    messages = [
        {"role": "system", "content": "请为专业报告写一段待人工核对的正文。仅使用给出的运行值、已核对来源和已批准条件句。"
         "逐条原样包含已批准条件句，不得增加因果、责任、法律或订单结论。"
         "不得增加输入以外的数字，不要在正文写来源编号、页码、核对过程或内部ID。"
         "只返回JSON：{\"text\":\"中文段落\",\"used_result_keys\":[\"实际用到的key\"]}。"
         "used_result_keys必须使用values中的key，至少一个；正文出现的每个数字都要列出对应key。"},
        {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
    ]
    last_error = "模型段落未通过来源与条件校验"
    for attempt in range(2):
        try:
            response = chat_json("writing", messages, max_tokens=850)
        except ValueError as exc:
            raise HTTPException(503, str(exc)) from exc
        text = response.get("text")
        keys = response.get("used_result_keys")
        if (not isinstance(text, str) or not text.strip() or len(text) > 320
                or not isinstance(keys, list) or not keys or any(not isinstance(key, str) or key not in allowed for key in keys)
                or len(set(keys)) != len(keys)):
            last_error = "模型段落缺少可核对的运行引用"
        else:
            visible = {value.replace(",", "") for value in re.findall(r"(?<!\d)\d[\d,]*(?:\.\d+)?(?!\d)", text)}
            permitted_numbers = {allowed[key]["value"] for key in keys if allowed[key]["value"] is not None}
            if visible - permitted_numbers:
                last_error = "模型段落出现运行以外的数字"
            elif (any(term in text for term in section.get("forbidden_terms", []))
                  or any(item["text"] not in text for item in conditions)
                  or any(condition["when_true" if item["outcome"] is False else "when_false"] in text
                         for condition, item in zip(section.get("conditions", []), conditions))):
                last_error = "模型段落含禁用或失效的条件表述"
            else:
                refs = [_ref(run.id, allowed[key]) for key in keys]
                audit = dict(getattr(response, "model_call", {}))
                audit["validation_attempts"] = attempt + 1
                return _p(section["id"], text.strip(), refs=refs,
                          fact_keys=[item["key"] for item in evidence if item["key"] in keys
                                     and allowed[item["key"]]["value"] in visible],
                          origin="model"), audit
        if attempt == 0:
            messages.append({"role": "user", "content": "上一候选未通过校验：" + last_error + "。请重新生成，保持给定的全部条件句和数字原样，"
                             "使用values中的key逐项列出正文实际引用的结果；没有依据的内容不要写。"})
    raise HTTPException(422, last_error + "；已重试，报告未改变")


def _candidate(session, run, section_id: str, mode: str) -> tuple[list[dict], dict]:
    if mode == "excerpt":
        raise HTTPException(400, "摘录候选只适用于文字章节")
    configuration = run.snapshot.get("configuration")
    if configuration:
        section = next((row for row in configuration["sections"] if row["id"] == section_id), None)
        if section is None:
            raise HTTPException(409, "当前配置没有此章节")
        if run.status != "COMPUTED":
            raise HTTPException(409, "本次推演存在不可评估结果")
        results = run.snapshot["results"]
        rows = [results[key] for key in section["result_keys"]]
        if any(row["value"] is None for row in rows):
            raise HTTPException(409, "本章所需指标仍有缺值")
        evidence = section_evidence(session, run.project_id, run, section)
        outcomes = [run.snapshot.get("condition_results", {}).get(f"{section_id}:{item['id']}")
                    for item in section.get("conditions", [])]
        if any(item is None or item["outcome"] is None for item in outcomes):
            raise HTTPException(409, "本章条件因输入缺失而不可评估")
        text = "本方案" + section["title"] + "采用：" + "；".join(
            f"{row['label']}{row['value']}{row['unit']}" for row in rows) + "。"
        blocks = [{"type": "h2", "id": str(uuid4()), "section_id": section_id,
                   "children": [{"text": section["title"]}]},
                  _p(section_id, text, refs=[_ref(run.id, row) for row in rows],
                     fact_keys=[key for key in section.get("evidence_keys", [])
                                if key in section["result_keys"]]),
                  _table(section_id, run.id, rows)]
        for item in outcomes:
            refs = [_ref(run.id, results[key]) for key in item["deps"]]
            blocks.append(_p(section_id, item["text"], refs=refs))
        if mode == "model":
            model, model_call = _configured_model(run, section, rows, outcomes, evidence)
            blocks.append(model)
        else:
            model_call = {}
        validate_content(blocks)
        return blocks, {"configuration_id": configuration["id"],
                        "configuration_version": configuration["version"],
                        "evidence": evidence, "conditions": outcomes,
                        "model_call": model_call}
    if section_id not in _sections:
        raise HTTPException(409, "该章节尚未完成推演写作配置")
    snapshot = run.snapshot
    results = snapshot["results"]
    if run.status != "COMPUTED":
        raise HTTPException(409, "本次推演存在不可评估结果")
    blocks = [{"type": "h2", "id": str(uuid4()), "section_id": section_id,
               "children": [{"text": _sections[section_id]}]}]
    audit: dict = {}
    if section_id == "S4":
        keys = ("N017", "N034", "N035") if "N017" in results else (
            "first_year_demand", "qualified_capacity", "planned_sales")
        if not all(key in results for key in keys):
            raise HTTPException(409, "本方案未配置首年需求与销售结果")
        demand, capacity, sales = (results[key] for key in keys)
        refs = [_ref(run.id, row) for row in (demand, capacity, sales)]
        condition = snapshot["condition"]
        sentence = "需求超过合格能力，销售计划受产能限制。" if condition == "需求高于产能" else (
                   "需求低于合格能力，销售计划以需求为限。" if condition == "需求低于产能" else
                   "需求与合格能力相等，销售计划与两者一致。")
        text = (f"本方案首年需求为{demand['value']}{demand['unit']}，"
                f"合格能力为{capacity['value']}{capacity['unit']}，"
                f"按两者取小，计划销售量为{sales['value']}{sales['unit']}。{sentence}")
        source_refs = [_source(session, run, 9, "C0052")] if snapshot.get("corpus_id") else []
        blocks.append(_p(section_id, text, refs=refs, source_refs=source_refs))
        rows = [demand, capacity, sales]
        if "demand_gap" in results:
            rows.append(results["demand_gap"])
        blocks.append(_table(section_id, run.id, rows))
        claim_source = ([_source(session, run, 9, "C0027"),
                         _source(session, run, 15, "L002"),
                         _source(session, run, 18, "EV-01")]
                        if snapshot.get("corpus_id") else [])
        blocks.append(_p(section_id, "需求量属于预测输入，尚不能作为已签订单；方案参数仍须结合本项目资料核实。",
                         source_refs=claim_source))
        if mode == "model":
            model, audit = _model_paragraph(section_id, run, rows,
                                             "计划销售量取需求与合格能力的较小值；需求不是订单")
            blocks.append(model)
    elif section_id == "S7.1":
        supplier = results.get("supplier")
        quote = results.get("supplier_quote")
        if not supplier:
            raise HTTPException(409, "本方案没有供应商字段")
        name = supplier["value"]
        text = (f"本方案供应商为{name}，设备报价尚未取得当前方案依据，采购范围及判断待核对。"
                if name and (not quote or quote["value"] is None) else
                "供应商及当前报价待补，设备购置判断暂不形成。" if not name else
                f"本方案供应商为{name}，录入的设备报价为{quote['value']}{quote['unit']}；报价范围和有效期仍需核对。")
        refs = [_ref(run.id, row) for row in (supplier, quote) if row and row["value"] is not None]
        blocks.append(_p(section_id, text, refs=refs))
        if mode == "model":
            model, audit = _model_paragraph(section_id, run, [supplier] + ([quote] if quote and quote["value"] is not None else []),
                                             "没有当前报价依据时不能给出确定性采购判断", old_supplier="禾进装备")
            blocks.append(model)
    else:
        if not snapshot.get("corpus_id"):
            raise HTTPException(409, "当前项目没有可核对的配电冲突来源")
        source = _source(session, run, 9, "C0063")
        blocks.append(_p(section_id,
            "历史资料06A与06B对同一接入点分别记录800kW和650kW的可用有功余量；"
            "两份资料尚未完成联合复测，当前不能形成确定性的配电适配或采购结论。",
            source_refs=[source]))
        if mode == "model":
            raise HTTPException(409, "配电分歧只支持经核对的披露文字")
    validate_content(blocks)
    return blocks, audit


def _narrative_candidate(session, project_id: str, config_id: str, section_id: str,
                         mode: str, evidence_ids: list[str], report_type: str) -> tuple[list[dict], dict]:
    from .main import _current_original_fact_evidence, _reviewed_fact_binding
    from .report_pipeline import numeric_tokens

    config = session.query(AnalysisConfig).filter_by(id=config_id, project_id=project_id,
                                                      status="PUBLISHED").one_or_none()
    if config is None:
        raise HTTPException(404, "已发布章节配置不存在")
    section = next((item for item in config.sections if item["id"] == section_id), None)
    if section is None or section.get("kind", "calculation") != "narrative":
        raise HTTPException(409, "该章节不是独立文字章节")
    if section.get("report_type", report_type) != report_type:
        raise HTTPException(409, "该章节配置不适用于当前报告类型")
    if mode not in {"model", "excerpt"}:
        raise HTTPException(400, "文字章节请选择摘录或模型起草")
    evidence = []
    selected = []
    if evidence_ids:
        if len(set(evidence_ids)) != len(evidence_ids):
            raise HTTPException(400, "证据不能重复选择")
        selected = session.query(ProjectEvidence).filter(ProjectEvidence.id.in_(evidence_ids),
                                                         ProjectEvidence.project_id == project_id).all()
        if len(selected) != len(evidence_ids):
            raise HTTPException(404, "所选证据不属于本项目")
    for key in section["evidence_keys"]:
        fact = session.query(ProjectFact).filter_by(project_id=project_id, key=key).one_or_none()
        if fact is None or fact.value_text is None or fact.value_status != "PROVIDED":
            raise HTTPException(409, f"本章缺少项目资料：{key}")
        linked = [item for item in selected if item.fact_key == key and item.fact_revision == fact.revision]
        if selected and not linked:
            raise HTTPException(409, f"本章资料 {fact.label} 未选择当前修订的证据")
        if linked:
            for item in linked:
                if session.query(ProjectEvidence.id).filter_by(
                        project_id=project_id, supersedes_id=item.id).first():
                    raise HTTPException(409, "所选证据已有新版本，请重新选择")
                document = session.get(SourceDocument, item.document_id)
                if document is None or document.project_id != project_id or document.sha256 != item.document_sha256:
                    raise HTTPException(409, "所选证据的原件版本已失效")
                parsed = session.get(SourceParseRevision, item.parse_revision_id) if item.parse_revision_id else None
                if item.parse_revision_id and (parsed is None or parsed.document_id != document.id
                                               or parsed.document_sha256 != document.sha256):
                    raise HTTPException(409, "所选证据的解析版本已失效")
                by_ref = {str(part.get("ref")): part for part in
                          (parsed.segments if parsed else document.segments)}
                if (not item.source_refs or any(ref not in by_ref for ref in item.source_refs)
                        or not any(source_supports(fact.value_text, str(by_ref[ref].get("text", "")))
                                   for ref in item.source_refs)):
                    raise HTTPException(409, "所选证据不再支持本项目事实的当前值")
                evidence.append({"key": key, "label": fact.label, "value": fact.value_text,
                                 "fact_revision": fact.revision, "evidence_id": item.id,
                                 "document_id": document.id, "document_sha256": document.sha256,
                                 "parse_revision_id": item.parse_revision_id,
                                 "source_refs": item.source_refs, "excerpt": item.excerpt,
                                 "status": "VERIFIED", "reason": None,
                                 "independent_verification": "not_recorded",
                                 "source_type": item.source_type})
        else:
            resolved = _reviewed_fact_binding(session, project_id, fact)
            if resolved is not None:
                binding, document, segments = resolved
                evidence.append({"key": key, "label": fact.label, "value": fact.value_text,
                                 "fact_revision": fact.revision, "evidence_id": None,
                                 "document_id": document.id, "document_sha256": document.sha256,
                                 "source_refs": binding.source_refs,
                                 "excerpt": "\n".join(str(part["text"]) for part in segments),
                                 "status": "VERIFIED", "reason": None,
                                 "independent_verification": "not_recorded", "source_type": "original"})
            else:
                original = _current_original_fact_evidence(session, project_id, fact)
                if original is None:
                    raise HTTPException(409, f"本章资料 {fact.label} 缺少当前修订的原文定位")
                item, document, segments = original
                evidence.append({"key": key, "label": fact.label, "value": fact.value_text,
                                 "fact_revision": fact.revision, "evidence_id": item.id,
                                 "document_id": document.id, "document_sha256": document.sha256,
                                 "parse_revision_id": item.parse_revision_id,
                                 "source_refs": item.source_refs, "excerpt": item.excerpt,
                                 "status": "VERIFIED", "reason": None,
                                 "independent_verification": "not_recorded", "source_type": "original"})
    if selected and any(item.fact_key not in section["evidence_keys"] for item in selected):
        raise HTTPException(409, "所选证据不属于本章所需的项目资料")
    heading = {"type": "h2", "id": str(uuid4()), "section_id": section_id,
               "children": [{"text": section["title"]}]}
    if mode == "excerpt":
        paragraphs = [{"text": f"据本项目原文记载，{item['value']}",
                       "used_fact_keys": [item["key"]]} for item in
                      {item["key"]: item for item in evidence}.values()]
        model_call = {}
    else:
        if any(issue.kind == "conflict" and section_id in issue.section_ids
               for issue in session.query(ProjectIssue).filter_by(project_id=project_id).all()):
            raise HTTPException(409, "本章存在相互矛盾的来源；请先人工核对，可使用原文摘录预审")
        context = {"section": section["title"], "source_assertions": [
            {"key": item["key"], "label": item["label"], "text": item["value"],
             "excerpt": item["excerpt"][:1600],
             "independent_verification": item["independent_verification"]} for item in evidence],
            "forbidden_terms": section.get("forbidden_terms", [])}
        response = chat_json("writing", [
            {"role": "system", "content": "依据本项目有定位的原文，为报告文字章节起草简洁的待人工核对文字，每条依据最多一段。"
             "仅转述来源明确陈述的内容，保留拟实施、拟申请、预测、原文时点等限定；"
             "未经独立核实的效果或必要性判断只能写成原报告观点，不得升级为已证实结论；"
             "不得编造数字、法规、批复或主体；不要推断未披露事项的审批、到位或额度。"
             "只返回 JSON：{\"paragraphs\":[{\"text\":\"段落\",\"used_fact_keys\":[\"实际使用的key\"]}]}。"},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ], max_tokens=1300)
        paragraphs = response.get("paragraphs")
        model_call = dict(getattr(response, "model_call", {}))
    by_key = {item["key"]: item for item in evidence}
    if not isinstance(paragraphs, list) or not 1 <= len(paragraphs) <= min(3, len(by_key)):
        raise HTTPException(422, "模型候选段落结构无效")
    forbidden = section.get("forbidden_terms", [])
    blocks = [heading]
    used_fact_keys: set[str] = set()
    for item in paragraphs:
        if not isinstance(item, dict):
            raise HTTPException(422, "候选段落结构无效")
        text_value, keys = item.get("text"), item.get("used_fact_keys")
        if (not isinstance(text_value, str) or not text_value.strip() or len(text_value) > 1200
                or not isinstance(keys, list) or not keys or len(set(keys)) != len(keys)
                or any(key not in by_key for key in keys)):
            raise HTTPException(422, "候选段落缺少可核对的项目来源")
        used_fact_keys.update(keys)
        if any(term in text_value for term in forbidden):
            raise HTTPException(422, "候选包含本章禁用或高风险确定性表述")
        allowed_numbers = set().union(*(numeric_tokens(by_key[key]["value"]) for key in keys))
        if numeric_tokens(text_value) - allowed_numbers:
            raise HTTPException(422, "候选出现来源之外的数字")
        # A negated phrase in the original, such as “尚非已签订单”, is not a positive claim.
        source_text = " ".join(by_key[key]["value"] for key in keys)
        for term in ("审批", "批复", "额度", "到位", "批准", "合同"):
            if term in text_value and term not in source_text:
                raise HTTPException(422, "候选包含来源未支持的审批、资金或合同判断")
        for key in keys:
            source_value = by_key[key]["value"]
            for modal in ("拟申请", "计划", "预测"):
                if modal in source_value and modal not in text_value:
                    raise HTTPException(422, "候选遗漏原文的计划、预测或拟申请限定")
        def positive_claim(value: str, phrase: str) -> bool:
            return any(not re.search(r"(?:不|非|未|尚未|尚非|不得|不能).{0,2}$",
                                     value[max(0, match.start()-5):match.start()])
                       for match in re.finditer(re.escape(phrase), value))

        for phrase in ("已落实资金", "资金已落实", "已经开通", "已开通", "已获批复", "已获批准",
                       "已签订合同", "已签订单", "已完成建设"):
            if positive_claim(text_value, phrase) and not positive_claim(source_text, phrase):
                raise HTTPException(422, "候选将拟议事项写成已完成事项")
        project_refs = [{"evidence_id": row["evidence_id"],
                         "document_sha256": row["document_sha256"],
                         **({"parse_revision_id": row["parse_revision_id"]} if row.get("parse_revision_id") else {})}
                        for row in evidence if row["key"] in keys and row["evidence_id"]]
        rendered = text_value.strip()
        if mode == "model" and not re.match(r"^(?:据.{0,12}(?:原文|原报告)|原报告|报告原文)", rendered):
            rendered = "据本项目原文记载，" + rendered
        blocks.append(_p(section_id, rendered, fact_keys=keys,
                         project_evidence_refs=project_refs,
                         origin="model" if mode == "model" else "guided"))
    validate_content(blocks)
    return blocks, {"configuration_id": config.id, "configuration_version": config.version,
                    "evidence": [{key: value for key, value in item.items() if key not in {"value", "excerpt"}}
                                 for item in evidence], "model_call": model_call,
                    "used_fact_keys": sorted(used_fact_keys),
                    "unused_fact_keys": sorted(set(section["evidence_keys"]) - used_fact_keys)}


def _manual_blocks(session, report, section_id: str) -> tuple[list[dict], list[str]]:
    """Find blocks changed or added after this section's last accepted candidate."""
    event = (session.query(AnalysisWritingEvent).filter_by(
        project_id=report.project_id, report_id=report.id,
        section_id=section_id).filter(
        AnalysisWritingEvent.action.in_(("accept_candidate", "refresh_baseline")))
        .order_by(AnalysisWritingEvent.report_version.desc()).first())
    if event is None:
        return [], []
    accepted_ids = event.payload.get("block_ids", [])
    version = session.get(ReportVersion, (report.id, event.report_version))
    if version is None:
        raise HTTPException(409, "章节原始版本缺失，无法安全替换")
    original = {block.get("id"): block for block in version.content
                if block.get("id") in accepted_ids}
    protected = []
    for block in report.content:
        if block.get("section_id") != section_id:
            continue
        old = original.get(block.get("id"))
        if (block.get("origin") == "manual"
                or block.get("id") in event.payload.get("manual_block_ids", [])
                or old is None or old != block):
            protected.append(block)
    return protected, accepted_ids


def _replacement(candidate: list[dict], protected: list[dict], accepted_ids: list[str]) -> list[dict]:
    """Update untouched generated blocks while retaining user-edited positions."""
    merged = deepcopy(candidate)
    extras = []
    for block in protected:
        kept = deepcopy(block)
        kept["origin"] = "manual"
        position = accepted_ids.index(block.get("id")) if block.get("id") in accepted_ids else -1
        if 0 <= position < len(merged) and merged[position].get("type") == kept.get("type"):
            merged[position] = kept
        else:
            extras.append(kept)
    return merged + extras


def _partial_section(session, report, candidate: list[dict], selected: list[str],
                     existing: list[dict], protected: list[dict]) -> tuple[list[dict], dict[str, str], list[dict]]:
    choices = {block.get("id") for block in candidate[1:]}
    if not selected or len(set(selected)) != len(selected) or any(block_id not in choices for block_id in selected):
        raise HTTPException(400, "请选择候选中的正文或表格")
    event = (session.query(AnalysisWritingEvent).filter_by(
        project_id=report.project_id, report_id=report.id,
        section_id=candidate[0]["section_id"], action="accept_candidate")
        .order_by(AnalysisWritingEvent.report_version.desc()).first())
    slots = dict(event.payload.get("candidate_slots", {})) if event else {}
    if event and not slots:
        slots = {str(index): block_id for index, block_id in enumerate(event.payload.get("block_ids", []))}
    adopted = deepcopy(existing) if existing else [deepcopy(candidate[0])]
    if not existing:
        slots = {"0": candidate[0]["id"]}
    protected_ids = {block.get("id") for block in protected}
    inserted: list[dict] = []
    for index, block in enumerate(candidate[1:], 1):
        if block["id"] not in selected:
            continue
        old_id = slots.get(str(index))
        position = next((at for at, old in enumerate(adopted) if old.get("id") == old_id), None)
        if position is not None:
            if old_id in protected_ids:
                raise HTTPException(409, "所选位置已有人工修改，请取消该项并在正文中核对")
            adopted[position] = deepcopy(block)
        else:
            preceding = next((slots.get(str(at)) for at in range(index - 1, -1, -1)
                              if slots.get(str(at)) in {row.get("id") for row in adopted}), None)
            offset = next((at + 1 for at, old in enumerate(adopted) if old.get("id") == preceding), len(adopted))
            adopted.insert(offset, deepcopy(block))
        slots[str(index)] = block["id"]
        inserted.append(block)
    return adopted, slots, inserted


@router.post("/preview")
def draft_preview(project_id: str, report_id: str, body: DraftRequest):
    return _draft_preview(project_id, report_id, body)


def _draft_preview(project_id: str, report_id: str, body: DraftRequest,
                   task_guard: tuple[str, str] | None = None):
    with SessionLocal() as session:
        report = _report(session, project_id, report_id)
        if body.run_id:
            run = _run(session, project_id, body.run_id)
            if report.analysis_run_id != run.id:
                raise HTTPException(409, "请先选择本次推演作为报告依据")
            configuration = run.snapshot.get("configuration")
            if configuration:
                section = next((row for row in configuration["sections"]
                                if row["id"] == body.section_id), None)
                if section and section.get("report_type", report.report_type) != report.report_type:
                    raise HTTPException(409, "本次运行的章节配置不适用于当前报告类型")
            content, model_audit = _candidate(session, run, body.section_id, body.mode)
        elif body.config_id:
            run = None
            content, model_audit = _narrative_candidate(session, project_id, body.config_id,
                                                         body.section_id, body.mode, body.evidence_ids,
                                                         report.report_type)
        else:
            raise HTTPException(400, "请选择文字章节配置或推演运行")
        pack = chapter_pack(session, project_id, report, body.section_id)
        modes = {row["category_id"]: row["mode"] for row in pack["categories"]}
        blocked = {ref.get("category_id") for block in content for ref in block.get("source_refs", [])
                   if modes.get(ref.get("category_id")) in {"review", "exclude"}}
        if blocked:
            raise HTTPException(409, "本章资料策略限制了候选使用：" + "、".join(sorted(blocked)))
        model_audit = {**model_audit, "writing_pack_sha256": pack["watermark_sha256"]}
        protected, _ = _manual_blocks(session, report, body.section_id)
        expires = int(time.time()) + 600
        data = {"section_id": body.section_id, "mode": body.mode, "evidence_ids": body.evidence_ids,
                "base_version": report.version, "content": content,
                "model_audit": model_audit, "expires_at": expires}
        data["run_id" if run else "config_id"] = run.id if run else body.config_id
        evidence_issues = [{"code": "CHAPTER_SCENARIO_ASSUMPTION" if item["status"] == "ASSUMPTION" else
                            "CHAPTER_EVIDENCE_MISSING",
                            "severity": "note" if item["status"] == "ASSUMPTION" else "block",
                            "message": f"{item['label']}：{item['reason']}", "fact_key": item["key"]}
                           for item in model_audit.get("evidence", []) if item["status"] != "VERIFIED"]
        digest = _candidate_digest(project_id, report_id, content, model_audit,
                                   body.section_id, report.version)
        result = {**data, "preview_token": _signature(project_id, report_id, data),
                  "issues": [*(run.snapshot["issues"] if run else []), *evidence_issues],
                  "preserved_blocks": protected}
    with SessionLocal.begin() as session:
        current = _report(session, project_id, report_id, lock=True)
        task = None
        if task_guard:
            task = session.query(WorkTask).filter_by(id=task_guard[0], project_id=project_id,
                                                      report_id=report_id).with_for_update().one_or_none()
            if (task is None or task.generation != task_guard[1]
                    or task.status != "RUNNING" or task.cancel_requested):
                raise HTTPException(409, "生成任务已取消或被新尝试替代")
            if (current.version != task.payload.get("report_version") or
                    model_audit["writing_pack_sha256"] != task.payload.get("pack_sha256")):
                raise HTTPException(409, "任务依据已变化，请创建新任务")
        if current.version != data["base_version"] or chapter_pack(
                session, project_id, current, body.section_id)["watermark_sha256"] != model_audit["writing_pack_sha256"]:
            raise HTTPException(409, "生成期间报告或资料已变化，请重新生成")
        stored = AnalysisCandidate(project_id=project_id, report_id=report_id,
            section_id=body.section_id, mode=body.mode, run_id=run.id if run else None,
            config_id=body.config_id if not run else None,
            evidence_ids=body.evidence_ids,
            base_version=data["base_version"], content=content,
            model_audit=model_audit, issues=result["issues"],
            preserved_blocks=protected, digest=digest, expires_at=expires)
        session.add(stored); session.flush()
        if task:
            task.status = "COMPLETED"
            task.stage = "DONE"
            task.result = {"candidate_id": stored.id, "report_version": current.version}
        return {**result, "candidate_id": stored.id}


@router.get("/candidates")
def draft_candidates(project_id: str, report_id: str, section_id: str | None = None):
    with SessionLocal() as session:
        report = _report(session, project_id, report_id)
        query = session.query(AnalysisCandidate).filter_by(
            project_id=project_id, report_id=report_id, status="PENDING")
        if section_id:
            query = query.filter_by(section_id=section_id)
        rows = query.order_by(AnalysisCandidate.created_at.desc()).limit(20).all()
        return [{"candidate_id": row.id, "section_id": row.section_id,
                 "mode": row.mode, "base_version": row.base_version,
                 "status": "READY" if row.expires_at >= time.time() and row.base_version == report.version else "STALE",
                 "created_at": row.created_at.isoformat()} for row in rows]


@router.get("/candidates/{candidate_id}")
def draft_candidate_get(project_id: str, report_id: str, candidate_id: str):
    with SessionLocal() as session:
        report = _report(session, project_id, report_id)
        row = session.query(AnalysisCandidate).filter_by(
            id=candidate_id, project_id=project_id, report_id=report_id).one_or_none()
        if row is None:
            raise HTTPException(404, "候选不存在")
        if row.status != "PENDING" or row.expires_at < time.time() or row.base_version != report.version:
            raise HTTPException(409, "候选已过期或报告已变化")
        if chapter_pack(session, project_id, report, row.section_id)["watermark_sha256"] != row.model_audit.get("writing_pack_sha256"):
            raise HTTPException(409, "候选依据已变化，请重新生成")
        data = {"section_id": row.section_id, "mode": row.mode,
                "evidence_ids": row.evidence_ids, "base_version": row.base_version,
                "content": row.content, "model_audit": row.model_audit,
                "expires_at": row.expires_at}
        data["run_id" if row.run_id else "config_id"] = row.run_id or row.config_id
        return {**data, "candidate_id": row.id,
                "preview_token": _signature(project_id, report_id, data),
                "issues": row.issues, "preserved_blocks": row.preserved_blocks}


@router.post("/candidates/{candidate_id}/decline")
def draft_decline(project_id: str, report_id: str, candidate_id: str):
    with SessionLocal.begin() as session:
        item = session.query(AnalysisCandidate).filter_by(
            id=candidate_id, project_id=project_id, report_id=report_id).with_for_update().one_or_none()
        if item is None:
            raise HTTPException(404, "候选不存在")
        if item.status == "ACCEPTED":
            raise HTTPException(409, "候选已加入报告")
        if item.status == "DECLINED":
            return {"id": item.id, "status": item.status}
        item.status = "DECLINED"
        report = _report(session, project_id, report_id)
        session.add(AnalysisWritingEvent(project_id=project_id, report_id=report_id,
            report_version=report.version, run_id=item.run_id, action="decline_candidate",
            section_id=item.section_id, payload={"candidate_id": item.id,
                                                  "block_ids": [block.get("id") for block in item.content]}))
        return {"id": item.id, "status": item.status}


@router.post("/commit")
def draft_commit(project_id: str, report_id: str, body: DraftCommit):
    digest = _candidate_digest(project_id, report_id, body.content, body.model_audit,
                               body.section_id, body.base_version)
    if body.candidate_id:
        with SessionLocal() as session:
            existing = session.query(AnalysisCandidate).filter_by(
                id=body.candidate_id, project_id=project_id, report_id=report_id).one_or_none()
            if existing is None or existing.digest != digest:
                raise HTTPException(409, "候选内容与已保存版本不一致")
            if existing.status == "ACCEPTED":
                report = _report(session, project_id, report_id)
                if (existing.adoption_request_sha256 != _adoption_digest(body)
                        or existing.accepted_report_version != report.version):
                    raise HTTPException(409, "候选已按其他选择采用，或报告随后发生变化")
                from .main import _current_facts, _report_dict
                return {"report": _report_dict(report, _current_facts(session, project_id), session),
                        "changes": [], "idempotent": True}
    signed = body.model_dump(exclude_none=True, exclude={"preview_token", "candidate_id", "replace_section", "selected_block_ids"})
    if body.expires_at < time.time() or not hmac.compare_digest(
            body.preview_token, _signature(project_id, report_id, signed)):
        raise HTTPException(409, "章节候选已过期或发生变化，请重新生成")
    with SessionLocal.begin() as session:
        report = _report(session, project_id, report_id, lock=True)
        stored = session.query(AnalysisCandidate).filter_by(
            id=body.candidate_id, project_id=project_id, report_id=report_id).with_for_update().one_or_none() if body.candidate_id else None
        if stored and (stored.status != "PENDING" or stored.digest != digest or stored.expires_at < time.time()):
            raise HTTPException(409, "候选已经处理或过期")
        if report.version != body.base_version:
            raise HTTPException(409, "报告版本已变化")
        if (body.model_audit.get("writing_pack_sha256") and
                body.model_audit["writing_pack_sha256"] != chapter_pack(
                    session, project_id, report, body.section_id)["watermark_sha256"]):
            raise HTTPException(409, "本章资料或策略已变化，请重新生成候选")
        if body.run_id:
            run = _run(session, project_id, body.run_id)
            if report.analysis_run_id != run.id:
                raise HTTPException(409, "推演依据已变化")
        elif body.config_id:
            run = None
            config = session.query(AnalysisConfig).filter_by(id=body.config_id,
                                                              project_id=project_id, status="PUBLISHED").one_or_none()
            if config is None or config.version != body.model_audit.get("configuration_version"):
                raise HTTPException(409, "章节配置已变化")
            from .main import _reviewed_fact_binding
            for item in body.model_audit.get("evidence", []):
                fact = session.query(ProjectFact).filter_by(project_id=project_id, key=item["key"]).one_or_none()
                if fact is None or fact.revision != item["fact_revision"]:
                    raise HTTPException(409, "项目资料已变化，请重新生成候选")
                if item.get("evidence_id"):
                    evidence = session.query(ProjectEvidence).filter_by(id=item["evidence_id"],
                                                                         project_id=project_id).one_or_none()
                    if (evidence is None or evidence.fact_key != item["key"]
                            or evidence.fact_revision != item["fact_revision"]
                            or evidence.document_id != item["document_id"]
                            or evidence.document_sha256 != item["document_sha256"]
                            or evidence.parse_revision_id != item.get("parse_revision_id")
                            or evidence.source_refs != item["source_refs"]):
                        raise HTTPException(409, "所选项目证据已变化，请重新生成候选")
                else:
                    resolved = _reviewed_fact_binding(session, project_id, fact)
                    if (resolved is None or resolved[1].id != item["document_id"]
                            or resolved[1].sha256 != item["document_sha256"]
                            or resolved[0].source_refs != item["source_refs"]):
                        raise HTTPException(409, "原文证据已变化，请重新生成候选")
        else:
            raise HTTPException(400, "缺少章节依据")
        validate_content(body.content)
        existing = [block for block in report.content if block.get("section_id") == body.section_id]
        if existing and not body.replace_section:
            raise HTTPException(409, "本章已有正文，请先查看替换范围")
        protected, accepted_ids = _manual_blocks(session, report, body.section_id)
        if body.selected_block_ids is not None:
            adopted, slots, new_blocks = _partial_section(session, report, body.content,
                                                          body.selected_block_ids, existing, protected)
        else:
            adopted = (_replacement(body.content, protected, accepted_ids)
                       if body.replace_section else body.content)
            slots = {str(index): block["id"] for index, block in enumerate(adopted[:len(body.content)])}
            new_blocks = adopted
        content = []
        inserted = False
        for block in report.content:
            if body.replace_section and block.get("section_id") == body.section_id:
                if not inserted:
                    content.extend(adopted)
                    inserted = True
            else:
                content.append(block)
        if len(content) == 2 and content[1].get("type") == "p" and not plain(content[1]).strip():
            content = content[:1]
        if not inserted:
            content.extend(adopted)
        validate_content(content)
        from .main import _analysis_report_state, _current_facts, _report_dict, _validate_corpus_article_content, _validate_writing_refs
        _validate_writing_refs(session, project_id, content)
        _validate_corpus_article_content(session, project_id, report_id, content)
        _analysis_report_state(session, report, content)
        impacts = change_impact(report.content, content)
        report.content = content
        report.reviewed_hash = None
        report.version += 1
        session.add(ReportVersion(report_id=report.id, version=report.version,
                                  content=content, bound_facts=report.bound_facts,
                                  analysis_run_id=report.analysis_run_id))
        source_bindings = [{"block_id": block["id"], "section_id": body.section_id,
                            "refs": block.get("source_refs", []), "project_rule_refs": []}
                           for block in new_blocks if block.get("source_refs")]
        if source_bindings and run and run.snapshot.get("corpus_id"):
            session.add(WritingCommitEvent(project_id=project_id, report_id=report.id,
                report_version=report.version, section_id=body.section_id,
                corpus_id=run.snapshot["corpus_id"], corpus_version=run.snapshot["corpus_version"],
                mode="analysis", block_ids=[block["id"] for block in new_blocks],
                source_refs=source_bindings, issues=[], approved_item_ids=[],
                model_audit=body.model_audit))
        session.add(AnalysisWritingEvent(project_id=project_id, report_id=report.id,
            report_version=report.version, run_id=run.id if run else None, action="accept_candidate",
            section_id=body.section_id,
            payload={"mode": body.mode, "block_ids": [block["id"] for block in adopted],
                     "candidate_slots": slots,
                     "selected_block_ids": body.selected_block_ids,
                     "model_block_ids": [block["id"] for block in new_blocks
                                         if block.get("origin") == "model" and
                                         block["id"] in {item["id"] for item in body.content}],
                     "input_sha256": run.input_sha256 if run else None,
                     "configuration_id": body.config_id, "model_audit": body.model_audit,
                     "preserved_block_ids": [block.get("id") for block in protected],
                     "replaced_block_ids": [block.get("id") for block in existing
                                            if block.get("id") not in {kept.get("id") for kept in protected}]}))
        if stored:
            stored.status = "ACCEPTED"
            stored.accepted_block_ids = [block["id"] for block in new_blocks]
            stored.adoption_request_sha256 = _adoption_digest(body)
            stored.accepted_report_version = report.version
        session.flush()
        return {"report": _report_dict(report, _current_facts(session, project_id), session),
                "changes": impacts}
