#!/usr/bin/env python3
"""Check the no-JavaScript surface build.py writes against listings.json.

browse/, llms.txt, sitemap.xml, robots.txt and the JSON-LD on each homes/<id>.html exist
for readers that never run the widget: AI assistants, crawlers, a browser with scripts
off. Nobody on the team looks at those pages, so a break in them is silent. A city page
that drops half its rows still returns 200 and still looks like a tidy table.

This compares every generated file with the feed it was built from.

Run:  python3 build.py --offline && python3 check_static.py
Exits non-zero if anything is wrong.
"""
import json
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

import build

HERE = pathlib.Path(__file__).parent
BASE = build.site_base()
# One request has to carry the whole page. A city table is ~190 bytes a row; these
# ceilings are where a page should be split rather than quietly outgrow a fetch tool.
MAX_CITY_BYTES = 120_000
MAX_STATE_BYTES = 250_000
# The ONLY keys the JSON-LD may carry. Fair Housing: it repeats listing fields. A new
# key here is a decision, and "description" is never one of them.
LD_KEYS = {
    "@context", "@type", "url", "name", "mainEntity", "offers", "potentialAction",
    "address", "streetAddress", "addressLocality", "addressRegion", "postalCode",
    "addressCountry", "geo", "latitude", "longitude", "numberOfBedrooms",
    "numberOfBathroomsTotal", "numberOfFullBathrooms", "numberOfPartialBathrooms",
    "floorSize", "value", "unitCode", "unitText", "image", "businessFunction", "offeredBy",
    "priceSpecification", "priceCurrency", "price", "minPrice", "maxPrice",
    "availability", "availabilityStarts", "target",
}
ROW = re.compile(r'<tr><td><a href="((?:\.\./)+)homes/(\d+)\.html">(.*?)</a></td><td>(.*?)</td>'
                 r'<td>(.*?)</td><td>(.*?)</td><td>(.*?)</td><td>(.*?)</td></tr>')


def local(url):
    """A URL on our own site -> the file GitHub Pages would serve for it."""
    path = url[len(BASE):].lstrip("/")
    f = HERE / path
    return f / "index.html" if path == "" or path.endswith("/") else f


def keys(o):
    if isinstance(o, dict):
        for k, v in o.items():
            yield k
            yield from keys(v)
    elif isinstance(o, list):
        for v in o:
            yield from keys(v)


def main():
    problems = []
    rows = json.loads((HERE / "listings.json").read_text(encoding="utf-8"))
    by_id = {str(l["id"]): l for l in rows}
    areas = build.group_areas(rows)

    # ---- browse pages: every listing exactly once per level, with the feed's own values ----
    for level, pages, ceiling in (
        ("state", [HERE / "browse" / s["slug"] / "index.html" for s in areas], MAX_STATE_BYTES),
        ("city", [HERE / "browse" / s["slug"] / f'{c["slug"]}.html' for s in areas for c in s["cities"]],
         MAX_CITY_BYTES),
    ):
        seen = {}
        for page in pages:
            rel = page.relative_to(HERE)
            if not page.exists():
                problems.append(f"{rel} was not written")
                continue
            text = page.read_text(encoding="utf-8")
            if len(text.encode("utf-8")) > ceiling:
                problems.append(f"{rel} is {len(text.encode('utf-8')):,} bytes (limit {ceiling:,})")
            if build.EHO not in text:
                problems.append(f"{rel} is missing the Equal Housing line")
            found = ROW.findall(text)
            if text.count("<tr><td>") != len(found):
                problems.append(f"{rel}: {text.count('<tr><td>')} rows but only {len(found)} parse")
            for up, lid, addr, rent, beds, baths, sqft, avail in found:
                seen[lid] = seen.get(lid, 0) + 1
                l = by_id.get(lid)
                if l is None:
                    problems.append(f"{rel} lists {lid}, which is not in the feed")
                    continue
                if not (page.parent / up / "homes" / f"{lid}.html").resolve().exists():
                    problems.append(f"{rel} links to homes/{lid}.html, which does not exist")
                want = (build.esc(l["address"]), build.esc(build.rent_text(l)), build.beds_text(l.get("beds")),
                        build._num(l.get("baths")), str(l.get("sqft") or ""),
                        build.esc(build.available_text(l.get("available"))))
                if (addr, rent, beds, baths, sqft, avail) != want:
                    problems.append(f"{rel} row {lid} does not match the feed: {(addr, rent, beds, baths, sqft, avail)}")
                if level == "city" and build.slug(l.get("city")) != page.stem:
                    problems.append(f"{rel} holds {lid}, whose city is {l.get('city')!r}")
        missing = set(by_id) - set(seen)
        twice = [k for k, n in seen.items() if n > 1]
        if missing:
            problems.append(f"{len(missing)} listings are on no {level} page: {sorted(missing)[:8]}")
        if twice:
            problems.append(f"{len(twice)} listings are on more than one {level} page: {twice[:8]}")
    on_disk = {p.relative_to(HERE).as_posix() for p in (HERE / "browse").rglob("*.html")}
    expected = ({"browse/index.html"} | {f'browse/{s["slug"]}/index.html' for s in areas}
                | {f'browse/{s["slug"]}/{c["slug"]}.html' for s in areas for c in s["cities"]})
    if on_disk != expected:
        problems.append(f"browse/ has stray or missing pages: {sorted(on_disk ^ expected)[:8]}")
    top = (HERE / "browse" / "index.html").read_text(encoding="utf-8") if (HERE / "browse" / "index.html").exists() else ""
    for s in areas:
        for href, n in [(f'{s["slug"]}/', len(s["rows"]))] + [(f'{s["slug"]}/{c["slug"]}.html', len(c["rows"]))
                                                             for c in s["cities"]]:
            if not re.search(rf'<a href="{re.escape(href)}">[^<]*</a> \({n}\)', top):
                problems.append(f"browse/index.html does not link {href} with its count ({n})")

    # ---- sitemap.xml and robots.txt ----
    try:
        ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
        locs = [e.text for e in ET.parse(HERE / "sitemap.xml").getroot().findall("s:url/s:loc", ns)]
    except Exception as e:
        locs = []
        problems.append(f"sitemap.xml does not parse: {e}")
    want = ({f"{BASE}/homes/{i}.html" for i in by_id} | {f"{BASE}/browse/"}
            | {f'{BASE}/browse/{s["slug"]}/' for s in areas}
            | {f'{BASE}/browse/{s["slug"]}/{c["slug"]}.html' for s in areas for c in s["cities"]})
    if set(locs) != want or len(locs) != len(want):
        problems.append(f"sitemap.xml has {len(locs)} URLs, expected {len(want)}; "
                        f"differs on {sorted(set(locs) ^ want)[:6]}")
    for u in locs:
        if not local(u).exists():
            problems.append(f"sitemap.xml lists {u}, which is not on disk")
            break
    robots = (HERE / "robots.txt").read_text(encoding="utf-8") if (HERE / "robots.txt").exists() else ""
    if f"Sitemap: {BASE}/sitemap.xml" not in robots or re.search(r"(?im)^disallow:\s*\S", robots):
        problems.append("robots.txt must allow everything and name the sitemap")

    # ---- llms.txt: llmstxt.org shape, live links, the closing line ----
    llms = (HERE / "llms.txt").read_text(encoding="utf-8") if (HERE / "llms.txt").exists() else ""
    lines = llms.splitlines()
    if not (lines and lines[0].startswith("# ") and any(x.startswith("> ") for x in lines[:4])):
        problems.append("llms.txt must open with an H1 and a blockquote summary")
    if not llms.rstrip().endswith(build.EHO):
        problems.append("llms.txt must end with the Equal Housing line")
    first_h2 = next((i for i, x in enumerate(lines) if x.startswith("## ")), len(lines))
    head = "\n".join(lines[:first_h2])
    for x in lines[first_h2:]:
        # The llmstxt.org reference parser throws on any line under an H2 that is not a link item.
        if x.strip() and not x.startswith("## ") and not re.fullmatch(r"- \[[^\]]+\]\([^)]+\)(: .*)?", x):
            problems.append(f"llms.txt: line under an H2 is not a link item: {x[:70]!r}")
    links = re.findall(r"\]\((https?://[^)]+)\)", llms)
    for u in links:
        if u.startswith(BASE) and not local(u).exists():
            problems.append(f"llms.txt links {u}, which is not on disk")
    browse_links = {u for u in links if u.startswith(f"{BASE}/browse/")}
    if browse_links != {u for u in want if "/browse/" in u}:
        problems.append("llms.txt does not link exactly the browse pages that exist")
    for name in ("q", "city", "zip", "beds", "baths", "min", "max", "sort"):
        if f"\n- {name}" not in head and f", {name}:" not in head:
            problems.append(f"llms.txt does not document the widget's {name}= filter")

    # ---- JSON-LD on every detail page ----
    for lid, l in by_id.items():
        page = HERE / "homes" / f"{lid}.html"
        blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                            page.read_text(encoding="utf-8") if page.exists() else "", re.S)
        if len(blocks) != 1:
            problems.append(f"homes/{lid}.html has {len(blocks)} JSON-LD blocks, expected 1")
            continue
        try:
            ld = json.loads(blocks[0])
        except ValueError as e:
            problems.append(f"homes/{lid}.html JSON-LD does not parse: {e}")
            continue
        extra = set(keys(ld)) - LD_KEYS
        if extra:
            problems.append(f"homes/{lid}.html JSON-LD carries keys outside the listing fields: {sorted(extra)}")
        home, offer = ld.get("mainEntity", {}), ld.get("offers", {})
        spec = offer.get("priceSpecification", {})
        checks = (
            ("type", ld.get("@type"), "RealEstateListing"),
            ("url", ld.get("url"), f"{BASE}/homes/{lid}.html"),
            ("city", home.get("address", {}).get("addressLocality"), l.get("city") or None),
            ("state", home.get("address", {}).get("addressRegion"), l.get("state") or None),
            ("zip", home.get("address", {}).get("postalCode"), l.get("zip") or None),
            ("beds", home.get("numberOfBedrooms"), l.get("beds")),
            ("baths", home.get("numberOfBathroomsTotal",
                               (home.get("numberOfFullBathrooms") or 0) + 0.5 * (home.get("numberOfPartialBathrooms") or 0)
                               if "numberOfFullBathrooms" in home else None), l.get("baths")),
            ("sqft", home.get("floorSize", {}).get("value"), l.get("sqft") or None),
            ("photo", home.get("image"), l.get("photo") or None),
            ("rent", spec.get("price", spec.get("minPrice")), l.get("rent_val") or None),
            ("available", offer.get("availabilityStarts") or None, build.available_iso(l.get("available")) or None),
        )
        for what, got, expected_v in checks:
            if got != expected_v:
                problems.append(f"homes/{lid}.html JSON-LD {what}: {got!r}, feed says {expected_v!r}")
        target = ld.get("potentialAction", {}).get("target")
        if target and target != l.get("apply_url"):
            problems.append(f"homes/{lid}.html JSON-LD apply target is not the listing's apply_url")

    # ---- the way in from the JavaScript pages ----
    widget = (HERE / "widget.html").read_text(encoding="utf-8")
    if not re.search(r"<noscript>.*?href=\"%s/browse/\".*?</noscript>" % re.escape(BASE), widget, re.S):
        problems.append("widget.html has no <noscript> link to the browse pages")
    if 'href="browse/"' not in (HERE / "index.html").read_text(encoding="utf-8"):
        problems.append("index.html has no static link to the browse pages")

    n_city = sum(len(s["cities"]) for s in areas)
    print(f"{len(rows)} listings, {len(areas)} state pages, {n_city} city pages, "
          f"{len(locs)} sitemap URLs, {len(links)} llms.txt links")
    if problems:
        print(f"\n{len(problems)} problem(s):")
        for p in problems[:40]:
            print("  x", p)
        return 1
    print("\nOK — 0 problem(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
