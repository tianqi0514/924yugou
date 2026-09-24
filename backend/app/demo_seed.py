"""预置公开报告演示原件；不抽取候选，也不确认任何事实。

Run from report-platform with:
    PYTHONPATH=backend .venv/bin/python backend/scripts/seed_demo_documents.py

Each report has its own project so facts from unrelated reports cannot mix.
The four original PDFs remain in test-fixtures and are copied to the normal
project document store. Re-running verifies the existing copies and adds none.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid5

from sqlalchemy import select

from app.db import Project, ProjectFact, SessionLocal, SourceDocument
from app.document_pipeline import STORAGE, parse_original


ROOT = Path(__file__).resolve().parents[2]
SOURCE_DIR = ROOT / "test-fixtures" / "public-reports"
NAMESPACE = UUID("7a3be4c6-936c-4fa6-9839-b09dba5f0e9a")


@dataclass(frozen=True)
class DemoDocument:
    slug: str
    project_name: str
    source_filename: str
    display_filename: str
    sha256: str
    pages: int

    @property
    def project_id(self) -> str:
        return str(uuid5(NAMESPACE, f"project/{self.slug}"))

    @property
    def document_id(self) -> str:
        return str(uuid5(NAMESPACE, f"document/{self.slug}/{self.sha256}"))

    @property
    def storage_name(self) -> str:
        return f"{self.document_id}.pdf"


# Creation order puts the primary accident walkthrough at the top of the
# app's newest-first project selector. The hashes are from the frozen README.
DEMO_DOCUMENTS = (
    DemoDocument("beijing-feasibility", "演示 · 建设项目可研", "01_beijing_feasibility.pdf",
                 "平谷公共交通枢纽项目建议书（代可研报告）.pdf",
                 "4406b7c6b40fade73a785342962d2cd47f87f67e04e4e7a5c905cbb0089a551c", 205),
    DemoDocument("jiangmen-eia", "演示 · 环境影响报告", "02_jiangmen_eia.pdf",
                 "金属废料分选项目环境影响报告表.pdf",
                 "2649027d0995f4de3ca8dd8f822ba3e9535322abfb46589fce371da161fa8846", 65),
    DemoDocument("taizhou-audit", "演示 · 扫描审计报告", "03_taizhou_audit.pdf",
                 "小微企业政策执行专项审计调查报告.pdf",
                 "9e56c7676016096282283718ffccd358f0b4bfde461ad17f77c9259a2c1688a8", 8),
    DemoDocument("jinjiang-accident", "演示 · 事故调查报告", "04_jinjiang_accident.pdf",
                 "晋江英林一般高坠事故调查报告.pdf",
                 "eb6655da71f0d11b8a41c3bc5320a53fd4307fec370018150a037978927d69c4", 15),
)


def _checked_bytes(spec: DemoDocument, source_dir: Path) -> bytes:
    path = source_dir / spec.source_filename
    data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != spec.sha256:
        raise ValueError(f"演示原件摘要不符：{spec.source_filename}")
    return data


def seed_demo_documents(
    specs: tuple[DemoDocument, ...] = DEMO_DOCUMENTS,
    *,
    source_dir: Path = SOURCE_DIR,
    storage_dir: Path = STORAGE,
    session_factory=SessionLocal,
    verify_only: bool = False,
) -> list[dict]:
    """Create isolated demo projects and copies, or verify a previous run.

    All source hashes are checked before any write. The database transaction
    creates only project and document rows. Files created in a failed attempt
    are removed, while pre-existing files and business data stay untouched.
    """
    prepared = [(spec, _checked_bytes(spec, source_dir)) for spec in specs]
    created_files: list[Path] = []
    results: list[dict] = []
    try:
        with session_factory.begin() as session:
            for spec, data in prepared:
                project = session.get(Project, spec.project_id)
                if project is None:
                    if verify_only:
                        raise ValueError(f"演示项目尚未预置：{spec.project_name}")
                    project = Project(id=spec.project_id, name=spec.project_name)
                    session.add(project)
                    session.flush()
                elif project.corpus is not None:
                    raise ValueError(f"演示项目 ID 已绑定只读语料：{spec.project_name}")

                existing = session.scalar(select(SourceDocument).where(
                    SourceDocument.project_id == spec.project_id,
                    SourceDocument.sha256 == spec.sha256,
                ))
                if existing is not None:
                    stored = storage_dir / existing.storage_name
                    if not stored.is_file() or hashlib.sha256(stored.read_bytes()).hexdigest() != spec.sha256:
                        raise ValueError(f"已登记原件缺失或摘要不符：{spec.project_name}")
                    document = existing
                    state = "已存在"
                else:
                    if verify_only:
                        raise ValueError(f"演示原件尚未预置：{spec.project_name}")
                    pages, segments, status = parse_original(data, "pdf")
                    if pages != spec.pages:
                        raise ValueError(f"演示原件页数不符：{spec.source_filename}")
                    storage_dir.mkdir(parents=True, exist_ok=True)
                    stored = storage_dir / spec.storage_name
                    if stored.exists():
                        if hashlib.sha256(stored.read_bytes()).hexdigest() != spec.sha256:
                            raise ValueError(f"演示存储位置已有不同文件：{stored.name}")
                    else:
                        temporary = storage_dir / f".{spec.storage_name}.tmp"
                        try:
                            temporary.write_bytes(data)
                            os.replace(temporary, stored)
                        finally:
                            temporary.unlink(missing_ok=True)
                        created_files.append(stored)
                    document = SourceDocument(
                        id=spec.document_id, project_id=spec.project_id,
                        filename=spec.display_filename, file_kind="pdf",
                        sha256=spec.sha256, storage_name=spec.storage_name,
                        pages=pages, segments=segments, status=status,
                    )
                    session.add(document)
                    state = "已预置"
                if session.scalar(select(ProjectFact.id).where(ProjectFact.project_id == spec.project_id).limit(1)):
                    fact_state = "已有项目事实（未修改）"
                else:
                    fact_state = "事实台账为空"
                results.append({"project_id": spec.project_id, "project": project.name,
                                "document_id": document.id, "filename": document.filename,
                                "pages": document.pages, "sha256": document.sha256,
                                "status": document.status, "seed_state": state,
                                "fact_state": fact_state})
    except Exception:
        for path in created_files:
            path.unlink(missing_ok=True)
        raise
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="预置或核验四份公开报告演示原件")
    parser.add_argument("--verify-only", action="store_true", help="只核验，不写入")
    args = parser.parse_args()
    for result in seed_demo_documents(verify_only=args.verify_only):
        print(f"{result['seed_state']} | {result['project']} | {result['pages']} 页 | "
              f"{result['status']} | {result['fact_state']} | {result['document_id']}")


if __name__ == "__main__":
    main()
