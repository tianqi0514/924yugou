"""Validate fixed files and internal consistency of the manual holdout review."""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app.document_pipeline import source_supports  # noqa: E402


def read(name: str) -> dict:
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def main() -> None:
    gold_path = HERE / "preregistered-gold.json"
    gold_hash = hashlib.sha256(gold_path.read_bytes()).hexdigest()
    assert gold_hash == "e87495704d157cf383dd98af12420e7df57a8c766d1f24b5e0f1a68ff27811cf"
    gold = read("preregistered-gold.json")
    review = read("manual-review.json")
    runs = {
        "04_jinjiang_accident.pdf#page=6": read("run-jinjiang-p6.json"),
        "03_taizhou_audit.pdf#page=3": read("run-taizhou-p3.json"),
    }
    expected_hashes = {
        "04_jinjiang_accident.pdf": "eb6655da71f0d11b8a41c3bc5320a53fd4307fec370018150a037978927d69c4",
        "03_taizhou_audit.pdf": "9e56c7676016096282283718ffccd358f0b4bfde461ad17f77c9259a2c1688a8",
    }
    all_gold = {item["id"]: item for page in gold["pages"] for item in page["gold"]}
    all_reviews = {item["id"]: item for item in review["gold_review"]}
    assert len(all_gold) == 14 and set(all_gold) == set(all_reviews)
    candidate_count = 0
    direct_count = 0
    lexical_true = 0
    for key, run in runs.items():
        path = HERE.parent / run["file"]
        assert run["source_sha256"] == expected_hashes[run["file"]] == hashlib.sha256(path.read_bytes()).hexdigest()
        assert run["preregistered_gold_sha256"] == gold_hash
        assert run["model_call"]["model"] == "deepseek-v4-flash-0731"
        assert run["model_call"]["temperature"] == 0
        assert len(run["candidates"]) == len(review["candidate_review"][key])
        refs = {segment["ref"]: segment for segment in run["segments"]}
        for index, (candidate, item_review) in enumerate(zip(run["candidates"], review["candidate_review"][key]), 1):
            assert item_review["index"] == index
            assert candidate["source_ref"] in refs
            assert candidate["source_valid"] == source_supports(candidate["value_text"], refs[candidate["source_ref"]]["text"])
            assert item_review["status"] in {"direct", "needs_edit", "missing_context"}
            candidate_count += 1
            direct_count += item_review["status"] == "direct"
            lexical_true += candidate["source_valid"]
    assert candidate_count == 30 and direct_count == 22 and lexical_true == 29
    for item in review["gold_review"]:
        assert item["status"] in {"hit", "partial", "miss"}
        if item["candidate_index"] is None:
            assert item["status"] == "miss" and not item["same_segment_direct"]
            continue
        page = next(page for page in gold["pages"] if item["id"] in {g["id"] for g in page["gold"]})
        key = f"{page['file']}#page={page['physical_page']}"
        run = runs[key]
        candidate = run["candidates"][item["candidate_index"] - 1]
        assert candidate["source_ref"] in {segment["ref"] for segment in run["segments"]}
        assert item["same_segment_direct"]
    counts = Counter(item["status"] for item in review["gold_review"])
    assert counts == {"hit": 9, "partial": 1, "miss": 4}
    print("gold: 14 preregistered, 9 value/field hits, 1 partial, 4 misses")
    print("candidates: 30 reviewed, 22 directly source-supported, 8 need edit/context")
    print("lexical gate: 29/30 source_valid; direct-source check is a different standard")
    print("PDF hashes, gold hash, reference IDs and review coverage: OK")


if __name__ == "__main__":
    main()
