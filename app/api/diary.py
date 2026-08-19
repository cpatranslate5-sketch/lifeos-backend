import io
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
import openpyxl

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
        if len(content) > 15 * 1024 * 1024:
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
async def add_photos_to_entry(entry_id: str, files: list[UploadFile] = File(default=[]), db: Session = Depends(get_db)):
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


@router.post("/diary/import")
async def import_diary_excel(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """
    Bulk-import from the old spreadsheet format: column A = date, columns
    B-G = one cell per НеМаленький entry that day, columns H onward = one
    cell per Котёнок entry that day. When a day has more entries than fit
    in its 6 columns, the overflow continues on the next row in the same
    columns, with column A left blank there — we treat any blank-date row
    as still belonging to the most recent dated row above it.
    """
    content = await file.read()
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    except Exception as e:
        raise HTTPException(400, f"Не удалось прочитать файл: {e}")

    ws = wb.worksheets[0]
    created = 0
    skipped_duplicates = 0
    current_date = None

    # Pre-fetch existing entries as a dedup guard, so re-running the import
    # (e.g. after adding more rows to the spreadsheet) doesn't duplicate.
    existing = {(e.profile, e.date, e.text) for e in db.query(DiaryEntry).all()}

    for row in ws.iter_rows(min_row=3):
        date_cell = row[0].value
        if isinstance(date_cell, datetime):
            current_date = date_cell.strftime("%Y-%m-%d")
        if current_date is None:
            continue

        for cell in row[1:7]:  # columns B-G
            v = cell.value
            if isinstance(v, str) and v.strip():
                key = ("nemalenkiy", current_date, v.strip())
                if key in existing:
                    skipped_duplicates += 1
                    continue
                db.add(DiaryEntry(profile="nemalenkiy", date=current_date, text=v.strip(), photo_paths=[]))
                existing.add(key)
                created += 1

        for cell in row[7:]:  # columns H onward, no fixed upper bound
            v = cell.value
            if isinstance(v, str) and v.strip():
                key = ("kotyonok", current_date, v.strip())
                if key in existing:
                    skipped_duplicates += 1
                    continue
                db.add(DiaryEntry(profile="kotyonok", date=current_date, text=v.strip(), photo_paths=[]))
                existing.add(key)
                created += 1

    db.commit()
    return {"created": created, "skipped_duplicates": skipped_duplicates}


@public_router.get("/diary/photo/{filename}")
def get_diary_photo(filename: str):
    fpath = os.path.join(PHOTOS_DIR, filename)
    if not os.path.exists(fpath) or ".." in filename:
        raise HTTPException(404, "Photo not found")
    return FileResponse(fpath)
