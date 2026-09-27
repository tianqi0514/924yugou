"""Unchanged result references can follow a new run without hiding changed lineage."""

import os
from copy import deepcopy
from types import SimpleNamespace

os.environ["DATABASE_URL"] = "postgresql+psycopg://report:local_development_only@127.0.0.1:55432/report_platform_test"

from app.analysis import _run_impacts, _safe_rebind_content
from app.db import engine


def _run(run_id, *, land="40", above="20", land_input="50", land_revision=1, condition=True):
    assert engine.url.database == "report_platform_test"
    results = {
        "land_input": {"key": "land_input", "value": land_input, "unit": "㎡", "status": "PROVIDED", "origin": "project_fact", "source_ref": None},
        "land_limit": {"key": "land_limit", "value": "40", "unit": "㎡", "status": "PROVIDED", "origin": "project_fact", "source_ref": None},
        "land": {"key": "land", "value": land, "unit": "㎡", "status": "COMPUTED", "origin": "computed", "source_ref": None},
        "above": {"key": "above", "value": above, "unit": "㎡", "status": "PROVIDED", "origin": "scenario_assumption", "source_ref": None},
    }
    return SimpleNamespace(id=run_id, snapshot={
        "blueprint_version": "config:area", "configuration": {"checksum": "same"},
        "corpus_id": None, "corpus_version": None,
        "definitions": [{"key": key, "unit": "㎡"} for key in results],
        "results": results,
        "inputs": {"land_input": {"value": land_input, "origin": "project_fact", "source_ref": None},
                   "land_limit": {"value": "40", "origin": "project_fact", "source_ref": None},
                   "above": {"value": above, "origin": "scenario_assumption", "source_ref": None}},
        "trace": [{"rule_id": "land-total", "rule_version": "v1", "expression": "min(land_input, land_limit)",
                   "target": "land", "inputs": {"land_input": land_input, "land_limit": "40"}, "result": land,
                   "status": "COMPUTED", "missing": [], "source_ref": None}],
        "project_fact_baseline": {"land_input": {"revision": land_revision, "value": land_input, "unit": "㎡"},
                                  "land_limit": {"revision": 1, "value": "40", "unit": "㎡"}},
        "condition_results": {"land:equal": {"text": "一致" if condition else "不一致", "outcome": condition}},
    })


def _report():
    return SimpleNamespace(content=[
        {"id": "a", "type": "p", "origin": "guided", "section_id": "area",
         "children": [{"text": "建筑面积20㎡"}],
         "analysis_refs": [{"run_id": "old", "result_key": "above", "value": "20", "unit": "㎡"}]},
        {"id": "l", "type": "p", "origin": "guided", "section_id": "land",
         "children": [{"text": "用地40㎡"}],
         "analysis_refs": [{"run_id": "old", "result_key": "land", "value": "40", "unit": "㎡"}]},
        {"id": "m", "type": "p", "origin": "model", "section_id": "land",
         "children": [{"text": "模型判断用地40㎡"}],
         "analysis_refs": [{"run_id": "old", "result_key": "land", "value": "40", "unit": "㎡"}]},
    ])


def test_only_unchanged_guided_lineage_is_rebound():
    old = _run("old")
    new = _run("new", above="15")
    report = _report()
    original = deepcopy(report.content)
    content, positions = _safe_rebind_content(report, old, new)
    assert positions == [2]
    assert content[1]["analysis_refs"][0]["run_id"] == "new"
    assert content[0]["analysis_refs"][0]["run_id"] == "old"
    assert content[2]["analysis_refs"][0]["run_id"] == "old"
    assert report.content == original
    assert {item["position"] for item in _run_impacts(report, new, content)} == {1, 3}


def test_same_value_does_not_hide_changed_input_revision_or_condition():
    report = _report()
    old = _run("old")
    for changed in (
        _run("new", land_input="60", land="40"),
        _run("new", land_revision=2),
        _run("new", condition=False),
    ):
        content, positions = _safe_rebind_content(report, old, changed)
        assert 2 not in positions
        assert content[1]["analysis_refs"][0]["run_id"] == "old"
