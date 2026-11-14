#!/usr/bin/env python3
"""Check each component, update data/status.json, and rebuild incident data.

Standard library only. Run from the repo root:
    python3 scripts/check.py            # run checks and rebuild
    python3 scripts/check.py --no-check # rebuild incidents/feed only
"""
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = json.loads((ROOT / "config.json").read_text())
STATUS_FILE = ROOT / "data" / "status.json"
INCIDENTS_DIR = ROOT / "incidents"
INCIDENTS_FILE = ROOT / "data" / "incidents.json"
FEED_FILE = ROOT / "feed.xml"
USER_AGENT = "maritime-status-check/1.0 (+https://status.maritime.sh)"

OPEN_STATES = {"investigating", "identified", "monitoring"}


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def probe(url, expect):
    """One HTTP GET. Returns (ok, http_code, latency_ms, error)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            body = resp.read(65536).decode("utf-8", "replace")
            code = resp.status
    except urllib.error.HTTPError as e:
        return False, e.code, int((time.monotonic() - start) * 1000), f"HTTP {e.code}"
    except Exception as e:  # timeouts, DNS, TLS
        return False, None, int((time.monotonic() - start) * 1000), type(e).__name__
    latency = int((time.monotonic() - start) * 1000)
    if not (200 <= code < 400):
        return False, code, latency, f"HTTP {code}"
    if expect and expect not in body.replace(" ", ""):
        return False, code, latency, "unexpected response"
    return True, code, latency, None


def check(component):
    """Probe up to 3 times so one network blip is not reported as an outage."""
    result = None
    for attempt in range(3):
        result = probe(component["url"], component.get("expect"))
        if result[0]:
            return result
        time.sleep(5)
    return result


def run_checks(state):
    today = now_utc().strftime("%Y-%m-%d")
    cutoff = (now_utc() - timedelta(days=CONFIG["history_days"])).strftime("%Y-%m-%d")
    for comp in CONFIG["components"]:
        if not comp.get("url"):
            continue
        ok, code, latency, error = check(comp)
        if ok and latency > CONFIG["slow_ms"]:
            measured = "degraded"
        else:
            measured = "operational" if ok else "outage"
        state["checks"][comp["id"]] = {
            "status": measured,
            "code": code,
            "latency_ms": latency,
            "error": error,
            "checked_at": iso(now_utc()),
        }
        days = state["daily"].setdefault(comp["id"], {})
        up, total = days.get(today, [0, 0])
        days[today] = [up + (1 if ok else 0), total + 1]
        for day in [d for d in days if d < cutoff]:
            del days[day]
        print(f"{comp['id']}: {measured} code={code} {latency}ms {error or ''}")
    state["last_run"] = iso(now_utc())


def md_inline(text):
    """Tiny, safe markdown: escape HTML, then **bold**, `code` and [text](url)."""
    out = html.escape(text, quote=False)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"`(.+?)`", r"<code>\1</code>", out)
    out = re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
        r'<a href="\2" rel="noopener">\1</a>',
        out,
    )
    return out


def md_to_html(body):
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", body.strip()) if p.strip()]
    return "".join(
        "<p>" + "<br>".join(md_inline(line) for line in p.splitlines()) + "</p>"
        for p in paragraphs
    )


def parse_incident(path):
    text = path.read_text()
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        raise SystemExit(f"{path.name}: missing --- front matter ---")
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip().strip('"')
    for key in ("title", "status", "started"):
        if not meta.get(key):
            raise SystemExit(f"{path.name}: front matter needs '{key}'")
    status = meta["status"].lower()
    if status not in OPEN_STATES | {"resolved"}:
        raise SystemExit(f"{path.name}: status must be investigating, identified, monitoring or resolved")
    known = {c["id"] for c in CONFIG["components"]}
    components = [c.strip() for c in meta.get("components", "").split(",") if c.strip()]
    for c in components:
        if c not in known:
            raise SystemExit(f"{path.name}: unknown component '{c}' (use {', '.join(sorted(known))})")
    impact = meta.get("impact", "outage").lower()
    if impact not in ("outage", "degraded", "maintenance"):
        raise SystemExit(f"{path.name}: impact must be outage, degraded or maintenance")
    return {
        "id": path.stem,
        "title": meta["title"],
        "status": status,
        "impact": impact,
        "components": components,
        "started": meta["started"],
        "resolved": meta.get("resolved") or None,
        "html": md_to_html(m.group(2)),
        "text": m.group(2).strip(),
    }


def build_incidents():
    incidents = [
        parse_incident(p)
        for p in sorted(INCIDENTS_DIR.glob("*.md"))
        if p.name.lower() != "readme.md" and not p.name.startswith("_")
    ]
    incidents.sort(key=lambda i: i["started"], reverse=True)
    INCIDENTS_FILE.write_text(json.dumps(
        [{k: v for k, v in i.items() if k != "text"} for i in incidents], indent=2) + "\n")
    write_feed(incidents)
    return incidents


def write_feed(incidents):
    site = CONFIG["site"]
    entries = []
    for i in incidents[:50]:
        updated = i["resolved"] or i["started"]
        entries.append(
            "  <entry>\n"
            f"    <title>{html.escape(i['title'])} ({i['status']})</title>\n"
            f"    <id>{site}/#{html.escape(i['id'])}</id>\n"
            f"    <link href=\"{site}/#{html.escape(i['id'])}\"/>\n"
            f"    <updated>{html.escape(updated)}</updated>\n"
            f"    <content type=\"html\">{html.escape(i['html'])}</content>\n"
            "  </entry>\n"
        )
    feed_updated = incidents[0]["resolved"] or incidents[0]["started"] if incidents else "2026-11-13T00:00:00Z"
    FEED_FILE.write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom">\n'
        "  <title>Maritime status</title>\n"
        f"  <id>{site}/</id>\n"
        f"  <link href=\"{site}/\"/>\n"
        f"  <link rel=\"self\" href=\"{site}/feed.xml\"/>\n"
        f"  <updated>{html.escape(feed_updated)}</updated>\n"
        + "".join(entries)
        + "</feed>\n"
    )


def main():
    if STATUS_FILE.exists():
        state = json.loads(STATUS_FILE.read_text())
    else:
        state = {"checks": {}, "daily": {}, "last_run": None}
    if "--no-check" not in sys.argv:
        run_checks(state)
    STATUS_FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    incidents = build_incidents()
    print(f"{len(incidents)} incident(s) built")


if __name__ == "__main__":
    main()
