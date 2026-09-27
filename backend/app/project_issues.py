"""Explicit project claims, conflicts and gaps with scoped report impacts."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .db import Project, ProjectEvidence, ProjectIssue, ReportDraft, SessionLocal
from .report_pipeline import plain
from .document_pipeline import source_supports


router = APIRouter(prefix="/api/projects/{project_id}/issues", tags=["论断与问题"])
_section = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,79}$")


class IssueCreate(BaseModel):
    kind: str = Field(pattern="^(claim|conflict|gap)$")
    title: str = Field(min_length=1, max_length=160)
    statement: str = Field(min_length=1, max_length=1200)
    section_ids: list[str] = Field(min_length=1, max_length=20)
    support_evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    opposing_evidence_ids: list[str] = Field(default_factory=list, max_length=20)


class IssueDecision(BaseModel):
    revision: int = Field(ge=1)
    status: str = Field(pattern="^(OPEN|LIMITED|RESOLVED)$")
    decision: str = Field(min_length=1, max_length=1200)
    resolution_evidence_id: str | None = None


def _project(session, project_id: str):
    if session.get(Project, project_id) is None:
        raise HTTPException(404, "项目不存在")


def _evidence_ids(session, project_id: str, ids: list[str]) -> None:
    if len(ids) != len(set(ids)):
        raise HTTPException(400, "证据不能重复")
    found = session.scalars(select(ProjectEvidence.id).where(
        ProjectEvidence.project_id == project_id, ProjectEvidence.id.in_(ids))).all()
    if len(found) != len(ids):
        raise HTTPException(404, "证据不属于本项目")


def _impacts(session, item: ProjectIssue) -> list[dict]:
    rows = []
    reports = session.scalars(select(ReportDraft).where(ReportDraft.project_id == item.project_id)).all()
    for report in reports:
        for position, block in enumerate(report.content, 1):
            if block.get("section_id") in item.section_ids and block.get("type") not in {"h1", "h2", "h3"}:
                if plain(block).strip():
                    rows.append({"report_id": report.id, "report_version": report.version,
                                 "report_title": report.title, "section_id": block.get("section_id"),
                                 "block_id": block.get("id"), "position": position})
    return rows


def _dict(session, item: ProjectIssue) -> dict:
    evidence = session.scalars(select(ProjectEvidence).where(
        ProjectEvidence.project_id == item.project_id,
        ProjectEvidence.id.in_(item.support_evidence_ids + item.opposing_evidence_ids +
                                ([item.resolution_evidence_id] if item.resolution_evidence_id else [])))).all()
    by_id = {row.id: row for row in evidence}
    def sources(ids: list[str]) -> list[dict]:
        return [{"id": key, "label": by_id[key].label, "statement": by_id[key].statement,
                 "source_type": by_id[key].source_type, "document_id": by_id[key].document_id,
                 "source_refs": by_id[key].source_refs} for key in ids if key in by_id]
    return {"id": item.id, "project_id": item.project_id, "kind": item.kind,
            "title": item.title, "statement": item.statement,
            "section_ids": item.section_ids, "status": item.status,
            "decision": item.decision, "revision": item.revision,
            "support": sources(item.support_evidence_ids),
            "opposing": sources(item.opposing_evidence_ids),
            "resolution_evidence_id": item.resolution_evidence_id,
            "independent_verification": "not_recorded",
            "impacts": _impacts(session, item),
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat()}


def _invalidate(session, item: ProjectIssue) -> None:
    for report_id in {row["report_id"] for row in _impacts(session, item)}:
        report = session.get(ReportDraft, report_id)
        if report is not None:
            report.reviewed_hash = None


@router.get("")
def issue_list(project_id: str, section_id: str | None = None):
    with SessionLocal() as session:
        _project(session, project_id)
        items = session.scalars(select(ProjectIssue).where(
            ProjectIssue.project_id == project_id).order_by(ProjectIssue.created_at.desc())).all()
        if section_id:
            items = [row for row in items if section_id in row.section_ids]
        return {"items": [_dict(session, row) for row in items]}


@router.post("", status_code=201)
def issue_create(project_id: str, body: IssueCreate):
    if any(not _section.fullmatch(value) for value in body.section_ids) or len(set(body.section_ids)) != len(body.section_ids):
        raise HTTPException(400, "章节范围无效")
    if set(body.support_evidence_ids) & set(body.opposing_evidence_ids):
        raise HTTPException(400, "同一证据不能同时支持和反对")
    if body.kind == "conflict" and (not body.support_evidence_ids or not body.opposing_evidence_ids):
        raise HTTPException(400, "冲突须保留双方来源")
    if body.kind == "claim" and not body.support_evidence_ids:
        raise HTTPException(400, "论断至少需要一项支撑来源")
    with SessionLocal.begin() as session:
        _project(session, project_id)
        _evidence_ids(session, project_id, body.support_evidence_ids + body.opposing_evidence_ids)
        item = ProjectIssue(project_id=project_id, kind=body.kind,
                            title=body.title.strip(), statement=body.statement.strip(),
                            section_ids=body.section_ids,
                            support_evidence_ids=body.support_evidence_ids,
                            opposing_evidence_ids=body.opposing_evidence_ids)
        session.add(item); session.flush()
        _invalidate(session, item)
        return _dict(session, item)


@router.get("/{issue_id}")
def issue_get(project_id: str, issue_id: str):
    with SessionLocal() as session:
        item = session.scalar(select(ProjectIssue).where(
            ProjectIssue.project_id == project_id, ProjectIssue.id == issue_id))
        if item is None:
            raise HTTPException(404, "问题不存在")
        return _dict(session, item)


@router.post("/{issue_id}/decision")
def issue_decide(project_id: str, issue_id: str, body: IssueDecision):
    with SessionLocal.begin() as session:
        item = session.scalar(select(ProjectIssue).where(
            ProjectIssue.project_id == project_id, ProjectIssue.id == issue_id).with_for_update())
        if item is None:
            raise HTTPException(404, "问题不存在")
        if item.revision != body.revision:
            raise HTTPException(409, "问题已变化，请刷新")
        if body.status == "RESOLVED":
            if not body.resolution_evidence_id or body.resolution_evidence_id in (
                    item.support_evidence_ids + item.opposing_evidence_ids):
                raise HTTPException(409, "裁决须引用双方之外的新证据")
            _evidence_ids(session, project_id, [body.resolution_evidence_id])
            resolution = session.get(ProjectEvidence, body.resolution_evidence_id)
            if resolution is None or not source_supports(body.decision.strip(), resolution.excerpt):
                raise HTTPException(409, "裁决依据须直接出现在新增来源中；解释性结论请保留待核对")
        elif body.resolution_evidence_id:
            raise HTTPException(400, "只有裁决时才能指定新证据")
        item.status, item.decision = body.status, body.decision.strip()
        item.resolution_evidence_id = body.resolution_evidence_id
        item.revision += 1
        _invalidate(session, item)
        session.flush()
        return _dict(session, item)
