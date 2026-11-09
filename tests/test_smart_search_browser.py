"""The shared Atrium search in every box on the listings site, in a real browser.

Alex, 2026-11-05: every search box should work like AppFolio's: misspellings,
close matches, best match first. static/smart_search.js is that engine, copied
word for word from the Intranet. This types into the three boxes the way a
prospect would and reads the cards each page draws:

    widget.html  #atr-q  the main listings page (and Marty's ?embed=1 view)
    w.html       #atr-q  the embed on owners' and clients' own sites
    grid.html    #q      the plain card grid build.py writes

Every "it appears first" is preceded by "it was not there", so a card that is
always on screen cannot pass for one the search found. The homes are made up
and served from a fake host; the browser aborts every other request (maps,
fonts, the geocoder), so nothing leaves the machine and no server is started.

The made-up homes are chosen so the old plain "contains" search fails every
case: "Cardnal" and "Sanfrod" are not in any field, and the decoy on Cardinal
Ln rents for more, so a rent sort alone would put it ahead of Cardinal Village.

RUN IT
    pip install pytest playwright && python3 -m playwright install chromium
    python3 -m pytest tests -q
"""
from __future__ import annotations

import json
import mimetypes
import sys
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
sync_playwright = sync_api.sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402  (grid.html is rendered from build.py's template)

HOST = "http://listings.test"

# id, community, street, city, zip, rent, lat, lng
HOMES = [
    (101, "Cardinal Village", "510 Wren Ave. Unit 2", "Deltona", "32738", 1450, 28.90, -81.20),
    (102, "", "88 Cardinal Ln", "Sanford", "32771", 2400, 28.80, -81.27),
    (103, "Oakmere Commons", "12 Oakmere Ct", "Orlando", "32801", 1800, 28.54, -81.38),
    (104, "", "77 Lake Pointe Dr", "Kissimmee", "34741", 1600, 28.29, -81.41),
]


def _listing(h):
    i, community, street, city, zp, rent, lat, lng = h
    address = f"{street}, {city}, FL {zp}"
    return {
        "id": i, "uuid": f"u{i}", "title": street, "street": street, "city": city,
        "state": "FL", "zip": zp, "address": address, "community": community,
        "property": community, "rent": f"${rent:,}", "rent_val": rent, "beds": 2,
        "baths": 2, "sqft": 1000, "specs": "2 bd / 2 ba", "photo": "",
        "photos": [], "available": "NOW", "lat": lat, "lng": lng,
        "description": "", "apply_url": "", "appfolio_url": "",
    }


LISTINGS = [_listing(h) for h in HOMES]
WIDGET_DATA = [{
    "id": l["id"], "a": l["address"], "u": "", "c": l["city"], "z": l["zip"],
    "p": l["community"], "pg": "", "mf": 0, "r": l["rent"], "rv": l["rent_val"],
    "bd": 2, "ba": 2, "sf": 1000, "ph": "", "av": "NOW", "lat": l["lat"],
    "lng": l["lng"], "ap": "", "af": "",
} for l in LISTINGS]
DIRECTORY = {"properties": [], "communities": [], "portfolios": [], "cities": [],
             "pgs": [], "mfProperties": [], "mfTotal": 0, "total": 4, "groups": []}
FIXTURES = {
    "/listings.json": LISTINGS,
    "/widget-data.json": WIDGET_DATA,
    "/directory.json": DIRECTORY,
}


def _serve(route):
    """The fake host: fixtures for the data, the branch's own files for the rest."""
    url = route.request.url
    if not url.startswith(HOST + "/"):
        return route.abort()
    path = url[len(HOST):].split("?")[0].split("#")[0]
    if path in FIXTURES:
        return route.fulfill(status=200, content_type="application/json",
                             body=json.dumps(FIXTURES[path]))
    if path == "/grid.html":
        return route.fulfill(status=200, content_type="text/html",
                             body=build.grid_html(LISTINGS))
    f = (ROOT / path.lstrip("/")).resolve()
    if ROOT not in f.parents or not f.is_file():
        return route.fulfill(status=404, body="")
    return route.fulfill(status=200, body=f.read_bytes(),
                         content_type=mimetypes.guess_type(f.name)[0] or "text/plain")


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture()
def page(browser):
    ctx = browser.new_context()
    ctx.route("**/*", _serve)
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    yield pg, errors
    ctx.close()


BOXES = [
    # page, search box, the cards it draws, how a card names its home
    ("widget.html", "#atr-q", "#atr-grid .atr-card", "el => el.dataset.id"),
    ("w.html", "#atr-q", "#atr-grid .atr-card", "el => el.dataset.id"),
    ("grid.html", "#q", "#grid a.card",
     "el => el.getAttribute('href').replace(/^.*\\//, '').replace('.html', '')"),
]


def _search(page, box, cards, ident, text):
    """Type into the box as a person does and return the homes drawn, in order.

    Each page redraws on the box's `input` event, synchronously, so the cards
    are current as soon as fill() returns.
    """
    page.fill(box, "")
    page.fill(box, text)
    return _drawn(page, cards, ident)


def _drawn(page, cards, ident):
    return [int(x) for x in page.eval_on_selector_all(cards, f"els => els.map({ident})")]


@pytest.mark.parametrize("url,box,cards,ident", BOXES, ids=[b[0] for b in BOXES])
def test_a_misspelt_community_or_city_finds_it_best_match_first(page, url, box, cards, ident):
    page, errors = page
    page.goto(f"{HOST}/{url}")
    page.wait_for_selector(cards)
    assert sorted(_drawn(page, cards, ident)) == [101, 102, 103, 104]
    assert page.evaluate("typeof window.SmartSearch.rank") == "function"

    # Community: not there for another search, then first for a misspelling,
    # ahead of the dearer Cardinal Ln home that a rent sort would put on top.
    assert _search(page, box, cards, ident, "Oakmere") == [103]
    assert _search(page, box, cards, ident, "Cardnal") == [101, 102]

    # City: the same, by a swapped pair of letters.
    assert 102 not in _search(page, box, cards, ident, "Oakmere")
    assert _search(page, box, cards, ident, "Sanfrod") == [102]

    # An empty box puts every home back.
    assert sorted(_search(page, box, cards, ident, "")) == [101, 102, 103, 104]
    assert errors == []


@pytest.mark.parametrize("url,box,cards,ident", BOXES[:2], ids=[b[0] for b in BOXES[:2]])
def test_a_sort_picked_by_hand_still_wins_over_best_match(page, url, box, cards, ident):
    page, errors = page
    page.goto(f"{HOST}/{url}")
    page.wait_for_selector(cards)
    assert _search(page, box, cards, ident, "Cardnal") == [101, 102]
    # Rent high to low is the default; picking a sort by hand re-sorts the hits.
    page.select_option("#atr-sort", "rent_asc")
    page.select_option("#atr-sort", "rent_desc")
    assert _drawn(page, cards, ident) == [102, 101]
    assert errors == []
