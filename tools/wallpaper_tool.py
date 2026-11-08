#!/usr/bin/env python3
"""Finds, adds and indexes location wallpapers for ETVLauncher.

Photos come from Wikimedia Commons, which gives the licence and the photographer for every file. Only licences that allow
reuse with credit are used (CC0, public domain, CC BY, CC BY-SA). The credit goes into the text next to the photo. Nothing is
drawn on the picture.

  seed   Run auto for every place of a seed list (tools/seed/*.json).
  auto   Find and add a pack for one point without help (the new-places workflow uses this; a person reviews the pull request).
  find   Search Commons near a place and write candidate pictures and contact sheets to a work folder, for a person to look at.
  add    Copy the chosen candidates into  <Country>/<Region>/<City>/{day,night}/  and write that city's place.json.
  index  Rebuild places.json (the list the app downloads: every place with its coordinates).

Needs Python 3 and Pillow. No other packages.
"""
import argparse
import html
import io
import json
import math
import os
import re
import sys
import time
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw, ImageFont, ImageStat

API = "https://commons.wikimedia.org/w/api.php"
# Wikimedia asks for a User-Agent that says who is calling.
UA = "ETVLauncher-Wallpapers/1.0 (https://github.com/Evesain/ETVLauncher-Wallpapers)"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OK_LICENCE = re.compile(r"^(CC0|Public domain|PD\b|CC[ -]BY(-SA)?\b|Attribution)", re.I)
BAD_WORDS = re.compile(r"\b(map|logo|flag|coat of arms|diagram|plan|poster|advert|sign|screenshot|scan|stamp|drawing)\b", re.I)
NIGHT_WORDS = re.compile(r"night|dusk|twilight|sunset|sunrise|illuminat|aurora|lights|evening|dawn", re.I)
QUALITY = re.compile(r"Featured pictures|Quality images|Valued images|Pictures of the day", re.I)
WIDTH = 1920          # wallpapers are stored at most this wide
MIN_WIDTH = 2400      # the original must be at least this wide, so the photo stays sharp on a 1080p / 4K TV
MIN_RATIO = 1.5       # wider than 3:2, so it fills a 16:9 screen without cutting much
MAX_RATIO = 2.0       # not a panorama strip: a 16:9 screen would show only a slice of it


def call(**params):
    params["format"] = "json"
    url = API + "?" + urllib.parse.urlencode(params)
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception:
            time.sleep(2 * (attempt + 1))
    raise SystemExit("Commons did not answer: " + url)


def strip_html(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s or "")).strip()


def pages_from(titles_or_ids):
    """imageinfo (size, licence, author) for up to 50 page ids at a time."""
    out = []
    ids = list(titles_or_ids)
    for i in range(0, len(ids), 50):
        data = call(action="query", prop="imageinfo|coordinates", colimit="max", pageids="|".join(str(x) for x in ids[i:i + 50]),
                    iiprop="url|size|mime|extmetadata", iiurlwidth=WIDTH, iiextmetadatafilter=
                    "LicenseShortName|Artist|ImageDescription|Categories|DateTimeOriginal|ObjectName")
        out.extend(data.get("query", {}).get("pages", {}).values())
    return out


def keep(page):
    """Turns a Commons page into a candidate dict, or None when it is not good enough."""
    ii = (page.get("imageinfo") or [None])[0]
    if not ii or ii.get("mime") != "image/jpeg":
        return None
    w, h = ii.get("width", 0), ii.get("height", 1)
    if w < MIN_WIDTH or w / h < MIN_RATIO or w / h > MAX_RATIO:
        return None
    meta = ii.get("extmetadata", {})
    licence = strip_html(meta.get("LicenseShortName", {}).get("value"))
    if not OK_LICENCE.match(licence):
        return None
    title = re.sub(r"^File:|\.jpe?g$", "", page["title"], flags=re.I)
    if BAD_WORDS.search(title):
        return None
    desc = strip_html(meta.get("ImageDescription", {}).get("value"))
    cats = strip_html(meta.get("Categories", {}).get("value"))
    return {
        "id": page["pageid"], "file": page["title"], "name": strip_html(meta.get("ObjectName", {}).get("value")) or title,
        "author": strip_html(meta.get("Artist", {}).get("value")) or "Unknown", "licence": licence,
        "page": ii.get("descriptionurl"), "thumb": ii.get("thumburl"), "width": w, "height": h,
        "lat": (page.get("coordinates") or [{}])[0].get("lat"), "lon": (page.get("coordinates") or [{}])[0].get("lon"),
        "desc": desc[:300], "quality": bool(QUALITY.search(cats)), "blob": (title + " " + desc + " " + cats).lower(), "hint_night": bool(NIGHT_WORDS.search(title + " " + desc + " " + cats)),
    }


def find_ids(centres, radius, queries, near):
    ids = {}
    for lat, lon in centres:
        cont = {}
        for _ in range(6):  # up to about 300 files per centre
            data = call(action="query", list="geosearch", gscoord="%s|%s" % (lat, lon), gsradius=radius,
                        gsnamespace=6, gslimit=50, **cont)
            for g in data.get("query", {}).get("geosearch", []):
                ids[g["pageid"]] = ("geo", "")
            if "continue" not in data:
                break
            cont = data["continue"]
    for q in queries:
        terms = [q, q + " night", q + " dusk", q + " sunset", q + " sunrise", q + " aurora"]
        # The pictures that Commons editors marked as quality, featured or valued are the best wallpapers.
        for flt in ('deepcategory:"Quality images"', 'deepcategory:"Featured pictures on Wikimedia Commons"', 'deepcategory:"Valued images"'):
            terms += [q + " " + flt, q + " night " + flt]
        for term in terms:
            cont = {}
            for _ in range(2):
                data = call(action="query", list="search", srsearch=term + " filetype:bitmap", srnamespace=6, srlimit=50, **cont)
                for s in data.get("query", {}).get("search", []):
                    ids.setdefault(s["pageid"], ("text", q))
                if "continue" not in data:
                    break
                cont = data["continue"]
    return ids


def fetch(url):
    for attempt in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=90) as r:
                return r.read()
        except Exception:
            time.sleep(3 * (attempt + 1))
    return None


def brightness(im):
    small = im.convert("L").resize((64, 36))
    return ImageStat.Stat(small).mean[0]


def sheet(items, path, work):
    """A contact sheet: 4 x 3 numbered thumbnails, to look at and choose from."""
    cols, rows, tw, th = 4, 3, 480, 270
    img = Image.new("RGB", (cols * tw, rows * (th + 26)), (20, 20, 24))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 18)
    except OSError:
        font = ImageFont.load_default()
    for i, c in enumerate(items[:cols * rows]):
        x, y = (i % cols) * tw, (i // cols) * (th + 26)
        im = Image.open(os.path.join(work, "%d.jpg" % c["id"])).convert("RGB")
        im.thumbnail((tw, th))
        img.paste(im, (x, y))
        d.text((x + 4, y + th + 3), "#%d  %s" % (c["id"], c["name"][:40]), fill=(235, 235, 235), font=font)
    img.save(path, quality=80)


def gather(centres, radius, queries, maxdist=35000):
    """Candidate pictures (size, licence and place checked) near the centres or named by the queries. No pictures are downloaded yet."""
    ids = find_ids(centres, radius, queries, queries)
    print("%d files found; checking size and licence ..." % len(ids))
    cands = []
    keys = list(ids)
    for i in range(0, len(keys), 50):
        for p in pages_from(keys[i:i + 50]):
            c = keep(p)
            if not c:
                continue
            src, term = ids[p["pageid"]]
            # A text hit counts only when the place name is really in its title, description or categories.
            if src == "text" and term.lower() not in c["blob"]:
                continue
            # A file with its own coordinates must lie within 35 km (or the place's radius) of a centre (so "Whitby" is not Whitby in Canada). Larger places allow more.
            if c["lat"] is not None and min(metres(c["lat"], c["lon"], la, lo) for la, lo in centres) > maxdist:
                continue
            c["from"] = src if c["lat"] is not None or src == "geo" else "text"
            cands.append(c)
    print("%d pass the size and licence checks" % len(cands))
    return cands


def download(cands, work):
    """Downloads each candidate at 1920 px and measures its brightness. Returns (day, night) lists."""
    os.makedirs(work, exist_ok=True)
    good = []
    for c in cands:
        path = os.path.join(work, "%d.jpg" % c["id"])
        if not os.path.exists(path):
            data = fetch(c["thumb"])
            if not data:
                continue
            open(path, "wb").write(data)
        try:
            c["brightness"] = round(brightness(Image.open(path)), 1)
        except OSError:
            continue
        good.append(c)
    # Night = dark picture; day = bright. Pictures in between (grey, indoors, dusk) are left for a person to decide.
    night = sorted([c for c in good if c["brightness"] < 75], key=lambda c: (not c["hint_night"], c["brightness"]))
    day = sorted([c for c in good if c["brightness"] >= 75], key=lambda c: -c["width"] * c["height"])
    for c in good:
        c.pop("blob", None)
    json.dump({"day": day, "night": night}, open(os.path.join(work, "candidates.json"), "w"), indent=1)
    return day, night


def cmd_find(a):
    centres = [tuple(float(v) for v in c.split(",")) for c in a.centre]
    day, night = download(gather(centres, a.radius, a.query), a.work)
    for name, items in (("day", day), ("night", night)):
        for n in range(0, len(items), 12):
            sheet(items[n:n + 12], os.path.join(a.work, "sheet_%s_%d.jpg" % (name, n // 12 + 1)), a.work)
    print("day candidates: %d, night candidates: %d. Contact sheets are in %s" % (len(day), len(night), a.work))


# ---------------------------------------------------------------- automatic pick (used by the new-places workflow)

# Words that mark a picture as a poor wallpaper (indoors, streets, signs, close-ups) or a good one (views, landmarks, nature).
POOR = re.compile(r"interior|inside|church|cathedral|chapel|\bpub\b|\binn\b|hotel|station|\bbus\b|\broad\b|street|\blane\b|junction|"
                  r"shop|market|cemetery|school|hospital|car park|garage|sign\b|plaque|memorial|window|altar|font\b|portrait|crowd|"
                  r"train|tram|\bcar\b|lorry|cattle|sheep|\bcow|roundabout|houses?\b|flats|estate|"
                  r"\bshed\b|barn|pylon|\bbin\b|recycling|warehouse|office|club|society|centre\b|"
                  r"\bhen\b|\bstag\b|party|wedding|birthday|selfie|friends|family|\bgirls?\b|\bboys?\b|\bwoman|\bman\b|\blady\b|"
                  r"kids?\b|child|people|portrait|\bme\b|\bmy\b|"
                  r"tourists?|posing|ruined|outbuilding|farmland|panosphere|panorama|moss\b|graffiti|\bcctv\b|scaffold", re.I)
GOOD = re.compile(r"park|lake|reservoir|hill|castle|view|sunrise|sunset|valley|river|canal|beach|harbou?r|skyline|panorama|forest|"
                  r"bridge|tower|abbey|cliff|mountain|moor|peak|cathedral|hall\b|palace|pier|lighthouse|waterfall|ridge|coast|"
                  r"bay\b|island|woods?\b|common|gardens?|aurora|night|dusk|twilight|illuminat", re.I)


def stem(name):
    n = re.sub(r"\s*-\s*geograph.*$", "", name, flags=re.I)
    return re.sub(r"[\d\W_]+$", "", n).lower()


def nice_title(name):
    n = re.sub(r"\s*-\s*geograph.*$", "", name, flags=re.I)
    n = re.sub(r"^\d{4}-\d{2}-\d{2}\s+", "", n)
    n = re.sub(r"\s*-\s*Flickr\s*-.*$", "", n)                 # "... - Flickr - username"
    n = re.sub(r"\s*\(\d{6,}\)$", "", n)                         # "(22879382966)"
    n = re.sub(r"\s*\(\d+\)$|\s+\d{1,4}$", "", n).strip(" -,.")
    return (n[:42].rsplit(" ", 1)[0] if len(n) > 42 else n) or "Photo"


def score(c, night):
    sc = 0.0
    if POOR.search(c["name"]) or POOR.search(c.get("desc", "")[:80]):
        sc -= 6
    if GOOD.search(c["name"]) or GOOD.search(c.get("desc", "")[:120]):
        sc += 3
    sc += 5 if c.get("quality") else 0                      # marked by Commons editors as a quality or featured picture
    sc += 2 if c.get("from") == "geo" else 0
    sc += 1 if c.get("lat") is not None else 0                # it carries coordinates near the place
    sc += min(c["width"] * c["height"] / 2e7, 2)           # bigger originals stay sharper
    sc += 1 if c["width"] / c["height"] >= 1.7 else 0       # close to 16:9, so little is cropped
    if "brightness" in c:
        if night:
            sc += 4 if c["hint_night"] else -3
            sc -= 3 if c["brightness"] < 12 else 0          # almost black
        else:
            sc -= 3 if c["brightness"] > 190 or c["brightness"] < 90 else 0
    return sc


def pick(items, night, n):
    chosen, stems, authors = [], set(), {}
    for c in sorted(items, key=lambda c: -score(c, night)):
        if POOR.search(c["name"]) or POOR.search(c.get("desc", "")[:80]):
            continue                                            # never pick indoors, streets, signs or people
        if score(c, night) < (1 if night else 0):
            break
        st = stem(c["name"])
        if st in stems or authors.get(c["author"], 0) >= 2:
            continue
        stems.add(st)
        authors[c["author"]] = authors.get(c["author"], 0) + 1
        chosen.append(c)
        if len(chosen) >= n:
            break
    return chosen


def reverse(lat, lon):
    """Country, region and town for a point, from OpenStreetMap's Nominatim (a few requests a day: well inside its limits)."""
    url = "https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode(
        {"format": "jsonv2", "zoom": 14, "addressdetails": 1, "accept-language": "en", "lat": lat, "lon": lon})
    data = json.loads(fetch(url) or b"{}")
    ad = data.get("address", {})
    town = next((ad[k] for k in ("city", "town", "village", "hamlet", "municipality", "suburb", "county") if ad.get(k)), "")
    region = ad.get("state") or ad.get("region") or ad.get("state_district") or ad.get("county") or ""
    return ad.get("country", ""), region, town


def metres(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    h = math.sin((lat2 - lat1) * p / 2) ** 2 + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2
    return 12742000 * math.asin(math.sqrt(h))


def covered(lat, lon):
    """The place that already covers this point, or None."""
    f = os.path.join(ROOT, "places.json")
    if not os.path.exists(f):
        return None
    for p in json.load(open(f, encoding="utf-8"))["places"]:
        if metres(lat, lon, p["lat"], p["lon"]) <= p.get("radius_km", 25) * 1000:
            return p
    return None


def auto(lat, lon, place, work, n=4, explicit=None):
    """Finds and adds a pack for one point. Returns (status, text): done, covered or none.
    explicit = {country, region, city, radius_km, queries}: a named place from a seed list (no geocoding, no overlap check)."""
    if explicit is None:
        have = covered(lat, lon)
        if have:
            return "covered", "%s already covers this point" % have["city"]
        country, region, town = reverse(lat, lon)
        town = town or place
        radius_km = 25
        queries = [town] + ([place] if place and place.lower() != town.lower() else [])
    else:
        country, region, town = explicit["country"], explicit["region"], explicit["city"]
        radius_km = explicit.get("radius_km", 25)
        queries = [town] + list(explicit.get("queries", []))
    if not (country and region and town):
        return "none", "no place name found for %s,%s" % (lat, lon)
    ring = min(radius_km * 0.35, 20)                      # km from the centre to the outer search circles
    dlat = ring / 111.0
    dlon = ring / (111.0 * max(math.cos(math.radians(lat)), 0.2))
    centres = [(lat, lon), (lat + dlat, lon), (lat - dlat, lon), (lat, lon + dlon), (lat, lon - dlon)]
    if radius_km >= 40:
        centres += [(lat + dlat * 1.2, lon + dlon * 1.2), (lat + dlat * 1.2, lon - dlon * 1.2),
                    (lat - dlat * 1.2, lon + dlon * 1.2), (lat - dlat * 1.2, lon - dlon * 1.2)]
    cands = gather(centres, 10000, queries, max(35000, int(radius_km * 1200)))
    # Download only the most promising ones, so the run stays quick.
    cands.sort(key=lambda c: -(score(c, False) + (2 if c["hint_night"] else 0)))
    day, night = download(cands[:100], work)
    d = pick(day, False, n)
    used = set(c["id"] for c in d)
    # Dusk, sunset and blue-hour pictures count as night photos when their own words say so.
    dusk = [c for c in day if c["brightness"] < 110 and c["hint_night"] and c["id"] not in used]
    ni = pick(night + dusk, True, n)
    if len(d) < 3:
        return "none", "%s, %s, %s: only %d day photos fit" % (town, region, country, len(d))
    sub = town + ", " + region
    folder = add_photos(work, country, region, town, round(lat, 4), round(lon, 4), radius_km,
                        ["%d=%s|%s" % (c["id"], nice_title(c["name"]), sub) for c in d],
                        ["%d=%s|%s" % (c["id"], nice_title(c["name"]), sub) for c in ni])
    if len(ni) < 3:
        # Too few night photos here: take some from the nearest packs. Their captions keep the real place name.
        got = borrow_night(folder, lat, lon, 4 - len(ni))
        if len(ni) + got < 3:
            import shutil
            shutil.rmtree(os.path.join(ROOT, folder), ignore_errors=True)
            return "none", "%s, %s, %s: only %d day and %d night photos fit" % (town, region, country, len(d), len(ni) + got)
        return "done", folder + " (%d night photos borrowed)" % got
    return "done", folder


def borrow_night(folder, lat, lon, need):
    """Copies up to {need} night photos from the nearest other packs (within 250 km) into this pack. Returns how many."""
    import shutil
    base = os.path.join(ROOT, folder)
    pj = os.path.join(base, "place.json")
    place = json.load(open(pj, encoding="utf-8"))
    near = []
    for dirpath, _, files in os.walk(ROOT):
        if "place.json" not in files or ".git" in dirpath or os.path.abspath(dirpath) == os.path.abspath(base):
            continue
        q = json.load(open(os.path.join(dirpath, "place.json"), encoding="utf-8"))
        km = metres(lat, lon, q["lat"], q["lon"]) / 1000
        if km <= 250 and any(x["mode"] == "night" for x in q["photos"]):
            near.append((km, dirpath, q))
    near.sort(key=lambda t: t[0])
    taken = 0
    for km, dirpath, q in near:
        for ph in q["photos"]:
            if taken >= need:
                break
            if ph["mode"] != "night" or any(x["source"] == ph["source"] for x in place["photos"]):
                continue
            n = 1 + sum(1 for x in place["photos"] if x["mode"] == "night")
            fname = "night/%02d.jpg" % n
            os.makedirs(os.path.join(base, "night"), exist_ok=True)
            shutil.copyfile(os.path.join(dirpath, ph["file"]), os.path.join(base, fname))
            entry = dict(ph)
            entry["file"] = fname
            place["photos"].append(entry)
            taken += 1
        if taken >= need:
            break
    json.dump(place, open(pj, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    return taken


def cmd_auto(a):
    status, text = auto(a.lat, a.lon, a.place or "", a.work)
    print("%s: %s" % (status, text))
    print("RESULT=" + status)


def cmd_seed(a):
    """Runs auto for every place in a seed list that has no folder yet. --shard K/N takes every N-th place, so several can run at once."""
    import shutil
    import tempfile
    global SKIP_INDEX
    SKIP_INDEX = True
    entries = []
    for f in a.list:
        entries += json.load(open(f, encoding="utf-8"))
    k, n = (int(v) for v in a.shard.split("/"))
    failed = []
    for i, e in enumerate(entries):
        if i % n != k:
            continue
        pj = os.path.join(ROOT, slug_ok(e["country"]), slug_ok(e["region"]), slug_ok(e["city"]), "place.json")
        if os.path.exists(pj):
            print("skip (exists):", e["city"])
            continue
        work = tempfile.mkdtemp(prefix="wp-seed-")
        try:
            status, text = auto(e["lat"], e["lon"], e["city"], work, explicit=e)
        except Exception as ex:  # keep going: one bad place must not stop the list
            status, text = "error", repr(ex)
        shutil.rmtree(work, ignore_errors=True)
        print("%-5s %s: %s" % (status.upper(), e["city"], text), flush=True)
        if status != "done":
            failed.append(e["city"])
    print("FAILED: " + (", ".join(failed) or "none"))


def slug_ok(s):
    return re.sub(r'[<>:"/\\|?*]', "", s).strip()


def credit(c):
    who = re.sub(r"^(Photograph|Photo|Image) by ", "", c["author"])
    who = re.sub(r"\s*\((www|http)[^)]*\)", "", who).split(";")[0]
    who = re.sub(r"\s*,[\s,]*", ", ", who).strip(" .,")           # "North Staffordshire,, England" -> one comma
    return "Photo: %s, %s" % (who, c["licence"])


SKIP_INDEX = False   # set while several seed runs write at once; run the index command at the end


def add_photos(work, country, region, city, lat, lon, radius_km, day, night):
    cands = json.load(open(os.path.join(work, "candidates.json"), encoding="utf-8"))
    by_id = {c["id"]: c for c in cands["day"] + cands["night"]}
    base = os.path.join(ROOT, slug_ok(country), slug_ok(region), slug_ok(city))
    pj = os.path.join(base, "place.json")
    place = json.load(open(pj, encoding="utf-8")) if os.path.exists(pj) else {
        "country": country, "region": region, "city": city, "lat": lat, "lon": lon, "radius_km": radius_km, "photos": []}
    for mode, spec in (("day", day), ("night", night)):
        os.makedirs(os.path.join(base, mode), exist_ok=True)
        for item in spec or []:
            cid, _, rest = item.partition("=")
            title, _, subtitle = rest.partition("|")
            c = by_id[int(cid)]
            n = 1 + sum(1 for p in place["photos"] if p["mode"] == mode)
            fname = "%02d.jpg" % n
            im = Image.open(os.path.join(work, "%d.jpg" % c["id"])).convert("RGB")
            if im.width > WIDTH:
                im = im.resize((WIDTH, round(im.height * WIDTH / im.width)), Image.LANCZOS)
            im.save(os.path.join(base, mode, fname), quality=82, optimize=True, progressive=True)
            place["photos"].append({"mode": mode, "file": mode + "/" + fname, "title": title or c["name"],
                                    "subtitle": subtitle or city + ", " + region, "credit": credit(c), "source": c["page"]})
    json.dump(place, open(pj, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    if not SKIP_INDEX:
        build_index()
    return os.path.relpath(base, ROOT).replace("\\", "/")


def cmd_add(a):
    print("wrote", add_photos(a.work, a.country, a.region, a.city, a.lat, a.lon, a.radius_km, a.day, a.night))


def build_index():
    places = []
    for dirpath, _, files in os.walk(ROOT):
        if "place.json" in files and ".git" not in dirpath:
            p = json.load(open(os.path.join(dirpath, "place.json"), encoding="utf-8"))
            places.append({"country": p["country"], "region": p["region"], "city": p["city"], "lat": p["lat"], "lon": p["lon"],
                           "radius_km": p.get("radius_km", 25),
                           "path": os.path.relpath(dirpath, ROOT).replace("\\", "/"),
                           "day": sum(1 for x in p["photos"] if x["mode"] == "day"),
                           "night": sum(1 for x in p["photos"] if x["mode"] == "night")})
    places.sort(key=lambda p: (p["country"], p["region"], p["city"]))
    json.dump({"version": 1, "places": places}, open(os.path.join(ROOT, "places.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("places.json: %d place(s)" % len(places))


def cmd_index(a):
    build_index()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find")
    f.add_argument("--centre", action="append", required=True, help="lat,lon (repeat for more centres; each searches 10 km)")
    f.add_argument("--radius", type=int, default=10000, help="metres, at most 10000")
    f.add_argument("--query", action="append", default=[], help="text search words, such as the town name (repeat)")
    f.add_argument("--work", required=True, help="folder for candidates and contact sheets")
    f.set_defaults(fn=cmd_find)
    d = sub.add_parser("add")
    d.add_argument("--work", required=True)
    d.add_argument("--country", required=True)
    d.add_argument("--region", required=True)
    d.add_argument("--city", required=True)
    d.add_argument("--lat", type=float, required=True)
    d.add_argument("--lon", type=float, required=True)
    d.add_argument("--radius-km", dest="radius_km", type=int, default=25)
    d.add_argument("--day", nargs="*", help="id, id=Title or id=Title|Subtitle, from the contact sheet")
    d.add_argument("--night", nargs="*", help="id, id=Title or id=Title|Subtitle, from the contact sheet")
    d.set_defaults(fn=cmd_add)
    u = sub.add_parser("auto", help="find and add a pack for one point by itself (used by the new-places workflow)")
    u.add_argument("--lat", type=float, required=True)
    u.add_argument("--lon", type=float, required=True)
    u.add_argument("--place", default="")
    u.add_argument("--work", required=True)
    u.set_defaults(fn=cmd_auto)
    sd = sub.add_parser("seed", help="run auto for every place of a seed list (tools/seed/*.json) that has no folder yet")
    sd.add_argument("list", nargs="+", help="one or more seed lists")
    sd.add_argument("--shard", default="0/1", help="K/N: take every N-th place, starting at K")
    sd.set_defaults(fn=cmd_seed)
    i = sub.add_parser("index")
    i.set_defaults(fn=cmd_index)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
