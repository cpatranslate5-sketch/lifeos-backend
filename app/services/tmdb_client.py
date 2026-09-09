"""
Thin client for The Movie Database (TMDB) API — used to auto-fill movie/show
card metadata (year, director, cast, genres, country, poster) from just a
title. Free API, no cost, standard rate limits only.
"""
import httpx

from app.config import settings

BASE_URL = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/w500"

# Our app only uses these five broad geo buckets (see GEO_OPTIONS in
# types.ts) — map TMDB's specific ISO country codes into them, rather than
# passing through TMDB's raw English country name (which wouldn't match
# our filter options at all, e.g. "United States of America").
EUROPE = {
    "GB", "FR", "DE", "IT", "ES", "NL", "SE", "NO", "DK", "FI", "PL", "PT",
    "IE", "BE", "AT", "CH", "GR", "CZ", "HU", "RO", "BG", "UA", "BY", "HR",
    "RS", "SK", "SI", "LT", "LV", "EE", "IS", "LU", "MT", "CY", "AL", "MK",
}
ASIA = {
    "JP", "KR", "CN", "IN", "TH", "VN", "ID", "PH", "MY", "SG", "HK", "TW",
    "IL", "TR", "AE", "SA", "IR", "IQ", "PK", "BD", "KZ", "GE", "AM", "AZ",
}
AMERICA = {
    "US", "CA", "MX", "BR", "AR", "CL", "CO", "PE", "VE", "CU",
}


def geo_bucket(iso_code: str | None) -> str | None:
    if not iso_code:
        return None
    if iso_code == "RU":
        return "Россия"
    if iso_code in EUROPE:
        return "Европа"
    if iso_code in ASIA:
        return "Азия"
    if iso_code in AMERICA:
        return "Америка"
    return "Остальное"


def geo_buckets(countries: list[dict]) -> list[str]:
    buckets: list[str] = []
    for c in countries:
        b = geo_bucket(c.get("iso_3166_1"))
        if b and b not in buckets:
            buckets.append(b)
    return buckets


async def _get(path: str, params: dict) -> dict:
    params = {**params, "api_key": settings.TMDB_API_KEY, "language": "ru-RU"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(f"{BASE_URL}{path}", params=params)
        resp.raise_for_status()
        return resp.json()


async def find_movie(title: str) -> dict | None:
    """Search TMDB for a movie by title, returning the best match's details
    (or None if nothing found)."""
    search = await _get("/search/movie", {"query": title})
    results = search.get("results") or []
    if not results:
        return None
    movie_id = results[0]["id"]
    details = await _get(f"/movie/{movie_id}", {"append_to_response": "credits"})
    return _extract_movie_fields(details)


async def find_tv(title: str) -> dict | None:
    """Same as find_movie, but for TV shows."""
    search = await _get("/search/tv", {"query": title})
    results = search.get("results") or []
    if not results:
        return None
    tv_id = results[0]["id"]
    details = await _get(f"/tv/{tv_id}", {"append_to_response": "credits"})
    return _extract_tv_fields(details)


def _extract_movie_fields(details: dict) -> dict:
    year = (details.get("release_date") or "")[:4]
    crew = (details.get("credits") or {}).get("crew") or []
    director = next((p["name"] for p in crew if p.get("job") == "Director"), None)
    cast = (details.get("credits") or {}).get("cast") or []
    actors = [p["name"] for p in cast[:5]]
    genres = [g["name"] for g in details.get("genres") or []]
    countries = details.get("production_countries") or []
    geo = geo_buckets(countries)
    poster_path = details.get("poster_path")
    return {
        "year": year or None,
        "author": [director] if director else [],  # "Режиссёр" field for movies
        "actors": actors,
        "genres": genres,
        "geo": geo,
        "poster_url": f"{IMAGE_BASE}{poster_path}" if poster_path else None,
    }


def _extract_tv_fields(details: dict) -> dict:
    year = (details.get("first_air_date") or "")[:4]
    creators = details.get("created_by") or []
    director = creators[0]["name"] if creators else None
    cast = (details.get("credits") or {}).get("cast") or []
    actors = [p["name"] for p in cast[:5]]
    genres = [g["name"] for g in details.get("genres") or []]
    countries = details.get("production_countries") or []
    geo = geo_buckets(countries)
    poster_path = details.get("poster_path")
    return {
        "year": year or None,
        "author": [director] if director else [],
        "actors": actors,
        "genres": genres,
        "geo": geo,
        "poster_url": f"{IMAGE_BASE}{poster_path}" if poster_path else None,
    }
