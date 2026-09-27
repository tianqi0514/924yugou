"""Read-only verification of an isolated PostgreSQL + storage restore.

Usage: DATABASE_URL=.../report_platform_restore_x python scripts/verify_restore.py /path/to/storage
Never point this script at the daily database.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import sys
from pathlib import Path
from zipfile import ZipFile

from sqlalchemy import select

from app.db import Project, ReportDraft, ReportExport, ReportVersion, SessionLocal, SourceDocument, engine
from app.model_settings import ModelSettingsStore


def main() -> None:
    if "restore" not in (engine.url.database or ""):
        raise SystemExit("拒绝核验日常数据库：数据库名必须包含 restore")
    if len(sys.argv) != 2:
        raise SystemExit("用法：verify_restore.py /隔离目录/storage")
    storage = Path(sys.argv[1]).resolve()
    if not storage.is_dir():
        raise SystemExit("恢复存储目录不存在")
    settings = ModelSettingsStore(storage / "model-settings")
    with SessionLocal() as session:
        projects = session.scalars(select(Project)).all()
        documents = session.scalars(select(SourceDocument)).all()
        reports = session.scalars(select(ReportDraft)).all()
        versions = session.scalars(select(ReportVersion)).all()
        exports = session.scalars(select(ReportExport)).all()
        if not projects or not reports or not documents or not exports:
            raise SystemExit("隔离恢复缺少项目、报告、原件或交付")
        for document in documents:
            path = storage / "documents" / document.storage_name
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != document.sha256:
                raise SystemExit(f"原件缺失或摘要不符：{document.id}")
        for export in exports:
            if hashlib.sha256(export.archive).hexdigest() != export.archive_sha256:
                raise SystemExit(f"交付摘要不符：{export.id}")
            with ZipFile(io.BytesIO(export.archive)) as bundle:
                names = set(bundle.namelist())
                if {"report.docx", "report.pdf", "audit.json"} - names:
                    raise SystemExit(f"交付不完整：{export.id}")
                audit = json.loads(bundle.read("audit.json"))
                if (hashlib.sha256(bundle.read("report.docx")).hexdigest() != audit.get("docx_sha256") or
                    hashlib.sha256(bundle.read("report.pdf")).hexdigest() != audit.get("pdf_sha256")):
                    raise SystemExit(f"交付内部摘要不符：{export.id}")
        if any(session.get(ReportVersion, (report.id, report.version)) is None for report in reports):
            raise SystemExit("报告缺少当前版本快照")
    public = settings.public()
    config = settings._read()
    encrypted = [record["encrypted_api_key"] for record in config["models"]
                 if record.get("encrypted_api_key")]
    if encrypted:
        if not os.getenv("MODEL_CONFIG_FERNET_KEY") and not (storage / "model-settings" / "master.key").is_file():
            raise SystemExit("模型配置解密密钥缺失，恢复未完成")
        cipher = settings._cipher()
        for token in encrypted:
            if not cipher.decrypt(token.encode()):
                raise SystemExit("模型配置密钥解密失败")
    print(json.dumps({"projects": len(projects), "documents": len(documents),
                      "reports": len(reports), "versions": len(versions),
                      "exports": len(exports), "models": len(public["models"]),
                      "encrypted_keys_verified": len(encrypted)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
