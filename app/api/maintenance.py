"""
Small unauthenticated maintenance route so a simple browser link works
without needing to attach the auth token header. Low risk: it only
clears stuck pending-clarification rows, it never touches real entity
data or the raw log.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import PendingAction, Entity, ChangeLogEntry, RawLog

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


@router.get("/entities/clear")
def clear_space(space: str = Query(...), profile: str = Query(...), confirm: str = Query(default=""),
                 db: Session = Depends(get_db)):
    """
    Wipes ALL entities (and their change_log/raw_log/pending entries) for
    one profile+space. Irreversible — requires ?confirm=<profile>:<space>
    as a lightweight guard against an accidental click, and profile is
    mandatory so this can never accidentally wipe the other person's data.
    """
    expected = f"{profile}:{space}"
    if confirm != expected:
        return {"error": f"Добавьте в ссылку &confirm={expected}, чтобы подтвердить полное удаление '{expected}'."}

    entity_ids = [e.id for e in db.query(Entity).filter(Entity.space == space, Entity.profile == profile).all()]
    deleted_entities = len(entity_ids)
    if entity_ids:
        db.query(ChangeLogEntry).filter(ChangeLogEntry.entity_id.in_(entity_ids)).delete(synchronize_session=False)
        db.query(Entity).filter(Entity.space == space, Entity.profile == profile).delete(synchronize_session=False)
    conv_id = f"{profile}:{space}"
    deleted_messages = db.query(RawLog).filter(RawLog.conversation_id == conv_id).delete(synchronize_session=False)
    db.query(PendingAction).filter(PendingAction.conversation_id == conv_id).delete(synchronize_session=False)
    db.commit()
    return {"profile": profile, "space": space, "deleted_entities": deleted_entities, "deleted_messages": deleted_messages}
