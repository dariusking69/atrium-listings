#!/usr/bin/env python3
"""Sanity-check the smart-search AREAS table in widget.html against listings.json.

The table maps colloquial place names ("Four Corners", "near Disney") to a
center + radius so the search box can answer them geographically. A typo in a
coordinate is invisible in code review but sends renters to the wrong part of
the state, so this validates every entry against the real listing coordinates.

Run:  python3 check_areas.py
Exits non-zero if any entry looks wrong.
"""
import json
import collections
import math
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).parent
# Rough bounding box of the states Atrium operates in (FL / GA / VA).
BOX = {"lat": (24.0, 40.0), "lng": (-88.0, -75.0)}


def miles(la1, lo1, la2, lo2):
    r = math.pi / 180
    a = (math.sin((la2 - la1) * r / 2) ** 2
         + math.cos(la1 * r) * math.cos(la2 * r) * math.sin((lo2 - lo1) * r / 2) ** 2)
    return 2 * 3958.8 * math.asin(math.sqrt(a))


def load_areas():
    html = (HERE / "widget.html").read_text(encoding="utf-8")
    block = html.split("AREAS_TABLE_START")[1].split("AREAS_TABLE_END")[0]
    out = []
    for m in re.finditer(
        r"\{name:(\".*?\"),aliases:\[(.*?)\],lat:(-?[\d.]+),lng:(-?[\d.]+),mi:([\d.]+)\}",
        block, re.S,
    ):
        out.append({
            "name": json.loads(m.group(1)),
            "aliases": [json.loads(a) for a in re.findall(r'"(?:[^"\\]|\\.)*"', m.group(2))],
            "lat": float(m.group(3)), "lng": float(m.group(4)), "mi": float(m.group(5)),
        })
    return out


def check_search_tiers(rows):
    """Guard the search tiers in widget.html against data drift.

    A community name ("Hammocks", "Champions") appears ONLY in a listing's
    marketing description, never in address/title/city. That is why a search for
    one used to match nothing and then get geocoded to a same-named place in
    another metro — the real "Hammocks meant Gainesville, showed North Tampa"
    bug. The widget now falls back to descriptions, but ONLY when the normal
    fields match nothing, so ordinary searches stay clean.

    This asserts the data still supports both halves of that contract.
    """
    def primary(l, q):
        return (q in (l.get("address") or "").lower()
                or q in (l.get("title") or "").lower()
                or q in (l.get("zip") or "")
                or q in (l.get("city") or "").lower())

    def desc(l, q):
        return q in (l.get("description") or "").lower()

    errors = []
    print("search tiers:")
    # 1. Community names must be reachable, and land in ONE metro.
    for name, expect_city in (("hammocks", "Gainesville"), ("champions", None)):
        prim = [l for l in rows if primary(l, name)]
        fell = [l for l in rows if desc(l, name)]
        if prim:
            print(f"  note: '{name}' now matches primary fields ({len(prim)}) — "
                  f"fallback no longer needed for it")
            continue
        if not fell:
            errors.append(f"'{name}' matches NOTHING — the community-name fallback "
                          f"can't find it, so it will be geocoded to another metro")
            continue
        cities = collections.Counter((l.get("city") or "?") for l in fell)
        top, n = cities.most_common(1)[0]
        print(f"  '{name}': {len(fell)} via description, top city {top} ({n})")
        if expect_city and top != expect_city:
            errors.append(f"'{name}' resolves to {top}, expected {expect_city}")

    # 2. Ordinary searches must NOT need the fallback (it would flood them).
    for q in ("orlando", "tampa", "pool", "gainesville"):
        prim = sum(1 for l in rows if primary(l, q))
        both = sum(1 for l in rows if primary(l, q) or desc(l, q))
        if prim == 0:
            errors.append(f"'{q}' no longer matches any primary field — it would "
                          f"fall through to descriptions and return {both} results")
        else:
            print(f"  '{q}': {prim} primary (fallback stays off; would be {both})")
    return errors


def main():
    areas = load_areas()
    data = json.loads((HERE / "listings.json").read_text(encoding="utf-8"))
    rows = data if isinstance(data, list) else (data.get("listings") or list(data.values())[0])
    pts = [(float(l["lat"]), float(l["lng"])) for l in rows if l.get("lat") and l.get("lng")]

    print(f"{len(areas)} areas, {sum(len(a['aliases']) for a in areas)} aliases, "
          f"{len(pts)} geocoded listings\n")

    errors, empty, seen = [], [], {}
    for a in areas:
        if not (BOX["lat"][0] <= a["lat"] <= BOX["lat"][1]
                and BOX["lng"][0] <= a["lng"] <= BOX["lng"][1]):
            errors.append(f"{a['name']}: center {a['lat']},{a['lng']} is outside FL/GA/VA "
                          f"(swapped or wrong sign?)")
        if not 0.5 <= a["mi"] <= 40:
            errors.append(f"{a['name']}: radius {a['mi']} mi is implausible")
        if not a["aliases"]:
            errors.append(f"{a['name']}: no aliases — unreachable")
        for al in a["aliases"]:
            if al in seen and seen[al] != a["name"]:
                errors.append(f"alias {al!r} maps to both {seen[al]!r} and {a['name']!r}")
            seen[al] = a["name"]
        n = sum(1 for la, lo in pts if miles(a["lat"], a["lng"], la, lo) <= a["mi"])
        if n == 0:
            empty.append(a["name"])

    if empty:
        print(f"note: {len(empty)} area(s) have no listings nearby right now "
              f"(fine — they honestly report 0): {', '.join(sorted(empty))}\n")
    errors += check_search_tiers(rows)

    for e in errors:
        print(f"  ERROR  {e}")
    print(f"\n{'FAIL' if errors else 'OK'} — {len(errors)} problem(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
