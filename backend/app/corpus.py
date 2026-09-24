"""内置历史语料的只读目录、逐类预览和局部知识图谱。"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict, deque
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from .corpus_storage import CorpusArchive, CorpusArtifact, CorpusCategoryRow, CorpusGraphEdge, CorpusGraphNode, CorpusRecord
from .db import SessionLocal


ROOT = Path(__file__).resolve().parents[2] / "demo-data" / "澄岳精密" / "解析产物"
EXPLAIN = ROOT.parent / "澄岳精密_30类产物文件位置与意义说明.xlsx"
GROUPS = [
    {"name": "基础信息", "numbers": [1, 2, 3]},
    {"name": "原始资料与结构", "numbers": [4, 5, 6, 7, 8]},
    {"name": "内容切块", "numbers": [9, 10, 11]},
    {"name": "语义图谱", "numbers": [12, 13, 14, 15, 16, 17]},
    {"name": "证据与推演", "numbers": [18, 19, 20, 21, 22]},
    {"name": "质量审查", "numbers": [23, 24, 25]},
    {"name": "写作与索引", "numbers": [26, 27, 28, 29, 30]},
]
CATEGORY_NAMES = [
    "语料概览", "项目基本信息", "来源与转换", "报告章节", "原始报告", "统一正文", "原文位置对照", "资料表格",
    "原文切块", "写作骨架", "向量状态", "语义节点", "计算规则", "节点关系", "论断支撑", "校验项", "知识图谱总览",
    "证据目录", "证据摘录", "推演过程", "决策与取舍", "执行轨迹", "来源冲突", "资料缺口", "质量闸门",
    "写作风格", "章节模板", "节点位置索引", "术语索引", "语料特征",
]


def _jsonable(value: Any) -> Any:
    """把 YAML 日期等值转换为 API 可序列化对象。

    Args:
        value: 解析后的文件内容。

    Returns:
        JSON 兼容的值。
    """
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


class CorpusRepository:
    """从 PostgreSQL 读取冻结语料；不将历史值写入项目事实库。"""

    def __init__(self, bootstrap: tuple[dict, dict[str, Any], dict[int, dict]] | None = None) -> None:
        self._manifest: dict | None = None
        self._bootstrap_parsed: dict[str, Any] | None = None
        self._schema_checked = False
        self._archive_revision: Any = None
        self._category_files: dict[int, list[str]] = {}
        self._allowed_files: set[str] = set()
        self._category_meta: dict[int, dict] = {}
        if bootstrap is not None:
            self._manifest, self._bootstrap_parsed, descriptions = bootstrap
            self._load_catalog(self._manifest, descriptions)

    def _load_catalog(self, manifest: dict, descriptions: dict[int, dict]) -> None:
        self._category_files.clear()
        self._allowed_files.clear()
        self._category_meta.clear()
        for item in manifest["files"]:
            number = item["no"]
            paths = [x["path"] for x in item.get("files", [])] if "files" in item else [item["path"]]
            self._category_files[number] = paths
            self._allowed_files.update(paths)
            group = next(group["name"] for group in GROUPS if number in group["numbers"])
            self._category_meta[number] = {
                "number": number,
                "name": CATEGORY_NAMES[number - 1],
                "group": group,
                "format": item["format"],
                "stage": item["stage"],
                "count": len(paths),
                "content_status": item["content_status"],
                "files": paths,
                **descriptions[number],
            }

    def _ensure_database(self) -> None:
        if self._bootstrap_parsed is not None:
            return
        if not self._schema_checked:
            from .corpus_import import migrate_corpus_schema
            migrate_corpus_schema()
            self._schema_checked = True
        with SessionLocal() as session:
            archive = session.scalar(select(CorpusArchive).limit(1))
        if archive is None:
            from .corpus_import import import_builtin_corpus
            import_builtin_corpus()
            with SessionLocal() as session:
                archive = session.scalar(select(CorpusArchive).limit(1))
            if archive is None:
                raise RuntimeError("内置语料尚未导入数据库")
        if (self._manifest is not None and self._manifest["corpus_id"] == archive.id
                and self._category_meta and self._archive_revision == archive.imported_at):
            return
        with SessionLocal() as session:
            rows = session.scalars(select(CorpusCategoryRow).where(CorpusCategoryRow.corpus_id == archive.id).order_by(CorpusCategoryRow.number)).all()
            artifact_rows = session.scalars(select(CorpusArtifact).where(CorpusArtifact.corpus_id == archive.id)).all()
            record_counts = dict(session.execute(
                select(CorpusRecord.artifact_id, func.count()).where(CorpusRecord.corpus_id == archive.id)
                .group_by(CorpusRecord.artifact_id)).all())
            artifact_by_path = {item.path: self._artifact_meta(item, record_counts.get(item.artifact_id, 0))
                                for item in artifact_rows}
            self._category_meta = {row.number: {**row.payload, "category_id": f"CAT-{row.number:02d}",
                                                "record_count": row.record_count,
                                                "artifacts": [artifact_by_path[path] for path in row.payload["files"]]}
                                   for row in rows}
            self._category_files = {row.number: list(row.payload["files"]) for row in rows}
            self._allowed_files = {path for paths in self._category_files.values() for path in paths}
        self._manifest = archive.manifest
        self._archive_revision = archive.imported_at

    @staticmethod
    def _artifact_meta(item: CorpusArtifact, record_count: int = 0) -> dict:
        return {
            "artifact_id": item.artifact_id,
            "path": item.path,
            "name": Path(item.path).name,
            "category_number": item.category_number,
            "purpose": item.purpose,
            "writing_use": item.writing_use,
            "boundary": item.boundary,
            "purpose_source": item.purpose_source,
            "source": item.purpose_source,
            "byte_size": item.byte_size,
            "record_count": record_count,
        }

    def artifact(self, artifact_id: str) -> dict:
        """Return a single physical archive file and its DB-backed explanation."""
        self._ensure_database()
        with SessionLocal() as session:
            item = session.scalar(select(CorpusArtifact).where(
                CorpusArtifact.corpus_id == self.manifest["corpus_id"],
                CorpusArtifact.artifact_id == artifact_id))
            if item is None:
                raise ValueError("语料资产不存在")
            record_count = session.scalar(select(func.count()).select_from(CorpusRecord).where(
                CorpusRecord.corpus_id == self.manifest["corpus_id"],
                CorpusRecord.artifact_id == artifact_id)) or 0
            return {**self._artifact_meta(item, record_count), "sha256": item.sha256,
                    "media_type": item.media_type}

    @property
    def manifest(self) -> dict:
        self._ensure_database()
        assert self._manifest is not None
        return self._manifest

    def path_for(self, relative: str) -> Path:
        """Legacy filesystem API; runtime callers must use :meth:`raw_bytes`."""
        raise RuntimeError("语料已入库，请从数据库读取原件")

    def raw_bytes(self, artifact: str) -> tuple[bytes, str, str]:
        """Read an original by stable artifact ID or legacy archive path."""
        self._ensure_database()
        with SessionLocal() as session:
            item = session.get(CorpusArtifact, (self.manifest["corpus_id"], artifact))
            if item is None:
                item = session.scalar(select(CorpusArtifact).where(CorpusArtifact.corpus_id == self.manifest["corpus_id"],
                                                                 CorpusArtifact.artifact_id == artifact))
            if item is None:
                raise ValueError("语料资产不存在")
            return bytes(item.content), item.media_type, Path(item.path).name

    def integrity(self) -> dict:
        """Verify SHA256SUMS against bytes stored in PostgreSQL."""
        self._ensure_database()
        with SessionLocal() as session:
            artifacts = session.scalars(select(CorpusArtifact).where(CorpusArtifact.corpus_id == self.manifest["corpus_id"])).all()
            by_path = {item.path: item for item in artifacts}
            checksum = by_path.get("SHA256SUMS")
            if checksum is None:
                return {"total_files": len(artifacts), "listed_hashes": 0, "valid": False, "errors": ["SHA256SUMS"]}
            listed: dict[str, str] = {}
            errors: list[str] = []
            for line in bytes(checksum.content).decode("utf-8").splitlines():
                digest, path = line.split("  ", 1)
                if path in listed or path not in by_path:
                    errors.append(path)
                else:
                    listed[path] = digest
                    if hashlib.sha256(bytes(by_path[path].content)).hexdigest() != digest:
                        errors.append(path)
            if set(by_path) - {"SHA256SUMS"} != set(listed):
                errors.extend(sorted((set(by_path) - {"SHA256SUMS"}) ^ set(listed)))
            return {"total_files": len(artifacts), "listed_hashes": len(listed), "valid": not errors, "errors": errors}

    def categories(self) -> list[dict]:
        """返回全部 30 类的人类可读目录。

        Returns:
            按规范编号排序的类别元数据。
        """
        self._ensure_database()
        return [self._category_meta[number] for number in range(1, 31)]

    def summary(self) -> dict:
        """返回语料版本和真实产物统计。

        Returns:
            概览卡所需信息。
        """
        counts = self.manifest["counts"]
        with SessionLocal() as session:
            auxiliary = session.scalars(select(CorpusArtifact).where(CorpusArtifact.corpus_id == self.manifest["corpus_id"],
                                                                      CorpusArtifact.category_number.is_(None)).order_by(CorpusArtifact.path)).all()
        return {
            "corpus_id": self.manifest["corpus_id"],
            "version": self.manifest["version"],
            "status": self.manifest["status"],
            "review_status": self.manifest["review_status"],
            "runtime_storage_deployed": True,
            "embedding_status": self.manifest["embedding_status"],
            "counts": counts,
            "groups": [{**group, "count": len(group["numbers"])} for group in GROUPS],
            "integrity": self.integrity(),
            "auxiliary_assets": [self._artifact_meta(item) for item in auxiliary],
        }

    @lru_cache(maxsize=160)
    def parsed(self, relative: str) -> Any:
        """按文件实际格式解析单个产物，保留冻结的原内容。

        Args:
            relative: 清单内的相对路径。

        Returns:
            可序列化的文档、记录或表格预览。
        """
        if self._bootstrap_parsed is not None:
            if relative not in self._bootstrap_parsed:
                raise ValueError("产物不存在")
            return self._bootstrap_parsed[relative]
        self._ensure_database()
        with SessionLocal() as session:
            item = session.get(CorpusArtifact, (self.manifest["corpus_id"], relative))
            if item is None or item.parsed is None:
                raise ValueError("产物不存在或没有可预览结构")
            return item.parsed

    def _records(self, number: int) -> tuple[list[Any], Any]:
        paths = self._category_files[number]
        documents = [self.parsed(path) for path in paths]
        if len(paths) > 1:
            if number == 19:
                by_path = {excerpt["path"]: {"evidence_id": evidence["id"], **excerpt} for evidence in self.parsed("evidence/evidence.yaml") for excerpt in evidence.get("excerpts", [])}
                return [{"file": path, **doc, "link": by_path.get(path)} for path, doc in zip(paths, documents)], None
            return [dict(file=path, **doc) if isinstance(doc, dict) else {"file": path, "data": doc} for path, doc in zip(paths, documents)], None
        document = documents[0]
        if number == 16:
            checks = {item["invariant_id"]: item for item in self.parsed("quality/gate_report.yaml").get("tests", [])}
            return [{**item, "check": checks.get(item["id"])} for item in document], None
        if number == 23:
            nodes = {item["id"]: item for item in self.parsed("graph/nodes.yaml")}
            evidence = {item["id"]: item for item in self.parsed("evidence/evidence.yaml")}
            return [{**item, "linked_nodes": [nodes[key] for key in item.get("nodes", []) if key in nodes], "linked_evidence": [evidence[key] for key in item.get("evidence", []) if key in evidence]} for item in document], None
        if isinstance(document, list):
            return document, None
        if number == 1:
            return document.get("files", []), {key: value for key, value in document.items() if key != "files"}
        if number == 4:
            return document.get("sections", []), {key: value for key, value in document.items() if key != "sections"}
        if number == 7:
            return document.get("entries", []), {key: value for key, value in document.items() if key != "entries"}
        if number == 17:
            return [{"collection": key, "count": len(value) if isinstance(value, list) else 1} for key, value in document.items() if key not in ("scope", "snapshot_version")], {"scope": document.get("scope"), "snapshot_version": document.get("snapshot_version")}
        if number == 28:
            return [{"id": key, **value} for key, value in document.items()], None
        if number == 29:
            return document.get("terms", []), {key: value for key, value in document.items() if key != "terms"}
        return [document], None

    def category(self, number: int, page: int = 1, size: int = 30, search: str = "", node_group: str = "", status: str = "", owner: str = "", rule_id: str = "", date_from: str = "", date_to: str = "", blocking: str = "") -> dict:
        """返回某类产物的分页记录及专属元数据。

        Args:
            number: 规范类别编号 1—30。
            page: 从 1 开始的页码。
            size: 每页记录数。
            search: 当前类的记录搜索词。

        Returns:
            类别详情、记录和分页信息。
        """
        self._ensure_database()
        if number not in self._category_meta:
            raise ValueError("产物类别不存在")
        if self._bootstrap_parsed is not None:
            records, context = self._records(number)
        else:
            with SessionLocal() as session:
                rows = session.scalars(select(CorpusRecord).where(CorpusRecord.corpus_id == self.manifest["corpus_id"], CorpusRecord.category_number == number).order_by(CorpusRecord.ordinal)).all()
                category_row = session.get(CorpusCategoryRow, (self.manifest["corpus_id"], number))
                records = [{**row.payload, "_record_id": row.record_id, "_artifact_id": row.artifact_id}
                           if isinstance(row.payload, dict) else row.payload for row in rows]
                context = category_row.context if category_row else None
        options: dict[str, list[str]] = {}
        if number == 12:
            options["statuses"] = sorted({str(item.get("status")) for item in records})
            if node_group:
                records = [item for item in records if ("结构数字" if item.get("role") == "document_structure" else "判断" if item.get("kind") == "judgment" else "事实") == node_group]
            if status:
                records = [item for item in records if item.get("status") == status]
        if number == 22:
            if rule_id:
                records = [item for item in records if rule_id.casefold() in str(item.get("rule_id") or "").casefold()]
            if date_from:
                records = [item for item in records if str(item.get("observed_at") or "")[:10] >= date_from]
            if date_to:
                records = [item for item in records if str(item.get("observed_at") or "")[:10] <= date_to]
        if number == 24:
            options["owners"] = sorted({str(item.get("owner")) for item in records})
            options["statuses"] = sorted({str(item.get("status")) for item in records})
            if owner:
                records = [item for item in records if item.get("owner") == owner]
            if status:
                records = [item for item in records if item.get("status") == status]
            if blocking:
                records = [item for item in records if str(item.get("blocking")).lower() == blocking.lower() or blocking.casefold() in str(item.get("blocking_scope") or "").casefold()]
        if search.strip():
            needle = search.strip().lower()
            records = [item for item in records if needle in json.dumps(item, ensure_ascii=False).lower()]
        total = len(records)
        start = (page - 1) * size
        return {"category": self._category_meta[number], "context": context, "records": records[start:start + size], "total": total, "page": page, "size": size, "filter_options": options}

    @lru_cache(maxsize=1)
    def graph(self) -> tuple[dict[str, dict], list[dict], dict[str, list[dict]]]:
        """从显式关系和规则构建可查询的只读图。

        Returns:
            实体、边以及按端点建立的邻接索引。
        """
        if self._bootstrap_parsed is None:
            self._ensure_database()
            with SessionLocal() as session:
                node_rows = session.scalars(select(CorpusGraphNode).where(CorpusGraphNode.corpus_id == self.manifest["corpus_id"])).all()
                edge_rows = session.scalars(select(CorpusGraphEdge).where(CorpusGraphEdge.corpus_id == self.manifest["corpus_id"])).all()
                entities = {row.id: {"id": row.id, "label": row.label, "kind": row.kind, "source": row.source_path} for row in node_rows}
                edges = [{"id": row.id, "source": row.source, "target": row.target, "type": row.type, "origin": row.origin,
                          "derivation": row.derivation} for row in edge_rows]
                adjacency: dict[str, list[dict]] = defaultdict(list)
                for edge in edges:
                    if edge["source"] in entities and edge["target"] in entities:
                        adjacency[edge["source"]].append(edge)
                        adjacency[edge["target"]].append(edge)
                return entities, edges, adjacency
        entities: dict[str, dict] = {}
        sources = [
            ("graph/nodes.yaml", "事实", lambda x: x.get("label") or x["id"]),
            ("graph/rules.yaml", "规则", lambda x: x.get("narrative_zh") or x["id"]),
            ("graph/claims.yaml", "论断", lambda x: x.get("label") or x["id"]),
            ("evidence/evidence.yaml", "证据", lambda x: x.get("title") or x["id"]),
            ("chunks/chunks.jsonl", "正文", lambda x: x.get("text", "")[:42]),
            ("quality/conflicts.yaml", "冲突", lambda x: x.get("what") or x.get("id")),
            ("quality/gaps.yaml", "缺口", lambda x: x.get("what") or x.get("id")),
        ]
        for path, kind, label_of in sources:
            for item in self.parsed(path):
                entities[item["id"]] = {"id": item["id"], "label": str(label_of(item))[:64], "kind": kind, "source": path}
        for section in self.parsed("outline.yaml")["sections"]:
            entities[section["id"]] = {"id": section["id"], "label": section["title"], "kind": "章节", "source": "outline.yaml"}
        edges = []
        for item in self.parsed("graph/relations.yaml"):
            edges.append({"id": item["id"], "source": item["from_id"], "target": item["to_id"], "type": item["type"], "origin": "graph/relations.yaml"})
        for rule in self.parsed("graph/rules.yaml"):
            for dep in rule["deps"]:
                edges.append({"id": f"{rule['id']}:{dep}:input", "source": dep, "target": rule["id"], "type": "RULE_INPUT", "origin": "graph/rules.yaml"})
            edges.append({"id": f"{rule['id']}:output", "source": rule["id"], "target": rule["target"], "type": "RULE_OUTPUT", "origin": "graph/rules.yaml"})
        for claim in self.parsed("graph/claims.yaml"):
            for node_id in claim.get("supports", []):
                edges.append({"id": f"{claim['id']}:{node_id}:support", "source": node_id, "target": claim["id"], "type": "CLAIM_SUPPORT", "origin": "graph/claims.yaml"})
            for evidence_id in claim.get("must_cite", []):
                edges.append({"id": f"{claim['id']}:{evidence_id}:cite", "source": evidence_id, "target": claim["id"], "type": "MUST_CITE", "origin": "graph/claims.yaml"})
        for conflict in self.parsed("quality/conflicts.yaml"):
            for node_id in conflict.get("nodes", []):
                edges.append({"id": f"{conflict['id']}:{node_id}", "source": conflict["id"], "target": node_id, "type": "CONFLICT_NODE", "origin": "quality/conflicts.yaml"})
        for gap in self.parsed("quality/gaps.yaml"):
            for node_id in gap.get("nodes", []):
                edges.append({"id": f"{gap['id']}:{node_id}", "source": gap["id"], "target": node_id, "type": "GAP_NODE", "origin": "quality/gaps.yaml"})
        adjacency: dict[str, list[dict]] = defaultdict(list)
        for edge in edges:
            if edge["source"] in entities and edge["target"] in entities:
                adjacency[edge["source"]].append(edge)
                adjacency[edge["target"]].append(edge)
        return entities, edges, adjacency

    def search_entities(self, query: str, limit: int = 20) -> list[dict]:
        """按 ID 或名称定位图谱中的真实对象。"""
        needle = query.strip().casefold()
        if not needle:
            return []
        entities, _, _ = self.graph()
        matched = [item for item in entities.values() if needle in item["id"].casefold() or needle in item["label"].casefold()]
        matched.sort(key=lambda item: (0 if item["id"].casefold() == needle else 1 if item["id"].casefold().startswith(needle) else 2, item["id"]))
        return matched[:limit]

    def graph_slice(self, focus: str, depth: int = 1, edge_types: set[str] | None = None, node_kinds: set[str] | None = None, direction: str = "both", limit: int = 90) -> dict:
        """按焦点和跳数裁剪图，避免一次渲染 1,666 条原始边。

        Args:
            focus: 节点、规则、论断或证据 ID。
            depth: 邻居跳数，最大 2。
            edge_types: 可选的关系类型过滤。
            limit: 最大节点数。

        Returns:
            节点、边、类型计数及源关系总数。

        Raises:
            ValueError: 焦点不存在。
        """
        entities, all_edges, adjacency = self.graph()
        if focus not in entities:
            raise ValueError("图谱焦点不存在")
        if direction not in ("both", "out", "in"):
            raise ValueError("图谱方向无效")
        selected = {focus}
        queue = deque([(focus, 0)])
        candidate_edges = {}
        while queue and len(selected) < limit:
            current, level = queue.popleft()
            if level >= depth:
                continue
            for edge in adjacency[current]:
                if edge_types and edge["type"] not in edge_types:
                    continue
                if direction == "out" and edge["source"] != current:
                    continue
                if direction == "in" and edge["target"] != current:
                    continue
                other = edge["target"] if edge["source"] == current else edge["source"]
                if node_kinds and entities[other]["kind"] not in node_kinds:
                    continue
                candidate_edges[edge["id"]] = edge
                if other not in selected and len(selected) < limit:
                    selected.add(other)
                    queue.append((other, level + 1))
        visible_edges = [edge for edge in candidate_edges.values() if edge["source"] in selected and edge["target"] in selected]
        return {
            "focus": focus,
            "nodes": [entities[key] for key in sorted(selected)],
            "edges": visible_edges[:180],
            "raw_relation_count": len(self.parsed("graph/relations.yaml")),
            "derived_edge_count": len(all_edges) - len(self.parsed("graph/relations.yaml")),
            "edge_type_counts": dict(Counter(edge["type"] for edge in visible_edges)),
            "edge_type_options": sorted({edge["type"] for edge in all_edges}),
            "node_kind_options": sorted({entity["kind"] for entity in entities.values()}),
            "truncated": len(selected) >= limit or len(visible_edges) > 180,
        }

    def entity(self, entity_id: str) -> dict:
        """返回图谱实体的原产物详情。

        Args:
            entity_id: 图谱稳定 ID。

        Returns:
            源文件、原记录与实体摘要。

        Raises:
            ValueError: ID 不存在。
        """
        entities, _, _ = self.graph()
        if entity_id not in entities:
            raise ValueError("图谱实体不存在")
        item = entities[entity_id]
        source = item["source"]
        collection = self.parsed(source)
        if isinstance(collection, list):
            detail = next((record for record in collection if record.get("id") == entity_id), None)
        elif source == "outline.yaml":
            detail = next((record for record in collection["sections"] if record.get("id") == entity_id), None)
        else:
            detail = None
        number = next((n for n, paths in self._category_files.items() if source in paths), None)
        return {"entity": item, "category_number": number, "detail": detail}
