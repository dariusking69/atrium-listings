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
  homes/<id>.html      one custom detail page per listing
  listings.json        enriched data cache
"""
import json, re, sys, ssl, html, urllib.request
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

HERE = Path(__file__).parent
SUB = "atriummanagement"
BASE = f"https://{SUB}.appfolio.com"
LISTINGS_URL = f"{BASE}/listings"

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
def fetch_grid():
    page = get(LISTINGS_URL)
    s = page.find("markers: [")
    i = page.find("[", s)
    depth = 0
    for j in range(i, len(page)):
        if page[j] == "[":
            depth += 1
        elif page[j] == "]":
            depth -= 1
            if depth == 0:
                return json.loads(page[i:j + 1])
    raise RuntimeError("markers[] not found")


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
            "city": parts[1] if len(parts) > 1 else "",
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
    apply_url = f"{BASE}/listings/rental_applications/new?listable_uid={m.group(1)}&source=Website" if m else listing["appfolio_url"]
    listing.update(title=title, description=desc, terms=terms, pets=pets,
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
        if c and not force and c.get("photos"):
            l.update({k: c[k] for k in ("title", "description", "terms", "pets", "photos", "apply_url") if k in c})
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


def build(listings):
    for l in listings:
        l["available"] = derive_available(l.get("terms"))
    (HERE / "homes").mkdir(exist_ok=True)
    for l in listings:
        (HERE / "homes" / f'{l["id"]}.html').write_text(build_detail_page(l), encoding="utf-8")
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
        '<a href="widget.html">View residential listings</a>', encoding="utf-8")
    (HERE / "listings.json").write_text(json.dumps(listings, indent=2), encoding="utf-8")
    print(f"Built index.html + {len(listings)} detail pages in homes/")


# ===================== TEMPLATES =====================
SHARED_CSS = r"""
  :root{--red:#f13d3d;--red-dk:#e82121;--ink:#121212;--ink-2:#424245;--line:#e6e6e6;
    --bg:#f5f5f7;--card:#fff;--radius:14px;--sans:"IBM Plex Sans","Helvetica Neue",Helvetica,Arial,sans-serif}
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
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap">
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
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap">
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
