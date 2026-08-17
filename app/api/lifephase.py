from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Query
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
def get_lifephase(profile: str = Query(default="nemalenkiy"), db: Session = Depends(get_db)):
    e = db.query(Entity).filter(Entity.type == "lifephase", Entity.is_active == True,  # noqa: E712
                                 Entity.profile == profile).first()
    return e.attributes if e else None


@router.put("/lifephase")
def set_lifephase(payload: LifephaseIn, profile: str = Query(default="nemalenkiy"), db: Session = Depends(get_db)):
    e = db.query(Entity).filter(Entity.type == "lifephase", Entity.is_active == True,  # noqa: E712
                                 Entity.profile == profile).first()
    attrs = payload.dict()
    if e:
        e.attributes = attrs
        e.updated_at = now()
    else:
        e = Entity(type="lifephase", name="Текущий фокус", attributes=attrs, is_active=True, profile=profile)
        db.add(e)
    db.commit()
    return attrs


@router.get("/reflection")
def get_reflection(profile: str = Query(default="nemalenkiy"), db: Session = Depends(get_db)):
    week_ago = now() - timedelta(days=7)
    entities = {e.id: e for e in db.query(Entity).filter(Entity.is_active == True, Entity.space == "life",  # noqa: E712
                                                           Entity.profile == profile).all()}
    recent_changes = db.query(ChangeLogEntry).filter(ChangeLogEntry.timestamp > week_ago).all()

    by_type: dict[str, int] = {}
    changed_entity_ids = set()
    for c in recent_changes:
        ent = entities.get(c.entity_id)
        if not ent:
            continue
        by_type[ent.type] = by_type.get(ent.type, 0) + 1
        changed_entity_ids.add(c.entity_id)

    stalled = [e.name for e in entities.values()
               if e.type in ("project", "goal") and e.id not in changed_entity_ids]

    return {"activity_by_type": by_type, "stalled": stalled}
