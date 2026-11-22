#!/usr/bin/env python3
"""
Bulk-submit all manuscripts from manifest.csv.

Usage:
    python submit_all.py [--api http://localhost:8000] [--concurrency 5]

Logs a summary line per manuscript and prints totals at the end.
Skipped rows (missing file, bad data) are reported but do not abort the run.
"""
import argparse
import asyncio
import csv
import sys
from pathlib import Path

try:
    import httpx
except ImportError:
    sys.exit("httpx is required: pip install httpx")

def find_manifest() -> Path:
    for p in [
        Path(__file__).parent / "manifest.csv",
        Path(__file__).parent.parent / "Assignment_for_TL" / "manifest.csv",
    ]:
        if p.exists():
            return p.resolve()
    sys.exit("manifest.csv not found. Expected at the repo root alongside this script.")


def find_manuscripts_dir() -> Path:
    p = Path(__file__).parent / "manuscripts"
    if not p.is_dir():
        sys.exit("manuscripts/ directory not found next to this script.")
    return p.resolve()


async def login(client: httpx.AsyncClient, api: str) -> str:
    resp = await client.post(
        f"{api}/auth/login",
        json={"email": "author@example.com", "password": "password123"},
    )
    if resp.status_code != 200:
        sys.exit(f"Login failed ({resp.status_code}): {resp.text}")
    return resp.json()["access_token"]


async def submit_one(
    sem: asyncio.Semaphore,
    client: httpx.AsyncClient,
    api: str,
    token: str,
    manuscripts_dir: Path,
    row: dict,
) -> dict:
    file_name = row.get("file_name", "").strip()
    title = row.get("title", "").strip()
    author_email = row.get("author_email", "").strip()
    priority = row.get("priority", "normal").strip()

    file_path = manuscripts_dir / file_name

    if not file_name:
        return {"file": file_name, "status": "skipped", "reason": "empty file_name"}
    if not file_path.exists():
        return {"file": file_name, "status": "skipped", "reason": "file not found"}

    async with sem:
        try:
            with open(file_path, "rb") as fh:
                resp = await client.post(
                    f"{api}/manuscripts",
                    headers={"Authorization": f"Bearer {token}"},
                    data={"title": title, "author_email": author_email, "priority": priority},
                    files={"file": (file_name, fh, "text/plain")},
                    timeout=30,
                )
            if resp.status_code in (200, 202):
                body = resp.json()
                return {"file": file_name, "status": "ok", "id": body.get("id")}
            else:
                return {
                    "file": file_name,
                    "status": "error",
                    "reason": f"HTTP {resp.status_code}: {resp.text[:120]}",
                }
        except Exception as exc:
            return {"file": file_name, "status": "error", "reason": str(exc)}


async def main(api: str, concurrency: int) -> None:
    manifest_path = find_manifest()
    manuscripts_dir = find_manuscripts_dir()

    with open(manifest_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    print(f"Manifest: {manifest_path}  ({len(rows)} rows)")
    print(f"API:      {api}")
    print(f"Workers:  {concurrency}\n")

    async with httpx.AsyncClient() as client:
        token = await login(client, api)
        print("Logged in as author@example.com\n")

        sem = asyncio.Semaphore(concurrency)
        tasks = [
            submit_one(sem, client, api, token, manuscripts_dir, row)
            for row in rows
        ]
        results = await asyncio.gather(*tasks)

    ok = [r for r in results if r["status"] == "ok"]
    skipped = [r for r in results if r["status"] == "skipped"]
    errors = [r for r in results if r["status"] == "error"]

    col_w = max((len(r.get("file", "")) for r in results), default=20) + 2

    for r in results:
        icon = {"ok": "OK", "skipped": "--", "error": "!!"}[r["status"]]
        detail = r.get("id") or r.get("reason", "")
        print(f"  {icon}  {r['file']:<{col_w}} {detail}")

    print(f"\n{'-'*60}")
    print(f"  Submitted : {len(ok)}")
    print(f"  Skipped   : {len(skipped)}")
    print(f"  Errors    : {len(errors)}")

    if skipped:
        print("\nSkipped:")
        for r in skipped:
            print(f"  {r['file']}: {r['reason']}")
    if errors:
        print("\nErrors:")
        for r in errors:
            print(f"  {r['file']}: {r['reason']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bulk-submit manuscripts from manifest.csv")
    parser.add_argument("--api", default="http://localhost:8000", help="API base URL")
    parser.add_argument("--concurrency", type=int, default=5, help="Parallel submissions")
    args = parser.parse_args()

    asyncio.run(main(args.api, args.concurrency))
