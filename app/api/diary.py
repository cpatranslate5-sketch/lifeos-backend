import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.db import get_db
from app.models import DiaryEntry

router = APIRouter(dependencies=[Depends(require_auth)])
public_router = APIRouter()  # photo serving only — <img> tags can't send auth headers

PHOTOS_DIR = "/app/data/photos"
os.makedirs(PHOTOS_DIR, exist_ok=True)
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".gif"}


class DiaryEntryOut(BaseModel):
    id: str
    profile: str
    date: str
    text: str
    photo_paths: list[str]
    created_at: datetime

    class Config:
        from_attributes = True


def _save_photos(files: list[UploadFile]) -> list[str]:
    saved_paths = []
    for f in files:
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext not in ALLOWED_EXT:
            continue
        fname = f"{uuid.uuid4()}{ext}"
        fpath = os.path.join(PHOTOS_DIR, fname)
        saved_paths.append((fname, fpath, f))
    return saved_paths


@router.get("/diary", response_model=list[DiaryEntryOut])
def list_diary(date: str = Query(...), profile: str | None = Query(default=None), db: Session = Depends(get_db)):
    q = db.query(DiaryEntry).filter(DiaryEntry.date == date)
    if profile:
        q = q.filter(DiaryEntry.profile == profile)
    rows = q.order_by(DiaryEntry.created_at.asc()).all()
    return [DiaryEntryOut.model_validate(r) for r in rows]


@router.post("/diary", response_model=DiaryEntryOut)
async def create_diary_entry(
    date: str = Form(...),
    profile: str = Form(...),
    text: str = Form(...),
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
):
    saved_paths = []
    for f in files:
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext not in ALLOWED_EXT:
            continue
        fname = f"{uuid.uuid4()}{ext}"
        fpath = os.path.join(PHOTOS_DIR, fname)
        content = await f.read()
        if len(content) > 15 * 1024 * 1024:  # 15MB cap per photo
            continue
        with open(fpath, "wb") as out:
            out.write(content)
        saved_paths.append(fname)

    entry = DiaryEntry(profile=profile, date=date, text=text, photo_paths=saved_paths)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return DiaryEntryOut.model_validate(entry)


@router.post("/diary/{entry_id}/photos", response_model=DiaryEntryOut)
async def add_photos_to_entry(
    entry_id: str,
    files: list[UploadFile] = File(default=[]),
    db: Session = Depends(get_db),
):
    """
    Attach one or more photos to an already-existing entry — for adding
    memories you find/receive later, without needing to redo the whole entry.
    """
    entry = db.get(DiaryEntry, entry_id)
    if not entry:
        raise HTTPException(404, "Entry not found")

    new_paths = list(entry.photo_paths or [])
    for f in files:
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext not in ALLOWED_EXT:
            continue
        fname = f"{uuid.uuid4()}{ext}"
        fpath = os.path.join(PHOTOS_DIR, fname)
        content = await f.read()
        if len(content) > 15 * 1024 * 1024:
            continue
        with open(fpath, "wb") as out:
            out.write(content)
        new_paths.append(fname)

    entry.photo_paths = new_paths
    db.commit()
    db.refresh(entry)
    return DiaryEntryOut.model_validate(entry)


@router.delete("/diary/{entry_id}")
def delete_diary_entry(entry_id: str, db: Session = Depends(get_db)):
    entry = db.get(DiaryEntry, entry_id)
    if not entry:
        raise HTTPException(404, "Entry not found")
    for fname in (entry.photo_paths or []):
        fpath = os.path.join(PHOTOS_DIR, fname)
        if os.path.exists(fpath):
            os.remove(fpath)
    db.delete(entry)
    db.commit()
    return {"deleted": True}


@public_router.get("/diary/photo/{filename}")
def get_diary_photo(filename: str):
    fpath = os.path.join(PHOTOS_DIR, filename)
    if not os.path.exists(fpath) or ".." in filename:
        raise HTTPException(404, "Photo not found")
    return FileResponse(fpath)
