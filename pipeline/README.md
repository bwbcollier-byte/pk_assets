# Component Sync Pipeline

Automated pipeline that keeps an Airtable-backed UI component marketplace in
sync with a handful of open-source component repos. Source code lives in
those upstream repos; Airtable is the system of record for marketplace
metadata. Claude Haiku fills in the human-facing copy.

## Architecture

```
upstream repos ──clone──▶ repos/<library>/...
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │ detect_new_components.py                │
        │  create stub records for any source     │
        │  file not yet in Airtable               │
        └─────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │ push_code_to_airtable.py                │
        │  fill Code React + Token Estimate on    │
        │  records that match a source file       │
        └─────────────────────────────────────────┘
                              │
                              ▼
        ┌─────────────────────────────────────────┐
        │ generate_fields_batch.py                │
        │  Claude Haiku writes Description,       │
        │  Prompt Text, and Code HTML             │
        └─────────────────────────────────────────┘
                              │
                              ▼
                    human QA + publish
```

## Scripts

| Script | Purpose |
|---|---|
| `detect_new_components.py` | Walks `repos/` and creates Airtable stubs for components that don't exist yet. Sets defaults: Framework `[React, Tailwind]`, Category `Components`, Style `Dark UI`, Tier `Free`, Published `Draft`, Source `Scraped`, Source URL, and Tags from the filename. |
| `push_code_to_airtable.py` | Reads source files under `repos/` and patches `Code React` + `Token Estimate` on matching records. Skips records that already have code. Matching is kebab-case filename → `Title Case (Library Name)` in Airtable, with a short override list for ambiguous cases. |
| `generate_fields_batch.py` | For records with code but thin copy, asks **Gemini 2.5 Flash** (free tier) for a JSON blob with `description`, `prompt_text`, and `code_html`. Falls back to **OpenRouter free models** (default `meta-llama/llama-3.3-70b-instruct:free`) when Gemini keys are exhausted. Both providers use key pools rotated round-robin per request, pulled from the Airtable Logins & Keys base at runtime so upstream key rotations don't require a redeploy. Supports `realtime` (default) and `apply <results.jsonl>` (re-patch Airtable from a saved log). |

## Setup

```bash
# Env — only one secret needed. The PAT must have:
#   read/write on the pk_assets base     (appbUpVCXkuPCOo6y)
#   read on the Logins & Keys base       (app6biS7yjV6XzFVG)
export AIRTABLE_PAT=pat_...

# Clone upstream component repos (the pipeline reads from these paths)
mkdir -p repos
git clone --depth 1 https://github.com/magicuidesign/magicui.git repos/magicui
git clone --depth 1 https://github.com/markmead/hyperui.git repos/hyperui
```

Airtable config is hard-coded at the top of each script:

- pk_assets base `appbUpVCXkuPCOo6y`, table `tblKkKRKRsd7IkqHm`
- Logins & Keys base `app6biS7yjV6XzFVG`, table `tbldJkG11gY1W3jTf`
  - Gemini keys record `rec5AHCBuv5uOxAv7` (parsed from the Keys field by `AIza…` prefix)
  - OpenRouter keys record `recWh4W2XOf2TfZAn` (parsed by `sk-or-v1-…` prefix)

Override the OpenRouter fallback model with `OPENROUTER_MODEL=...:free`.

## Run order

```bash
python pipeline/detect_new_components.py   # create stubs for new files
python pipeline/push_code_to_airtable.py   # fill in source code
python pipeline/generate_fields_batch.py   # Gemini → OpenRouter fallback
```

Each generation appends to `generation_log.jsonl`. To re-patch Airtable from
that log (e.g., after manual edits to a failed entry):

```bash
python pipeline/generate_fields_batch.py apply generation_log.jsonl
```

## Cost

| Step | Cost |
|---|---|
| `detect_new_components.py` | $0 |
| `push_code_to_airtable.py` | $0 |
| `generate_fields_batch.py` (Gemini free + OpenRouter free) | $0 |

Throughput: Gemini's free tier is ~15 RPM per key. With 7 keys rotated
round-robin, that's ~105 RPM — 368 components finish in ~4 minutes. If a key
hits HTTP 401/403 it's pulled from the pool for the run; OpenRouter keys take
over once all Gemini keys are out.

The trade-off versus paid Claude Haiku: copy quality is lower and JSON output
is a bit flakier. The pipeline retries once via OpenRouter on a parse error;
anything that still fails lands in `generation_log.jsonl` for human review or
for a Claude Code pass later.

## Repo coverage

| Library | Status | Notes |
|---|---|---|
| Magic UI | ✅ | 76 `.tsx` files under `apps/www/registry/magicui` |
| HyperUI | ✅ | HTML snippets under `src/components` |
| Aceternity UI | ❌ | No public source repo — needs hand-authored prompts |
| daisyUI | N/A | CSS-only library; components are Tailwind class lists, not files |

## What still needs human judgment

- **QA pass** on the Haiku-generated copy before flipping `Published` off `Draft`.
- **Aceternity and daisyUI** entries — no source code, so the Haiku generator
  can't run on them. Prompts and HTML need to be authored by hand or sourced
  from the component's demo page.
- **Pro tier curation** — everything defaults to `Free`; picking the right Pro
  candidates is a product decision, not an automation one.
- **Featured flags** — highlighted entries are a marketing call.
