import asyncio
import json
import re
import httpx

from app.config import settings

SYSTEM_PROMPT = """Ты — модуль структурного анализа для персональной системы учёта жизни пользователя (Life OS).
Сегодняшняя дата: {today} (используй её, чтобы вычислить дату из относительных выражений вроде "завтра", "в четверг", "сегодня вечером").
Тебе НЕЛЬЗЯ формулировать подтверждение о том, что записано/создано/изменено — это делает код. Единственное исключение — поле "reflection" ниже, где можно и нужно сказать что-то по существу, если есть что. Верни ТОЛЬКО валидный JSON, без markdown-обрамления, без пояснений, строго в этой форме:
{{
  "intent": "capture" | "edit" | "question" | "smalltalk",
  "facts": ["краткий факт 1", "краткий факт 2"],
  "candidates": [
    {{"type": "person|project|event|goal|habit|task|movie|show|book|game|leisure", "name": "строка", "fields": {{"date": "YYYY-MM-DD или null"}}, "confidence": 0.0}}
  ],
  "relations": [
    {{"from_name": "строка", "to_name": "строка", "relation": "involves|belongs_to|scheduled_for|blocks|part_of", "confidence": 0.0}}
  ],
  "edits": [
    {{"existing_name": "строка — точное или близкое имя УЖЕ известной сущности из списка ниже", "new_name": "строка или null", "new_type": "person|project|event|goal|habit|task|movie|show|book|game|leisure или null", "new_fields": {{}}}}
  ],
  "reflection": "короткая (до 15 слов) содержательная реплика от тебя — наблюдение, уместный вопрос или связь с чем-то ранее известным. Пустая строка, если добавить нечего по существу — НЕ пиши формальности вроде «понял» или «хорошо»."
}}
ВАЖНО про intent="edit": используй его, когда пользователь явно просит переименовать/исправить/поправить/это не то, что уже существует. НЕ создавай candidate в этом случае — используй "edits", указав "existing_name" точно как в списке известных сущностей ниже.
Поле "date" в fields обязательно для type="event" и для type="task", если в сообщении есть временной ориентир — переведи его в YYYY-MM-DD относительно сегодняшней даты. Если ориентира нет — date: null.
Бытовые дела (уборка, покупки, починка) — type="task" с fields.category="household".
Привычки (type="habit") — если пользователь привязывает её к конкретному дню недели ("по субботам", "каждый понедельник", "на постоянной основе во вторник") — обязательно укажи fields.recurring=true и fields.weekday (число: 0=понедельник, 1=вторник, 2=среда, 3=четверг, 4=пятница, 5=суббота, 6=воскресенье). Без дня недели привычка не будет нигде отображаться в приложении, поэтому это поле критично.
Фильмы/сериалы/книги/игры — type="movie"/"show"/"book"/"game", fields.status: "want" (хочет позже) или "in_progress" (сейчас смотрит/читает/играет), или укажи завершение словами "посмотрел"/"прочитал"/"прошёл" — код сам поймёт это как готово.
Прочий досуг (футбол, поездки, хобби вне этих категорий) — type="leisure".
Если сообщение не несёт изменения состояния — intent="smalltalk" или "question", candidates=[], relations=[], edits=[], но "reflection" заполни содержательно — это и есть ответ пользователю.
confidence — твоя уверенность в том, что ты правильно понял факт (0 до 1), не уверенность в резолюции с базой (её оценивает код)."""


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    a, b = a.lower().strip(), b.lower().strip()
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.75
    aw, bw = set(a.split()), set(b.split())
    common = len(aw & bw)
    return common / max(len(aw), len(bw), 1) * 0.6


async def call_claude(user_message: str, today_str: str, lifephase: dict | None,
                       known_entities: list[dict], recent_messages: list[str]) -> dict:
    system = SYSTEM_PROMPT.format(today=today_str)
    context_block = (
        f"Контекст (только релевантное, не вся база):\n"
        f"Активный фокус (LifePhase): {json.dumps(lifephase, ensure_ascii=False) if lifephase else 'не задан'}\n"
        f"Известные сущности (для сопоставления имён): {json.dumps(known_entities, ensure_ascii=False)}\n"
        f"Последние сообщения: {json.dumps(recent_messages, ensure_ascii=False)}"
    )
    user_content = f"{context_block}\n\nСообщение пользователя: \"{user_message}\""

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.post(
                    "https://api.anthropic.com/v1/messages",
                    headers={
                        "x-api-key": settings.ANTHROPIC_API_KEY,
                        "anthropic-version": "2023-06-01",
                        "content-type": "application/json",
                    },
                    json={
                        "model": settings.CLAUDE_MODEL,
                        "max_tokens": 1000,
                        "system": system,
                        "messages": [{"role": "user", "content": user_content}],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            break
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError, httpx.ReadTimeout) as e:
            last_error = e
            if attempt < 2:
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            raise RuntimeError(f"Не удалось связаться с Claude API после {attempt + 1} попыток: {e}")
    else:
        raise RuntimeError(f"Не удалось связаться с Claude API: {last_error}")

    text_block = next((b["text"] for b in data.get("content", []) if b.get("type") == "text"), None)
    if not text_block:
        raise RuntimeError("Empty response from Claude")

    cleaned = re.sub(r"```json|```", "", text_block).strip()
    return json.loads(cleaned)
