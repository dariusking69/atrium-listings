#!/usr/bin/env python3
"""
Generate a self-contained listings widget for each multifamily COMMUNITY, pinned to
only that building's available units.

How communities are found: the AppFolio public feed is available-only (a unit drops
off when it leases). Units in one building share exact coordinates, so we cluster
listings.json by (lat,lng); each cluster with >= MIN_UNITS is a community.

Output: communities/<slug>/ — a fully self-contained site (widget.html, listings.json,
homes/<id>.html, static/) ready to iframe on that community's website. Plus
communities/index.html, a menu of every community with its embed URL.

Run `python3 build.py` first (it refreshes listings.json), then `python3 build_communities.py`.
Rename a community heading by editing NAMES below (keyed by slug).
"""
import json, re, shutil
from collections import defaultdict
from pathlib import Path
import build  # reuse DETAIL_TPL, build_detail_page(), esc()

HERE = Path(__file__).parent
OUT = HERE / "communities"
PAGES_BASE = "https://dariusking69.github.io/atrium-listings/communities"
MIN_UNITS = 4

# Optional: friendly community names keyed by generated slug (override the address label).
# (Pulled from each listing's own marketing title; confirm exact wording with marketing.)
NAMES = {
    "winter-springs-07-loblolly-ct": "The Avenues at Winter Springs",
}


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def community_label(address):
    """Best-effort building name from an address: street (minus unit) + city."""
    parts = [p.strip() for p in address.split(",")]
    # last part is usually "ST 12345"; city is the part before it
    city = ""
    for i in range(len(parts) - 1, -1, -1):
        if re.match(r"^[A-Z]{2}\s*\d{5}", parts[i]):
            city = parts[i - 1] if i - 1 >= 0 else ""
            break
    if not city and len(parts) >= 2:
        city = parts[-2]
    street = parts[0]
    street = re.sub(r"\s*(unit|apt|apartment|bldg|suite|ste|#).*$", "", street, flags=re.I)
    street = re.sub(r"\s*-\s*\d[\w-]*$", "", street).strip(" ,#-")
    return street, city


def clusters(listings):
    g = defaultdict(list)
    for l in listings:
        g[(round(l.get("lat") or 0, 5), round(l.get("lng") or 0, 5))].append(l)
    return sorted([c for c in g.values() if len(c) >= MIN_UNITS], key=lambda c: -len(c))


def build_one(units):
    units = sorted(units, key=lambda x: (x.get("rent_val") or 0))
    street, city = community_label(units[0]["address"])
    slug = slugify(f"{city}-{street}") or f"community-{units[0]['id']}"
    label = NAMES.get(slug) or (f"{street} — {city}" if city else street)
    d = OUT / slug
    (d / "homes").mkdir(parents=True, exist_ok=True)
    if (d / "static").exists():
        shutil.rmtree(d / "static")
    shutil.copytree(HERE / "static", d / "static")
    (d / "listings.json").write_text(json.dumps(units, indent=2), encoding="utf-8")
    for l in units:
        (d / "homes" / f"{l['id']}.html").write_text(build.build_detail_page(l), encoding="utf-8")
    # widget.html: inject heading + title
    w = (HERE / "widget.html").read_text()
    head = f'<div class="atr-head">{build.esc(label)}<span>Available units</span></div>'
    w = w.replace("<!--ATR-HEAD-->", head)
    w = w.replace("<title>Residential Listings — Atrium</title>",
                  f"<title>{build.esc(label)} — Available Units</title>")
    (d / "widget.html").write_text(w, encoding="utf-8")
    return {"slug": slug, "label": label, "units": len(units), "city": city,
            "url": f"{PAGES_BASE}/{slug}/widget.html"}


MENU_TPL = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Atrium — Community Listing Widgets</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Open+Sans:wght@400;600;700&display=swap">
<style>
 body{font-family:"Open Sans",Arial,sans-serif;color:#121212;max-width:900px;margin:0 auto;padding:32px 20px 60px}
 h1{font-size:26px}.sub{color:#555;margin-bottom:24px}
 .c{border:1px solid #e6e6e6;border-radius:12px;padding:16px 18px;margin-bottom:14px}
 .c h2{font-size:18px;margin:0 0 2px}.c .m{color:#777;font-size:13px;margin-bottom:10px}
 .c a{color:#f13d3d;font-weight:700;text-decoration:none}
 code{display:block;background:#f5f5f7;border-radius:8px;padding:10px;font-size:12px;overflow-x:auto;white-space:pre;margin-top:8px}
</style></head><body>
<h1>Atrium — community listing widgets</h1>
<div class="sub">One self-contained widget per multifamily community, showing only that building's available units. Embed each on its own site via the iframe below.</div>
__CARDS__
</body></html>"""


def menu(built):
    cards = []
    for b in built:
        iframe = (f'<iframe src="{b["url"]}" title="{build.esc(b["label"])}" '
                  f'style="width:100%;height:1400px;border:0;" loading="lazy" '
                  f'referrerpolicy="no-referrer-when-downgrade"></iframe>')
        cards.append(
            f'<div class="c"><h2>{build.esc(b["label"])}</h2>'
            f'<div class="m">{b["units"]} available units · <a href="{b["slug"]}/widget.html" target="_blank">preview →</a></div>'
            f'<code>{build.esc(iframe)}</code></div>')
    (OUT / "index.html").write_text(MENU_TPL.replace("__CARDS__", "\n".join(cards)), encoding="utf-8")


def main():
    listings = json.loads((HERE / "listings.json").read_text())
    for l in listings:  # ensure 'available' present (build.py adds it; be safe offline)
        l.setdefault("available", build.derive_available(l.get("terms")))
    if OUT.exists():
        shutil.rmtree(OUT)  # clear stale community folders (buildings that emptied out)
    OUT.mkdir(exist_ok=True)
    built = [build_one(c) for c in clusters(listings)]
    menu(built)
    print(f"Built {len(built)} community widgets ({sum(b['units'] for b in built)} units) in communities/")
    for b in built:
        print(f"  {b['units']:2d}u  {b['slug']:42s} {b['label']}")


if __name__ == "__main__":
    main()
