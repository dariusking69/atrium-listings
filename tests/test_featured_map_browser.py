"""widget.html in a real browser: hand-picked homes first, and the map narrows the list.

From the 11/25 marketing meeting (Trey, Alex): the full list opened on floor plans and
long runs of one apartment building. Now about 20 homes picked from their thumbnails
(featured.json) lead the default view, then everything else; and moving the map narrows
the list to the homes in view, like Zillow's "Search as I move the map".

What a visitor must see:
  - the picks first, in the file's order, then every other home in the normal order;
  - a pick that leased (gone from the feed) or changed its first photo is skipped;
  - under min_live picks left, the normal order, and a console warning;
  - any filter, sort, search, area or map move wins over the picks; Reset brings them back;
  - never in a scoped view: Marty's ?embed=1, ?props=, ?p=, and w.html never asks for them;
  - dragging the map or its zoom buttons narrows the list to the visible map and puts
    view= in the share URL; the map's own fitting and a popup panning into view do not;
  - the toggle turns it off; a shared view= link reopens the same list.

The homes are made up: ten around Deltona (north-east) and ten around Tampa (south-west),
so a drag can push one group off the map. Every request except Leaflet is answered or
aborted locally. Leaflet itself is fetched once from unpkg into a temp folder and served
from there; without network the map tests skip and the list tests still run.

RUN IT
    pip install pytest playwright && python3 -m playwright install chromium
    python3 -m pytest tests -q
"""
from __future__ import annotations

import json
import mimetypes
import ssl
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
sync_playwright = sync_api.sync_playwright

ROOT = Path(__file__).resolve().parent.parent
HOST = "http://listings.test"
LEAFLET = "https://unpkg.com/leaflet@1.9.4/dist/"

NORTH = list(range(201, 211))   # Deltona
SOUTH = list(range(211, 221))   # Tampa
ALL = NORTH + SOUTH


def _home(i):
    north = i in NORTH
    k = i - (201 if north else 211)
    lat = (28.90 if north else 27.95) + (k % 5) * 0.004
    lng = (-81.20 if north else -82.45) + (k // 5) * 0.004
    city, zp = ("Deltona", "32738") if north else ("Tampa", "33603")
    rent = 900 + ((i * 37) % 20) * 50          # 20 different rents, not in id order
    street = f"{i} Palm Way"
    return {
        "id": i, "uuid": f"u{i}", "title": street, "street": street, "city": city, "state": "FL",
        "zip": zp, "address": f"{street}, {city}, FL {zp}", "community": "", "property": "",
        "rent": f"${rent:,}", "rent_val": rent, "beds": 3 if i % 2 else 2, "baths": 2, "sqft": 1000,
        "specs": "", "photo": f"{HOST}/img/{i}.jpg", "photos": [], "available": "NOW",
        "lat": lat, "lng": lng, "description": "", "apply_url": "", "appfolio_url": "",
    }


HOMES = [_home(i) for i in ALL]
BY_ID = {h["id"]: h for h in HOMES}
# 999 has leased (not in the feed); 207's first photo changed since it was picked.
PICKED = [215, 203, 999, 218, 207, 211, 201, 219, 205, 213, 209, 217, 202, 216]
LIVE = [i for i in PICKED if i not in (999, 207)]          # 12


def _featured(minimum=12):
    return {"min_live": minimum, "homes": [
        {"id": i, "address": "", "why": "test",
         "photo": f"{HOST}/img/{i}.jpg" if i != 207 else f"{HOST}/img/old-207.jpg"} for i in PICKED]}


def rent_desc(ids):
    return sorted(ids, key=lambda i: -BY_ID[i]["rent_val"])


def featured_order():
    return LIVE + rent_desc([i for i in ALL if i not in LIVE])


WIDGET_DATA = [{"id": h["id"], "a": h["address"], "u": "", "c": h["city"], "z": h["zip"], "p": "",
                "pg": "", "mf": 0, "r": h["rent"], "rv": h["rent_val"], "bd": h["beds"], "ba": 2,
                "sf": 1000, "ph": h["photo"], "av": "NOW", "lat": h["lat"], "lng": h["lng"],
                "ap": "", "af": ""} for h in HOMES]
DIRECTORY = {"properties": [], "communities": [], "portfolios": [], "cities": [], "pgs": [],
             "mfProperties": [], "mfTotal": 0, "total": len(HOMES), "groups": []}


@pytest.fixture(scope="module")
def leaflet(tmp_path_factory):
    """Leaflet's two files, fetched once. None (map tests skip) without network."""
    d = tmp_path_factory.mktemp("leaflet")
    ctx = None
    try:                    # python.org's macOS build ships without CA roots; certifi has them
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    try:
        for name in ("leaflet.js", "leaflet.css"):
            with urllib.request.urlopen(LEAFLET + name, timeout=20, context=ctx) as r:
                (d / name).write_bytes(r.read())
    except Exception:
        return None
    return d


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


def _open(browser, leaflet, featured=None, viewport=None):
    """A page on the fake host. featured: a dict, or False for a 404."""
    files = {"/listings.json": HOMES, "/widget-data.json": WIDGET_DATA, "/directory.json": DIRECTORY,
             "/featured.json": _featured() if featured is None else featured}
    ctx = browser.new_context(viewport=viewport or {"width": 1280, "height": 900})
    asked = []

    def serve(route):
        url = route.request.url
        if url.startswith(LEAFLET) and leaflet is not None:
            name = url[len(LEAFLET):].split("?")[0]
            if (leaflet / name).is_file():
                return route.fulfill(status=200, body=(leaflet / name).read_bytes(),
                                     content_type=mimetypes.guess_type(name)[0])
        if not url.startswith(HOST + "/"):
            return route.abort()
        path = url[len(HOST):].split("?")[0].split("#")[0]
        asked.append(path)
        if path in files and files[path] is not False:
            return route.fulfill(status=200, content_type="application/json", body=json.dumps(files[path]))
        f = (ROOT / path.lstrip("/")).resolve()
        if path in files or ROOT not in f.parents or not f.is_file():
            return route.fulfill(status=404, body="")
        return route.fulfill(status=200, body=f.read_bytes(),
                             content_type=mimetypes.guess_type(f.name)[0] or "text/plain")

    ctx.route("**/*", serve)
    page = ctx.new_page()
    page.errors, page.warnings, page.asked = [], [], asked
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.on("console", lambda m: page.warnings.append(m.text) if m.type == "warning" else None)
    return ctx, page


@pytest.fixture()
def opener(browser, leaflet):
    ctxs = []

    def open_(path="widget.html", featured=None, viewport=None, need_map=False):
        if need_map and leaflet is None:
            pytest.skip("Leaflet could not be fetched (no network)")
        ctx, page = _open(browser, leaflet, featured, viewport)
        ctxs.append(ctx)
        page.goto(f"{HOST}/{path}")
        page.wait_for_selector("#atr-grid .atr-card")
        if need_map:
            page.wait_for_selector("#atr-map .leaflet-marker-icon")
            _settle(page)
        return page

    yield open_
    for c in ctxs:
        c.close()


def drawn(page):
    return [int(x) for x in page.eval_on_selector_all("#atr-grid .atr-card", "els => els.map(e => e.dataset.id)")]


def view_box(page):
    v = parse_qs(urlparse(page.url).query).get("view")
    if not v:
        return None
    s, w, n, e = map(float, v[0].split("_"))
    return s, w, n, e


def inside(box):
    s, w, n, e = box
    return [i for i in ALL if s <= BY_ID[i]["lat"] <= n and w <= BY_ID[i]["lng"] <= e]


def _settle(page, ms=900):
    """Let Leaflet finish its zoom/pan animations (and moveend) before reading."""
    page.wait_for_timeout(ms)


# ---------------------------------------------------------------- featured first

def test_picks_lead_the_default_view_then_everything_else_in_rent_order(opener):
    page = opener()
    assert drawn(page) == featured_order()
    # The leased pick is simply absent; the pick whose photo changed is back in rent order.
    assert 999 not in drawn(page)
    assert drawn(page).index(207) >= len(LIVE)
    assert page.errors == []


@pytest.mark.parametrize("act", ["beds", "sort", "min", "search", "city"])
def test_any_filter_sort_or_search_the_visitor_picks_wins(opener, act):
    page = opener()
    assert drawn(page) == featured_order()
    if act == "beds":
        page.select_option("#atr-beds", "3")
        want = rent_desc([i for i in ALL if BY_ID[i]["beds"] >= 3])
    elif act == "sort":
        page.select_option("#atr-sort", "rent_asc")
        want = list(reversed(rent_desc(ALL)))
    elif act == "min":
        page.fill("#atr-min", "1500")
        want = rent_desc([i for i in ALL if BY_ID[i]["rent_val"] >= 1500])
    elif act == "search":
        page.fill("#atr-q", "Palm Way")            # every home matches: only the order can tell
        want = rent_desc(ALL)
    else:
        page.select_option("#atr-city", "Tampa")
        want = rent_desc(SOUTH)
    assert drawn(page) == want
    page.click("#atr-reset")
    assert drawn(page) == featured_order()
    assert page.errors == []


def test_too_few_picks_left_falls_back_to_the_normal_order_and_says_so(opener):
    page = opener(featured=_featured(minimum=13))      # 12 live picks, 13 needed
    assert drawn(page) == rent_desc(ALL)
    assert any("featured.json" in w and "12 of 14" in w for w in page.warnings)
    assert page.errors == []


def test_a_missing_picks_file_is_just_the_normal_order(opener):
    page = opener(featured=False)
    assert drawn(page) == rent_desc(ALL)
    assert page.errors == []


@pytest.mark.parametrize("path", ["widget.html?embed=1", "widget.html?props=Somewhere",
                                  "widget.html?p=Somewhere", "widget.html?property=Somewhere"])
def test_scoped_views_never_show_or_even_fetch_the_picks(opener, path):
    page = opener(path)
    assert drawn(page) == rent_desc(ALL)
    assert "/featured.json" not in page.asked
    assert page.errors == []


def test_the_community_widget_never_fetches_the_picks(opener):
    page = opener("w.html")
    assert sorted(drawn(page)) == sorted(ALL)
    assert "/featured.json" not in page.asked
    assert page.errors == []


# ---------------------------------------------------------------- search as I move the map

def test_the_maps_own_fitting_does_not_narrow_the_list(opener):
    page = opener(need_map=True)
    assert view_box(page) is None
    assert drawn(page) == featured_order()
    assert page.is_visible("#atr-mapsearch")
    assert page.is_checked("#atr-mapsearch input")


def _drag(page, dx, dy):
    box = page.locator("#atr-map").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    for k in range(1, 21):
        page.mouse.move(x + dx * k / 20, y + dy * k / 20)
        page.wait_for_timeout(15)
    page.wait_for_timeout(120)                      # stop before release: no inertia fling
    page.mouse.up()
    page.wait_for_function("location.search.indexOf('view=') >= 0", timeout=5000)
    _settle(page)


def _zoom_and_leftmost_pin(page):
    return page.evaluate("""() => {
        const z = (document.querySelector('#atr-map .leaflet-proxy').style.transform.match(/scale\\(([\\d.]+)\\)/) || [])[1];
        const xs = [...document.querySelectorAll('#atr-map .leaflet-marker-icon')].map(m => m.getBoundingClientRect().left);
        return [z, Math.min(...xs)];
    }""")


def test_dragging_the_map_narrows_the_list_to_the_homes_in_view(opener):
    page = opener(need_map=True)
    zoom, left = _zoom_and_leftmost_pin(page)
    # The fit puts Deltona at the right edge and Tampa at the left: drag right, Deltona leaves.
    _drag(page, 260, 0)
    box = view_box(page)
    assert box is not None
    assert sorted(drawn(page)) == sorted(inside(box)) == sorted(SOUTH)
    assert drawn(page) == rent_desc(SOUTH)            # the visitor's area wins over the picks
    assert page.inner_text("#atr-count") == "10 homes"
    assert page.locator("#atr-map .leaflet-marker-icon").count() == 10
    # The map stayed where the visitor left it: no re-fit to the pins that are left.
    zoom2, left2 = _zoom_and_leftmost_pin(page)
    assert zoom2 == zoom and abs(left2 - (left + 260)) < 12
    assert page.errors == []


def test_picking_a_radius_on_the_map_is_the_radius_not_a_map_move(opener):
    page = opener(need_map=True)
    page.click("#atr-radius")
    # Just below the tip of the lowest Tampa pin: open map, a mile or two from those homes.
    x, y = page.evaluate("""() => {
        const ms = [...document.querySelectorAll('#atr-map .leaflet-marker-icon')]
          .map(m => m.getBoundingClientRect()).sort((a, b) => b.bottom - a.bottom);
        return [ms[0].left + ms[0].width / 2, ms[0].bottom + 6];
    }""")
    page.mouse.click(x, y)
    _settle(page, 1200)
    q = parse_qs(urlparse(page.url).query)
    assert "rlat" in q and "view" not in q              # the circle's own fit is ours
    assert sorted(drawn(page)) == sorted(SOUTH)
    assert page.errors == []


def test_the_zoom_buttons_narrow_the_list_too(opener):
    page = opener(need_map=True)
    page.click("#atr-map .leaflet-control-zoom-out")
    page.wait_for_function("location.search.indexOf('view=') >= 0", timeout=5000)
    _settle(page)
    # Zoomed out, every home is in view, but the visitor moved the map: normal order.
    assert sorted(inside(view_box(page))) == sorted(ALL)
    assert drawn(page) == rent_desc(ALL)
    assert page.errors == []


def test_opening_a_pin_near_the_edge_does_not_narrow_the_list(opener):
    # Phone width: the map is 300px tall, so the top-most pin's popup cannot fit without
    # Leaflet panning the map down. That pan is Leaflet's, not the visitor's.
    page = opener(viewport={"width": 390, "height": 844}, need_map=True)
    top = page.evaluate("""() => {
        const ms = [...document.querySelectorAll('#atr-map .leaflet-marker-icon')];
        ms.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
        const r = ms[0].getBoundingClientRect();
        return [r.left + r.width / 2, r.top + r.height / 2];
    }""")
    before = page.evaluate("document.querySelector('#atr-map .leaflet-map-pane').style.transform")
    page.mouse.click(*top)
    page.wait_for_selector("#atr-map .leaflet-popup")
    _settle(page)
    moved = page.evaluate("document.querySelector('#atr-map .leaflet-map-pane').style.transform") != before
    assert moved, "the popup did not pan the map, so this test proves nothing"
    assert view_box(page) is None
    assert drawn(page) == featured_order()
    assert page.is_visible("#atr-map .leaflet-popup")
    assert page.errors == []


def test_the_toggle_turns_it_off_and_back_on(opener):
    page = opener(need_map=True)
    _drag(page, 260, 0)
    assert sorted(drawn(page)) == sorted(SOUTH)
    page.uncheck("#atr-mapsearch input")
    _settle(page)
    assert view_box(page) is None
    assert drawn(page) == featured_order()           # nothing narrowed any more: picks lead again
    # Off: moving the map leaves the list alone.
    box = page.locator("#atr-map").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x + 200, y, steps=10); page.mouse.up()
    _settle(page)
    assert view_box(page) is None and drawn(page) == featured_order()
    # On again: it searches the map as it is now.
    page.check("#atr-mapsearch input")
    _settle(page)
    box = view_box(page)
    assert box is not None and sorted(drawn(page)) == sorted(inside(box))
    assert page.errors == []


def test_a_shared_view_link_reopens_the_same_narrowed_list(opener):
    s, w, n, e = 27.90, -82.50, 28.05, -82.40          # around the Tampa homes only
    page = opener(f"widget.html?view={s}_{w}_{n}_{e}", need_map=True)
    assert drawn(page) == rent_desc(SOUTH)
    # The map went to that box and the URL kept it: the map's own move did not replace it.
    assert view_box(page) == (s, w, n, e)
    pane = page.locator("#atr-map").bounding_box()
    pins = page.evaluate("""() => [...document.querySelectorAll('#atr-map .leaflet-marker-icon')]
        .map(m => { const r = m.getBoundingClientRect(); return [r.left + r.width / 2, r.bottom]; })""")
    assert len(pins) == 10
    assert all(pane["x"] <= x <= pane["x"] + pane["width"] and pane["y"] <= y <= pane["y"] + pane["height"]
               for x, y in pins)
    assert page.errors == []


def test_searching_a_city_lets_go_of_the_map_box(opener):
    page = opener(need_map=True)
    _drag(page, 260, 0)                                # Tampa in view only
    page.select_option("#atr-city", "Deltona")         # a new area: the map box goes
    _settle(page)
    assert view_box(page) is None
    assert drawn(page) == rent_desc(NORTH)
    assert page.errors == []


def test_the_toggle_sits_on_the_map_at_phone_width(opener):
    page = opener(viewport={"width": 390, "height": 844}, need_map=True)
    t = page.locator("#atr-mapsearch").bounding_box()
    m = page.locator("#atr-map").bounding_box()
    z = page.locator("#atr-map .leaflet-control-zoom").bounding_box()
    assert m["x"] <= t["x"] and t["x"] + t["width"] <= m["x"] + m["width"]
    assert m["y"] <= t["y"] and t["y"] + t["height"] <= m["y"] + m["height"]
    assert t["x"] >= z["x"] + z["width"] or t["y"] >= z["y"] + z["height"]   # clear of the +/- buttons
    assert page.evaluate("document.documentElement.scrollWidth") <= 390     # no sideways scroll
    assert page.errors == []
