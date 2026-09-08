"""
Thin client for The Movie Database (TMDB) API — used to auto-fill movie/show
card metadata (year, director, cast, genres, country, poster) from just a
title. Free API, no cost, standard rate limits only.
"""
import httpx

from app.config import settings

BASE_URL = "https://api.themoviedb.org/3"
IMAGE_BASE = "https://image.tmdb.org/t/p/w500"


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
    geo = countries[0]["name"] if countries else None
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
    geo = countries[0]["name"] if countries else None
    poster_path = details.get("poster_path")
    return {
        "year": year or None,
        "author": [director] if director else [],
        "actors": actors,
        "genres": genres,
        "geo": geo,
        "poster_url": f"{IMAGE_BASE}{poster_path}" if poster_path else None,
    }
