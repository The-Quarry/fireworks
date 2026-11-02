#!/usr/bin/env python3
"""Rebuild events.json from the States of Guernsey firework events page.

Usage:
  python scripts/update_events.py                 # fetch https://www.gov.gg/fireworks
  python scripts/update_events.py --html FILE     # parse a saved copy instead
  python scripts/update_events.py --no-geocode    # skip online geocoding

gov.gg only lists forthcoming events, so past events are kept from the
previous events.json. A future event that disappears is kept and marked
"no_longer_listed"; if the same address reappears on another date it is
treated as rescheduled.
"""
import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
EVENTS_PATH = ROOT / "events.json"
GEOCACHE_PATH = ROOT / "scripts" / "geocache.json"
SOURCE_URL = "https://www.gov.gg/fireworks"
UA = "TheQuarryFireworksMap/2.0 (editor@thequarry.media)"

POSTCODE_RE = re.compile(r"\bGY\d{1,2}(?:\s*\d[A-Z]{2})?\b", re.I)
FULL_POSTCODE_RE = re.compile(r"\bGY\d{1,2}\s*\d[A-Z]{2}\b", re.I)
TIME_RE = re.compile(r"\d{1,2}[:.]\d{2}")
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july",
     "august", "september", "october", "november", "december"], 1)}

PARISHES = [
    ("St Peter Port", r"st\.?\s*peter\s*port"),
    ("St Sampson", r"st\.?\s*sampsons?"),
    ("Vale", r"\bvale\b"),
    ("Castel", r"\bcastel\b"),
    ("St Saviour", r"st\.?\s*sav(?:i)?ours?"),
    ("St Pierre du Bois", r"st\.?\s*pierre\s*du\s*bois|st\.?\s*peters?\b"),
    ("Torteval", r"\btorteval\b"),
    ("Forest", r"\bforest\b"),
    ("St Martin", r"st\.?\s*martins?\b"),
    ("St Andrew", r"st\.?\s*andrews?\b"),
]


def parish_of(text):
    t = (text or "").lower()
    for name, pat in PARISHES:
        if re.search(pat, t):
            return name
    return ""


def norm_key(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def cell_text(td):
    for br in td.find_all("br"):
        br.replace_with("\n")
    txt = td.get_text().replace("\xa0", " ")
    return [re.sub(r"\s+", " ", ln).strip(" ,") for ln in txt.split("\n") if ln.strip(" ,")]


def infer_date(day_header, today):
    """'Thursday 5 November' -> date, choosing the year that matches the weekday."""
    m = re.search(r"(\d{1,2})\s+([A-Za-z]+)", day_header)
    if not m or m.group(2).lower() not in MONTHS:
        return None
    day, month = int(m.group(1)), MONTHS[m.group(2).lower()]
    weekday = day_header.split()[0].lower()
    candidates = [today.year, today.year + 1, today.year - 1]
    for y in candidates:
        try:
            d = dt.date(y, month, day)
        except ValueError:
            continue
        if d.strftime("%A").lower() == weekday and abs((d - today).days) < 200:
            return d
    # weekday mismatch: fall back to the nearest plausible year
    d = dt.date(today.year, month, day)
    if (today - d).days > 180:
        d = dt.date(today.year + 1, month, day)
    print(f"warning: weekday in '{day_header}' does not match {d}", file=sys.stderr)
    return d


def parse_time(text):
    """'17:00-21:00 (fireworks at 19:15)' -> (time, start, end, fireworks_at, extra)."""
    fw = re.search(r"fireworks?\s+(?:at|from)\s+(\d{1,2}[:.]\d{2})", text, re.I)
    fireworks_at = fw.group(1).replace(".", ":") if fw else ""
    main = re.sub(r"\(.*?\)", "", text).strip()
    times = [t.replace(".", ":") for t in TIME_RE.findall(main)]
    start = times[0] if times else ""
    end = times[1] if len(times) > 1 else ""
    time_str = f"{start}-{end}" if end else start
    extra = re.sub(r"[()]", "", text.replace(main, "")).strip() if not fw else ""
    return time_str or text.strip(), start, end, fireworks_at, extra


def parse_page(html, today):
    soup = BeautifulSoup(html, "html.parser")
    table = None
    for t in soup.find_all("table"):
        head = t.get_text(" ").lower()
        if "event type" in head and "location" in head:
            table = t
            break
    if table is None:
        raise SystemExit("Could not find the firework events table on the page")

    events, current = [], None
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) == 1 or (tds and tds[0].get("colspan")):
            current = infer_date(" ".join(tds[0].get_text(" ").split()), today)
            continue
        if len(tds) < 3 or current is None:
            continue
        loc_lines = cell_text(tds[0])
        etype = " ".join(cell_text(tds[1]))
        time_raw = " ".join(cell_text(tds[2]))
        if not loc_lines:
            continue
        full = ", ".join(loc_lines)
        pc = POSTCODE_RE.search(full)
        postcode = pc.group(0).upper().replace("  ", " ") if pc else ""
        if postcode and " " not in postcode and len(postcode) > 4:
            postcode = postcode[:-3] + " " + postcode[-3:]
        name = (loc_lines[0] if len(loc_lines) > 1 else re.sub(POSTCODE_RE, "", full)).split(",")[0].strip(" ,")
        time_str, start, end, fw_at, extra = parse_time(time_raw)
        is_public = etype.lower().startswith("pub") or bool(tds[0].find(["strong", "b"]))
        events.append({
            "name": name,
            "full_address": full,
            "parish": parish_of(full),
            "postcode": postcode,
            "date": current.isoformat(),
            "time": time_str,
            "start": start,
            "end": end,
            "fireworks_at": fw_at,
            "type": "Public" if is_public else "Private",
            "notes": extra,
        })
    return events


def event_id(e):
    return f"{e['date']}|{norm_key(e['full_address'])}"


GSY_BBOX = "-2.72,49.40,-2.49,49.52"  # Guernsey only (excludes Alderney, Sark, Herm)


def photon(q, postcode=False):
    """Photon (photon.komoot.io) is OSM-based like Nominatim but includes the few
    Guernsey postcodes OSM holds and allows browser and CI use without blocking."""
    params = {"q": q, "limit": 5, "bbox": GSY_BBOX, "lang": "en"}
    if postcode:
        params["osm_tag"] = "place:postcode"
    url = "https://photon.komoot.io/api/?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.load(r)
    time.sleep(1.1)  # be polite to a free service
    hits = []
    for f in data.get("features", []):
        pr = f.get("properties", {})
        lon, lat = f["geometry"]["coordinates"]
        name = ", ".join(x for x in [pr.get("name"), pr.get("street"), pr.get("district"), pr.get("city")] if x)
        hits.append({"lat": lat, "lon": lon, "type": pr.get("osm_value", ""),
                     "postcode": pr.get("postcode") or (pr.get("name") if postcode else ""),
                     "display_name": name})
    if postcode:
        want = q.replace(" ", "").upper()
        hits = [h for h in hits if (h["postcode"] or "").replace(" ", "").upper() == want]
    return hits[0] if hits else None


def plausible(hit, q):
    """Geocoders fall back to broad matches (Nominatim returns the airport for any
    Guernsey postcode), so require a word from the place name in the result."""
    if hit.get("type") in ("aerodrome", "island", "country", "administrative"):
        return False
    first = q.split(",")[0].lower()
    words = [w for w in re.findall(r"[a-z']{4,}", first)
             if w not in ("road", "lane", "rue", "route", "estate", "close", "clos")]
    name = hit.get("display_name", "").lower()
    return not words or any(w in name for w in words)


def geocode(e, cache, enabled):
    """Lookup order: exact address in cache, postcode in cache (keys 'pc:GY1 2AB'),
    then Photon by postcode, address, then street. Parish centroid is the last resort.
    Edit scripts/geocache.json by hand to fix a location; it is never overwritten."""
    key = norm_key(e["full_address"])
    if key in cache:
        return cache[key]
    pc_key = "pc:" + (e["postcode"] or "").upper()
    if FULL_POSTCODE_RE.search(e["postcode"] or "") and pc_key in cache:
        return {**cache[pc_key], "precision": "postcode"}
    if not enabled:
        return None
    if FULL_POSTCODE_RE.search(e["postcode"] or ""):
        try:
            hit = photon(e["postcode"], postcode=True)
        except Exception as ex:
            print(f"geocode error for {e['postcode']!r}: {ex}", file=sys.stderr)
            return None
        if hit:
            res = {"lat": round(hit["lat"], 6), "lon": round(hit["lon"], 6),
                   "precision": "postcode", "query": e["postcode"]}
            cache[key] = res
            return res
    no_pc = POSTCODE_RE.sub("", e["full_address"]).strip(" ,")
    parts = [p.strip() for p in no_pc.split(",") if p.strip()]
    attempts = [("address", f"{no_pc}, Guernsey")]
    if len(parts) > 2:
        attempts.append(("street", f"{', '.join(parts[-2:])}, Guernsey"))
    if e["parish"]:
        attempts.append(("parish", f"{e['parish']}, Guernsey"))
    for precision, q in attempts:
        try:
            hit = photon(q)
        except Exception as ex:  # network hiccup: leave uncached, try next run
            print(f"geocode error for {q!r}: {ex}", file=sys.stderr)
            return None
        if hit and (precision == "parish" or plausible(hit, q)):
            res = {"lat": round(float(hit["lat"]), 6), "lon": round(float(hit["lon"]), 6),
                   "precision": precision, "query": q}
            cache[key] = res
            return res
    return None


def merge(prev, scraped, today):
    prev_by_id = {e["id"]: e for e in prev if "id" in e}
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    out, seen = [], set()
    for e in scraped:
        e["id"] = event_id(e)
        old = prev_by_id.get(e["id"])
        e["first_seen"] = (old or {}).get("first_seen", now)
        e["status"] = "listed"
        if old and old.get("rescheduled_from"):
            e["rescheduled_from"] = old["rescheduled_from"]
        seen.add(e["id"])
        out.append(e)

    scraped_by_addr = {}
    for e in out:
        scraped_by_addr.setdefault(norm_key(e["full_address"]), []).append(e)

    for old in prev:
        if old.get("id") in seen:
            continue
        if old["date"] < today.isoformat():
            out.append(old)  # past event: gov.gg drops these, keep our record
            continue
        # same address newly listed on another date: treat as rescheduled
        moved = [e for e in scraped_by_addr.get(norm_key(old["full_address"]), [])
                 if e["date"] != old["date"] and e["id"] not in prev_by_id]
        if moved:
            for e in moved:
                e["rescheduled_from"] = old["date"]
            continue
        if old.get("status") != "no_longer_listed":
            old = {**old, "status": "no_longer_listed", "removed_seen": now}
        out.append(old)

    out.sort(key=lambda e: (e["date"], e.get("start") or "99", e["full_address"]))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", help="parse a saved HTML file instead of fetching gov.gg")
    ap.add_argument("--no-geocode", action="store_true")
    ap.add_argument("--today", help="override today's date (YYYY-MM-DD) for testing")
    args = ap.parse_args()

    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    if args.html:
        html = Path(args.html).read_text(encoding="utf-8")
    else:
        req = urllib.request.Request(SOURCE_URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r:
            html = r.read().decode("utf-8", "replace")

    scraped = parse_page(html, today)
    if not scraped:
        raise SystemExit("Parsed zero events; refusing to overwrite events.json")

    prev_doc = json.loads(EVENTS_PATH.read_text()) if EVENTS_PATH.exists() else {}
    prev = prev_doc.get("events", []) if isinstance(prev_doc, dict) else []
    prev = [e for e in prev if e.get("date", "")[:4] == str(today.year)]

    cache = json.loads(GEOCACHE_PATH.read_text()) if GEOCACHE_PATH.exists() else {}
    for e in scraped:
        g = geocode(e, cache, not args.no_geocode)
        e["lat"], e["lon"] = (g["lat"], g["lon"]) if g else (None, None)
        e["geo_precision"] = g["precision"] if g else "none"
    GEOCACHE_PATH.write_text(json.dumps(cache, indent=2, ensure_ascii=False, sort_keys=True) + "\n")

    events = merge(prev, scraped, today)
    old_events = json.dumps(prev_doc.get("events") if isinstance(prev_doc, dict) else None, sort_keys=True)
    changed = old_events != json.dumps(events, sort_keys=True)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    doc = {
        "source": SOURCE_URL,
        "year": today.year,
        "checked": now,
        "changed": now if changed else prev_doc.get("changed", now),
        "events": events,
    }
    EVENTS_PATH.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
    missing = [e["full_address"] for e in events if e.get("lat") is None]
    print(f"{len(scraped)} listed, {len(events)} total, changed={changed}")
    if missing:
        print("not geocoded (add to scripts/geocache.json by hand):", *missing, sep="\n  ")


if __name__ == "__main__":
    main()
