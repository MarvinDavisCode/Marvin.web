#!/usr/bin/env python3
"""
Publish on Approval — Editorial OS, Stage 3
=============================================

Watches the Drafts table for rows Marvin has marked Status "Approved" with
Target "Blog", turns each one into a real insight-post HTML file (using the
site's existing page structure), adds a matching teaser card to
insights.html, and opens ONE pull request containing everything found this
run. It never commits straight to the default branch — the PR is the only
output, and nothing reaches the live site until Marvin reviews and merges
it himself.

Why a PR and not a direct commit: Marvin approving a draft in Airtable is
a content decision ("this is worth publishing"), not a "push this exact
HTML to my live site right now" decision. The PR is a second, mechanical
checkpoint — a chance to preview the actual rendered page, catch anything
that reads wrong once it's laid out, or fix a typo, before it goes live.

Idempotency: a Drafts row is only considered here if its "GitHub PR URL"
field is still blank. Once a PR is opened, that field is filled in with
the PR's URL — so re-running this on the same Approved draft (e.g. the
next day's scheduled run, before Marvin has merged the PR) does not open
a second PR for it. If you want a draft re-published from scratch (e.g.
after closing a PR without merging), clear its "GitHub PR URL" field.

Environment variables required:
    AIRTABLE_PAT     Airtable Personal Access Token (repo secret)
    GITHUB_TOKEN      Provided automatically by GitHub Actions — needs
                      `contents: write` and `pull-requests: write`
                      permissions in the workflow file.

Optional environment variables:
    AIRTABLE_BASE_ID       Airtable base ID (default: the live base)
    GITHUB_REPOSITORY      "owner/repo" — set automatically by Actions
    GITHUB_API_URL         Default: https://api.github.com
    DEFAULT_BRANCH         Branch to open the PR against (default: main)
    MAX_DRAFTS_PER_RUN     Safety cap on how many drafts one run publishes
                           (default: 10)

Usage (must be run from the repo root, with git already checked out):
    python automation/scripts/publish_approved.py
"""

import os
import re
import sys
import html
import time
import logging
import subprocess
from datetime import datetime, timezone

import requests

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "appGoegWQtI3TmtNW")
AIRTABLE_PAT = os.environ.get("AIRTABLE_PAT")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY")  # "owner/repo", set by Actions
GITHUB_API_URL = os.environ.get("GITHUB_API_URL", "https://api.github.com")
DEFAULT_BRANCH = os.environ.get("DEFAULT_BRANCH", "main")
MAX_DRAFTS_PER_RUN = int(os.environ.get("MAX_DRAFTS_PER_RUN", "10"))

DRAFTS_TABLE = "tblRA7DupHBXo1Fqq"
CANDIDATES_TABLE = "tblSP4rl8SCiXeXOE"

DRAFT_FIELD_TITLE = "fldVXmgpzUGj1kWdB"
DRAFT_FIELD_SUBTITLE = "fldVDynt3pJhLzeas"
DRAFT_FIELD_RELATED_CANDIDATE = "fldmrHSphei3yrWKw"
DRAFT_FIELD_BODY = "fld6accY9ibbxA37f"
DRAFT_FIELD_FOCUS_AREAS = "fldbzLKWZjb5wExUV"
DRAFT_FIELD_SOURCES_REFERENCES = "fldOC66mBtlGVER7i"
DRAFT_FIELD_SEO_TITLE = "fld93t9YWjZ0oV5yu"
DRAFT_FIELD_META_DESCRIPTION = "fldjm3yzA116AMi7S"
DRAFT_FIELD_STATUS = "fldK03CiL62pWHGDV"
DRAFT_FIELD_TARGET = "fldYsBRNgbvG2Zcj7"
DRAFT_FIELD_GITHUB_PR_URL = "fldTVnI8GFRQkUn21"

DRAFT_STATUS_APPROVED = "Approved"
DRAFT_TARGET_BLOG = "Blog"

CAND_FIELD_URL = "fldZiWr7lJGOGB5Wn"

AIRTABLE_API_ROOT = "https://api.airtable.com/v0"
PAGE_SIZE = 100
REQUEST_TIMEOUT = 30
MAX_RETRIES = 4

SITE_ROOT = "."  # script is run with the repo root as the working directory
INSIGHTS_LISTING_PATH = os.path.join(SITE_ROOT, "insights.html")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("publish_approved")


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


# --------------------------------------------------------------------------
# Airtable reads/writes
# --------------------------------------------------------------------------

def fetch_ready_drafts(limit):
    """Drafts that are Approved, targeted at the Blog, and haven't already
    had a PR opened for them (GitHub PR URL still blank)."""
    records = []
    url = f"{AIRTABLE_API_ROOT}/{BASE_ID}/{DRAFTS_TABLE}"
    formula = (
        f'AND({{{DRAFT_FIELD_STATUS}}}="{DRAFT_STATUS_APPROVED}", '
        f'{{{DRAFT_FIELD_TARGET}}}="{DRAFT_TARGET_BLOG}", '
        f'{{{DRAFT_FIELD_GITHUB_PR_URL}}}="")'
    )
    # returnFieldsByFieldId is essential: without it, Airtable keys the
    # returned `fields` object by field NAME, but every constant in this
    # script (DRAFT_FIELD_TITLE etc.) is a field ID — every .get() against
    # a fetched record would otherwise silently return None (this is
    # exactly what caused the "Untitled" / "insight-untitled.html" bug).
    params = {"pageSize": PAGE_SIZE, "returnFieldsByFieldId": "true", "filterByFormula": formula}

    while len(records) < limit:
        resp = _request_with_retry("GET", url, headers=_airtable_headers(), params=params)
        if resp is None or resp.status_code != 200:
            log.error("Failed to list ready Drafts: %s", getattr(resp, "text", "no response")[:300])
            break
        payload = resp.json()
        records.extend(payload.get("records", []))
        offset = payload.get("offset")
        if not offset:
            break
        params["offset"] = offset

    return records[:limit]


def fetch_candidate(record_id):
    url = f"{AIRTABLE_API_ROOT}/{BASE_ID}/{CANDIDATES_TABLE}/{record_id}"
    resp = _request_with_retry("GET", url, headers=_airtable_headers(),
                                params={"returnFieldsByFieldId": "true"})
    if resp is None or resp.status_code != 200:
        log.warning("Could not fetch related Candidate %s: %s", record_id, getattr(resp, "text", "")[:200])
        return None
    return resp.json()


def set_draft_pr_url(record_id, pr_url):
    url = f"{AIRTABLE_API_ROOT}/{BASE_ID}/{DRAFTS_TABLE}/{record_id}"
    resp = _request_with_retry(
        "PATCH", url, headers=_airtable_headers(),
        json={"fields": {DRAFT_FIELD_GITHUB_PR_URL: pr_url}},
    )
    if resp is None or resp.status_code != 200:
        log.error("Failed to record PR URL on Draft %s: %s", record_id, getattr(resp, "text", "")[:300])
        return False
    return True


# --------------------------------------------------------------------------
# Content rendering
# --------------------------------------------------------------------------

def slugify(text, max_len=60):
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:max_len].rstrip("-") or "post"


def unique_filename(slug, record_id):
    filename = f"insight-{slug}.html"
    if not os.path.exists(os.path.join(SITE_ROOT, filename)):
        return filename
    # Collision (e.g. two drafts with very similar titles) — disambiguate
    # with a short, stable suffix from the record ID rather than guessing.
    return f"insight-{slug}-{record_id[-6:].lower()}.html"


def paragraphs_to_html(body_text):
    parts = re.split(r"\n\s*\n", body_text.strip())
    return "\n\n".join(f"        <p>\n          {html.escape(p.strip())}\n        </p>" for p in parts if p.strip())


PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{seo_title} — Marvin Darvis</title>
  <meta name="description" content="{meta_description}" />
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="css/style.css">
</head>
<body>
  <a class="skip-link" href="#main">Skip to content</a>

  <header class="site-header">
    <nav class="nav">
      <a class="nav-brand" href="index.html"><span class="mark">M</span> Marvin Darvis</a>
      <button class="nav-toggle" aria-label="Toggle navigation" aria-expanded="false">
        <span></span><span></span><span></span>
      </button>
      <ul class="nav-links">
        <li><a href="index.html">Home</a></li>
        <li><a href="about.html">About</a></li>
        <li><a href="focus-areas.html">Focus Areas</a></li>
        <li><a href="insights.html">Insights</a></li>
        <li><a href="community.html">Community</a></li>
        <li><a href="contact.html" class="nav-cta">Contact</a></li>
      </ul>
    </nav>
  </header>

  <main id="main">
    <section style="padding-bottom:0;">
      <div class="container" style="max-width:720px;">
        <p><a class="read-more" href="insights.html">← All insights</a></p>
        <span class="post-tag">{post_tag}</span>
        <h1>{title}</h1>
        <p style="font-size:1.15rem; max-width:56ch;">{subtitle}</p>
        <p class="post-meta">Published {published_date}{prompted_by}</p>
      </div>
    </section>

    <section>
      <div class="container prose" style="max-width:720px;">
        <div class="notice-box">
          This post was AI-drafted from published reporting, then reviewed and approved by
          Marvin before publishing. See "Sources &amp; References" below for the original
          material this commentary is responding to.
        </div>

{body_html}

        <h2>Sources &amp; References</h2>
        <p>{sources_references}</p>

        <p>
          Have thoughts on this, or working on something similar? <a href="contact.html">I'd
          like to hear about it</a>.
        </p>
      </div>
    </section>
  </main>

  <footer class="site-footer">
    <div class="container">
      <div class="footer-grid">
        <div>
          <a class="nav-brand" href="index.html"><span class="mark">M</span> Marvin Darvis</a>
          <p style="margin-top:12px;max-width:320px;">Mental Health &amp; Behavioral Health Innovation Specialist — building evidence-informed, technology-enabled systems for accessible care.</p>
        </div>
        <nav class="footer-links" aria-label="Footer">
          <a href="about.html">About</a>
          <a href="focus-areas.html">Focus Areas</a>
          <a href="insights.html">Insights</a>
          <a href="community.html">Community</a>
          <a href="contact.html">Contact</a>
        </nav>
        <div class="social-row" aria-label="Social links">
          <a href="#" aria-label="LinkedIn — add your profile link" title="Add your LinkedIn URL">in</a>
          <a href="#" aria-label="X / Twitter — add your profile link" title="Add your X URL">X</a>
          <a href="mailto:marvindarvis@gmail.com" aria-label="Email Marvin">@</a>
        </div>
      </div>
      <div class="footer-bottom">
        <span>© <span data-year>2026</span> Marvin Darvis. All rights reserved.</span>
        <span>Built with care for mental health innovation.</span>
      </div>
    </div>
  </footer>

  <script src="js/main.js"></script>
</body>
</html>
"""

CARD_TEMPLATE = """          <article class="post-card">
            <span class="post-tag">{post_tag}</span>
            <h3><a href="{filename}">{title}</a></h3>
            <p>{subtitle}</p>
            <span class="post-meta">{published_date}</span>
            <a class="read-more" href="{filename}">Read more →</a>
          </article>
"""


def render_post(draft_fields, candidate_fields):
    title = draft_fields.get(DRAFT_FIELD_TITLE, "Untitled")
    subtitle = draft_fields.get(DRAFT_FIELD_SUBTITLE, "")
    body = draft_fields.get(DRAFT_FIELD_BODY, "")
    focus_areas = draft_fields.get(DRAFT_FIELD_FOCUS_AREAS) or []
    seo_title = draft_fields.get(DRAFT_FIELD_SEO_TITLE) or title
    meta_description = draft_fields.get(DRAFT_FIELD_META_DESCRIPTION, "")
    sources_references = draft_fields.get(DRAFT_FIELD_SOURCES_REFERENCES, "")

    post_tag = focus_areas[0] if focus_areas else "Insights"
    published_date = datetime.now(timezone.utc).strftime("%B %-d, %Y") if os.name != "nt" \
        else datetime.now(timezone.utc).strftime("%B %d, %Y")

    original_url = (candidate_fields or {}).get("fields", {}).get(CAND_FIELD_URL)
    prompted_by = ""
    sources_html = html.escape(sources_references) if sources_references else "See the linked Candidate record in Airtable."
    if original_url:
        prompted_by = f' · <a href="{html.escape(original_url)}">Read the original</a>'
        if sources_references:
            sources_html = f'{html.escape(sources_references)} — <a href="{html.escape(original_url)}">{html.escape(original_url)}</a>'

    page_html = PAGE_TEMPLATE.format(
        seo_title=html.escape(seo_title),
        meta_description=html.escape(meta_description),
        post_tag=html.escape(post_tag),
        title=html.escape(title),
        subtitle=html.escape(subtitle),
        published_date=published_date,
        prompted_by=prompted_by,
        body_html=paragraphs_to_html(body),
        sources_references=sources_html,
    )

    card_html = CARD_TEMPLATE.format(
        post_tag=html.escape(post_tag),
        title=html.escape(title),
        subtitle=html.escape(subtitle),
        published_date=published_date,
        filename="{filename}",  # filled in by caller once the filename is chosen
    )

    return title, page_html, card_html


def insert_cards_into_listing(card_snippets):
    with open(INSIGHTS_LISTING_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    marker = '<div class="grid grid-3">'
    idx = content.find(marker)
    if idx == -1:
        raise RuntimeError(f"Could not find '{marker}' in {INSIGHTS_LISTING_PATH} — page structure may have changed.")

    insert_at = idx + len(marker) + 1  # right after the opening tag's newline
    new_cards_block = "\n" + "\n".join(card_snippets)
    updated = content[:insert_at] + new_cards_block + content[insert_at:]

    with open(INSIGHTS_LISTING_PATH, "w", encoding="utf-8") as f:
        f.write(updated)


# --------------------------------------------------------------------------
# Git / GitHub
# --------------------------------------------------------------------------

def run_git(*args):
    result = subprocess.run(["git", *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout


def open_pull_request(branch_name, title, body):
    url = f"{GITHUB_API_URL}/repos/{GITHUB_REPOSITORY}/pulls"
    resp = _request_with_retry(
        "POST", url, headers=_github_headers(),
        json={"title": title, "body": body, "head": branch_name, "base": DEFAULT_BRANCH},
    )
    if resp is None or resp.status_code not in (200, 201):
        log.error("Failed to open PR: %s", getattr(resp, "text", "no response")[:500])
        return None
    return resp.json().get("html_url")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def process_draft(rec):
    """Renders one draft to (filename, page_html, card_html_template, title)
    or raises — the caller isolates failures per-draft."""
    draft_fields = rec.get("fields", {})
    title = draft_fields.get(DRAFT_FIELD_TITLE, "Untitled")

    related = draft_fields.get(DRAFT_FIELD_RELATED_CANDIDATE) or []
    candidate_fields = fetch_candidate(related[0]) if related else None

    rendered_title, page_html, card_template = render_post(draft_fields, candidate_fields)
    # Base the filename on the SEO title when there is one — it's written to
    # be short and search-friendly, so it makes a cleaner URL slug than the
    # (often longer) editorial headline.
    slug_source = draft_fields.get(DRAFT_FIELD_SEO_TITLE) or rendered_title
    slug = slugify(slug_source, max_len=45)
    filename = unique_filename(slug, rec["id"])
    card_html = card_template.replace("{filename}", filename)

    return filename, page_html, card_html, rendered_title


def main():
    if not AIRTABLE_PAT:
        log.error("AIRTABLE_PAT is not set — add it as a GitHub repo secret.")
        sys.exit(1)
    if not GITHUB_TOKEN:
        log.error("GITHUB_TOKEN is not set — this should be provided automatically by GitHub Actions.")
        sys.exit(1)
    if not GITHUB_REPOSITORY:
        log.error("GITHUB_REPOSITORY is not set — this should be provided automatically by GitHub Actions.")
        sys.exit(1)

    log.info("Publish-on-approval starting (repo: %s, cap: %d)", GITHUB_REPOSITORY, MAX_DRAFTS_PER_RUN)

    drafts = fetch_ready_drafts(MAX_DRAFTS_PER_RUN)
    if not drafts:
        log.info("No Approved, unpublished Blog drafts found — nothing to do. Exiting cleanly.")
        return

    log.info("Found %d draft(s) ready to publish.", len(drafts))

    published = []  # list of (record_id, filename, page_html, card_html, title)
    for rec in drafts:
        title = rec.get("fields", {}).get(DRAFT_FIELD_TITLE, "(untitled)")
        try:
            filename, page_html, card_html, rendered_title = process_draft(rec)
            published.append((rec["id"], filename, page_html, card_html, rendered_title))
            log.info("Rendered: %-50s -> %s", rendered_title[:50], filename)
        except Exception as exc:
            # One malformed draft should never block the others — it's just
            # skipped and stays Approved (GitHub PR URL still blank) for a
            # future run, after Marvin or Claude fixes whatever's wrong.
            log.error("Failed to render draft '%s': %s", title, exc)

    if not published:
        log.warning("No drafts could be rendered successfully. Nothing to commit.")
        return

    branch_name = f"editorial/publish-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

    try:
        run_git("config", "user.name", "Editorial OS Bot")
        run_git("config", "user.email", "editorial-os-bot@users.noreply.github.com")
        run_git("checkout", DEFAULT_BRANCH)
        run_git("checkout", "-b", branch_name)

        for _, filename, page_html, _, _ in published:
            with open(os.path.join(SITE_ROOT, filename), "w", encoding="utf-8") as f:
                f.write(page_html)

        insert_cards_into_listing([card for _, _, _, card, _ in published])

        changed_files = [filename for _, filename, _, _, _ in published] + ["insights.html"]
        run_git("add", *changed_files)
        run_git("commit", "-m", f"Publish {len(published)} approved insight(s)")
        run_git("push", "-u", "origin", branch_name)
    except RuntimeError as exc:
        log.error("Git operation failed — no Draft rows were updated, so this is safe to retry: %s", exc)
        sys.exit(1)

    pr_title = f"Publish {len(published)} approved insight{'s' if len(published) != 1 else ''}"
    pr_body_lines = [
        "Auto-generated by the Editorial OS publish-on-approval Action.",
        "",
        "Every post below was already reviewed and marked Approved in Airtable. "
        "This PR is the mechanical step of turning that approval into real site files — "
        "please preview each page before merging, since layout issues only show up once rendered.",
        "",
    ]
    for _, filename, _, _, rendered_title in published:
        pr_body_lines.append(f"- **{rendered_title}** → `{filename}`")
    pr_url = open_pull_request(branch_name, pr_title, "\n".join(pr_body_lines))

    if not pr_url:
        log.error("Branch was pushed but PR creation failed — open it manually from GitHub, "
                  "or re-run once the issue is fixed. Draft rows were left untouched.")
        sys.exit(1)

    log.info("Opened PR: %s", pr_url)

    for record_id, _, _, _, rendered_title in published:
        if not set_draft_pr_url(record_id, pr_url):
            log.warning("Could not record the PR URL on '%s' in Airtable — the PR itself is fine, "
                        "but this draft may get included again on the next run.", rendered_title)

    log.info("-" * 60)
    log.info("Done. Published %d insight(s) in one PR: %s", len(published), pr_url)


if __name__ == "__main__":
    main()
