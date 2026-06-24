# Atrium Residential Listings — self-hosted

Replaces the $8/mo DevSpecial widget (`dashboard.devspecial.com/mj-widgets.js`)
on `meetatrium.com/residential-listings` with a page **we** own and style.

## How it works

AppFolio publishes every active listing on a **public** page —
`https://atriummanagement.appfolio.com/listings` — and embeds the full set as a
JSON array (address, rent, beds/baths/sqft, photo URL, detail link, coords) in a
`window.googleMap = new GoogleMap({ markers: [...] })` block. No API key, no auth.
Photos are served from AppFolio's CDN (`images.cdn.appfolio.com`). That's the same
data DevSpecial reads — we just render it ourselves.

Each listing's public `/listings/detail/<uuid>` page also exposes the full photo
gallery (~11 photos avg), description, rental terms, pet policy, and the secure
application link — so detail pages are ours too, with no API access.

- `build.py` — fetches the grid feed + every detail page, then writes:
  - `index.html` — the Atrium-branded grid (self-contained, double-click to open).
  - `homes/<id>.html` — a custom detail page per listing (gallery, description,
    terms, pets, "Apply Now"). The Apply button is the ONLY hand-off to AppFolio
    (`/rental_applications/new?...`) — that's the secure submission form we keep.
  - `listings.json` — enriched data cache.
- Detail fetching is **incremental**: listings already cached are skipped, so the
  scheduled rebuild only pulls newly-posted units. Use `--refresh-details` to force all.

## widget.html — the Squarespace listings widget (map + grid)

`widget.html` is the drop-in replacement for the current DevSpecial page. It replicates
that layout exactly: a **sticky map with red pins on the left**, a **card grid on the
right**, and the full filter bar (search, min/max rent, beds, baths, city, zip, sort,
move-in date). Cards match the live design — translucent price badge, availability badge
(date or "NOW"), title, address, bed/bath/sqft icons, and a black **Details** / gray
**Apply** bottom bar. Clicking a pin opens a mini-card popup and highlights its card.

It reads `listings.json` at runtime (so it stays fresh via the GitHub Action) and links
Details → our `homes/<id>.html` pages, Apply → the AppFolio application form.

**Map engine.** The live site uses Google Maps. `widget.html` supports both:
- Leave `GOOGLE_MAPS_API_KEY` blank → renders an OpenStreetMap/CARTO basemap with the
  same red pins. **No key, works instantly** — best for a zero-setup Squarespace paste.
- Set `GOOGLE_MAPS_API_KEY` → renders literal Google Maps, pixel-identical to the live
  page. Needs a Google Cloud Maps JavaScript API key (billing-enabled project).

**Embed on Squarespace:**
1. Host `listings.json` + the `homes/` folder publicly (GitHub Pages / raw GitHub — both
   send open CORS headers, so the widget can `fetch()` them cross-origin).
2. In the `CONFIG` block at the top of the `<script>`, set `DATA_URL` and `DETAIL_BASE`
   to those hosted URLs (and optionally `GOOGLE_MAPS_API_KEY`).
3. Paste everything between the "WIDGET START" / "WIDGET END" markers into a Squarespace
   Code Block, or use the whole file behind an `<iframe>`.

## Refresh the data

```bash
python3 build.py            # fetch live from AppFolio, rebuild
python3 build.py --offline  # rebuild from the saved listings.json (no network)
```

`.github/workflows/refresh-listings.yml` runs `build.py` every 4 hours and commits
any changes — same GitHub Actions pattern as the other Atrium automations.

## Putting it on meetatrium.com (Squarespace)

The site is Squarespace. Three options, easiest first:

1. **iframe embed** — host `index.html` anywhere static (GitHub Pages / Netlify /
   Cloudflare Pages, all free) and drop a Code block on the listings page:
   `<iframe src="https://<your-pages-url>/index.html" style="width:100%;border:0;height:1600px"></iframe>`
   Cleanest swap; keeps our code isolated from Squarespace's.

2. **Code-injection** — paste the contents of `index.html`'s `<style>`/`<body>`/`<script>`
   into a Squarespace Code block. Matches site fonts natively, no iframe height quirks.

3. **Live fetch instead of inlined data** — host `listings.json` next to the page and
   have the front-end `fetch()` it (already same-origin, so no CORS). Lets you refresh
   data without re-deploying the HTML. `build.py` writes both files for this.

## Notes
- `$0` rent in AppFolio = pricing not set yet → shown as "Contact for price", sorted last.
- Design uses Atrium brand: red `#f13d3d`, ink `#121212`, font **IBM Plex Sans** (Google Fonts).
- Header logo is `static/atrium-mark.png`. The `static/` folder must be deployed alongside
  the pages (grid links `static/...`, detail pages link `../static/...`). The white wordmark
  (`atrium-white.png` / `atrium-wordmark.png`) is on hand if a dark header is ever wanted.
- Tweak look in the `TEMPLATE` string at the bottom of `build.py`, then rerun build.
