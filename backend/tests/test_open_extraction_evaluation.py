"""固定样本评分与人工复核完整性检查。"""

from __future__ import annotations

import json
import runpy
from copy import deepcopy
from pathlib import Path

import pytest

SCRIPT = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts" / "evaluate_open_extraction.py"))
REVIEW, SNAPSHOT, evaluate = SCRIPT["REVIEW"], SCRIPT["SNAPSHOT"], SCRIPT["evaluate"]


def test_open_extraction_review_is_complete_and_reproducible() -> None:
    """锁定首次抽取与累计候选的评分，并拒绝未审核候选。"""
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    review = json.loads(REVIEW.read_text(encoding="utf-8"))
    result = evaluate(snapshot, review)["total"]
    assert (result["gold_total"], result["value_hits"], result["direct_hits"]) == (40, 29, 25)
    assert (result["first_pass"]["candidate_total"], result["first_pass"]["value_hits"], result["first_pass"]["direct_hits"]) == (53, 27, 24)
    assert sum(result["review_counts"].values()) == result["candidate_total"] == 59

    incomplete = deepcopy(review)
    incomplete["documents"][0]["candidate_reviews"]["7"]["usable"].remove(1)
    with pytest.raises(ValueError, match="候选尚未逐条复核"):
        evaluate(snapshot, incomplete)
