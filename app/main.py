from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import Base, engine
from app.api import health, messages, export

# Iteration 1 has one table: raw_log. Later iterations add entities,
# edges, aliases, change_log, pending_actions, inbox_items via proper
# migrations (Alembic) instead of create_all — fine for now since we
# are the only ones running against this database.
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Life OS API", version="0.1.0")

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
