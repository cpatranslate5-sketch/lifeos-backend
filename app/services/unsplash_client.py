"""
Thin client for Unsplash — used to auto-attach an illustrative photo to
task/event/leisure/habit cards by name, when no cover was set manually.
Free API, 50 requests/hour on the demo tier (plenty for normal use).
"""
import httpx

from app.config import settings

SEARCH_URL = "https://api.unsplash.com/search/photos"


async def find_image(query: str) -> str | None:
    if not settings.UNSPLASH_ACCESS_KEY or not query.strip():
        return None
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            resp = await client.get(
                SEARCH_URL,
                params={"query": query, "per_page": 1, "orientation": "portrait"},
                headers={"Authorization": f"Client-ID {settings.UNSPLASH_ACCESS_KEY}"},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return None

    results = data.get("results") or []
    if not results:
        return None
    return results[0]["urls"]["small"]
