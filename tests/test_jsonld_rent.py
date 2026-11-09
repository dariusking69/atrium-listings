"""The rent in each home's JSON-LD is the rent the page and the feed show.

The JSON-LD in the head of homes/<id>.html is what Google and AI assistants read
for the price. Two Lakeland homes rent for "$1,630.50" and "$1,490.50" in AppFolio
(the renters insurance and AC filter fees are folded into the rent). rent_bounds()
read the cents as a second amount, so their JSON-LD said "$50 to $1,630 a month"
while the page said $1,630.50 and listings.json said rent_val 1630.

The JSON-LD price is whole dollars, the same number as rent_val, which llms.txt
documents as "the lowest monthly rent as a whole number of US dollars" and which
check_static.py compares it against.

RUN IT
    python3 -m pytest tests/test_jsonld_rent.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build  # noqa: E402


def home(rent):
    return {"id": 1, "address": "1 Test St, Lakeland, FL 33805", "street": "1 Test St",
            "city": "Lakeland", "state": "FL", "zip": "33805",
            "rent": rent, "rent_val": build.rent_to_int(rent), "available": "NOW"}


def price(ld):
    spec = ld["offers"].get("priceSpecification")
    if spec is None:
        return None
    return spec.get("price", (spec.get("minPrice"), spec.get("maxPrice")))


@pytest.mark.parametrize("rent, want", [
    ("$1,630.50", 1630),                      # homes/2405.html said minPrice 50
    ("$1,490.50", 1490),                      # homes/2208.html said minPrice 50
    ("$1,600", 1600),
    ("$1,600.00", 1600),
    ("$950.99", 950),
    ("$1,280 - $2,064", (1280, 2064)),
    ("$1,280.50 - $2,064.75", (1280, 2064)),
    ("$0", None),                             # no price set yet: no price in the JSON-LD
    ("", None),
])
def test_jsonld_price_is_the_listing_rent(rent, want):
    ld = build.listing_jsonld(home(rent))
    assert price(ld) == want
    if isinstance(want, int):
        assert ld["offers"]["price"] == want


@pytest.mark.parametrize("rent", ["$1,630.50", "$1,490.50", "$1,600", "$1,280.50 - $2,064.75"])
def test_jsonld_low_price_equals_rent_val(rent):
    l = home(rent)
    spec = build.listing_jsonld(l)["offers"]["priceSpecification"]
    assert spec.get("price", spec.get("minPrice")) == l["rent_val"]


def test_every_cached_listing_jsonld_matches_rent_val():
    """The real feed cache: every priced home's JSON-LD starts at its rent_val."""
    listings = json.loads((ROOT / "listings.json").read_text(encoding="utf-8"))
    wrong = []
    for l in listings:
        if (l.get("rent_val") or 0) <= 0:
            continue
        spec = build.listing_jsonld(l)["offers"].get("priceSpecification", {})
        got = spec.get("price", spec.get("minPrice"))
        if got != l["rent_val"]:
            wrong.append((l["id"], l.get("rent"), got, l["rent_val"]))
    assert not wrong, wrong
