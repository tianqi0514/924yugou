"""Explicit, source-checked project fact mapping for a report chapter.

This is a short path into the existing immutable AnalysisConfig contract.  The
preview is read-only; commit repeats every check under a project lock.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .analysis import _calculate, _canonical
from .analysis_config import _digest as config_digest, _dict as config_dict, _normalize
from .db import (AnalysisConfig, Project, ProjectFact, ReportDraft, RuleRecord,
                 SessionLocal, utcnow)
from .rules import RuleError


router = APIRouter(prefix="/api/projects/{project_id}/reports/{report_id}/chapter-mapping",
                   tags=["章节资料映射"])
_secret = secrets.token_bytes(32)


class MappingChoice(BaseModel):
    fact_keys: list[str] = Field(min_length=1, max_length=12)
    result_keys: list[str] = Field(default_factory=list, max_length=8)
    mode: str = Field(pattern="^(narrative|calculation)$")


class MappingCommit(MappingChoice):
    preview_digest: str = Field(min_length=64, max_length=64)
    preview_token: str
    expires_at: int


def _scope(session, project_id: str, report_id: str, section_id: str):
    project = session.get(Project, project_id)
    report = session.scalar(select(ReportDraft).where(
        ReportDraft.id == report_id, ReportDraft.project_id == project_id))
    if project is None or report is None:
        raise HTTPException(404, "项目或报告不存在")
    if project.corpus is not None:
        raise HTTPException(403, "内置历史语料只读；请在新项目映射本项目资料")
    section = next((item for item in (report.template_snapshot or {}).get("sections", [])
                    if item["id"] == section_id), None)
    if section is None or not any(block.get("type") == "h2" and block.get("section_id") == section_id
                                  for block in report.content):
        raise HTTPException(404, "本报告没有此章节")
    if report.report_type not in {"government_feasibility", "enterprise_feasibility"}:
        raise HTTPException(409, "此报告类型尚无章节映射模板，请使用完整配置")
    return project, report, section


def _source(session, project_id: str, fact: ProjectFact) -> dict:
    from .main import _fact_has_project_evidence, _reviewed_fact_binding

    binding = _reviewed_fact_binding(session, project_id, fact)
    location = None
    if binding is not None:
        record, document, segments = binding
        location = {"document_id": document.id, "filename": document.filename,
                    "ref": record.source_refs[0], "page": segments[0].get("page") or 1}
    return {"verified": _fact_has_project_evidence(session, project_id, fact, set()),
            "location": location}


def _materialize(session, project_id: str, report_id: str, section_id: str,
                 body: MappingChoice) -> dict:
    project, report, section = _scope(session, project_id, report_id, section_id)
    keys = body.fact_keys
    if len(set(keys)) != len(keys) or len(set(body.result_keys)) != len(body.result_keys):
        raise HTTPException(400, "事实或结果不能重复选择")
    if body.mode == "narrative" and (body.result_keys or len(keys) > 3):
        raise HTTPException(400, "文字章节最多选择三项原文事实，不选择计算指标")
    if body.mode == "calculation" and (not body.result_keys or
                                       not set(body.result_keys).issubset(keys)):
        raise HTTPException(400, "请从所选事实中选择至少一个数值指标")
    facts = {row.key: row for row in session.scalars(select(ProjectFact).where(
        ProjectFact.project_id == project_id)).all()}
    rules = {row.target_key: row for row in session.scalars(select(RuleRecord).where(
        RuleRecord.project_id == project_id)).all()}
    if any(key not in facts for key in keys):
        raise HTTPException(404, "所选事实不属于本项目")
    if body.mode == "narrative" and not any(facts[key].data_type == "text" for key in keys):
        raise HTTPException(400, "原文文字章节至少需要一条完整的文字事实")
    if body.mode == "calculation" and any(
            facts[key].data_type not in {"integer", "decimal"} for key in body.result_keys):
        raise HTTPException(400, "计算指标须为数值事实")

    ordered: list[str] = []
    visiting: set[str] = set()
    def visit(key: str) -> None:
        if key in visiting:
            raise HTTPException(400, "项目规则存在循环依赖")
        if key in ordered:
            return
        if key not in facts:
            raise HTTPException(409, f"规则依赖的项目事实 {key} 不存在")
        visiting.add(key)
        rule = rules.get(key)
        if body.mode == "calculation" and rule is not None:
            for dependency in rule.deps:
                visit(dependency)
        visiting.remove(key)
        ordered.append(key)
    for key in keys:
        visit(key)

    sources = {key: _source(session, project_id, facts[key]) for key in ordered}
    blocked = [facts[key].label for key in ordered if facts[key].value_text is None
               or not sources[key]["verified"]]
    if blocked:
        raise HTTPException(409, "请先填写并核对原文位置：" + "、".join(blocked))
    definitions = [{"key": key, "label": facts[key].label,
                    "data_type": facts[key].data_type, "unit": facts[key].unit,
                    "group": "本项目事实", "computed": body.mode == "calculation" and key in rules,
                    "caliber": facts[key].caliber, "period": facts[key].as_of}
                   for key in ordered]
    config_rules = [{"id": "R_" + rules[key].id.replace("-", ""),
                     "name": rules[key].name, "target_key": key,
                     "expression": rules[key].expression}
                    for key in ordered if body.mode == "calculation" and key in rules]
    evidence_keys = [key for key in ordered if key not in rules] if body.mode == "calculation" else keys
    config_section = {"id": section_id, "title": section["title"],
                      "report_type": report.report_type,
                      "kind": body.mode, "result_keys": body.result_keys,
                      "evidence_keys": evidence_keys, "conditions": [], "forbidden_terms": []}
    fields, normalized_rules, sections = _normalize(definitions, config_rules,
                                                    [config_section], complete=True)
    try:
        inputs = {key: {"value": _canonical(facts[key].value_text, facts[key].data_type),
                        "origin": "project_fact", "source_ref": None,
                        "review_status": "source_locator_reviewed"}
                  for key in ordered if key not in rules or body.mode == "narrative"}
    except RuleError as exc:
        raise HTTPException(400, str(exc)) from exc
    trial = SimpleNamespace(id="chapter-mapping-preview", revision=1, blueprint_version="project-facts",
                            corpus_id=None, corpus_version=None, definitions=fields,
                            rules=normalized_rules)
    snapshot = _calculate(trial, inputs)
    if snapshot["status"] != "COMPUTED" or any(
            snapshot["results"][key]["value"] is None for key in body.result_keys):
        raise HTTPException(409, "本章指标不可评估，请先核对规则与输入")
    name = f"{section['title']} · 项目资料"
    watermark = {"project_id": project_id, "report_id": report_id,
                 "report_version": report.version, "project_version": project.version,
                 "section_id": section_id, "mode": body.mode, "fact_keys": keys,
                 "result_keys": body.result_keys,
                 "facts": [(key, facts[key].revision, facts[key].value_text,
                             sources[key]["verified"], sources[key]["location"])
                            for key in ordered],
                 "rules": [(key, rules[key].revision, rules[key].expression)
                           for key in ordered if body.mode == "calculation" and key in rules]}
    digest = hashlib.sha256(json.dumps(watermark, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")).encode()).hexdigest()
    return {"name": name, "report_type": report.report_type,
            "definitions": fields, "rules": normalized_rules,
            "sections": sections, "snapshot": snapshot,
            "sources": sources, "preview_digest": digest,
            "config_checksum": config_digest(name, fields, normalized_rules, sections)}


def _token(project_id: str, report_id: str, section_id: str, digest: str, expires: int) -> str:
    message = f"{project_id}:{report_id}:{section_id}:{digest}:{expires}"
    return hmac.new(_secret, message.encode(), hashlib.sha256).hexdigest()


@router.get("/{section_id}")
def mapping_options(project_id: str, report_id: str, section_id: str):
    with SessionLocal() as session:
        _, report, section = _scope(session, project_id, report_id, section_id)
        facts = session.scalars(select(ProjectFact).where(
            ProjectFact.project_id == project_id).order_by(ProjectFact.label)).all()
        configs = session.scalars(select(AnalysisConfig).where(
            AnalysisConfig.project_id == project_id,
            AnalysisConfig.status == "PUBLISHED").order_by(AnalysisConfig.version.desc())).all()
        current = next((item for item in configs if any(
            row["id"] == section_id and row.get("report_type", report.report_type) == report.report_type
            for row in item.sections)), None)
        return {"section_id": section_id, "title": section["title"],
                "template_kind": section["kind"],
                "current_config": {"id": current.id, "version": current.version,
                                   **next({"evidence_keys": row.get("evidence_keys", []),
                                           "result_keys": row.get("result_keys", []),
                                           "kind": row.get("kind", "calculation")}
                                          for row in current.sections if row["id"] == section_id
                                          and row.get("report_type", report.report_type) == report.report_type)} if current else None,
                "facts": [{"key": fact.key, "label": fact.label,
                           "data_type": fact.data_type, "value": fact.value_text,
                           "unit": fact.unit, "revision": fact.revision,
                           "status": fact.value_status,
                           "source": _source(session, project_id, fact)} for fact in facts]}


@router.post("/{section_id}/preview")
def mapping_preview(project_id: str, report_id: str, section_id: str, body: MappingChoice):
    with SessionLocal() as session:
        result = _materialize(session, project_id, report_id, section_id, body)
        expires = int(time.time()) + 600
        return {"section_id": section_id, "mode": body.mode,
                "fact_keys": body.fact_keys, "result_keys": body.result_keys,
                "preview_digest": result["preview_digest"], "expires_at": expires,
                "preview_token": _token(project_id, report_id, section_id,
                                        result["preview_digest"], expires),
                "results": [{"key": key, "label": result["snapshot"]["results"][key]["label"],
                             "value": result["snapshot"]["results"][key]["value"],
                             "unit": result["snapshot"]["results"][key]["unit"]}
                            for key in body.result_keys],
                "trace": result["snapshot"]["trace"],
                "sources": result["sources"]}


@router.post("/{section_id}/commit")
def mapping_commit(project_id: str, report_id: str, section_id: str, body: MappingCommit):
    if body.expires_at < time.time() or not hmac.compare_digest(
            body.preview_token, _token(project_id, report_id, section_id,
                                       body.preview_digest, body.expires_at)):
        raise HTTPException(409, "预览已失效，请重新预览")
    with SessionLocal.begin() as session:
        project = session.scalar(select(Project).where(Project.id == project_id).with_for_update())
        if project is None:
            raise HTTPException(404, "项目不存在")
        result = _materialize(session, project_id, report_id, section_id, body)
        if result["preview_digest"] != body.preview_digest:
            raise HTTPException(409, "项目事实、规则或报告已变化，请重新预览")
        existing = session.scalar(select(AnalysisConfig).where(
            AnalysisConfig.project_id == project_id,
            AnalysisConfig.status == "PUBLISHED",
            AnalysisConfig.checksum == result["config_checksum"]))
        if existing and any(row["id"] == section_id and
                            row.get("report_type") == result["report_type"]
                            for row in existing.sections):
            return config_dict(existing)
        draft = session.scalar(select(AnalysisConfig).where(
            AnalysisConfig.project_id == project_id, AnalysisConfig.status == "DRAFT"))
        if draft:
            raise HTTPException(409, "请先完成现有配置草稿")
        version = (session.scalar(select(func.max(AnalysisConfig.version)).where(
            AnalysisConfig.project_id == project_id)) or 0) + 1
        item = AnalysisConfig(project_id=project_id, version=version,
                              name=result["name"], definitions=result["definitions"],
                              rules=result["rules"], sections=result["sections"],
                              checksum=result["config_checksum"], status="PUBLISHED",
                              published_at=utcnow())
        session.add(item)
        session.flush()
        return config_dict(item)
