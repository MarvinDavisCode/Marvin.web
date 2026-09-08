#!/usr/bin/env python3
"""
Source Monitor — Editorial OS, Stage 1
=======================================

Reads every "Active" row in the Airtable **Sources** table, fetches its RSS/
Atom/RDF feed, and creates a new **Candidates** row for any item that isn't
already there (deduplicated by URL). It intentionally does NOT score,
summarize, or draft anything — that's the next stage (relevance scoring +
draft generation). This script's only job is: "what's new out there?"

Designed to run unattended on a daily GitHub Actions schedule. It is
deliberately defensive: a single broken feed, a single malformed entry, or a
single rate-limited request should never take down the whole run.

Environment variables required:
    AIRTABLE_PAT       Airtable Personal Access Token (repo secret)
    AIRTABLE_BASE_ID   Airtable base ID (not sensitive — set as a plain env var)

Usage:
    python automation/scripts/source_monitor.py
"""

import os
import sys
import time
import logging
from datetime import datetime, timezone

import requests
import feedparser
from dateutil import parser as dateparser

# --------------------------------------------------------------------------
# Config — Airtable base / table / field IDs
# --------------------------------------------------------------------------

BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "appGoegWQtI3TmtNW")
PAT = os.environ.get("AIRTABLE_PAT")

SOURCES_TABLE = "tbllrhWY2ExPnf9Gc"
CANDIDATES_TABLE = "tblSP4rl8SCiXeXOE"

# Sources table fields (read from each Active source row) — field IDs, not
# names, so this keeps working even if someone renames a column in the UI.
SRC_FIELD_NAME = "fldJTJpBoGqZ2ujo2"          # Source Name
SRC_FIELD_FEED_URL = "fldw8KVO1nFn2Vos6"      # Feed URL
SRC_FIELD_STATUS = "fldwH2lfXloQYA9ql"        # Status (Active / Needs Follow-up / Blocked)
SRC_FIELD_FOCUS_AREAS = "fldf00VIMGQB293Xm"   # Focus Areas

# Candidates table fields (written for each new item)
CAND_FIELD_TITLE = "fldZkd5g4bNhK8ocW"
CAND_FIELD_SOURCE_LINK = "fld6IB7NC00OlhRVq"
CAND_FIELD_URL = "fldZiWr7lJGOGB5Wn"
CAND_FIELD_PUBLISHED = "fldtmhPa9u6C0KUs6"
CAND_FIELD_FOCUS_AREAS = "fldkyQbpdvzJSHLhW"
CAND_FIELD_SUMMARY = "fldlr69vWrpcIKPMz"
CAND_FIELD_STATUS = "fldFlcPP0hjX6ZpbU"

CAND_STATUS_NEW = "New"

API_ROOT = "https://api.airtable.com/v0"
PAGE_SIZE = 100          # Airtable's max per list-records page
CREATE_BATCH_SIZE = 10   # Airtable's max per create-records call
REQUEST_TIMEOUT = 20     # seconds, per HTTP request
MAX_RETRIES = 4

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("source_monitor")


def _headers():
    return {
        "Authorization": f"Bearer {PAT}",
        "Content-Type": "application/json",
    }


def _request_with_retry(method, url, **kwargs):
    """Wraps requests.request with basic 429/5xx backoff. Never raises for
    a single bad response past MAX_RETRIES — callers decide what to do."""
    delay = 1.5
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.request(method, url, timeout=REQUEST_TIMEOUT, **kwargs)
        except requests.RequestException as exc:
            log.warning("Request error (attempt %d/%d) for %s: %s", attempt, MAX_RETRIES, url, exc)
            time.sleep(delay)
            delay *= 2
            continue

        if resp.status_code == 429 or resp.status_code >= 500:
            log.warning(
                "Airtable returned %s (attempt %d/%d) for %s — backing off %.1fs",
                resp.status_code, attempt, MAX_RETRIES, url, delay,
            )
            time.sleep(delay)
            delay *= 2
            continue

        return resp

    return resp  # last attempt's response, even if it's an error


# --------------------------------------------------------------------------
# Airtable reads
# --------------------------------------------------------------------------

def get_active_sources():
    """Returns a list of dicts: {name, feed_url, focus_areas} for every
    Sources row whose Status is 'Active'. Skips 'Needs Follow-up' and
    'Blocked' sources — those aren't ready for automated fetching yet."""
    sources = []
    url = f"{API_ROOT}/{BASE_ID}/{SOURCES_TABLE}"
    params = {"pageSize": PAGE_SIZE}

    while True:
        resp = _request_with_retry("GET", url, headers=_headers(), params=params)
        if resp.status_code != 200:
            log.error("Failed to list Sources: %s %s", resp.status_code, resp.text[:300])
            break

        payload = resp.json()
        for rec in payload.get("records", []):
            fields = rec.get("fields", {})
            status = fields.get(SRC_FIELD_STATUS)
            if status != "Active":
                continue
            feed_url = fields.get(SRC_FIELD_FEED_URL)
            name = fields.get(SRC_FIELD_NAME, "(unnamed source)")
            if not feed_url:
                log.warning("Source '%s' is Active but has no Feed URL — skipping.", name)
                continue
            sources.append({
                "record_id": rec["id"],
                "name": name,
                "feed_url": feed_url,
                "focus_areas": fields.get(SRC_FIELD_FOCUS_AREAS, []),
            })

        offset = payload.get("offset")
        if not offset:
            break
        params["offset"] = offset

    log.info("Found %d Active source(s).", len(sources))
    return sources


def get_existing_candidate_urls():
    """Returns a set of every URL already present in Candidates, so we never
    create a duplicate row for the same article."""
    existing = set()
    url = f"{API_ROOT}/{BASE_ID}/{CANDIDATES_TABLE}"
    params = {"pageSize": PAGE_SIZE, "fields[]": CAND_FIELD_URL}

    while True:
        resp = _request_with_retry("GET", url, headers=_headers(), params=params)
        if resp.status_code != 200:
            log.error("Failed to list Candidates: %s %s", resp.status_code, resp.text[:300])
            break

        payload = resp.json()
        for rec in payload.get("records", []):
            u = rec.get("fields", {}).get(CAND_FIELD_URL)
            if u:
                existing.add(u.strip())

        offset = payload.get("offset")
        if not offset:
            break
        params["offset"] = offset

    log.info("Loaded %d existing Candidate URL(s) for dedup.", len(existing))
    return existing


# --------------------------------------------------------------------------
# Feed parsing
# --------------------------------------------------------------------------

def parse_feed_entries(source):
    """Parses one source's feed and returns a list of normalized item dicts.
    Never raises — any failure is logged and results in an empty list, so one
    broken feed can't take down the whole run."""
    items = []
    try:
        parsed = feedparser.parse(source["feed_url"])
    except Exception as exc:  # feedparser is generally exception-safe, but just in case
        log.error("feedparser raised for '%s': %s", source["name"], exc)
        return items

    if parsed.bozo and not parsed.entries:
        log.warning(
            "Feed for '%s' looked malformed and had no entries (%s) — skipping.",
            source["name"], getattr(parsed, "bozo_exception", "unknown parse error"),
        )
        return items

    for entry in parsed.entries:
        link = entry.get("link")
        title = entry.get("title")
        if not link or not title:
            continue

        published_iso = None
        for date_field in ("published", "updated", "created"):
            raw_date = entry.get(date_field)
            if raw_date:
                try:
                    dt = dateparser.parse(raw_date)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    published_iso = dt.astimezone(timezone.utc).date().isoformat()
                    break
                except (ValueError, OverflowError):
                    continue

        summary = entry.get("summary", "") or ""
        # Feed summaries are often raw HTML — keep it short and let a human
        # (or the next stage's AI) do real cleanup later.
        summary = summary.strip()
        if len(summary) > 500:
            summary = summary[:497] + "..."

        items.append({
            "title": title.strip(),
            "url": link.strip(),
            "published": published_iso,
            "summary": summary,
        })

    return items


# --------------------------------------------------------------------------
# Airtable writes
# --------------------------------------------------------------------------

def build_candidate_fields(source, item):
    fields = {
        CAND_FIELD_TITLE: item["title"],
        CAND_FIELD_URL: item["url"],
        CAND_FIELD_STATUS: CAND_STATUS_NEW,
        CAND_FIELD_SOURCE_LINK: [source["record_id"]],
    }
    if item["published"]:
        fields[CAND_FIELD_PUBLISHED] = item["published"]
    if item["summary"]:
        fields[CAND_FIELD_SUMMARY] = item["summary"]
    if source["focus_areas"]:
        fields[CAND_FIELD_FOCUS_AREAS] = source["focus_areas"]
    return fields


def create_candidates(records):
    """Creates Candidate records in batches of CREATE_BATCH_SIZE. Returns the
    number successfully created."""
    if not records:
        return 0

    url = f"{API_ROOT}/{BASE_ID}/{CANDIDATES_TABLE}"
    created = 0

    for i in range(0, len(records), CREATE_BATCH_SIZE):
        batch = records[i:i + CREATE_BATCH_SIZE]
        body = {"records": [{"fields": f} for f in batch]}
        resp = _request_with_retry("POST", url, headers=_headers(), json=body)

        if resp.status_code in (200, 201):
            created += len(resp.json().get("records", []))
        else:
            log.error(
                "Failed to create a batch of %d candidate(s): %s %s",
                len(batch), resp.status_code, resp.text[:500],
            )

    return created


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    if not PAT:
        log.error("AIRTABLE_PAT is not set — nothing to do. Add it as a GitHub repo secret.")
        sys.exit(1)

    started = datetime.now(timezone.utc)
    log.info("Source monitor starting at %s UTC", started.isoformat(timespec="seconds"))

    sources = get_active_sources()
    if not sources:
        log.info("No Active sources found — nothing to fetch. Exiting cleanly.")
        return

    existing_urls = get_existing_candidate_urls()

    new_records = []
    total_seen = 0
    per_source_new = {}

    for source in sources:
        entries = parse_feed_entries(source)
        total_seen += len(entries)
        new_for_this_source = 0

        for item in entries:
            if item["url"] in existing_urls:
                continue
            existing_urls.add(item["url"])  # guard against dupes within this same run
            new_records.append(build_candidate_fields(source, item))
            new_for_this_source += 1

        per_source_new[source["name"]] = new_for_this_source
        log.info(
            "%-30s  fetched %3d item(s), %2d new",
            source["name"], len(entries), new_for_this_source,
        )

    created = create_candidates(new_records)

    log.info("-" * 60)
    log.info(
        "Done. Sources checked: %d | Items seen: %d | New candidates created: %d",
        len(sources), total_seen, created,
    )
    if created != len(new_records):
        log.warning(
            "Note: %d new item(s) were identified but only %d were created "
            "(see errors above).", len(new_records), created,
        )


if __name__ == "__main__":
    main()
