#!/usr/bin/env python3
"""Detect components present in cloned repos but missing from Airtable.

Creates stub records for any source file whose expected Airtable name isn't
already present, and writes a templated Prompt Text at the same time (matching
21st.dev's 'Copy prompt' format, minus the bits we can't derive — no demo.tsx,
no tailwind config, no registry deps — just the main file + imported NPM deps).

With --update-prompts, also rewrites Prompt Text on already-existing
magicui / hyperui records so they use the same template as the 21st.dev ones.

Env:
    AIRTABLE_PAT  Personal access token with data.records:read/write scope.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from urllib import error, parse, request

from prompt_template import build_prompt_text, extract_npm_deps

BASE_ID = "appbUpVCXkuPCOo6y"
TABLE_ID = "tblKkKRKRsd7IkqHm"

F_NAME = "fldYb4DpOA6hNTgYh"
F_PROMPT_TEXT = "fldJfx71qA76Z6bJE"
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
        qs_pairs = [("pageSize", "100")]
        if offset:
            qs_pairs.append(("offset", offset))
        url = f"{API_ROOT}?{parse.urlencode(qs_pairs)}"
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


def prompt_for(filename: str, code: str) -> str:
    return build_prompt_text(
        main_filename=filename,
        main_code=code,
        npm_deps=extract_npm_deps(code),
    )


def build_stub(path: Path, library_label: str, url_fmt: str) -> dict:
    code = path.read_text(encoding="utf-8", errors="replace")
    stem = path.stem
    display_name = f"{title_case(stem)} ({library_label})"
    prompt_text = prompt_for(path.name, code)
    return {
        "fields": {
            F_NAME: display_name,
            F_CODE_REACT: code,
            F_PROMPT_TEXT: prompt_text,
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


def patch_records(updates: list[dict]) -> tuple[int, int]:
    ok = fail = 0
    for i in range(0, len(updates), 10):
        chunk = updates[i : i + 10]
        try:
            payload = http("PATCH", API_ROOT, {"records": chunk, "typecast": True})
            ok += len(payload.get("records", []))
        except Exception as e:
            fail += len(chunk)
            print(f"patch failed: {e}", file=sys.stderr)
        time.sleep(0.25)
    return ok, fail


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--update-prompts",
        action="store_true",
        help="also rewrite Prompt Text on existing non-21st.dev records using the template",
    )
    args = ap.parse_args(argv)

    records = list_records()
    by_name: dict[str, dict] = {}
    for r in records:
        fields = r.get("fields", {})
        name = (fields.get("Name") or fields.get(F_NAME) or "").strip().lower()
        if name:
            by_name[name] = r
    print(f"airtable holds {len(by_name)} named records", flush=True)

    stubs: list[dict] = []
    updates: list[dict] = []

    for src in REPO_SOURCES:
        root: Path = src["root"]
        if not root.exists():
            print(f"skip missing repo root: {root}")
            continue
        for path in sorted(root.rglob(src["pattern"])):
            display_name = f"{title_case(path.stem)} ({src['label']})"
            existing = by_name.get(display_name.lower())
            if existing is None:
                stubs.append(build_stub(path, src["label"], src["url_fmt"]))
                continue
            if not args.update_prompts:
                continue
            code = path.read_text(encoding="utf-8", errors="replace")
            prompt_text = prompt_for(path.name, code)
            updates.append(
                {"id": existing["id"], "fields": {F_PROMPT_TEXT: prompt_text}}
            )

    print(f"creating {len(stubs)} new stub records", flush=True)
    if stubs:
        create_records(stubs)

    if updates:
        print(f"patching Prompt Text on {len(updates)} existing records", flush=True)
        ok, fail = patch_records(updates)
        print(f"  patched={ok} failed={fail}")

    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
