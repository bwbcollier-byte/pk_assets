#!/usr/bin/env python3
"""Push source code from cloned component repos into Airtable.

Reads .tsx files from repos/<library>/..., matches filenames to Airtable
records by kebab-case -> "Title Case (Library Name)" convention, and writes
the source to the Code React field plus a Token Estimate (chars / 4).

Records that already have Code React populated are skipped.

Env:
    AIRTABLE_PAT  Personal access token with data.records:read/write scope.
"""
from __future__ import annotations

import os
import sys
import time
import json
from pathlib import Path
from typing import Iterable
from urllib import request, parse, error

BASE_ID = "appbUpVCXkuPCOo6y"
TABLE_ID = "tblKkKRKRsd7IkqHm"

F_NAME = "fldYb4DpOA6hNTgYh"
F_CODE_REACT = "fldWLYeGvIbKq9yK0"
F_TOKEN_ESTIMATE = "fldCuoXQOiyWLAPVR"

API_ROOT = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}"

# Repos to scan. (library_label, glob_root, glob_pattern)
REPO_SOURCES = [
    ("Magic UI", "repos/magicui/apps/www/registry/magicui", "*.tsx"),
    ("HyperUI", "repos/hyperui/src/components", "*.html"),
]

# Special-case filename overrides: Airtable record name -> filename stem.
NAME_OVERRIDES = {
    "Safari Browser": "safari",
    "Safari Mockup": "safari",
    "iPhone 15 Pro": "iphone",
    "Animated Circular Progress": "animated-circular-progress-bar",
}


def pat() -> str:
    token = os.environ.get("AIRTABLE_PAT")
    if not token:
        sys.exit("AIRTABLE_PAT is not set")
    return token


def http(method: str, url: str, body: dict | None = None) -> dict:
    data = None
    headers = {
        "Authorization": f"Bearer {pat()}",
        "Content-Type": "application/json",
    }
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = request.Request(url, data=data, method=method, headers=headers)
    for attempt in range(5):
        try:
            with request.urlopen(req) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError(f"gave up after retries: {method} {url}")


def list_records() -> list[dict]:
    records: list[dict] = []
    offset: str | None = None
    while True:
        qs = {"pageSize": "100"}
        if offset:
            qs["offset"] = offset
        url = f"{API_ROOT}?{parse.urlencode(qs)}"
        payload = http("GET", url)
        records.extend(payload.get("records", []))
        offset = payload.get("offset")
        if not offset:
            break
        time.sleep(0.2)
    return records


def kebab(name: str) -> str:
    base = name.split("(")[0].strip()
    out = []
    for ch in base:
        if ch.isalnum():
            out.append(ch.lower())
        elif ch in " -_":
            out.append("-")
    slug = "".join(out)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")


def candidate_stems(record_name: str) -> list[str]:
    if record_name in NAME_OVERRIDES:
        return [NAME_OVERRIDES[record_name]]
    return [kebab(record_name)]


def library_from_name(name: str) -> str | None:
    if "(" in name and ")" in name:
        return name[name.rfind("(") + 1 : name.rfind(")")].strip()
    return None


def iter_source_files() -> dict[str, list[Path]]:
    out: dict[str, list[Path]] = {}
    for label, root, pattern in REPO_SOURCES:
        root_path = Path(root)
        if not root_path.exists():
            print(f"skip missing source root: {root_path}", file=sys.stderr)
            continue
        out[label] = sorted(root_path.rglob(pattern))
    return out


def batched(items: Iterable[dict], n: int) -> Iterable[list[dict]]:
    batch: list[dict] = []
    for it in items:
        batch.append(it)
        if len(batch) == n:
            yield batch
            batch = []
    if batch:
        yield batch


def patch_records(updates: list[dict]) -> None:
    for chunk in batched(updates, 10):
        http("PATCH", API_ROOT, {"records": chunk, "typecast": True})
        time.sleep(0.25)


def main() -> int:
    sources = iter_source_files()
    if not sources:
        print("no source repos present on disk")
        return 0

    records = list_records()
    print(f"loaded {len(records)} airtable records")

    updates: list[dict] = []
    for rec in records:
        fields = rec.get("fields", {})
        name = fields.get("Name") or fields.get(F_NAME)
        if not name:
            continue
        if (fields.get("Code React") or fields.get(F_CODE_REACT)):
            continue
        lib = library_from_name(name)
        if not lib or lib not in sources:
            continue
        stems = candidate_stems(name)
        match: Path | None = None
        for path in sources[lib]:
            if path.stem in stems:
                match = path
                break
        if not match:
            continue
        code = match.read_text(encoding="utf-8", errors="replace")
        updates.append(
            {
                "id": rec["id"],
                "fields": {
                    F_CODE_REACT: code,
                    F_TOKEN_ESTIMATE: max(1, len(code) // 4),
                },
            }
        )

    print(f"pushing code for {len(updates)} records")
    if updates:
        patch_records(updates)
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
