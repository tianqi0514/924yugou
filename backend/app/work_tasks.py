"""Durable, project-owned model and export tasks for the local single-user service."""

from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, Thread
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .analysis_writing import DraftRequest, _draft_preview
from .chapter_materials import chapter_pack
from .db import Project, ReportDraft, SessionLocal, WorkTask, utcnow
from .report_pipeline import content_hash


router = APIRouter(prefix="/api/projects/{project_id}/tasks", tags=["写作任务"])
LEASE_SECONDS = 90
HEARTBEAT_SECONDS = 20
executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="report-task")


class TaskCreate(BaseModel):
    kind: str = Field(pattern="^(model_draft|export)$")
    report_id: str
    request_key: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    section_id: str | None = None
    run_id: str | None = None
    config_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    level: str | None = Field(default=None, pattern="^(formal|preview|scenario)$")


def _task_dict(row: WorkTask) -> dict:
    return {"id": row.id, "project_id": row.project_id, "report_id": row.report_id,
            "kind": row.kind, "status": row.status, "stage": row.stage,
            "attempt": row.attempt, "result": row.result, "error": row.error,
            "created_at": row.created_at.isoformat(),
            "updated_at": row.updated_at.isoformat()}


def _digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


@router.post("", status_code=201)
def task_create(project_id: str, body: TaskCreate):
    with SessionLocal.begin() as session:
        if session.scalar(select(Project.id).where(Project.id == project_id).with_for_update()) is None:
            raise HTTPException(404, "项目不存在")
        report = session.scalar(select(ReportDraft).where(
            ReportDraft.id == body.report_id, ReportDraft.project_id == project_id))
        if report is None:
            raise HTTPException(404, "报告不存在")
        if body.kind == "model_draft":
            if not body.section_id or bool(body.run_id) == bool(body.config_id) or body.level:
                raise HTTPException(400, "请选择本章的文字配置或推演运行")
            request = DraftRequest(section_id=body.section_id, mode="model", run_id=body.run_id,
                                   config_id=body.config_id, evidence_ids=body.evidence_ids)
            pack = chapter_pack(session, project_id, report, request.section_id)
            payload = {**request.model_dump(), "report_version": report.version,
                       "pack_sha256": pack["watermark_sha256"]}
        else:
            if not body.level or body.section_id or body.run_id or body.config_id or body.evidence_ids:
                raise HTTPException(400, "请选择导出用途")
            payload = {"report_version": report.version, "level": body.level,
                       "content_sha256": content_hash(report.content)}
        digest = _digest(payload)
        existing = session.scalar(select(WorkTask).where(
            WorkTask.project_id == project_id, WorkTask.kind == body.kind,
            WorkTask.request_key == body.request_key))
        if existing:
            if existing.report_id != report.id or existing.input_sha256 != digest:
                raise HTTPException(409, "该请求标识已经用于其他内容")
            result = _task_dict(existing)
        else:
            task = WorkTask(project_id=project_id, report_id=report.id, kind=body.kind,
                            request_key=body.request_key, input_sha256=digest, payload=payload)
            session.add(task); session.flush()
            result = _task_dict(task)
    if result["status"] == "PENDING":
        executor.submit(process_one, result["id"])
    return result


@router.get("")
def tasks_list(project_id: str, report_id: str | None = None):
    recover_expired()
    with SessionLocal() as session:
        query = select(WorkTask).where(WorkTask.project_id == project_id)
        if report_id:
            report = session.scalar(select(ReportDraft.id).where(
                ReportDraft.id == report_id, ReportDraft.project_id == project_id))
            if report is None:
                raise HTTPException(404, "报告不存在")
            query = query.where(WorkTask.report_id == report_id)
        return [_task_dict(row) for row in session.scalars(
            query.order_by(WorkTask.created_at.desc()).limit(30)).all()]


@router.get("/{task_id}")
def task_get(project_id: str, task_id: str):
    recover_expired()
    with SessionLocal() as session:
        row = session.scalar(select(WorkTask).where(
            WorkTask.id == task_id, WorkTask.project_id == project_id))
        if row is None:
            raise HTTPException(404, "任务不存在")
        return _task_dict(row)


@router.post("/{task_id}/cancel")
def task_cancel(project_id: str, task_id: str):
    with SessionLocal.begin() as session:
        row = session.scalar(select(WorkTask).where(
            WorkTask.id == task_id, WorkTask.project_id == project_id).with_for_update())
        if row is None:
            raise HTTPException(404, "任务不存在")
        if row.status == "COMPLETED":
            raise HTTPException(409, "任务已完成")
        if row.status not in {"FAILED", "UNCERTAIN"}:
            row.status = "CANCELLED"
            row.stage = "CANCELLED"
            row.cancel_requested = True
        return _task_dict(row)


@router.post("/{task_id}/retry")
def task_retry(project_id: str, task_id: str):
    with SessionLocal.begin() as session:
        row = session.scalar(select(WorkTask).where(
            WorkTask.id == task_id, WorkTask.project_id == project_id).with_for_update())
        if row is None:
            raise HTTPException(404, "任务不存在")
        if row.status not in {"FAILED", "UNCERTAIN", "CANCELLED"}:
            raise HTTPException(409, "当前任务无需重试")
        report = session.scalar(select(ReportDraft).where(
            ReportDraft.id == row.report_id, ReportDraft.project_id == project_id))
        if report is None or report.version != row.payload["report_version"]:
            raise HTTPException(409, "报告已变化，请创建新任务")
        if row.kind == "model_draft" and chapter_pack(
                session, project_id, report, row.payload["section_id"])["watermark_sha256"] != row.payload["pack_sha256"]:
            raise HTTPException(409, "写作依据已变化，请创建新任务")
        row.status, row.stage, row.cancel_requested = "PENDING", "QUEUED", False
        row.generation, row.error, row.result = None, None, {}
        result = _task_dict(row)
    executor.submit(process_one, task_id)
    return result


def recover_expired() -> int:
    """A crashed worker never silently retries an uncertain external model call."""
    count = 0
    with SessionLocal.begin() as session:
        rows = session.scalars(select(WorkTask).where(
            WorkTask.status == "RUNNING", WorkTask.lease_expires_at < utcnow()).with_for_update(skip_locked=True)).all()
        for row in rows:
            row.status, row.stage = "UNCERTAIN", "RETRY_REQUIRED"
            row.error = "工作进程中断；请核对后重试"
            count += 1
    return count


def _heartbeat(task_id: str, generation: str, stop: Event) -> None:
    """A living worker renews its lease without holding a report lock."""
    while not stop.wait(HEARTBEAT_SECONDS):
        try:
            with SessionLocal.begin() as session:
                row = session.scalar(select(WorkTask).where(WorkTask.id == task_id).with_for_update())
                if row is None or row.status != "RUNNING" or row.generation != generation:
                    return
                row.lease_expires_at = utcnow() + timedelta(seconds=LEASE_SECONDS)
        except Exception:
            # A transient database outage does not turn an unknown remote model
            # response into success. Lease expiry will mark it uncertain.
            return


def process_one(task_id: str | None = None) -> bool:
    recover_expired()
    with SessionLocal.begin() as session:
        query = select(WorkTask).where(WorkTask.status == "PENDING")
        if task_id:
            query = query.where(WorkTask.id == task_id)
        row = session.scalar(query.order_by(WorkTask.created_at).with_for_update(skip_locked=True))
        if row is None:
            return False
        generation = str(uuid4())
        row.status, row.stage = "RUNNING", "CALLING_MODEL" if row.kind == "model_draft" else "RENDERING"
        row.generation = generation
        row.attempt += 1
        row.lease_expires_at = utcnow() + timedelta(seconds=LEASE_SECONDS)
        project_id, report_id, kind, payload = row.project_id, row.report_id, row.kind, dict(row.payload)
        running_id = row.id
    heartbeat_stop = Event()
    heartbeat = Thread(target=_heartbeat, args=(running_id, generation, heartbeat_stop), daemon=True)
    heartbeat.start()
    try:
        with SessionLocal() as session:
            report = session.scalar(select(ReportDraft).where(
                ReportDraft.id == report_id, ReportDraft.project_id == project_id))
            if report is None or report.version != payload["report_version"]:
                raise HTTPException(409, "报告版本已变化，请创建新任务")
            if kind == "model_draft" and chapter_pack(
                    session, project_id, report, payload["section_id"])["watermark_sha256"] != payload["pack_sha256"]:
                raise HTTPException(409, "写作依据已变化，请创建新任务")
        if kind == "model_draft":
            _draft_preview(project_id, report_id, DraftRequest.model_validate(payload), (task_id or row.id, generation))
        else:
            from .main import _report_export
            _report_export(project_id, report_id, payload["level"], (task_id or row.id, generation))
    except Exception as exc:
        message = str(exc.detail) if isinstance(exc, HTTPException) else "任务执行失败，请检查模型连接或导出条件"
        with SessionLocal.begin() as session:
            current = session.scalar(select(WorkTask).where(WorkTask.id == (task_id or row.id)).with_for_update())
            if current and current.generation == generation and current.status == "RUNNING":
                current.status, current.stage = "FAILED", "FAILED"
                current.error = message[:450]
        return True
    finally:
        heartbeat_stop.set()
        heartbeat.join(timeout=1)
    return True


def run_forever() -> None:
    from .db import init_db
    init_db()
    while True:
        if not process_one():
            time.sleep(1)


if __name__ == "__main__":
    run_forever()
