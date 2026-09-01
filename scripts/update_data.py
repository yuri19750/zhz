#!/usr/bin/env python3
"""
Ververst data/objects.json met alle actuele (te koop / te huur) objecten
van https://www.zeeuwsehorecazaken.nl/objecten — de EIGEN site van
Zeeuwse Horeca Zaken.

Werkwijze:
1. Haal alle objecten op van de /objecten-collectie via Squarespace's eigen
   JSON-API (?format=json), met paginering.
2. Filter naar "actief" aanbod: alles wat niet als "verkocht" of "verhuurd"
   is getagd of getiteld.
3. Elk object is een blogbericht met een "summary" (excerpt) waarin de
   precieze locatie verborgen staat als platte tekst:
       vmh_address_lat: 51.441152,
       vmh_address_lng: 3.574700,
       vmh_status: te huur
   Dit veld wordt uitgelezen met een regex. Objecten zonder deze velden
   in hun summary (meestal brede/regionale advertenties zonder een
   duidelijke locatie) worden overgeslagen.
4. Schrijf het resultaat weg als data/objects.json.

Vereist: pip install requests
"""

import json
import re
import sys
from pathlib import Path

import requests

BASE_URL = "https://www.zeeuwsehorecazaken.nl"
COLLECTION_URL = f"{BASE_URL}/objecten"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "objects.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

LAT_RE = re.compile(r'vmh_address_lat:\s*(-?\d+\.\d+)')
LNG_RE = re.compile(r'vmh_address_lng:\s*(-?\d+\.\d+)')
SOLD_WORDS_RE = re.compile(r'verkocht|verhuurd', re.IGNORECASE)


def fetch_json(url: str, session: requests.Session) -> dict:
    resp = session.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json()


def get_active_listings(session: requests.Session):
    """Return all non-sold/non-rented listing items from the /objecten
    collection, paginating through Squarespace's JSON API."""
    items = []
    url = f"{COLLECTION_URL}?format=json"
    for _ in range(20):  # hard cap to avoid infinite loop on unexpected data
        data = fetch_json(url, session)
        collection_items = (
            data.get("collection", {}).get("items")
            or data.get("items")
            or []
        )
        for it in collection_items:
            items.append({
                "title": it.get("title", "").strip(),
                "url": it.get("fullUrl"),
                "categories": it.get("categories", []) or [],
            })
        pagination = data.get("pagination") or {}
        if pagination.get("nextPage"):
            url = f"{BASE_URL}{pagination['nextPageUrl']}&format=json"
        else:
            break

    active = []
    for it in items:
        cats = it["categories"]
        sold = "verkocht" in cats or "verhuurd" in cats or SOLD_WORDS_RE.search(it["title"])
        if not sold:
            active.append(it)
    return active


def get_coords_from_excerpt(url: str, session: requests.Session):
    """Read the hidden vmh_address_lat / vmh_address_lng values out of the
    post's summary (excerpt) field."""
    data = fetch_json(f"{BASE_URL}{url}?format=json", session)
    excerpt = data.get("item", {}).get("excerpt", "") or ""
    lat_m = LAT_RE.search(excerpt)
    lng_m = LNG_RE.search(excerpt)
    if not lat_m or not lng_m:
        return None, None
    return float(lat_m.group(1)), float(lng_m.group(1))


def main():
    session = requests.Session()

    print("Actieve objecten ophalen van /objecten ...")
    active = get_active_listings(session)
    print(f"  {len(active)} actieve (niet-verkochte/verhuurde) objecten gevonden")

    results = []
    skipped = 0

    for item in active:
        try:
            lat, lng = get_coords_from_excerpt(item["url"], session)
        except Exception as exc:  # noqa: BLE001
            print(f"  [WARN] ophalen mislukt voor {item['url']}: {exc}", file=sys.stderr)
            lat, lng = None, None

        if lat is None:
            skipped += 1
            print(f"  [INFO] geen lat/lng in summary: {item['title']}")
            continue

        slug = item["url"].rsplit("/", 1)[-1]
        results.append({
            "id": slug,
            "title": item["title"],
            "lat": lat,
            "lng": lng,
            "link": f"{BASE_URL}{item['url']}",
        })

    print(f"Klaar: {len(results)} objecten met lat/lon, {skipped} overgeslagen (geen lat/lon in summary).")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"Weggeschreven naar {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
