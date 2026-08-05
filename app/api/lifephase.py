from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.db import get_db
from app.models import Entity, ChangeLogEntry

router = APIRouter(dependencies=[Depends(require_auth)])


def now():
    return datetime.now(timezone.utc)


class LifephaseIn(BaseModel):
    focus: str = ""
    priorities: list[str] = []
    constraints: list[str] = []


@router.get("/lifephase")
def get_lifephase(db: Session = Depends(get_db)):
    e = db.query(Entity).filter(Entity.type == "lifephase", Entity.is_active == True).first()  # noqa: E712
    return e.attributes if e else None


@router.put("/lifephase")
def set_lifephase(payload: LifephaseIn, db: Session = Depends(get_db)):
    e = db.query(Entity).filter(Entity.type == "lifephase", Entity.is_active == True).first()  # noqa: E712
    attrs = payload.dict()
    if e:
        e.attributes = attrs
        e.updated_at = now()
    else:
        e = Entity(type="lifephase", name="Текущий фокус", attributes=attrs, is_active=True)
        db.add(e)
    db.commit()
    return attrs


@router.get("/reflection")
def get_reflection(db: Session = Depends(get_db)):
    week_ago = now() - timedelta(days=7)
    entities = {e.id: e for e in db.query(Entity).filter(Entity.is_active == True, Entity.space == "life").all()}  # noqa: E712
    recent_changes = db.query(ChangeLogEntry).filter(ChangeLogEntry.timestamp > week_ago).all()

    by_type: dict[str, int] = {}
    changed_entity_ids = set()
    for c in recent_changes:
        ent = entities.get(c.entity_id)
        t = ent.type if ent else "other"
        by_type[t] = by_type.get(t, 0) + 1
        if c.entity_id:
            changed_entity_ids.add(c.entity_id)

    stalled = [e.name for e in entities.values()
               if e.type in ("project", "goal") and e.id not in changed_entity_ids]

    return {"activity_by_type": by_type, "stalled": stalled}
