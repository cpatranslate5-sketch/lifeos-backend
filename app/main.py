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

with engine.connect() as conn:
    cols = [row[1] for row in conn.execute(text("PRAGMA table_info(entities)"))]
    if "space" not in cols:
        conn.execute(text("ALTER TABLE entities ADD COLUMN space TEXT DEFAULT 'life'"))
        conn.commit()
    if "profile" not in cols:
        conn.execute(text("ALTER TABLE entities ADD COLUMN profile TEXT DEFAULT 'nemalenkiy'"))
        conn.commit()

os.makedirs("/app/data/photos", exist_ok=True)
os.makedirs("/app/data/covers", exist_ok=True)

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
app.include_router(entities.public_router)
app.include_router(lifephase.router)
app.include_router(maintenance.router)
app.include_router(diary.router)
app.include_router(diary.public_router)
