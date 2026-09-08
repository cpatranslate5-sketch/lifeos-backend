import os
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, Query, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.config import settings
from app.db import get_db
from app.models import Entity, ChangeLogEntry
from app.services import tmdb_client, openlibrary_client

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


class MarkSportIn(BaseModel):
    keyword: str
    profile: str
    space: str = "life"


@router.post("/entities/mark-sport")
def mark_sport(payload: MarkSportIn, db: Session = Depends(get_db)):
    keyword = payload.keyword.strip().lower()
    if not keyword:
        raise HTTPException(400, "Пустой текст для поиска")

    matches = (db.query(Entity)
               .filter(Entity.profile == payload.profile, Entity.space == payload.space,
                       Entity.type == "event", Entity.is_active == True)  # noqa: E712
               .all())
    updated = 0
    for e in matches:
        if keyword in e.name.lower():
            e.attributes = {**(e.attributes or {}), "category": "sport"}
            e.updated_at = now()
            updated += 1
    db.commit()
    return {"updated": updated}


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


async def _fetch_enrichment(e: Entity) -> dict | None:
    """Looks up external metadata for one entity, based on its type."""
    if e.type == "movie":
        return await tmdb_client.find_movie(e.name)
    if e.type == "show":
        return await tmdb_client.find_tv(e.name)
    if e.type == "book":
        return await openlibrary_client.find_book(e.name)
    return None


async def _apply_enrichment(e: Entity, data: dict) -> None:
    """Merges fetched metadata into an entity's attributes and downloads
    the poster/cover image, if any."""
    new_attrs = dict(e.attributes or {})
    if data.get("year"):
        new_attrs["year"] = data["year"]
    if data.get("author"):
        new_attrs["author"] = data["author"]
    if data.get("actors"):
        new_attrs["actors"] = data["actors"]
    if data.get("genres"):
        new_attrs["genres"] = data["genres"]
    if data.get("geo"):
        new_attrs["geo"] = data["geo"]

    if data.get("poster_url") and not new_attrs.get("cover_path"):
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                img_resp = await client.get(data["poster_url"])
            if img_resp.status_code == 200:
                fname = f"{uuid.uuid4()}.jpg"
                with open(os.path.join(COVERS_DIR, fname), "wb") as out:
                    out.write(img_resp.content)
                new_attrs["cover_path"] = fname
        except Exception:
            pass  # poster is a nice-to-have — don't fail the whole entity over it

    e.attributes = new_attrs
    e.updated_at = now()


class EnrichTmdbIn(BaseModel):
    profile: str
    type: str
    space: str = "life"


@router.post("/entities/enrich-tmdb")
async def enrich_tmdb(payload: EnrichTmdbIn, db: Session = Depends(get_db)):
    """
    Auto-fills year/author-director/actors/genres/country/cover for movie,
    show, or book cards (whichever one type is passed in) that only have a
    title so far (i.e. no genres set yet). Cards that already have genres
    are left untouched, so manual edits are never overwritten by a re-run.
    """
    if payload.type not in ("movie", "show", "book"):
        raise HTTPException(400, "Unsupported type")

    candidates = (db.query(Entity)
                  .filter(Entity.profile == payload.profile, Entity.space == payload.space,
                          Entity.type == payload.type, Entity.is_active == True)  # noqa: E712
                  .all())
    to_enrich = [e for e in candidates if not (e.attributes or {}).get("genres")]

    enriched = 0
    not_found: list[str] = []

    for e in to_enrich:
        if e.type in ("movie", "show") and not settings.TMDB_API_KEY:
            not_found.append(e.name)
            continue
        try:
            data = await _fetch_enrichment(e)
        except Exception:
            data = None

        if not data:
            not_found.append(e.name)
            continue

        await _apply_enrichment(e, data)
        enriched += 1

    db.commit()
    return {"enriched": enriched, "total_candidates": len(to_enrich), "not_found": not_found}


@router.post("/entities/{entity_id}/enrich", response_model=EntityOut)
async def enrich_single_entity(entity_id: str, db: Session = Depends(get_db)):
    """Same idea as /entities/enrich-tmdb, but for exactly one card —
    used by the "Заполнить карточку автоматически" button on an individual
    card. Works even if the card already has some fields set (a manual
    retry is an explicit, deliberate action, unlike the bulk pass)."""
    e = db.get(Entity, entity_id)
    if not e:
        raise HTTPException(404, "Entity not found")
    if e.type in ("movie", "show") and not settings.TMDB_API_KEY:
        raise HTTPException(400, "TMDB_API_KEY не настроен на сервере")

    data = await _fetch_enrichment(e)
    if not data:
        raise HTTPException(404, "Ничего не найдено по этому названию")

    await _apply_enrichment(e, data)
    db.commit()
    db.refresh(e)
    return EntityOut.model_validate(e)


@public_router.get("/entities/cover/{filename}")
def get_cover(filename: str):
    fpath = os.path.join(COVERS_DIR, filename)
    if not os.path.exists(fpath) or ".." in filename:
        raise HTTPException(404, "Cover not found")
    return FileResponse(fpath)
