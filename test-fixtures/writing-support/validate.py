"""Read-only self-check of preregistered writing experiments. No DB or model calls."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
CORPUS = ROOT / "demo-data/澄岳精密/解析产物"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def read_yaml(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check() -> None:
    cases = read_json(HERE / "cases.json")
    protocol = read_json(HERE / "protocol.json")
    manifest = read_yaml(CORPUS / "manifest.yaml")

    assert cases["status"] == "preregistered_not_executed"
    assert protocol["status"] == "design_only_not_executed"
    assert cases["corpus"]["corpus_id"] == manifest["corpus_id"]
    assert cases["corpus"]["version"] == manifest["version"]
    assert cases["corpus"]["manifest_sha256"] == digest(CORPUS / "manifest.yaml")
    assert cases["project_setup"]["initial_project_facts"] == []
    assert cases["project_setup"]["allow_historical_fact_value_copy"] is False

    categories = protocol["categories"]
    assert len(categories) == 30
    assert [entry["number"] for entry in categories] == list(range(1, 31))
    assert [(entry["number"], entry["path"]) for entry in categories] == [
        (entry["no"], entry["path"]) for entry in manifest["files"]
    ]
    assert {entry["actual"] for entry in categories} == {"not_run"}
    assert [entry["id"] for entry in protocol["package_variants"]] == [
        "minimal_information_contract", "applicable_conditional", "all_available"
    ]
    assert len(protocol["package_variants"][-1]["category_numbers"]) == 30

    source_ids = set()
    for path, mode in (
        ("chunks/chunks.jsonl", "jsonl"),
        ("graph/nodes.yaml", "yaml"),
        ("graph/rules.yaml", "yaml"),
        ("graph/claims.yaml", "yaml"),
        ("evidence/evidence.yaml", "yaml"),
        ("quality/conflicts.yaml", "yaml"),
    ):
        rows = read_jsonl(CORPUS / path) if mode == "jsonl" else read_yaml(CORPUS / path)
        source_ids.update(row["id"] for row in rows)
    templates = (CORPUS / "style/section_templates").glob("*.yaml")
    source_ids.update(read_yaml(path)["section_id"] for path in templates)

    required_case_ids = {
        "WS-C0052-BASE", "WS-C0052-ZERO", "WS-C0052-MISSING",
        "WS-C0083-IDENTITY", "WS-X001-CONFLICT", "WS-L002-CLAIM", "WS-NEG-ACCIDENT",
    }
    assert {case["id"] for case in cases["cases"]} == required_case_ids
    for case in cases["cases"]:
        assert case["expected"] and case["forbidden"] and case["failure_if"]
        assert set(case["historical_refs"]).issubset(source_ids), case["id"]
        assert all(fact["source"] != "historical_corpus_value" for fact in case["project_facts"])
    negative = next(case for case in cases["cases"] if case["id"] == "WS-NEG-ACCIDENT")
    assert digest(ROOT / negative["held_out_document"]["path"]) == negative["held_out_document"]["sha256"]

    rule = next(row for row in read_yaml(CORPUS / "graph/rules.yaml") if row["id"] == "R006")
    assert rule["target"] == "N035" and rule["expr"] == "min(N017,N034)"
    conflict = next(row for row in read_yaml(CORPUS / "quality/conflicts.yaml") if row["id"] == "X001")
    assert conflict["status"] == "open" and set(conflict["evidence"]) == {"EV-06A", "EV-06B"}
    evidence = next(row for row in read_yaml(CORPUS / "evidence/evidence.yaml") if row["id"] == "EV-01")
    assert evidence["original_document_available"] is False
    claim = next(row for row in read_yaml(CORPUS / "graph/claims.yaml") if row["id"] == "L002")
    assert "EV-01" in claim["must_cite"] and claim["status"] == "source_asserted_not_independently_verified"
    print("Writing-support preregistration: 7 cases, 30 categories, 3 variants; source IDs and hashes verified. No DB/model call.")


if __name__ == "__main__":
    check()
