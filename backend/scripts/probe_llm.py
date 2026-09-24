"""Inspect a user-provided OpenAI-compatible endpoint without logging its key."""

from __future__ import annotations

import argparse
import getpass
import sys

import httpx


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    key = getpass.getpass("API key: ") if sys.stdin.isatty() else sys.stdin.readline().strip()
    if not key:
        raise SystemExit("Missing API key")
    base = args.base_url.rstrip("/")
    with httpx.Client(timeout=15, trust_env=False) as client:
        response = client.get(f"{base}/v1/models", headers={"Authorization": f"Bearer {key}"})
    print("status:", response.status_code)
    if response.is_success:
        payload = response.json()
        models = payload.get("data", []) if isinstance(payload, dict) else []
        print("model_ids:", [item.get("id") for item in models if isinstance(item, dict)])
    else:
        print("content_type:", response.headers.get("content-type", ""))


if __name__ == "__main__":
    main()
