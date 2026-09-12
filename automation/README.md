# Editorial OS — Automation

This folder holds the automated content pipeline described in the Claude
Project doc (`claude/editorial-automation-plan.md`). As of 2026-09-12 this
is a 5-stage pipeline — the newsletter now publishes to the site itself
instead of emailing subscribers, and there's a new stage that notifies you
with social drafts once something actually goes live:

- **Stage 1 — Source Monitor**: checks your Airtable **Sources** table
  daily and drops any new articles into the **Candidates** table.
- **Stage 2 — Relevance Scorer**: reads those Candidates, scores them
  against your Focus Areas using Claude, and writes a full draft into the
  **Drafts** table for the promising ones.
- **Stage 3 — Publish Approved Drafts**: watches for Drafts (blog posts)
  and Weekly Newsletters rows (Afrinex issues) you've marked "Approved",
  turns each into a real page on the site, and opens a pull request for
  you to merge — nothing goes live without that merge.
- **Stage 3.5 — On-Publish Notify**: fires on every push to `main` (i.e.
  when you merge a Stage 3 PR). Confirms what's now actually live, drafts a
  LinkedIn and a Facebook version of it from the real published text, and
  writes it all back to Airtable — which is what triggers an Airtable
  automation that emails you the link and both drafts.
- **Stage 4 — Weekly Newsletter (Afrinex issue compiler)**: once a week,
  compiles the next Afrinex issue — a recap of the week's published
  post(s) plus short AI-written blurbs for anything Stage 2 flagged as a
  newsletter mention — into a Weekly Newsletters row for you to review
  right there in Airtable. Nothing is emailed to subscribers; Afrinex is
  now a page on your site, published the same deliberate, PR-gated way a
  blog post is.

Only Stage 3 ever touches the site itself, and even then only through a PR
you review. Stage 3.5 only ever reads the live site and writes to
Airtable — it doesn't touch the site or post to any social platform
itself. Every draft this pipeline produces sits in Airtable waiting for
you to read, edit, or approve it, and every social post it drafts sits in
your inbox waiting for you to personally review and publish it.

## How a piece of content moves through the whole pipeline

For a blog post: **New candidate → scored → drafted (Needs Review) → you
set Status "Approved" → Stage 3 opens a PR → you merge it → Stage 3.5
confirms it's live, drafts LinkedIn/Facebook copy, emails you.**

For an Afrinex issue: **Stage 4 compiles the week's content into a Weekly
Newsletters row (Compiled Body, Status "Needs Review") → you read it in
Airtable, optionally add an Editor's Note, and set Status "Approved" →
Stage 3 renders it as a real `afrinex-issue-*.html` page and opens a PR →
you merge it → Stage 3.5 confirms it's live, drafts LinkedIn/Facebook
copy, emails you.**

Notice Stage 3 and Stage 3.5 are shared machinery between blog posts and
Afrinex issues — the same PR-then-merge gate, and the same "confirm it's
live, then draft social copy" notification, apply to both.

## End-to-end live test recipe (do this once, on your real repo)

This walks through the *whole* pipeline in one sitting, on your real
GitHub repo and Airtable base — the fastest way to prove the new
site-publish + notify flow actually works before trusting the schedules.
Each per-stage section below has its own "Triggering a run manually"
instructions; this section just chains them in the right order with the
specific checkpoints to look for. Budget about 15-20 minutes, most of it
waiting for GitHub Actions runs (each takes 1-3 minutes).

**0. One-time setup checklist** (skip anything already done):
   - `AIRTABLE_PAT` and `ANTHROPIC_API_KEY` GitHub secrets are set (Stage 1
     / Stage 2 setup above).
   - **Settings → Actions → General → Workflow permissions** →  "Allow
     GitHub Actions to create and approve pull requests" is checked (Stage
     3 setup above) — otherwise the PR step below will 403.
   - `SITE_BASE_URL` repository **variable** is set (Settings → Secrets
     and variables → Actions → **Variables** tab) to your real site URL —
     otherwise Published URL stays blank, but everything else still works.
   - The two Airtable automations ("Notify Marvin — Blog post live" and
     "Notify Marvin — Afrinex issue live") are switched **on** in your
     base's Automations tab — otherwise you won't get the email at the end.

**1. Test the blog-post half:**
   1. In Airtable's **Drafts** table, either use a draft Stage 2 already
      wrote, or duplicate any row for a disposable test — either way, set
      its **Status** to `Approved` (leave Target as `Blog`).
   2. Trigger **Publish Approved Drafts** manually (Actions tab). Watch the
      log for `Rendered post: ... -> insight-*.html` and `Opened PR:
      https://github.com/.../pull/N`.
   3. Check Airtable: that Draft's **GitHub PR URL** and **Filename**
      fields should now be filled in.
   4. Open the PR on GitHub, glance at the "Files changed" tab (you should
      see the new `insight-*.html` file and an updated `insights.html`
      with a new teaser card), then **merge it**.
   5. Merging pushes to `main`, which fires **On-Publish Notify**
      automatically — you can watch it start under the Actions tab within
      a few seconds. Watch its log for `... confirmed live, social drafts
      written.`
   6. Check Airtable: that Draft's **Live At**, **Published URL**,
      **LinkedIn Post**, and **Facebook Post** fields should now be filled
      in.
   7. Check your email (marvindarvis@gmail.com) — you should get a message
      from the "Notify Marvin — Blog post live" automation with the live
      link and both drafts. If the fields filled in but no email arrived,
      the automation is probably still switched off (see step 0).

**2. Test the Afrinex-issue half** (same shape, different table):
   1. Trigger **Weekly Newsletter** manually (Actions tab) to compile an
      issue — or, if one already exists in **Weekly Newsletters** with
      Status "Needs Review", reuse it.
   2. Open that row in Airtable, read the **Compiled Body**, optionally
      replace the Editor's Note placeholder with a real note, then set
      **Status** to `Approved`.
   3. Trigger **Publish Approved Drafts** again. Watch for `Rendered
      issue: ... -> afrinex-issue-*.html` and a PR opening (it'll bundle
      with any blog posts also awaiting publish, or open on its own).
   4. Merge the PR, same as step 1.4 above.
   5. **On-Publish Notify** fires again automatically — watch for the
      issue's title confirmed live in the log.
   6. Check Airtable and your email the same way as steps 1.6-1.7, this
      time on the Weekly Newsletters row and the "Notify Marvin — Afrinex
      issue live" automation.

**3. Confirm on the live site:** visit your site's Afrinex page and
Insights page and check the new post/issue actually renders correctly and
the "Coming soon" placeholder card can be deleted from `afrinex.html` now
that a real issue exists (see `CONTENT-GUIDE.md`).

If every checkpoint above lands, the whole updated pipeline — generalized
Stage 3, the new Stage 3.5, and the simplified Stage 4 — is confirmed
working end to end on your real infrastructure, not just in a local
mock-up.

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

This is the step that actually turns something approved into a real page on
your site — but it never does that unattended. It opens a pull request for
you to review and merge; nothing reaches the live site on its own. It
handles two kinds of content, side by side:

1. `.github/workflows/publish-approved.yml` runs every 6 hours (cheap to
   check, and it means an approval you make in Airtable doesn't sit for a
   full day before something happens with it), plus `workflow_dispatch`.
2. It runs `automation/scripts/publish_approved.py`, which:
   - **Blog posts**: looks in Drafts for rows where **Status = "Approved"**,
     **Target = "Blog"**, and the **GitHub PR URL** field is still blank.
     For each one, generates a real `insight-*.html` page using the site's
     existing header/nav/footer markup, with the draft's Title, Subtitle,
     Body, SEO Title, and Meta Description filled in — plus a visible
     notice on the page itself disclosing that it was AI-drafted from
     published reporting and reviewed by Marvin before publishing, and a
     "Sources & References" section linking back to the original article.
     Adds a matching teaser card to the top of `insights.html`'s listing.
   - **Afrinex issues**: looks in Weekly Newsletters for rows where
     **Status = "Approved"** and **GitHub PR URL** is still blank. For each
     one, generates a real `afrinex-issue-*.html` page from that row's
     Compiled Body (with any Editor's Note you added shown as a highlighted
     intro), and adds a matching teaser card to afrinex.html's "Latest
     issues" list.
   - Commits everything found in that run — blog posts and Afrinex issues
     together — to **one new branch** and opens **one pull request**, so
     you review one PR instead of several, and so multiple pieces never
     collide trying to edit the same listing file.
3. Once the PR is open, each included row's **GitHub PR URL** field (and
   **Filename** field, recording exactly which file it became) is filled
   in — that's both a convenience (click through from Airtable) and the
   mechanism that stops the same item from being bundled into a second PR
   next time this runs. The Filename is also what Stage 3.5 uses later to
   find and read the real published file.
4. **Review the PR like any other pull request** — click through to the
   "Files changed" tab, or check out the branch locally if you want to see
   it rendered. Once you're happy, merge it — that's the moment it actually
   goes live via GitHub Pages, and the moment Stage 3.5 picks it up (see
   below).
5. One bad draft or issue (missing a linked Candidate, malformed content,
   whatever) never blocks the others — it's logged and skipped, staying
   `Approved` with a blank GitHub PR URL so it's picked up cleanly on a
   future run once whatever was wrong is fixed.

### Why a PR, and how re-runs are prevented

Marking a Draft "Approved" in Airtable is a content decision. Turning that
into committed HTML is a separate, mechanical step — the PR is a second
checkpoint where you can preview the actual rendered page (spacing, line
breaks, how a long title wraps) before it's live, and fix anything that
only becomes obvious once it's laid out.

Because of that, this script never touches the Status field — a Draft or
Weekly Newsletters row stays "Approved" forever, even after it's
published. What it does track is the **GitHub PR URL** field: blank means
"not picked up yet", filled in means "already has a PR". If you close a PR
without merging it and want that item re-picked-up from scratch, clear its
GitHub PR URL field (and Filename field) in Airtable — the next run will
treat it as new again.

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
and Filename fields fill in. Same idea for an Afrinex issue: set a Weekly
Newsletters row's Status to `Approved` and trigger a run.

## Stage 3.5 — On-Publish Notify: how it works

This is the step that closes the loop Stage 3 can't close on its own —
Stage 3 only opens a PR, so it has no way of knowing when (or whether) you
actually merge it.

1. `.github/workflows/on-publish.yml` runs on every push to `main` — which,
   in this repo, only really happens when you merge one of Stage 3's PRs —
   plus `workflow_dispatch`.
2. It runs `automation/scripts/on_publish_notify.py`, which:
   - Looks at every Draft and Weekly Newsletters row that has a **GitHub PR
     URL** but no **Live At** date yet (i.e. published-a-PR-for but not yet
     confirmed live).
   - For each one, asks the GitHub API directly whether that PR is actually
     merged. If not, it's simply skipped — it'll be checked again on the
     next push.
   - If it is merged, reads the real HTML file right off disk (this
     workflow runs after checkout of the now-updated `main`, so the merged
     file is right there), strips it down to plain text, and asks Claude to
     draft a LinkedIn version and a Facebook version of it — strictly from
     that real published text, same no-invented-facts rule as everywhere
     else in this pipeline.
   - Writes **Published URL**, **Live At** (today's date), **LinkedIn
     Post**, and **Facebook Post** back to the row.
3. Writing **Live At** is what triggers an Airtable automation (see "Stage
   3.5 — One-time setup" below) that emails you the live link plus both
   social drafts. This script itself never sends an email and never posts
   anything to LinkedIn or Facebook — you review and post those yourself.
4. Nothing here is destructive or hard to retry: a row only gets picked up
   while its Live At is blank, and merge status is re-checked fresh every
   time, so a slow-to-merge PR just gets checked again on the next push
   with zero side effects in between.

### Stage 3.5 — One-time setup

No new GitHub secret is needed — this reuses `AIRTABLE_PAT`,
`ANTHROPIC_API_KEY`, and the automatic `GITHUB_TOKEN`. Two things to set up
once, though:

1. **`SITE_BASE_URL` repository variable** (shared with Stage 4) — go to
   **Settings → Secrets and variables → Actions → Variables tab** (not
   Secrets — this isn't sensitive) and add a repository variable named
   `SITE_BASE_URL` set to your real site URL (e.g.
   `https://marvindarvis.com/` or your GitHub Pages URL, with a trailing
   slash or not — the scripts handle either). Skip this and Published URL
   is simply left blank; everything else (Live At, the social drafts, the
   email) still works.
2. **Turn on the two Airtable automations** this setup created for you:
   - **"Notify Marvin — Blog post live"** — fires when a Draft's Live At
     becomes non-empty.
   - **"Notify Marvin — Afrinex issue live"** — fires when a Weekly
     Newsletters row's Live At becomes non-empty.

   Both were created in a draft/off state (Airtable's API can't turn an
   automation on for you) — open your base, go to the **Automations** tab,
   find each one, and switch it on. Until you do, Stage 3.5 will still
   confirm things are live and fill in the social drafts, but you won't get
   an email about it.

### Stage 3.5 — Triggering a run manually (to test it)

Same pattern: **Actions** tab → **On-Publish Notify** in the sidebar →
**Run workflow**. If nothing has an open PR waiting on confirmation, you'll
see:

```
No published-but-unconfirmed rows found — nothing to do. Exiting cleanly.
```

To generate something to test with: merge any Stage 3 PR normally (that
push to `main` triggers this workflow automatically — you don't need to
run it manually at all in normal use), or run it manually right after a
merge if you don't want to wait. Then check the relevant Draft or Weekly
Newsletters row in Airtable for Published URL / Live At / LinkedIn Post /
Facebook Post, and check your email for the notification once you've
turned the automations on.

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

**If you rename or add a Taxonomy entry** (Airtable → Taxonomy table): go
ahead — `relevance_scorer.py` reads the Taxonomy table's Name/Description
fresh on every run and uses whatever's there, both to prompt Claude and to
validate what it comes back with. You don't need to touch any script.
The one thing that used to be fragile: writing a brand-new Taxonomy name
onto a Candidate or Draft's Focus Areas field would fail if that exact
option didn't already exist as a choice on that field. `update_candidate()`
and both `create_draft()` functions (in `relevance_scorer.py` and
`weekly_newsletter.py`) now pass `typecast: true` on every write, so
Airtable auto-creates the choice instead of rejecting the whole update —
including the Status change bundled into the same call, which is what made
this failure mode nasty (a Candidate would silently get stuck on "New" and
re-scored, and re-billed, on every future run). (Fixed 2026-09-12, when the
Taxonomy was realigned from the site's old "Focus Areas" wording to its
current "Professional Interests" — see git history if you want the full
story.)

**If you're calling Claude's Messages API in these scripts**: never assume
`content[0]` is the text block in the response. In practice, calls to
`claude-sonnet-5` have come back with a `"thinking"` block first (even
without deliberately enabling extended thinking) and the actual JSON text
in a later block — a naive `payload["content"][0]["text"]` read breaks on
that shape with a `KeyError`, logged as "Unexpected Claude response
shape." Both `relevance_scorer.py` and `weekly_newsletter.py` instead scan
every block in `content` and concatenate whichever ones have
`"type": "text"`, which is safe regardless of what other block types show
up before or after it. (Found and fixed 2026-09-09, during Stage 4's
rollout — see git history if you want the full story.)

## Stage 4 — Weekly Newsletter (Afrinex issue compiler): how it works

Once a week, this stage compiles the content for the next **Afrinex
issue** — published as a real page on the site once you approve it, not
emailed to anyone. Two kinds of content go into it:

1. **"This week on the blog"** — any Drafts row with Target "Blog" that
   already has a GitHub PR URL (meaning Stage 3 has published it, or at
   least opened a PR for it) and hasn't been recapped in a previous issue
   yet.
2. **"Worth a quick read"** — Candidates Stage 2 flagged as Status
   "Newsletter Candidate" (relevant, but not enough for a full post) that
   haven't been turned into a blurb yet. Each gets a short (2-3 sentence)
   blurb written by Claude, strictly from that Candidate's own
   title/source/summary — the same no-invented-facts rule used for blog
   drafts.

Every included item becomes (or already is) a row in the **Drafts** table
(newsletter blurbs get Target "Newsletter"), and all of them get linked
from one new **Weekly Newsletters** row for the week, with its **Status**
set to "Needs Review" and the full compiled text written into its
**Compiled Body** field, with an explicit Editor's Note placeholder at the
top. Read it there in Airtable, replace the placeholder in the **Editor's
Note** field with your own take on the week if you want one, and when
you're happy, set that row's **Status** to `Approved`. That's it — nothing
publishes automatically. Stage 3 (the same workflow that publishes blog
posts) is what picks up an Approved issue and turns it into a real
`afrinex-issue-*.html` page via a PR, exactly like a blog post.

One run never processes the same blog post or the same Candidate twice:
once a Draft is linked into a Weekly Newsletters row, it's excluded from
future runs automatically (via the same link field, in both directions).
A blurb that fails to write (a bad model response, a network hiccup) is
just skipped for this run — the Candidate stays uncovered and gets picked
up again automatically next week, with no separate retry step needed.

## Stage 4 — One-time setup

No new secret is needed beyond `AIRTABLE_PAT` and `ANTHROPIC_API_KEY`,
which Stage 2 already requires. (Buttondown has been removed entirely —
Afrinex issues publish to the site now, not to an email list, so there's
nothing to configure there anymore. If you still have a Buttondown
account/API key lying around from before, it's simply unused; feel free to
cancel it.)

**Optional but recommended**: set the `SITE_BASE_URL` repository variable
described in "Stage 3.5 — One-time setup" above — it's shared by both
stages, so you only need to set it once, and it's what lets the blog recap
section link straight to your Insights page.

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
Nothing new for this week's issue — nothing to do. Exiting cleanly.
```

Otherwise, watch for a line like:

```
Found 1 blog post(s) to recap and 2 new mention(s) to draft (cap 15).
```

Then check Airtable: you should see a new Weekly Newsletters row (Status
"Needs Review") with its Compiled Body filled in, linked to the relevant
Drafts. Set its Status to "Approved" and trigger a Publish Approved Drafts
run (see Stage 3 above) to see it become a real page.

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

All five stages are built. The Editorial OS is now a complete loop:
sources are monitored daily, candidates are scored and drafted, approved
content (blog posts and Afrinex issues alike) becomes a real page through
a PR you merge, and once it's live you get an email with LinkedIn and
Facebook drafts ready for you to personally review and post. Nothing at
any stage reaches your site, your inbox, or any social platform without
you taking an explicit action first (approving something in Airtable,
merging a PR, or clicking post on LinkedIn/Facebook yourself).

The "Join Afrinex" signup form on afrinex.html is still Formspree, per
your choice — each signup emails you directly with their name, email, and
what drew them to Afrinex, and you reach out personally from there. There
is no separate running subscriber list/table in Airtable; if that ever
becomes worth building (e.g. once signups get too frequent to track from
email alone), it's a small, well-scoped addition — a Subscribers table
plus swapping the form's target.

Possible future refinements (not required, just ideas):
- Build the PubMed saved-search RSS feeds noted in "Known source issues"
  below, to bring the blocked/needs-follow-up sources online.
- If Formspree-by-email ever feels like too much manual bookkeeping, add
  an Airtable Subscribers table + form so Afrinex signups are tracked in
  one place alongside everything else.
