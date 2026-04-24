#!/usr/bin/env python3
"""Harvest components from 21st.dev into Airtable as Draft records.

For every two-segment component URL in 21st.dev's public sitemap:
  1. Fetch the shadcn registry JSON at /r/<author>/<slug> (source + deps).
  2. Fetch the demo source from cdn.21st.dev (best-effort).
  3. Fetch each registryDependency's main file (best-effort, one level deep).
  4. Assemble a Prompt Text that exactly matches 21st.dev's 'Copy prompt'
     format — deterministic, no LLM involved.

Behavior:
  * New components (by Source URL) → CREATE full record with Prompt Text
    already populated. Gemini then only needs to fill Description and Code
    HTML, not Prompt Text.
  * Existing components → by default PATCH the Prompt Text to the freshly
    templated version (overwrites anything Gemini wrote previously). Pass
    --no-update-prompts to skip these.

Env:
    AIRTABLE_PAT   read/write on the pk_assets base

Flags:
    --limit N              stop after N components touched
    --dry-run              no Airtable writes
    --no-update-prompts    skip the Prompt Text backfill on existing records
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from urllib import error, parse, request

from prompt_template import build_prompt_text

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
    try:
        req = request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    except Exception:
        # Malformed URL — caller treats 0 as a skip.
        return 0, b""
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
        except Exception:
            # Catches http.client.InvalidURL and other connection-level oddities.
            return 0, b""
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
        if len(parts) != 2:
            continue
        # Decode so we work with raw author/slug strings internally — we
        # re-encode when building URLs and the Source URL field value.
        author = parse.unquote(parts[0])
        slug = parse.unquote(parts[1])
        key = (author, slug)
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _enc(segment: str) -> str:
    return parse.quote(segment, safe="")


def fetch_registry(author: str, slug: str) -> dict | None:
    status, body = http_get(f"https://21st.dev/r/{_enc(author)}/{_enc(slug)}")
    if status != 200:
        return None
    try:
        data = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "files" not in data:
        return None
    return data


def fetch_demo(author: str, slug: str) -> str | None:
    status, body = http_get(
        f"https://cdn.21st.dev/user_{_enc(author)}/{_enc(slug)}.demo.tsx"
    )
    if status != 200 or not body:
        return None
    return body.decode("utf-8", errors="replace")


def fetch_registry_dep(ref: str) -> dict | None:
    """Resolve a registryDependencies entry — usually a full /r URL, sometimes
    a bare author/slug, rarely a shadcn primitive name."""
    if ref.startswith("http://") or ref.startswith("https://"):
        url = ref
    elif "/" in ref:
        url = f"https://21st.dev/r/{ref}"
    else:
        # Bare primitive like 'button' → try shadcn's own registry.
        url = f"https://ui.shadcn.com/r/styles/default/{ref}.json"
    status, body = http_get(url)
    if status != 200 or not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return None


def build_registry_deps(refs: list[str]) -> list[dict]:
    """Resolve a list of registryDependencies into inlinable {label, code} blocks."""
    out: list[dict] = []
    for ref in refs or []:
        dep = fetch_registry_dep(ref)
        if not dep:
            continue
        files = dep.get("files") or []
        if not files:
            continue
        first = files[0]
        code = first.get("content") or ""
        if not code.strip():
            continue
        # Label: for URL-style refs use author/name, for primitives use shadcn/name.
        if ref.startswith("http"):
            tail = ref.rsplit("/r/", 1)[-1] if "/r/" in ref else ref.rsplit("/", 1)[-1]
            label = tail
        elif "/" in ref:
            label = ref
        else:
            label = f"shadcn/{ref}"
        out.append({"label": label, "code": code})
    return out


def list_existing(limit: int = 0) -> dict[str, str]:
    """Return {source_url: record_id} across all existing records."""
    by_url: dict[str, str] = {}
    offset: str | None = None
    while True:
        qs_pairs = [
            ("pageSize", "100"),
            ("fields[]", "Source URL"),
            ("filterByFormula", "LEN({Source URL})>0"),
        ]
        if offset:
            qs_pairs.append(("offset", offset))
        status, payload = airtable("GET", f"{AIRTABLE_ROOT}?{parse.urlencode(qs_pairs)}")
        if status != 200:
            sys.exit(f"airtable list failed: {status} {payload}")
        for rec in payload.get("records", []):
            u = rec.get("fields", {}).get("Source URL")
            if u:
                by_url[u.strip()] = rec["id"]
        offset = payload.get("offset")
        if not offset:
            break
        if limit and len(by_url) >= limit * 4:
            # Heuristic: don't page forever when we only need a small sample.
            pass
        time.sleep(0.1)
    return by_url


def title_case(slug: str) -> str:
    return " ".join(w.capitalize() for w in slug.replace("_", "-").split("-") if w)


def build_stub(author: str, slug: str, registry: dict, prompt_text: str, demo: str | None) -> dict:
    files = registry.get("files") or []
    if not files:
        return {}
    if len(files) == 1:
        code = files[0].get("content") or ""
    else:
        code = "\n\n".join(
            f"// ---- {f.get('path','')} ----\n{f.get('content','')}" for f in files
        )
    if not code.strip():
        return {}

    deps = registry.get("dependencies") or []
    display = f"{title_case(slug)} (21st.dev)"
    source_url = f"https://21st.dev/community/components/{_enc(author)}/{_enc(slug)}"

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
            F_PROMPT_TEXT: prompt_text,
            F_TOKEN_ESTIMATE: max(1, len(code) // 4),
            F_TAGS: ", ".join(dict.fromkeys(tag_words)),
            F_FRAMEWORK: ["React", "Tailwind"],
            F_CATEGORY: "Components",
            F_STYLE: "Dark UI",
            F_TIER: "Free",
            F_PUBLISHED: "Draft",
            F_SOURCE: "21st.dev",
            F_SOURCE_URL: source_url,
        }
    }


def assemble_prompt(author: str, slug: str, registry: dict) -> tuple[str, str | None]:
    files = registry.get("files") or []
    if not files:
        return "", None
    main_file = files[0]
    main_filename = main_file.get("path", "").split("/")[-1] or f"{slug}.tsx"
    main_code = main_file.get("content") or ""
    demo = fetch_demo(author, slug)
    npm_deps = registry.get("dependencies") or []
    reg_deps_refs = registry.get("registryDependencies") or []
    reg_deps = build_registry_deps(reg_deps_refs)
    tw = (registry.get("tailwind") or {}).get("config") or None
    if tw == {}:
        tw = None
    prompt = build_prompt_text(
        main_filename=main_filename,
        main_code=main_code,
        demo_code=demo,
        npm_deps=npm_deps,
        registry_deps=reg_deps,
        tailwind_config=tw,
    )
    return prompt, demo


def create_records(stubs: list[dict]) -> tuple[int, int]:
    ok = fail = 0
    for i in range(0, len(stubs), 10):
        chunk = stubs[i : i + 10]
        status, payload = airtable(
            "POST", AIRTABLE_ROOT, {"records": chunk, "typecast": True}
        )
        if status >= 400:
            fail += len(chunk)
            print(f"create failed ({status}): {json.dumps(payload)[:200]}", file=sys.stderr)
        else:
            ok += len(payload.get("records", []))
        time.sleep(0.25)
    return ok, fail


def patch_records(updates: list[dict]) -> tuple[int, int]:
    ok = fail = 0
    for i in range(0, len(updates), 10):
        chunk = updates[i : i + 10]
        status, payload = airtable(
            "PATCH", AIRTABLE_ROOT, {"records": chunk, "typecast": True}
        )
        if status >= 400:
            fail += len(chunk)
            print(f"patch failed ({status}): {json.dumps(payload)[:200]}", file=sys.stderr)
        else:
            ok += len(payload.get("records", []))
        time.sleep(0.25)
    return ok, fail


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="stop after N components touched")
    ap.add_argument("--dry-run", action="store_true", help="no Airtable writes")
    ap.add_argument(
        "--no-update-prompts",
        action="store_true",
        help="skip rewriting Prompt Text on already-existing records",
    )
    args = ap.parse_args(argv)

    print("enumerating components from sitemap…", flush=True)
    components = enumerate_components()
    print(f"  found {len(components)} 2-segment component paths", flush=True)

    known = list_existing() if not args.dry_run else {}
    if not args.dry_run:
        print(f"  {len(known)} existing records indexed by Source URL", flush=True)

    stubs: list[dict] = []
    updates: list[dict] = []
    fetch_failed = empty_registry = touched = 0
    ok_new_total = fail_new_total = ok_upd_total = fail_upd_total = 0
    FLUSH_EVERY = 50

    for idx, (author, slug) in enumerate(components, 1):
        if args.limit and touched >= args.limit:
            break
        try:
            source_url = f"https://21st.dev/community/components/{_enc(author)}/{_enc(slug)}"
            is_existing = source_url in known
            if is_existing and args.no_update_prompts:
                continue

            registry = fetch_registry(author, slug)
            if registry is None:
                fetch_failed += 1
                print(f"[{idx}/{len(components)}] fetch fail {author}/{slug}", file=sys.stderr)
                time.sleep(0.15)
                continue

            prompt_text, demo = assemble_prompt(author, slug, registry)
            if not prompt_text:
                empty_registry += 1
                continue

            if is_existing:
                updates.append(
                    {"id": known[source_url], "fields": {F_PROMPT_TEXT: prompt_text}}
                )
            else:
                stub = build_stub(author, slug, registry, prompt_text, demo)
                if not stub:
                    empty_registry += 1
                    continue
                stubs.append(stub)

            touched += 1
            if touched % 25 == 0:
                print(
                    f"[{idx}/{len(components)}] staged new={len(stubs)} updates={len(updates)}",
                    flush=True,
                )
            if not args.dry_run and (len(stubs) >= FLUSH_EVERY or len(updates) >= FLUSH_EVERY):
                if stubs:
                    ok_n, fail_n = create_records(stubs)
                    ok_new_total += ok_n
                    fail_new_total += fail_n
                    stubs = []
                if updates:
                    ok_u, fail_u = patch_records(updates)
                    ok_upd_total += ok_u
                    fail_upd_total += fail_u
                    updates = []
                print(
                    f"[{idx}/{len(components)}] flushed — created={ok_new_total} updated={ok_upd_total}",
                    flush=True,
                )
        except Exception as e:
            fetch_failed += 1
            print(f"[{idx}/{len(components)}] error {author}/{slug}: {e}", file=sys.stderr)
        time.sleep(0.15)

    print(
        f"staged new={len(stubs)} updates={len(updates)} "
        f"fetch_failed={fetch_failed} empty_registry={empty_registry}",
        flush=True,
    )

    if args.dry_run:
        print("--dry-run: no writes")
        for s in stubs[:3]:
            print("CREATE", s["fields"].get(F_NAME))
        for u in updates[:3]:
            print("UPDATE", u["id"])
        return 0

    # Final flush for whatever is left after the last increment.
    if stubs:
        ok_n, fail_n = create_records(stubs)
        ok_new_total += ok_n
        fail_new_total += fail_n
    if updates:
        ok_u, fail_u = patch_records(updates)
        ok_upd_total += ok_u
        fail_upd_total += fail_u

    total_fail = fail_new_total + fail_upd_total
    print(
        f"airtable: created={ok_new_total} updated={ok_upd_total} failed={total_fail}"
    )
    return 0 if total_fail == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
