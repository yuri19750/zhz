#!/usr/bin/env python3
"""
Ververst data/objects.json met alle actuele (te koop / te huur) objecten
van https://www.zeeuwsehorecazaken.nl/objecten — de EIGEN site van
Zeeuwse Horeca Zaken, niet vmh-horeca.nl.

Werkwijze:
1. Haal alle objecten op van de /objecten-collectie via Squarespace's eigen
   JSON-API (?format=json), met paginering.
2. Filter naar "actief" aanbod: alles wat niet als "verkocht" of "verhuurd"
   is getagd of getiteld (dat is dus geen momentopname van alles wat er
   ooit heeft gestaan, maar alleen wat nu echt te koop/te huur staat).
3. Alleen objecten met een daadwerkelijk vermeld adres in de
   "Kerngegevens"-tabel worden meegenomen - geen benadering op
   plaatsnaam-niveau. Staat er geen adres bij, dan komt het object niet
   op de kaart (totdat het adres alsnog wordt toegevoegd op de site).
4. Geocodeer het vermelde adres via de gratis Nominatim-API (OpenStreetMap)
   naar lat/lon. Dit gebeurt met 1 request per seconde, zoals Nominatim's
   gebruiksvoorwaarden vereisen.
5. Schrijf het resultaat weg als data/objects.json.

Vereist: pip install requests
"""

import json
import re
import sys
import time
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

# Nominatim requires a descriptive User-Agent identifying the application,
# and a max of 1 request/second - see
# https://operations.osmfoundation.org/policies/nominatim/
GEOCODE_HEADERS = {
    "User-Agent": "zhz-map-updater/1.0 (contact: via zeeuwsehorecazaken.nl)"
}
GEOCODE_DELAY_SECONDS = 1.1

ADDRESS_RE = re.compile(r'Adres</th>\s*<td>([^<]+)</td>')
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
                "tags": it.get("tags", []) or [],
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


def get_address(url: str, session: requests.Session):
    """Return the structured 'Adres' field from an object's Kerngegevens
    table, if present."""
    data = fetch_json(f"{BASE_URL}{url}?format=json", session)
    body = data.get("item", {}).get("body", "") or ""
    m = ADDRESS_RE.search(body)
    return m.group(1).strip() if m else None


def geocode(query: str, session: requests.Session):
    resp = session.get(
        "https://nominatim.openstreetmap.org/search",
        params={"format": "json", "limit": 1, "q": query},
        headers=GEOCODE_HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data:
        return None, None
    return float(data[0]["lat"]), float(data[0]["lon"])


def main():
    session = requests.Session()

    print("Actieve objecten ophalen van /objecten ...")
    active = get_active_listings(session)
    print(f"  {len(active)} actieve (niet-verkochte/verhuurde) objecten gevonden")

    results = []
    skipped_regional = 0
    skipped_no_geocode = 0

    for item in active:
        try:
            address = get_address(item["url"], session)
        except Exception as exc:  # noqa: BLE001
            print(f"  [WARN] adres ophalen mislukt voor {item['url']}: {exc}", file=sys.stderr)
            address = None

        # Alleen objecten met een daadwerkelijk vermeld adres in de
        # Kerngegevens-tabel worden meegenomen. Geen benadering op
        # plaatsnaam-niveau meer: als het adres niet vermeld staat, komt
        # het object niet op de kaart.
        if not address:
            skipped_regional += 1
            continue

        query = f"{address}, Nederland"

        try:
            lat, lng = geocode(query, session)
        except Exception as exc:  # noqa: BLE001
            print(f"  [WARN] geocoderen mislukt voor {item['url']}: {exc}", file=sys.stderr)
            lat, lng = None, None
        time.sleep(GEOCODE_DELAY_SECONDS)

        if lat is None:
            skipped_no_geocode += 1
            print(f"  [INFO] geen geocode-resultaat: {item['title']} ({query})")
            continue

        slug = item["url"].rsplit("/", 1)[-1]
        results.append({
            "id": slug,
            "title": item["title"],
            "lat": lat,
            "lng": lng,
            "link": f"{BASE_URL}{item['url']}",
        })

    print(
        f"Klaar: {len(results)} objecten met vermeld adres, "
        f"{skipped_regional} overgeslagen (geen adres vermeld), "
        f"{skipped_no_geocode} overgeslagen (geocoderen mislukt)."
    )

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"Weggeschreven naar {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
