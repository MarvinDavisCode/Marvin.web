# Marvin Darvis — Personal Website

A static, dependency-free personal brand site: bio, focus areas, insights/writing,
a community page, the Afrinex newsletter, and contact. No build step — open
`index.html` directly or deploy the folder as-is.

## Structure

```
marvin-site/
├── index.html          Home
├── about.html           Full bio (with photo)
├── focus-areas.html     The 6 focus areas
├── insights.html        Insights/writing listing
├── insight-post.html    One full sample post (template for new posts)
├── community.html       Who it's for + a promo card linking to Afrinex
├── afrinex.html          Afrinex: the standalone weekly newsletter page + signup
├── contact.html         Contact form + direct email
├── css/style.css        All styling (electric cyan / blue brand palette)
├── js/main.js           Mobile nav toggle, active-link highlight, form status handling
├── assets/
│   ├── marvin-headshot.jpg   Photo used on About + Home hero
│   └── afrinex-logo.png      Afrinex brand mark, used on community.html + afrinex.html
├── automation/          Editorial OS: source monitor, scoring, publishing, newsletter
│   ├── README.md            Setup guide — start here
│   ├── requirements.txt
│   └── scripts/
│       ├── source_monitor.py       Stage 1: RSS feeds -> Candidates
│       ├── relevance_scorer.py     Stage 2: Candidates -> scored + Drafts
│       ├── publish_approved.py     Stage 3: Approved Drafts -> PR
│       └── weekly_newsletter.py    Stage 4: weekly recap -> Buttondown draft
└── .github/workflows/
    ├── source-monitor.yml      Daily scheduled run of the source monitor
    ├── relevance-scorer.yml    Daily scheduled run of the relevance scorer
    ├── publish-approved.yml    Runs every 6 hours, opens PRs for approvals
    └── weekly-newsletter.yml   Mondays, compiles a Buttondown newsletter draft
```

Every page shares the same header/footer markup and the same `css/style.css` and
`js/main.js`, so a global style change only needs to happen in one place. Afrinex
is intentionally **not** in the main nav — it's reachable from the Community page
and from the "Explore Afrinex" button in the Home page's CTA band, per how the site
is currently wired.

## Before you launch: 2 things to do

1. **Wire up the forms.** The Afrinex newsletter signup (`afrinex.html`) and the
   contact form (`contact.html`) both point to a placeholder Formspree URL
   (`https://formspree.io/f/your-form-id`). To make them actually send:
   - Create a free account at [formspree.io](https://formspree.io), make a new form,
     and copy the endpoint URL it gives you.
   - Replace every `action="https://formspree.io/f/your-form-id"` with your real
     endpoint (use one form per purpose, or the same one for both — your choice).
   - Prefer Mailchimp or ConvertKit for the newsletter instead? Swap the `<form>`
     block on `afrinex.html` for the embed code those tools provide.
   - `js/main.js` will show a friendly "not configured yet" message in the UI until
     you do this, instead of failing silently.

2. **Add real social links.** Every `<a href="#" ...>` in the footer, contact page,
   and community page is a placeholder for LinkedIn / X / Discord. Search for
   `add your` in the HTML files to find every spot at once.

Your headshot (`assets/marvin-headshot.jpg`) and the Afrinex logo
(`assets/afrinex-logo.png`) are already wired in — About page, Home hero,
Community's Afrinex promo card, and the Afrinex page itself.

## Adding a new insight post

1. Duplicate `insight-post.html`, rename it (e.g. `insight-referral-pathways.html`).
2. Update the `<title>`, the `<h1>`, the `post-tag`, and the body content.
3. On `insights.html`, replace one of the "Coming soon" placeholder cards with a
   real link to your new file, and set its `post-tag` and teaser text to match.
4. Optionally add a matching teaser card to the "Recent insights" section on
   `index.html`.

## Brand palette

All colors, fonts, and spacing live in `css/style.css` under the `:root` block at
the top. By design the site uses only four blues plus white — no other hues —
in an Apple-inspired, typographic, motion-led style:

| Color | Hex | Used for |
|---|---|---|
| Medium Blue | `#0884C8` | Buttons, links, primary accent (the main interactive color) |
| Deep Blue | `#0263AB` | Headings, hover/pressed states, CTA band gradient |
| Dark Blue | `#02559A` | Body copy, secondary text |
| Deep Navy-Blue | `#053D73` | Dark sections — footer, CTA band background |
| White | `#FFFFFF` | Page background; all "tint" shades below are just this navy blended toward white |

```css
:root {
  --white: #FFFFFF;
  --medium-blue: #0884C8;
  --deep-blue:   #0263AB;
  --dark-blue:   #02559A;
  --navy:        #053D73;
  --tint-1: #F1F6FA;  /* light backgrounds — navy blended toward white */
  --tint-2: #E3EEF6;  /* slightly deeper tint, for badges/tags/borders */
  --font-head: -apple-system, BlinkMacSystemFont, "Inter", ...;
  --font-body: -apple-system, BlinkMacSystemFont, "Inter", ...;
}
```

Change these values and the whole site updates — no need to touch individual pages.
Typography is a single professional sans-serif family (system font on Apple
devices, falling back to Inter elsewhere) with weight and size doing the work
instead of multiple typefaces — headlines are bold and tightly tracked, body
text is regular weight, matching an Apple-style editorial look.

**Motion**: `js/main.js` adds two small, dependency-free interactions:
- Cards, section headers, and the CTA band fade and rise into view as you
  scroll to them (`IntersectionObserver`, respects `prefers-reduced-motion`).
- The header condenses and gains a solid blurred background once you scroll
  past the top of the page, the same way Apple's site nav behaves.

Both are pure CSS transitions plus a small JS observer — no animation library.

## Deploying

This is a plain static site — any static host works. Two easy free options:

**GitHub Pages**
1. Create a new GitHub repository and push this folder's contents to it.
2. In the repo settings, go to **Pages**, set the source to the `main` branch
   (root), and save.
3. Your site will be live at `https://<username>.github.io/<repo-name>/` within a
   few minutes.

**Netlify**
1. Go to [netlify.com](https://netlify.com), sign up, and choose **Add new site →
   Deploy manually**.
2. Drag this whole folder into the upload area.
3. Netlify gives you a live URL immediately; add a custom domain under
   **Domain settings** if you have one.

Either way, if you'd rather connect a custom domain (e.g. `marvindarvis.com`),
both platforms have a "Custom domain" setting where you point your domain's DNS
at them — their docs walk through the exact records to add.

## Editorial OS automation

`automation/` and `.github/workflows/` together are the full 4-stage
automated content pipeline: GitHub Actions that check your Airtable Sources
for new articles, score them against your Focus Areas with Claude, write
full drafts for the promising ones into Airtable's Drafts table for your
review, turn approved ones into real pages on the site via a pull request
you merge yourself, and — once a week — compile a Buttondown draft
recapping what's new for the Afrinex newsletter, ready for you to add your
own Editor's Note and send. Nothing reaches the live site or your
subscribers' inboxes without you taking an explicit action first (merging
a PR, clicking Send in Buttondown). See `automation/README.md` for the
one-time setup (Airtable, Anthropic, and Buttondown, all added as GitHub
secrets) and full details on how each stage works.

## Accessibility & performance notes

- Semantic HTML throughout (`header`, `nav`, `main`, `footer`, proper heading order).
- A "Skip to content" link is included on every page for keyboard/screen-reader users.
- Color contrast in the default palette meets WCAG AA for body text; if you change
  accent colors, re-check contrast against the light backgrounds and the dark
  navy footer/CTA sections.
- No JavaScript frameworks — `js/main.js` is small and has no external dependencies,
  so pages load fast even on slow connections.
