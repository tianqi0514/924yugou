"""Summarize captured real writing API responses without touching the database.

Input is a JSON object with ``runs``. Each run embeds the unchanged JSON bodies of
GET package, POST preview, POST commit and GET persisted report as ``package``,
``preview``, ``commit`` and ``persisted_report``. An HTTP rejection can be recorded
as ``preview_error`` or ``reference_error`` with ``status_code`` and ``body``.
This script never creates success evidence: absent responses stay ``not_run``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _refs(value: Any) -> list[dict]:
    refs: list[dict] = []
    if isinstance(value, dict):
        source_refs = value.get("source_refs")
        if isinstance(source_refs, list):
            refs.extend(item for item in source_refs if isinstance(item, dict))
        for key, child in value.items():
            if key != "source_refs":
                refs.extend(_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.extend(_refs(child))
    return refs


def _report_content(response: dict) -> Any:
    if "content" in response:
        return response["content"]
    if isinstance(response.get("report"), dict):
        return response["report"].get("content", [])
    return []


def _semantic_paragraphs(paragraphs: list[dict]) -> list[dict]:
    """Compare content across isolated projects without their random UUIDs.

    Project rule IDs are still checked and retained in the raw capture/audit;
    only the cross-project equivalence signature removes them. Expression,
    inputs, revisions, text, source identities and issue codes remain.
    """
    normalized = []
    for paragraph in paragraphs:
        row = {key: value for key, value in paragraph.items() if key != "id"}
        if "project_rule_refs" in row:
            row["project_rule_refs"] = [
                {key: value for key, value in rule.items() if key != "rule_id"}
                for rule in row["project_rule_refs"]
            ]
        normalized.append(row)
    return normalized


def _plain(value: Any) -> str:
    if isinstance(value, dict):
        if value.get("type") == "fact_ref":
            return str(value.get("display", ""))
        if "text" in value:
            return str(value["text"])
        return "".join(_plain(child) for child in value.get("children", []))
    if isinstance(value, list):
        return "".join(_plain(child) for child in value)
    return ""


def _fact_refs(value: Any) -> list[dict]:
    if isinstance(value, dict):
        own = [value] if value.get("type") == "fact_ref" else []
        return own + [ref for child in value.get("children", []) for ref in _fact_refs(child)]
    if isinstance(value, list):
        return [ref for child in value for ref in _fact_refs(child)]
    return []


def _case_checks(run: dict, package: dict, preview: dict) -> list[dict]:
    scenario = run["scenario_id"]
    if scenario == "WS-NEG-ACCIDENT":
        error = run.get("reference_error") or {}
        return [{"name": "cross_type_rejected", "passed": error.get("status_code") == 409,
                 "observed": error.get("status_code")}]
    if run["package_variant"] == "minimal_information_contract":
        return [{"name": "minimal_variant_implementation_coupling",
                 "passed": None, "observed": (run.get("preview_error") or {}).get("status_code"),
                 "note": "A fixed implementation dependency failure cannot prove category necessity."}]

    result: list[dict] = []

    def check(name: str, condition: bool, observed: Any = None) -> None:
        result.append({"name": name, "passed": bool(condition), "observed": observed})

    facts = {item["key"]: item for item in run.get("project_facts_after", [])}
    text = "\n".join(_plain(p) for p in preview.get("paragraphs", []))
    issue_codes = {item.get("code") for item in preview.get("issues", [])}
    preview_export = run.get("preview_export") or {}
    preview_audit = preview_export.get("audit") or {}
    item_by_semantic = {item.get("semantic_id"): item for item in package.get("items", [])}
    details = run.get("source_details") or {}

    def source(semantic_id: str) -> dict:
        item = item_by_semantic.get(semantic_id) or {}
        return (details.get(item.get("record_id")) or {}).get("payload") or {}

    if scenario in {"WS-C0052-BASE", "WS-C0052-ZERO", "WS-C0052-MISSING", "WS-L002-CLAIM"}:
        expected = "0" if scenario == "WS-C0052-ZERO" else "254016"
        if scenario == "WS-C0052-MISSING":
            check("missing_dependency_unevaluable", facts.get("planned_sales", {}).get("status") == "UNEVALUABLE"
                  and facts.get("planned_sales", {}).get("value") is None,
                  facts.get("planned_sales"))
            check("missing_dependency_preview_rejected", (run.get("preview_error") or {}).get("status_code") == 409,
                  (run.get("preview_error") or {}).get("status_code"))
        else:
            check("project_min_result", facts.get("planned_sales", {}).get("value") == expected,
                  facts.get("planned_sales", {}).get("value"))
            check("condition_and_historical_value_safe", bool(preview) and "需求低于能力" not in text
                  and "180,000" not in text, text[:320])
            rules = [item for item in run.get("project_rules_after", []) if item.get("target_key") == "planned_sales"]
            check("project_rule_exists", len(rules) == 1 and rules[0].get("expression") ==
                  "min(first_year_demand, qualified_capacity)", rules)
            if rules and preview:
                refs = [entry for paragraph in preview.get("paragraphs", [])
                        for entry in paragraph.get("project_rule_refs", [])]
                expected_rule = rules[0]
                check("project_rule_in_candidate", any(ref.get("rule_id") == expected_rule.get("id")
                      and ref.get("expression") == expected_rule.get("expression") for ref in refs), refs)
                audit_refs = [entry for block in preview_audit.get("source_refs", [])
                              for entry in block.get("project_rule_refs", [])]
                check("project_rule_in_preview_audit", any(ref.get("rule_id") == expected_rule.get("id")
                      and ref.get("expression") == expected_rule.get("expression") for ref in audit_refs), audit_refs)
    if scenario == "WS-C0052-ZERO":
        check("zero_is_value_in_text", "计划销售量为0套" in text, text[:320])
    if scenario == "WS-C0083-IDENTITY":
        check("new_supplier_and_price_only", all(term in text for term in
              ("新拓设备", "12,345,000元/条", "1,234.50万元/条"))
              and all(term not in text for term in ("禾进装备", "14,800,000", "1,480.00", "机器人本体", "采购部门应")), text[:320])
        check("quote_scope_review_flag", "QUOTE_SCOPE_UNVERIFIED" in issue_codes, sorted(issue_codes))
    if scenario == "WS-X001-CONFLICT":
        conflict = source("X001")
        check("historical_conflict_open_two_sources", conflict.get("status") == "open"
              and set(conflict.get("evidence") or []) == {"EV-06A", "EV-06B"}, conflict)
        check("no_project_power_fact", not any("power" in key or "配电" in key for key in facts), list(facts))
        check("conflict_disclosed_without_determinate_conclusion", bool(preview)
              and "不作配电适配结论" in text and "POWER_CONFLICT_UNRESOLVED" in issue_codes, text[:320])
        check("formal_review_and_export_blocked", (run.get("review_result") or {}).get("status_code") != 200
              and (run.get("export_result") or {}).get("status_code") != 200,
              [(run.get("review_result") or {}).get("status_code"), (run.get("export_result") or {}).get("status_code")])
    if scenario == "WS-L002-CLAIM":
        claim, evidence = source("L002"), source("EV-01")
        check("historical_claim_and_excerpt_boundary", "EV-01" in (claim.get("must_cite") or [])
              and evidence.get("original_document_available") is False
              and claim.get("status") == "source_asserted_not_independently_verified",
              {"claim": claim.get("status"), "evidence_original_available": evidence.get("original_document_available")})
        check("caution_not_order_assertion", "不能据此认定已有已签订单" in text, text[:320])
    if preview and run.get("commit"):
        check("preview_zip_labeled", preview_export.get("status_code") == 200
              and preview_audit.get("delivery_status") == "preview_only"
              and preview_export.get("docx_preview_label_present") is True
              and preview_export.get("pdf_preview_label_present") is True,
              {"status": preview_export.get("status_code"),
               "delivery_status": preview_audit.get("delivery_status"),
               "docx_label": preview_export.get("docx_preview_label_present"),
               "pdf_label": preview_export.get("pdf_preview_label_present")})
    if scenario in {"WS-C0052-BASE", "WS-C0052-ZERO", "WS-C0083-IDENTITY", "WS-L002-CLAIM"} and preview:
        statuses = {key: value.get("body", {}).get("status") for key, value in
                    (run.get("project_fact_evidence") or {}).items()}
        check("synthetic_input_not_verified_evidence", bool(statuses)
              and all(status == "UNVERIFIED" for status in statuses.values()), statuses)
        check("formal_export_blocked_without_project_original", (run.get("review_result") or {}).get("status_code") != 200
              and (run.get("export_result") or {}).get("status_code") != 200,
              [(run.get("review_result") or {}).get("status_code"),
               (run.get("export_result") or {}).get("status_code")])
    model_response = run.get("model_preview") or {}
    if scenario == "WS-C0052-BASE" and model_response.get("status_code") == 200:
        model_body = model_response.get("body") or {}
        model_paragraphs = model_body.get("paragraphs") or []
        model_facts = {ref.get("fact_key"): ref.get("display")
                       for ref in _fact_refs(model_paragraphs)}
        model_text = "\n".join(_plain(paragraph) for paragraph in model_paragraphs)
        check("model_candidate_bound_to_current_facts",
              model_facts == {"first_year_demand": "300,000",
                              "qualified_capacity": "254,016", "planned_sales": "254,016"}
              and "需求低于能力" not in model_text and "禾进装备" not in model_text,
              {"fact_refs": model_facts, "text": model_text[:400]})
        check("model_candidate_rule_provenance",
              any(ref.get("expression") == "min(first_year_demand, qualified_capacity)"
                  for paragraph in model_paragraphs for ref in paragraph.get("project_rule_refs", [])),
              [ref for paragraph in model_paragraphs for ref in paragraph.get("project_rule_refs", [])])
    return result


def summarize(capture: dict) -> dict:
    protocol = json.loads((HERE / "protocol.json").read_text(encoding="utf-8"))
    cases = json.loads((HERE / "cases.json").read_text(encoding="utf-8"))
    allowed_cases = {item["id"] for item in cases["cases"]}
    allowed_variants = {item["id"] for item in protocol["package_variants"]}
    by_number = {row["number"]: {**row, "category_id": f"CAT-{row['number']:02d}",
                                 "corpus_id": capture.get("corpus_id"),
                                 "corpus_version": capture.get("corpus_version"),
                                 "package_visible": 0, "preview_used": 0,
                                 "preview_source_ref": 0, "report_source_ref": 0,
                                 "report_use_roles": {}, "rejected": 0, "run_ids": [],
                                 "package_record_ids": [], "persisted_record_ids": [],
                                 "rejected_record_ids": [], "artifact_ids": [], "persisted_sections": []}
                 for row in protocol["categories"]}
    seen_runs: set[str] = set()
    run_results: list[dict] = []
    comparisons: dict[str, list[dict]] = defaultdict(list)

    for run in capture.get("runs", []):
        run_id = str(run.get("run_id") or "")
        scenario_id = run.get("scenario_id")
        variant = run.get("package_variant")
        if not run_id or run_id in seen_runs:
            raise ValueError(f"Missing or duplicate run_id: {run_id}")
        seen_runs.add(run_id)
        if scenario_id not in allowed_cases or variant not in allowed_variants:
            raise ValueError(f"Unregistered case or variant: {run_id}")

        package = run.get("package") or {}
        preview = run.get("preview") or {}
        persisted = run.get("persisted_report") or {}
        commit = run.get("commit") or {}
        before_preview = run.get("report_before_preview") or {}
        after_preview = run.get("report_after_preview") or {}
        item_by_id = {item["record_id"]: item for item in package.get("items", [])
                      if isinstance(item, dict) and item.get("record_id")}
        errors: list[str] = []
        for item in item_by_id.values():
            number = item.get("category_number")
            if number not in by_number:
                errors.append(f"unknown category {number} in package")
                continue
            by_number[number]["package_visible"] += 1
            if item["record_id"] not in by_number[number]["package_record_ids"]:
                by_number[number]["package_record_ids"].append(item["record_id"])
            if item.get("artifact_id") and item["artifact_id"] not in by_number[number]["artifact_ids"]:
                by_number[number]["artifact_ids"].append(item["artifact_id"])
            if run_id not in by_number[number]["run_ids"]:
                by_number[number]["run_ids"].append(run_id)

        used = set(preview.get("used_items") or [])
        rejected = preview.get("rejected_items") or []
        preview_refs = _refs(preview.get("paragraphs") or [])
        report_refs = _refs(_report_content(persisted)) if commit else []
        if preview and {ref.get("record_id") for ref in preview_refs} != used:
            errors.append("preview used_items do not match paragraph source_refs")
        for record_id in used:
            item = item_by_id.get(record_id)
            if item is None:
                errors.append(f"used_items references unknown package item {record_id}")
            else:
                by_number[item["category_number"]]["preview_used"] += 1
        for item in rejected:
            item_id = item.get("item_id") if isinstance(item, dict) else None
            source_item = item_by_id.get(item_id)
            if source_item is None:
                errors.append(f"rejected_items references unknown package item {item_id}")
            else:
                by_number[source_item["category_number"]]["rejected"] += 1
                rejected_ids = by_number[source_item["category_number"]]["rejected_record_ids"]
                if item_id not in rejected_ids:
                    rejected_ids.append(item_id)
        for label, refs, counter in (
            ("preview", preview_refs, "preview_source_ref"),
            ("persisted", report_refs, "report_source_ref"),
        ):
            for ref in refs:
                record_id = ref.get("record_id")
                item = item_by_id.get(record_id)
                if item is None:
                    errors.append(f"{label} source_refs references unknown package item {record_id}")
                    continue
                if ref.get("category_id") != item.get("category_id") or ref.get("artifact_id") != item.get("artifact_id"):
                    errors.append(f"{label} source_refs identity mismatch for {record_id}")
                    continue
                if ref.get("corpus_id") != package.get("corpus_id") or ref.get("corpus_version") != package.get("corpus_version"):
                    errors.append(f"{label} source_refs corpus version mismatch for {record_id}")
                    continue
                by_number[item["category_number"]][counter] += 1
                if label == "persisted":
                    persisted_ids = by_number[item["category_number"]]["persisted_record_ids"]
                    if record_id not in persisted_ids:
                        persisted_ids.append(record_id)
                    section_id = run.get("section_id")
                    sections = by_number[item["category_number"]]["persisted_sections"]
                    if section_id and section_id not in sections:
                        sections.append(section_id)
                    role = str(ref.get("use") or "unspecified")
                    roles = by_number[item["category_number"]]["report_use_roles"]
                    roles[role] = roles.get(role, 0) + 1

        if commit and not persisted:
            errors.append("commit supplied without a persisted report read-back")
        if preview and before_preview and after_preview:
            if before_preview.get("version") != after_preview.get("version") or before_preview.get("content") != after_preview.get("content"):
                errors.append("preview changed persisted report content or version")
        if commit and before_preview and persisted:
            if not isinstance(persisted.get("version"), int) or persisted["version"] <= before_preview.get("version", -1):
                errors.append("commit did not advance persisted report version")
        after_model = run.get("report_after_model_preview") or {}
        if run.get("model_preview") and after_model and persisted:
            if after_model.get("version") != persisted.get("version") or after_model.get("content") != persisted.get("content"):
                errors.append("model preview changed persisted report content or version")
        result = {
            "run_id": run_id, "scenario_id": scenario_id, "package_variant": variant,
            "status": "captured" if preview or run.get("preview_error") or run.get("reference_error") else "package_only",
            "project_id": run.get("project_id"), "section_id": run.get("section_id"),
            "package_item_count": len(item_by_id), "preview_used_item_count": len(used),
            "preview_source_ref_count": len(preview_refs), "persisted_source_ref_count": len(report_refs),
            "commit_observed": bool(commit), "reference_error": run.get("reference_error"),
            "preview_error": run.get("preview_error"), "issues": preview.get("issues", []),
            "review_status_code": (run.get("review_result") or {}).get("status_code"),
            "export_status_code": (run.get("export_result") or {}).get("status_code"),
            "preview_export_status_code": (run.get("preview_export") or {}).get("status_code"),
            "model_preview_status_code": (run.get("model_preview") or {}).get("status_code"),
            "model_preview_elapsed_ms": (run.get("model_preview") or {}).get("elapsed_ms"),
            "integrity_errors": errors,
        }
        result["preregistered_technical_checks"] = _case_checks(run, package, preview)
        run_results.append(result)
        comparisons[scenario_id].append({
            "run_id": run_id, "variant": variant, "mode": run.get("mode"),
            "fact_signature": _digest(package.get("facts", [])) if package else None,
            "paragraph_signature": _digest(_semantic_paragraphs(preview.get("paragraphs", []))) if preview else None,
            "issue_signature": _digest(preview.get("issues", [])) if preview else None,
        })

    matrix = []
    for number in range(1, 31):
        entry = by_number[number]
        for key in ("package_record_ids", "persisted_record_ids", "rejected_record_ids",
                    "artifact_ids", "persisted_sections"):
            entry[key].sort()
        if entry["report_source_ref"]:
            entry["observed_use"] = "persisted_report_reference"
        elif entry["preview_used"] or entry["preview_source_ref"]:
            entry["observed_use"] = "candidate_only"
        elif entry["rejected"]:
            entry["observed_use"] = "rejected_or_guarded"
        elif entry["package_visible"]:
            entry["observed_use"] = "package_visible_only"
        else:
            entry["observed_use"] = "not_run_or_not_selected"
        entry["necessity_conclusion"] = "unproven_without_controlled_ablation_and_human_review"
        matrix.append(entry)

    variant_comparisons = []
    for scenario_id, group in comparisons.items():
        for left_index in range(len(group)):
            for right in group[left_index + 1:]:
                left = group[left_index]
                same_controls = (left["fact_signature"] is not None
                                 and left["fact_signature"] == right["fact_signature"]
                                 and left["mode"] == right["mode"])
                variant_comparisons.append({
                    "scenario_id": scenario_id, "left_run_id": left["run_id"],
                    "right_run_id": right["run_id"], "same_facts_and_mode": same_controls,
                    "same_candidate": left["paragraph_signature"] == right["paragraph_signature"] if same_controls else None,
                    "same_issues": left["issue_signature"] == right["issue_signature"] if same_controls else None,
                    "interpretation": "descriptive_only; implementation failure and model variance are not evidence of information necessity",
                })

    return {
        "schema_version": "writing-support-summary/1.0", "source": "captured_actual_api_bodies",
        "run_count": len(run_results), "runs": run_results,
        "categories": matrix, "variant_comparisons": variant_comparisons,
        "limitations": [
            "API source_refs and used_items prove observable consumption, not semantic correctness.",
            "A category with no observed use is not proven unnecessary; single-corpus results do not generalize across report types.",
            "Final necessity classes require controlled ablation and independent human review.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path, help="JSON file containing unchanged writing API responses")
    parser.add_argument("--output", type=Path, help="Write summary to a JSON file instead of stdout")
    args = parser.parse_args()
    capture = json.loads(args.capture.read_text(encoding="utf-8"))
    result = summarize(capture)
    encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")


if __name__ == "__main__":
    main()
