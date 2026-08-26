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
    {{"type": "person|project|event|goal|habit|task|movie|show|book|game|leisure|order|anniversary", "name": "строка", "fields": {{"date": "YYYY-MM-DD или null"}}, "confidence": 0.0}}
  ],
  "relations": [
    {{"from_name": "строка", "to_name": "строка", "relation": "involves|belongs_to|scheduled_for|blocks|part_of", "confidence": 0.0}}
  ],
  "edits": [
    {{"existing_name": "строка — точное или близкое имя УЖЕ известной сущности из списка ниже", "new_name": "строка или null", "new_type": "person|project|event|goal|habit|task|movie|show|book|game|leisure|order|anniversary или null", "new_fields": {{}}}}
  ],
  "analytics_query": {{"habit_name": "строка — привычка, которую нужно посчитать", "date_from": "YYYY-MM-DD или null", "date_to": "YYYY-MM-DD или null"}} или null,
  "reflection": "короткая (до 15 слов) содержательная реплика от тебя — наблюдение, уместный вопрос или связь с чем-то ранее известным. Пустая строка, если добавить нечего по существу — НЕ пиши формальности вроде «понял» или «хорошо»."
}}
ВАЖНО про intent="edit": используй его, когда пользователь явно просит переименовать/исправить/поправить/это не то, что уже существует. НЕ создавай candidate в этом случае — используй "edits", указав "existing_name" точно как в списке известных сущностей ниже.
ВАЖНО про "analytics_query": заполни его (intent при этом = "question"), когда пользователь спрашивает "сколько раз", "посчитай", "сколько было" про повторяющуюся привычку (например "Посчитай тренировки по теннису за 2026 год"). habit_name — как можно точнее к названию известной привычки из списка ниже. date_from/date_to — переведи период в конкретные даты (например "за 2026 год" → date_from="2026-01-01", date_to="2026-12-31"; если период не указан — оба null, посчитать за всё время). Если вопрос не про подсчёт привычки — оставь null.
Поле "date" в fields обязательно для type="event" и для type="task", если в сообщении есть временной ориентир — переведи его в YYYY-MM-DD относительно сегодняшней даты. Если ориентира нет — date: null.
Бытовые дела (уборка, покупки, починка) — type="task" с fields.category="household".
В рабочем контексте: конкретные заказы/заявки клиентов (например, "заказ 1win", "заявка №123") — type="order".
Важные ежегодные даты (дни рождения, годовщины) — type="anniversary", fields.day (число) и fields.month (число), год не нужен.
Фильмы/сериалы/книги/игры — type="movie"/"show"/"book"/"game", fields.status: "want" (хочет позже) или "in_progress" (сейчас смотрит/читает/играет), или укажи завершение словами "посмотрел"/"прочитал"/"прошёл" — код сам поймёт это как готово.
Прочий досуг (футбол, поездки, хобби вне этих категорий) — type="leisure".
Привычки (type="habit") — если пользователь привязывает её к конкретному дню недели ("по субботам", "каждый понедельник") — укажи fields.recurring=true и fields.weekday (0=понедельник...6=воскресенье). Если "каждый день"/"ежедневно" — вместо weekday укажи fields.daily=true и fields.recurring=true. Если "по будням"/"пн-пт"/"понедельник-пятница" — укажи fields.workdays=true и fields.recurring=true (не weekday и не daily). Если привязка к конкретному числу месяца ("каждое 1 число", "5 числа каждого месяца", "в конце месяца" = 28) — укажи fields.monthday (число 1-31) и fields.recurring=true, не weekday/daily/workdays. Если указан срок окончания ("до декабря", "до 31 декабря", "по конец года") — переведи его в конкретную дату YYYY-MM-DD (последний день упомянутого периода) относительно сегодняшней даты выше и укажи как fields.until. Без weekday/daily/workdays/monthday привычка не будет нигде отображаться в приложении, поэтому эти поля критичны.
Если сообщение не несёт изменения состояния и не является analytics_query — intent="smalltalk" или "question", candidates=[], relations=[], edits=[], но "reflection" заполни содержательно — это и есть ответ пользователю.
confidence — твоя уверенность в том, что ты правильно понял факт (0 до 1), не уверенность в резолюции с базой (её оценивает код)."""

ANALYTICS_PHRASE_PROMPT = """Пользователь задал вопрос про подсчёт привычки. Ты уже получил точный, посчитанный кодом ответ — не пересчитывай и не сомневайся в числе, просто сформулируй его естественно и коротко (1-2 предложения) на русском.
Вопрос пользователя: "{question}"
Посчитанные факты: {facts}
Верни только сам текст ответа, без пояснений и без markdown."""

DIARY_QUERY_PROMPT = """Пользователь задаёт вопрос про совместный дневник воспоминаний (записи вида "Посмотрели фильм X", "Сходили в зоопарк", "Дочитала книгу Y"). Сегодняшняя дата: {today}.
Определи ключевое слово или короткую фразу для поиска по тексту записей (самое конкретное существительное/название из вопроса — например для "сколько раз ходили в кино" это "кино", для "сколько книг прочитала" это "книгу" или "дочитала") и период, если он указан (переведи в конкретные даты YYYY-MM-DD относительно сегодняшней; если период не указан — оба null).
Верни ТОЛЬКО JSON: {{"keyword": "строка", "date_from": "YYYY-MM-DD или null", "date_to": "YYYY-MM-DD или null"}}"""

DIARY_PHRASE_PROMPT = """Пользователь спросил про совместный дневник воспоминаний. Ты уже получил точные факты, посчитанные кодом (реальные записи из дневника) — не выдумывай ничего сверх них, отвечай только на основе них. Ответь естественно и по существу на русском, 1-3 предложения — можно кратко перечислить пару примеров дат/событий из examples, если это уместно для вопроса.
Вопрос пользователя: "{question}"
Найденные факты: {facts}
Верни только сам текст ответа, без пояснений и без markdown."""


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


async def _post_to_claude(system: str, user_content: str, max_tokens: int = 1000) -> str:
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
                        "max_tokens": max_tokens,
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
        stop_reason = data.get("stop_reason", "неизвестно")
        raise RuntimeError(f"Пустой ответ от Claude (stop_reason: {stop_reason}) — возможно, сообщение слишком объёмное/сложное для одного запроса")
    return text_block


async def call_claude(user_message: str, today_str: str, lifephase: dict | None,
                       known_entities: list[dict], recent_messages: list[str], space: str = "life") -> dict:
    system = SYSTEM_PROMPT.format(today=today_str)
    space_note = ("Сейчас пространство «Работа» — не используй личные категории (быт, кино/книги/игры/досуг), "
                   "только person/project/event/goal/habit/task/order, актуальные для рабочего контекста."
                   if space == "work" else "Сейчас пространство «Жизнь» — личный контекст, все типы доступны.")
    context_block = (
        f"{space_note}\n"
        f"Активный фокус (LifePhase): {json.dumps(lifephase, ensure_ascii=False) if lifephase else 'не задан'}\n"
        f"Известные сущности (для сопоставления имён): {json.dumps(known_entities, ensure_ascii=False)}\n"
        f"Последние сообщения: {json.dumps(recent_messages, ensure_ascii=False)}"
    )
    user_content = f"{context_block}\n\nСообщение пользователя: \"{user_message}\""

    text_block = await _post_to_claude(system, user_content, max_tokens=16000)
    cleaned = re.sub(r"```json|```", "", text_block).strip()
    return json.loads(cleaned)


async def phrase_analytics_answer(question: str, facts: dict) -> str:
    prompt = ANALYTICS_PHRASE_PROMPT.format(question=question, facts=json.dumps(facts, ensure_ascii=False))
    text_block = await _post_to_claude("", prompt, max_tokens=300)
    return text_block.strip()


async def extract_diary_query(question: str, today_str: str) -> dict:
    prompt = DIARY_QUERY_PROMPT.format(today=today_str) + f'\n\nВопрос пользователя: "{question}"'
    text_block = await _post_to_claude("", prompt, max_tokens=300)
    cleaned = re.sub(r"```json|```", "", text_block).strip()
    return json.loads(cleaned)


async def phrase_diary_answer(question: str, facts: dict) -> str:
    prompt = DIARY_PHRASE_PROMPT.format(question=question, facts=json.dumps(facts, ensure_ascii=False))
    text_block = await _post_to_claude("", prompt, max_tokens=400)
    return text_block.strip()
