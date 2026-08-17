import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import text

from app.config import settings
from app.db import Base, engine, SessionLocal
from app.models import Entity
from app.api import health, messages, export, entities, lifephase, maintenance, diary

Base.metadata.create_all(bind=engine)

# Lightweight migration: add columns that predate this version of the schema.
# SQLite's CREATE TABLE-based create_all() won't alter existing tables.
with engine.connect() as conn:
    cols = [row[1] for row in conn.execute(text("PRAGMA table_info(entities)"))]
    if "space" not in cols:
        conn.execute(text("ALTER TABLE entities ADD COLUMN space TEXT DEFAULT 'life'"))
        conn.commit()
    if "profile" not in cols:
        conn.execute(text("ALTER TABLE entities ADD COLUMN profile TEXT DEFAULT 'nemalenkiy'"))
        conn.commit()

# Folder for uploaded diary photos, alongside the SQLite file on the
# persistent Railway volume so they survive redeploys.
PHOTOS_DIR = os.path.join(os.path.dirname(settings.DATABASE_URL.replace("sqlite:///", "")), "photos") \
    if "sqlite" in settings.DATABASE_URL else "/app/data/photos"
os.makedirs(PHOTOS_DIR, exist_ok=True)

# Seed the known recurring weekly schedule once, as Habit entities —
# only for the "nemalenkiy" profile, since Kotyonok's folder starts empty.
# weekday: 0=Mon..6=Sun (Python's date.weekday() convention).
RECURRING_SCHEDULE_SEED = [
    {"name": "Бокс (групповая)", "weekday": 0, "time": "21:00"},
    {"name": "Гибкость", "weekday": 1, "time": "14:00"},
    {"name": "Бокс (индивидуальная)", "weekday": 2, "time": "19:00"},
    {"name": "Теннис", "weekday": 3, "time": "19:30"},
    {"name": "Теннис (иногда)", "weekday": 6, "time": None, "irregular": True},
]


def seed_habits():
    db: Session = SessionLocal()
    try:
        existing = db.query(Entity).filter(Entity.type == "habit", Entity.profile == "nemalenkiy").first()
        if existing:
            return
        for s in RECURRING_SCHEDULE_SEED:
            db.add(Entity(
                type="habit", name=s["name"], is_active=True, space="life", profile="nemalenkiy",
                attributes={"weekday": s["weekday"], "time": s["time"], "recurring": True,
                            "irregular": s.get("irregular", False)},
            ))
        db.commit()
    finally:
        db.close()


seed_habits()

app = FastAPI(title="Life OS API", version="0.3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(messages.router)
app.include_router(export.router)
app.include_router(entities.router)
app.include_router(lifephase.router)
app.include_router(maintenance.router)
app.include_router(diary.router)
app.include_router(diary.public_router)
