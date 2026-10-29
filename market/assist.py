"""AI rules helper for the market creation wizard (spec E9.1).

The model gets only the question, the category and today's Moscow date. Its answer is checked with the
rules of manual creation (`services.check_market_fields`); anything else is a refusal (`Failed`).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, time
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.utils import timezone

from .models import CATEGORIES, Event, Trader
from .services import MarketError, check_market_fields

log = logging.getLogger(__name__)

MSK = ZoneInfo("Europe/Moscow")
EVENT = "assist_request"

TITLE_MIN, TITLE_MAX = 10, 120    # as in the wizard and Question.title of a user market
CRITERIA_MIN = 15                 # as in the wizard: shorter rules keep «Далее» disabled
WHY_MAX = 160
TEMPERATURE = 0.2
MAX_TOKENS = 700

# Forbidden topics (decided 25.10.2026): the helper warns, complaints and the moderator hide the market.
WARNINGS = {
    "health": "Похоже, это рынок о здоровье конкретного человека",
    "violence": "Похоже, это рынок о насилии",
    "politics": "Похоже, это рынок о политике",
    "private": "Похоже, это рынок о личной жизни другого человека",
}

WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")

_PROMPT = """Ты помогаешь составить правила рынка прогнозов в Telegram. Рынок — это вопрос, на который позже можно точно ответить ДА или НЕТ. Участники ставят виртуальные монеты, создатель сам объявляет итог, а участники могут его оспорить, поэтому правила должны быть однозначными: любой, кто их прочитает, придёт к тому же итогу.

На входе JSON: question — вопрос создателя, category — выбранная им категория или null, today — сегодняшняя дата по Москве.

Ответь одним JSON-объектом, без пояснений и без markdown:
{"forbidden": null, "question": null, "why": null, "criteria": "...", "category": "...", "closes_at": "ГГГГ-ММ-ДДTЧЧ:ММ", "resolves_at": "ГГГГ-ММ-ДДTЧЧ:ММ"}

forbidden — запрещённая тема, иначе null:
- "health" — здоровье, болезнь, травма или смерть конкретного человека;
- "violence" — насилие: драки, нападения, убийства, теракты, войны;
- "politics" — политика: выборы, политики, партии, власти, законы, митинги;
- "private" — личная жизнь другого человека: отношения, расставания, беременность, его тайны.
Если тема запрещена, остальные поля — null.

question — уточнённая формулировка или null. Уточняй, только если вопрос неоднозначный: нет срока; день без числа («в понедельник», «завтра», «на этой неделе»); непонятно, что считать успехом. Сохрани смысл и слова автора, не добавляй новых условий. Длина — от 10 до 120 символов, в конце — знак вопроса. Дату пиши словами: «до 30 ноября», «в понедельник, 2 ноября». Если вопрос однозначный — null.

why — если question не null, одна короткая фраза, зачем уточнили, например «Без срока итог не определить — добавили дату.» или «„В понедельник“ поймут по-разному — добавили дату.». Иначе null.

criteria — как определяем результат: строки через \\n, ровно в таком виде:
ДА — если …
НЕТ — если …
Чем подтвердить: …
Если перенесут или отменят — …
Последнюю строку пропусти, если событие нельзя перенести или отменить. ДА и НЕТ вместе покрывают все исходы. Пиши коротко и конкретно, без «и т. п.». «Чем подтвердить» — то, что любой участник может проверить: скриншот, ссылка, официальный протокол.

Примеры criteria:
%(examples)s

category — ровно один ключ: %(categories)s. Если category на входе подходит к вопросу — оставь её.

closes_at — до какого момента принимаем прогнозы, resolves_at — когда станет известен итог. Время московское, формат ГГГГ-ММ-ДДTЧЧ:ММ.
- Если в вопросе есть дата события или срок («до 30 ноября», «в субботу»), closes_at — 23:59 накануне этой даты, resolves_at — 23:59 следующего дня после неё.
- Если срока нет, предложи разумный (обычно от недели до месяца от сегодняшнего дня), впиши его в question и считай сроки от него.
- closes_at — в будущем и не дальше чем через год от сегодняшнего дня; resolves_at — не раньше closes_at и не позже чем через год после него.

Пример. На входе: {"question": "Отменят ли пары в понедельник?", "category": null, "today": "2026-10-28, среда"}
Ответ: {"forbidden": null, "question": "Отменят ли пары в понедельник, 2 ноября?", "why": "«В понедельник» поймут по-разному — добавили дату.", "criteria": "ДА — если 2 ноября отменят все пары группы: объявление в чате группы или на сайте вуза.\\nНЕТ — если хотя бы одна пара пройдёт по расписанию.\\nЧем подтвердить: скриншот объявления или расписания.", "category": "study", "closes_at": "2026-11-01T23:59", "resolves_at": "2026-11-03T23:59"}"""

_EXAMPLES = (
    "Вопрос «Победит ли Зенит в матче 7 ноября?»:\n"
    "ДА — если «Зенит» победит в матче 7 ноября по официальному протоколу, с учётом овертайма и серии пенальти.\n"
    "НЕТ — если ничья или поражение.\n"
    "Чем подтвердить: скриншот результата матча.\n"
    "Если матч перенесут или отменят — рынок отменяется.\n\n"
    "Вопрос «Сдам ли матанализ на 4 или 5 до 25 января?»:\n"
    "ДА — если до 25 января в зачётке по предмету «матанализ» стоит 4 или 5.\n"
    "НЕТ — если стоит 3 или ниже, незачёт или экзамен не сдан к этой дате.\n"
    "Чем подтвердить: скриншот зачётки или ведомости.\n"
    "Если экзамен перенесут — ждём новой даты, но не дольше месяца.\n\n"
    "Вопрос «Выйдет ли GTA 6 до 1 декабря?»:\n"
    "ДА — если GTA 6 официально выйдет до 1 декабря: релиз в магазине или у издателя.\n"
    "НЕТ — если к этой дате релиза не будет; ранний доступ и бета не считаются.\n"
    "Чем подтвердить: ссылка на страницу игры.\n"
    "Если игру отменят — итог НЕТ."
)

SYSTEM_PROMPT = _PROMPT % {
    "examples": _EXAMPLES,
    "categories": ", ".join(f'"{slug}" ({label})' for slug, label in CATEGORIES),
}

_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)
_MOMENT = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


class Failed(Exception):
    """No usable answer from the model; `reason` goes to analytics: timeout, http, schema or dates."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def enabled() -> bool:
    """The helper is on for everyone: switched on and a key is set on the server."""
    return bool(settings.ASSIST_ENABLED and settings.LLM_API_KEY)


def used_today(trader: Trader) -> int:
    """Requests that count against the daily limit: every `assist_request` of this Moscow day but refusals by the limit."""
    day_start = datetime.combine(timezone.localdate(timezone=MSK), time.min, tzinfo=MSK)
    return (Event.objects.filter(trader=trader, name=EVENT, created_at__gte=day_start)
            .exclude(props__result="limit").count())


def left(trader: Trader) -> int:
    return max(0, settings.ASSIST_DAILY_LIMIT - used_today(trader))


def state(trader: Trader) -> dict:
    """`Me.assist` in the API."""
    return {"enabled": enabled(), "left": left(trader)}


# --- the model ---------------------------------------------------------------------------

def request_body(title: str, category: str | None, now: datetime) -> dict:
    """Chat completion request. The user message holds the question, the category and today's date only."""
    today = timezone.localdate(now, timezone=MSK)
    user = {"question": title, "category": category, "today": f"{today.isoformat()}, {WEEKDAYS[today.weekday()]}"}
    return {
        "model": settings.LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
        ],
        "response_format": {"type": "json_object"},
        "reasoning": {"effort": "none"},
        "temperature": TEMPERATURE,
        "max_tokens": MAX_TOKENS,
    }


def complete(payload: dict) -> str:
    """Text of the model's reply."""
    try:
        response = requests.post(f"{settings.LLM_BASE_URL}/chat/completions", json=payload,
                                 headers={"Authorization": f"Bearer {settings.LLM_API_KEY}"},
                                 timeout=settings.ASSIST_TIMEOUT)
    except requests.Timeout:
        raise Failed("timeout") from None
    except requests.RequestException as exc:
        log.warning("AI helper: %s", type(exc).__name__)
        raise Failed("http") from None
    if response.status_code != 200:
        log.warning("AI helper: HTTP %s", response.status_code)
        raise Failed("http")
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, LookupError, TypeError):
        raise Failed("schema") from None
    if not isinstance(content, str):
        raise Failed("schema")
    return content


# --- checking the answer -----------------------------------------------------------------

def parse(content: str) -> dict:
    fenced = _FENCE.match(content)
    if fenced:
        content = fenced.group(1)
    try:
        data = json.loads(content)
    except ValueError:
        raise Failed("schema") from None
    if not isinstance(data, dict):
        raise Failed("schema")
    return data


def _text(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise Failed("schema")
    return value.strip() or None


def _same(a: str, b: str) -> bool:
    def norm(s: str) -> str:
        return " ".join(s.casefold().split()).rstrip("?!. ")
    return norm(a) == norm(b)


def _category(value) -> str | None:
    """A category key; the model sometimes answers with the label instead."""
    if not isinstance(value, str):
        return None
    value = value.strip().casefold()
    for slug, label in CATEGORIES:
        if value in (slug, label.casefold()):
            return slug
    return None


def _moment(value) -> datetime:
    """A moment as YYYY-MM-DDTHH:MM; naive means Moscow."""
    if not isinstance(value, str) or not _MOMENT.match(value.strip()):
        raise Failed("schema")
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError:
        raise Failed("schema") from None
    if timezone.is_naive(moment):
        moment = moment.replace(tzinfo=MSK)
    return moment.replace(second=0, microsecond=0)


def check(data: dict, title: str, category: str | None, now: datetime) -> dict:
    """The model's answer as {"forbidden": topic} or checked fields; raises Failed."""
    forbidden = data.get("forbidden")
    if forbidden not in (None, False, ""):
        if not isinstance(forbidden, str) or forbidden not in WARNINGS:
            raise Failed("schema")
        return {"forbidden": forbidden}

    question = _text(data.get("question"))
    if question is not None and _same(question, title):
        question = None
    if question is not None and not TITLE_MIN <= len(question) <= TITLE_MAX:
        raise Failed("schema")
    why = _text(data.get("why")) if question else None
    if why and len(why) > WHY_MAX:
        why = why[:WHY_MAX - 1].rstrip() + "…"

    criteria = data.get("criteria")
    if not isinstance(criteria, str):
        raise Failed("schema")
    criteria = "\n".join(line.strip() for line in criteria.strip().splitlines() if line.strip())
    if len(criteria) < CRITERIA_MIN:
        raise Failed("schema")

    slug = _category(data.get("category")) or category
    if slug is None:
        raise Failed("schema")

    closes_at, resolves_at = _moment(data.get("closes_at")), _moment(data.get("resolves_at"))
    try:
        # Title, criteria and category are valid by now, so only the dates can fail here.
        check_market_fields(title=question or title, criteria=criteria, category=slug,
                            closes_at=closes_at, resolves_at=resolves_at, now=now)
    except MarketError:
        raise Failed("dates") from None
    return {"question": question, "why": why, "criteria": criteria, "category": slug,
            "closes_at": closes_at.astimezone(MSK), "resolves_at": resolves_at.astimezone(MSK)}


def suggest(title: str, category: str | None, now: datetime | None = None) -> dict:
    """Rules for the question `title`: {"forbidden": topic} or {question, why, criteria, category, closes_at,
    resolves_at}, checked like a manually filled wizard. Raises Failed when the model gives nothing usable."""
    now = now or timezone.now()
    if not isinstance(category, str) or category not in dict(CATEGORIES):
        category = None
    return check(parse(complete(request_body(title, category, now))), title, category, now)
