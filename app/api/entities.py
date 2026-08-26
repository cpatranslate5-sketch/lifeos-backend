import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.db import get_db
from app.models import Entity, ChangeLogEntry

router = APIRouter(dependencies=[Depends(require_auth)])
public_router = APIRouter()  # cover image serving only — <img> tags can't send auth headers

COVERS_DIR = "/app/data/covers"
os.makedirs(COVERS_DIR, exist_ok=True)
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def now():
    return datetime.now(timezone.utc)


class EntityOut(BaseModel):
    id: str
    type: str
    name: str
    profile: str
    space: str
    attributes: dict
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class EntityCreate(BaseModel):
    type: str
    name: str
    profile: str = "nemalenkiy"
    space: str = "life"
    attributes: dict = {}


class EntityFieldUpdate(BaseModel):
    key: str
    value: object


class EntityRename(BaseModel):
    name: str


class BulkDelete(BaseModel):
    ids: list[str]


@router.get("/entities", response_model=list[EntityOut])
def list_entities(type: str | None = Query(default=None), space: str | None = Query(default=None),
                   profile: str | None = Query(default=None), db: Session = Depends(get_db)):
    q = db.query(Entity).filter(Entity.is_active == True)  # noqa: E712
    if type:
        q = q.filter(Entity.type == type)
    if space:
        q = q.filter(Entity.space == space)
    if profile:
        q = q.filter(Entity.profile == profile)
    return [EntityOut.model_validate(e) for e in q.order_by(Entity.created_at.asc()).all()]


@router.post("/entities", response_model=EntityOut)
def create_entity(payload: EntityCreate, db: Session = Depends(get_db)):
    e = Entity(type=payload.type, name=payload.name, space=payload.space, profile=payload.profile,
               attributes=payload.attributes, is_active=True)
    db.add(e)
    db.flush()
    db.add(ChangeLogEntry(entity_id=e.id, field="created", value=payload.attributes, confidence=1.0,
                           decision_trace={"action": "manual_add",
                                           "factors": ["добавлено вручную через интерфейс"], "confidence": 1.0}))
    db.commit()
    db.refresh(e)
    return EntityOut.model_validate(e)


@router.patch("/entities/{entity_id}/field", response_model=EntityOut)
def update_entity_field(entity_id: str, payload: EntityFieldUpdate, db: Session = Depends(get_db)):
    e = db.get(Entity, entity_id)
    if not e:
        raise HTTPException(404, "Entity not found")
    e.attributes = {**(e.attributes or {}), payload.key: payload.value}
    e.updated_at = now()
    db.add(ChangeLogEntry(entity_id=e.id, field=payload.key, value=payload.value, confidence=1.0,
                           decision_trace={"action": "manual_field_update",
                                           "factors": [f"пользователь вручную изменил поле {payload.key}"],
                                           "confidence": 1.0}))
    db.commit()
    db.refresh(e)
    return EntityOut.model_validate(e)


@router.patch("/entities/{entity_id}/rename", response_model=EntityOut)
def rename_entity(entity_id: str, payload: EntityRename, db: Session = Depends(get_db)):
    e = db.get(Entity, entity_id)
    if not e:
        raise HTTPException(404, "Entity not found")
    old_name = e.name
    e.name = payload.name
    e.updated_at = now()
    db.add(ChangeLogEntry(entity_id=e.id, field="name", value=payload.name, confidence=1.0,
                           decision_trace={"action": "manual_rename",
                                           "factors": [f'пользователь переименовал вручную: "{old_name}" → "{payload.name}"'],
                                           "confidence": 1.0}))
    db.commit()
    db.refresh(e)
    return EntityOut.model_validate(e)


@router.post("/entities/bulk-delete")
def bulk_delete_entities(payload: BulkDelete, db: Session = Depends(get_db)):
    deleted = 0
    for entity_id in payload.ids:
        e = db.get(Entity, entity_id)
        if not e:
            continue
        db.add(ChangeLogEntry(entity_id=entity_id, field="deleted", value={"name": e.name, "type": e.type},
                               confidence=1.0,
                               decision_trace={"action": "manual_bulk_delete",
                                               "factors": [f'пользователь удалил массово: "{e.name}"'],
                                               "confidence": 1.0}))
        db.delete(e)
        deleted += 1
    db.commit()
    return {"deleted": deleted}


@router.delete("/entities/{entity_id}")
def delete_entity(entity_id: str, db: Session = Depends(get_db)):
    e = db.get(Entity, entity_id)
    if not e:
        raise HTTPException(404, "Entity not found")
    for fname in [e.attributes.get("cover_path")] if e.attributes else []:
        if fname:
            fpath = os.path.join(COVERS_DIR, fname)
            if os.path.exists(fpath):
                os.remove(fpath)
    db.add(ChangeLogEntry(entity_id=entity_id, field="deleted", value={"name": e.name, "type": e.type},
                           confidence=1.0,
                           decision_trace={"action": "manual_delete",
                                           "factors": [f'пользователь удалил вручную: "{e.name}"'],
                                           "confidence": 1.0}))
    db.delete(e)
    db.commit()
    return {"deleted": True}


@router.get("/entities/{entity_id}/history")
def entity_history(entity_id: str, db: Session = Depends(get_db)):
    rows = (db.query(ChangeLogEntry).filter(ChangeLogEntry.entity_id == entity_id)
            .order_by(ChangeLogEntry.timestamp.desc()).limit(10).all())
    return [{"field": r.field, "confidence": r.confidence, "decision_trace": r.decision_trace,
             "timestamp": r.timestamp} for r in rows]


@router.post("/entities/{entity_id}/cover", response_model=EntityOut)
async def upload_cover(entity_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    e = db.get(Entity, entity_id)
    if not e:
        raise HTTPException(404, "Entity not found")
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, "Unsupported image type")
    content = await file.read()
    if len(content) > 8 * 1024 * 1024:
        raise HTTPException(400, "Файл слишком большой (максимум 8 МБ)")

    old_cover = (e.attributes or {}).get("cover_path")
    fname = f"{uuid.uuid4()}{ext}"
    with open(os.path.join(COVERS_DIR, fname), "wb") as out:
        out.write(content)
    if old_cover:
        old_path = os.path.join(COVERS_DIR, old_cover)
        if os.path.exists(old_path):
            os.remove(old_path)

    e.attributes = {**(e.attributes or {}), "cover_path": fname}
    e.updated_at = now()
    db.commit()
    db.refresh(e)
    return EntityOut.model_validate(e)


class PropagateCoverIn(BaseModel):
    source_entity_id: str
    keyword: str


@router.post("/entities/propagate-cover")
def propagate_cover(payload: PropagateCoverIn, db: Session = Depends(get_db)):
    source = db.get(Entity, payload.source_entity_id)
    if not source:
        raise HTTPException(404, "Entity not found")
    cover_path = (source.attributes or {}).get("cover_path")
    if not cover_path:
        raise HTTPException(400, "У исходной карточки нет обложки")

    keyword = payload.keyword.strip().lower()
    if not keyword:
        raise HTTPException(400, "Пустой текст для поиска")

    matches = (db.query(Entity)
               .filter(Entity.profile == source.profile, Entity.type == source.type,
                       Entity.is_active == True, Entity.id != source.id)  # noqa: E712
               .all())
    updated = 0
    for e in matches:
        if keyword in e.name.lower():
            e.attributes = {**(e.attributes or {}), "cover_path": cover_path}
            e.updated_at = now()
            updated += 1
    db.commit()
    return {"updated": updated}


@public_router.get("/entities/cover/{filename}")
def get_cover(filename: str):
    fpath = os.path.join(COVERS_DIR, filename)
    if not os.path.exists(fpath) or ".." in filename:
        raise HTTPException(404, "Cover not found")
    return FileResponse(fpath)
