#!/usr/bin/env python3
"""Generate Description, Prompt Text, and Code HTML for records with source code.

Runs against free-tier models only:
    primary:   Gemini 2.5 Flash  (Google AI Studio, 7 keys rotated)
    fallback:  OpenRouter free   (e.g. meta-llama/llama-3.3-70b-instruct:free)

Keys live in the "Logins & Keys" Airtable base and are fetched at runtime so
key rotations upstream don't require a redeploy. The script walks a round-
robin pool per request, pulls a key out of rotation on hard failures, and
falls back to OpenRouter only after all Gemini keys are dead.

Selection filter: Code React populated (LEN > 50) AND (Description short
(LEN < 100) OR Prompt Text empty).

Env:
    AIRTABLE_PAT   Personal access token with read on the Logins & Keys base
                   AND read/write on the pk_assets base.

Modes:
    realtime   (default) process every candidate and write to Airtable
    apply <results.jsonl>
               parse a previously-saved JSONL {"record_id","parsed":{...}}
               file and patch Airtable (useful for retries / manual edits)
"""
from __future__ import annotations

import os
import sys
import json
import time
import re
from pathlib import Path
from urllib import request, parse, error

# --- pk_assets base (destination) -------------------------------------------

BASE_ID = "appbUpVCXkuPCOo6y"
TABLE_ID = "tblKkKRKRsd7IkqHm"

F_NAME = "fldYb4DpOA6hNTgYh"
F_DESCRIPTION = "fld5YVhf3YEgvyo2j"
F_PROMPT_TEXT = "fldJfx71qA76Z6bJE"
F_CODE_REACT = "fldWLYeGvIbKq9yK0"
F_CODE_HTML = "fldMfa1ePnUo2panh"

API_ROOT = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}"

# --- Logins & Keys base (source of API keys) --------------------------------

KEYS_BASE_ID = "app6biS7yjV6XzFVG"
KEYS_TABLE_ID = "tbldJkG11gY1W3jTf"
KEYS_FIELD_ID = "fld4fYgMypJz9Iete"  # free-text "Keys" column
GEMINI_RECORD_ID = "rec5AHCBuv5uOxAv7"
OPENROUTER_RECORD_ID = "recWh4W2XOf2TfZAn"

# --- Model config -----------------------------------------------------------

GEMINI_MODEL = "gemini-2.5-flash"
OPENROUTER_MODEL = os.environ.get(
    "OPENROUTER_MODEL", "meta-llama/llama-3.3-70b-instruct:free"
)

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


# --- HTTP helper ------------------------------------------------------------

def http(method: str, url: str, headers: dict, body: dict | None = None, timeout: int = 60) -> tuple[int, dict]:
    data = None
    h = {"Content-Type": "application/json", **headers}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
    req = request.Request(url, data=data, method=method, headers=h)
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:
            payload = {"error": str(e)}
        return e.code, payload


def airtable_pat() -> str:
    token = os.environ.get("AIRTABLE_PAT")
    if not token:
        sys.exit("AIRTABLE_PAT is not set")
    return token


def airtable_headers() -> dict:
    return {"Authorization": f"Bearer {airtable_pat()}"}


# --- Key fetching + rotation ------------------------------------------------

GEMINI_KEY_RE = re.compile(r"AIza[0-9A-Za-z_\-]{30,}")
OPENROUTER_KEY_RE = re.compile(r"sk-or-v1-[0-9a-f]{30,}")


def fetch_keys(record_id: str, pattern: re.Pattern) -> list[str]:
    url = f"https://api.airtable.com/v0/{KEYS_BASE_ID}/{KEYS_TABLE_ID}/{record_id}"
    status, payload = http("GET", url, airtable_headers())
    if status != 200:
        sys.exit(f"failed to fetch {record_id}: {status} {payload}")
    blob = payload.get("fields", {}).get("Keys") or payload.get("fields", {}).get(KEYS_FIELD_ID) or ""
    # De-duplicate while preserving order.
    seen: set[str] = set()
    keys: list[str] = []
    for match in pattern.findall(blob):
        if match not in seen:
            seen.add(match)
            keys.append(match)
    return keys


class KeyPool:
    def __init__(self, label: str, keys: list[str]) -> None:
        self.label = label
        self.keys = list(keys)
        self.index = 0
        self.dead: set[str] = set()

    def next(self) -> str | None:
        if not self.keys:
            return None
        for _ in range(len(self.keys)):
            key = self.keys[self.index]
            self.index = (self.index + 1) % len(self.keys)
            if key not in self.dead:
                return key
        return None

    def kill(self, key: str, reason: str) -> None:
        if key in self.dead:
            return
        self.dead.add(key)
        print(f"[{self.label}] removed key ...{key[-6:]} from pool: {reason}", file=sys.stderr)

    def alive(self) -> int:
        return len(self.keys) - len(self.dead)


# --- Model callers ----------------------------------------------------------

def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group(0))
    raise ValueError("model returned no parseable JSON")


def call_gemini(key: str, code: str, name: str) -> dict:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={key}"
    )
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "text": (
                            f"Component name: {name}\n\n"
                            f"Source code:\n```\n{code}\n```"
                        )
                    }
                ],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.4,
            "maxOutputTokens": 2048,
        },
    }
    status, payload = http("POST", url, {}, body)
    if status != 200:
        err = json.dumps(payload)[:200]
        raise RuntimeError(f"gemini {status}: {err}")
    candidates = payload.get("candidates") or []
    if not candidates:
        raise RuntimeError(f"gemini empty: {json.dumps(payload)[:200]}")
    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts)
    return _extract_json(text)


def call_openrouter(key: str, code: str, name: str) -> dict:
    body = {
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Component name: {name}\n\nSource code:\n```\n{code}\n```",
            },
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0.4,
        "max_tokens": 2048,
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "HTTP-Referer": "https://github.com/bwbcollier-byte/pk_assets",
        "X-Title": "pk_assets component sync",
    }
    status, payload = http("POST", "https://openrouter.ai/api/v1/chat/completions", headers, body)
    if status != 200:
        err = json.dumps(payload)[:200]
        raise RuntimeError(f"openrouter {status}: {err}")
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError(f"openrouter empty: {json.dumps(payload)[:200]}")
    text = choices[0].get("message", {}).get("content", "")
    return _extract_json(text)


# HTTP codes that should evict a key from the pool permanently for this run.
FATAL_STATUSES = ("401", "403")
# HTTP codes that are retryable — back off and try another key.
RATE_STATUSES = ("429", "503", "529")


def generate_one(gemini: KeyPool, openrouter: KeyPool, code: str, name: str) -> dict | None:
    attempts = 0
    max_attempts = gemini.alive() + openrouter.alive()
    while attempts < max_attempts:
        attempts += 1
        key = gemini.next()
        if key:
            try:
                return call_gemini(key, code, name)
            except Exception as e:
                msg = str(e)
                if any(code_ in msg for code_ in FATAL_STATUSES):
                    gemini.kill(key, msg[:80])
                elif any(code_ in msg for code_ in RATE_STATUSES):
                    time.sleep(1.5)
                else:
                    print(f"[gemini] {name}: {msg[:120]}", file=sys.stderr)
                continue
        key = openrouter.next()
        if key:
            try:
                return call_openrouter(key, code, name)
            except Exception as e:
                msg = str(e)
                if any(code_ in msg for code_ in FATAL_STATUSES):
                    openrouter.kill(key, msg[:80])
                elif any(code_ in msg for code_ in RATE_STATUSES):
                    time.sleep(1.5)
                else:
                    print(f"[openrouter] {name}: {msg[:120]}", file=sys.stderr)
                continue
        break
    return None


# --- Airtable I/O -----------------------------------------------------------

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
        status, payload = http("GET", url, airtable_headers())
        if status != 200:
            sys.exit(f"airtable list failed: {status} {payload}")
        records.extend(payload.get("records", []))
        offset = payload.get("offset")
        if not offset:
            break
        time.sleep(0.2)
    return records


def update_record(record_id: str, parsed: dict) -> None:
    fields = {
        F_DESCRIPTION: parsed.get("description", ""),
        F_PROMPT_TEXT: parsed.get("prompt_text", ""),
        F_CODE_HTML: parsed.get("code_html", ""),
    }
    status, payload = http(
        "PATCH",
        f"{API_ROOT}/{record_id}",
        airtable_headers(),
        {"fields": fields},
    )
    if status >= 400:
        print(f"airtable patch {record_id} failed: {status} {payload}", file=sys.stderr)
    time.sleep(0.2)


# --- Modes ------------------------------------------------------------------

def run_realtime() -> int:
    gemini_keys = fetch_keys(GEMINI_RECORD_ID, GEMINI_KEY_RE)
    openrouter_keys = fetch_keys(OPENROUTER_RECORD_ID, OPENROUTER_KEY_RE)
    print(f"gemini keys: {len(gemini_keys)}  |  openrouter keys: {len(openrouter_keys)}")
    if not gemini_keys and not openrouter_keys:
        sys.exit("no usable keys found in Airtable Logins & Keys")

    gemini = KeyPool("gemini", gemini_keys)
    openrouter = KeyPool("openrouter", openrouter_keys)

    candidates = list_candidates()
    print(f"{len(candidates)} candidates for generation")
    log_path = Path("generation_log.jsonl")
    succeeded = failed = 0
    with log_path.open("a", encoding="utf-8") as log:
        for rec in candidates:
            fields = rec.get("fields", {})
            name = fields.get("Name") or ""
            code = fields.get("Code React") or ""
            if not code:
                continue
            parsed = generate_one(gemini, openrouter, code, name)
            if parsed is None:
                failed += 1
                log.write(json.dumps({"record_id": rec["id"], "name": name, "error": "all providers exhausted"}) + "\n")
                print(f"FAIL {name}", file=sys.stderr)
                continue
            update_record(rec["id"], parsed)
            succeeded += 1
            log.write(json.dumps({"record_id": rec["id"], "name": name, "parsed": parsed}) + "\n")
            print(f"ok  {name}")
    print(f"done: {succeeded} succeeded, {failed} failed. log: {log_path}")
    return 0 if failed == 0 else 2


def run_apply(results_path: Path) -> int:
    count = 0
    with results_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            record_id = entry.get("record_id")
            parsed = entry.get("parsed")
            if not record_id or not isinstance(parsed, dict):
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
    if mode == "apply":
        if len(argv) < 3:
            sys.exit("usage: generate_fields_batch.py apply <results.jsonl>")
        return run_apply(Path(argv[2]))
    sys.exit(f"unknown mode: {mode}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
