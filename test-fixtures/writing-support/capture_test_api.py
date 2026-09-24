"""Run preregistered guided-writing experiments against report_platform_test only.

This script creates isolated QA projects, captures actual API responses, and never
drops tables. Run only after other tests that reset report_platform_test finish.
It must never be pointed at the application database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TEST_URL = "postgresql+psycopg://report:local_development_only@127.0.0.1:55432/report_platform_test"


def _body(response) -> dict:
    try:
        value = response.json()
    except ValueError:
        value = {"text": response.text[:500]}
    return value if isinstance(value, dict) else {"data": value}


def _expect(response, expected: int) -> dict:
    if response.status_code != expected:
        raise RuntimeError(f"{response.request.method} {response.request.url.path}: expected {expected}, "
                           f"got {response.status_code}: {response.text[:400]}")
    return _body(response)


def _record_response(response) -> dict:
    return {"status_code": response.status_code, "body": _body(response)}


def _zip_evidence(response) -> dict:
    if response.status_code != 200:
        return _record_response(response)
    from docx import Document
    import pymupdf

    with ZipFile(BytesIO(response.content)) as archive:
        audit = json.loads(archive.read("audit.json"))
        docx_bytes = archive.read("report.docx")
        pdf_bytes = archive.read("report.pdf")
    doc = Document(BytesIO(docx_bytes))
    docx_text = "\n".join(paragraph.text for paragraph in doc.paragraphs)
    pdf = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    pdf_text = "\n".join(page.get_text() for page in pdf)
    pdf.close()
    label = "预审稿 · 来源待核 · 不构成正式结论"
    # PyMuPDF's CID-font text extraction may omit the decorative middle dots.
    compact_label = "".join(label.replace("·", "").split())
    compact_pdf = "".join(pdf_text.replace("·", "").split())
    return {"status_code": response.status_code, "byte_count": len(response.content),
            "audit": audit, "docx_preview_label_present": label in docx_text,
            "pdf_preview_label_present": compact_label in compact_pdf,
            "docx_sha256": hashlib.sha256(docx_bytes).hexdigest(),
            "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest()}


def _change_fact(client, project_id: str, fact: dict) -> dict | None:
    if fact["value"] is None:
        return None
    body = {"fact_key": fact["key"], "value": fact["value"],
            "source": fact["source"], "reason": "预注册 QA 合成输入"}
    proposal = _expect(client.post(f"/api/projects/{project_id}/changes/preview", json=body), 200)
    committed = _expect(client.post(f"/api/projects/{project_id}/changes/commit", json={
        **body, "base_version": proposal["base_version"], "preview_token": proposal["preview_token"],
    }), 200)
    return {"key": fact["key"], "preview_changes": proposal.get("changes", []),
            "committed_changes": committed.get("changes", [])}


def _run_case(client, case: dict, variant: dict, corpus_id: str, corpus_version: str) -> dict:
    scenario_id, variant_id = case["id"], variant["id"]
    run_id = f"{scenario_id}::{variant_id}"
    project = _expect(client.post("/api/projects", json={"name": f"QA-写作支撑验证-{run_id}"}), 201)
    project_id = project["id"]
    record = {"run_id": run_id, "scenario_id": scenario_id, "package_variant": variant_id,
              "mode": "guided", "project_id": project_id,
              "section_id": case.get("target_section"),
              "project_initial_facts": _expect(client.get(f"/api/projects/{project_id}/facts"), 200)["facts"]}
    if record["project_initial_facts"]:
        raise RuntimeError(f"New project unexpectedly has facts: {run_id}")

    report_type = "accident_investigation" if case["kind"] == "cross_type_negative_control" else "feasibility"
    reference_body = {"corpus_id": corpus_id, "corpus_version": corpus_version,
                      "target_report_type": report_type, "accepted": True}
    reference = client.put(f"/api/projects/{project_id}/writing/reference", json=reference_body)
    if case["kind"] == "cross_type_negative_control":
        record["reference_error"] = _record_response(reference)
        record["project_facts_after"] = _expect(client.get(f"/api/projects/{project_id}/facts"), 200)["facts"]
        return record
    record["reference"] = _expect(reference, 200)

    if case["project_facts"]:
        record["setup"] = _expect(client.post(f"/api/projects/{project_id}/writing/setup"), 200)
        bindings = {"N017": "first_year_demand", "N034": "qualified_capacity", "N035": "planned_sales",
                    "supplier_name": "supplier_name", "N080": "equipment_unit_price"}
        record["bindings"] = _expect(client.put(f"/api/projects/{project_id}/writing/bindings",
                                             json={"bindings": bindings}), 200)
        record["fact_changes"] = [_change_fact(client, project_id, fact) for fact in case["project_facts"]]
    record["project_facts_after"] = _expect(client.get(f"/api/projects/{project_id}/facts"), 200)["facts"]
    record["project_rules_after"] = _expect(client.get(f"/api/projects/{project_id}/rules"), 200)["rules"]
    record["project_fact_evidence"] = {
        fact["key"]: _record_response(client.get(f"/api/projects/{project_id}/facts/{fact['key']}/evidence"))
        for fact in case["project_facts"] if fact["value"] is not None
    }

    report = _expect(client.post(f"/api/projects/{project_id}/reports", json={
        "title": f"{scenario_id} 写作验证"
    }), 201)
    report_id = report["id"]
    record["report_id"] = report_id
    record["report_before_preview"] = _expect(client.get(f"/api/projects/{project_id}/reports/{report_id}"), 200)
    section_id = case["target_section"]
    package = _expect(client.get(f"/api/projects/{project_id}/writing/packages/{section_id}"), 200)
    record["package"] = package
    important_ids = set(case["historical_refs"])
    record["source_details"] = {
        item["record_id"]: _expect(client.get(
            f"/api/projects/{project_id}/writing/sources/{item['category_number']}/{item['record_id']}"), 200)
        for item in package["items"] if item.get("semantic_id") in important_ids
    }
    category_set = set(variant["category_numbers"])
    approved = [item["item_id"] for item in package["items"]
                if item["decision"] == "selectable" and item["category_number"] in category_set]
    request = {"section_id": section_id, "approved_item_ids": approved, "mode": "guided"}
    preview = client.post(f"/api/projects/{project_id}/reports/{report_id}/writing/preview", json=request)
    if preview.status_code != 200:
        record["preview_error"] = _record_response(preview)
        record["persisted_report"] = _expect(client.get(f"/api/projects/{project_id}/reports/{report_id}"), 200)
        return record
    record["preview"] = _body(preview)
    # Preview must not persist a report change; capture the read-back before commit.
    record["report_after_preview"] = _expect(client.get(f"/api/projects/{project_id}/reports/{report_id}"), 200)
    commit = client.post(f"/api/projects/{project_id}/reports/{report_id}/writing/commit",
                         json={**request, **record["preview"]})
    if commit.status_code == 200:
        record["commit"] = _body(commit)
    else:
        record["commit_error"] = _record_response(commit)
    record["persisted_report"] = _expect(client.get(f"/api/projects/{project_id}/reports/{report_id}"), 200)
    record["preview_export"] = _zip_evidence(client.get(
        f"/api/projects/{project_id}/reports/{report_id}/export?level=preview"))
    review = client.post(f"/api/projects/{project_id}/reports/{report_id}/review")
    record["review_result"] = _record_response(review)
    export = client.get(f"/api/projects/{project_id}/reports/{report_id}/export")
    record["export_result"] = {"status_code": export.status_code,
                               "body": _body(export) if export.status_code != 200 else None,
                               "byte_count": len(export.content) if export.status_code == 200 else None}
    if scenario_id == "WS-C0052-BASE" and variant_id == "applicable_conditional":
        started = time.perf_counter()
        model = client.post(f"/api/projects/{project_id}/reports/{report_id}/writing/preview",
                            json={**request, "mode": "model"})
        record["model_preview"] = {**_record_response(model),
                                   "elapsed_ms": round((time.perf_counter() - started) * 1000)}
        record["report_after_model_preview"] = _expect(
            client.get(f"/api/projects/{project_id}/reports/{report_id}"), 200)
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-test-db", action="store_true", required=True,
                        help="Acknowledge that this creates QA projects in report_platform_test")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    existing = os.environ.get("DATABASE_URL")
    if existing and not existing.endswith("/report_platform_test"):
        raise RuntimeError("DATABASE_URL does not target report_platform_test; refusing to run")
    os.environ["DATABASE_URL"] = existing or TEST_URL
    sys.path.insert(0, str(ROOT / "backend"))
    from fastapi.testclient import TestClient
    from app.db import engine
    from app.main import app

    if engine.url.database != "report_platform_test":
        raise RuntimeError("Refusing to write outside report_platform_test")
    cases = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
    protocol = json.loads((HERE / "protocol.json").read_text(encoding="utf-8"))
    with TestClient(app) as client:
        builtin = next(item for item in _expect(client.get("/api/projects"), 200)["data"] if item["is_builtin"])
        summary = _expect(client.get(f"/api/projects/{builtin['id']}/corpus/summary"), 200)
        corpus_id = summary["corpus_id"]
        corpus_version = summary["version"]
        if (corpus_id, corpus_version) != (cases["corpus"]["corpus_id"], cases["corpus"]["version"]):
            raise RuntimeError("Test DB corpus differs from preregistered identity")
        output = {"schema_version": "writing-support-api-capture/1.0",
                  "test_database": engine.url.database,
                  "fixture_sha256": hashlib.sha256((HERE / "cases.json").read_bytes()).hexdigest(),
                  "corpus_id": corpus_id, "corpus_version": corpus_version, "runs": []}
        for case in cases["cases"]:
            for variant in protocol["package_variants"]:
                output["runs"].append(_run_case(client, case, variant, corpus_id, corpus_version))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Captured {len(output['runs'])} actual API runs from {engine.url.database}: {args.output}")


if __name__ == "__main__":
    main()
