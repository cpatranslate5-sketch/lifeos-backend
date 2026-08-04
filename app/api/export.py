import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.db import get_db
from app.models import RawLog

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/export")
def export_data(
    format: Literal["json", "md"] = Query(default="json"),
    conversation_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """
    Two formats, per the architecture doc:
      - json: full fidelity, meant for restoring the system later.
      - md: human-readable, meant for reading/skimming.

    conversation_id is optional — omit it to export everything.
    """
    query = db.query(RawLog).order_by(RawLog.conversation_id, RawLog.event_timestamp.asc())
    if conversation_id:
        query = query.filter(RawLog.conversation_id == conversation_id)
    rows = query.all()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    if format == "json":
        payload = [
            {
                "id": r.id,
                "conversation_id": r.conversation_id,
                "role": r.role,
                "content": r.content,
                "event_timestamp": r.event_timestamp.isoformat(),
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ]
        body = json.dumps({"raw_log": payload}, ensure_ascii=False, indent=2)
        return Response(
            content=body,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="lifeos-export-{stamp}.json"'},
        )

    # format == "md"
    lines: list[str] = ["# Life OS — экспорт", ""]
    current_conv = None
    for r in rows:
        if r.conversation_id != current_conv:
            current_conv = r.conversation_id
            lines.append(f"\n## Диалог: {current_conv}\n")
        ts = r.event_timestamp.strftime("%Y-%m-%d %H:%M")
        who = "Вы" if r.role == "user" else "Система"
        lines.append(f"**{ts} — {who}:** {r.content}")
    body = "\n\n".join(lines) if len(lines) > 2 else "# Life OS — экспорт\n\n(пусто)"

    return Response(
        content=body,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="lifeos-export-{stamp}.md"'},
    )
