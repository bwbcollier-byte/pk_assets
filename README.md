# pk_assets

Automation pipeline for a UI component asset library stored in Airtable.
Syncs source code from open-source component repos into Airtable and uses
Claude Haiku to generate marketplace descriptions, prompts, and HTML.

See [`pipeline/README.md`](pipeline/README.md) for architecture, cost, and
what still needs human judgment.

## Quick start

```bash
export AIRTABLE_PAT=pat_...
export ANTHROPIC_API_KEY=sk-ant-...

mkdir -p repos
git clone --depth 1 https://github.com/magicuidesign/magicui.git repos/magicui
git clone --depth 1 https://github.com/markmead/hyperui.git repos/hyperui

python pipeline/detect_new_components.py
python pipeline/push_code_to_airtable.py
python pipeline/generate_fields_batch.py batch batch_requests.jsonl
# submit to Anthropic Message Batches API, then:
python pipeline/generate_fields_batch.py apply results.jsonl
```

A nightly GitHub Actions workflow (`.github/workflows/nightly-sync.yml`) runs
the same steps at 3 AM UTC using the `AIRTABLE_PAT` and `ANTHROPIC_API_KEY`
repo secrets.
