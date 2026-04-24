# pk_assets

Automation pipeline for a UI component asset library stored in Airtable.
Syncs source code from open-source component repos into Airtable and uses
free-tier Gemini 2.5 Flash (with OpenRouter free-model fallback) to generate
marketplace descriptions, prompts, and HTML.

See [`pipeline/README.md`](pipeline/README.md) for architecture, cost, and
what still needs human judgment.

## Quick start

```bash
export AIRTABLE_PAT=pat_...   # scopes: read on Logins & Keys base, read/write on pk_assets base

mkdir -p repos
git clone --depth 1 https://github.com/magicuidesign/magicui.git repos/magicui
git clone --depth 1 https://github.com/markmead/hyperui.git repos/hyperui

python pipeline/detect_new_components.py
python pipeline/push_code_to_airtable.py
python pipeline/generate_fields_batch.py
```

Gemini and OpenRouter keys are fetched from Airtable at runtime, so only a
single `AIRTABLE_PAT` needs to be configured as a GitHub secret. A nightly
workflow at `.github/workflows/nightly-sync.yml` runs the three scripts at
3 AM UTC.

## Preview links in Airtable

`preview/` contains a Cloudflare Worker that renders any record's `Code HTML`
field as a live page. After `wrangler deploy`, add a formula field called
**Preview URL** in Airtable:

```
"https://pk-preview.<your-subdomain>.workers.dev/" & RECORD_ID()
```

Reviewers click the link → see the component rendered. See
[`preview/README.md`](preview/README.md) for setup.
