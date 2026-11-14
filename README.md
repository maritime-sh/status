# Maritime status page

Public status page for Maritime, served by GitHub Pages at https://status.maritime.sh.

It lives on GitHub on purpose: it must stay up when Maritime's own infrastructure
(Railway, Hetzner hosts, Neon) is down. Do not move it onto Maritime servers.

## How it works

- `.github/workflows/status.yml` runs every 5 minutes (GitHub's scheduler can run late,
  the page says so if checks are more than 45 minutes old).
- `scripts/check.py` (standard library only) checks each component in `config.json`
  three times before calling it down, records daily up/total counts in
  `data/status.json` (90 days kept), and builds `data/incidents.json` and `feed.xml`
  from the Markdown files in `incidents/`.
- `index.html` is a static page that reads those JSON files.

| Component | Check |
| --- | --- |
| Website and dashboard | `GET https://maritime.sh` |
| API | `GET https://api.maritime.sh/health`, body contains `"status":"ok"` |
| Agents / computers | No public endpoint; set by incidents only |
| MCP | `GET https://mcp.maritime.sh/healthz`, body contains `"ok":true` |

A response slower than 5 s counts as degraded.

## Posting an incident

1. Copy `incidents/_template.md` to `incidents/YYYY-MM-DD-short-name.md`.
2. Fill in `title`, `status` (investigating, identified, monitoring, resolved),
   `impact` (outage, degraded, maintenance), `components` (website, api, agents, mcp),
   and `started` in UTC.
3. Write the update in plain language. Add each new update as a new paragraph at the top,
   starting with the bold status and the UTC time.
4. Commit to `main` (the GitHub web editor is fine). The push triggers the workflow and
   the page updates within about a minute.
5. When fixed, set `status: resolved` and `resolved:` to the UTC time, and add a final update.

Writing rules: plain words, no em or en dashes, say "agents" or "instances", never "fleet",
no customer names, no internal hostnames or IPs.

Customers are also emailed directly for incidents that affect them (see the contingency plan).
Anyone can follow incidents through the RSS feed at https://status.maritime.sh/feed.xml.
