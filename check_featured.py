#!/usr/bin/env python3
"""Are enough of the hand-picked homes in featured.json still listed?

widget.html leads its default view with the homes in featured.json (11/25 marketing
meeting: about 20 good-looking homes first, then everything else). The refresh bot
rebuilds listings.json all day, and the widget skips a pick once it:
  - is gone from listings.json (leased or taken down),
  - has no price (rent_val 0), or
  - leads with a different first photo than the one that was picked.
Under `min_live` picks left, the widget shows the normal order. This check says so in
the refresh run, so someone picks again. It also catches a malformed picks file.

featured.json is written by a person (or Claude), never by build.py, so the bot does not
commit it.

Run:  python3 check_featured.py
Exits non-zero if the picks need attention. In GitHub Actions each problem is a warning.
"""
import collections
import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent
MAX_PER_BUILDING = 2
# The feed's catch-all community name for homes that are not in a named community.
NO_COMMUNITY = {"", "atrium management company"}


def first_photo(listing):
    """The photo the card leads with: the same rule as widget.html's loader."""
    return listing.get("photo") or (listing.get("photos") or [""])[0] or ""


def building(listing):
    """One key per building: the community, else the street without its unit."""
    community = (listing.get("community") or "").strip().lower()
    if community not in NO_COMMUNITY:
        return "community: " + community
    street = re.sub(r"\s+(?:-\s*)?(?:#|apt\.?|unit|ste\.?|suite|bldg\.?|building|lot)\s*[\w-]*\s*$", "",
                    (listing.get("street") or "").strip().lower())
    return f"street: {street} {listing.get('zip', '')}".strip()


def live_picks(featured, listings):
    """The picks the widget would still show, in featured.json's order."""
    by_id = {l.get("id"): l for l in listings}
    seen, out = set(), []
    for h in featured.get("homes") or []:
        l = by_id.get(h.get("id"))
        if l is None or h.get("id") in seen or not (l.get("rent_val") or 0) > 0:
            continue
        if h.get("photo") and first_photo(l) != h["photo"]:
            continue
        seen.add(h["id"])
        out.append(h)
    return out


def problems(featured, listings):
    out = []
    homes = featured.get("homes")
    if not isinstance(homes, list) or not homes:
        return ["featured.json has no homes"]
    minimum = featured.get("min_live")
    if not isinstance(minimum, int) or minimum < 1:
        out.append("min_live must be a whole number above 0")
        minimum = 12
    ids = [h.get("id") for h in homes]
    for i, n in collections.Counter(ids).items():
        if n > 1:
            out.append(f"listing {i} is picked {n} times")
    for h in homes:
        if not isinstance(h.get("id"), int):
            out.append(f"{h.get('id')!r} is not a listing id")
        if not str(h.get("why") or "").strip():
            out.append(f"listing {h.get('id')}: no reason given")
        if not str(h.get("photo") or "").startswith("https://"):
            out.append(f"listing {h.get('id')}: no photo URL (the photo that was picked)")
    live = live_picks(featured, listings)
    by_id = {l.get("id"): l for l in listings}
    for b, n in collections.Counter(building(by_id[h["id"]]) for h in live).items():
        if n > MAX_PER_BUILDING:
            out.append(f"{n} picks from one building ({b}); at most {MAX_PER_BUILDING}")
    if len(live) < minimum:
        out.append(f"only {len(live)} of {len(homes)} picks are still listed with the photo that was "
                   f"picked (minimum {minimum}): widget.html shows the normal order until someone "
                   f"picks again")
    return out


def main():
    featured = json.loads((HERE / "featured.json").read_text(encoding="utf-8"))
    listings = json.loads((HERE / "listings.json").read_text(encoding="utf-8"))
    found = problems(featured, listings)
    live = live_picks(featured, listings)
    in_actions = os.environ.get("GITHUB_ACTIONS") == "true"
    for p in found:
        print(f"::warning::featured.json: {p}" if in_actions else f"featured.json: {p}")
    if found:
        return 1
    print(f"featured.json OK: {len(live)} of {len(featured['homes'])} picks still listed "
          f"(minimum {featured['min_live']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
