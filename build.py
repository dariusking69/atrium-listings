#!/usr/bin/env python3
"""
Build a self-contained, Atrium-branded residential listings site from AppFolio's
PUBLIC data — grid page + a custom detail page for every listing.

Data source (no API key, no auth):
  - Grid feed: window.googleMap markers[] on https://atriummanagement.appfolio.com/listings
  - Per-listing detail: the public /listings/detail/<uuid> page (gallery, description,
    terms, pet policy, application link). Same data the $8/mo DevSpecial widget reads.

Usage:
  python3 build.py                  # fetch grid + detail pages (incremental), rebuild
  python3 build.py --refresh-details # force re-fetch every detail page
  python3 build.py --offline        # rebuild from cached listings.json, no network

Output:
  index.html          grid (self-contained, opens via double-click)
  homes/<id>.html      one custom detail page per listing (with schema.org JSON-LD)
  listings.json        enriched data cache
  browse/              plain-HTML tables per state and city, for readers without JavaScript
  llms.txt, sitemap.xml, robots.txt   how an AI agent or crawler finds all of the above
"""
import datetime, functools, json, re, shutil, sys, ssl, html, urllib.request
from urllib.parse import quote
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

HERE = Path(__file__).parent
SUB = "atriummanagement"
BASE = f"https://{SUB}.appfolio.com"
LISTINGS_URL = f"{BASE}/listings"
# Every lead AppFolio captures carries this string on its guest card. It is the ONLY
# way to tell an Inicio-site lead from a meetatrium.com one, so it must reach BOTH
# branches of apply_url below — the fallback branch shipped without it for months and
# nothing surfaced it. Per-embed overrides ride the widget's &src= at click time.
SOURCE_DEFAULT = "Website"

try:
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _CTX = ssl.create_default_context()
    try:
        _CTX.load_default_certs()
    except Exception:
        pass


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        return urllib.request.urlopen(req, timeout=30, context=_CTX).read().decode("utf-8", "replace")
    except ssl.SSLError:
        return urllib.request.urlopen(url, timeout=30,
                                      context=ssl._create_unverified_context()).read().decode("utf-8", "replace")


# ---------- grid feed ----------
def _extract_markers(html):
    s = html.find("markers: [")
    if s < 0:
        return []
    i = html.find("[", s)
    depth = 0
    for j in range(i, len(html)):
        if html[j] == "[":
            depth += 1
        elif html[j] == "]":
            depth -= 1
            if depth == 0:
                return json.loads(html[i:j + 1])
    return []


def fetch_grid():
    """Walk EVERY page of the public listings feed. AppFolio caps the page (and the
    map markers) at ~300, so page 1 alone silently drops the rest of the portfolio."""
    combined = {}
    page = 1
    while page <= 50:  # safety bound
        url = LISTINGS_URL if page == 1 else f"{LISTINGS_URL}?page={page}"
        markers = _extract_markers(get(url))
        if not markers:
            break
        new = sum(1 for m in markers if m["listing_id"] not in combined)
        for m in markers:
            combined.setdefault(m["listing_id"], m)
        print(f"  page {page}: {len(markers)} listings ({new} new, {len(combined)} total)")
        if len(markers) < 300:  # short page => last page
            break
        page += 1
    if not combined:
        raise RuntimeError("markers[] not found on any page")
    return list(combined.values())


def parse_specs(spec):
    beds = baths = sqft = None
    if re.search(r"studio", spec, re.I):
        beds = 0
    m = re.search(r"([\d.]+)\s*bd", spec, re.I);  beds = float(m.group(1)) if m else beds
    m = re.search(r"([\d.]+)\s*ba", spec, re.I);  baths = float(m.group(1)) if m else baths
    m = re.search(r"([\d,]+)\s*Sq", spec, re.I);  sqft = int(m.group(1).replace(",", "")) if m else sqft
    return beds, baths, sqft


def rent_to_int(rent):
    n = re.findall(r"[\d,]+", rent or "")
    return int(n[0].replace(",", "")) if n else 0


# Normalize source-data city spelling/casing variants so the City dropdown doesn't duplicate.
CITY_FIXES = {"gainsville": "Gainesville", "deland": "DeLand"}


def parse_city(address):
    """City = the comma segment right before 'ST ZIP'. Handles addresses where the
    unit is its own segment, e.g. '1600 Neo Landings Loop, Unit 405, Kissimmee, FL 34744'."""
    parts = [p.strip() for p in (address or "").split(",")]
    city = ""
    for i in range(len(parts) - 1, -1, -1):
        if re.match(r"^[A-Za-z]{2}\.?\s*\d{5}", parts[i]):  # e.g. "FL 34744"
            city = parts[i - 1] if i - 1 >= 0 else ""
            break
    else:
        city = parts[1] if len(parts) > 1 else ""
    return CITY_FIXES.get(city.strip().lower(), city.strip())


def normalize_grid(raw):
    out = []
    for r in raw:
        beds, baths, sqft = parse_specs(r.get("unit_specs", ""))
        parts = [p.strip() for p in r.get("address", "").split(",")]
        out.append({
            "id": r.get("listing_id"),
            "uuid": (r.get("detail_page_url", "").rstrip("/").split("/") or [""])[-1],
            "address": r.get("address", ""),
            "street": parts[0] if parts else "",
            "city": parse_city(r.get("address", "")),
            "rent": r.get("rent_range", ""), "rent_val": rent_to_int(r.get("rent_range", "")),
            "specs": r.get("unit_specs", ""), "beds": beds, "baths": baths, "sqft": sqft,
            "photo": r.get("default_photo_url", ""),
            "appfolio_url": f"{BASE}{r.get('detail_page_url','')}",
            "lat": r.get("latitude"), "lng": r.get("longitude"),
        })
    out.sort(key=lambda x: x["id"] or 0, reverse=True)
    return out


# ---------- detail page ----------
def _clean(s):
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</p>", "\n\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"[ \t]+", " ", html.unescape(s)).strip()


def _items(block):
    return [_clean(x) for x in re.findall(r"<li[^>]*>(.*?)</li>", block, re.S)] if block else []


def fetch_detail(listing):
    h = get(listing["appfolio_url"])
    # ordered unique gallery photos (prefer /large)
    seen, photos = set(), []
    for uid, ext in re.findall(r"images\.cdn\.appfolio\.com/atriummanagement/images/([a-f0-9-]+)/[a-z0-9_]+\.(png|jpe?g)", h):
        if uid not in seen:
            seen.add(uid)
            photos.append(f"https://images.cdn.appfolio.com/atriummanagement/images/{uid}/large.{ext}")
    m = re.search(r'listing-detail__title[^>]*>(.*?)</', h, re.S)
    title = _clean(m.group(1)) if m else listing["street"]
    m = re.search(r'listing-detail__description[^>]*>(.*?)</div>', h, re.S)
    desc = _clean(m.group(1)) if m else ""
    m = re.search(r'js-show-rental-terms[^>]*>(.*?)</ul>', h, re.S)
    terms = _items(m.group(1) if m else "")
    m = re.search(r'js-pet-policy-list[^>]*>(.*?)</ul>', h, re.S)
    pets = _items(m.group(1) if m else "")
    m = re.search(r'rental_applications/new\?listable_uid=([a-f0-9-]+)', h)
    apply_url = (f"{BASE}/listings/rental_applications/new?listable_uid={m.group(1)}"
                 f"&source={quote(SOURCE_DEFAULT)}") if m else \
        f'{listing["appfolio_url"]}{"&" if "?" in listing["appfolio_url"] else "?"}source={quote(SOURCE_DEFAULT)}'
    # Property/portfolio identity: the detail page sidebar carries the community name,
    # its logo and leasing phone. Named communities give e.g. "The Julian"; scattered
    # single-family rentals fall back to the default company portfolio name.
    m = re.search(r'<img alt="([^"]*)"\s+class="sidebar__portfolio-logo"', h)
    prop = _clean(m.group(1)).rstrip("*").strip() if m else ""
    m = re.search(r'sidebar__portfolio-logo"\s+src="([^"]+)"', h)
    logo = m.group(1) if m else ""
    m = re.search(r'portfolio-logo.*?</div>.*?<div[^>]*>\s*([^<]+)<br>\s*([\(\)\d\s.\-]{10,20})<br>', h, re.S)
    phone = m.group(2).strip() if m else ""
    listing.update(property=prop, logo=logo, phone=phone,
                   title=title, description=desc, terms=terms, pets=pets,
                   photos=photos or ([listing["photo"]] if listing["photo"] else []),
                   apply_url=apply_url)
    return listing


def enrich(listings, force=False):
    cache = {}
    cf = HERE / "listings.json"
    if cf.exists():
        for r in json.loads(cf.read_text()):
            cache[r.get("id")] = r
    todo = []
    for l in listings:
        c = cache.get(l["id"])
        if c and not force and c.get("photos") and c.get("property") is not None:
            l.update({k: c[k] for k in ("title", "description", "terms", "pets", "photos", "apply_url",
                                        "property", "logo", "phone") if k in c})
        else:
            todo.append(l)
    if todo:
        print(f"Fetching {len(todo)} detail pages…")
        with ThreadPoolExecutor(max_workers=12) as ex:
            for i, _ in enumerate(ex.map(fetch_detail, todo), 1):
                if i % 25 == 0:
                    print(f"  {i}/{len(todo)}")
    return listings


# ---------- render ----------
def esc(s):
    return html.escape(str(s or ""), quote=True)


def build_detail_page(l):
    photos = l.get("photos") or ([l["photo"]] if l.get("photo") else [])
    thumbs = "".join(
        f'<button class="thumb{" on" if i==0 else ""}" onclick="pick({i})"><img src="{esc(p)}" loading="lazy" alt=""></button>'
        for i, p in enumerate(photos))
    terms = "".join(f"<li>{esc(t)}</li>" for t in l.get("terms", []))
    pets = "".join(f"<li>{esc(p)}</li>" for p in l.get("pets", []))
    desc = esc(l.get("description", "")).replace("\n", "<br>")
    priceTxt = f'{esc(l["rent"])}/mo' if l.get("rent_val", 0) > 0 else "Contact for price"
    city = esc(l["city"])
    repl = {
        "@@TITLE@@": esc(l.get("title") or l["street"]),
        "@@STREET@@": esc(l["street"]),
        "@@CITYSEP@@": ", " + city if city else "",
        "@@PRICE@@": priceTxt,
        "@@SPECS@@": esc(l["specs"]),
        "@@HERO@@": esc(photos[0] if photos else ""),
        "@@THUMBS@@": thumbs,
        "@@DESC@@": desc or "No description provided.",
        "@@TERMS@@": terms or "<li>Contact us for terms.</li>",
        "@@PETS@@": (f'<div class="block"><h3>Pet Policy</h3><ul class="terms">{pets}</ul></div>' if pets else ""),
        "@@APPLY@@": esc(l.get("apply_url") or l["appfolio_url"]),
        "@@PHOTOS@@": json.dumps(photos),
        "@@CANON@@": esc(home_url(l)),
        "@@JSONLD@@": _jsonld_or_empty(l),
    }
    out = DETAIL_TPL
    for k, v in repl.items():
        out = out.replace(k, v)
    return out


def derive_available(terms):
    """From terms like 'Available 8/19/26' -> '8/19/26'; 'Available Now'/none -> 'NOW'."""
    for t in (terms or []):
        m = re.match(r"\s*available\b[:\s]*(.+)", t, re.I)
        if m:
            v = m.group(1).strip()
            if re.search(r"\d", v):
                return re.sub(r"\s+", " ", v)
            return "NOW"
    return "NOW"


def _groups():
    """(uuid -> {pg, mf, mfp, g}, client-group meta) from sync_groups.py. Absent/stale is
    survivable: listings simply carry no PG/MF tag and those scopes disappear from the
    directory. The "_meta" entry carries each client group's FULL property list, including
    properties with zero listed units — that is how a property with no current vacancy
    (Ivy Flats) stays visible to the widget builder instead of silently vanishing."""
    f = HERE / "groups.json"
    if not f.exists():
        return {}, {}
    try:
        d = json.loads(f.read_text())
    except Exception:
        return {}, {}
    meta = d.pop("_meta", {}) if isinstance(d, dict) else {}
    return d, (meta.get("groups") or {})


def build(listings):
    groups, client_groups = _groups()
    for l in listings:
        l["available"] = derive_available(l.get("terms"))
        l["city"] = parse_city(l.get("address", ""))  # re-derive so cached data is corrected too
        m = re.search(r"(\d{5})(?:-\d{4})?\s*$", l.get("address", ""))
        l["zip"] = m.group(1) if m else ""
        l["state"] = parse_state(l.get("address", ""))
        m = re.search(r"\b(?:unit|apt|apartment|#)\s*([A-Za-z0-9-]+)", l.get("street", ""), re.I)
        l["unit"] = m.group(1) if m else ""
        g = groups.get(str(l.get("uuid") or ""), {})
        l["pg"] = g.get("pg", "")
        l["mf"] = bool(g.get("mf"))
        # Client-portfolio group keys (sync_groups.CLIENT_GROUPS). Separate key from `pg`
        # on purpose: `pg` is matched against eight hardcoded labels in w.html and
        # overloading it would silently break every PG embed.
        l["cg"] = [str(x) for x in (g.get("g") or []) if x]
        # MF community name from AppFolio beats the scraped portfolio name (which is
        # generic for ~40% of MF listings); fall back to the scrape for non-MF.
        l["community"] = g.get("mfp") or (l.get("property") or "")
    (HERE / "homes").mkdir(exist_ok=True)
    for l in listings:
        (HERE / "homes" / f'{l["id"]}.html').write_text(build_detail_page(l), encoding="utf-8")
    # prune detail pages for listings no longer in the feed (leased/removed)
    ids = {str(l["id"]) for l in listings}
    for f in (HERE / "homes").glob("*.html"):
        if f.stem not in ids:
            f.unlink()
    grid = INDEX_TPL.replace("/*__DATA__*/", json.dumps(
        [{k: l.get(k) for k in ("id", "street", "city", "rent", "rent_val", "specs", "beds", "photo", "address")} for l in listings],
        separators=(",", ":"))).replace("__COUNT__", str(len(listings)))
    # widget.html (map + grid) is the canonical listings page; keep the plain grid as grid.html
    (HERE / "grid.html").write_text(grid, encoding="utf-8")
    # index.html / bare site URL -> redirect to the widget so there is one listings page
    (HERE / "index.html").write_text(
        '<!DOCTYPE html><meta charset="utf-8"><title>Atrium Residential Listings</title>'
        '<meta http-equiv="refresh" content="0; url=widget.html">'
        '<script>location.replace("widget.html"+location.search+location.hash)</script>'
        '<a href="widget.html">View residential listings</a> '
        '<a href="browse/">Browse rentals by state and city (plain HTML, no JavaScript)</a>',
        encoding="utf-8")
    (HERE / "listings.json").write_text(json.dumps(listings, indent=2), encoding="utf-8")

    # Slim feed for the configurable widget (w.html) + generator. Grid fields only —
    # every embed downloads this once, so keep it small (no descriptions/galleries).
    slim = [{
        "id": l.get("id"), "a": l.get("address", ""), "u": l.get("unit", ""),
        "c": l.get("city", ""), "z": l.get("zip", ""), "p": l.get("community", "") or l.get("property", ""),
        "pg": l.get("pg", ""), "mf": 1 if l.get("mf") else 0,
        "r": l.get("rent", ""), "rv": l.get("rent_val", 0),
        "bd": l.get("beds"), "ba": l.get("baths"), "sf": l.get("sqft"),
        "ph": l.get("photo", ""), "av": l.get("available", "NOW"),
        "lat": l.get("lat"), "lng": l.get("lng"),
        "ap": l.get("apply_url") or l.get("appfolio_url", ""),
        # `af` is the AppFolio listing page. Needed because a client-hosted embed sends
        # unit clicks there (owner-branded, has Contact Us + Schedule Showing) instead of
        # to our Atrium-red detail page, which carries no form and no phone.
        "af": l.get("appfolio_url", ""),
        "g": ",".join(l.get("cg") or []),
    } for l in listings]
    for row in slim:                     # keep the feed small: 49 of 573 rows have a group
        if not row["g"]:
            del row["g"]
    (HERE / "widget-data.json").write_text(json.dumps(slim, separators=(",", ":")), encoding="utf-8")

    # Directory of scopes the generator offers (property communities + cities).
    props, cities = {}, {}
    for l in listings:
        p = (l.get("property") or "").strip()
        if p:
            e = props.setdefault(p, {"name": p, "count": 0, "logo": l.get("logo", ""),
                                     "phone": l.get("phone", ""), "cities": set()})
            e["count"] += 1
            e["cities"].add(l.get("city", ""))
        ct = (l.get("city") or "").strip()
        if ct:
            cities[ct] = cities.get(ct, 0) + 1
    # PGs and Multifamily communities come from AppFolio group membership (sync_groups.py),
    # so a newly onboarded property joins these lists on its own.
    pgs, mfprops = {}, {}
    for l in listings:
        pg = (l.get("pg") or "").strip()
        if pg:
            pgs[pg] = pgs.get(pg, 0) + 1
        if l.get("mf") and (l.get("community") or "").strip():
            n = l["community"].strip()
            e = mfprops.setdefault(n, {"name": n, "count": 0, "cities": set()})
            e["count"] += 1
            e["cities"].add(l.get("city", ""))
    # ---- what the widget can ACTUALLY filter on -------------------------------
    # widget-data.json sets each row's `p` to `community or property`, but the
    # `properties` map above is keyed on `property` alone. Where a property holds
    # differently-named communities (Inicio Tampa -> Westshore Marina Flats, ...)
    # the parent name matches NO listing, so offering it in a picker produces an
    # empty widget. `communities` is therefore the authoritative selectable list,
    # and `portfolios` is the parent grouping used to pick a whole portfolio at once.
    comms, portfolios = {}, {}
    for l in listings:
        name = (l.get("community") or "").strip() or (l.get("property") or "").strip()
        if not name:
            continue
        e = comms.setdefault(name, {"name": name, "count": 0, "logo": l.get("logo", ""),
                                    "phone": l.get("phone", ""), "cities": set()})
        e["count"] += 1
        e["cities"].add(l.get("city", ""))
        parent = (l.get("property") or "").strip()
        if parent:
            f = portfolios.setdefault(parent, {"name": parent, "count": 0, "communities": set(),
                                               "cities": set()})
            f["count"] += 1
            f["communities"].add(name)
            f["cities"].add(l.get("city", ""))
    # A portfolio is only interesting when it groups communities the picker would
    # otherwise have to be found one by one — i.e. more than one child, or a child
    # whose name differs from the parent's.
    portfolios = {k: v for k, v in portfolios.items()
                  if len(v["communities"]) > 1 or v["communities"] != {k}}

    directory = {
        "properties": sorted(({**v, "cities": sorted(x for x in v["cities"] if x)}
                              for v in props.values()), key=lambda x: -x["count"]),
        "communities": sorted(({**v, "cities": sorted(x for x in v["cities"] if x)}
                               for v in comms.values()), key=lambda x: -x["count"]),
        "portfolios": sorted(({**v, "communities": sorted(v["communities"]),
                               "cities": sorted(x for x in v["cities"] if x)}
                              for v in portfolios.values()), key=lambda x: -x["count"]),
        "cities": sorted(({"name": k, "count": v} for k, v in cities.items()), key=lambda x: -x["count"]),
        "pgs": sorted(({"name": k, "count": v} for k, v in pgs.items()), key=lambda x: x["name"]),
        "mfProperties": sorted(({**v, "cities": sorted(x for x in v["cities"] if x)}
                                for v in mfprops.values()), key=lambda x: -x["count"]),
        "mfTotal": sum(1 for l in listings if l.get("mf")),
        "total": len(listings),
        # Client portfolios, keyed on an AppFolio property-group id we pin. `properties`
        # is the group's real membership and `listings` the count live right now — a
        # group with 0 listings this week still belongs in the builder's picker, so the
        # builder must render on `properties`, never on what the feed happens to contain.
        "groups": sorted(({"key": k, "name": v.get("name", k), "id": v.get("id", ""),
                           "properties": sorted(v.get("properties") or []),
                           "units": v.get("units", 0),
                           "listings": sum(1 for l in listings if k in (l.get("cg") or []))}
                          for k, v in client_groups.items()), key=lambda x: x["name"]),
    }
    (HERE / "directory.json").write_text(json.dumps(directory, indent=2), encoding="utf-8")
    # Same rule as the refresh job's sanity tiers: these pages are an extra for crawlers,
    # and a bug in them must never stop rent and availability from publishing. Loud, not fatal.
    try:
        n_pages = build_static(listings)
    except Exception as e:
        n_pages = 0
        print(f"::warning::no-JavaScript pages were NOT rebuilt (browse/, llms.txt, sitemap.xml): {e!r}")
    print(f"Built index.html + {len(listings)} detail pages in homes/ "
          f"+ {n_pages} browse pages, llms.txt, sitemap.xml, robots.txt")


# ---------- plain-HTML surface for readers that do not run JavaScript ----------
# widget.html renders zero listings without JavaScript and listings.json is ~3 MB, more
# than most AI-assistant fetch tools will take, so an assistant asked "what does Atrium
# have in Orlando" had nowhere to look. Everything below is static, small, and rebuilt
# from the same rows on every refresh, so it cannot drift from the widget.
#
# FAIR HOUSING: these pages repeat listing fields and counts and nothing else. Do not add
# copy that describes a home, a neighborhood, or who a place would suit.
#
# NO TIMESTAMPS in any of it (no "updated at", no sitemap <lastmod>): the refresh job
# commits only when a file changed, and a clock in the output would make that every run.
COMPANY = "Atrium Management Company"
COMPANY_URL = "https://www.meetatrium.com/"
EHO = "Atrium is an Equal Housing Opportunity provider."
STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
    "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota",
    "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania",
    "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
    "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


@functools.lru_cache(maxsize=None)
def site_base():
    """https://<the Pages custom domain>. CNAME is the one place that already names it."""
    f = HERE / "CNAME"
    host = f.read_text().strip() if f.exists() else ""
    return f"https://{host or 'listings.meetatrium.com'}"


def home_url(l):
    return f'{site_base()}/homes/{l["id"]}.html'


def parse_state(address):
    """'..., Kissimmee, FL 34744' -> 'FL'. '' when the address does not end in ST ZIP."""
    m = re.search(r",\s*([A-Za-z]{2})\.?\s*\d{5}(?:-\d{4})?\s*$", address or "")
    return m.group(1).upper() if m else ""


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")


def _num(v):
    """2.0 -> '2', 2.5 -> '2.5', None -> ''."""
    if v is None:
        return ""
    return str(int(v)) if float(v) == int(v) else str(v)


def beds_text(b):
    return "" if b is None else ("Studio" if b == 0 else _num(b))


def rent_text(l):
    return l.get("rent", "") if (l.get("rent_val") or 0) > 0 else "Contact for price"


def rent_bounds(l):
    """'$1,280 - $2,064' -> (1280, 2064); a single amount -> (n, n); unpriced -> None."""
    n = [int(x.replace(",", "")) for x in re.findall(r"\d[\d,]*", l.get("rent") or "")]
    n = [x for x in n if x > 0]
    return (min(n), max(n)) if n and (l.get("rent_val") or 0) > 0 else None


def available_iso(av):
    """'10/19/26' -> '2026-10-19'. '' for NOW or anything that is not a plain US date."""
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})", (av or "").strip())
    if not m:
        return ""
    mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    try:
        return datetime.date(y + 2000 if y < 100 else y, mo, d).isoformat()
    except ValueError:
        return ""


def available_text(av):
    """Month spelled out: '10/11/26' reads as 10 November to anyone outside the US."""
    av = (av or "NOW").strip()
    if av.upper() == "NOW":
        return "Now"
    iso = available_iso(av)
    if not iso:
        return av
    d = datetime.date.fromisoformat(iso)
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"


# ---- schema.org JSON-LD for a detail page ----
def listing_jsonld(l):
    """Built ONLY from the listing's own fields. A field the feed leaves empty is left out
    rather than guessed, and there is no description: that is marketing copy."""
    addr = {"@type": "PostalAddress", "addressCountry": "US"}
    parts = [p.strip() for p in (l.get("address") or "").split(",")]
    # Street = everything before the city, so a unit that is its own comma segment
    # ('1600 Neo Landings Loop, Unit 405, Kissimmee, FL 34744') stays in the street line.
    city = l.get("city") or ""
    street = ", ".join(parts[:parts.index(city)]) if city in parts else (l.get("street") or "")
    for k, v in (("streetAddress", street), ("addressLocality", city),
                 ("addressRegion", l.get("state") or parse_state(l.get("address", ""))),
                 ("postalCode", l.get("zip") or "")):
        if v:
            addr[k] = v
    home = {"@type": "Accommodation", "name": l.get("address") or l.get("street") or "",
            "address": addr}
    if l.get("lat") is not None and l.get("lng") is not None:
        home["geo"] = {"@type": "GeoCoordinates", "latitude": l["lat"], "longitude": l["lng"]}
    if l.get("beds") is not None:
        home["numberOfBedrooms"] = int(l["beds"]) if float(l["beds"]) == int(l["beds"]) else l["beds"]
    ba = l.get("baths")
    if ba is not None:
        if float(ba) == int(ba):
            home["numberOfBathroomsTotal"] = int(ba)
        else:
            # schema.org defines numberOfBathroomsTotal as an integer that counts a half
            # bath as one, so 2.5 would have to be written 3. Say what the feed says.
            home["numberOfFullBathrooms"] = int(ba)
            home["numberOfPartialBathrooms"] = 1
    if l.get("sqft"):
        home["floorSize"] = {"@type": "QuantitativeValue", "value": l["sqft"],
                             "unitCode": "FTK", "unitText": "sq ft"}
    if l.get("photo"):
        home["image"] = l["photo"]

    offer = {"@type": "Offer", "businessFunction": "http://purl.org/goodrelations/v1#LeaseOut",
             "offeredBy": {"@type": "Organization", "name": COMPANY, "url": COMPANY_URL}}
    rb = rent_bounds(l)
    if rb:
        spec = {"@type": "UnitPriceSpecification", "priceCurrency": "USD",
                "unitCode": "MON", "unitText": "month"}
        if rb[0] == rb[1]:
            spec["price"] = rb[0]
            offer["price"], offer["priceCurrency"] = rb[0], "USD"
        else:
            spec["minPrice"], spec["maxPrice"] = rb
        offer["priceSpecification"] = spec
    iso = available_iso(l.get("available"))
    if iso:
        offer["availabilityStarts"] = iso
    elif (l.get("available") or "NOW").strip().upper() == "NOW":
        offer["availability"] = "https://schema.org/InStock"

    ld = {"@context": "https://schema.org", "@type": "RealEstateListing",
          "url": home_url(l), "name": home["name"], "mainEntity": home, "offers": offer}
    apply_url = l.get("apply_url") or ""
    if "rental_applications/new" in apply_url:      # the fallback is a listing page, not a form
        ld["potentialAction"] = {"@type": "ApplyAction", "name": "Apply", "target": apply_url}
    return ld


def _jsonld_or_empty(l):
    """A listing the JSON-LD cannot describe still gets its page; see build() on why this
    is a warning. check_static.py reports the empty block."""
    try:
        return _ld_json(listing_jsonld(l))
    except Exception as e:
        print(f"::warning::JSON-LD left empty for listing {l.get('id')}: {e!r}")
        return "{}"


def _ld_json(obj):
    """JSON safe to sit inside <script>: a '</script>' in an address must not end the block."""
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).replace("<", "\\u003c")


# ---- browse pages ----
def group_areas(listings):
    """[{code, slug, name, rows, cities: [{slug, name, rows}]}], biggest first.

    Keyed on the SLUG, not the display name, so two spellings that would land on the same
    file are merged into one page instead of the second silently overwriting the first."""
    states = {}
    for l in listings:
        code = l.get("state") or parse_state(l.get("address", ""))
        st = states.setdefault(slug(code) or "other", {"code": code, "rows": [], "cities": {}})
        st["rows"].append(l)
        c = st["cities"].setdefault(slug(l.get("city")) or "other", {"names": {}, "rows": []})
        c["rows"].append(l)
        name = (l.get("city") or "").strip()
        c["names"][name] = c["names"].get(name, 0) + 1
    by_rent = lambda l: ((l.get("rent_val") or 0) <= 0, l.get("rent_val") or 0, l.get("address") or "")
    out = []
    for sslug, st in states.items():
        cities = [{"slug": cslug, "rows": sorted(c["rows"], key=by_rent),
                   "name": max(c["names"], key=lambda n: (c["names"][n], n)) or "Other"}
                  for cslug, c in st["cities"].items()]
        cities.sort(key=lambda c: (-len(c["rows"]), c["name"]))
        order = {c["slug"]: i for i, c in enumerate(cities)}
        rows = sorted(st["rows"], key=lambda l: (order[slug(l.get("city")) or "other"],) + by_rent(l))
        out.append({"code": st["code"], "slug": sslug, "rows": rows, "cities": cities,
                    "name": STATE_NAMES.get(st["code"], st["code"] or "Other areas")})
    out.sort(key=lambda s: (-len(s["rows"]), s["name"]))
    return out


def _n(count):
    return f"{count} rental" + ("" if count == 1 else "s")


BROWSE_CSS = ("body{margin:0 auto;max-width:980px;padding:20px 16px 48px;color:#121212;background:#fff;"
              "font:15px/1.5 'Open Sans','Helvetica Neue',Helvetica,Arial,sans-serif}"
              "a{color:#121212}h1{font-size:26px;line-height:1.2;margin:14px 0 6px}"
              "h2{font-size:18px;margin:26px 0 6px}.c{color:#424245}"
              ".t{overflow-x:auto}table{border-collapse:collapse;width:100%;margin-top:14px}"
              "th,td{text-align:left;padding:8px 14px 8px 0;border-bottom:1px solid #e6e6e6;white-space:nowrap}"
              "td:first-child{white-space:normal;min-width:220px}"
              "th{font-size:12px;letter-spacing:.04em;text-transform:uppercase;color:#424245}"
              "ul{padding-left:20px;margin:6px 0}footer{margin-top:32px;font-size:13px;color:#424245}")


def _browse_page(path, title, crumbs, h1, body):
    """`path` is relative to browse/. Directory URLs ('fl/') are the canonical form."""
    canon = f"{site_base()}/browse/{path[:-len('index.html')] if path.endswith('index.html') else path}"
    crumb = " / ".join(f'<a href="{esc(h)}">{esc(t)}</a>' if h else esc(t) for t, h in crumbs)
    doc = (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<title>{esc(title)} | {COMPANY}</title><link rel="canonical" href="{esc(canon)}">'
           f'<style>{BROWSE_CSS}</style></head><body>\n<nav class="c">{crumb}</nav>\n'
           f'<h1>{esc(h1)}</h1>\n{body}\n<footer>{EHO}</footer>\n</body></html>\n')
    f = HERE / "browse" / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(doc, encoding="utf-8")


def _rows_table(rows, up):
    """`up` climbs from the page to the site root ('../../' for browse/fl/orlando.html)."""
    tr = "\n".join(
        f'<tr><td><a href="{up}homes/{l["id"]}.html">{esc(l.get("address") or l.get("street"))}</a></td>'
        f'<td>{esc(rent_text(l))}</td><td>{beds_text(l.get("beds"))}</td><td>{_num(l.get("baths"))}</td>'
        f'<td>{l.get("sqft") or ""}</td><td>{esc(available_text(l.get("available")))}</td></tr>'
        for l in rows)
    return ('<div class="t"><table><thead><tr><th>Address</th><th>Rent per month</th><th>Beds</th>'
            f'<th>Baths</th><th>Sq ft</th><th>Available</th></tr></thead><tbody>\n{tr}\n</tbody></table></div>')


def build_browse(areas, total):
    """Write browse/. Returns the page paths (relative to the site root) for the sitemap."""
    shutil.rmtree(HERE / "browse", ignore_errors=True)   # an area with no listings left loses its page
    n_cities = sum(len(s["cities"]) for s in areas)
    paths = ["browse/"]
    body = [f'<p class="c">{_n(total)} listed by {COMPANY}, in {len(areas)} '
            f'state{"" if len(areas) == 1 else "s"} and {n_cities} cit{"y" if n_cities == 1 else "ies"}. '
            f'<a href="../widget.html">Search with a map and filters</a> (needs JavaScript).</p>']
    for s in areas:
        body.append(f'<h2><a href="{s["slug"]}/">{esc(s["name"])}</a> ({len(s["rows"])})</h2>\n<ul>' + "".join(
            f'\n<li><a href="{s["slug"]}/{c["slug"]}.html">{esc(c["name"])}</a> ({len(c["rows"])})</li>'
            for c in s["cities"]) + "\n</ul>")
    body.append('<p class="c">For software: <a href="../llms.txt">llms.txt</a>, '
                '<a href="../listings.json">listings.json</a>, <a href="../sitemap.xml">sitemap.xml</a>.</p>')
    _browse_page("index.html", "Rentals by state and city", [("All areas", "")],
                 "Rentals by state and city", "\n".join(body))
    for s in areas:
        paths.append(f'browse/{s["slug"]}/')
        cities = "".join(f'\n<li><a href="{c["slug"]}.html">{esc(c["name"])}</a> ({len(c["rows"])})</li>'
                         for c in s["cities"])
        _browse_page(f'{s["slug"]}/index.html', f'Rentals in {s["name"]}',
                     [("All areas", "../"), (s["name"], "")], f'Rentals in {s["name"]}',
                     f'<p class="c">{_n(len(s["rows"]))} listed by {COMPANY}.</p>\n<ul>{cities}\n</ul>\n'
                     + _rows_table(s["rows"], "../../"))
        for c in s["cities"]:
            paths.append(f'browse/{s["slug"]}/{c["slug"]}.html')
            place = f'{c["name"]}, {s["code"]}' if s["code"] else c["name"]
            _browse_page(f'{s["slug"]}/{c["slug"]}.html', f"Rentals in {place}",
                         [("All areas", "../"), (s["name"], "./"), (c["name"], "")], f"Rentals in {place}",
                         f'<p class="c">{_n(len(c["rows"]))} listed by {COMPANY}. '
                         f'<a href="../../widget.html?city={quote(c["name"])}">Search these with a map and '
                         f'filters</a> (needs JavaScript).</p>\n' + _rows_table(c["rows"], "../../"))
    return paths


def build_llms_txt(areas, total):
    """llmstxt.org layout: H1, blockquote, free text, then H2 sections that hold ONLY link
    lists. The reference parser reads every line under an H2 as '- [name](url): notes' and
    throws on anything else, which is why the closing Equal Housing line is a list item."""
    base = site_base()
    states = [s["name"] for s in areas]
    where = (", ".join(states[:-1]) + " and " + states[-1]) if len(states) > 1 else "".join(states)
    out = [f"# {COMPANY}: available rentals", "",
           f"> Every home and apartment {COMPANY} currently has for rent: {_n(total)} in {where}. "
           "Each listing has its address, monthly rent, bedrooms, bathrooms, square feet, the date it "
           "is available, photos and a link to apply online. The data is rebuilt from Atrium's "
           "property management system several times a day.", "",
           "The pages linked below are plain HTML and need no JavaScript. To answer a question about "
           "what is available somewhere, open that city's page: it is one small table with a row per "
           f"rental, cheapest first. Each row links to the rental's own page at {base}/homes/<id>.html, "
           "which also carries the same facts as schema.org JSON-LD (RealEstateListing).", "",
           f"Full feed: {base}/listings.json is one JSON array with every listing (about 3 MB, so "
           "prefer the city pages when a fetch has a size limit). Fields on each listing:", "",
           "- id: number. The rental's page is homes/<id>.html.",
           "- address: full address. Also split into street, unit, city, state (two letters) and zip.",
           "- rent: monthly rent as text, one amount or a range such as \"$1,280 - $2,064\".",
           "- rent_val: the lowest monthly rent as a whole number of US dollars. 0 means no price is set yet.",
           "- beds: bedrooms. 0 is a studio. baths: bathrooms, where 2.5 is two full and one half.",
           "- sqft: square feet. specs: beds, baths and square feet as one line of text.",
           "- available: \"NOW\", or the first available date as month/day/year (US order, such as 10/19/26).",
           "- photo: main photo URL. photos: every photo URL.",
           "- lat, lng: map coordinates.",
           "- apply_url: the online rental application for this listing.",
           "- appfolio_url: the same listing on Atrium's AppFolio site, with contact and showing requests.",
           "- title, description, terms, pets: the listing's own headline, description, rental terms and pet policy, as published.",
           "",
           f"Search page: {base}/widget.html is the map and filter search people use. It needs "
           "JavaScript, so do not read it; link a person to it. It accepts these URL parameters, "
           "in any combination:", "",
           "- q: text to match against address, city or ZIP.",
           "- city: a city name exactly as it appears in the feed, such as city=Orlando.",
           "- zip: a five digit ZIP code.",
           "- beds: 0 for studios only, or 1 to 5 for at least that many bedrooms.",
           "- baths: 1, 2 or 3 for at least that many bathrooms.",
           "- min, max: lowest and highest monthly rent in dollars.",
           "- sort: rent_desc (the default), rent_asc or new.",
           "- embed=1: a compact layout for a narrow panel.",
           "",
           f"Example: {base}/widget.html?city=Orlando&beds=2&max=2000", ""]
    out += ["## Browse", "",
            f"- [All areas]({base}/browse/): every state and city with its count of rentals", ""]
    for s in areas:
        out += [f"## {s['name']}", "",
                f"- [{s['name']}]({base}/browse/{s['slug']}/): {_n(len(s['rows']))}"]
        out += [f"- [{c['name']}, {s['code']}]({base}/browse/{s['slug']}/{c['slug']}.html): {_n(len(c['rows']))}"
                for c in s["cities"]]
        out.append("")
    out += ["## Data", "",
            f"- [listings.json]({base}/listings.json): every listing as JSON, fields as described above",
            f"- [sitemap.xml]({base}/sitemap.xml): every browse page and every rental page", "",
            "## About", "",
            f"- [{COMPANY}]({COMPANY_URL}): {EHO}"]
    (HERE / "llms.txt").write_text("\n".join(out) + "\n", encoding="utf-8")


def build_static(listings):
    """browse/, llms.txt, sitemap.xml, robots.txt. Returns the number of browse pages."""
    base = site_base()
    areas = group_areas(listings)
    pages = build_browse(areas, len(listings))
    build_llms_txt(areas, len(listings))
    urls = [f"{base}/{p}" for p in pages] + [home_url(l) for l in listings]
    (HERE / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"<url><loc>{esc(u)}</loc></url>\n" for u in urls) + "</urlset>\n", encoding="utf-8")
    (HERE / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\n\nSitemap: {base}/sitemap.xml\n", encoding="utf-8")
    return len(pages)


# ===================== TEMPLATES =====================
SHARED_CSS = r"""
  :root{--red:#f13d3d;--red-dk:#e82121;--ink:#121212;--ink-2:#424245;--line:#e6e6e6;
    --bg:#f5f5f7;--card:#fff;--radius:14px;--sans:"Open Sans","Helvetica Neue",Helvetica,Arial,sans-serif}
  *{box-sizing:border-box}html,body{margin:0}
  body{font-family:var(--sans);color:var(--ink);background:var(--bg);-webkit-font-smoothing:antialiased}
  a{color:inherit;text-decoration:none}
  header.site{position:sticky;top:0;z-index:30;background:rgba(255,255,255,.86);
    backdrop-filter:saturate(140%) blur(12px);border-bottom:1px solid var(--line)}
  .bar{max-width:1280px;margin:0 auto;padding:16px 24px;display:flex;align-items:center;gap:18px}
  .brand{display:flex;align-items:center}
  .brand img{height:30px;width:auto;display:block}
"""

INDEX_TPL = r"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Residential Listings — Atrium</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Open+Sans:wght@400;500;600;700&display=swap">
<style>__CSS__
  .count{margin-left:auto;font-size:13px;color:var(--ink-2)}.count b{color:var(--ink)}
  .hero{max-width:1280px;margin:0 auto;padding:40px 24px 8px}
  .hero h1{font-size:clamp(28px,4vw,44px);line-height:1.04;margin:0 0 8px;letter-spacing:-.02em}
  .hero p{margin:0;color:var(--ink-2);font-size:16px}
  .filters{position:sticky;top:65px;z-index:20;background:var(--bg);border-bottom:1px solid var(--line)}
  .filters .inner{max-width:1280px;margin:0 auto;padding:14px 24px;display:flex;flex-wrap:wrap;gap:10px}
  .field{position:relative;display:flex;align-items:center}
  .field input,.field select{appearance:none;font:inherit;font-size:14px;color:var(--ink);background:var(--card);
    border:1px solid var(--line);border-radius:999px;padding:10px 16px;outline:none}
  .field input{min-width:240px}.field select{padding-right:36px;cursor:pointer}
  .field.sel::after{content:"";position:absolute;right:14px;pointer-events:none;width:8px;height:8px;
    border-right:2px solid var(--ink-2);border-bottom:2px solid var(--ink-2);transform:rotate(45deg) translateY(-2px)}
  .field input:focus,.field select:focus{border-color:var(--red);box-shadow:0 0 0 3px rgba(241,61,61,.15)}
  .clear{margin-left:auto;align-self:center;font-size:13px;color:var(--red);cursor:pointer;font-weight:600}
  main{max-width:1280px;margin:0 auto;padding:24px}
  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:22px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);overflow:hidden;
    display:flex;flex-direction:column;transition:transform .18s,box-shadow .18s}
  .card:hover{transform:translateY(-4px);box-shadow:0 16px 40px -18px rgba(18,18,18,.35)}
  .ph{position:relative;aspect-ratio:4/3;background:#eceff3}
  .ph img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:0;transition:opacity .35s}
  .ph img.on{opacity:1}
  .price{position:absolute;left:12px;bottom:12px;background:var(--ink);color:#fff;font-weight:700;
    font-size:15px;padding:7px 12px;border-radius:999px}
  .body{padding:15px 16px 17px;display:flex;flex-direction:column;gap:7px;flex:1}
  .specs{font-size:12.5px;font-weight:700;color:var(--red);letter-spacing:.04em;text-transform:uppercase}
  .addr{font-size:15px;font-weight:600;line-height:1.3}.city{font-size:13px;color:var(--ink-2)}
  .cta{margin-top:auto;padding-top:12px}
  .cta span{display:block;text-align:center;font-weight:700;font-size:14px;padding:11px;border-radius:10px;
    background:var(--ink);color:#fff;transition:background .15s}
  .card:hover .cta span{background:var(--red)}
  .empty{text-align:center;padding:80px 20px;color:var(--ink-2)}
  footer{max-width:1280px;margin:0 auto;padding:30px 24px 60px;color:#9aa0a6;font-size:12px}
</style></head><body>
<header class="site"><div class="bar"><a class="brand" href="index.html"><img src="static/atrium-mark.png" alt="Atrium"></a>
  <div class="count"><b id="shown">0</b> of __COUNT__ homes available</div></div></header>
<section class="hero"><h1>Find your next home</h1>
  <p>Browse every available Atrium residential rental — updated straight from our system.</p></section>
<section class="filters"><div class="inner">
  <label class="field"><input id="q" type="search" placeholder="Search by address or city…" autocomplete="off"></label>
  <label class="field sel"><select id="beds"><option value="">Any beds</option><option value="0">Studio</option>
    <option value="1">1+ bed</option><option value="2">2+ beds</option><option value="3">3+ beds</option><option value="4">4+ beds</option></select></label>
  <label class="field sel"><select id="price"><option value="">Any price</option><option value="1500">Under $1,500</option>
    <option value="2000">Under $2,000</option><option value="2500">Under $2,500</option><option value="3000">Under $3,000</option></select></label>
  <label class="field sel"><select id="sort"><option value="rent_asc">Price: Low to High</option>
    <option value="rent_desc">Price: High to Low</option><option value="new">Newest</option></select></label>
  <span class="clear" id="clear">Reset</span></div></section>
<main><div class="grid" id="grid"></div>
  <div class="empty" id="empty" style="display:none">No homes match your filters.</div></main>
<footer>Listings sourced live from Atrium’s property management system. Equal Housing Opportunity.</footer>
<script>
const LISTINGS=/*__DATA__*/;const $=s=>document.querySelector(s);
const grid=$('#grid'),q=$('#q'),beds=$('#beds'),price=$('#price'),sort=$('#sort');
function card(l){const a=document.createElement('a');a.className='card';a.href='homes/'+l.id+'.html';
  const pr=l.rent_val>0?l.rent+'/mo':'Contact for price';
  a.innerHTML=`<div class="ph">${l.photo?`<img loading="lazy" src="${l.photo}" alt="">`:''}<span class="price">${pr}</span></div>
   <div class="body"><div class="specs">${l.specs||''}</div><div class="addr">${l.street}</div>
   <div class="city">${l.city||''}</div><div class="cta"><span>View details &amp; apply →</span></div></div>`;
  const img=a.querySelector('img');if(img){img.onload=()=>img.classList.add('on');if(img.complete)img.classList.add('on');}return a;}
function apply(){const t=q.value.trim().toLowerCase();
  const mb=beds.value===''?null:parseFloat(beds.value),mp=price.value===''?null:parseInt(price.value);
  let rows=LISTINGS.filter(l=>{if(t&&!(l.address||'').toLowerCase().includes(t))return false;
    if(mb!==null){if(mb===0){if(l.beds!==0)return false;}else if((l.beds||0)<mb)return false;}
    if(mp!==null&&(l.rent_val||0)>mp)return false;return true;});
  const s=sort.value,asc=v=>v>0?v:Infinity;
  rows.sort((a,b)=>s==='rent_desc'?b.rent_val-a.rent_val:s==='new'?(b.id||0)-(a.id||0):asc(a.rent_val)-asc(b.rent_val));
  grid.innerHTML='';rows.forEach(l=>grid.appendChild(card(l)));
  $('#shown').textContent=rows.length;$('#empty').style.display=rows.length?'none':'block';}
[q,beds,price,sort].forEach(e=>e.addEventListener('input',apply));
$('#clear').onclick=()=>{q.value='';beds.value='';price.value='';sort.value='rent_asc';apply();};apply();
</script></body></html>""".replace("__CSS__", SHARED_CSS)

DETAIL_TPL = r"""<!DOCTYPE html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>@@STREET@@ — Atrium</title>
<link rel="canonical" href="@@CANON@@">
<script type="application/ld+json">@@JSONLD@@</script>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Open+Sans:wght@400;500;600;700&display=swap">
<style>__CSS__
  .back{max-width:1100px;margin:0 auto;padding:18px 24px 0}
  .back a{font-size:14px;color:var(--ink-2);font-weight:600}.back a:hover{color:var(--red)}
  .wrap{max-width:1100px;margin:0 auto;padding:16px 24px 60px;display:grid;grid-template-columns:1.5fr 1fr;gap:32px}
  .gallery .main{width:100%;aspect-ratio:4/3;object-fit:cover;border-radius:var(--radius);background:#eceff3;display:block}
  .thumbs{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-top:10px}
  .thumb{padding:0;border:2px solid transparent;border-radius:9px;overflow:hidden;cursor:pointer;background:none;aspect-ratio:1}
  .thumb img{width:100%;height:100%;object-fit:cover;display:block}
  .thumb.on{border-color:var(--red)}
  .info h1{font-size:26px;line-height:1.15;margin:0 0 4px;letter-spacing:-.01em}
  .sub{color:var(--ink-2);font-size:15px;margin-bottom:16px}
  .pricerow{display:flex;align-items:baseline;gap:12px;margin-bottom:6px}
  .pricerow .p{font-size:28px;font-weight:800}.pricerow .s{font-weight:700;color:var(--red);font-size:13px;letter-spacing:.04em;text-transform:uppercase}
  .apply{display:block;text-align:center;background:var(--red);color:#fff;font-weight:800;font-size:16px;
    padding:16px;border-radius:12px;margin:18px 0 8px;transition:background .15s}
  .apply:hover{background:var(--red-dk)}
  .note{font-size:12px;color:#9aa0a6;text-align:center}
  .block{margin-top:26px}.block h3{font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:var(--ink-2);margin:0 0 10px}
  .desc{font-size:15px;line-height:1.65;color:#2a2a2e}
  .terms{list-style:none;padding:0;margin:0}
  .terms li{padding:10px 0;border-bottom:1px solid var(--line);font-size:15px}
  @media(max-width:820px){.wrap{grid-template-columns:1fr;gap:22px}}
</style></head><body>
<header class="site"><div class="bar"><a class="brand" href="../widget.html"><img src="../static/atrium-mark.png" alt="Atrium"></a></div></header>
<div class="back"><a href="../widget.html">← All listings</a></div>
<div class="wrap">
  <div class="gallery">
    <img id="main" class="main" src="@@HERO@@" alt="">
    <div class="thumbs">@@THUMBS@@</div>
  </div>
  <div class="info">
    <h1>@@TITLE@@</h1>
    <div class="sub">@@STREET@@@@CITYSEP@@</div>
    <div class="pricerow"><span class="p">@@PRICE@@</span><span class="s">@@SPECS@@</span></div>
    <a class="apply" href="@@APPLY@@" target="_blank" rel="noopener">Apply Now →</a>
    <div class="note">Secure application — opens Atrium’s online form.</div>
    <div class="block"><h3>About this home</h3><div class="desc">@@DESC@@</div></div>
    <div class="block"><h3>Rental Terms</h3><ul class="terms">@@TERMS@@</ul></div>
    @@PETS@@
  </div>
</div>
<script>
const PHOTOS=@@PHOTOS@@;
function pick(i){document.getElementById('main').src=PHOTOS[i];
  document.querySelectorAll('.thumb').forEach((t,n)=>t.classList.toggle('on',n===i));}
</script></body></html>""".replace("__CSS__", SHARED_CSS)


if __name__ == "__main__":
    if "--offline" in sys.argv:
        raw = json.loads((HERE / "listings.json").read_text())
        listings = raw if raw and "rent_val" in raw[0] else normalize_grid(raw)
    else:
        print("Fetching grid feed…")
        listings = enrich(normalize_grid(fetch_grid()), force="--refresh-details" in sys.argv)
    build(listings)
