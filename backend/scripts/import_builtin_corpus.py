"""Run the frozen-corpus schema migration and transactional import.

Usage: PYTHONPATH=backend .venv/bin/python backend/scripts/import_builtin_corpus.py
"""

from __future__ import annotations

import json
import argparse

from app.corpus_import import import_builtin_corpus


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Verify and import the bundled frozen corpus into PostgreSQL")
    parser.add_argument("--rebuild", action="store_true", help="Atomically rebuild DB projections from the same verified archive")
    args = parser.parse_args()
    print(json.dumps(import_builtin_corpus(rebuild=args.rebuild), ensure_ascii=False, sort_keys=True))
