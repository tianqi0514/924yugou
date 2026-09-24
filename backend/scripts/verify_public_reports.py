"""Reproduce the Beijing holdout document-layer check without touching app data."""

from __future__ import annotations

import json
from pathlib import Path

from app.pdf_extract import extract_pdf


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "test-fixtures" / "public-reports"
OUTPUT = FIXTURES / "baseline-extraction"


def compact(value: str | None) -> str:
    return "".join((value or "").split())


def evaluate(bundle: dict, gold: dict) -> list[dict]:
    by_page = {page["pdf_page"]: page for page in bundle["pages"]}
    checks = []
    for anchor in gold["text_anchors"]:
        page = by_page[anchor["pdf_page"]]
        found = compact(anchor["text"]) in compact(page["text"])
        blocks = [block["block_id"] for block in page["blocks"] if compact(anchor["text"]) in compact(block["text"])]
        checks.append({"id": anchor["id"], "kind": "page_text", "passed": found, "pdf_page": anchor["pdf_page"], "block_ids": blocks})
    for anchor in gold["heading_anchors"]:
        page = by_page[anchor["pdf_page"]]
        matches = [item for item in page["heading_candidates"] if compact(item["text"]) == compact(anchor["text"])]
        checks.append({"id": anchor["id"], "kind": "heading_candidate", "passed": bool(matches), "pdf_page": anchor["pdf_page"], "block_ids": [item["block_id"] for item in matches]})
    for anchor in gold["table_anchors"]:
        page = by_page[anchor["pdf_page"]]
        by_table = {table["table_id"]: table for table in page["table_candidates"]}
        matches = []
        for candidate in page["table_value_candidates"]:
            if compact(by_table[candidate["table_id"]]["caption"]) != compact(anchor["caption"]):
                continue
            if all(compact(candidate[key]) == compact(anchor[key]) for key in ("label", "value", "unit")):
                matches.append(candidate)
        checks.append({"id": anchor["id"], "kind": "table_value_candidate", "passed": bool(matches), "pdf_page": anchor["pdf_page"], "matches": matches})
    return checks


def main() -> None:
    gold = json.loads((FIXTURES / "beijing-gold.json").read_text(encoding="utf-8"))
    source = FIXTURES / gold["source_file"]
    bundle = extract_pdf(source)
    if bundle["sha256"] != gold["sha256"]:
        raise ValueError("北京样本摘要与人工标注版本不一致")
    checks = evaluate(bundle, gold)
    cross_type = []
    audit_page_2_status = None
    for filename in ("02_jiangmen_eia.pdf", "03_taizhou_audit.pdf", "04_jinjiang_accident.pdf"):
        other = extract_pdf(FIXTURES / filename)
        pages = other["pages"]
        cross_type.append({
            "source_file": filename,
            "pdf_pages": other["pdf_pages"],
            "native_text_pages": sum(page["text_status"] == "native_text" for page in pages),
            "ocr_required_pages": [page["pdf_page"] for page in pages if page["text_status"] == "ocr_required"],
            "heading_candidates": sum(len(page["heading_candidates"]) for page in pages),
            "table_candidates": sum(len(page["table_candidates"]) for page in pages),
            "table_value_candidates": sum(len(page["table_value_candidates"]) for page in pages),
        })
        if filename == "03_taizhou_audit.pdf":
            audit_page_2_status = pages[1]["text_status"]
    summary = {
        "source_file": bundle["source_file"],
        "sha256": bundle["sha256"],
        "pdf_pages": bundle["pdf_pages"],
        "native_text_pages": sum(page["text_status"] == "native_text" for page in bundle["pages"]),
        "ocr_required_pages": [page["pdf_page"] for page in bundle["pages"] if page["text_status"] == "ocr_required"],
        "heading_candidates": sum(len(page["heading_candidates"]) for page in bundle["pages"]),
        "table_candidates": sum(len(page["table_candidates"]) for page in bundle["pages"]),
        "table_value_candidates": sum(len(page["table_value_candidates"]) for page in bundle["pages"]),
        "table_pages": sum(bool(page["table_candidates"]) for page in bundle["pages"]),
        "contents_pages": [page["pdf_page"] for page in bundle["pages"] if page["is_contents_candidate"]],
        "gold_passed": sum(check["passed"] for check in checks),
        "gold_total": len(checks),
        "cross_type": cross_type,
        "audit_page_2_text_status": audit_page_2_status,
        "checks": checks,
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with (OUTPUT / "beijing-pages.jsonl").open("w", encoding="utf-8") as stream:
        for page in bundle["pages"]:
            stream.write(json.dumps(page, ensure_ascii=False) + "\n")
    (OUTPUT / "verification.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "checks"}, ensure_ascii=False, indent=2))
    if summary["gold_passed"] != summary["gold_total"] or audit_page_2_status != "ocr_required":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
