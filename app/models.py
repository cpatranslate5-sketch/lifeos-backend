import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, Float, JSON

from app.db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RawLog(Base):
    """
    The immutable source of truth (see architecture doc §1, §4, §11).
    Nothing here is ever edited or deleted — only appended.

    conversation_id: kept from day 1 even though the MVP only uses a
        single conversation ("default") — this is what lets us split
        into multiple conversations later without a schema change.

    event_timestamp vs created_at: in the MVP these are the same
        (a message is recorded the instant it happens), but they are
        kept as separate columns so that a future import/restore
        feature can insert historical messages with their original
        event_timestamp while created_at correctly reflects when the
        row was actually written to this database.
    """

    __tablename__ = "raw_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String, nullable=False, default="default", index=True)
    role = Column(String, nullable=False)  # "user" | "assistant"
    content = Column(Text, nullable=False)
    event_timestamp = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)


def new_id() -> str:
    return str(uuid.uuid4())


class Entity(Base):
    """
    Any tracked thing: person, project, event, goal, habit, task,
    movie, show, book, game, leisure, lifephase (singleton).
    `attributes` is a flexible JSON bag — schema is per-type by convention,
    not enforced, so new fields never require a migration.
    """
    __tablename__ = "entities"

    id = Column(String, primary_key=True, default=new_id)
    type = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    space = Column(String, nullable=False, default="life", index=True)  # "life" | "work"
    profile = Column(String, nullable=False, default="nemalenkiy", index=True)  # "nemalenkiy" | "kotyonok"
    attributes = Column(JSON, nullable=False, default=dict)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)


class ChangeLogEntry(Base):
    """
    Append-only log of every create/update/delete, each carrying a
    decision_trace so "why is this here" is always answerable from
    what was recorded, not re-derived after the fact.
    """
    __tablename__ = "change_log"

    id = Column(String, primary_key=True, default=new_id)
    entity_id = Column(String, index=True, nullable=True)
    field = Column(String, nullable=True)
    value = Column(JSON, nullable=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    confidence = Column(Float, nullable=True)
    decision_trace = Column(JSON, nullable=True)
    source_message_id = Column(String, nullable=True)


class DiaryEntry(Base):
    """
    A single freeform diary entry for the PD (shared diary) folder.
    `profile` here means whose column/tab it's logged under
    (nemalenkiy/kotyonok), not a data-isolation boundary like on Entity —
    both of you can read the whole diary.
    """
    __tablename__ = "diary_entries"

    id = Column(String, primary_key=True, default=new_id)
    profile = Column(String, nullable=False, index=True)
    date = Column(String, nullable=False, index=True)  # YYYY-MM-DD
    text = Column(String, nullable=False)
    photo_paths = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)


class PendingAction(Base):
    """
    An unresolved clarification the pipeline is waiting on — e.g. an
    ambiguous entity match. The next user message is checked against
    this before running the normal pipeline.
    """
    __tablename__ = "pending_actions"

    id = Column(String, primary_key=True, default=new_id)
    conversation_id = Column(String, nullable=False, default="default")
    question = Column(String, nullable=False)
    candidate = Column(JSON, nullable=False)
    matched_entity_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_utcnow)
    resolved = Column(Boolean, nullable=False, default=False)
