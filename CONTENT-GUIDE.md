# Making content changes yourself

This is a plain HTML site — no CMS, no build step. Every page is its own
`.html` file that you (or a text editor's "Find in Files") can edit directly.
This guide covers the changes we just made, as templates for doing similar
ones yourself next time.

The one thing to always keep in mind: **the header (nav) and footer are not
shared automatically** — they're copy-pasted into all 8 pages (`index.html`,
`about.html`, `focus-areas.html`, `insights.html`, `insight-post.html`,
`community.html`, `afrinex.html`, `contact.html`). Anything you want to
change "everywhere" — your name, your tagline, a nav label — has to be
changed in all 8 files, not just one. The only things that live in a single
shared file are colors/fonts (`css/style.css`) and the small nav/scroll
behaviors (`js/main.js`).

## Tools that make this easy

You don't need to open all 8 files by hand. In VS Code:

1. Press `Cmd/Ctrl + Shift + F` to open **Search in Files**.
2. Type the exact text you want to find (e.g. `Marvin Davis Odhiambo`).
3. Click the **Replace** field that appears, type the new text, and either
   replace one at a time or use "Replace All".
4. Always search first and eyeball the list of matches before replacing —
   some words (like "Community") can appear in more than one place with
   different meanings (a nav label vs. a page's own heading vs. a dropdown
   option), so a blind "replace all" can change something you didn't mean to.

## Common changes, step by step

### 1. Change your name or title/tagline everywhere
Search for the exact current text (e.g. `Marvin Davis Odhiambo` or
`Founder of Afrinex, Psychologist & Behavioral Health Innovator`) and
replace it across all files. It will appear in: the nav brand (top of every
page), each page's `<title>`, several `<meta name="description">` tags, and
the footer tagline + copyright line.

### 2. Rename a nav/footer link's label (like Community → Afrinex, or Focus Areas → Interests)
Search for the exact link, e.g.:
```html
<a href="focus-areas.html">Focus Areas</a>
```
and replace just the visible text (`Focus Areas` → `Interests`), keeping the
`href` the same. That way the link still goes to the same page — you're
only changing what it's called in the menu. Do this in the `<nav class="nav">`
block (near the top) and the `<nav class="footer-links">` block (near the
bottom) of every page.

If you also want the destination page itself to feel renamed (its own
heading, browser tab title, etc.), you'll edit that one page's `<title>`,
`<span class="eyebrow">`, and `<h1>` separately — renaming the nav label
doesn't touch the page's own content.

### 3. Edit the Home page's Biography / Leadership & Affiliations / Education / Professional Interests
These all live in `index.html`, as separate `<section>` blocks in order,
each with an HTML comment above it like:
```html
<!-- ===================== BIOGRAPHY ===================== -->
```
Find the section by its comment, and edit the text inside the `<p>` tags
(Biography) or `<li>` tags (Leadership & Affiliations, Education), or the
`<h3>` / `<p>` pairs inside each `.card` (Professional Interests). Keep the
surrounding tags (`<section>`, `<div>`, etc.) intact — just change the words
between them.

### 4. Expand on a Professional Interest in more depth
The short version lives on the Home page (`index.html`, "Professional
Interests" section). The expanded version lives on `focus-areas.html` (now
labeled "Interests" in the nav), as a numbered `.detail-row` per topic. Add
or edit paragraphs inside the matching `<div class="detail-row">` block —
each one already has its own `<h3>` heading, so just find the topic by name.

### 5. Add a new insight/blog post
This one's already documented — see the "Adding a new insight post" section
in `README.md`. Short version: duplicate `insight-post.html`, update its
title/heading/body, then link to it from `insights.html` (and optionally
add a short teaser card to the "Writing/Publications" section on
`index.html` too, following the existing `.post-card` pattern).

### 6. Add your real social links
Every placeholder link looks like:
```html
<a href="#" aria-label="X / Twitter — add your profile link" title="Add your X URL">X</a>
```
Replace the `href="#"` with your real profile URL, e.g.
`href="https://x.com/yourhandle"`, and add `target="_blank" rel="noopener"`
so it opens in a new tab (that's exactly what we already did for your
LinkedIn link — search `linkedin.com/in/marvin-davis0` to see the pattern).

## A note on the automation

If you ever change your name, tagline, or nav labels again, also check
`automation/scripts/publish_approved.py` — it contains its own copy of the
same header/footer, used whenever the Editorial OS automatically generates
a new insight post page. It's easy to forget since it's not a `.html` file.

## When to just ask Claude instead

Site-wide renames (name, tagline, nav labels) touch 8+ files and are exactly
the kind of repetitive, easy-to-get-slightly-wrong task that's faster and
safer to hand back to Claude — just describe the change the way you did
this time. This guide is for the smaller, single-page edits you'll want to
make on your own in between.
