from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.auth import require_auth
from app.db import get_db
from app.models import RawLog, Entity, ChangeLogEntry, PendingAction, new_id
from app.services.claude_client import call_claude, similarity, phrase_analytics_answer

router = APIRouter(dependencies=[Depends(require_auth)])

TYPE_LABELS = {
    "person": "человек", "project": "проект", "event": "событие", "goal": "цель",
    "habit": "привычка", "task": "задача", "movie": "фильм", "show": "сериал",
    "book": "книга", "game": "игра", "leisure": "досуг", "order": "заказ",
    "anniversary": "дата",
}

HIGH_MATCH = 0.55
COMPLETION_WORDS = ["посмотрел", "посмотрела", "прочитал", "прочитала", "сделано",
                     "закончил", "закончила", "завершил", "завершила", "сходил",
                     "сходила", "съездил", "съездила", "прошёл", "прошла"]
WEEKDAY_MAP = {
    "monday": 0, "понедельник": 0, "пн": 0,
    "tuesday": 1, "вторник": 1, "вт": 1,
    "wednesday": 2, "среда": 2, "ср": 2,
    "thursday": 3, "четверг": 3, "чт": 3,
    "friday": 4, "пятница": 4, "пт": 4,
    "saturday": 5, "суббота": 5, "сб": 5,
    "sunday": 6, "воскресенье": 6, "вс": 6,
}


def now():
    return datetime.now(timezone.utc)


class MessageIn(BaseModel):
    content: str
    conversation_id: str = "default"
    today: str
    space: str = "life"
    profile: str = "nemalenkiy"


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


def normalize_habit_fields(fields: dict) -> dict:
    """
    Claude doesn't reliably use the exact field names we ask for in the
    prompt (seen in practice: day, day_of_week, frequency instead of
    weekday/recurring). This recovers weekday/daily/workdays/monthday/until
    from whatever reasonable field/format shows up, rather than depending
    on the model following the schema perfectly every time.
    """
    f = dict(fields or {})

    freq = str(f.get("frequency", "")).lower()
    if freq in ("daily", "every day", "ежедневно", "каждый день"):
        f["daily"] = True
        f["recurring"] = True
    elif freq in ("weekdays", "workdays", "будни", "будние дни", "пн-пт", "понедельник-пятница"):
        f["workdays"] = True
        f["recurring"] = True
    elif freq in ("biweekly", "every 2 weeks", "раз в 2 недели", "каждые 2 недели", "через неделю"):
        f["biweekly"] = True
        f["recurring"] = True
    elif freq in ("weekly", "recurring"):
        f["recurring"] = True

    if str(f.get("biweekly", "")).lower() in ("true", "1"):
        f["biweekly"] = True
    for key in ("anchor_date", "start_date", "first_date"):
        if f.get(key):
            f["anchor_date"] = f[key]
            break

    for key in ("weekday", "day_of_week", "day", "weekDay", "dow", "days"):
        val = f.get(key)
        if val in (None, ""):
            continue
        if isinstance(val, str) and val.strip().lower() in (
                "weekdays", "workdays", "будни", "будние дни", "пн-пт", "понедельник-пятница",
                "monday-friday", "mon-fri"):
            f["workdays"] = True
            f["recurring"] = True
            break
        if isinstance(val, str) and val.strip().lower() in ("daily", "every day", "ежедневно", "каждый день"):
            f["daily"] = True
            f["recurring"] = True
            break
        if isinstance(val, list):
            nums = set()
            for v in val:
                if isinstance(v, str):
                    n = WEEKDAY_MAP.get(v.strip().lower())
                    if n is not None:
                        nums.add(n)
                else:
                    try:
                        nums.add(int(v))
                    except (TypeError, ValueError):
                        pass
            if nums == {0, 1, 2, 3, 4}:
                f["workdays"] = True
                f["recurring"] = True
            elif len(nums) == 1:
                f["weekday"] = next(iter(nums))
                f["recurring"] = True
            break
        if isinstance(val, str):
            wd = WEEKDAY_MAP.get(val.strip().lower())
            if wd is not None:
                f["weekday"] = wd
                f["recurring"] = True
                break
        else:
            try:
                f["weekday"] = int(val)
                f["recurring"] = True
                break
            except (TypeError, ValueError):
                pass

    for key in ("until", "end_date", "endDate", "through", "deadline"):
        if f.get(key) not in (None, ""):
            f["until"] = f[key]
            break

    for key in ("monthday", "day_of_month", "monthDay", "month_day"):
        val = f.get(key)
        if val not in (None, ""):
            try:
                md = int(val)
                if 1 <= md <= 31:
                    f["monthday"] = md
                    f["recurring"] = True
                    f.pop("weekday", None)
                    f.pop("daily", None)
                    f.pop("workdays", None)
            except (TypeError, ValueError):
                pass
            break

    return f


def find_match(db: Session, cand_type: str, cand_name: str, space: str, profile: str, cand_date: str | None = None):
    best, best_score = None, 0.0
    entities = db.query(Entity).filter(Entity.type == cand_type, Entity.is_active == True,  # noqa: E712
                                        Entity.space == space, Entity.profile == profile).all()
    for e in entities:
        # For events (and anything else with an explicit date), two different
        # concrete dates mean two different occurrences no matter how similar
        # the names are — e.g. two fixtures between the same two teams,
        # home and away, share every word in the name.
        if cand_type == "event" and cand_date and e.attributes.get("date") and e.attributes["date"] != cand_date:
            continue
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
        pending_created = pending.created_at
        if pending_created.tzinfo is None:
            pending_created = pending_created.replace(tzinfo=timezone.utc)
        stale_by_time = (now() - pending_created).total_seconds() > 900
        # A short yes/no-shaped reply is a real answer to "тот же или новый?";
        # anything long (like a fresh bulk message) clearly isn't, even if it
        # arrives before the 15-minute window — treat the pending as stale
        # rather than silently swallowing the new message as a one-word answer.
        stale_by_length = len(payload.content.strip()) > 40
        if stale_by_time or stale_by_length:
            pending.resolved = True
            db.commit()
            pending = None
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
            new_entity = Entity(type=cand["type"], name=cand["name"], space=payload.space, profile=payload.profile,
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
                       for e in db.query(Entity).filter(Entity.is_active == True,  # noqa: E712
                                                          Entity.space == payload.space,
                                                          Entity.profile == payload.profile).all()]
    recent = [f"{r.role}: {r.content}" for r in
              db.query(RawLog).filter(RawLog.conversation_id == payload.conversation_id)
              .order_by(desc(RawLog.event_timestamp)).limit(6).all()][::-1]
    lifephase_entity = db.query(Entity).filter(Entity.type == "lifephase", Entity.is_active == True,
                                                Entity.profile == payload.profile).first()
    lifephase = lifephase_entity.attributes if lifephase_entity else None

    try:
        analysis = await call_claude(payload.content, payload.today, lifephase, known_entities, recent, payload.space)
    except Exception as e:
        reply_lines.append(f"Не получилось разобрать сообщение через Claude API. ({e})")
        asst = RawLog(conversation_id=payload.conversation_id, role="assistant",
                       content="\n".join(reply_lines), event_timestamp=now(), created_at=now())
        db.add(asst)
        db.commit()
        return [MessageOut.model_validate(user_row), MessageOut.model_validate(asst)]

    reflection = (analysis.get("reflection") or "").strip()

    # --- Analytics question: count habit occurrences from done_dates ---
    aq = analysis.get("analytics_query")
    if analysis.get("intent") == "question" and aq and aq.get("habit_name"):
        habit, score = find_match(db, "habit", aq["habit_name"], payload.space, payload.profile)
        if not habit or score < 0.3:
            reply_lines.append(f"Не нашёл привычку «{aq['habit_name']}» для подсчёта.")
        else:
            done_dates = habit.attributes.get("done_dates", []) or []
            date_from = aq.get("date_from")
            date_to = aq.get("date_to")
            filtered = [d for d in done_dates
                        if (not date_from or d >= date_from) and (not date_to or d <= date_to)]
            facts = {
                "habit_name": habit.name,
                "count": len(filtered),
                "date_from": date_from, "date_to": date_to,
                "total_ever_tracked": len(done_dates),
            }
            try:
                answer = await phrase_analytics_answer(payload.content, facts)
            except Exception:
                period = f" с {date_from} по {date_to}" if date_from or date_to else ""
                answer = f"«{habit.name}»: отмечено выполненным {len(filtered)} раз{period}."
            reply_lines.append(answer)
        asst = RawLog(conversation_id=payload.conversation_id, role="assistant",
                       content="\n".join(reply_lines), event_timestamp=now(), created_at=now())
        db.add(asst)
        db.commit()
        return [MessageOut.model_validate(user_row), MessageOut.model_validate(asst)]

    if analysis.get("intent") == "edit" and analysis.get("edits"):
        for edit in analysis["edits"]:
            target, best_score = None, 0.0
            for e in db.query(Entity).filter(Entity.is_active == True, Entity.space == payload.space,  # noqa: E712
                                              Entity.profile == payload.profile).all():
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
            new_fields = edit.get("new_fields") or {}
            if target.type == "habit":
                new_fields = normalize_habit_fields(new_fields)
            target.attributes = {**(target.attributes or {}), **new_fields}
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

    pending_created_this_turn = False
    for cand in analysis["candidates"]:
        cand["fields"] = normalize_fields(cand.get("fields", {}))
        if cand["type"] == "habit":
            cand["fields"] = normalize_habit_fields(cand["fields"])
        best, score = find_match(db, cand["type"], cand["name"], payload.space, payload.profile, cand["fields"].get("date"))
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
        elif best and 0 < score < HIGH_MATCH and not pending_created_this_turn:
            pending_created_this_turn = True
            db.add(PendingAction(conversation_id=payload.conversation_id,
                                  question=f'Это тот же {TYPE_LABELS.get(cand["type"])} «{best.name}»?',
                                  candidate=cand, matched_entity_id=best.id))
            reply_lines.append(
                f'Уточнение: это тот же {TYPE_LABELS.get(cand["type"], cand["type"])} «{best.name}», '
                f'или новый «{cand["name"]}»? Ответьте "тот же" или "новый".')
        else:
            new_entity = Entity(type=cand["type"], name=cand["name"], space=payload.space, profile=payload.profile,
                                 attributes=cand["fields"], is_active=True)
            db.add(new_entity)
            db.flush()
            note = "похожих сущностей не найдено"
            if best:
                note = f'похоже на "{best.name}" (score {score:.2f}), но создано отдельно — не более одного уточнения за раз'
            log_change(db, new_entity.id, "created", cand["fields"], final_conf, "create_entity",
                       [note, f'уверенность Claude {cand.get("confidence")}'], source_msg_id)
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
