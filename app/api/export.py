import json
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.auth import require_auth
from app.db import get_db
from app.models import RawLog, Entity, ChangeLogEntry

router = APIRouter(dependencies=[Depends(require_auth)])


@router.get("/export")
def export_data(format: Literal["json", "md"] = Query(default="json"), db: Session = Depends(get_db)):
    raw = db.query(RawLog).order_by(RawLog.conversation_id, RawLog.event_timestamp.asc()).all()
    entities = db.query(Entity).all()
    changes = db.query(ChangeLogEntry).order_by(ChangeLogEntry.timestamp.asc()).all()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    if format == "json":
        payload = {
            "raw_log": [{"id": r.id, "conversation_id": r.conversation_id, "role": r.role,
                         "content": r.content, "event_timestamp": r.event_timestamp.isoformat(),
                         "created_at": r.created_at.isoformat()} for r in raw],
            "entities": [{"id": e.id, "type": e.type, "name": e.name, "attributes": e.attributes,
                          "is_active": e.is_active, "created_at": e.created_at.isoformat(),
                          "updated_at": e.updated_at.isoformat()} for e in entities],
            "change_log": [{"id": c.id, "entity_id": c.entity_id, "field": c.field, "value": c.value,
                            "confidence": c.confidence, "decision_trace": c.decision_trace,
                            "timestamp": c.timestamp.isoformat()} for c in changes],
        }
        body = json.dumps(payload, ensure_ascii=False, indent=2)
        return Response(content=body, media_type="application/json",
                         headers={"Content-Disposition": f'attachment; filename="lifeos-export-{stamp}.json"'})

    lines = ["# Life OS — экспорт", "", "## Сущности", ""]
    for e in entities:
        lines.append(f"- **{e.name}** ({e.type})")
    lines.append("\n## Сообщения\n")
    current_conv = None
    for r in raw:
        if r.conversation_id != current_conv:
            current_conv = r.conversation_id
            lines.append(f"\n### Диалог: {current_conv}\n")
        ts = r.event_timestamp.strftime("%Y-%m-%d %H:%M")
        who = "Вы" if r.role == "user" else "Система"
        lines.append(f"**{ts} — {who}:** {r.content}")
    body = "\n\n".join(lines)
    return Response(content=body, media_type="text/markdown",
                     headers={"Content-Disposition": f'attachment; filename="lifeos-export-{stamp}.md"'})
