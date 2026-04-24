#!/usr/bin/env python3
"""Generate Description, Prompt Text, and Code HTML for records with source code.

Modes:
    realtime              send each record to Claude Haiku synchronously (~$0.006/record)
    batch                 write a JSONL file for the Message Batches API (50% cheaper)
    apply <results.jsonl> parse a completed batch result file and write to Airtable

Selection filter: Code React populated (LEN > 50) AND Description short
(LEN < 100) OR Prompt Text empty.

Env:
    AIRTABLE_PAT        personal access token for Airtable
    ANTHROPIC_API_KEY   key for Claude Haiku (realtime mode only)
"""
from __future__ import annotations

import os
import sys
import json
import time
from pathlib import Path
from urllib import request, parse, error

BASE_ID = "appbUpVCXkuPCOo6y"
TABLE_ID = "tblKkKRKRsd7IkqHm"

F_NAME = "fldYb4DpOA6hNTgYh"
F_DESCRIPTION = "fld5YVhf3YEgvyo2j"
F_PROMPT_TEXT = "fldJfx71qA76Z6bJE"
F_CODE_REACT = "fldWLYeGvIbKq9yK0"
F_CODE_HTML = "fldMfa1ePnUo2panh"

API_ROOT = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}"

MODEL = "claude-haiku-4-5-20251001"

SYSTEM_PROMPT = """You generate marketplace assets for a UI component library.

Given a component's source code, return a JSON object with exactly these keys:

  "description"  — 2–3 sentence marketplace copy describing what the component
                   does and where a buyer would use it. Do NOT mention the
                   source library by name.
  "prompt_text"  — A detailed prompt a developer could hand to an LLM to
                   reproduce the component. Include the props interface, key
                   Tailwind CSS classes, any animation keyframes, and the
                   behavior expected. This is the actual product — be specific.
  "code_html"    — A standalone HTML version styled with Tailwind CDN that
                   visually matches the original. Include a <style> block for
                   any custom keyframes or effects. No <html> or <body> wrapper
                   is required — just the component fragment and its styles.

Respond with raw JSON only, no prose, no markdown fences."""


def pat() -> str:
    token = os.environ.get("AIRTABLE_PAT")
    if not token:
        sys.exit("AIRTABLE_PAT is not set")
    return token


def anthropic_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("ANTHROPIC_API_KEY is not set")
    return key


def http(method: str, url: str, headers: dict, body: dict | None = None) -> dict:
    data = None
    h = {"Content-Type": "application/json", **headers}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = request.Request(url, data=data, method=method, headers=h)
    for attempt in range(5):
        try:
            with request.urlopen(req) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as e:
            if e.code in (429, 529):
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError(f"gave up after retries: {method} {url}")


def airtable_headers() -> dict:
    return {"Authorization": f"Bearer {pat()}"}


def list_candidates() -> list[dict]:
    formula = (
        "AND("
        "LEN({Code React})>50,"
        "OR(LEN({Description})<100,LEN({Prompt Text})=0)"
        ")"
    )
    records: list[dict] = []
    offset: str | None = None
    while True:
        qs = {"pageSize": "100", "filterByFormula": formula}
        if offset:
            qs["offset"] = offset
        url = f"{API_ROOT}?{parse.urlencode(qs)}"
        payload = http("GET", url, airtable_headers())
        records.extend(payload.get("records", []))
        offset = payload.get("offset")
        if not offset:
            break
        time.sleep(0.2)
    return records


def call_haiku(code: str, name: str) -> dict:
    body = {
        "model": MODEL,
        "max_tokens": 2000,
        "system": SYSTEM_PROMPT,
        "messages": [
            {
                "role": "user",
                "content": f"Component name: {name}\n\nSource code:\n```\n{code}\n```",
            }
        ],
    }
    payload = http(
        "POST",
        "https://api.anthropic.com/v1/messages",
        {
            "x-api-key": anthropic_key(),
            "anthropic-version": "2023-06-01",
        },
        body,
    )
    text = "".join(b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text")
    return json.loads(text)


def update_record(record_id: str, parsed: dict) -> None:
    fields = {
        F_DESCRIPTION: parsed.get("description", ""),
        F_PROMPT_TEXT: parsed.get("prompt_text", ""),
        F_CODE_HTML: parsed.get("code_html", ""),
    }
    http(
        "PATCH",
        f"{API_ROOT}/{record_id}",
        airtable_headers(),
        {"fields": fields},
    )
    time.sleep(0.25)


def run_realtime() -> int:
    candidates = list_candidates()
    print(f"{len(candidates)} candidates for generation")
    for rec in candidates:
        fields = rec.get("fields", {})
        name = fields.get("Name") or ""
        code = fields.get("Code React") or ""
        if not code:
            continue
        try:
            parsed = call_haiku(code, name)
        except Exception as e:
            print(f"skip {name}: {e}", file=sys.stderr)
            continue
        update_record(rec["id"], parsed)
        print(f"updated {name}")
    return 0


def run_batch_write(out_path: Path) -> int:
    candidates = list_candidates()
    with out_path.open("w", encoding="utf-8") as fh:
        for rec in candidates:
            fields = rec.get("fields", {})
            name = fields.get("Name") or ""
            code = fields.get("Code React") or ""
            if not code:
                continue
            fh.write(
                json.dumps(
                    {
                        "custom_id": rec["id"],
                        "params": {
                            "model": MODEL,
                            "max_tokens": 2000,
                            "system": SYSTEM_PROMPT,
                            "messages": [
                                {
                                    "role": "user",
                                    "content": f"Component name: {name}\n\nSource code:\n```\n{code}\n```",
                                }
                            ],
                        },
                    }
                )
                + "\n"
            )
    print(f"wrote {out_path} with {len(candidates)} requests")
    print(
        "submit with: curl https://api.anthropic.com/v1/messages/batches "
        '-H "x-api-key: $ANTHROPIC_API_KEY" -H "anthropic-version: 2023-06-01" '
        "-H 'content-type: application/json' --data-binary @<(jq -cs '{requests:.}' "
        f"{out_path})"
    )
    return 0


def run_apply(results_path: Path) -> int:
    count = 0
    with results_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            record_id = entry.get("custom_id")
            result = entry.get("result", {})
            if result.get("type") != "succeeded":
                print(f"skip {record_id}: {result.get('type')}", file=sys.stderr)
                continue
            msg = result.get("message", {})
            text = "".join(b.get("text", "") for b in msg.get("content", []) if b.get("type") == "text")
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                print(f"skip {record_id}: non-JSON response", file=sys.stderr)
                continue
            update_record(record_id, parsed)
            count += 1
            print(f"applied {record_id}")
    print(f"applied {count} records")
    return 0


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "realtime"
    if mode == "realtime":
        return run_realtime()
    if mode == "batch":
        out = Path(argv[2]) if len(argv) > 2 else Path("batch_requests.jsonl")
        return run_batch_write(out)
    if mode == "apply":
        if len(argv) < 3:
            sys.exit("usage: generate_fields_batch.py apply <results.jsonl>")
        return run_apply(Path(argv[2]))
    sys.exit(f"unknown mode: {mode}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
