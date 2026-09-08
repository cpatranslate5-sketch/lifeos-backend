"""
Thin client for Open Library — used to auto-fill book card metadata
(year, author, genres, country/language, cover). Free, no API key needed.
"""
import httpx

SEARCH_URL = "https://openlibrary.org/search.json"
COVER_URL = "https://covers.openlibrary.org/b/id/{cover_id}-L.jpg"

LANGUAGE_TO_COUNTRY = {
    "rus": "Россия", "eng": "США", "fre": "Франция", "ger": "Германия",
    "spa": "Испания", "ita": "Италия", "jpn": "Япония", "chi": "Китай",
    "por": "Португалия", "pol": "Польша", "swe": "Швеция", "ukr": "Украина",
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
    geo = next((LANGUAGE_TO_COUNTRY[lang] for lang in languages if lang in LANGUAGE_TO_COUNTRY), None)
    cover_id = doc.get("cover_i")

    return {
        "year": str(year) if year else None,
        "author": authors[:2],
        "genres": subjects,
        "geo": geo,
        "poster_url": COVER_URL.format(cover_id=cover_id) if cover_id else None,
    }
