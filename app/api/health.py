from datetime import datetime, timezone

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health():
    """
    No auth required — used to verify the app is up and reachable,
    including from an external network/monitoring check.
    """
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}
