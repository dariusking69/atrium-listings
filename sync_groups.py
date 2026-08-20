#!/usr/bin/env python3
"""
Map each public listing to its AppFolio Property Group(s): the PGs and the
Multifamily group (254), plus the MF property it belongs to.

Why this exists: the public listings feed knows nothing about PGs, and its
portfolio-name sidebar only names ~20 communities — 178 MF listings show the
generic company name and 18 MF properties are missing entirely. The Reports API
knows the real structure, so we join on it.

The join is exact, not fuzzy: unit_directory's `rentable_uid` IS the uuid in the
public listing's /listings/detail/<uuid> URL.

Because this reads group membership live, a property onboarded into a PG or into
Multifamily shows up on the next run with no code change.

Usage:  python3 sync_groups.py          # writes groups.json
Env:    REPORTS_CLIENT_ID, REPORTS_CLIENT_SECRET, DEVELOPER_ID,
        APPFOLIO_DATABASE_NAME   (optional: .env file next to this script)
Without credentials it exits 0 and leaves any existing groups.json untouched, so
the listings build still works — it just can't refresh group membership.
"""
import base64, json, os, ssl, sys, time, urllib.request
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "groups.json"

# PG6 deliberately does not exist.
PGS = {"PG1": "13", "PG2": "15", "PG3": "14", "PG4": "31",
       "PG5": "40", "PG7": "72", "PG8": "75", "PG9": "80"}
MF_GROUP = "254"
# Third-party portfolios we publish a scoped widget for. Keyed on the AppFolio property
# GROUP id, never on property names: the group is the client's own list, so a rename or a
# newly onboarded building resolves here with no change to the embed URL living on their
# CMS. We only get to hand that URL over once.
CLIENT_GROUPS = {"inicio": {"id": "288", "name": "Inicio Living"}}
BATCH = 6          # properties per unit_directory call — keeps every call under the 5,000-row cap

try:
    import certifi
    CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    CTX = ssl.create_default_context()


def creds():
    env = dict(os.environ)
    dotenv = HERE / ".env"
    if dotenv.exists():                      # local convenience; never committed
        for line in dotenv.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    need = ("REPORTS_CLIENT_ID", "REPORTS_CLIENT_SECRET", "DEVELOPER_ID", "APPFOLIO_DATABASE_NAME")
    if not all(env.get(k) for k in need):
        return None
    return {k: env[k] for k in need}


def report(c, name, body, tries=5):
    """POST a v2 report. AppFolio 429s readily — back off and retry."""
    url = f"https://{c['APPFOLIO_DATABASE_NAME']}.appfolio.com/api/v2/reports/{name}.json"
    auth = base64.b64encode(f"{c['REPORTS_CLIENT_ID']}:{c['REPORTS_CLIENT_SECRET']}".encode()).decode()
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers={
                "Authorization": "Basic " + auth,
                "X-AppFolio-Developer-ID": c["DEVELOPER_ID"],
                "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=180, context=CTX) as r:
                d = json.loads(r.read().decode())
            return d.get("results", d) if isinstance(d, dict) else d
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(2 ** attempt)


def scope(group_ids=(), property_ids=()):
    return {"properties": {"property_groups_ids": list(group_ids), "properties_ids": list(property_ids),
                           "portfolios_ids": [], "owners_ids": []}}


def units(c, **kw):
    body = scope(**kw)
    body["unit_visibility"] = "active"
    rows = report(c, "unit_directory", body)
    if len(rows) >= 5000:
        print(f"    WARNING: hit the 5,000-row cap ({len(rows)}) — results may be truncated", file=sys.stderr)
    return rows


def main():
    c = creds()
    if not c:
        print("No AppFolio credentials in the environment — skipping group sync.")
        print("groups.json left as-is; PG / MF scopes will use whatever was last synced.")
        return 0

    out = {}   # listing uuid -> {"pg": "PG1", "mf": True, "mfp": "The Julian", "pid": "8145"}

    print("Property groups:")
    for label, gid in PGS.items():
        time.sleep(1.5)
        rows = units(c, group_ids=[gid])
        n = 0
        for r in rows:
            uid = str(r.get("rentable_uid") or "")
            if uid:
                e = out.setdefault(uid, {})
                e["pg"] = label
                if r.get("property_id"):
                    e.setdefault("pid", str(r["property_id"]))
                n += 1
        print(f"  {label}: {len(rows)} units")

    print("Multifamily (group 254):")
    props = report(c, "property_directory", scope(group_ids=[MF_GROUP]))
    ids = [str(p["property_id"]) for p in props if p.get("property_id")]
    names = {str(p["property_id"]): (p.get("property_name") or "").strip() for p in props}
    print(f"  {len(ids)} properties")
    for i in range(0, len(ids), BATCH):
        batch = ids[i:i + BATCH]
        time.sleep(1.5)
        rows = units(c, property_ids=batch)
        for r in rows:
            uid = str(r.get("rentable_uid") or "")
            if not uid:
                continue
            pid = str(r.get("property_id") or "")
            e = out.setdefault(uid, {})
            e["mf"] = True
            e["mfp"] = (r.get("property_name") or names.get(pid, "")).strip()
            e["pid"] = pid
        print(f"  batch {i // BATCH + 1}/{(len(ids) + BATCH - 1) // BATCH}: {len(rows)} units")

    meta = {"groups": {}}
    for key, cfg in CLIENT_GROUPS.items():
        time.sleep(1.5)
        props = report(c, "property_directory", scope(group_ids=[cfg["id"]]))
        pnames = sorted({(p.get("property_name") or "").strip() for p in props if p.get("property_name")})
        if not props:
            # An empty client group is indistinguishable from a rate-limited read, and a
            # blank widget on a client's own site is the worst possible failure. Refuse to
            # write a groups.json that would produce one.
            print(f"ERROR: client group {key} (id {cfg['id']}) returned 0 properties — "
                  f"refusing to write {OUT.name}", file=sys.stderr)
            return 1
        ids = [str(p["property_id"]) for p in props if p.get("property_id")]
        n = 0
        for i in range(0, len(ids), BATCH):
            time.sleep(1.5)
            for r in units(c, property_ids=ids[i:i + BATCH]):
                uid = str(r.get("rentable_uid") or "")
                if not uid:
                    continue
                e = out.setdefault(uid, {})
                e.setdefault("g", [])
                if key not in e["g"]:
                    e["g"].append(key)
                e.setdefault("pid", str(r.get("property_id") or ""))
                n += 1
        # `properties` is the full membership including buildings with no vacancy today.
        meta["groups"][key] = {"id": cfg["id"], "name": cfg["name"], "properties": pnames, "units": n}
        print(f"Client group {key} (id {cfg['id']}): {len(props)} properties, {n} units")
        print(f"  {', '.join(pnames)}")

    out["_meta"] = meta
    OUT.write_text(json.dumps(out, indent=0, sort_keys=True), encoding="utf-8")
    rows = [v for k, v in out.items() if k != "_meta"]
    mf = sum(1 for v in rows if v.get("mf"))
    pg = sum(1 for v in rows if v.get("pg"))
    print(f"\nWrote {OUT.name}: {len(rows)} units mapped ({pg} in a PG, {mf} multifamily, "
          f"{len({v['mfp'] for v in rows if v.get('mfp')})} MF properties)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
