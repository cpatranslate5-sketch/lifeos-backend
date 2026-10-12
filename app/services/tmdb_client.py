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


# Жанры TMDB (ru-RU) → жанры приложения (как в фильтрах Life OS).
_GENRE_MAP = {
    "боевик": ["Боевик"], "вестерн": ["Вестерн"], "военный": ["Военный"], "детектив": ["Детектив"],
    "драма": ["Драма"], "история": ["История"], "комедия": ["Комедия"], "криминал": ["Криминал"],
    "мелодрама": ["Мелодрама"], "музыка": ["Мюзикл"], "мультфильм": ["Мультфильм"],
    "приключения": ["Приключения"], "семейный": ["Семейный"], "триллер": ["Триллер"],
    "ужасы": ["Ужасы/Мистика"], "фантастика": ["Фантастика"], "фэнтези": ["Фэнтези"],
    "боевик и приключения": ["Боевик", "Приключения"], "нф и фэнтези": ["Фантастика", "Фэнтези"],
    "война и политика": ["Военный"],
}


def _map_genres(names: list) -> list:
    out = []
    for n in names:
        for g in _GENRE_MAP.get(str(n).strip().lower(), []):
            if g not in out:
                out.append(g)
    return out


async def _get(path: str, params: dict) -> dict:
    params = {**params, "api_key": settings.TMDB_API_KEY, "language": "ru-RU"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(f"{BASE_URL}{path}", params=params)
        resp.raise_for_status()
        return resp.json()


def _pick(results: list, year, date_key: str) -> dict:
    """Из результатов поиска берём тот, чей год совпадает с годом карточки
    (±1), — иначе по названию легко попасть в ремейк или другой фильм."""
    try:
        y = int(str(year)[:4]) if year else None
    except ValueError:
        y = None
    if y:
        for r in results:
            d = (r.get(date_key) or "")[:4]
            if d.isdigit() and abs(int(d) - y) <= 1:
                return r
    return results[0]


async def find_movie(title: str, year=None) -> dict | None:
    """Search TMDB for a movie by title, returning the best match's details
    (or None if nothing found)."""
    search = await _get("/search/movie", {"query": title})
    results = search.get("results") or []
    if not results:
        return None
    movie_id = _pick(results, year, "release_date")["id"]
    details = await _get(f"/movie/{movie_id}", {"append_to_response": "credits"})
    return _extract_movie_fields(details)


async def find_tv(title: str, year=None) -> dict | None:
    """Same as find_movie, but for TV shows."""
    search = await _get("/search/tv", {"query": title})
    results = search.get("results") or []
    if not results:
        return None
    tv_id = _pick(results, year, "first_air_date")["id"]
    details = await _get(f"/tv/{tv_id}", {"append_to_response": "credits"})
    return _extract_tv_fields(details)


def _extract_movie_fields(details: dict) -> dict:
    year = (details.get("release_date") or "")[:4]
    crew = (details.get("credits") or {}).get("crew") or []
    director = next((p["name"] for p in crew if p.get("job") == "Director"), None)
    cast = (details.get("credits") or {}).get("cast") or []
    actors = [p["name"] for p in cast[:5]]
    genres = _map_genres([g["name"] for g in details.get("genres") or []])
    countries = details.get("production_countries") or []
    geo = geo_buckets(countries)
    poster_path = details.get("poster_path")
    runtime = details.get("runtime") or None
    return {
        "year": year or None,
        "duration_min": int(runtime) if runtime else None,
        "author": [director] if director else [],  # "Режиссёр" field for movies
        "actors": actors,
        "genres": genres,
        "geo": geo,
        "poster_url": f"{IMAGE_BASE}{poster_path}" if poster_path else None,
    }


def _tv_total_minutes(details: dict) -> int | None:
    """Сколько минут займут ВСЕ вышедшие серии: число серий × длина серии.
    TMDB не всегда заполняет длину серии, поэтому берём то, что есть."""
    episodes = details.get("number_of_episodes") or 0
    run_times = [r for r in (details.get("episode_run_time") or []) if r]
    ep_len = sum(run_times) / len(run_times) if run_times else None
    if not ep_len:
        ep_len = ((details.get("last_episode_to_air") or {}).get("runtime")
                  or (details.get("next_episode_to_air") or {}).get("runtime"))
    if not episodes or not ep_len:
        return None
    return int(round(episodes * ep_len))


def _released_seasons(details: dict) -> int | None:
    """Сколько сезонов уже вышло (без «спецвыпусков» — сезона 0)."""
    seasons = [s for s in (details.get("seasons") or []) if (s.get("season_number") or 0) > 0 and (s.get("episode_count") or 0) > 0]
    if seasons:
        return len(seasons)
    return details.get("number_of_seasons") or None


def _extract_tv_fields(details: dict) -> dict:
    year = (details.get("first_air_date") or "")[:4]
    creators = details.get("created_by") or []
    director = creators[0]["name"] if creators else None
    cast = (details.get("credits") or {}).get("cast") or []
    actors = [p["name"] for p in cast[:5]]
    genres = _map_genres([g["name"] for g in details.get("genres") or []])
    countries = details.get("production_countries") or []
    geo = geo_buckets(countries)
    poster_path = details.get("poster_path")
    return {
        "year": year or None,
        "duration_min": _tv_total_minutes(details),
        # Сезоны: сколько уже вышло и выходит ли сериал дальше —
        # чтобы учитывать будущие сезоны в расчёте времени.
        "seasons_released": _released_seasons(details),
        "show_ongoing": (details.get("status") in ("Returning Series", "In Production", "Planned")) or bool(details.get("in_production")),
        "author": [director] if director else [],
        "actors": actors,
        "genres": genres,
        "geo": geo,
        "poster_url": f"{IMAGE_BASE}{poster_path}" if poster_path else None,
    }
