"""Project-owned, immutable source excerpts and explicit replacement previews."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .db import ProjectEvidence, ProjectFact, ReportDraft, SessionLocal, SourceDocument, SourceParseRevision
from .document_pipeline import source_supports
from .report_pipeline import plain


router = APIRouter(prefix="/api/projects/{project_id}/evidence", tags=["项目证据"])
_secret = secrets.token_bytes(32)


class EvidenceCreate(BaseModel):
    document_id: str
    source_refs: list[str] = Field(min_length=1, max_length=5)
    statement: str = Field(min_length=1, max_length=1200)
    label: str = Field(min_length=1, max_length=160)
    subject: str = Field(default="", max_length=160)
    asserted_at: str = Field(default="", max_length=40)
    source_type: str = Field(default="original", pattern="^(original|secondary)$")
    fact_key: str | None = None


class EvidenceReplacement(EvidenceCreate):
    base_report_versions: dict[str, int] = Field(default_factory=dict)
    expires_at: int | None = None
    preview_token: str | None = None


def _project(session, project_id: str):
    from .analysis import _project as get_project
    return get_project(session, project_id)


def _prepare(session, project_id: str, body: EvidenceCreate) -> dict:
    document = session.scalar(select(SourceDocument).where(SourceDocument.id == body.document_id,
                                                           SourceDocument.project_id == project_id))
    if document is None:
        raise HTTPException(404, "本项目原件不存在")
    parsed = session.scalar(select(SourceParseRevision).where(
        SourceParseRevision.document_id == document.id,
        SourceParseRevision.revision == document.parse_revision))
    if parsed is None or parsed.document_sha256 != document.sha256:
        raise HTTPException(409, "原件的解析版本尚未记录")
    if len(set(body.source_refs)) != len(body.source_refs):
        raise HTTPException(400, "原文位置不能重复")
    by_ref = {part.get("ref"): part for part in parsed.segments}
    if any(ref not in by_ref for ref in body.source_refs):
        raise HTTPException(409, "原文位置已失效")
    excerpt = "\n".join(str(by_ref[ref].get("text", "")) for ref in body.source_refs)
    statement = body.statement.strip()
    if not any(source_supports(statement, str(by_ref[ref].get("text", ""))) for ref in body.source_refs):
        raise HTTPException(400, "证据陈述须直接出现在所选原文位置")
    revision = None
    if body.fact_key:
        fact = session.scalar(select(ProjectFact).where(ProjectFact.project_id == project_id,
                                                        ProjectFact.key == body.fact_key))
        if (fact is None or fact.value_text is None or fact.value_status != "PROVIDED"
                or not any(source_supports(fact.value_text, str(by_ref[ref].get("text", "")))
                           for ref in body.source_refs)):
            raise HTTPException(409, "所选位置不能支持本项目事实的当前值")
        revision = fact.revision
    return {"project_id": project_id, "document_id": document.id,
            "document_sha256": document.sha256, "parse_revision_id": parsed.id,
            "source_refs": list(body.source_refs),
            "excerpt": excerpt, "statement": statement, "label": body.label.strip(),
            "subject": body.subject.strip(), "asserted_at": body.asserted_at.strip(),
            "source_type": body.source_type, "fact_key": body.fact_key,
            "fact_revision": revision}


def _dict(item: ProjectEvidence, document: SourceDocument | None = None,
          parsed: SourceParseRevision | None = None) -> dict:
    from .main import STORAGE
    refs = {part.get("ref"): part for part in (parsed.segments if parsed else document.segments if document else [])}
    pages = [refs[ref].get("page") for ref in item.source_refs if ref in refs]
    page = pages[0] if pages else None
    return {"id": item.id, "project_id": item.project_id, "document_id": item.document_id,
            "document_sha256": item.document_sha256, "parse_revision_id": item.parse_revision_id,
            "parse_revision": parsed.revision if parsed else None,
            "document_filename": document.filename if document else None,
            "source_refs": item.source_refs, "excerpt": item.excerpt, "statement": item.statement,
            "label": item.label, "subject": item.subject, "asserted_at": item.asserted_at,
            "source_type": item.source_type, "review_status": "source_location_selected",
            "independent_verification": "not_recorded",
            "full_original_available": bool(document and item.source_type == "original" and
                                            (STORAGE / document.storage_name).is_file()),
            "fact_key": item.fact_key, "fact_revision": item.fact_revision,
            "supersedes_id": item.supersedes_id, "created_at": item.created_at.isoformat(),
            "page": page, "original_url": f"/api/projects/{item.project_id}/documents/{item.document_id}/original"
            + (f"#page={page}" if document and document.file_kind == "pdf" and page else "")}


def _evidence(session, project_id: str, evidence_id: str) -> ProjectEvidence:
    item = session.scalar(select(ProjectEvidence).where(ProjectEvidence.id == evidence_id,
                                                       ProjectEvidence.project_id == project_id))
    if item is None:
        raise HTTPException(404, "证据不存在")
    return item


def _impacts(session, project_id: str, evidence_id: str) -> list[dict]:
    impacts = []
    reports = session.scalars(select(ReportDraft).where(ReportDraft.project_id == project_id)).all()
    for report in reports:
        for position, block in enumerate(report.content, 1):
            if any(ref.get("evidence_id") == evidence_id for ref in block.get("project_evidence_refs", [])):
                impacts.append({"report_id": report.id, "report_title": report.title,
                                "report_version": report.version, "position": position,
                                "block_id": block.get("id"), "text": plain(block)[:240]})
    return impacts


@router.get("")
def evidence_list(project_id: str, fact_key: str | None = None, document_id: str | None = None, q: str = ""):
    with SessionLocal() as session:
        _project(session, project_id)
        query = select(ProjectEvidence).where(ProjectEvidence.project_id == project_id)
        if fact_key:
            query = query.where(ProjectEvidence.fact_key == fact_key)
        if document_id:
            query = query.where(ProjectEvidence.document_id == document_id)
        rows = session.scalars(query.order_by(ProjectEvidence.created_at.desc()).limit(300)).all()
        if q.strip():
            query_text = q.strip().casefold()
            rows = [row for row in rows if query_text in (row.label + row.statement + row.subject).casefold()]
        docs = {doc.id: doc for doc in session.scalars(select(SourceDocument).where(
            SourceDocument.project_id == project_id,
            SourceDocument.id.in_([row.document_id for row in rows]))).all()} if rows else {}
        parsed = {rev.id: rev for rev in session.scalars(select(SourceParseRevision).where(
            SourceParseRevision.id.in_([row.parse_revision_id for row in rows if row.parse_revision_id]))).all()} if rows else {}
        return {"items": [_dict(row, docs.get(row.document_id), parsed.get(row.parse_revision_id)) for row in rows]}


@router.post("", status_code=201)
def evidence_create(project_id: str, body: EvidenceCreate):
    with SessionLocal.begin() as session:
        _project(session, project_id)
        values = _prepare(session, project_id, body)
        previous = session.scalars(select(ProjectEvidence).where(
            ProjectEvidence.project_id == project_id,
            ProjectEvidence.document_id == values["document_id"],
            ProjectEvidence.fact_key == values["fact_key"],
            ProjectEvidence.statement == values["statement"])).all()
        for item in previous:
            if item.source_refs == values["source_refs"] and item.fact_revision == values["fact_revision"]:
                return _dict(item, session.get(SourceDocument, item.document_id),
                             session.get(SourceParseRevision, item.parse_revision_id))
        item = ProjectEvidence(**values)
        session.add(item); session.flush()
        return _dict(item, session.get(SourceDocument, item.document_id),
                     session.get(SourceParseRevision, item.parse_revision_id))


@router.get("/{evidence_id}")
def evidence_get(project_id: str, evidence_id: str):
    with SessionLocal() as session:
        item = _evidence(session, project_id, evidence_id)
        return {**_dict(item, session.get(SourceDocument, item.document_id),
                        session.get(SourceParseRevision, item.parse_revision_id)),
                "impacts": _impacts(session, project_id, evidence_id)}


def _token(project_id: str, evidence_id: str, values: dict, impacts: list[dict], expires: int) -> str:
    payload = json.dumps([project_id, evidence_id, values, impacts, expires],
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hmac.new(_secret, payload.encode(), hashlib.sha256).hexdigest()


@router.post("/{evidence_id}/replacement/preview")
def evidence_replacement_preview(project_id: str, evidence_id: str, body: EvidenceCreate):
    with SessionLocal() as session:
        old = _evidence(session, project_id, evidence_id)
        values = _prepare(session, project_id, body)
        impacts = _impacts(session, project_id, old.id)
        expires = int(time.time()) + 600
        return {"old": _dict(old, session.get(SourceDocument, old.document_id),
                             session.get(SourceParseRevision, old.parse_revision_id)),
                "new": {key: value for key, value in values.items() if key != "excerpt"},
                "impacts": impacts, "base_report_versions": {x["report_id"]: x["report_version"] for x in impacts},
                "expires_at": expires, "preview_token": _token(project_id, evidence_id, values, impacts, expires)}


@router.post("/{evidence_id}/replacement/commit", status_code=201)
def evidence_replacement_commit(project_id: str, evidence_id: str, body: EvidenceReplacement):
    if not body.expires_at or body.expires_at < time.time() or not body.preview_token:
        raise HTTPException(409, "替换预览已过期")
    with SessionLocal.begin() as session:
        old = _evidence(session, project_id, evidence_id)
        values = _prepare(session, project_id, body)
        impacts = _impacts(session, project_id, old.id)
        if (body.base_report_versions != {x["report_id"]: x["report_version"] for x in impacts}
                or not hmac.compare_digest(body.preview_token,
                                           _token(project_id, evidence_id, values, impacts, body.expires_at))):
            raise HTTPException(409, "证据或受影响报告已变化，请重新预览")
        existing = session.scalar(select(ProjectEvidence).where(ProjectEvidence.project_id == project_id,
                                                                 ProjectEvidence.supersedes_id == old.id))
        if existing:
            if (existing.document_id == values["document_id"] and existing.source_refs == values["source_refs"]
                    and existing.statement == values["statement"]):
                return _dict(existing, session.get(SourceDocument, existing.document_id),
                             session.get(SourceParseRevision, existing.parse_revision_id))
            raise HTTPException(409, "该证据已经生成新版本")
        item = ProjectEvidence(**values, supersedes_id=old.id)
        session.add(item); session.flush()
        # Old report paragraphs keep the old evidence ID; only their current review watermark expires.
        for report_id in body.base_report_versions:
            report = session.get(ReportDraft, report_id)
            if report is not None:
                report.reviewed_hash = None
        return _dict(item, session.get(SourceDocument, item.document_id),
                     session.get(SourceParseRevision, item.parse_revision_id))
