"""
Thin client for Open Library — used to auto-fill book card metadata
(year, author, genres, country/language, cover). Free, no API key needed.
"""
import httpx

SEARCH_URL = "https://openlibrary.org/search.json"
COVER_URL = "https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"

# Same five broad geo buckets as GEO_OPTIONS in types.ts (see tmdb_client.py
# for the movie/show version of this mapping) — guessed from the book's
# language code, since Open Library doesn't give a country directly.
LANGUAGE_TO_GEO = {
    "rus": "Россия", "ukr": "Россия",
    "eng": "Америка",
    "fre": "Европа", "ger": "Европа", "spa": "Европа", "ita": "Европа",
    "por": "Европа", "pol": "Европа", "swe": "Европа", "dut": "Европа",
    "jpn": "Азия", "chi": "Азия", "kor": "Азия",
}


async def find_book(title: str) -> dict | None:
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(SEARCH_URL, params={"q": title, "limit": 1})
        resp.raise_for_status()
        data = resp.json()

    docs = data.get("docs") or []
    if not docs:
        return None
    doc = docs[0]

    year = doc.get("first_publish_year")
    authors = doc.get("author_name") or []
    subjects = (doc.get("subject") or [])[:3]
    languages = doc.get("language") or []
    geo = [LANGUAGE_TO_GEO[lang] for lang in languages if lang in LANGUAGE_TO_GEO][:1]
    cover_id = doc.get("cover_i")

    return {
        "year": str(year) if year else None,
        "author": authors[:2],
        "genres": subjects,
        "geo": geo,
        "poster_url": COVER_URL.format(cover_id=cover_id) if cover_id else None,
    }
