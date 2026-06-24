# Atrium Residential Listings — Website Handoff

A custom, self-hosted replacement for the listings page on **meetatrium.com/residential-listings**.
It looks and behaves like the current page — **map with pins + filterable card grid** — but it's
ours. Listings come straight from AppFolio's public feed and refresh automatically, so there's
nothing to maintain and **no more DevSpecial widget** ($8/mo).

---

## 1. Preview it first

Open this link in any browser. This is exactly what will appear on the site:

**https://dariusking69.github.io/atrium-listings/widget.html**

---

## 2. Put it on the website (Squarespace) — ~5 minutes

1. Edit the **Residential Listings** page (`meetatrium.com/residential-listings`).
2. Find the **existing listings widget** — a **Code Block** containing `mj-widget` /
   `dashboard.devspecial.com`. Also check **Page → Settings → Advanced → Code Injection**, since
   the DevSpecial loader script is sometimes placed there too.
3. Replace that block's contents (or add a new **Code Block**) with this snippet:

   ```html
   <iframe src="https://dariusking69.github.io/atrium-listings/widget.html"
           title="Atrium Residential Listings"
           style="width:100%; height:1600px; border:0;"
           loading="lazy" referrerpolicy="no-referrer-when-downgrade"></iframe>
   ```

4. Make sure the Code Block's **"Display Source" toggle is OFF** (otherwise the code shows as
   plain text instead of rendering).
5. **Save**, then view the **published** page or an **incognito** window to confirm.

### Two normal Squarespace quirks (not problems)
- In the **editor** the Code Block looks **blank** — that's expected. It only renders on the
  **published** page / incognito view.
- Requires a **Core plan or higher**. The current DevSpecial widget already runs on the site,
  so this is already satisfied.

---

## 3. After it's verified live

- You can **cancel the DevSpecial subscription** ($8/mo) once the new page looks right.
- **"Details"** buttons open our own custom listing pages; **"Apply"** opens AppFolio's secure
  application form (unchanged from today).

---

## Notes
- **Map:** currently OpenStreetMap (zero setup, no API key). To switch to literal **Google Maps**
  to match the old map tile-for-tile, we just add a Google Maps API key — ask Alex.
- **Data freshness:** the page rebuilds from AppFolio automatically; new vacancies appear on their
  own. (Source repo: `github.com/dariusking69/atrium-listings`.)
- **Questions:** Alex Odescalchi — aodescalchi@atriummanagement.com
