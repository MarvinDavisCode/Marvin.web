#!/usr/bin/env python3
"""
Relevance Scorer + Draft Generator — Editorial OS, Stage 2
============================================================

Reads every Candidates row with Status "New", asks Claude to judge how
relevant it is to Marvin's focus areas and whether it merits a full blog
post or just a newsletter mention, and — for the promising ones — asks
Claude again to write a full draft into the Drafts table with Status
"Needs Review".

This script never publishes anything and never sends anything. Every
draft it writes sits in Airtable waiting for Marvin's review — nothing
reaches the live site or the newsletter without a human approving it
first (see Stage 3 / Stage 4, not yet built).

Outcomes per candidate, after scoring:
    "ignore"            -> Candidate Status = "Ignored". No draft.
    "newsletter_mention" -> Candidate Status = "Newsletter Candidate".
                            No draft — Stage 4 will pull these directly
                            into the weekly roundup.
    "blog_draft"        -> Candidate Status = "Blog Candidate", AND a new
                            Drafts row is created (Status "Needs Review",
                            Fact-Check Status "Not Started").

Environment variables required:
    AIRTABLE_PAT        Airtable Personal Access Token (repo secret)
    ANTHROPIC_API_KEY   Anthropic API key (repo secret)

Optional environment variables:
    AIRTABLE_BASE_ID          Airtable base ID (default: the live base)
    SCORING_MODEL             Model used for the cheap triage pass
                              (default: claude-haiku-4-5-20251001)
    DRAFTING_MODEL            Model used for actual draft writing
                              (default: claude-sonnet-5)
    MAX_CANDIDATES_PER_RUN    Safety cap on API spend per run
                              (default: 25)

Usage:
    python automation/scripts/relevance_scorer.py
"""

import os
import sys
import json
import time
import logging

import requests

# --------------------------------------------------------------------------
# Config — Airtable base / table / field IDs
# --------------------------------------------------------------------------

BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "appGoegWQtI3TmtNW")
AIRTABLE_PAT = os.environ.get("AIRTABLE_PAT")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

SCORING_MODEL = os.environ.get("SCORING_MODEL", "claude-haiku-4-5-20251001")
DRAFTING_MODEL = os.environ.get("DRAFTING_MODEL", "claude-sonnet-5")
MAX_CANDIDATES_PER_RUN = int(os.environ.get("MAX_CANDIDATES_PER_RUN", "25"))

TAXONOMY_TABLE = "tblVxa7O6y19kiJbz"
SOURCES_TABLE = "tbllrhWY2ExPnf9Gc"
CANDIDATES_TABLE = "tblSP4rl8SCiXeXOE"
DRAFTS_TABLE = "tblRA7DupHBXo1Fqq"

# Taxonomy fields
TAX_FIELD_NAME = "fld0YJP2u4qiHlm7Q"
TAX_FIELD_DESCRIPTION = "fldRkAlHi5T51ekfA"

# Sources fields (just enough to label a candidate's origin in prompts/text)
SRC_FIELD_NAME = "fldJTJpBoGqZ2ujo2"

# Candidates fields
CAND_FIELD_TITLE = "fldZkd5g4bNhK8ocW"
CAND_FIELD_SOURCE_LINK = "fld6IB7NC00OlhRVq"
CAND_FIELD_URL = "fldZiWr7lJGOGB5Wn"
CAND_FIELD_PUBLISHED = "fldtmhPa9u6C0KUs6"
CAND_FIELD_RELEVANCE = "fldYrYDUmvT2YgRtE"       # percent field: store as a 0-1 fraction
CAND_FIELD_FOCUS_AREAS = "fldkyQbpdvzJSHLhW"
CAND_FIELD_SUMMARY = "fldlr69vWrpcIKPMz"
CAND_FIELD_STATUS = "fldFlcPP0hjX6ZpbU"

CAND_STATUS_NEW = "New"
CAND_STATUS_SCORED = "Scored"
CAND_STATUS_BLOG_CANDIDATE = "Blog Candidate"
CAND_STATUS_NEWSLETTER_CANDIDATE = "Newsletter Candidate"
CAND_STATUS_IGNORED = "Ignored"

# Drafts fields
DRAFT_FIELD_TITLE = "fldVXmgpzUGj1kWdB"
DRAFT_FIELD_SUBTITLE = "fldVDynt3pJhLzeas"
DRAFT_FIELD_RELATED_CANDIDATE = "fldmrHSphei3yrWKw"   # link -> Candidates
DRAFT_FIELD_BODY = "fld6accY9ibbxA37f"
DRAFT_FIELD_FOCUS_AREAS = "fldbzLKWZjb5wExUV"
DRAFT_FIELD_SOURCES_REFERENCES = "fldOC66mBtlGVER7i"  # plain text citation, not a link field
DRAFT_FIELD_SEO_TITLE = "fld93t9YWjZ0oV5yu"
DRAFT_FIELD_META_DESCRIPTION = "fldjm3yzA116AMi7S"
DRAFT_FIELD_SOCIAL_EXCERPT = "fldnZntw9EIW16h8p"
DRAFT_FIELD_FACT_CHECK_STATUS = "fldvHpWP6NBPIG1uq"
DRAFT_FIELD_STATUS = "fldK03CiL62pWHGDV"
DRAFT_FIELD_TARGET = "fldYsBRNgbvG2Zcj7"
DRAFT_FIELD_EDITORIAL_NOTES = "fld50HgnHVTxBg8ub"

DRAFT_FACT_CHECK_NOT_STARTED = "Not Started"
DRAFT_STATUS_NEEDS_REVIEW = "Needs Review"
DRAFT_TARGET_BLOG = "Blog"

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
log = logging.getLogger("relevance_scorer")


# --------------------------------------------------------------------------
# HTTP helpers — shared retry/backoff wrapper for both Airtable and Anthropic
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
    """Paginates through every record in a table. Small reference tables
    only (Taxonomy, Sources) — Candidates uses a filtered fetch instead."""
    records = []
    url = f"{AIRTABLE_API_ROOT}/{BASE_ID}/{table_id}"
    # returnFieldsByFieldId is essential: without it, Airtable keys the
    # returned `fields` object by field NAME, but every constant in this
    # script (TAX_FIELD_NAME, CAND_FIELD_TITLE, etc.) is a field ID — so
    # every .get() against a fetched record would silently return None.
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


def fetch_taxonomy():
    """Returns (taxonomy_prompt_block, set_of_valid_names)."""
    records = fetch_all_records(TAXONOMY_TABLE)
    lines = []
    names = set()
    for rec in records:
        fields = rec.get("fields", {})
        name = fields.get(TAX_FIELD_NAME)
        desc = fields.get(TAX_FIELD_DESCRIPTION, "")
        if not name:
            continue
        names.add(name)
        lines.append(f"- {name}: {desc}")
    return "\n".join(lines), names


def fetch_sources_map():
    """Returns dict: record_id -> source name."""
    records = fetch_all_records(SOURCES_TABLE)
    out = {}
    for rec in records:
        out[rec["id"]] = rec.get("fields", {}).get(SRC_FIELD_NAME, "Unknown source")
    return out


def fetch_new_candidates(limit):
    """Fetches up to `limit` Candidates rows with Status == 'New'."""
    records = []
    url = f"{AIRTABLE_API_ROOT}/{BASE_ID}/{CANDIDATES_TABLE}"
    params = {
        "pageSize": PAGE_SIZE,
        "returnFieldsByFieldId": "true",
        "filterByFormula": f"{{{CAND_FIELD_STATUS}}} = \"{CAND_STATUS_NEW}\"",
    }

    while len(records) < limit:
        resp = _request_with_retry("GET", url, headers=_airtable_headers(), params=params)
        if resp is None or resp.status_code != 200:
            log.error("Failed to list new Candidates: %s", getattr(resp, "text", "no response")[:300])
            break

        payload = resp.json()
        records.extend(payload.get("records", []))

        offset = payload.get("offset")
        if not offset:
            break
        params["offset"] = offset

    return records[:limit]


# --------------------------------------------------------------------------
# Airtable writes
# --------------------------------------------------------------------------

def update_candidate(record_id, fields):
    url = f"{AIRTABLE_API_ROOT}/{BASE_ID}/{CANDIDATES_TABLE}/{record_id}"
    resp = _request_with_retry("PATCH", url, headers=_airtable_headers(), json={"fields": fields})
    if resp is None or resp.status_code != 200:
        log.error("Failed to update Candidate %s: %s", record_id, getattr(resp, "text", "no response")[:300])
        return False
    return True


def create_draft(fields):
    url = f"{AIRTABLE_API_ROOT}/{BASE_ID}/{DRAFTS_TABLE}"
    resp = _request_with_retry("POST", url, headers=_airtable_headers(), json={"records": [{"fields": fields}]})
    if resp is None or resp.status_code not in (200, 201):
        log.error("Failed to create Draft: %s", getattr(resp, "text", "no response")[:300])
        return None
    return resp.json()["records"][0]["id"]


# --------------------------------------------------------------------------
# Claude calls
# --------------------------------------------------------------------------

def _call_claude(model, system_prompt, user_prompt, max_tokens):
    """Calls the Messages API and returns the raw text response, or None on
    any failure (network, non-2xx, or an unexpected response shape)."""
    body = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_prompt}],
    }
    resp = _request_with_retry(
        "POST", ANTHROPIC_API_URL, headers=_anthropic_headers(), json=body,
    )
    if resp is None or resp.status_code != 200:
        log.error("Claude API call failed (%s): %s", model, getattr(resp, "text", "no response")[:500])
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
    """Claude is asked to return raw JSON, but models sometimes wrap it in
    a ```json fence anyway — strip that defensively before parsing. Returns
    None (and logs) if the result still isn't valid JSON."""
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


SCORING_SYSTEM_PROMPT_TEMPLATE = """You are triaging incoming articles for Marvin Darvis's editorial system. \
Marvin is a psychologist and Mental Health & Behavioral Health Innovation Specialist. His audience is \
practitioners, educators, researchers, and program teams working in evidence-based mental health practice, \
AI in mental health, implementation science, and (often) resource-constrained settings including Africa/Kenya.

His site's focus areas are:
{taxonomy_block}

For the article the user gives you, decide:
1. relevance_score: an integer from 0 to 100 for how relevant and valuable this would be to Marvin's audience.
2. recommendation: exactly one of "blog_draft" (substantial and relevant enough to merit a full standalone \
blog post), "newsletter_mention" (relevant and worth a brief mention in the weekly roundup, but not enough \
substance on its own for a full post), or "ignore" (not a good fit for this audience).
3. focus_areas: a list of the focus area names above that best apply (use the exact names given verbatim; \
include at least one if the article is at all relevant).
4. reasoning: one sentence explaining your call, for Marvin's own internal reference only — never shown publicly.

Respond with ONLY a single JSON object with exactly these keys: relevance_score, recommendation, focus_areas, \
reasoning. No markdown formatting, no code fences, no extra commentary before or after the JSON."""


DRAFTING_SYSTEM_PROMPT = """You are drafting a blog post for Marvin Darvis's professional website, in his voice: \
a psychologist and Mental Health & Behavioral Health Innovation Specialist writing for practitioners, educators, \
researchers, and program teams. His tone is evidence-based, measured, and practical, and he never overclaims — \
he is a licensed mental health professional publishing under his own name, so accuracy matters more than excitement.

Ground rules — follow these strictly:
- Base the post ONLY on the title, source, and summary given below. Do not invent statistics, quotes, study \
findings, sample sizes, or any specific detail that isn't present in that material.
- If the summary doesn't give enough detail to support a specific claim you want to make, write about it in \
general terms instead, and note in editorial_notes that Marvin should verify the specifics against the full \
original article before publishing.
- Frame this clearly as commentary on published research or news, not as Marvin's own study or primary claim.
- Structure: an opening hook, what the finding or development actually is, why it matters for the relevant focus \
area(s), and a closing practical implication or open question for the field. Roughly 4-6 short paragraphs.
- Match the register of his existing writing: grounded, plain-spoken, occasionally direct about limitations and \
what AI or a single study can't tell us. Avoid hype language.

Respond with ONLY a single JSON object with exactly these keys:
- title: a headline in Marvin's style
- subtitle: a one-sentence dek/subtitle
- body: the full post body as plain text with paragraphs separated by blank lines (no markdown headers)
- seo_title: a concise, search-friendly title (under 60 characters)
- meta_description: under 155 characters, for search engines
- social_excerpt: a short excerpt (under 280 characters) suitable for sharing
- editorial_notes: a short note to Marvin — anything he should fact-check or verify before publishing, or "None" \
if nothing stands out

No markdown formatting, no code fences, no extra commentary before or after the JSON."""


def score_candidate(candidate_text, taxonomy_block):
    system_prompt = SCORING_SYSTEM_PROMPT_TEMPLATE.format(taxonomy_block=taxonomy_block)
    raw = _call_claude(SCORING_MODEL, system_prompt, candidate_text, max_tokens=400)
    return _parse_json_response(raw)


def draft_candidate(candidate_text):
    raw = _call_claude(DRAFTING_MODEL, DRAFTING_SYSTEM_PROMPT, candidate_text, max_tokens=3000)
    return _parse_json_response(raw)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def build_candidate_text(fields, source_name):
    return (
        f"Title: {fields.get(CAND_FIELD_TITLE, '(untitled)')}\n"
        f"Source: {source_name}\n"
        f"Published: {fields.get(CAND_FIELD_PUBLISHED, 'unknown date')}\n"
        f"URL: {fields.get(CAND_FIELD_URL, '')}\n"
        f"Summary: {fields.get(CAND_FIELD_SUMMARY, '(no summary available)')}"
    )


def clean_focus_areas(raw_list, valid_names, fallback):
    if not isinstance(raw_list, list):
        return fallback
    cleaned = [name for name in raw_list if name in valid_names]
    return cleaned if cleaned else fallback


def process_candidate(rec, taxonomy_block, valid_focus_areas, sources_map):
    """Handles exactly one Candidate end to end and returns an outcome
    string: 'blog_draft', 'newsletter_mention', 'ignore', 'scoring_failed',
    or 'draft_failed'. Deliberately has no bare try/except of its own —
    the caller wraps every call to this function, so *any* unexpected
    shape from the model (wrong JSON type, missing key, whatever) is
    caught there and logged as a per-candidate failure rather than ever
    taking down the whole run."""
    record_id = rec["id"]
    fields = rec.get("fields", {})
    title = fields.get(CAND_FIELD_TITLE, "(untitled)")
    source_id_list = fields.get(CAND_FIELD_SOURCE_LINK) or []
    source_name = sources_map.get(source_id_list[0], "Unknown source") if source_id_list else "Unknown source"
    original_focus_areas = fields.get(CAND_FIELD_FOCUS_AREAS, [])

    candidate_text = build_candidate_text(fields, source_name)

    score_result = score_candidate(candidate_text, taxonomy_block)
    if not isinstance(score_result, dict) or "recommendation" not in score_result:
        log.warning("Scoring failed for '%s' — leaving Status as 'New' for a future run.", title)
        return "scoring_failed"

    recommendation = score_result.get("recommendation")
    relevance_score = score_result.get("relevance_score")
    focus_areas = clean_focus_areas(score_result.get("focus_areas"), valid_focus_areas, original_focus_areas)

    candidate_update = {CAND_FIELD_FOCUS_AREAS: focus_areas}
    if isinstance(relevance_score, (int, float)):
        candidate_update[CAND_FIELD_RELEVANCE] = max(0, min(100, relevance_score)) / 100.0

    if recommendation == "ignore":
        candidate_update[CAND_FIELD_STATUS] = CAND_STATUS_IGNORED
        update_candidate(record_id, candidate_update)
        log.info("%-60s  ignore (score %s)", title[:60], relevance_score)
        return "ignore"

    if recommendation == "newsletter_mention":
        candidate_update[CAND_FIELD_STATUS] = CAND_STATUS_NEWSLETTER_CANDIDATE
        update_candidate(record_id, candidate_update)
        log.info("%-60s  newsletter mention (score %s)", title[:60], relevance_score)
        return "newsletter_mention"

    if recommendation != "blog_draft":
        # Unrecognized value from the model — don't guess, just park it
        # as "Scored" for Marvin to triage manually rather than silently
        # dropping it or mis-filing it.
        log.warning("Unrecognized recommendation '%s' for '%s' — marking 'Scored' for manual review.",
                    recommendation, title)
        candidate_update[CAND_FIELD_STATUS] = CAND_STATUS_SCORED
        update_candidate(record_id, candidate_update)
        return "scoring_failed"

    # recommendation == "blog_draft" — write the draft before flipping
    # the Candidate's status, so a drafting failure never leaves a
    # Candidate marked "Blog Candidate" with no actual draft behind it.
    draft_result = draft_candidate(candidate_text)
    required_keys = {"title", "subtitle", "body", "seo_title", "meta_description",
                      "social_excerpt", "editorial_notes"}
    if not isinstance(draft_result, dict) or not required_keys.issubset(draft_result.keys()):
        log.warning("Drafting failed for '%s' — marking 'Scored' so it isn't silently lost; retry manually "
                    "or re-run once the issue is fixed.", title)
        candidate_update[CAND_FIELD_STATUS] = CAND_STATUS_SCORED
        update_candidate(record_id, candidate_update)
        return "draft_failed"

    published = fields.get(CAND_FIELD_PUBLISHED, "unknown date")
    url = fields.get(CAND_FIELD_URL, "")
    sources_references = f"{source_name}. \"{title}.\" {published}. {url}"

    draft_fields = {
        DRAFT_FIELD_TITLE: draft_result["title"],
        DRAFT_FIELD_SUBTITLE: draft_result["subtitle"],
        DRAFT_FIELD_RELATED_CANDIDATE: [record_id],
        DRAFT_FIELD_BODY: draft_result["body"],
        DRAFT_FIELD_FOCUS_AREAS: focus_areas,
        DRAFT_FIELD_SOURCES_REFERENCES: sources_references,
        DRAFT_FIELD_SEO_TITLE: draft_result["seo_title"],
        DRAFT_FIELD_META_DESCRIPTION: draft_result["meta_description"],
        DRAFT_FIELD_SOCIAL_EXCERPT: draft_result["social_excerpt"],
        DRAFT_FIELD_FACT_CHECK_STATUS: DRAFT_FACT_CHECK_NOT_STARTED,
        DRAFT_FIELD_STATUS: DRAFT_STATUS_NEEDS_REVIEW,
        DRAFT_FIELD_TARGET: DRAFT_TARGET_BLOG,
        DRAFT_FIELD_EDITORIAL_NOTES: draft_result["editorial_notes"],
    }

    draft_id = create_draft(draft_fields)
    if not draft_id:
        log.warning("Draft write failed for '%s' — marking 'Scored' rather than losing the scoring work.", title)
        candidate_update[CAND_FIELD_STATUS] = CAND_STATUS_SCORED
        update_candidate(record_id, candidate_update)
        return "draft_failed"

    candidate_update[CAND_FIELD_STATUS] = CAND_STATUS_BLOG_CANDIDATE
    update_candidate(record_id, candidate_update)
    log.info("%-60s  blog draft created (score %s)", title[:60], relevance_score)
    return "blog_draft"


def main():
    if not AIRTABLE_PAT:
        log.error("AIRTABLE_PAT is not set — add it as a GitHub repo secret.")
        sys.exit(1)
    if not ANTHROPIC_API_KEY:
        log.error("ANTHROPIC_API_KEY is not set — add it as a GitHub repo secret.")
        sys.exit(1)

    log.info("Relevance scorer starting (scoring model: %s, drafting model: %s, cap: %d)",
              SCORING_MODEL, DRAFTING_MODEL, MAX_CANDIDATES_PER_RUN)

    taxonomy_block, valid_focus_areas = fetch_taxonomy()
    if not valid_focus_areas:
        log.error("Could not load Taxonomy table — nothing to score against. Aborting.")
        sys.exit(1)

    sources_map = fetch_sources_map()

    candidates = fetch_new_candidates(MAX_CANDIDATES_PER_RUN)
    if not candidates:
        log.info("No Candidates with Status 'New' — nothing to do. Exiting cleanly.")
        return

    log.info("Found %d new candidate(s) to process (cap is %d per run).",
              len(candidates), MAX_CANDIDATES_PER_RUN)

    counts = {"blog_draft": 0, "newsletter_mention": 0, "ignore": 0, "scoring_failed": 0, "draft_failed": 0}

    for rec in candidates:
        title = rec.get("fields", {}).get(CAND_FIELD_TITLE, "(untitled)")
        try:
            outcome = process_candidate(rec, taxonomy_block, valid_focus_areas, sources_map)
        except Exception as exc:
            # Whatever went wrong — a malformed model response, an Airtable
            # write hiccup, anything — one candidate must never take down
            # the rest of the run. It's left as Status "New" and will be
            # picked up again on the next scheduled run.
            log.error("Unexpected error processing '%s': %s", title, exc)
            outcome = "scoring_failed"
        counts[outcome] = counts.get(outcome, 0) + 1

    log.info("-" * 60)
    log.info(
        "Done. Processed %d candidate(s) — blog drafts: %d | newsletter mentions: %d | ignored: %d | "
        "scoring failed: %d | draft failed: %d",
        len(candidates), counts["blog_draft"], counts["newsletter_mention"], counts["ignore"],
        counts["scoring_failed"], counts["draft_failed"],
    )
    if len(candidates) == MAX_CANDIDATES_PER_RUN:
        log.info("Hit the per-run cap (%d) — there may be more 'New' candidates left for the next scheduled run.",
                  MAX_CANDIDATES_PER_RUN)


if __name__ == "__main__":
    main()
