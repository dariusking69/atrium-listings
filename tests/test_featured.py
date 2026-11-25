"""featured.json, the hand-picked homes that lead widget.html, and check_featured.py.

The refresh bot rebuilds listings.json all day, so a pick that leased has to drop out on
its own, and the run has to say when too few are left. These tests cover that rule and
the shape of the committed picks file. They do NOT check how many picks are live in the
committed listings.json: that number falls as homes lease, and a test that fails because
time passed is a time bomb. The refresh run's warning is where that number is watched.

RUN IT
    python3 -m pytest tests/test_featured.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import check_featured as cf  # noqa: E402


def home(i, community="", street=None, rent=1500, photo=None, zip_="32801"):
    return {"id": i, "community": community, "street": street or f"{i} Main St",
            "zip": zip_, "rent_val": rent, "photo": photo or f"https://img.test/{i}.jpg"}


def pick(i, photo=None, why="Sunny front of the house."):
    return {"id": i, "address": f"{i} Main St", "photo": photo or f"https://img.test/{i}.jpg", "why": why}


def picks_file(ids, minimum=3, **kw):
    return {"min_live": minimum, "homes": [pick(i, **kw) for i in ids]}


def test_a_pick_drops_out_when_it_leases_loses_its_price_or_changes_photo():
    listings = [home(1), home(2, rent=0), home(3, photo="https://img.test/floorplan.jpg"), home(4), home(5)]
    featured = picks_file([5, 1, 2, 3, 4, 99])   # 99 has leased: it is not in the feed
    assert [h["id"] for h in cf.live_picks(featured, listings)] == [5, 1, 4]


def test_the_photo_check_uses_the_photo_the_card_shows():
    # build.py always fills photo, but the widget falls back to photos[0]; so does the check.
    listings = [{"id": 1, "rent_val": 900, "photo": "", "photos": ["https://img.test/1.jpg"]}]
    assert cf.live_picks(picks_file([1]), listings) == [pick(1)]


def test_too_few_live_picks_is_reported_so_someone_picks_again():
    listings = [home(1), home(2)]
    found = cf.problems(picks_file([1, 2, 3], minimum=3), listings)
    assert any("only 2 of 3 picks are still listed" in p for p in found)
    assert cf.problems(picks_file([1, 2], minimum=2), listings) == []


def test_at_most_two_picks_from_one_building():
    listings = [home(1, community="Horizon Vue Apartments"), home(2, community="Horizon Vue Apartments"),
                home(3, community="Horizon Vue Apartments"), home(4)]
    found = cf.problems(picks_file([1, 2, 3, 4]), listings)
    assert any("3 picks from one building (community: horizon vue apartments)" in p for p in found)


def test_units_on_one_street_address_are_one_building():
    # "Atrium Management Company" is the feed's catch-all, not a community.
    a = home(1, community="Atrium Management Company", street="740 Annesley Pl Unit 118", zip_="32789")
    b = home(2, community="Atrium Management Company", street="740 Annesley Pl Unit 116", zip_="32789")
    c = home(3, community="", street="5309 Seminole Ave  - Unit A", zip_="33603")
    d = home(4, community="", street="5309 Seminole Ave  - Unit B", zip_="33603")
    assert cf.building(a) == cf.building(b)
    assert cf.building(c) == cf.building(d)
    assert cf.building(a) != cf.building(c)


def test_a_malformed_file_is_reported():
    listings = [home(1), home(2)]
    bad = {"min_live": 0, "homes": [pick(1, why=""), pick(1), {"id": "2", "photo": "", "why": "x"}]}
    found = cf.problems(bad, listings)
    assert "min_live must be a whole number above 0" in found
    assert "listing 1 is picked 2 times" in found
    assert "listing 1: no reason given" in found
    assert "'2' is not a listing id" in found
    assert any("listing 2: no photo URL" in p for p in found)
    assert cf.problems({"homes": []}, listings) == ["featured.json has no homes"]


def test_the_committed_picks_file_is_well_formed():
    featured = json.loads((ROOT / "featured.json").read_text(encoding="utf-8"))
    homes = featured["homes"]
    # "About 20" (11/25 meeting), with room for some to lease before the fallback.
    assert 18 <= len(homes) <= 26
    assert isinstance(featured["min_live"], int) and 1 <= featured["min_live"] < len(homes)
    assert len({h["id"] for h in homes}) == len(homes)
    for h in homes:
        assert isinstance(h["id"], int)
        assert h["photo"].startswith("https://images.cdn.appfolio.com/")
        assert h["why"].strip() and h["address"].strip()
    # Structural problems only: liveness is the refresh run's job (see the docstring).
    structural = [p for p in cf.problems(featured, []) if not p.startswith("only ")]
    assert structural == []
