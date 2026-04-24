/**
 * Cloudflare Worker that renders an Airtable record's Code HTML field.
 *
 * GET /<record_id> → HTML page wrapping the Code HTML value in Tailwind-styled
 *                    dark chrome. Intended for a Preview URL formula field in
 *                    Airtable so reviewers can click through and see the
 *                    component rendered without rebuilding locally.
 *
 * Secrets:
 *   AIRTABLE_PAT   Airtable personal access token with read on the pk_assets
 *                  base.
 */

const BASE_ID = "appbUpVCXkuPCOo6y";
const TABLE_ID = "tblKkKRKRsd7IkqHm";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const recordId = url.pathname.replace(/^\/+|\/+$/g, "");

    if (!recordId) {
      return html(landingPage(), 200);
    }
    if (!/^rec[a-zA-Z0-9]{14}$/.test(recordId)) {
      return html(errorPage("Bad record ID", recordId), 400);
    }

    const apiUrl = `https://api.airtable.com/v0/${BASE_ID}/${TABLE_ID}/${recordId}`;
    let resp;
    try {
      resp = await fetch(apiUrl, {
        headers: { Authorization: `Bearer ${env.AIRTABLE_PAT}` },
      });
    } catch (e) {
      return html(errorPage("Network error reaching Airtable", String(e)), 502);
    }

    if (resp.status === 404) {
      return html(errorPage("Record not found", recordId), 404);
    }
    if (!resp.ok) {
      return html(errorPage(`Airtable returned ${resp.status}`, await resp.text()), 502);
    }

    const data = await resp.json();
    const fields = data.fields || {};
    const name = fields["Name"] || "Component";
    const codeHtml = fields["Code HTML"] || "";
    const description = fields["Description"] || "";
    const source = fields["Source"] || "";
    const sourceUrl = fields["Source URL"] || "";

    if (!codeHtml.trim()) {
      return html(emptyStatePage(name, description, source, sourceUrl), 200);
    }

    return html(renderPage(name, codeHtml, description, source, sourceUrl), 200);
  },
};

function html(body, status) {
  return new Response(body, {
    status,
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Cache-Control": "no-store",
    },
  });
}

function escape(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function chrome(title, innerBody) {
  return `<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${escape(title)}</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    :root { color-scheme: dark; }
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
  </style>
</head>
<body class="bg-zinc-950 text-zinc-100 min-h-screen">
  ${innerBody}
</body>
</html>`;
}

function headerBar(name, description, source, sourceUrl) {
  const sourceLink = sourceUrl
    ? `<a href="${escape(sourceUrl)}" target="_blank" rel="noopener" class="text-zinc-400 hover:text-zinc-200 underline decoration-dotted">${escape(source || "source")}</a>`
    : source
    ? `<span class="text-zinc-500">${escape(source)}</span>`
    : "";
  return `
  <header class="border-b border-zinc-800/80 px-6 py-4 flex items-center justify-between gap-6">
    <div class="min-w-0">
      <h1 class="text-lg font-semibold text-zinc-100 truncate">${escape(name)}</h1>
      ${description ? `<p class="text-sm text-zinc-400 mt-1 truncate">${escape(description)}</p>` : ""}
    </div>
    <div class="text-sm shrink-0">${sourceLink}</div>
  </header>`;
}

function renderPage(name, codeHtml, description, source, sourceUrl) {
  return chrome(
    name,
    `
    ${headerBar(name, description, source, sourceUrl)}
    <main class="px-6 py-12 flex items-center justify-center">
      <div class="w-full max-w-5xl">${codeHtml}</div>
    </main>
  `,
  );
}

function emptyStatePage(name, description, source, sourceUrl) {
  return chrome(
    name,
    `
    ${headerBar(name, description, source, sourceUrl)}
    <main class="px-6 py-24 flex items-center justify-center">
      <div class="max-w-md text-center text-zinc-400">
        <p class="text-zinc-200 font-semibold mb-2">No preview yet</p>
        <p class="text-sm">This record's <span class="text-zinc-300">Code HTML</span> field is empty. The nightly generator populates it automatically — check back after the next run, or trigger the workflow manually.</p>
      </div>
    </main>
  `,
  );
}

function errorPage(title, detail) {
  return chrome(
    title,
    `
    <main class="px-6 py-24 flex items-center justify-center">
      <div class="max-w-md text-center">
        <h1 class="text-xl font-semibold text-zinc-100 mb-2">${escape(title)}</h1>
        ${detail ? `<pre class="mt-4 text-left text-xs text-zinc-500 bg-zinc-900/60 p-4 rounded overflow-auto">${escape(detail).slice(0, 400)}</pre>` : ""}
      </div>
    </main>
  `,
  );
}

function landingPage() {
  return chrome(
    "pk_assets preview",
    `
    <main class="px-6 py-24 flex items-center justify-center">
      <div class="max-w-md text-center">
        <h1 class="text-xl font-semibold text-zinc-100 mb-2">pk_assets preview</h1>
        <p class="text-sm text-zinc-400">Append an Airtable record ID to the URL, e.g. <code class="text-zinc-300">/recXXXXXXXXXXXXXX</code>.</p>
      </div>
    </main>
  `,
  );
}
