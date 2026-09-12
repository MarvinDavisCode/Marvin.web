#!/usr/bin/env python3
"""
Weekly Newsletter — Editorial OS, Stage 4
===========================================

Once a week, compiles the content for the next Afrinex issue — published on
the site's Afrinex section (not emailed) once Marvin approves it — made up
of:

  1. "This week on the blog" — Drafts with Target "Blog" that already have a
     GitHub PR URL (i.e. Stage 3 has published or opened a PR for them) and
     haven't been recapped in a previous issue yet.
  2. "Worth a quick read" — Candidates with Status "Newsletter Candidate"
     (Stage 2's "relevant, but not a full post" verdict) that haven't been
     turned into a blurb yet. Each gets a short (2-3 sentence) blurb written
     by Claude, strictly from the Candidate's own title/source/summary —
     same no-invented-facts rule as Stage 2's blog drafts.

Every included item becomes (or already is) a row in the Drafts table, and
all of them get linked from one new Weekly Newsletters row for the week.
The full compiled text is written into that row's "Compiled Body" field —
for Marvin to read, add his own Editor's Note to (in the Editor's Note
field), and then move to Status "Approved" himself. Nothing goes anywhere
near the live site until he does that; a separate script (part of Stage 3,
publish_approved.py) is what actually turns an Approved issue into a real
page once he's approved it, the same way an Approved Draft becomes a blog
post.

This script no longer sends or drafts anything in an email service — the
Afrinex "newsletter" is now a page on the site, not an email. See
automation/README.md for the full Stage 3/4 flow.

Environment variables required:
    AIRTABLE_PAT          Airtable Personal Access Token (repo secret)
    ANTHROPIC_API_KEY     Anthropic API key (repo secret)

Optional environment variables:
    AIRTABLE_BASE_ID       Airtable base ID (default: the live base)
    NEWSLETTER_MODEL       Model used to write each blurb (default: claude-sonnet-5)
    MAX_MENTIONS_PER_RUN   Safety cap on how many new blurbs get drafted in
                            one run (default: 15)
    SITE_BASE_URL          Base URL of the live site, e.g.
                            "https://marvindarvis.com/" — used to link the
                            "This week on the blog" recap to the Insights
                            page. If unset, the recap mentions the post by
                            title without a link (see automation/README.md).

Usage:
    python automation/scripts/weekly_newsletter.py
"""

import os
import sys
import json
import time
import logging
from datetime import datetime, timedelta, timezone

import requests

# --------------------------------------------------------------------------
# Config — Airtable base / table / field IDs
# --------------------------------------------------------------------------

BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "appGoegWQtI3TmtNW")
AIRTABLE_PAT = os.environ.get("AIRTABLE_PAT")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

NEWSLETTER_MODEL = os.environ.get("NEWSLETTER_MODEL", "claude-sonnet-5")
MAX_MENTIONS_PER_RUN = int(os.environ.get("MAX_MENTIONS_PER_RUN", "15"))
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "").strip()

SOURCES_TABLE = "tbllrhWY2ExPnf9Gc"
CANDIDATES_TABLE = "tblSP4rl8SCiXeXOE"
DRAFTS_TABLE = "tblRA7DupHBXo1Fqq"
NEWSLETTERS_TABLE = "tblR7xYb6UuMibeif"

# Sources fields (just enough to label a candidate's origin)
SRC_FIELD_NAME = "fldJTJpBoGqZ2ujo2"

# Candidates fields
CAND_FIELD_TITLE = "fldZkd5g4bNhK8ocW"
CAND_FIELD_SOURCE_LINK = "fld6IB7NC00OlhRVq"
CAND_FIELD_URL = "fldZiWr7lJGOGB5Wn"
CAND_FIELD_PUBLISHED = "fldtmhPa9u6C0KUs6"
CAND_FIELD_FOCUS_AREAS = "fldkyQbpdvzJSHLhW"
CAND_FIELD_SUMMARY = "fldlr69vWrpcIKPMz"
CAND_FIELD_STATUS = "fldFlcPP0hjX6ZpbU"

CAND_STATUS_NEWSLETTER_CANDIDATE = "Newsletter Candidate"

# Drafts fields
DRAFT_FIELD_TITLE = "fldVXmgpzUGj1kWdB"
DRAFT_FIELD_RELATED_CANDIDATE = "fldmrHSphei3yrWKw"   # link -> Candidates
DRAFT_FIELD_BODY = "fld6accY9ibbxA37f"
DRAFT_FIELD_FOCUS_AREAS = "fldbzLKWZjb5wExUV"
DRAFT_FIELD_SOURCES_REFERENCES = "fldOC66mBtlGVER7i"  # plain text citation
DRAFT_FIELD_FACT_CHECK_STATUS = "fldvHpWP6NBPIG1uq"
DRAFT_FIELD_STATUS = "fldK03CiL62pWHGDV"
DRAFT_FIELD_TARGET = "fldYsBRNgbvG2Zcj7"
DRAFT_FIELD_EDITORIAL_NOTES = "fld50HgnHVTxBg8ub"
DRAFT_FIELD_WEEKLY_NEWSLETTERS = "fldhO1wRCUJ8YjcDm"  # link -> Weekly Newsletters
DRAFT_FIELD_GITHUB_PR_URL = "fldTVnI8GFRQkUn21"

DRAFT_FACT_CHECK_NOT_STARTED = "Not Started"
DRAFT_STATUS_NEEDS_REVIEW = "Needs Review"
DRAFT_TARGET_BLOG = "Blog"
DRAFT_TARGET_NEWSLETTER = "Newsletter"

# Weekly Newsletters fields
NEWS_FIELD_WEEK_OF = "fldN787tyRphhoLhb"
NEWS_FIELD_THEME = "fldJQ2UcIxo1GzNMJ"
NEWS_FIELD_INCLUDED_DRAFTS = "fldEjhrKi01f6M9MV"      # link -> Drafts
NEWS_FIELD_EDITORS_NOTE = "fldOR3U3GiL4sf1BJ"
NEWS_FIELD_STATUS = "fld77FwNlvdgiC0Rt"
NEWS_FIELD_COMPILED_BODY = "fldOMrGQaLmA65ZOX"         # full issue text, for Marvin's review

NEWS_STATUS_NEEDS_REVIEW = "Needs Review"

EDITORS_NOTE_PLACEHOLDER = (
    "[Editor's note — add your own take on the week before approving this "
    "for publish. This line is just a placeholder; replace it in this "
    "Weekly Newsletter row's Editor's Note field, then set Status to "
    "'Approved' when you're ready for it to go live on the Afrinex page.]"
)

AIRTABLE_API_ROOT = "https://api.airtable.com/v0"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

PAGE_SIZE = 100
REQUEST_TIMEOUT = 30
MAX_RETRIES = 4

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("weekly_newsletter")


# --------------------------------------------------------------------------
# HTTP helpers — shared retry/backoff wrapper for Airtable + Anthropic
# --------------------------------------------------------------------------

def _request_with_retry(method, url, **kwargs):
    """Wraps requests.request with basic 429/5xx backoff. Returns the final
    response even if it's still an error after MAX_RETRIES — callers decide
    what to do with a non-2xx response."""
    delay = 1.5
    resp = None
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
                "%s returned %s (attempt %d/%d) — backing off %.1fs",
                url, resp.status_code, attempt, MAX_RETRIES, delay,
            )
            time.sleep(delay)
            delay *= 2
            continue

        return resp

    return resp


def _airtable_headers():
    return {"Authorization": f"Bearer {AIRTABLE_PAT}", "Content-Type": "application/json"}


def _anthropic_headers():
    return {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }


# --------------------------------------------------------------------------
# Airtable reads
# --------------------------------------------------------------------------

def fetch_all_records(table_id, extra_params=None):
    """Paginates through every record in a table."""
    records = []
    url = f"{AIRTABLE_API_ROOT}/{BASE_ID}/{table_id}"
    # returnFieldsByFieldId is essential: without it, Airtable keys the
    # returned `fields` object by field NAME, but every constant in this
    # script is a field ID — every .get() against a fetched record would
    # otherwise silently return None. (This exact bug bit Stages 1-3 before
    # it was caught and fixed — see automation/README.md Troubleshooting.)
    params = {"pageSize": PAGE_SIZE, "returnFieldsByFieldId": "true"}
    if extra_params:
        params.update(extra_params)

    while True:
        resp = _request_with_retry("GET", url, headers=_airtable_headers(), params=params)
        if resp is None or resp.status_code != 200:
            log.error("Failed to list %s: %s", table_id, getattr(resp, "text", "no response")[:300])
            break

        payload = resp.json()
        records.extend(payload.get("records", []))

        offset = payload.get("offset")
        if not offset:
            break
        params["offset"] = offset

    return records


def fetch_sources_map():
    """Returns dict: record_id -> source name."""
    records = fetch_all_records(SOURCES_TABLE)
    out = {}
    for rec in records:
        out[rec["id"]] = rec.get("fields", {}).get(SRC_FIELD_NAME, "Unknown source")
    return out


def fetch_newsletter_candidates():
    """Every Candidates row with Status == 'Newsletter Candidate'."""
    formula = f'{{{CAND_FIELD_STATUS}}} = "{CAND_STATUS_NEWSLETTER_CANDIDATE}"'
    return fetch_all_records(CANDIDATES_TABLE, extra_params={"filterByFormula": formula})


def fetch_drafts_for_dedup():
    """One pass over the whole Drafts table, used for two things at once:
      - which Candidates already have a Newsletter-target blurb Draft
        (so we never draft the same mention twice)
      - which Blog-target Drafts have a GitHub PR URL (Stage 3 has acted on
        them) but haven't been linked into any Weekly Newsletter yet (so we
        recap each published/PR'd post exactly once).
    Returns (covered_candidate_ids: set, recap_blog_drafts: list of records).
    """
    all_drafts = fetch_all_records(DRAFTS_TABLE)

    covered_candidate_ids = set()
    recap_blog_drafts = []

    for rec in all_drafts:
        fields = rec.get("fields", {})
        target = fields.get(DRAFT_FIELD_TARGET)

        if target == DRAFT_TARGET_NEWSLETTER:
            related = fields.get(DRAFT_FIELD_RELATED_CANDIDATE) or []
            covered_candidate_ids.update(related)

        elif target == DRAFT_TARGET_BLOG:
            has_pr = bool(fields.get(DRAFT_FIELD_GITHUB_PR_URL))
            already_recapped = bool(fields.get(DRAFT_FIELD_WEEKLY_NEWSLETTERS))
            if has_pr and not already_recapped:
                recap_blog_drafts.append(rec)

    return covered_candidate_ids, recap_blog_drafts


# --------------------------------------------------------------------------
# Airtable writes
# --------------------------------------------------------------------------

def create_draft(fields):
    url = f"{AIRTABLE_API_ROOT}/{BASE_ID}/{DRAFTS_TABLE}"
    # typecast=True: this Draft's Focus Areas value is copied straight from
    # the source Candidate, which may carry a Taxonomy-derived tag name that
    # isn't yet a choice on this field (e.g. right after Marvin renames/adds
    # a Taxonomy entry) — without typecast the whole write would fail.
    resp = _request_with_retry(
        "POST", url, headers=_airtable_headers(),
        json={"records": [{"fields": fields}], "typecast": True},
    )
    if resp is None or resp.status_code not in (200, 201):
        log.error("Failed to create newsletter Draft: %s", getattr(resp, "text", "no response")[:300])
        return None
    return resp.json()["records"][0]["id"]


def create_weekly_newsletter(fields):
    url = f"{AIRTABLE_API_ROOT}/{BASE_ID}/{NEWSLETTERS_TABLE}"
    resp = _request_with_retry(
        "POST", url, headers=_airtable_headers(),
        json={"records": [{"fields": fields}], "typecast": True},
    )
    if resp is None or resp.status_code not in (200, 201):
        log.error("Failed to create Weekly Newsletter row: %s", getattr(resp, "text", "no response")[:300])
        return None
    return resp.json()["records"][0]["id"]


# --------------------------------------------------------------------------
# Claude call — write one short newsletter blurb
# --------------------------------------------------------------------------

BLURB_SYSTEM_PROMPT = """You are writing a short mention for Marvin Davis Odhiambo's weekly \
Afrinex community issue, published on his site. Marvin is a psychologist, Founder of Afrinex, \
and Behavioral Health Innovator; his audience is practitioners, educators, researchers, and \
program teams working in evidence-based mental health practice, AI in mental health, \
implementation science, clinical and cognitive neuroscience, and human-centered, scalable \
mental health innovation, and (often) resource-constrained settings including Africa/Kenya.

This item was already judged relevant enough for a brief mention (not a full blog post). Write a \
short mention about it.

Ground rules — follow these strictly:
- Base the mention ONLY on the title, source, and summary given below. Do not invent statistics, \
quotes, study findings, or any specific detail that isn't present in that material.
- Length: 2-3 short sentences. No headers, no markdown formatting.
- Tone: grounded, plain-spoken, practical — matching a licensed mental health professional writing \
to peers, not marketing copy.
- Make clear why this is worth a reader's 30 seconds, without overclaiming what the source material \
actually shows.

Respond with ONLY a single JSON object with exactly these keys:
- blurb_title: a short label for this mention (under 70 characters)
- blurb: the 2-3 sentence mention itself, plain text
- editorial_notes: anything Marvin should double check before approving this for publish, or "None" if nothing stands out

No markdown formatting, no code fences, no extra commentary before or after the JSON."""


def _call_claude(system_prompt, user_prompt, max_tokens=500):
    body = {
        "model": NEWSLETTER_MODEL,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    resp = _request_with_retry("POST", ANTHROPIC_API_URL, headers=_anthropic_headers(), json=body)
    if resp is None or resp.status_code != 200:
        log.error("Claude API call failed (%s): %s", NEWSLETTER_MODEL, getattr(resp, "text", "no response")[:500])
        return None
    try:
        payload = resp.json()
        # Don't assume content[0] is the text block — the API can put a
        # "thinking" block first (seen in practice with claude-sonnet-5,
        # even without opting into extended thinking), which would silently
        # break a naive content[0]["text"] read. Concatenate every "text"
        # block instead, wherever it falls in the list.
        content_blocks = payload.get("content", [])
        text = "".join(
            block.get("text", "") for block in content_blocks if block.get("type") == "text"
        )
        if not text:
            raise ValueError("no text content block in response")
        return text
    except (KeyError, IndexError, ValueError) as exc:
        log.error("Unexpected Claude response shape: %s | body: %s", exc, resp.text[:500])
        return None


def _parse_json_response(raw_text):
    if raw_text is None:
        return None
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        log.error("Could not parse JSON from Claude response: %s | raw: %s", exc, raw_text[:500])
        return None


def build_candidate_text(fields, source_name):
    return (
        f"Title: {fields.get(CAND_FIELD_TITLE, '(untitled)')}\n"
        f"Source: {source_name}\n"
        f"Published: {fields.get(CAND_FIELD_PUBLISHED, 'unknown date')}\n"
        f"URL: {fields.get(CAND_FIELD_URL, '')}\n"
        f"Summary: {fields.get(CAND_FIELD_SUMMARY, '(no summary available)')}"
    )


def write_blurb(candidate_text):
    raw = _call_claude(BLURB_SYSTEM_PROMPT, candidate_text)
    return _parse_json_response(raw)


# --------------------------------------------------------------------------
# Per-mention processing (isolated so one bad item can't block the others)
# --------------------------------------------------------------------------

def process_candidate_mention(rec, sources_map):
    """Writes a blurb for one Newsletter Candidate and creates its Draft
    row. Returns a dict with everything needed for the compiled issue, or
    None if this candidate couldn't be processed this run (it stays
    uncovered and will be picked up automatically on a future run)."""
    fields = rec.get("fields", {})
    title = fields.get(CAND_FIELD_TITLE, "(untitled)")
    source_id_list = fields.get(CAND_FIELD_SOURCE_LINK) or []
    source_name = sources_map.get(source_id_list[0], "Unknown source") if source_id_list else "Unknown source"
    url = fields.get(CAND_FIELD_URL, "")
    published = fields.get(CAND_FIELD_PUBLISHED, "unknown date")
    focus_areas = fields.get(CAND_FIELD_FOCUS_AREAS, [])

    candidate_text = build_candidate_text(fields, source_name)
    blurb_result = write_blurb(candidate_text)

    required_keys = {"blurb_title", "blurb", "editorial_notes"}
    if not isinstance(blurb_result, dict) or not required_keys.issubset(blurb_result.keys()):
        log.warning("Blurb-writing failed for '%s' — will retry on a future run.", title)
        return None

    sources_references = f"{source_name}. \"{title}.\" {published}. {url}"

    draft_fields = {
        DRAFT_FIELD_TITLE: blurb_result["blurb_title"],
        DRAFT_FIELD_RELATED_CANDIDATE: [rec["id"]],
        DRAFT_FIELD_BODY: blurb_result["blurb"],
        DRAFT_FIELD_FOCUS_AREAS: focus_areas,
        DRAFT_FIELD_SOURCES_REFERENCES: sources_references,
        DRAFT_FIELD_FACT_CHECK_STATUS: DRAFT_FACT_CHECK_NOT_STARTED,
        DRAFT_FIELD_STATUS: DRAFT_STATUS_NEEDS_REVIEW,
        DRAFT_FIELD_TARGET: DRAFT_TARGET_NEWSLETTER,
        DRAFT_FIELD_EDITORIAL_NOTES: blurb_result["editorial_notes"],
    }

    draft_id = create_draft(draft_fields)
    if not draft_id:
        log.warning("Draft write failed for '%s' — will retry on a future run.", title)
        return None

    log.info("%-60s  blurb drafted", title[:60])
    return {
        "draft_id": draft_id,
        "title": blurb_result["blurb_title"],
        "blurb": blurb_result["blurb"],
        "url": url,
    }


# --------------------------------------------------------------------------
# Issue compilation
# --------------------------------------------------------------------------

def compile_issue_body(week_of, recap_items, mention_items):
    """Builds the full text of this week's Afrinex issue. This is what gets
    published as the on-site page once Marvin approves it — not emailed."""
    lines = []
    lines.append(EDITORS_NOTE_PLACEHOLDER)
    lines.append("")

    if recap_items:
        lines.append("This week on the blog")
        lines.append("")
        for item in recap_items:
            if SITE_BASE_URL:
                insights_url = SITE_BASE_URL.rstrip("/") + "/insights.html"
                lines.append(f"- {item['title']} — read it on the Insights page: {insights_url}")
            else:
                lines.append(f"- {item['title']} — now live on the Insights page.")
        lines.append("")

    if mention_items:
        lines.append("Worth a quick read")
        lines.append("")
        for item in mention_items:
            link = f" ({item['url']})" if item["url"] else ""
            lines.append(f"- {item['title']} — {item['blurb']}{link}")
        lines.append("")

    lines.append("---")
    lines.append(
        "This issue was AI-assisted from published sources and reviewed by Marvin before publishing."
    )

    return "\n".join(lines)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    if not AIRTABLE_PAT:
        log.error("AIRTABLE_PAT is not set — add it as a GitHub repo secret.")
        sys.exit(1)
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY is not set — add it as a GitHub repo secret.")
        sys.exit(1)

    today = datetime.now(timezone.utc).date()
    week_of = (today - timedelta(days=today.weekday())).isoformat()  # Monday of this week

    log.info("Weekly newsletter compiler starting (model: %s, week of: %s, cap: %d)",
              NEWSLETTER_MODEL, week_of, MAX_MENTIONS_PER_RUN)

    covered_candidate_ids, recap_blog_drafts = fetch_drafts_for_dedup()

    all_mention_candidates = fetch_newsletter_candidates()
    new_mention_candidates = [
        rec for rec in all_mention_candidates if rec["id"] not in covered_candidate_ids
    ][:MAX_MENTIONS_PER_RUN]

    if not recap_blog_drafts and not new_mention_candidates:
        log.info("Nothing new for this week's issue — nothing to do. Exiting cleanly.")
        return

    log.info(
        "Found %d blog post(s) to recap and %d new mention(s) to draft (cap %d).",
        len(recap_blog_drafts), len(new_mention_candidates), MAX_MENTIONS_PER_RUN,
    )

    sources_map = fetch_sources_map()

    included_draft_ids = []
    recap_items = []
    for rec in recap_blog_drafts:
        fields = rec.get("fields", {})
        included_draft_ids.append(rec["id"])
        recap_items.append({"title": fields.get(DRAFT_FIELD_TITLE, "(untitled post)")})

    mention_items = []
    mentions_drafted = 0
    mentions_failed = 0
    for rec in new_mention_candidates:
        title = rec.get("fields", {}).get(CAND_FIELD_TITLE, "(untitled)")
        try:
            result = process_candidate_mention(rec, sources_map)
        except Exception as exc:
            # A malformed model response or an Airtable write hiccup must
            # never take down the rest of the run — log it and move on.
            log.error("Unexpected error drafting a mention for '%s': %s", title, exc)
            result = None

        if result:
            included_draft_ids.append(result["draft_id"])
            mention_items.append(result)
            mentions_drafted += 1
        else:
            mentions_failed += 1

    if not included_draft_ids:
        log.info("Nothing could be drafted this run (see warnings above) — skipping issue creation.")
        return

    issue_body = compile_issue_body(week_of, recap_items, mention_items)

    newsletter_fields = {
        NEWS_FIELD_WEEK_OF: week_of,
        NEWS_FIELD_INCLUDED_DRAFTS: included_draft_ids,
        NEWS_FIELD_EDITORS_NOTE: "",
        NEWS_FIELD_STATUS: NEWS_STATUS_NEEDS_REVIEW,
        NEWS_FIELD_COMPILED_BODY: issue_body,
    }

    newsletter_id = create_weekly_newsletter(newsletter_fields)

    log.info("-" * 60)
    if newsletter_id:
        log.info(
            "Done. Weekly Newsletter row created (week of %s) — %d blog recap(s), %d new mention(s) "
            "drafted, %d mention(s) failed and will retry next run.",
            week_of, len(recap_items), mentions_drafted, mentions_failed,
        )
        log.info(
            "Nothing is published yet — read the Compiled Body field in Airtable, add an Editor's "
            "Note if you want one, and set Status to 'Approved' when ready. The publish-approved "
            "workflow will then turn it into a real page on the Afrinex section and open a PR."
        )
    else:
        log.error(
            "Weekly Newsletter row failed to save, even though %d Draft(s) were created — those "
            "Drafts are not lost, but you'll need to create the Weekly Newsletters row by hand and "
            "link them (IDs logged above as each was created).", len(included_draft_ids),
        )


if __name__ == "__main__":
    main()
