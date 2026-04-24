# preview

Cloudflare Worker that renders an Airtable record's `Code HTML` field into a
Tailwind-styled dark preview page. Intended for a Preview URL formula field in
Airtable so reviewers can click through from a record.

## One-time setup

1. Install Wrangler (Cloudflare's CLI) if you don't have it:
   ```bash
   npm install -g wrangler
   ```
2. Log in with your Cloudflare account:
   ```bash
   wrangler login
   ```
3. From this directory, store the Airtable PAT as a Worker secret:
   ```bash
   cd preview
   wrangler secret put AIRTABLE_PAT
   # Paste the same PAT value used elsewhere in pk_assets
   ```
4. Deploy:
   ```bash
   wrangler deploy
   ```
   Output will be a URL like `https://pk-preview.<your-subdomain>.workers.dev`.

## Wire it into Airtable

In the **Assets** table, add a formula field called **Preview URL**:

```
"https://pk-preview.<your-subdomain>.workers.dev/" & RECORD_ID()
```

Make it a URL field type so it's clickable. Now every row has a link that opens
a live-rendered preview of that component in a new tab.

## Redeploying after changes

```bash
wrangler deploy
```

No secret rotation needed unless the PAT itself changes.

## Local dev

```bash
wrangler dev
```

Serves on `http://localhost:8787`. Works with your real AIRTABLE_PAT once it's
set via `wrangler secret put`.

## What it renders

- `Name` in the header
- `Description` as a subtitle
- `Source` with a link to `Source URL` on the right
- `Code HTML` as the main body, centered, on a dark background with Tailwind CDN

If `Code HTML` is empty, shows a friendly "not generated yet" state instead.

## Security

The Worker has read-only scope via `AIRTABLE_PAT`. Anyone with a record ID can
view its `Code HTML` + metadata — URLs aren't guessable but aren't secret
either. If you ever need auth, put Cloudflare Access in front of it.
