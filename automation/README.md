# Editorial OS — Automation

This folder holds the automated content pipeline described in the Claude
Project doc (`claude/editorial-automation-plan.md`). All four stages are
live:

- **Stage 1 — Source Monitor**: checks your Airtable **Sources** table
  daily and drops any new articles into the **Candidates** table.
- **Stage 2 — Relevance Scorer**: reads those Candidates, scores them
  against your Focus Areas using Claude, and writes a full draft into the
  **Drafts** table for the promising ones.
- **Stage 3 — Publish Approved Drafts**: watches for Drafts you've marked
  "Approved", turns each into a real page on the site, and opens a pull
  request for you to merge — nothing goes live without that merge.
- **Stage 4 — Weekly Newsletter**: once a week, compiles a Buttondown
  *draft* (never sent automatically) recapping the week's published post(s)
  plus short AI-written blurbs for anything Stage 2 flagged as a newsletter
  mention — ready for you to add your Editor's Note and send yourself.

Only Stage 3 ever touches the site itself, and even then only through a PR
you review. Stage 4 only ever creates a *draft* email — nothing is ever
sent without you opening Buttondown and clicking Send yourself. Every
draft this pipeline produces sits in Airtable (or Buttondown) waiting for
you to read, edit, approve, or send it.

## Stage 1 — Source Monitor: how it works

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

## Stage 1 — One-time setup (do this before the schedule can actually run)

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

## Stage 1 — Triggering a run manually (to test it)

You don't have to wait for the daily schedule:

1. Go to your repo on GitHub → the **Actions** tab.
2. Click **Source Monitor** in the left sidebar.
3. Click **Run workflow** (top right) → **Run workflow** again to confirm.
4. Click into the run to watch the log as it checks each source.

If it's your first run, do this once right after adding the `AIRTABLE_PAT`
secret to confirm everything's wired up correctly before trusting the daily
schedule.

## Stage 1 — Testing it locally (optional)

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

## Stage 2 — Relevance Scorer: how it works

1. `.github/workflows/relevance-scorer.yml` runs daily at 07:00 UTC (about
   10:00 in Nairobi — an hour after Source Monitor, so that morning's new
   Candidates are already there) and can also be triggered manually.
2. It runs `automation/scripts/relevance_scorer.py`, which:
   - Reads every Candidates row with **Status = "New"** (capped at 25 per
     run by default — see `MAX_CANDIDATES_PER_RUN` below — so a big backlog
     can't run up an unexpectedly large API bill in one go).
   - Asks Claude (a cheap, fast model by default) to score each one against
     your Focus Areas and recommend one of three outcomes:
     - **Ignore** — not a good fit. Candidate Status → `Ignored`.
     - **Newsletter mention** — relevant, but not enough for a full post.
       Candidate Status → `Newsletter Candidate`. Stage 4 will pull these
       into the weekly roundup directly; no draft is written.
     - **Blog draft** — worth a full post. Candidate Status →
       `Blog Candidate`, and a second, better Claude call writes an actual
       draft into the **Drafts** table (Status `Needs Review`, Fact-Check
       Status `Not Started`).
3. Every draft is written strictly from the Candidate's title, source, and
   summary — the prompt explicitly tells Claude not to invent statistics,
   quotes, or study details that aren't in that material, and to flag
   anything worth double-checking in the draft's **Editorial Notes** field.
   Treat every draft as a first pass to fact-check against the original
   article, never as publish-ready — that's exactly what the separate
   Fact-Check Status field is for.
4. One bad model response, one malformed JSON reply, or one failed Airtable
   write never stops the run or silently loses a candidate — it's logged
   and the Candidate is left in a state you can find again (either still
   `New` for a retry, or `Scored` for manual attention).

## Stage 2 — One-time setup

This stage needs one more secret on top of `AIRTABLE_PAT`: an **Anthropic
API key**, so the workflow can call Claude.

1. **Get an API key.** Go to [console.anthropic.com](https://console.anthropic.com),
   sign in (or create an account), and create a new API key under
   **API Keys**. Anthropic API usage is billed separately from any Claude.ai
   subscription — check their console for current pricing before turning
   this on if you want to keep an eye on cost.
2. **Add it as a GitHub repo secret**, the same way as `AIRTABLE_PAT`:
   **Settings → Secrets and variables → Actions → New repository secret**.
   - Name: `ANTHROPIC_API_KEY`
   - Value: paste the key.
   - Save.

Nothing else is needed — the workflow file already points at the right
Airtable base and uses sensible default models.

## Stage 2 — Cost controls

- **`MAX_CANDIDATES_PER_RUN`** (default 25) caps how many Candidates get
  processed per run. Since Stage 1 has been quietly filling Candidates
  since it went live, your first Stage 2 run may find a backlog — the cap
  means it'll process the oldest 25 and pick up the rest on the next
  scheduled run, rather than scoring/drafting everything at once.
- **Two different models by default**: a cheap, fast model
  (`claude-haiku-4-5-20251001`) does the initial scoring pass on every
  candidate, and a stronger model (`claude-sonnet-5`) is only used for
  actually writing a draft — which happens for a minority of candidates.
  Both are overridable via the `SCORING_MODEL` / `DRAFTING_MODEL`
  environment variables in the workflow file if you'd rather tune the
  quality/cost balance.

## Stage 2 — Triggering a run manually (to test it)

Same pattern as Stage 1: **Actions** tab → **Relevance Scorer** in the
sidebar → **Run workflow**. Watch the log for a line like:

```
Found 6 new candidate(s) to process (cap is 25 per run).
...
Done. Processed 6 candidate(s) — blog drafts: 1 | newsletter mentions: 2 | ignored: 3 | scoring failed: 0 | draft failed: 0
```

Then check Airtable: Candidates should show updated Status/Relevance Score
values, and any "blog draft" outcomes should have a matching new row in
Drafts with Status "Needs Review".

## Stage 3 — Publish Approved Drafts: how it works

This is the step that actually turns an approved draft into a real page on
your site — but it never does that unattended. It opens a pull request for
you to review and merge; nothing reaches the live site on its own.

1. `.github/workflows/publish-approved.yml` runs every 6 hours (cheap to
   check, and it means an approval you make in Airtable doesn't sit for a
   full day before something happens with it), plus `workflow_dispatch`.
2. It runs `automation/scripts/publish_approved.py`, which:
   - Looks in Drafts for rows where **Status = "Approved"**, **Target =
     "Blog"**, and the **GitHub PR URL** field is still blank (see "Why a
     PR, and how re-runs are prevented" below).
   - For each one, generates a real `insight-*.html` page using the site's
     existing header/nav/footer markup, with the draft's Title, Subtitle,
     Body, SEO Title, and Meta Description filled in — plus a visible
     notice on the page itself disclosing that it was AI-drafted from
     published reporting and reviewed by Marvin before publishing, and a
     "Sources & References" section linking back to the original article.
   - Adds a matching teaser card to the top of `insights.html`'s listing.
   - Commits everything found in that run to **one new branch** and opens
     **one pull request** — even if several drafts were approved at once —
     so you review one PR instead of several, and so multiple posts never
     collide trying to edit the same spot in `insights.html`.
3. Once the PR is open, each included Draft's **GitHub PR URL** field is
   filled in with a link straight to it — that's both a convenience (click
   through from Airtable) and the mechanism that stops the same draft from
   being bundled into a second PR next time this runs.
4. **Review the PR like any other pull request** — click through to the
   "Files changed" tab, or check out the branch locally if you want to see
   it rendered. Once you're happy, merge it — that's the moment the post
   actually goes live via GitHub Pages.
5. One bad draft (missing a linked Candidate, malformed content, whatever)
   never blocks the others — it's logged and skipped, staying `Approved`
   with a blank GitHub PR URL so it's picked up cleanly on a future run
   once whatever was wrong is fixed.

### Why a PR, and how re-runs are prevented

Marking a Draft "Approved" in Airtable is a content decision. Turning that
into committed HTML is a separate, mechanical step — the PR is a second
checkpoint where you can preview the actual rendered page (spacing, line
breaks, how a long title wraps) before it's live, and fix anything that
only becomes obvious once it's laid out.

Because of that, this script never touches the Status field — a Draft
stays "Approved" forever, even after it's published. What it does track is
the **GitHub PR URL** field: blank means "not picked up yet", filled in
means "already has a PR". If you close a PR without merging it and want
that draft re-picked-up from scratch, just clear its GitHub PR URL field in
Airtable — the next run will treat it as new again.

### If more than one PR is open at a time

Each run bundles everything it finds into a single PR, so this only comes
up if you merge one PR and then, before the *next* scheduled run, approve
more drafts that get bundled into a second PR — both would have edited the
same spot in `insights.html` from the same starting point. GitHub will
flag this as a merge conflict when you go to merge the second one; resolve
it the normal way (GitHub's web editor can usually do it in a couple of
clicks since the conflict is just "two new cards both added at the top of
the same list") — nothing is lost, and it doesn't require reverting anything.

### Stage 3 — One-time setup

No new secret is needed — this stage reuses `AIRTABLE_PAT` and the
`GITHUB_TOKEN` that GitHub Actions provides to every workflow automatically.
The workflow file's `permissions:` block (already set in
`publish-approved.yml`) grants `contents: write` and `pull-requests: write`,
but that alone isn't enough — GitHub also has a **repo-level setting** that
blocks the built-in token from opening PRs by default, separately from the
workflow file. Turn it on once, before the first real run:

1. In your repo on GitHub, go to **Settings → Actions → General**.
2. Scroll to **Workflow permissions**.
3. Check **"Allow GitHub Actions to create and approve pull requests."**
4. Click **Save**.

Skip this and the run will get all the way to opening the PR, then fail
with `"GitHub Actions is not permitted to create or approve pull requests"`
(403) — the branch will already have been pushed at that point, so you'll
also see a stray `editorial/publish-*` branch on GitHub with no matching
PR. That branch is harmless: once this setting is on, the next run picks
the same Draft back up (its Status is still "Approved" and its GitHub PR
URL is still blank) and opens a fresh PR normally. You can delete the old
stray branch or just ignore it.

### Stage 3 — Triggering a run manually (to test it)

Same pattern as the other stages: **Actions** tab → **Publish Approved
Drafts** in the sidebar → **Run workflow**. If nothing is Approved yet,
you'll see:

```
No Approved, unpublished Blog drafts found — nothing to do. Exiting cleanly.
```

To generate something to test with: open a Draft in Airtable, set its
Status to `Approved` (it needs Target `Blog`, which is already the default
for anything Stage 2 writes), then trigger a run. You should see a new
branch and pull request appear on GitHub, and that Draft's GitHub PR URL
field fill in with a link to it.

## Troubleshooting

**"A run finished green, but nothing seems to have happened."** A
successful (green) run in GitHub's Actions tab only means the script didn't
crash — it can still legitimately find "0 to do" and exit cleanly, which
looks identical to a silent failure at a glance. Before assuming something
is broken, open the run's log and check what it actually reports finding.
Two specific gotchas to check first:

- **Fact-Check Status vs. Status, on a Draft.** These are two separate
  fields in the Drafts table and it's easy to update the wrong one. Stage 3
  only looks at the **Status** field (it must be exactly `Approved`) — it
  ignores **Fact-Check Status** entirely (that field is for your own
  tracking of whether you've verified the draft's facts against the source
  article). Setting Fact-Check Status to "Verified" but leaving Status at
  "Needs Review" means Stage 3 correctly finds nothing to publish and exits
  cleanly — no error, just a no-op. Make sure it's **Status** that's set to
  `Approved`.
- **The GitHub Actions PR-permission repo setting** (see "Stage 3 —
  One-time setup" above) — if it's off, you'll see an explicit 403 error in
  the log rather than a silent no-op, but it's easy to miss since the run
  still shows a partial success (the branch and content were created; only
  the PR failed).

**If you're extending these scripts**: every Airtable list/GET call in
`source_monitor.py`, `relevance_scorer.py`, and `publish_approved.py` passes
`returnFieldsByFieldId=true`. All three scripts read fields by field ID
(e.g. `fldZkd5g4bNhK8ocW`), not by field name, so they stay correct even if
someone renames a column in the Airtable UI — but Airtable's default
list/GET response is keyed by field **name** unless you explicitly ask for
ID-keyed output with that parameter. Drop it from a new call and every
`.get()` against the response will silently return `None` instead of
erroring — exactly the kind of bug that produces a "successful" run that
quietly does nothing (this happened once already; see git history around
2026-09-08 if you want the full story). Record create/update (`POST`/
`PATCH`) calls are unaffected — they already accept field IDs as input keys
regardless of this parameter.

## Stage 4 — Weekly Newsletter: how it works

Once a week, this stage compiles a **draft** email for the Afrinex
newsletter — it never sends anything. Two kinds of content go into it:

1. **"This week on the blog"** — any Drafts row with Target "Blog" that
   already has a GitHub PR URL (meaning Stage 3 has published it, or at
   least opened a PR for it) and hasn't been recapped in a previous
   newsletter yet.
2. **"Worth a quick read"** — Candidates Stage 2 flagged as Status
   "Newsletter Candidate" (relevant, but not enough for a full post) that
   haven't been turned into a newsletter blurb yet. Each gets a short
   (2-3 sentence) blurb written by Claude, strictly from that Candidate's
   own title/source/summary — the same no-invented-facts rule used for
   blog drafts.

Every included item becomes (or already is) a row in the **Drafts** table
(newsletter blurbs get Target "Newsletter"), and all of them get linked
from one new **Weekly Newsletters** row for the week, with its **Status**
set to "Needs Review" and an explicit Editor's Note placeholder in the
compiled email. The email itself is pushed to Buttondown as a **draft
only** — nothing is sent until you open it in Buttondown, add your own
Editor's Note, and click Send yourself. The Weekly Newsletters row's
**Buttondown Draft URL** field is filled in if Buttondown's response
includes one; if not, the run's log tells you to check your Buttondown
dashboard's Drafts tab directly rather than guessing at a link.

One run never processes the same blog post or the same Candidate twice:
once a Draft is linked into a Weekly Newsletters row, it's excluded from
future runs automatically (via the same link field, in both directions).
A blurb that fails to write (a bad model response, a network hiccup) is
just skipped for this run — the Candidate stays uncovered and gets picked
up again automatically next week, with no separate retry step needed.

## Stage 4 — One-time setup

This stage needs one new secret: a **Buttondown API key**.

1. **Create a Buttondown account** at [buttondown.email](https://buttondown.email)
   if you don't already have one (this is the "Decision: Buttondown" ESP
   from the Claude Project doc).
2. **Get your API key** from your Buttondown account's settings/API page.
3. **Add it as a GitHub repo secret**, the same way as the others:
   **Settings → Secrets and variables → Actions → New repository secret**.
   - Name: `BUTTONDOWN_API_KEY`
   - Value: paste the key.
   - Save.
4. **Optional but recommended**: open
   `.github/workflows/weekly-newsletter.yml` and set `SITE_BASE_URL` to
   your real site's URL (e.g. `https://marvindarvis.com` or your GitHub
   Pages URL) so the blog recap section can link straight to your Insights
   page. If you leave it blank, the recap just mentions the post by title
   without a link.

## Stage 4 — Cost controls

- **`MAX_MENTIONS_PER_RUN`** (default 15) caps how many new newsletter
  blurbs get drafted in one run — the blog recap section isn't capped
  separately since it only ever includes posts Stage 3 already published,
  which is inherently a small, human-gated number.
- Uses one Claude call per new mention (default `claude-sonnet-5`,
  overridable via `NEWSLETTER_MODEL`) — no separate cheap triage pass,
  since Stage 2 already did that work when it decided something was a
  "Newsletter Candidate" in the first place.

## Stage 4 — Triggering a run manually (to test it)

Same pattern as the other stages: **Actions** tab → **Weekly Newsletter**
in the sidebar → **Run workflow**. If there's nothing new to include,
you'll see:

```
Nothing new for this week's newsletter — nothing to do. Exiting cleanly.
```

Otherwise, watch for a line like:

```
Found 1 blog post(s) to recap and 2 new newsletter mention(s) to draft (cap 15).
```

Then check: Airtable should show a new Weekly Newsletters row (Status
"Needs Review") linked to the relevant Drafts, and your Buttondown
account should show a new draft email under its Drafts tab.

## Stage 4 — Known limitation

The blog recap links to your site's general Insights page, not the exact
new post's URL — the automation never persists the exact filename Stage 3
generates for a published post anywhere in Airtable, so there's nothing
precise to link to yet. This is fine in practice (the post is right at the
top of the Insights listing), but if you'd like an exact deep link instead,
that would mean having Stage 3 write the generated filename/URL back to
the Draft record for Stage 4 to read later — a reasonable small
enhancement if it bothers you.

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

All four planned stages are built. The Editorial OS is now a complete
loop: sources are monitored daily, candidates are scored and drafted,
approved drafts become live pages through a PR you merge, and a weekly
newsletter draft is compiled automatically for you to review and send.
Nothing at any stage reaches your site or your subscribers' inboxes
without you taking an explicit action first (merging a PR, clicking Send
in Buttondown).

Possible future refinements (not required, just ideas):
- Persist each published post's exact filename/URL so Stage 4's blog
  recap can link directly to it instead of the general Insights page.
- Build the PubMed saved-search RSS feeds noted in "Known source issues"
  below, to bring the blocked/needs-follow-up sources online.
