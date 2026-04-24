#!/usr/bin/env python3
"""Harvest components from 21st.dev into Airtable as Draft records.

Uses the public shadcn registry JSON endpoints (no auth, no API key). Walks
the sitemap to enumerate every community component, fetches each component's
registry JSON at /r/<author>/<name>, and upserts a stub record into Airtable
keyed on Source URL so the script is safe to re-run.

Category assignment isn't available in the registry JSON and the category
listing pages hydrate client-side, so everything lands as Category="Components"
for now — a later pass can classify via Gemini based on code content.

Env:
    AIRTABLE_PAT   read/write on the pk_assets base

Flags:
    --limit N       stop after N components (use for smoke tests)
    --dry-run       fetch and print what would be created, no Airtable writes
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from urllib import error, parse, request

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

AIRTABLE_ROOT = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE_ID}"
SITEMAP_URL = "https://21st.dev/sitemap.xml"
UA = "pk_assets harvest_21st.py / +https://github.com/bwbcollier-byte/pk_assets"

SKIP_FIRST_SEGMENT = {"popular", "newest", "featured", "week", "s"}


def pat() -> str:
    token = os.environ.get("AIRTABLE_PAT")
    if not token:
        sys.exit("AIRTABLE_PAT is not set")
    return token


def http_get(url: str, headers: dict | None = None) -> tuple[int, bytes]:
    req = request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    for attempt in range(4):
        try:
            with request.urlopen(req, timeout=45) as resp:
                return resp.status, resp.read()
        except error.HTTPError as e:
            if e.code in (429, 503, 529):
                time.sleep(2 ** attempt)
                continue
            return e.code, e.read() if hasattr(e, "read") else b""
        except error.URLError:
            time.sleep(1 + attempt)
            continue
    return 0, b""


def airtable(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {
        "Authorization": f"Bearer {pat()}",
        "Content-Type": "application/json",
        "User-Agent": UA,
    }
    req = request.Request(url, data=data, method=method, headers=headers)
    for attempt in range(4):
        try:
            with request.urlopen(req, timeout=60) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as e:
            if e.code == 429:
                time.sleep(2 ** attempt)
                continue
            try:
                payload = json.loads(e.read().decode("utf-8"))
            except Exception:
                payload = {"error": str(e)}
            return e.code, payload
    return 0, {}


def enumerate_components() -> list[tuple[str, str]]:
    """Return [(author, component_slug), ...] from the public sitemap."""
    status, body = http_get(SITEMAP_URL)
    if status != 200 or not body:
        sys.exit(f"sitemap fetch failed: {status}")
    xml = body.decode("utf-8")
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for loc in re.findall(r"<loc>([^<]+)</loc>", xml):
        if not loc.startswith("https://21st.dev/community/components/"):
            continue
        tail = loc[len("https://21st.dev/community/components/"):].strip("/")
        if not tail:
            continue
        parts = tail.split("/")
        if parts[0] in SKIP_FIRST_SEGMENT:
            continue
        if len(parts) < 2:
            continue
        # Variant URLs (3+ segments) 404 on the registry endpoint — skip.
        if len(parts) > 2:
            continue
        key = (parts[0], parts[1])
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def fetch_registry(author: str, slug: str) -> dict | None:
    url = f"https://21st.dev/r/{author}/{slug}"
    status, body = http_get(url)
    if status != 200:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return None


def existing_source_urls() -> set[str]:
    urls: set[str] = set()
    offset: str | None = None
    while True:
        qs = {
            "pageSize": "100",
            "fields[]": "Source URL",
            "filterByFormula": "LEN({Source URL})>0",
        }
        if offset:
            qs["offset"] = offset
        # urlencode doesn't handle the repeated fields[] the way we want; build manually.
        qs_pairs = [(k, v) for k, v in qs.items()]
        status, payload = airtable("GET", f"{AIRTABLE_ROOT}?{parse.urlencode(qs_pairs)}")
        if status != 200:
            sys.exit(f"airtable list failed: {status} {payload}")
        for rec in payload.get("records", []):
            u = rec.get("fields", {}).get("Source URL")
            if u:
                urls.add(u.strip())
        offset = payload.get("offset")
        if not offset:
            break
        time.sleep(0.1)
    return urls


def title_case(slug: str) -> str:
    return " ".join(w.capitalize() for w in slug.replace("_", "-").split("-") if w)


def build_stub(author: str, slug: str, registry: dict) -> dict:
    files = registry.get("files") or []
    if not files:
        return {}
    # Concatenate if multiple files; most components ship one.
    if len(files) == 1:
        code = files[0].get("content") or ""
    else:
        parts = []
        for f in files:
            path = f.get("path", "")
            parts.append(f"// ---- {path} ----\n{f.get('content', '')}")
        code = "\n\n".join(parts)
    if not code.strip():
        return {}

    deps = registry.get("dependencies") or []
    display = f"{title_case(slug)} (21st.dev)"
    source_url = f"https://21st.dev/community/components/{author}/{slug}"
    tag_words = [author.lower()]
    for token in re.split(r"[-_]", slug):
        if token:
            tag_words.append(token.lower())
    for d in deps:
        tag_words.append(d.lower())

    return {
        "fields": {
            F_NAME: display,
            F_CODE_REACT: code,
            F_TOKEN_ESTIMATE: max(1, len(code) // 4),
            F_TAGS: ", ".join(dict.fromkeys(tag_words)),  # dedupe, preserve order
            F_FRAMEWORK: ["React", "Tailwind"],
            F_CATEGORY: "Components",
            F_STYLE: "Dark UI",
            F_TIER: "Free",
            F_PUBLISHED: "Draft",
            F_SOURCE: "21st.dev",
            F_SOURCE_URL: source_url,
        }
    }


def create_records(stubs: list[dict]) -> tuple[int, int]:
    ok = fail = 0
    for i in range(0, len(stubs), 10):
        chunk = stubs[i : i + 10]
        status, payload = airtable(
            "POST",
            AIRTABLE_ROOT,
            {"records": chunk, "typecast": True},
        )
        if status >= 400:
            fail += len(chunk)
            print(f"create failed ({status}): {json.dumps(payload)[:200]}", file=sys.stderr)
        else:
            ok += len(payload.get("records", []))
        time.sleep(0.25)
    return ok, fail


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="stop after N components")
    ap.add_argument("--dry-run", action="store_true", help="no Airtable writes")
    args = ap.parse_args(argv)

    print("enumerating components from sitemap…", flush=True)
    components = enumerate_components()
    print(f"  found {len(components)} 2-segment component paths", flush=True)

    if not args.dry_run:
        print("loading existing Source URLs from Airtable…", flush=True)
        known = existing_source_urls()
        print(f"  {len(known)} already in Airtable", flush=True)
    else:
        known = set()

    stubs: list[dict] = []
    skipped_existing = fetch_failed = empty_registry = 0
    for idx, (author, slug) in enumerate(components, 1):
        if args.limit and len(stubs) >= args.limit:
            break
        source_url = f"https://21st.dev/community/components/{author}/{slug}"
        if source_url in known:
            skipped_existing += 1
            continue
        registry = fetch_registry(author, slug)
        if registry is None:
            fetch_failed += 1
            print(f"[{idx}/{len(components)}] fetch fail {author}/{slug}", file=sys.stderr)
            time.sleep(0.2)
            continue
        stub = build_stub(author, slug, registry)
        if not stub:
            empty_registry += 1
            continue
        stubs.append(stub)
        if len(stubs) % 25 == 0:
            print(f"[{idx}/{len(components)}] staged {len(stubs)} stubs", flush=True)
        time.sleep(0.2)

    print(
        f"staged={len(stubs)} skipped_existing={skipped_existing} "
        f"fetch_failed={fetch_failed} empty_registry={empty_registry}",
        flush=True,
    )

    if args.dry_run:
        print("--dry-run: no records created")
        for s in stubs[:5]:
            print(json.dumps(s["fields"].get(F_NAME), ensure_ascii=False))
        return 0

    if not stubs:
        print("nothing new to create")
        return 0

    ok, fail = create_records(stubs)
    print(f"airtable: created={ok} failed={fail}")
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
