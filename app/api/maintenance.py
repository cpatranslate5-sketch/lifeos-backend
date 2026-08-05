"""
Small unauthenticated maintenance route so a simple browser link works
without needing to attach the auth token header. Low risk: it only
clears stuck pending-clarification rows, it never touches real entity
data or the raw log.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import PendingAction

router = APIRouter()


@router.get("/pending/clear")
def clear_pending(conversation_id: str = Query(default="default"), db: Session = Depends(get_db)):
    rows = (db.query(PendingAction)
            .filter(PendingAction.conversation_id == conversation_id, PendingAction.resolved == False)  # noqa: E712
            .all())
    count = len(rows)
    for r in rows:
        r.resolved = True
    db.commit()
    return {"cleared": count, "conversation_id": conversation_id}
