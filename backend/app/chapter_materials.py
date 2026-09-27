"""Current-chapter material inventory; references are counted only when actually used."""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .corpus import CATEGORY_NAMES, GROUPS
from .corpus_storage import CorpusArchive, CorpusCategoryRow
from .db import (AnalysisConfig, AnalysisRun, ProjectCorpus, ProjectEvidence, ProjectFact,
                 ReportDraft, SessionLocal, WritingReference)
from .writing_materials import WritingMaterialSetting
from .writing_support import GUIDED_SECTIONS


router = APIRouter(prefix="/api/projects/{project_id}/reports/{report_id}/materials",
                   tags=["本章资料"])


class MaterialPolicy(BaseModel):
    category_id: str = Field(pattern=r"^CAT-(0[1-9]|[12][0-9]|30)$")
    mode: str = Field(pattern="^(auto|review|exclude)$")


class MaterialPolicySave(BaseModel):
    section_id: str = Field(min_length=1, max_length=80)
    base_revision: int = Field(ge=0)
    settings: list[MaterialPolicy] = Field(max_length=30)


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def chapter_pack(session, project_id: str, report: ReportDraft, section_id: str) -> dict:
    if report.project_id != project_id:
        raise HTTPException(404, "报告不存在")
    configs = session.scalars(select(AnalysisConfig).where(
        AnalysisConfig.project_id == project_id, AnalysisConfig.status == "PUBLISHED"
    ).order_by(AnalysisConfig.version.desc())).all()
    configured = next(((config, section) for config in configs for section in config.sections
                       if section["id"] == section_id and
                       section.get("report_type", report.report_type) == report.report_type), None)
    blocks = [block for block in report.content if block.get("section_id") == section_id]
    if not configured and not blocks and section_id not in GUIDED_SECTIONS:
        raise HTTPException(404, "本报告没有此章节")
    config, section = configured if configured else (None, None)
    expected_keys = set(section.get("evidence_keys", [])) if section else set()
    facts = session.scalars(select(ProjectFact).where(ProjectFact.project_id == project_id)).all()
    facts = [fact for fact in facts if fact.key in expected_keys]
    evidence = session.scalars(select(ProjectEvidence).where(
        ProjectEvidence.project_id == project_id).order_by(ProjectEvidence.created_at.desc())).all()
    evidence = [row for row in evidence if row.fact_key in expected_keys]
    superseded = set(session.scalars(select(ProjectEvidence.supersedes_id).where(
        ProjectEvidence.project_id == project_id,
        ProjectEvidence.supersedes_id.in_([row.id for row in evidence]))).all()) if evidence else set()
    fact_revisions = {fact.key: fact.revision for fact in facts}
    project_corpus = session.get(ProjectCorpus, project_id)
    reference = session.get(WritingReference, project_id)
    corpus_id = project_corpus.corpus_id if project_corpus else reference.corpus_id if reference else None
    archive = session.get(CorpusArchive, corpus_id) if corpus_id else None
    if reference and not project_corpus and (archive is None or archive.version != reference.corpus_version):
        raise HTTPException(409, "历史资料版本已变化，请重新选择")
    corpus_version = archive.version if archive else None
    category_rows = {row.number: row for row in session.scalars(select(CorpusCategoryRow).where(
        CorpusCategoryRow.corpus_id == corpus_id)).all()} if archive else {}
    setting = session.get(WritingMaterialSetting, (project_id, section_id))
    policy = setting.settings if setting else {}
    used_refs: dict[int, list[dict]] = {}
    for block in blocks:
        for ref in block.get("source_refs", []):
            if not corpus_id or ref.get("corpus_id") != corpus_id:
                continue
            raw_category = ref.get("category_id", "")
            try:
                number = int(raw_category.removeprefix("CAT-"))
            except (ValueError, AttributeError):
                continue
            if 1 <= number <= 30:
                used_refs.setdefault(number, []).append({
                    "block_id": block.get("id"), "record_id": ref.get("record_id"),
                    "artifact_id": ref.get("artifact_id"), "use": ref.get("use")})
    categories = []
    for number, label in enumerate(CATEGORY_NAMES, 1):
        category_id = f"CAT-{number:02d}"
        row = category_rows.get(number)
        used = used_refs.get(number, [])
        mode = policy.get(category_id, "auto")
        status = ("未生成" if number == 11 else
                  "部分未生成" if number == 30 else
                  "正文已引用" if used else
                  "未选择历史资料" if archive is None else
                  "本章未采用")
        categories.append({"category_id": category_id, "name": label,
                           "group": next(group["name"] for group in GROUPS if number in group["numbers"]),
                           "mode": mode, "status": status,
                           "corpus_id": corpus_id, "corpus_version": corpus_version,
                           "record_count": row.record_count if row else 0,
                           "used_records": used,
                           "reason": ("向量文件为零行" if number == 11 else
                                      "向量及匹配权重未生成" if number == 30 else
                                      "正文中有稳定来源引用" if used else
                                      "当前项目没有选择这套只读语料" if archive is None else
                                      "未发现本章正文对该类的记录级引用")})
    run = session.scalar(select(AnalysisRun).where(
        AnalysisRun.id == report.analysis_run_id,
        AnalysisRun.project_id == project_id)) if report.analysis_run_id else None
    watermark = {"project_id": project_id, "section_id": section_id,
                 "config_id": config.id if config else None,
                 "config_version": config.version if config else None,
                 "fact_revisions": {row.key: row.revision for row in facts},
                 "evidence": sorted((row.id, row.fact_revision, row.parse_revision_id) for row in evidence),
                 "run_id": run.id if run else None,
                 "corpus_id": corpus_id, "corpus_version": corpus_version,
                 "policy_revision": setting.revision if setting else 0}
    return {"project_id": project_id, "report_id": report.id,
            "report_version": report.version, "section_id": section_id,
            "config_id": watermark["config_id"], "config_version": watermark["config_version"],
            "run_id": watermark["run_id"], "corpus_id": corpus_id,
            "corpus_version": corpus_version, "policy_revision": watermark["policy_revision"],
            "watermark_sha256": _digest(watermark),
            "facts": [{"key": row.key, "label": row.label, "revision": row.revision,
                       "status": row.value_status} for row in facts],
            "project_evidence": [{"id": row.id, "label": row.label,
                                  "fact_key": row.fact_key, "fact_revision": row.fact_revision,
                                  "parse_revision_id": row.parse_revision_id,
                                  "source_type": row.source_type,
                                  "source_state": ("已更新" if row.id in superseded else
                                                   "旧事实修订" if fact_revisions.get(row.fact_key) != row.fact_revision else
                                                   "二手材料" if row.source_type == "secondary" else
                                                   "当前原文位置")}
                                 for row in evidence],
            "groups": [{"name": group["name"], "numbers": group["numbers"]} for group in GROUPS],
            "categories": categories}


@router.get("")
def chapter_materials_get(project_id: str, report_id: str, section_id: str):
    with SessionLocal() as session:
        report = session.scalar(select(ReportDraft).where(
            ReportDraft.id == report_id, ReportDraft.project_id == project_id))
        if report is None:
            raise HTTPException(404, "报告不存在")
        return chapter_pack(session, project_id, report, section_id)


@router.put("")
def chapter_materials_save(project_id: str, report_id: str, body: MaterialPolicySave):
    with SessionLocal.begin() as session:
        report = session.scalar(select(ReportDraft).where(
            ReportDraft.id == report_id, ReportDraft.project_id == project_id))
        if report is None:
            raise HTTPException(404, "报告不存在")
        before = chapter_pack(session, project_id, report, body.section_id)
        if not before["corpus_id"]:
            raise HTTPException(409, "本项目尚未选择只读历史语料")
        row = session.get(WritingMaterialSetting, (project_id, body.section_id))
        revision = row.revision if row else 0
        if body.base_revision != revision:
            raise HTTPException(409, "资料策略已变化，请刷新")
        modes = {}
        for item in body.settings:
            if item.category_id in modes:
                raise HTTPException(400, "资料类别重复")
            if item.mode != "auto":
                modes[item.category_id] = item.mode
        if row is None:
            row = WritingMaterialSetting(project_id=project_id, section_id=body.section_id,
                                         revision=1, settings=modes)
            session.add(row)
        elif modes != row.settings:
            row.revision += 1
            row.settings = modes
        session.flush()
        return chapter_pack(session, project_id, report, body.section_id)
