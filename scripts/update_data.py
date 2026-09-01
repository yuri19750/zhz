#!/usr/bin/env python3
"""
Ververst data/objects.json met alle objecten van Zeeuwse Horeca Zaken die
daadwerkelijk op https://vmh-horeca.nl/aanbod/ staan, inclusief lat/lon.

Dit is de site-specifieke variant van het VMH-brede scrape-script: hij
haalt wel de volledige live objectenlijst op (nodig om te weten welke
makelaar bij welk object hoort), maar verwerkt en bewaart alleen de
objecten die bij Zeeuwse Horeca Zaken horen.

Werkwijze (zelfde als de handmatige aanpak):
1. Haal de live objectenlijst + makelaar op van de /aanbod/ archiefpagina
   (dit is de "waarheid" - wat er echt op de site staat, i.p.v. de volledige
   WP REST API die ook oude/verweesde posts teruggeeft).
2. Filter naar alleen objecten van Zeeuwse Horeca Zaken.
3. Haal van elk van die objecten de detailpagina op en lees lat/lon uit de
   verborgen Google Maps marker-div.
4. Schrijf het resultaat weg als data/objects.json.

Vereist: pip install requests
"""

import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

BASE_URL = "https://vmh-horeca.nl"
ARCHIVE_URL = f"{BASE_URL}/aanbod/"
OUTPUT_PATH = Path(__file__).resolve().parent.parent / "data" / "objects.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

MARKER_RE = re.compile(
    r'<div class="marker" data-lat="\s*(-?\d+\.?\d*)\s*" '
    r'data-lng="\s*(-?\d+\.?\d*)\s*">'
)

# Each listing "card" on the archive page starts with this marker.
# We split the page into per-card chunks and search *within* each chunk —
# a flat regex across the whole page (the previous approach) can drift and
# pair a card's link with a DIFFERENT card's makelaar class, because the
# lazy ".*?" has no card boundary to stop at.
CARD_SPLIT_MARKER = 'class="object-item"'
HREF_RE = re.compile(r'href="(https://vmh-horeca\.nl/aanbod/[^"]+/)"')
MAKELAAR_CLASS_RE = re.compile(r'object-makelaar makelaar-([a-z]+)')


def fetch(url: str, session: requests.Session) -> str:
    resp = session.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def get_live_objects(session: requests.Session):
    """Return {link: makelaar_slug} for every object actually listed on
    the /aanbod/ archive page (deduplicated), scoped strictly per card so
    a link is never paired with another card's makelaar."""
    html = fetch(ARCHIVE_URL, session)
    seen = {}
    # Skip index 0: it's the page content before the first card.
    for card_html in html.split(CARD_SPLIT_MARKER)[1:]:
        href_match = HREF_RE.search(card_html)
        makelaar_match = MAKELAAR_CLASS_RE.search(card_html)
        if not href_match or not makelaar_match:
            continue
        link = href_match.group(1)
        if link not in seen:
            seen[link] = makelaar_match.group(1)
    return seen


def get_title_and_id(link: str, session: requests.Session):
    """Look up id + title via the WP REST API by slug (cheap, no HTML parse)."""
    slug = link.rstrip("/").rsplit("/", 1)[-1]
    api_url = f"{BASE_URL}/wp-json/wp/v2/aanbod-api"
    resp = session.get(
        api_url,
        params={"slug": slug, "_fields": "id,title"},
        headers=HEADERS,
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data:
        return None, None
    return data[0]["id"], data[0]["title"]["rendered"]


# NOTE: "aanbod" is a custom post type, so the WordPress shortlink format
# "/?p=<id>" does NOT resolve for these objects (it 404s — that query var
# defaults to post_type=post). Always use the real permalink (the `link`
# already scraped from the archive page) instead of reconstructing one.


def get_coords(link: str, session: requests.Session):
    html = fetch(link, session)
    m = MARKER_RE.search(html)
    if not m:
        return None, None
    return float(m.group(1)), float(m.group(2))


TARGET_MAKELAAR_SLUG = "zeeuwsehorecazaken"


def process_object(link: str, makelaar_slug: str, session: requests.Session):
    try:
        obj_id, title = get_title_and_id(link, session)
        lat, lng = get_coords(link, session)
        if lat is None or obj_id is None:
            return None
        return {
            "id": str(obj_id),
            "title": title,
            "lat": lat,
            "lng": lng,
            "link": link,  # real permalink, not the broken "?p=" shortlink
        }
    except Exception as exc:  # noqa: BLE001
        print(f"  [WARN] {link}: {exc}", file=sys.stderr)
        return None


def main():
    session = requests.Session()

    print("Live objecten ophalen van /aanbod/ ...")
    live_objects = get_live_objects(session)
    print(f"  {len(live_objects)} unieke objecten gevonden op de site")

    # Alleen objecten van Zeeuwse Horeca Zaken verwerken — dit is een
    # site-specifieke kaart, geen behoefte om de andere makelaars te scrapen.
    zhz_objects = {
        link: slug for link, slug in live_objects.items()
        if slug == TARGET_MAKELAAR_SLUG
    }
    print(f"  waarvan {len(zhz_objects)} van Zeeuwse Horeca Zaken")

    results = []
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {
            pool.submit(process_object, link, slug, session): link
            for link, slug in zhz_objects.items()
        }
        for future in as_completed(futures):
            result = future.result()
            if result:
                results.append(result)

    skipped = len(zhz_objects) - len(results)
    print(f"Klaar: {len(results)} objecten met coordinaten, {skipped} overgeslagen "
          f"(discrete verkoop / geen locatie).")

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"Weggeschreven naar {OUTPUT_PATH}")



if __name__ == "__main__":
    main()
