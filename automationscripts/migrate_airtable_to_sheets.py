#!/usr/bin/env python3
"""
One-time migration: copies everything in the Airtable "Marvin — Editorial OS"
base into the new Google Sheet, via the Apps Script API (sheet-api.gs).

Run this yourself, locally, the same way you already run publish_approved.py
etc. — it reads your existing AIRTABLE_PAT plus two new env vars for the
Sheet, and never sends your Sheets token anywhere except your own Sheet.

Required environment variables:
    AIRTABLE_PAT       - same token you already use for the other scripts
    SHEETS_API_URL     - the Apps Script Web app URL (ends in /exec)
    SHEETS_API_TOKEN   - the random string you set as the API_TOKEN
                         script property in Api.gs

Optional:
    AIRTABLE_BASE_ID   - defaults to appGoegWQtI3TmtNW (your Editorial OS base)
    DRY_RUN            - set to "1" to fetch from Airtable and print counts
                         *without* writing anything to the Sheet, so you can
                         sanity-check record counts first

Usage:
    export AIRTABLE_PAT="..."
    export SHEETS_API_URL="https://script.google.com/macros/s/XXXX/exec"
    export SHEETS_API_TOKEN="..."
    python migrate_airtable_to_sheets.py            # does the real migration
    DRY_RUN=1 python migrate_airtable_to_sheets.py   # preview only, no writes

Safe to re-run: it always APPENDS new rows, so running it twice will create
duplicates. If a run fails partway through, don't just re-run it — tell me
which tab it got to and we'll figure out the right way to resume instead of
double-writing everything before that point.
"""
import os
import sys
import time
import logging

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s %(message)s",
                     datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger(__name__)

AIRTABLE_PAT = os.environ.get("AIRTABLE_PAT", "")
AIRTABLE_BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "appGoegWQtI3TmtNW")
SHEETS_API_URL = os.environ.get("SHEETS_API_URL", "")
SHEETS_API_TOKEN = os.environ.get("SHEETS_API_TOKEN", "")
DRY_RUN = os.environ.get("DRY_RUN", "") == "1"

AIRTABLE_API_URL = "https://api.airtable.com/v0"

# ---------------------------------------------------------------------------
# Airtable table + field IDs (from the live base, confirmed via the schema
# already documented in the Claude Project doc for this build)
# ---------------------------------------------------------------------------

TAXONOMY_TABLE = "tblVxa7O6y19kiJbz"
TAX_NAME = "fld0YJP2u4qiHlm7Q"
TAX_DESCRIPTION = "fldRkAlHi5T51ekfA"
TAX_SITE_REF = "fld5hjl68mD9oMMHK"

SOURCES_TABLE = "tbllrhWY2ExPnf9Gc"
SRC_NAME = "fldJTJpBoGqZ2ujo2"
SRC_FEED_URL = "fldw8KVO1nFn2Vos6"
SRC_TYPE = "fldek2I6pNHcqmrfA"
SRC_STATUS = "fldwH2lfXloQYA9ql"
SRC_FOCUS_AREAS = "fldf00VIMGQB293Xm"
SRC_NOTES = "fldmEN4FQXluFER8E"

CANDIDATES_TABLE = "tblSP4rl8SCiXeXOE"
CAND_TITLE = "fldZkd5g4bNhK8ocW"
CAND_SOURCE_LINK = "fld6IB7NC00OlhRVq"
CAND_URL = "fldZiWr7lJGOGB5Wn"
CAND_PUBLISHED_DATE = "fldtmhPa9u6C0KUs6"
CAND_RELEVANCE_SCORE = "fldYrYDUmvT2YgRtE"
CAND_FOCUS_AREAS = "fldkyQbpdvzJSHLhW"
CAND_SUMMARY = "fldlr69vWrpcIKPMz"
CAND_STATUS = "fldFlcPP0hjX6ZpbU"

DRAFTS_TABLE = "tblRA7DupHBXo1Fqq"
DRAFT_TITLE = "fldVXmgpzUGj1kWdB"
DRAFT_SUBTITLE = "fldVDynt3pJhLzeas"
DRAFT_RELATED_CANDIDATE_LINK = "fldmrHSphei3yrWKw"
DRAFT_BODY = "fld6accY9ibbxA37f"
DRAFT_FOCUS_AREAS = "fldbzLKWZjb5wExUV"
DRAFT_SOURCES_REFERENCES = "fldOC66mBtlGVER7i"
DRAFT_SEO_TITLE = "fld93t9YWjZ0oV5yu"
DRAFT_META_DESCRIPTION = "fldjm3yzA116AMi7S"
DRAFT_SOCIAL_EXCERPT = "fldnZntw9EIW16h8p"
DRAFT_FACT_CHECK_STATUS = "fldvHpWP6NBPIG1uq"
DRAFT_STATUS = "fldK03CiL62pWHGDV"
DRAFT_TARGET = "fldYsBRNgbvG2Zcj7"
DRAFT_EDITORIAL_NOTES = "fld50HgnHVTxBg8ub"
DRAFT_WEEKLY_NEWSLETTER_LINK = "fldhO1wRCUJ8YjcDm"
DRAFT_GITHUB_PR_URL = "fldTVnI8GFRQkUn21"
DRAFT_FILENAME = "fld20kmWiQmoEoj83"
DRAFT_PUBLISHED_URL = "fldZnMLl4A03sRt8h"
DRAFT_LIVE_AT = "fldPYkdysjkpca282"
DRAFT_LINKEDIN_POST = "fldFI3cT8dSpnP7qn"
DRAFT_FACEBOOK_POST = "fldU5ODJsjm0eczla"

NEWSLETTERS_TABLE = "tblR7xYb6UuMibeif"
NEWS_WEEK_OF = "fldN787tyRphhoLhb"
NEWS_THEME = "fldJQ2UcIxo1GzNMJ"
NEWS_EDITORS_NOTE = "fldOR3U3GiL4sf1BJ"
NEWS_STATUS = "fld77FwNlvdgiC0Rt"
NEWS_COMPILED_BODY = "fldOMrGQaLmA65ZOX"
NEWS_GITHUB_PR_URL = "fldgTm4pbHDgRzG6X"
NEWS_FILENAME = "fldWs6Iv53kujVqGk"
NEWS_PUBLISHED_URL = "fldPG8n9NkAqUy5rW"
NEWS_LIVE_AT = "fld9OSgmM2clTLXJl"
NEWS_LINKEDIN_POST = "fldjr2bAzIMgWYSJh"
NEWS_FACEBOOK_POST = "fld5svsRBgQvR10Cx"


def airtable_list_all(table_id):
    """Fetches every record in an Airtable table, following pagination."""
    records = []
    offset = None
    headers = {"Authorization": f"Bearer {AIRTABLE_PAT}"}
    while True:
        params = {"pageSize": 100, "returnFieldsByFieldId": "true"}
        if offset:
            params["offset"] = offset
        resp = requests.get(f"{AIRTABLE_API_URL}/{AIRTABLE_BASE_ID}/{table_id}",
                             headers=headers, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        records.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break
    return records


def sheets_call(action, tab, **kwargs):
    payload = {"token": SHEETS_API_TOKEN, "action": action, "tab": tab}
    payload.update(kwargs)
    resp = requests.post(SHEETS_API_URL, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(f"Sheets API error on {action}/{tab}: {data.get('error')}")
    return data


def join_multi(value):
    """Airtable multipleSelects fields come back as a list of plain strings
    (confirmed against the raw REST API, not the MCP tool's object-wrapped
    shape) — join them into one comma-separated cell for the Sheet."""
    if not value:
        return ""
    if isinstance(value, list):
        return ", ".join(value)
    return str(value)


def first_link(value):
    """Airtable link fields come back as a list of record IDs — we only
    ever use single links here, so take the first one if present."""
    if isinstance(value, list) and value:
        return value[0]
    return None


def append_row(tab, fields):
    if DRY_RUN:
        return {"Row ID": f"DRYRUN-{tab}-{len(fields)}"}
    result = sheets_call("append", tab, fields=fields)
    time.sleep(0.2)  # be gentle with the Apps Script quota
    return result["row"]


def migrate_taxonomy():
    records = airtable_list_all(TAXONOMY_TABLE)
    log.info("Taxonomy: %d record(s) in Airtable", len(records))
    for rec in records:
        f = rec["fields"]
        append_row("Taxonomy", {
            "Name": f.get(TAX_NAME, ""),
            "Description": f.get(TAX_DESCRIPTION, ""),
            "Site Reference": f.get(TAX_SITE_REF, ""),
        })
    log.info("Taxonomy: done")


def migrate_sources():
    records = airtable_list_all(SOURCES_TABLE)
    log.info("Sources: %d record(s) in Airtable", len(records))
    id_map = {}
    for rec in records:
        f = rec["fields"]
        row = append_row("Sources", {
            "Name": f.get(SRC_NAME, ""),
            "Feed URL": f.get(SRC_FEED_URL, ""),
            "Type": f.get(SRC_TYPE, ""),
            "Status": f.get(SRC_STATUS, ""),
            "Focus Areas": join_multi(f.get(SRC_FOCUS_AREAS)),
            "Notes": f.get(SRC_NOTES, ""),
        })
        id_map[rec["id"]] = row["Row ID"]
    log.info("Sources: done (%d mapped)", len(id_map))
    return id_map


def migrate_newsletters():
    records = airtable_list_all(NEWSLETTERS_TABLE)
    log.info("Weekly Newsletters: %d record(s) in Airtable", len(records))
    id_map = {}
    for rec in records:
        f = rec["fields"]
        row = append_row("Weekly Newsletters", {
            "Week Of": f.get(NEWS_WEEK_OF, ""),
            "Theme": f.get(NEWS_THEME, ""),
            "Editor's Note": f.get(NEWS_EDITORS_NOTE, ""),
            "Status": f.get(NEWS_STATUS, ""),
            "Compiled Body": f.get(NEWS_COMPILED_BODY, ""),
            "Filename": f.get(NEWS_FILENAME, ""),
            "Published URL": f.get(NEWS_PUBLISHED_URL, ""),
            "Live At": f.get(NEWS_LIVE_AT, ""),
            "LinkedIn Post": f.get(NEWS_LINKEDIN_POST, ""),
            "Facebook Post": f.get(NEWS_FACEBOOK_POST, ""),
            "GitHub PR URL": f.get(NEWS_GITHUB_PR_URL, ""),
        })
        id_map[rec["id"]] = row["Row ID"]
    log.info("Weekly Newsletters: done (%d mapped)", len(id_map))
    return id_map


def migrate_candidates(source_id_map):
    records = airtable_list_all(CANDIDATES_TABLE)
    log.info("Candidates: %d record(s) in Airtable", len(records))
    id_map = {}
    skipped_source_links = 0
    for rec in records:
        f = rec["fields"]
        source_airtable_id = first_link(f.get(CAND_SOURCE_LINK))
        source_row_id = source_id_map.get(source_airtable_id, "") if source_airtable_id else ""
        if source_airtable_id and not source_row_id:
            skipped_source_links += 1
        row = append_row("Candidates", {
            "Title": f.get(CAND_TITLE, ""),
            "Source Row ID": source_row_id,
            "URL": f.get(CAND_URL, ""),
            "Published Date": f.get(CAND_PUBLISHED_DATE, ""),
            "Relevance Score": f.get(CAND_RELEVANCE_SCORE, ""),
            "Focus Areas": join_multi(f.get(CAND_FOCUS_AREAS)),
            "Summary": f.get(CAND_SUMMARY, ""),
            "Status": f.get(CAND_STATUS, ""),
        })
        id_map[rec["id"]] = row["Row ID"]
    log.info("Candidates: done (%d mapped, %d had an unresolvable source link)",
              len(id_map), skipped_source_links)
    return id_map


def migrate_drafts(candidate_id_map, newsletter_id_map):
    records = airtable_list_all(DRAFTS_TABLE)
    log.info("Drafts: %d record(s) in Airtable", len(records))
    unresolved_candidate = 0
    unresolved_newsletter = 0
    for rec in records:
        f = rec["fields"]
        cand_airtable_id = first_link(f.get(DRAFT_RELATED_CANDIDATE_LINK))
        cand_row_id = candidate_id_map.get(cand_airtable_id, "") if cand_airtable_id else ""
        if cand_airtable_id and not cand_row_id:
            unresolved_candidate += 1

        news_airtable_id = first_link(f.get(DRAFT_WEEKLY_NEWSLETTER_LINK))
        news_row_id = newsletter_id_map.get(news_airtable_id, "") if news_airtable_id else ""
        if news_airtable_id and not news_row_id:
            unresolved_newsletter += 1

        append_row("Drafts", {
            "Title": f.get(DRAFT_TITLE, ""),
            "Subtitle": f.get(DRAFT_SUBTITLE, ""),
            "Related Candidate Row ID": cand_row_id,
            "Body": f.get(DRAFT_BODY, ""),
            "Focus Areas": join_multi(f.get(DRAFT_FOCUS_AREAS)),
            "Sources References": f.get(DRAFT_SOURCES_REFERENCES, ""),
            "SEO Title": f.get(DRAFT_SEO_TITLE, ""),
            "Meta Description": f.get(DRAFT_META_DESCRIPTION, ""),
            "Social Excerpt": f.get(DRAFT_SOCIAL_EXCERPT, ""),
            "Fact-Check Status": f.get(DRAFT_FACT_CHECK_STATUS, ""),
            "Status": f.get(DRAFT_STATUS, ""),
            "Target": f.get(DRAFT_TARGET, ""),
            "Editorial Notes": f.get(DRAFT_EDITORIAL_NOTES, ""),
            "Weekly Newsletter Row ID": news_row_id,
            "Filename": f.get(DRAFT_FILENAME, ""),
            "Published URL": f.get(DRAFT_PUBLISHED_URL, ""),
            "Live At": f.get(DRAFT_LIVE_AT, ""),
            "LinkedIn Post": f.get(DRAFT_LINKEDIN_POST, ""),
            "Facebook Post": f.get(DRAFT_FACEBOOK_POST, ""),
            "GitHub PR URL": f.get(DRAFT_GITHUB_PR_URL, ""),
        })
    log.info("Drafts: done (%d had an unresolvable candidate link, "
              "%d had an unresolvable newsletter link)",
              unresolved_candidate, unresolved_newsletter)


def main():
    missing = [name for name, val in [
        ("AIRTABLE_PAT", AIRTABLE_PAT),
        ("SHEETS_API_URL", SHEETS_API_URL),
        ("SHEETS_API_TOKEN", SHEETS_API_TOKEN),
    ] if not val and not DRY_RUN]
    if missing and not DRY_RUN:
        log.error("Missing required environment variable(s): %s", ", ".join(missing))
        sys.exit(1)
    if not AIRTABLE_PAT:
        log.error("AIRTABLE_PAT is required even for a dry run (we still read from Airtable).")
        sys.exit(1)

    if DRY_RUN:
        log.info("DRY RUN — fetching from Airtable only, nothing will be written to the Sheet.")

    log.info("Migration starting (base: %s)", AIRTABLE_BASE_ID)
    log.info("-" * 60)

    migrate_taxonomy()
    source_map = migrate_sources()
    newsletter_map = migrate_newsletters()
    candidate_map = migrate_candidates(source_map)
    migrate_drafts(candidate_map, newsletter_map)

    log.info("-" * 60)
    if DRY_RUN:
        log.info("Dry run complete — re-run without DRY_RUN=1 to actually write to the Sheet.")
    else:
        log.info("Migration complete. Check your Sheet's tabs to confirm everything landed.")


if __name__ == "__main__":
    main()
