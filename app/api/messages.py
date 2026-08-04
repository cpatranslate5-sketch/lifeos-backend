from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.auth import require_auth
from app.db import get_db
from app.models import RawLog, Entity, ChangeLogEntry, PendingAction, new_id
from app.services.claude_client import call_claude, similarity

router = APIRouter(dependencies=[Depends(require_auth)])

TYPE_LABELS = {
    "person": "человек", "project": "проект", "event": "событие", "goal": "цель",
    "habit": "привычка", "task": "задача", "movie": "фильм", "show": "сериал",
    "book": "книга", "game": "игра", "leisure": "досуг",
}

HIGH_MATCH = 0.55
COMPLETION_WORDS = ["посмотрел", "посмотрела", "прочитал", "прочитала", "сделано",
                     "закончил", "закончила", "завершил", "завершила", "сходил",
                     "сходила", "съездил", "съездила", "прошёл", "прошла"]


def now():
    return datetime.now(timezone.utc)


class MessageIn(BaseModel):
    content: str
    conversation_id: str = "default"
    today: str


class MessageOut(BaseModel):
    id: int
    conversation_id: str
    role: str
    content: str
    event_timestamp: datetime
    created_at: datetime

    class Config:
        from_attributes = True


def normalize_fields(fields: dict) -> dict:
    f = dict(fields or {})
    status = str(f.get("status") or "").lower()
    if status and any(w in status for w in COMPLETION_WORDS):
        f["done"] = True
    return f


def find_match(db: Session, cand_type: str, cand_name: str):
    best, best_score = None, 0.0
    entities = db.query(Entity).filter(Entity.type == cand_type, Entity.is_active == True).all()  # noqa: E712
    for e in entities:
        score = similarity(e.name, cand_name)
        if score > best_score:
            best, best_score = e, score
    return best, best_score


def log_change(db, entity_id, field, value, confidence, action, factors, source_message_id=None):
    db.add(ChangeLogEntry(
        entity_id=entity_id, field=field, value=value, confidence=confidence,
        source_message_id=source_message_id,
        decision_trace={"action": action, "factors": factors, "confidence": confidence,
                         "source": source_message_id},
    ))


@router.post("/messages", response_model=list[MessageOut])
async def send_message(payload: MessageIn, db: Session = Depends(get_db)):
    ts = now()
    user_row = RawLog(conversation_id=payload.conversation_id, role="user",
                       content=payload.content, event_timestamp=ts, created_at=ts)
    db.add(user_row)
    db.flush()
    source_msg_id = str(user_row.id)

    reply_lines = []

    pending = (db.query(PendingAction)
               .filter(PendingAction.conversation_id == payload.conversation_id,
                        PendingAction.resolved == False)  # noqa: E712
               .order_by(desc(PendingAction.created_at)).first())
    if pending:
        answer = payload.content.lower()
        cand = pending.candidate
        if "тот же" in answer or answer.strip().startswith("да"):
            target = db.get(Entity, pending.matched_entity_id)
            if target:
                target.attributes = {**(target.attributes or {}), **normalize_fields(cand.get("fields", {}))}
                target.updated_at = now()
                log_change(db, target.id, "attributes", cand.get("fields"), 1.0,
                           "resolved_pending_merge", ["пользователь подтвердил совпадение"], source_msg_id)
                reply_lines.append("Понял, объединил с существующей записью.")
        else:
            new_entity = Entity(type=cand["type"], name=cand["name"],
                                 attributes=normalize_fields(cand.get("fields", {})), is_active=True)
            db.add(new_entity)
            db.flush()
            log_change(db, new_entity.id, "created", cand.get("fields"), 1.0,
                       "resolved_pending_new", ["пользователь подтвердил, что это новая сущность"], source_msg_id)
            reply_lines.append(f"Хорошо, создал отдельную запись «{cand['name']}».")
        pending.resolved = True
        asst = RawLog(conversation_id=payload.conversation_id, role="assistant",
                       content="\n".join(reply_lines), event_timestamp=now(), created_at=now())
        db.add(asst)
        db.commit()
        return [MessageOut.model_validate(user_row), MessageOut.model_validate(asst)]

    known_entities = [{"id": e.id, "type": e.type, "name": e.name}
                       for e in db.query(Entity).filter(Entity.is_active == True).all()]  # noqa: E712
    recent = [f"{r.role}: {r.content}" for r in
              db.query(RawLog).filter(RawLog.conversation_id == payload.conversation_id)
              .order_by(desc(RawLog.event_timestamp)).limit(6).all()][::-1]
    lifephase_entity = db.query(Entity).filter(Entity.type == "lifephase", Entity.is_active == True).first()
    lifephase = lifephase_entity.attributes if lifephase_entity else None

    try:
        analysis = await call_claude(payload.content, payload.today, lifephase, known_entities, recent)
    except Exception as e:
        reply_lines.append(f"Не получилось разобрать сообщение через Claude API. ({e})")
        asst = RawLog(conversation_id=payload.conversation_id, role="assistant",
                       content="\n".join(reply_lines), event_timestamp=now(), created_at=now())
        db.add(asst)
        db.commit()
        return [MessageOut.model_validate(user_row), MessageOut.model_validate(asst)]

    reflection = (analysis.get("reflection") or "").strip()

    if analysis.get("intent") == "edit" and analysis.get("edits"):
        for edit in analysis["edits"]:
            target, best_score = None, 0.0
            for e in db.query(Entity).filter(Entity.is_active == True).all():  # noqa: E712
                score = similarity(e.name, edit["existing_name"])
                if score > best_score:
                    target, best_score = e, score
            if not target or best_score < 0.5:
                reply_lines.append(f"Не нашёл сущность «{edit['existing_name']}» для редактирования.")
                continue
            old_name, old_type = target.name, target.type
            if edit.get("new_name"):
                target.name = edit["new_name"]
            if edit.get("new_type"):
                target.type = edit["new_type"]
            target.attributes = {**(target.attributes or {}), **(edit.get("new_fields") or {})}
            target.updated_at = now()
            log_change(db, target.id, "edit", edit, 1.0, "explicit_edit",
                       [f'пользователь явно указал существующую сущность "{old_name}"'], source_msg_id)
            parts = []
            if edit.get("new_name") and edit["new_name"] != old_name:
                parts.append(f"переименовано в «{edit['new_name']}»")
            if edit.get("new_type") and edit["new_type"] != old_type:
                parts.append(f"тип изменён на \"{TYPE_LABELS.get(edit['new_type'], edit['new_type'])}\"")
            reply_lines.append(f"«{old_name}» — {', '.join(parts) if parts else 'поля обновлены'}.")
        if reflection:
            reply_lines.append(reflection)
        asst = RawLog(conversation_id=payload.conversation_id, role="assistant",
                       content="\n".join(reply_lines), event_timestamp=now(), created_at=now())
        db.add(asst)
        db.commit()
        return [MessageOut.model_validate(user_row), MessageOut.model_validate(asst)]

    if analysis.get("intent") != "capture" or not analysis.get("candidates"):
        reply_lines.append(reflection if reflection else "Записано.")
        asst = RawLog(conversation_id=payload.conversation_id, role="assistant",
                       content="\n".join(reply_lines), event_timestamp=now(), created_at=now())
        db.add(asst)
        db.commit()
        return [MessageOut.model_validate(user_row), MessageOut.model_validate(asst)]

    for cand in analysis["candidates"]:
        cand["fields"] = normalize_fields(cand.get("fields", {}))
        best, score = find_match(db, cand["type"], cand["name"])
        final_conf = (cand.get("confidence", 0.5) + score) / 2

        if best and score >= HIGH_MATCH:
            best.attributes = {**(best.attributes or {}), **cand["fields"]}
            best.updated_at = now()
            factors = [f'сопоставлено с "{best.name}" (score {score:.2f})',
                       f'уверенность Claude {cand.get("confidence")}']
            if cand["fields"].get("done"):
                factors.append("статус распознан как завершено — чекбокс отмечен автоматически")
            log_change(db, best.id, "attributes", cand["fields"], final_conf, "update_entity", factors, source_msg_id)
            reply_lines.append(f'Обновлено: {TYPE_LABELS.get(cand["type"], cand["type"])} «{best.name}».')
        elif best and 0 < score < HIGH_MATCH:
            db.add(PendingAction(conversation_id=payload.conversation_id,
                                  question=f'Это тот же {TYPE_LABELS.get(cand["type"])} «{best.name}»?',
                                  candidate=cand, matched_entity_id=best.id))
            reply_lines.append(
                f'Уточнение: это тот же {TYPE_LABELS.get(cand["type"], cand["type"])} «{best.name}», '
                f'или новый «{cand["name"]}»? Ответьте "тот же" или "новый".')
        else:
            new_entity = Entity(type=cand["type"], name=cand["name"], attributes=cand["fields"], is_active=True)
            db.add(new_entity)
            db.flush()
            log_change(db, new_entity.id, "created", cand["fields"], final_conf, "create_entity",
                       ["похожих сущностей не найдено", f'уверенность Claude {cand.get("confidence")}'],
                       source_msg_id)
            reply_lines.append(f'Создано: {TYPE_LABELS.get(cand["type"], cand["type"])} «{cand["name"]}».')

    if reflection:
        reply_lines.append(reflection)

    asst = RawLog(conversation_id=payload.conversation_id, role="assistant",
                   content="\n".join(reply_lines), event_timestamp=now(), created_at=now())
    db.add(asst)
    db.commit()
    return [MessageOut.model_validate(user_row), MessageOut.model_validate(asst)]


@router.get("/messages", response_model=list[MessageOut])
def list_messages(conversation_id: str = Query(default="default"), db: Session = Depends(get_db)):
    rows = (db.query(RawLog).filter(RawLog.conversation_id == conversation_id)
            .order_by(RawLog.event_timestamp.asc()).all())
    return [MessageOut.model_validate(r) for r in rows]
