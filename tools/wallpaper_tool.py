#!/usr/bin/env python3
"""Finds, adds and indexes location wallpapers for ETVLauncher.

Photos come from Wikimedia Commons, which gives the licence and the photographer for every file. Only licences that allow
reuse with credit are used (CC0, public domain, CC BY, CC BY-SA). The credit goes into the text next to the photo. Nothing is
drawn on the picture.

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
WIDTH = 1920          # wallpapers are stored at most this wide
MIN_WIDTH = 2400      # the original must be at least this wide, so the photo stays sharp on a 1080p / 4K TV
MIN_RATIO = 1.5       # wider than 3:2, so it fills a 16:9 screen without cutting much


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
        data = call(action="query", prop="imageinfo", pageids="|".join(str(x) for x in ids[i:i + 50]),
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
    if w < MIN_WIDTH or w / h < MIN_RATIO:
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
        "desc": desc[:300], "blob": (title + " " + desc + " " + cats).lower(), "hint_night": bool(NIGHT_WORDS.search(title + " " + desc + " " + cats)),
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
        for term in (q, q + " night", q + " dusk", q + " sunset", q + " sunrise", q + " aurora"):
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


def cmd_find(a):
    centres = [tuple(float(v) for v in c.split(",")) for c in a.centre]
    ids = find_ids(centres, a.radius, a.query, a.query)
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
            c["from"] = src
            cands.append(c)
    print("%d pass the size and licence checks" % len(cands))
    os.makedirs(a.work, exist_ok=True)
    good = []
    for c in cands:
        path = os.path.join(a.work, "%d.jpg" % c["id"])
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
    json.dump({"day": day, "night": night}, open(os.path.join(a.work, "candidates.json"), "w"), indent=1)
    for name, items in (("day", day), ("night", night)):
        for n in range(0, len(items), 12):
            sheet(items[n:n + 12], os.path.join(a.work, "sheet_%s_%d.jpg" % (name, n // 12 + 1)), a.work)
    print("day candidates: %d, night candidates: %d. Contact sheets are in %s" % (len(day), len(night), a.work))


def slug_ok(s):
    return re.sub(r'[<>:"/\\|?*]', "", s).strip()


def credit(c):
    who = re.sub(r"^(Photograph|Photo|Image) by ", "", c["author"])
    who = re.sub(r"\s*\((www|http)[^)]*\)", "", who).split(";")[0].strip(" .")
    return "Photo: %s, %s" % (who, c["licence"])


def cmd_add(a):
    cands = json.load(open(os.path.join(a.work, "candidates.json")))
    by_id = {c["id"]: c for c in cands["day"] + cands["night"]}
    base = os.path.join(ROOT, slug_ok(a.country), slug_ok(a.region), slug_ok(a.city))
    pj = os.path.join(base, "place.json")
    place = json.load(open(pj, encoding="utf-8")) if os.path.exists(pj) else {
        "country": a.country, "region": a.region, "city": a.city, "lat": a.lat, "lon": a.lon, "radius_km": a.radius_km, "photos": []}
    for mode, spec in (("day", a.day), ("night", a.night)):
        os.makedirs(os.path.join(base, mode), exist_ok=True)
        for item in spec or []:
            cid, _, rest = item.partition("=")
            title, _, subtitle = rest.partition("|")
            c = by_id[int(cid)]
            n = 1 + sum(1 for p in place["photos"] if p["mode"] == mode)
            fname = "%02d.jpg" % n
            im = Image.open(os.path.join(a.work, "%d.jpg" % c["id"])).convert("RGB")
            if im.width > WIDTH:
                im = im.resize((WIDTH, round(im.height * WIDTH / im.width)), Image.LANCZOS)
            im.save(os.path.join(base, mode, fname), quality=85, optimize=True, progressive=True)
            place["photos"].append({"mode": mode, "file": mode + "/" + fname, "title": title or c["name"],
                                    "subtitle": subtitle or a.city + ", " + a.region, "credit": credit(c), "source": c["page"]})
    json.dump(place, open(pj, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    print("wrote", base)
    cmd_index(a)


def cmd_index(a):
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
    i = sub.add_parser("index")
    i.set_defaults(fn=cmd_index)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
