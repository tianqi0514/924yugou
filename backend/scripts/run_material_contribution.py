"""Run the preregistered 30-category QA cases against the isolated test database.

This script creates a synthetic QA project. It never resets a database and refuses
to run outside report_platform_test. The JSON output is ignored local evidence;
the immutable experiment rows stay in the test database until its next reset.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import engine
from app.main import app


CASES = {
    "S4": ["S4-base", "S4-zero", "S4-missing", "S4-lower", "S4-equal"],
    "S7.1": ["S7.1-supplier"],
    "S5.2": ["S5.2-conflict"],
}


def check(response):
    if response.status_code not in {200, 201}:
        raise RuntimeError(f"实验请求失败：HTTP {response.status_code} {response.text[:300]}")
    return response.json()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("tmp/material-contribution-20260927.json"))
    args = parser.parse_args()
    if engine.url.database != "report_platform_test":
        raise SystemExit("拒绝在非 report_platform_test 数据库运行")
    with TestClient(app) as client:
        project_id = check(client.post("/api/projects", json={"name": "30类贡献评估 · 合成QA"}))["id"]
        check(client.put(f"/api/projects/{project_id}/writing/reference", json={
            "corpus_id": "cy_tray_20260918", "corpus_version": "v2",
            "target_report_type": "feasibility", "accepted": True,
        }))
        runs = []
        for section_id, case_ids in CASES.items():
            for case_id in case_ids:
                run = check(client.post(
                    f"/api/projects/{project_id}/writing/materials/{section_id}/evaluate",
                    json={"case_id": case_id},
                ))
                if run["result"]["category_count"] != 30:
                    raise RuntimeError(f"{case_id} 没有生成 30 行")
                runs.append(run)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"project_id": project_id, "runs": runs},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
    for case in runs:
        rows = case["result"]["categories"]
        consumed = [row["category_id"] for row in rows if row["candidate_record_ids"]]
        equivalent = [row["category_id"] for row in rows
                      if row["equivalent"]["status"] == "equivalent"]
        print(f"{case['case_id']}: {len(rows)} 类；候选引用 {','.join(consumed) or '无'}；"
              f"等价通过 {','.join(equivalent) or '无'}；候选 {case['result']['configured']['status']}")
    print(f"记录已保存到测试库；JSON：{args.output}")


if __name__ == "__main__":
    main()
