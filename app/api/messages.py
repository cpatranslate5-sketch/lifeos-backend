from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.db import get_db
from app.models import RawLog

router = APIRouter(dependencies=[Depends(require_auth)])


class MessageIn(BaseModel):
    content: str = Field(..., min_length=1)
    conversation_id: str = "default"


class MessageOut(BaseModel):
    id: int
    conversation_id: str
    role: str
    content: str
    event_timestamp: datetime
    created_at: datetime

    class Config:
        from_attributes = True


class SendMessageResponse(BaseModel):
    user_message: MessageOut
    assistant_message: MessageOut


@router.post("/messages", response_model=SendMessageResponse)
def send_message(payload: MessageIn, db: Session = Depends(get_db)):
    """
    Iteration 1: no NLU/Claude call yet — just append to raw_log and
    return a fixed acknowledgement. Entity extraction is iteration 2.
    """
    now = datetime.now(timezone.utc)

    user_row = RawLog(
        conversation_id=payload.conversation_id,
        role="user",
        content=payload.content,
        event_timestamp=now,
        created_at=now,
    )
    db.add(user_row)
    db.flush()  # get user_row.id before commit

    assistant_row = RawLog(
        conversation_id=payload.conversation_id,
        role="assistant",
        content="Записано.",
        event_timestamp=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )
    db.add(assistant_row)
    db.commit()
    db.refresh(user_row)
    db.refresh(assistant_row)

    return SendMessageResponse(
        user_message=MessageOut.model_validate(user_row),
        assistant_message=MessageOut.model_validate(assistant_row),
    )


@router.get("/messages", response_model=list[MessageOut])
def list_messages(
    conversation_id: str = Query(default="default"),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(RawLog)
        .filter(RawLog.conversation_id == conversation_id)
        .order_by(RawLog.event_timestamp.asc())
        .all()
    )
    return [MessageOut.model_validate(r) for r in rows]
