"""Offline, read-only ablation of the frozen corpus against two current features.

This script never opens the application database and never executes the archive's
validate_package.py.  A deliberately unusable DATABASE_URL guards accidental DB
access if a future code change leaves the in-memory bootstrap path.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import yaml


PROJECT = Path(__file__).resolve().parents[2]
ARCHIVE = PROJECT / "demo-data" / "澄岳精密" / "解析产物"
OUTPUT = Path(__file__).with_name("results.json")
os.environ["DATABASE_URL"] = "postgresql+psycopg://invalid:invalid@127.0.0.1:1/corpus_necessity_offline"
sys.path.insert(0, str(PROJECT / "backend"))

from app.corpus import CorpusRepository  # noqa: E402
from app.writing import render_cases, source_cases  # noqa: E402


def parse(path: str) -> object:
    raw = (ARCHIVE / path).read_text(encoding="utf-8")
    if path.endswith(".jsonl"):
        return [json.loads(line) for line in raw.splitlines() if line.strip()]
    if path.endswith((".yaml", ".yml")):
        return yaml.safe_load(raw)
    raise ValueError(f"Probe does not parse {path}")


def run_chapter_validation(documents: dict[str, object]) -> dict:
    class MemoryCorpus:
        @staticmethod
        def parsed(path: str) -> object:
            return documents[path]

    sources = source_cases(MemoryCorpus())
    facts = {
        "first_year_demand": {"value": "300000", "status": "PROVIDED"},
        "qualified_capacity": {"value": "254016", "status": "PROVIDED"},
        "planned_sales": {"value": "254016", "status": "COMPUTED"},
        "supplier_name": {"value": "演示供应商", "status": "PROVIDED"},
        "equipment_unit_price": {"value": "1234.50", "status": "PROVIDED"},
    }
    bindings = {"N017": "first_year_demand", "N034": "qualified_capacity", "N035": "planned_sales",
                "supplier_name": "supplier_name", "N080": "equipment_unit_price"}
    rules = [SimpleNamespace(target_key="planned_sales", expression="min(first_year_demand, qualified_capacity)")]
    output = render_cases(facts, rules, bindings, sources)
    assert len(sources) == 2 and len(output["blocks"]) == 2
    assert any("254,016" in block["text"] for block in output["blocks"])
    return {"source_cases": len(sources), "blocks": len(output["blocks"]), "blocked": output["blocked"]}


def run_graph(manifest: dict, documents: dict[str, object]) -> dict:
    repository = CorpusRepository(bootstrap=(manifest, documents, {number: {} for number in range(1, 31)}))
    nodes, edges, _ = repository.graph()
    raw = sum(edge["origin"] == "graph/relations.yaml" for edge in edges)
    return {"nodes": len(nodes), "edges": len(edges), "raw_edges": raw, "derived_edges": len(edges) - raw}


def run() -> dict:
    manifest = parse("manifest.yaml")
    assert isinstance(manifest, dict)
    entries = manifest["files"]
    category_files = {int(entry["no"]): [item["path"] for item in entry.get("files", [])]
                      if "files" in entry else [entry["path"]] for entry in entries}
    assert set(category_files) == set(range(1, 31))
    all_files = {str(path.relative_to(ARCHIVE)) for path in ARCHIVE.rglob("*") if path.is_file()}
    artifact_files = {path for paths in category_files.values() for path in paths}
    auxiliary_files = {"README.md", "SHA256SUMS", "validate_package.py"}
    assert len(artifact_files) == 114 and len(all_files) == 117
    assert artifact_files | auxiliary_files == all_files and not artifact_files & auxiliary_files

    checksums = {}
    for line in (ARCHIVE / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        digest, path = line.split("  ", 1)
        assert path not in checksums
        checksums[path] = digest
    assert set(checksums) == all_files - {"SHA256SUMS"} and len(checksums) == 116
    mismatches = [path for path, digest in checksums.items()
                  if hashlib.sha256((ARCHIVE / path).read_bytes()).hexdigest() != digest]
    assert not mismatches, mismatches

    # These are the exact paths the two probed functions currently request.
    needed = {
        "outline.yaml", "chunks/chunks.jsonl", "chunks/skeletons.jsonl", "graph/nodes.yaml",
        "graph/rules.yaml", "graph/relations.yaml", "graph/claims.yaml", "evidence/evidence.yaml",
        "quality/conflicts.yaml", "quality/gaps.yaml",
    }
    documents = {path: parse(path) for path in needed}
    baseline_chapter = run_chapter_validation(documents)
    baseline_graph = run_graph(manifest, documents)
    assert baseline_graph["raw_edges"] == 1666 and baseline_graph["derived_edges"] == 566

    rows = []
    for number in range(1, 31):
        masked = {path: value for path, value in documents.items() if path not in category_files[number]}
        try:
            run_chapter_validation(masked)
            chapter_available = True
        except KeyError:
            chapter_available = False
        try:
            run_graph(manifest, masked)
            graph_available = True
        except (KeyError, ValueError):
            graph_available = False
        rows.append({"number": number, "path": entries[number - 1]["path"],
                     "physical_file_count": len(category_files[number]),
                     "chapter_validation_available": chapter_available,
                     "graph_builder_available": graph_available})

    result = {
        "method": "One-category-at-a-time masking of parsed, in-memory source data",
        "scope": ["writing.source_cases + writing.render_cases", "CorpusRepository.graph in bootstrap mode"],
        "database_access": "disabled by invalid localhost URL; no database methods called",
        "archive": {"corpus_id": manifest["corpus_id"], "version": manifest["version"],
                    "manifest_sha256": hashlib.sha256((ARCHIVE / "manifest.yaml").read_bytes()).hexdigest(),
                    "categories": len(category_files), "artifacts": len(artifact_files),
                    "auxiliary_files": len(auxiliary_files), "checksum_entries": len(checksums), "checksum_mismatches": mismatches},
        "baseline": {"chapter_validation": baseline_chapter, "graph_builder": baseline_graph},
        "categories": rows,
    }
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    result = run()
    print(f"{result['archive']['categories']} classes, {result['archive']['artifacts']} artifacts, "
          f"{result['archive']['checksum_entries']} hashes verified")
    print("Chapter validation depends on:", [row["number"] for row in result["categories"]
          if not row["chapter_validation_available"]])
    print("Graph builder depends on:", [row["number"] for row in result["categories"]
          if not row["graph_builder_available"]])
