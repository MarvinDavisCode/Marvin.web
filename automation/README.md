# Editorial OS — Automation

This folder is Stage 1 of the automated content pipeline described in the
Claude Project doc (`claude/editorial-automation-plan.md`): a GitHub Action
that checks your Airtable **Sources** table daily and drops any new articles
into the **Candidates** table for review.

It does **not** score articles, write drafts, publish anything to the site,
or touch the Afrinex newsletter — those are later stages, not built yet.
This stage's only job is "what's new out there?"

## How it works

1. `.github/workflows/source-monitor.yml` runs on a schedule (daily, 06:00
   UTC — about 09:00 in Nairobi) and can also be triggered manually.
2. It installs Python + a few small libraries, then runs
   `automation/scripts/source_monitor.py`.
3. That script:
   - Reads every row in Sources whose **Status** is `Active` (skips `Needs
     Follow-up` and `Blocked` sources — those aren't ready yet).
   - Fetches each source's RSS/Atom/RDF feed.
   - Compares every item's URL against what's already in Candidates, so
     nothing gets added twice.
   - Creates a new Candidates row for each genuinely new item — Title,
     Source (linked back to Sources), URL, Published Date, Summary, and the
     source's Focus Areas — with **Status set to "New"**.
4. One broken feed, one bad HTTP response, or one malformed entry never
   stops the whole run — it's logged and skipped, and every other source
   still gets checked.

You'll see the run's log (which feeds were checked, how many items each had,
how many were new) under the repo's **Actions** tab on GitHub after each run.

## One-time setup (do this before the schedule can actually run)

The workflow needs a way to read and write your Airtable base. That's an
Airtable **Personal Access Token**, added as a GitHub secret — never put it
directly in any file in this repo.

1. **Create the token.** Go to
   [airtable.com/create/tokens](https://airtable.com/create/tokens) while
   signed in to the account that owns the base.
   - Name it something like `marvin-site-source-monitor`.
   - Under **Scopes**, add:
     - `data.records:read`
     - `data.records:write`
     - `schema.bases:read`
   - Under **Access**, add the specific base: **Editorial OS**
     (`appGoegWQtI3TmtNW`) — no need to grant access to any other base.
   - Click **Create token** and copy it. Airtable only shows it once.

2. **Add it as a GitHub repo secret.**
   - In your repo on GitHub, go to **Settings → Secrets and variables →
     Actions**.
   - Click **New repository secret**.
   - Name: `AIRTABLE_PAT`
   - Value: paste the token you just copied.
   - Save.

That's the only secret this stage needs. The base ID itself
(`appGoegWQtI3TmtNW`) isn't sensitive — it's already sitting in the
workflow file as a plain environment variable, since knowing it doesn't
grant any access without the token.

## Triggering a run manually (to test it)

You don't have to wait for the daily schedule:

1. Go to your repo on GitHub → the **Actions** tab.
2. Click **Source Monitor** in the left sidebar.
3. Click **Run workflow** (top right) → **Run workflow** again to confirm.
4. Click into the run to watch the log as it checks each source.

If it's your first run, do this once right after adding the `AIRTABLE_PAT`
secret to confirm everything's wired up correctly before trusting the daily
schedule.

## Testing it locally (optional)

If you want to run it on your own machine before trusting it in GitHub
Actions:

```bash
cd marvin-site
pip install -r automation/requirements.txt

export AIRTABLE_PAT="your-token-here"
export AIRTABLE_BASE_ID="appGoegWQtI3TmtNW"

python automation/scripts/source_monitor.py
```

You'll see the same log output described above, printed straight to your
terminal.

## Known source issues (see Airtable Sources table for details)

A few of the feeds you asked to add aren't fully working yet — they're
marked `Needs Follow-up` or `Blocked` in Airtable rather than `Active`, so
this script skips them automatically until they're fixed:

- **The Lancet Psychiatry** — blocked by the publisher's bot protection
  (403). Recommended fix: build a PubMed saved-search RSS feed instead of
  using the publisher's own feed directly.
- **Implementation Science** — its feed URL currently redirects into a
  broken cookie-dependent link. Needs testing with a real feed reader (this
  script's fetcher isn't the issue — it's the publisher's redirect).
- **American Psychologist** — blocked by the publisher's `robots.txt` for
  automated fetchers. Needs testing with a production-grade fetcher before
  trusting it.

Once any of these is confirmed working, just flip its Status to `Active` in
Airtable — no code change needed.

## What's next

This is Stage 1 of 4. Still to build (tracked in the Claude Project doc):

- **Stage 2 — Relevance scoring + draft generation.** A script that reads
  `New` Candidates, scores them against your Focus Areas taxonomy using the
  Claude API, and writes a full draft for the promising ones (Status
  `Needs Review`) into the Drafts table.
- **Stage 3 — Publish on approval.** A GitHub Action that watches for
  Drafts marked `Approved` in Airtable, generates the insight-post HTML from
  your existing template, and opens a pull request for you to merge — never
  commits directly.
- **Stage 4 — Weekly Afrinex newsletter.** Pulls the week's approved items
  into a Buttondown draft with a placeholder for your own Editor's Note,
  ready every Monday for you to review before sending.

Each of those needs its own account/API key (Anthropic API key for scoring
and drafting; a Buttondown account and API key for the newsletter) added the
same way as above — as a GitHub repo secret, never committed to the repo.
