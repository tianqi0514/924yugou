"""One-time, transactional import of the frozen demonstration corpus."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import mimetypes
from pathlib import Path
from typing import Any

import pyarrow.parquet as parquet
import yaml
from docx import Document
from openpyxl import load_workbook
from sqlalchemy import delete, func, select, text

from .corpus import EXPLAIN, ROOT, CorpusRepository, _jsonable
from .corpus_storage import (
    CorpusArchive, CorpusArtifact, CorpusCategoryRow, CorpusGraphEdge, CorpusGraphNode, CorpusRecord, CorpusSourceAsset,
)
from .db import SessionLocal, engine, utcnow


TABLES = [
    CorpusArchive.__table__, CorpusCategoryRow.__table__, CorpusArtifact.__table__, CorpusRecord.__table__,
    CorpusGraphNode.__table__, CorpusGraphEdge.__table__, CorpusSourceAsset.__table__,
]


def artifact_id(corpus_id: str, path: str) -> str:
    """Stable identity independent of file content revisions or display names."""
    return "ART-" + hashlib.sha256(f"{corpus_id}\0{path}".encode("utf-8")).hexdigest()[:24]


def migrate_corpus_schema() -> None:
    """Create only the new frozen-corpus tables; never alter existing project tables."""
    CorpusArchive.metadata.create_all(engine, tables=TABLES, checkfirst=True)
    # Early imports represented absent optional JSONB values as JSON null.
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE corpus_artifacts ADD COLUMN IF NOT EXISTS artifact_id varchar(28)"))
        connection.execute(text("ALTER TABLE corpus_artifacts ADD COLUMN IF NOT EXISTS purpose text NOT NULL DEFAULT ''"))
        connection.execute(text("ALTER TABLE corpus_artifacts ADD COLUMN IF NOT EXISTS writing_use text NOT NULL DEFAULT ''"))
        connection.execute(text("ALTER TABLE corpus_artifacts ADD COLUMN IF NOT EXISTS boundary text NOT NULL DEFAULT ''"))
        connection.execute(text("ALTER TABLE corpus_artifacts ADD COLUMN IF NOT EXISTS purpose_source text NOT NULL DEFAULT ''"))
        connection.execute(text("ALTER TABLE corpus_records ADD COLUMN IF NOT EXISTS artifact_id varchar(28)"))
        pending = connection.execute(text("SELECT corpus_id, path FROM corpus_artifacts WHERE artifact_id IS NULL")).all()
        for corpus_id, path in pending:
            connection.execute(text("UPDATE corpus_artifacts SET artifact_id = :artifact_id WHERE corpus_id = :corpus_id AND path = :path"),
                               {"artifact_id": artifact_id(corpus_id, path), "corpus_id": corpus_id, "path": path})
        connection.execute(text("UPDATE corpus_records AS record SET artifact_id = artifact.artifact_id FROM corpus_artifacts AS artifact "
                                "WHERE record.corpus_id = artifact.corpus_id AND record.artifact_path = artifact.path AND record.artifact_id IS NULL"))
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_corpus_artifact_id ON corpus_artifacts (corpus_id, artifact_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_corpus_records_artifact_id ON corpus_records (artifact_id)"))
        connection.execute(text("ALTER TABLE corpus_artifacts ALTER COLUMN artifact_id SET NOT NULL"))
        connection.execute(text("ALTER TABLE corpus_records ALTER COLUMN artifact_id SET NOT NULL"))
        connection.execute(text("UPDATE corpus_records SET location = NULL WHERE location = 'null'::jsonb"))
        connection.execute(text("UPDATE corpus_categories SET context = NULL WHERE context = 'null'::jsonb"))
        connection.execute(text("UPDATE corpus_artifacts SET parsed = NULL WHERE parsed = 'null'::jsonb"))


def _parse(path: str, content: bytes) -> Any:
    suffix = Path(path).suffix.lower()
    if suffix in (".yaml", ".yml"):
        return _jsonable(yaml.safe_load(content.decode("utf-8")))
    if suffix == ".json":
        return json.loads(content.decode("utf-8"))
    if suffix == ".jsonl":
        return [json.loads(line) for line in content.decode("utf-8").splitlines() if line.strip()]
    if suffix == ".csv":
        rows = list(csv.reader(io.StringIO(content.decode("utf-8-sig"))))
        return {"name": Path(path).stem, "rows": rows, "row_count": len(rows), "column_count": max(map(len, rows), default=0)}
    if suffix == ".md":
        return {"path": path, "text": content.decode("utf-8")}
    if suffix == ".docx":
        document = Document(io.BytesIO(content))
        return {
            "title": document.core_properties.title,
            "paragraphs": [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()],
            "tables": [{"name": f"表格 {index + 1}", "rows": [[cell.text for cell in row.cells] for row in table.rows]}
                       for index, table in enumerate(document.tables)],
            "preview_kind": "文本与表格预览；原 DOCX 排版以原件为准",
        }
    if suffix == ".parquet":
        file = parquet.ParquetFile(io.BytesIO(content))
        metadata = {key.decode(): value.decode(errors="replace") for key, value in (file.metadata.metadata or {}).items()}
        return {"rows": file.metadata.num_rows, "columns": [{"name": field.name, "type": str(field.type)} for field in file.schema_arrow],
                "metadata": metadata, "status": metadata.get("status", "unknown")}
    return None


def _descriptions(content: bytes) -> dict[int, dict]:
    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        sheet = workbook["30类产物总览"]
        return {int(row[0]): {"description": row[11] or "", "actual_content": row[12] or "", "boundary": row[15] or ""}
                for row in sheet.iter_rows(min_row=7, max_row=36, values_only=True)}
    finally:
        workbook.close()


def _artifact_explanations(content: bytes) -> dict[str, dict[str, str]]:
    """Read the workbook's per-file explanations, including three delivery helpers."""
    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        sheet = workbook["逐文件明细"]
        explanations: dict[str, dict[str, str]] = {}
        for row in sheet.iter_rows(min_row=7, max_row=123, values_only=True):
            path = str(row[4] or "").strip()
            if not path or path in explanations:
                raise ValueError(f"逐文件说明缺少路径或重复：{path}")
            purpose = str(row[8] or "").strip()
            if not purpose:
                raise ValueError(f"逐文件说明缺少用途：{path}")
            explanations[path] = {
                "purpose": purpose,
                "boundary": str(row[9] or "").strip(),
                "purpose_source": str(row[10] or "").strip(),
            }
        if len(explanations) != 117:
            raise ValueError(f"逐文件说明应覆盖 117 个文件，实际 {len(explanations)}")
        return explanations
    finally:
        workbook.close()


def _validated_source(root: Path, manifest: dict) -> tuple[dict[str, bytes], dict[str, str], dict[int, list[str]]]:
    root = root.resolve()
    contents = {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    if len(contents) != 117:
        raise ValueError(f"归档应包含 117 个文件，实际 {len(contents)}")
    listed: dict[str, str] = {}
    for line in contents["SHA256SUMS"].decode("utf-8").splitlines():
        digest, name = line.split("  ", 1)
        if name in listed:
            raise ValueError(f"重复的 SHA256SUMS 条目：{name}")
        listed[name] = digest
    if set(contents) - {"SHA256SUMS"} != set(listed) or len(listed) != 116:
        raise ValueError("SHA256SUMS 文件清单与归档不一致")
    errors = [name for name, digest in listed.items() if hashlib.sha256(contents[name]).hexdigest() != digest]
    if errors:
        raise ValueError(f"SHA256SUMS 校验失败：{', '.join(errors[:5])}")
    category_files: dict[int, list[str]] = {}
    for item in manifest["files"]:
        number = int(item["no"])
        category_files[number] = [entry["path"] for entry in item.get("files", [])] if "files" in item else [item["path"]]
    if set(category_files) != set(range(1, 31)) or sum(map(len, category_files.values())) != 114:
        raise ValueError("30 类或 114 个产物文件不完整")
    if {path for paths in category_files.values() for path in paths} != set(contents) - {"README.md", "SHA256SUMS", "validate_package.py"}:
        raise ValueError("产物文件与 manifest 不一致")
    return contents, listed, category_files


def import_builtin_corpus(root: Path | None = None, explanation: Path | None = None, *, rebuild: bool = False) -> dict:
    """Import or atomically rebuild DB projections from the same verified frozen archive.

    An existing corpus ID may be rebuilt only when every archived byte still matches
    the supplied SHA256SUMS-verified snapshot. The historic source is never edited.
    """
    migrate_corpus_schema()
    root = root or ROOT
    explanation = explanation or EXPLAIN
    manifest = _parse("manifest.yaml", (root / "manifest.yaml").read_bytes())
    corpus_id = manifest["corpus_id"]
    contents, listed, category_files = _validated_source(root, manifest)
    explain_bytes = explanation.read_bytes()
    descriptions = _descriptions(explain_bytes)
    explanations = _artifact_explanations(explain_bytes)
    if set(explanations) != set(contents):
        raise ValueError("逐文件说明与语料归档的 117 个路径不一致")
    with SessionLocal() as session:
        existing = session.get(CorpusArchive, corpus_id)
        if existing is not None:
            stored_rows = session.scalars(
                select(CorpusArtifact).where(CorpusArtifact.corpus_id == corpus_id)).all()
            stored = {item.path: item.sha256 for item in stored_rows}
            incoming = {path: hashlib.sha256(content).hexdigest() for path, content in contents.items()}
            if existing.manifest != manifest or stored != incoming:
                raise ValueError("同一语料 ID 的归档内容与已入库版本不同")
            source_asset = session.get(CorpusSourceAsset, (corpus_id, explanation.name))
            complete = (
                existing.category_count == 30 and existing.artifact_count == 117
                and all(item.purpose == explanations[item.path]["purpose"]
                        and item.writing_use == (descriptions[item.category_number]["description"] if item.category_number else "")
                        and item.boundary == explanations[item.path]["boundary"]
                        and item.purpose_source == explanations[item.path]["purpose_source"]
                        for item in stored_rows)
                and source_asset is not None
                and source_asset.sha256 == hashlib.sha256(explain_bytes).hexdigest()
                and session.scalar(select(func.count()).select_from(CorpusCategoryRow).where(CorpusCategoryRow.corpus_id == corpus_id)) == 30
                and session.scalar(select(func.count()).select_from(CorpusRecord).where(CorpusRecord.corpus_id == corpus_id)) == 3462
                and session.scalar(select(func.count()).select_from(CorpusGraphNode).where(CorpusGraphNode.corpus_id == corpus_id)) == 728
                and session.scalar(select(func.count()).select_from(CorpusGraphEdge).where(CorpusGraphEdge.corpus_id == corpus_id)) == 2232
            )
            if complete and not rebuild:
                return {"corpus_id": corpus_id, "version": existing.version, "categories": existing.category_count,
                        "artifacts": existing.artifact_count, "imported": False}
    parsed = {path: _parse(path, contents[path]) for paths in category_files.values() for path in paths}
    if parsed["chunks/embeddings.parquet"]["rows"] != 0:
        raise ValueError("向量文件状态与冻结语料说明不符")
    if len(parsed["graph/relations.yaml"]) != 1666 or len(category_files[8]) != 18 or len(category_files[19]) != 20 or len(category_files[27]) != 49:
        raise ValueError("关键产物数量与归档说明不符")
    repository = CorpusRepository(bootstrap=(manifest, parsed, descriptions))
    entities, edges, _ = repository.graph()
    if len(edges) != 2232 or len(edges) - len(parsed["graph/relations.yaml"]) != 566:
        raise ValueError("关系图边数不符合冻结快照")
    category_records = {number: repository._records(number) for number in range(1, 31)}

    with SessionLocal.begin() as session:
        # The existence check is repeated within the transaction for idempotence.
        existing = session.get(CorpusArchive, corpus_id)
        if existing is not None:
            # Rebuilding derived rows leaves the corpus ID and project association intact.
            current = {item.path: item.sha256 for item in session.scalars(
                select(CorpusArtifact).where(CorpusArtifact.corpus_id == corpus_id)).all()}
            if existing.manifest != manifest or current != {path: hashlib.sha256(content).hexdigest() for path, content in contents.items()}:
                raise ValueError("重建期间语料版本已变化")
            for table in (CorpusGraphEdge, CorpusGraphNode, CorpusRecord, CorpusCategoryRow, CorpusArtifact, CorpusSourceAsset):
                session.execute(delete(table).where(table.corpus_id == corpus_id))
            existing.version = manifest["version"]
            existing.integrity = {"total_files": 117, "listed_hashes": len(listed), "valid": True, "errors": []}
            existing.artifact_count = 117
            existing.category_count = 30
            existing.imported_at = utcnow()
        else:
            session.add(CorpusArchive(id=corpus_id, version=manifest["version"], manifest=manifest,
                                      integrity={"total_files": 117, "listed_hashes": len(listed), "valid": True, "errors": []},
                                      artifact_count=117, category_count=30))
        session.flush()
        path_category = {path: number for number, paths in category_files.items() for path in paths}
        for path, content in contents.items():
            media_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
            explanation_row = explanations[path]
            session.add(CorpusArtifact(corpus_id=corpus_id, path=path, artifact_id=artifact_id(corpus_id, path), category_number=path_category.get(path),
                                       sha256=hashlib.sha256(content).hexdigest(), byte_size=len(content),
                                       media_type=media_type,
                                       purpose=explanation_row["purpose"],
                                       writing_use=descriptions[path_category[path]]["description"] if path in path_category else "",
                                       boundary=explanation_row["boundary"], purpose_source=explanation_row["purpose_source"],
                                       content=content, parsed=parsed.get(path)))
        session.add(CorpusSourceAsset(corpus_id=corpus_id, name=explanation.name,
                                      sha256=hashlib.sha256(explain_bytes).hexdigest(), content=explain_bytes,
                                      purpose="30 类产物用途、实际位置和使用边界说明"))
        for number, (records, context) in category_records.items():
            session.add(CorpusCategoryRow(corpus_id=corpus_id, number=number, payload=repository._category_meta[number],
                                          context=_jsonable(context), record_count=len(records)))
            for ordinal, record in enumerate(records):
                semantic_id = str(record.get("id") or record.get("section_id") or record.get("chunk_id") or "") if isinstance(record, dict) else ""
                artifact_path = str(record.get("file") or category_files[number][0]) if isinstance(record, dict) else category_files[number][0]
                location = None
                if isinstance(record, dict):
                    location = record.get("source_locations") or record.get("source_location") or record.get("source_locator")
                session.add(CorpusRecord(corpus_id=corpus_id, category_number=number,
                                         record_id=f"{number:02d}:{ordinal:06d}", ordinal=ordinal,
                                         semantic_id=semantic_id or None, artifact_path=artifact_path,
                                         artifact_id=artifact_id(corpus_id, artifact_path),
                                         location=_jsonable(location), payload=_jsonable(record)))
        for node in entities.values():
            session.add(CorpusGraphNode(corpus_id=corpus_id, id=node["id"], label=node["label"],
                                        kind=node["kind"], source_path=node["source"]))
        for edge in edges:
            session.add(CorpusGraphEdge(corpus_id=corpus_id, id=edge["id"], source=edge["source"], target=edge["target"],
                                        type=edge["type"], origin=edge["origin"],
                                        derivation="raw" if edge["origin"] == "graph/relations.yaml" else "derived"))
    return {"corpus_id": corpus_id, "version": manifest["version"], "categories": 30, "artifacts": 117,
            "records": sum(len(records) for records, _ in category_records.values()), "nodes": len(entities),
            "raw_edges": 1666, "derived_edges": 566, "imported": existing is None, **({"rebuilt": True} if existing is not None else {})}
