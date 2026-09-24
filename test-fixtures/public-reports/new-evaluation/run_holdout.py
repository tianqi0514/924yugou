"""One-shot, database-free evaluation snapshot for two preregistered PDF pages.

Run from the report-platform root with .venv/bin/python. The script reads the
current encrypted model configuration, never serializes credentials, and refuses
to overwrite a prior model response.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.document_pipeline import model_candidates, parse_original, table_segments  # noqa: E402
from app.model_settings import parse_document_page  # noqa: E402


def snapshot(name: str, content: dict) -> None:
    path = HERE / name
    if path.exists():
        raise SystemExit(f"Existing snapshot kept: {path}")
    path.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def metadata(source: Path, page: int, segments: list[dict], candidates: list[dict]) -> dict:
    return {
        "file": source.name,
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "physical_page": page,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "segments": segments,
        "model_call": getattr(candidates, "model_call", {}),
        "candidates": list(candidates),
    }


def main() -> None:
    if (HERE / "run-jinjiang-p6.json").exists() or (HERE / "run-taizhou-p3.json").exists():
        raise SystemExit("At least one response snapshot exists; no repeat model call is allowed.")
    gold = HERE / "preregistered-gold.json"
    gold_sha = hashlib.sha256(gold.read_bytes()).hexdigest()
    if gold_sha != "e87495704d157cf383dd98af12420e7df57a8c766d1f24b5e0f1a68ff27811cf":
        raise SystemExit("Preregistered gold changed; stop before model call.")
    sources = HERE.parent

    jinjiang = sources / "04_jinjiang_accident.pdf"
    source = jinjiang.read_bytes()
    _, parsed, status = parse_original(source, "pdf")
    segments = [item for item in parsed if item["page"] == 6] + table_segments(source, 6)
    if not segments:
        raise SystemExit("Jinjiang page 6 has no text segments.")
    print(f"Jinjiang page 6: {len(segments)} source segments; invoking extraction once.", flush=True)
    candidates = model_candidates(segments)
    record = metadata(jinjiang, 6, segments, candidates)
    record.update({"parse_status": status, "preregistered_gold_sha256": gold_sha})
    snapshot("run-jinjiang-p6.json", record)
    print(f"Jinjiang candidates: {len(candidates)}; snapshot saved.", flush=True)

    taizhou = sources / "03_taizhou_audit.pdf"
    source = taizhou.read_bytes()
    _, parsed, status = parse_original(source, "pdf")
    if any(item["page"] == 3 for item in parsed):
        raise SystemExit("Taizhou page 3 unexpectedly has a text layer; stop.")
    print("Taizhou page 3: OCR required; invoking configured OCR once.", flush=True)
    ocr = parse_document_page(source, 3)
    segments = [{"ref": "p3-ocr1", "page": 3, "kind": "ocr", "review_status": "PENDING_REVIEW",
                 "locator": "PDF 物理第 3 页 · OCR 待校对", "text": ocr["text"]}]
    print(f"Taizhou OCR chars: {len(ocr['text'])}; invoking extraction once.", flush=True)
    candidates = model_candidates(segments)
    record = metadata(taizhou, 3, segments, candidates)
    record.update({"parse_status": status, "ocr": {key: value for key, value in ocr.items() if key != "text"},
                   "preregistered_gold_sha256": gold_sha})
    snapshot("run-taizhou-p3.json", record)
    print(f"Taizhou candidates: {len(candidates)}; snapshot saved.", flush=True)


if __name__ == "__main__":
    main()
