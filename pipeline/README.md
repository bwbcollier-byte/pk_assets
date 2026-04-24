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
| `generate_fields_batch.py` | For records with code but thin copy, asks Claude Haiku (`claude-haiku-4-5-20251001`) for a JSON blob with `description`, `prompt_text`, and `code_html`. Supports `realtime` (sync, ~$0.006/component) and `batch` (writes JSONL for the Message Batches API, 50% cheaper). Also has an `apply <results.jsonl>` mode for writing batch results back to Airtable. |

## Setup

```bash
# Env
export AIRTABLE_PAT=pat_...        # scope: data.records:read, data.records:write
export ANTHROPIC_API_KEY=sk-ant-...

# Clone upstream component repos (the pipeline reads from these paths)
mkdir -p repos
git clone --depth 1 https://github.com/magicuidesign/magicui.git repos/magicui
git clone --depth 1 https://github.com/markmead/hyperui.git repos/hyperui
```

Airtable config is hard-coded at the top of each script:

- Base `appbUpVCXkuPCOo6y`
- Table `tblKkKRKRsd7IkqHm`

## Run order

```bash
python pipeline/detect_new_components.py       # create stubs for new files
python pipeline/push_code_to_airtable.py       # fill in source code
python pipeline/generate_fields_batch.py batch batch_requests.jsonl
# submit batch_requests.jsonl via Anthropic Message Batches API, then:
python pipeline/generate_fields_batch.py apply results.jsonl
```

For ad-hoc runs, `generate_fields_batch.py realtime` will stream updates one
record at a time.

## Cost

| Step | Cost |
|---|---|
| `detect_new_components.py` | $0 |
| `push_code_to_airtable.py` | $0 |
| `generate_fields_batch.py` (realtime, 368 components) | ~$2.20 |
| `generate_fields_batch.py` (batch mode, 368 components) | ~$1.10 |

Batch mode is strongly preferred — same output, half the price, no rate-limit
hand-wringing.

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
