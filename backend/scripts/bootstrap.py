"""Initialize an empty/local database without deleting existing project data."""

from __future__ import annotations

import argparse
import json

from app.corpus_import import import_builtin_corpus
from app.db import init_db
from app.demo_seed import seed_demo_documents


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化项目表并导入冻结语料；不清空既有数据")
    parser.add_argument("--with-public-reports", action="store_true", help="同时预置四份公开报告的独立演示项目")
    args = parser.parse_args()
    init_db()
    print(json.dumps(import_builtin_corpus(), ensure_ascii=False, sort_keys=True))
    if args.with_public_reports:
        for row in seed_demo_documents():
            print(f"{row['seed_state']} | {row['project']} | {row['pages']} 页 | {row['fact_state']}")


if __name__ == "__main__":
    main()
