import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Text, DateTime

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
