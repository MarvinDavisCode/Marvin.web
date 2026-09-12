#!/usr/bin/env python3
"""
On-Publish Notify — Editorial OS, Stage 3.5
==============================================

Runs on every push to the default branch. publish_approved.py (Stage 3)
only opens pull requests — it has no way of knowing when Marvin actually
merges one. This script is what closes that loop: it looks at every Draft
and Weekly Newsletters row that has a GitHub PR URL but no "Live At" date
yet, checks whether that PR is actually merged, and if so:

  1. Reads the real, live HTML file from this checkout (we're running
     after a push to main, so the merged file is right here on disk).
  2. Asks Claude to draft a LinkedIn version and a Facebook version of it,
     based strictly on that published text.
  3. Writes Published URL, Live At, LinkedIn Post, and Facebook Post back
     to the Airtable row.

Writing "Live At" is what triggers Airtable's own automation (built once,
no extra secrets needed) that emails Marvin the live link plus both social
drafts, for him to review and post himself. Nothing here posts anything to
LinkedIn or Facebook automatically, and nothing here re-touches the site —
this script only reads files and writes to Airtable.

Idempotent by design: a row is only considered while "Live At" is blank,
and merge status is checked fresh against the GitHub API every run, so a
row whose PR isn't merged yet is simply skipped and picked up on a later
push.

Environment variables required:
    AIRTABLE_PAT       Airtable Personal Access Token (repo secret)
    ANTHROPIC_API_KEY  Anthropic API key (repo secret)
    GITHUB_TOKEN       Provided automatically by GitHub Actions
    GITHUB_REPOSITORY  "owner/repo" — set automatically by Actions

Optional environment variables:
    AIRTABLE_BASE_ID   Airtable base ID (default: the live base)
    GITHUB_API_URL     Default: https://api.github.com
    SITE_BASE_URL      Base URL of the live site, e.g.
                        "https://marvindarvis.com/" — used to build the
                        Published URL and to link to it from the social
                        drafts. If unset, Published URL is left blank and
                        the social drafts omit the link (see
                        automation/README.md).
    SOCIAL_MODEL       Model used to draft social copy (default: claude-sonnet-5)

Usage (must be run from the repo root, checked out at the current main):
    python automation/scripts/on_publish_notify.py
"""

import os
import re
import sys
import json
import time
import logging
from datetime import datetime, timezone

import requests

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "appGoegWQtI3TmtNW")
AIRTABLE_PAT = os.environ.get("AIRTABLE_PAT")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY")
GITHUB_API_URL = os.environ.get("GITHUB_API_URL", "https://api.github.com")
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "").strip()
SOCIAL_MODEL = os.environ.get("SOCIAL_MODEL", "claude-sonnet-5")

DRAFTS_TABLE = "tblRA7DupHBXo1Fqq"
NEWSLETTERS_TABLE = "tblR7xYb6UuMibeif"

DRAFT_FIELD_TITLE = "fldVXmgpzUGj1kWdB"
DRAFT_FIELD_GITHUB_PR_URL = "fldTVnI8GFRQkUn21"
DRAFT_FIELD_FILENAME = "fld20kmWiQmoEoj83"
DRAFT_FIELD_PUBLISHED_URL = "fldZnMLl4A03sRt8h"
DRAFT_FIELD_LIVE_AT = "fldPYkdysjkpca282"
DRAFT_FIELD_LINKEDIN_POST = "fldFI3cT8dSpnP7qn"
DRAFT_FIELD_FACEBOOK_POST = "fldU5ODJsjm0eczla"

NEWS_FIELD_WEEK_OF = "fldN787tyRphhoLhb"
NEWS_FIELD_GITHUB_PR_URL = "fldgTm4pbHDgRzG6X"
NEWS_FIELD_FILENAME = "fldWs6Iv53kujVqGk"
NEWS_FIELD_PUBLISHED_URL = "fldPG8n9NkAqUy5rW"
NEWS_FIELD_LIVE_AT = "fld9OSgmM2clTLXJl"
NEWS_FIELD_LINKEDIN_POST = "fldjr2bAzIMgWYSJh"
NEWS_FIELD_FACEBOOK_POST = "fld5svsRBgQvR10Cx"

AIRTABLE_API_ROOT = "https://api.airtable.com/v0"
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

SITE_ROOT = "."
PAGE_SIZE = 100
REQUEST_TIMEOUT = 30
MAX_RETRIES = 4

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("on_publish_notify")


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------

def _request_with_retry(method, url, **kwargs):
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
            log.warning("%s returned %s (attempt %d/%d) — backing off %.1fs",
                        url, resp.status_code, attempt, MAX_RETRIES, delay)
            time.sleep(delay)
            delay *= 2
            continue
        return resp
    return resp


def _airtable_headers():
    return {"Authorization": f"Bearer {AIRTABLE_PAT}", "Content-Type": "application/json"}


def _github_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _anthropic_headers():
    return {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }


# --------------------------------------------------------------------------
# Airtable reads/writes
# --------------------------------------------------------------------------

def fetch_pending(table_id, pr_url_field, live_at_field):
    """Rows with a GitHub PR URL set but Live At still blank — i.e.
    published (a PR was opened) but not yet confirmed merged."""
    records = []
    url = f"{AIRTABLE_API_ROOT}/{BASE_ID}/{table_id}"
    formula = f'AND(NOT({{{pr_url_field}}}=""), {{{live_at_field}}}="")'
    params = {"pageSize": PAGE_SIZE, "returnFieldsByFieldId": "true", "filterByFormula": formula}
    while True:
        resp = _request_with_retry("GET", url, headers=_airtable_headers(), params=params)
        if resp is None or resp.status_code != 200:
            log.error("Failed to list pending rows in %s: %s", table_id, getattr(resp, "text", "")[:300])
            break
        payload = resp.json()
        records.extend(payload.get("records", []))
        offset = payload.get("offset")
        if not offset:
            break
        params["offset"] = offset
    return records


def update_record(table_id, record_id, fields):
    url = f"{AIRTABLE_API_ROOT}/{BASE_ID}/{table_id}/{record_id}"
    resp = _request_with_retry("PATCH", url, headers=_airtable_headers(), json={"fields": fields})
    if resp is None or resp.status_code != 200:
        log.error("Failed to update %s/%s: %s", table_id, record_id, getattr(resp, "text", "")[:300])
        return False
    return True


# --------------------------------------------------------------------------
# GitHub — merge check
# --------------------------------------------------------------------------

PR_NUMBER_RE = re.compile(r"/pull/(\d+)")


def pr_is_merged(pr_url):
    match = PR_NUMBER_RE.search(pr_url or "")
    if not match:
        log.warning("Could not parse a PR number out of '%s' — skipping.", pr_url)
        return False
    number = match.group(1)
    url = f"{GITHUB_API_URL}/repos/{GITHUB_REPOSITORY}/pulls/{number}"
    resp = _request_with_retry("GET", url, headers=_github_headers())
    if resp is None or resp.status_code != 200:
        log.warning("Could not check PR #%s status: %s", number, getattr(resp, "text", "")[:200])
        return False
    return bool(resp.json().get("merged"))


# --------------------------------------------------------------------------
# Plain-text extraction from a rendered page
# --------------------------------------------------------------------------

TAG_RE = re.compile(r"<[^>]+>")
MAIN_RE = re.compile(r"<main[^>]*>(.*?)</main>", re.DOTALL | re.IGNORECASE)


def extract_plain_text(filename, max_chars=6000):
    path = os.path.join(SITE_ROOT, filename)
    try:
        with open(path, "r", encoding="utf-8") as f:
            html_content = f.read()
    except OSError as exc:
        log.warning("Could not read %s to extract text: %s", filename, exc)
        return ""

    match = MAIN_RE.search(html_content)
    section = match.group(1) if match else html_content
    text = TAG_RE.sub(" ", section)
    text = _html_unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()[:max_chars]


def _html_unescape(text):
    import html as html_module
    return html_module.unescape(text)


# --------------------------------------------------------------------------
# Claude call — draft LinkedIn + Facebook versions
# --------------------------------------------------------------------------

SOCIAL_SYSTEM_PROMPT = """You are drafting social media posts for Marvin Davis Odhiambo, a \
psychologist, Founder of Afrinex, and Behavioral Health Innovator, to share something he just \
published on his own site. He will review and edit these himself before posting — you are \
producing a first draft, not a final post.

Ground rules — follow these strictly:
- Base both drafts ONLY on the published text given below. Do not invent statistics, quotes, or \
claims not present in that text.
- LinkedIn draft: a professional, reflective register suited to peers in mental health, \
implementation science, and AI-in-health — roughly 100-200 words. It's fine to end with a short, \
genuine question or invitation to discuss.
- Facebook draft: warmer and more conversational, suited to a broader community audience — \
roughly 60-120 words.
- Neither draft should read like marketing copy or use more than 2-3 hashtags (only if they add \
real discoverability, e.g. #MentalHealth — never force them).
- If a live URL is given below, include it naturally in both drafts as the way to read more.
- Do not fabricate engagement bait ("You won't believe...") or false urgency.

Respond with ONLY a single JSON object with exactly these keys:
- linkedin_post: the LinkedIn draft, plain text
- facebook_post: the Facebook draft, plain text

No markdown formatting, no code fences, no extra commentary before or after the JSON."""


def _call_claude(user_prompt, max_tokens=900):
    body = {
        "model": SOCIAL_MODEL,
        "max_tokens": max_tokens,
        "system": SOCIAL_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    resp = _request_with_retry("POST", ANTHROPIC_API_URL, headers=_anthropic_headers(), json=body)
    if resp is None or resp.status_code != 200:
        log.error("Claude API call failed (%s): %s", SOCIAL_MODEL, getattr(resp, "text", "no response")[:500])
        return None
    try:
        payload = resp.json()
        # Don't assume content[0] is the text block — see automation/README.md
        # Troubleshooting, "Claude response shape" for why this matters.
        content_blocks = payload.get("content", [])
        text = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
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


def draft_social_copy(title, plain_text, live_url):
    prompt_lines = [f"Title: {title}", ""]
    if live_url:
        prompt_lines.append(f"Live URL: {live_url}")
        prompt_lines.append("")
    prompt_lines.append("Published text:")
    prompt_lines.append(plain_text or "(no text could be extracted — draft only from the title)")
    raw = _call_claude("\n".join(prompt_lines))
    result = _parse_json_response(raw)
    if not isinstance(result, dict) or not {"linkedin_post", "facebook_post"}.issubset(result.keys()):
        return None
    return result


# --------------------------------------------------------------------------
# Per-row processing
# --------------------------------------------------------------------------

def process_row(table_id, rec, title, pr_url_field, filename_field, published_url_field,
                 live_at_field, linkedin_field, facebook_field):
    fields = rec.get("fields", {})
    pr_url = fields.get(pr_url_field)
    filename = fields.get(filename_field)

    if not pr_url:
        return False
    if not pr_is_merged(pr_url):
        log.info("%-55s  PR not merged yet — will check again next push.", title[:55])
        return False
    if not filename:
        log.warning("%-55s  PR is merged but no Filename on record — cannot verify the live file. "
                    "Skipping; this needs a look.", title[:55])
        return False

    plain_text = extract_plain_text(filename)
    published_url = f"{SITE_BASE_URL.rstrip('/')}/{filename}" if SITE_BASE_URL else ""

    social = draft_social_copy(title, plain_text, published_url)
    if not social:
        log.warning("%-55s  merged, but social-copy drafting failed — Live At left blank so this "
                    "retries on the next push.", title[:55])
        return False

    update_fields = {
        live_at_field: datetime.now(timezone.utc).date().isoformat(),
        linkedin_field: social["linkedin_post"],
        facebook_field: social["facebook_post"],
    }
    if published_url:
        update_fields[published_url_field] = published_url

    if not update_record(table_id, rec["id"], update_fields):
        log.warning("%-55s  merged and drafted, but the Airtable write failed — will retry.", title[:55])
        return False

    log.info("%-55s  confirmed live, social drafts written.", title[:55])
    return True


def main():
    if not AIRTABLE_PAT:
        log.error("AIRTABLE_PAT is not set — add it as a GitHub repo secret.")
        sys.exit(1)
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY is not set — add it as a GitHub repo secret.")
        sys.exit(1)
    if not GITHUB_TOKEN:
        log.error("GITHUB_TOKEN is not set — this should be provided automatically by GitHub Actions.")
        sys.exit(1)
    if not GITHUB_REPOSITORY:
        log.error("GITHUB_REPOSITORY is not set — this should be provided automatically by GitHub Actions.")
        sys.exit(1)
    if not SITE_BASE_URL:
        log.warning("SITE_BASE_URL is not set — Published URL will be left blank and social drafts "
                    "won't include a link. Set the SITE_BASE_URL repository variable to fix this.")

    log.info("On-publish notify starting (repo: %s)", GITHUB_REPOSITORY)

    pending_drafts = fetch_pending(DRAFTS_TABLE, DRAFT_FIELD_GITHUB_PR_URL, DRAFT_FIELD_LIVE_AT)
    pending_issues = fetch_pending(NEWSLETTERS_TABLE, NEWS_FIELD_GITHUB_PR_URL, NEWS_FIELD_LIVE_AT)

    if not pending_drafts and not pending_issues:
        log.info("No published-but-unconfirmed rows found — nothing to do. Exiting cleanly.")
        return

    log.info("Checking %d draft(s) and %d issue(s) with an open publish PR.",
              len(pending_drafts), len(pending_issues))

    confirmed = 0
    for rec in pending_drafts:
        title = rec.get("fields", {}).get(DRAFT_FIELD_TITLE, "(untitled)")
        try:
            if process_row(DRAFTS_TABLE, rec, title, DRAFT_FIELD_GITHUB_PR_URL, DRAFT_FIELD_FILENAME,
                            DRAFT_FIELD_PUBLISHED_URL, DRAFT_FIELD_LIVE_AT, DRAFT_FIELD_LINKEDIN_POST,
                            DRAFT_FIELD_FACEBOOK_POST):
                confirmed += 1
        except Exception as exc:
            log.error("Unexpected error processing draft '%s': %s", title, exc)

    for rec in pending_issues:
        week_of = rec.get("fields", {}).get(NEWS_FIELD_WEEK_OF, "unknown week")
        title = f"Afrinex — Week of {week_of}"
        try:
            if process_row(NEWSLETTERS_TABLE, rec, title, NEWS_FIELD_GITHUB_PR_URL, NEWS_FIELD_FILENAME,
                            NEWS_FIELD_PUBLISHED_URL, NEWS_FIELD_LIVE_AT, NEWS_FIELD_LINKEDIN_POST,
                            NEWS_FIELD_FACEBOOK_POST):
                confirmed += 1
        except Exception as exc:
            log.error("Unexpected error processing Afrinex issue '%s': %s", title, exc)

    log.info("-" * 60)
    log.info("Done. Confirmed %d item(s) live this run (out of %d checked).",
              confirmed, len(pending_drafts) + len(pending_issues))
    if confirmed:
        log.info("Airtable's own automation will email Marvin for each — check your inbox.")


if __name__ == "__main__":
    main()
