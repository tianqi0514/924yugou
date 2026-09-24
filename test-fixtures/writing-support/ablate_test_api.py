"""Controlled per-category writing-package masking in report_platform_test.

Masking happens only in the test client's package boundary. It does not delete or
modify frozen corpus rows. A missing-item error is classified as implementation
coupling, not proof that an independent category/file is universally necessary.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

from capture_test_api import TEST_URL, _body, _expect, _record_response, _change_fact
from summarize import _semantic_paragraphs, _plain


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def _project(client, *, scenario: str, values: list[tuple[str, str]]):
    project_id = _expect(client.post("/api/projects", json={"name": f"QA-逐类遮蔽-{scenario}"}), 201)["id"]
    reference = _expect(client.get(f"/api/projects/{project_id}/writing/reference"), 200)
    _expect(client.put(f"/api/projects/{project_id}/writing/reference", json={
        "corpus_id": reference["corpus_id"], "corpus_version": reference["corpus_version"],
        "target_report_type": "feasibility", "accepted": True,
    }), 200)
    if values:
        _expect(client.post(f"/api/projects/{project_id}/writing/setup"), 200)
        _expect(client.put(f"/api/projects/{project_id}/writing/bindings", json={"bindings": {
            "N017": "first_year_demand", "N034": "qualified_capacity", "N035": "planned_sales",
            "supplier_name": "supplier_name", "N080": "equipment_unit_price",
        }}), 200)
        for key, value in values:
            _change_fact(client, project_id, {"key": key, "value": value, "source": "QA合成输入"})
    report_id = _expect(client.post(f"/api/projects/{project_id}/reports", json={
        "title": f"{scenario} 逐类遮蔽"
    }), 201)["id"]
    return project_id, report_id


def _candidate_summary(preview: dict) -> dict:
    paragraphs = _semantic_paragraphs(preview.get("paragraphs", []))
    return {
        "text": [_plain(paragraph) for paragraph in paragraphs],
        "source_refs": [{"record_id": ref.get("record_id"), "use": ref.get("use")}
                        for paragraph in paragraphs for ref in paragraph.get("source_refs", [])],
        "issue_codes": [item.get("code") for item in preview.get("issues", [])],
    }


def _study(client, main_module, project_id: str, report_id: str, section_id: str,
           numbers: list[int]) -> dict:
    url = f"/api/projects/{project_id}/reports/{report_id}/writing/preview"
    package = _expect(client.get(f"/api/projects/{project_id}/writing/packages/{section_id}"), 200)
    approved = [item["item_id"] for item in package["items"] if item["decision"] == "selectable"]
    baseline = _expect(client.post(url, json={"section_id": section_id,
                                             "approved_item_ids": approved, "mode": "guided"}), 200)
    before = _expect(client.get(f"/api/projects/{project_id}/reports/{report_id}"), 200)
    results = []
    original = main_module.writing_package
    for number in numbers:
        removed = {item["item_id"] for item in package["items"] if item["category_number"] == number}

        def masked(session, pid, sid, target=number):
            original_package = original(session, pid, sid)
            removed_ids = {item["item_id"] for item in original_package["items"]
                           if item["category_number"] == target}
            return {**original_package,
                    "items": [item for item in original_package["items"] if item["item_id"] not in removed_ids],
                    "issues": [issue for issue in original_package["issues"]
                               if not set(issue.get("item_ids", [])) & removed_ids]}

        selected = [item_id for item_id in approved if item_id not in removed]
        with patch.object(main_module, "writing_package", side_effect=masked):
            masked_package = _expect(client.get(
                f"/api/projects/{project_id}/writing/packages/{section_id}"), 200)
            response = client.post(url, json={"section_id": section_id,
                                              "approved_item_ids": selected, "mode": "guided"})
        after = _expect(client.get(f"/api/projects/{project_id}/reports/{report_id}"), 200)
        if before["version"] != after["version"] or before["content"] != after["content"]:
            raise RuntimeError("A masked preview wrote to the report")
        remaining = {item["item_id"] for item in masked_package["items"]}
        if removed & remaining or len(masked_package["items"]) != len(package["items"]) - len(removed):
            raise RuntimeError("Masked package discovery count is inconsistent")
        detail_statuses = {}
        for item in package["items"]:
            if item["item_id"] in removed:
                detail = client.get(f"/api/projects/{project_id}/writing/sources/"
                                    f"{number}/{item['record_id']}")
                detail_statuses[item["record_id"]] = detail.status_code
        entry = {"category_number": number, "removed_item_ids": sorted(removed),
                 "package_source_item_count_before": len(package["items"]),
                 "package_source_item_count_after": len(masked_package["items"]),
                 "removed_source_detail_statuses_by_direct_url": detail_statuses,
                 "response": _record_response(response)}
        if response.status_code == 200:
            candidate = _candidate_summary(_body(response))
            reference = _candidate_summary(baseline)
            entry["text_changed"] = candidate["text"] != reference["text"]
            entry["source_refs_changed"] = candidate["source_refs"] != reference["source_refs"]
            entry["issues_changed"] = candidate["issue_codes"] != reference["issue_codes"]
            entry["classification"] = ("observable_text_or_audit_change" if any(
                entry[key] for key in ("text_changed", "source_refs_changed", "issues_changed"))
                else "no_observed_candidate_change")
        else:
            entry["classification"] = "implementation_coupling"
        results.append(entry)
    return {"project_id": project_id, "report_id": report_id, "section_id": section_id,
            "baseline": {"used_items": baseline["used_items"],
                         "candidate": _candidate_summary(baseline)}, "masks": results}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-test-db", action="store_true", required=True)
    parser.add_argument("--review-only-extra", action="store_true",
                        help="Run only the additional CAT-19/CAT-24 review-item masks")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    existing = os.environ.get("DATABASE_URL")
    if existing and not existing.endswith("/report_platform_test"):
        raise RuntimeError("DATABASE_URL does not target report_platform_test")
    os.environ["DATABASE_URL"] = existing or TEST_URL
    sys.path.insert(0, str(ROOT / "backend"))
    from fastapi.testclient import TestClient
    from app.db import engine
    import app.main as main_module

    if engine.url.database != "report_platform_test":
        raise RuntimeError("Refusing to write outside report_platform_test")
    with TestClient(main_module.app) as client:
        p1, r1 = _project(client, scenario="S4", values=[
            ("first_year_demand", "300000"), ("qualified_capacity", "254016")])
        s4 = _study(client, main_module, p1, r1, "S4",
                    [19, 24] if args.review_only_extra else [4, 27, 10, 13, 15, 9, 18, 12, 26])
        studies = [s4]
        if not args.review_only_extra:
            p2, r2 = _project(client, scenario="S5.2", values=[])
            studies.append(_study(client, main_module, p2, r2, "S5.2", [23, 18]))
    output = {"schema_version": "writing-support-ablation/1.0", "database": engine.url.database,
              "scope": "writing_package_boundary_mask_only", "studies": studies}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Captured {sum(len(study['masks']) for study in output['studies'])} controlled masks: {args.output}")


if __name__ == "__main__":
    main()
