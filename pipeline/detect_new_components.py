#!/usr/bin/env python3
"""Detect components present in cloned repos but missing from Airtable.

Creates stub records for any source file whose expected Airtable name is not
already present. Stubs include source code, a token estimate, tags derived
from the filename, and sensible defaults for the select fields so a human
reviewer can pick up from a consistent starting point.

Env:
    AIRTABLE_PAT  Personal access token with data.records:read/write scope.
"""
from __future__ import annotations

import os
import sys
import time
import json
from pathlib import Path
from urllib import request, parse, error

BASE_ID = "appbUpVCXkuPCOo6y"
TABLE_ID = "tblKkKRKRsd7IkqHm"

F_NAME = "fldYb4DpOA6hNTgYh"
F_CODE_REACT = "fldWLYeGvIbKq9yK0"
F_TOKEN_ESTIMATE = "fldCuoXQOiyWLAPVR"
F_FRAMEWORK = "fldcZoPqXudr99WYG"
F_CATEGORY = "fldq1pnoEPOF2GsLq"
F_STYLE = "fldpfRXUCyOmxcTxv"
F_TIER = "fldvxJOHaIurQrXQo"
F_PUBLISHED = "fldu6soOkw6VTD9jg"
F_SOURCE = "fldSG3VSf6J2Z1gNC"
F_SOURCE_URL = "fldAEdgO8lDRYhjoQ"
F_TAGS = "fldZNayFgYr4NmtZM"

API_ROOT = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}"

REPO_SOURCES = [
    {
        "label": "Magic UI",
        "root": Path("repos/magicui/apps/www/registry/magicui"),
        "pattern": "*.tsx",
        "url_fmt": "https://github.com/magicuidesign/magicui/blob/main/apps/www/registry/magicui/{name}",
    },
    {
        "label": "HyperUI",
        "root": Path("repos/hyperui/src/components"),
        "pattern": "*.html",
        "url_fmt": "https://github.com/markmead/hyperui/blob/main/src/components/{name}",
    },
]


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


def title_case(stem: str) -> str:
    return " ".join(w.capitalize() for w in stem.replace("_", "-").split("-") if w)


def tags_from_stem(stem: str) -> list[str]:
    return [w.lower() for w in stem.replace("_", "-").split("-") if w]


def build_stub(path: Path, library_label: str, url_fmt: str) -> dict:
    code = path.read_text(encoding="utf-8", errors="replace")
    stem = path.stem
    display_name = f"{title_case(stem)} ({library_label})"
    return {
        "fields": {
            F_NAME: display_name,
            F_CODE_REACT: code,
            F_TOKEN_ESTIMATE: max(1, len(code) // 4),
            F_TAGS: ", ".join(tags_from_stem(stem)),
            F_FRAMEWORK: ["React", "Tailwind"],
            F_CATEGORY: "Components",
            F_STYLE: "Dark UI",
            F_TIER: "Free",
            F_PUBLISHED: "Draft",
            F_SOURCE: "Scraped",
            F_SOURCE_URL: url_fmt.format(name=path.name),
        }
    }


def create_records(stubs: list[dict]) -> None:
    for i in range(0, len(stubs), 10):
        chunk = stubs[i : i + 10]
        http("POST", API_ROOT, {"records": chunk, "typecast": True})
        time.sleep(0.25)


def main() -> int:
    records = list_records()
    existing = set()
    for r in records:
        name = r.get("fields", {}).get("Name") or r.get("fields", {}).get(F_NAME)
        if name:
            existing.add(name.strip().lower())
    print(f"airtable holds {len(existing)} named records")

    stubs: list[dict] = []
    for src in REPO_SOURCES:
        root: Path = src["root"]
        if not root.exists():
            print(f"skip missing repo root: {root}")
            continue
        for path in sorted(root.rglob(src["pattern"])):
            display_name = f"{title_case(path.stem)} ({src['label']})"
            if display_name.lower() in existing:
                continue
            stubs.append(build_stub(path, src["label"], src["url_fmt"]))

    print(f"creating {len(stubs)} new stub records")
    if stubs:
        create_records(stubs)
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
