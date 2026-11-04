"""AI rules helper for the market creation wizard (spec E9.1), one-line creation (E9.5) and /market in a chat (E9.4).

The model gets only the question (or the chat message's text), the category and today's Moscow date. Its answer
is checked with the rules of manual creation (`services.check_market_fields`); anything else is a refusal (`Failed`).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, time
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .events import log_event
from .models import CATEGORIES, Event, Trader
from .services import MarketError, check_market_fields

log = logging.getLogger(__name__)

MSK = ZoneInfo("Europe/Moscow")
EVENT = "assist_request"

TITLE_MIN, TITLE_MAX = 10, 120    # as in the wizard and Question.title of a user market
CRITERIA_MIN = 15                 # as in the wizard: shorter rules keep «Далее» disabled
SOURCE_MAX = 1000                 # a chat message (/market) is cut to this before it goes to the model
OPTIONS_MIN, OPTIONS_MAX, OPTION_MAX = 2, 10, 80   # as `services.create_market` checks options
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

_PROMPT = """Ты помогаешь составить правила рынка прогнозов в Telegram. Рынок — это вопрос, на который позже можно точно ответить ДА или НЕТ, или вопрос «кто из…» с вариантами ответа, где по каждому варианту итог — ДА или НЕТ. Участники ставят виртуальные монеты, создатель сам объявляет итог, а участники могут его оспорить, поэтому правила должны быть однозначными: любой, кто их прочитает, придёт к тому же итогу.

На входе JSON: question — вопрос создателя или текст сообщения из чата, category — выбранная им категория или null, today — сегодняшняя дата по Москве.

Ответь одним JSON-объектом, без пояснений и без markdown:
{"forbidden": null, "question": null, "why": null, "options": null, "criteria": "...", "category": "...", "closes_at": "ГГГГ-ММ-ДДTЧЧ:ММ", "resolves_at": "ГГГГ-ММ-ДДTЧЧ:ММ"}

forbidden — запрещённая тема, иначе null:
- "health" — здоровье, болезнь, травма или смерть конкретного человека;
- "violence" — насилие: драки, нападения, убийства, теракты, войны;
- "politics" — политика: выборы, политики, партии, власти, законы, митинги;
- "private" — личная жизнь другого человека: отношения, расставания, беременность, его тайны.
Если тема запрещена, остальные поля — null.

question — уточнённая формулировка или null. Уточняй, только если вопрос неоднозначный: нет срока; день без числа («в понедельник», «завтра», «на этой неделе»); непонятно, что считать успехом. Сохрани смысл и слова автора, не добавляй новых условий. Длина — от 10 до 120 символов, в конце — знак вопроса. Дату пиши словами: «до 30 ноября», «в понедельник, 2 ноября». Если вопрос однозначный — null.
Если на входе не вопрос, а утверждение или сообщение из чата («спорим, что Петя опоздает на пару», «завтра снег»), question обязателен: сформулируй из него вопрос по тем же правилам, без обращений, приветствий и слова «спорим».

why — если question не null, одна короткая фраза, зачем уточнили, например «Без срока итог не определить — добавили дату.» или «„В понедельник“ поймут по-разному — добавили дату.». Иначе null.

options — только для вопроса «кто из…», «какой…», «кто первым…», где возможных ответов несколько: список от 2 до 10 разных коротких вариантов, каждый до 80 символов, без нумерации. Варианты бери из текста или из общеизвестного списка (команды в матче, номинанты премии) — не выдумывай. Если назвать варианты нельзя, сформулируй вопрос на ДА или НЕТ и верни null. Для обычного вопроса ДА/НЕТ — null.

criteria — как определяем результат: строки через \\n, ровно в таком виде:
ДА — если …
НЕТ — если …
Чем подтвердить: …
Если перенесут или отменят — …
Последнюю строку пропусти, если событие нельзя перенести или отменить. ДА и НЕТ вместе покрывают все исходы. Если есть options, ДА и НЕТ — про один любой вариант: «ДА — если этот вариант…». Пиши коротко и конкретно, без «и т. п.». «Чем подтвердить» — то, что любой участник может проверить: скриншот, ссылка, официальный протокол.

Примеры criteria:
%(examples)s

category — ровно один ключ: %(categories)s. Если category на входе подходит к вопросу — оставь её.

closes_at — до какого момента принимаем прогнозы, resolves_at — когда станет известен итог. Время московское, формат ГГГГ-ММ-ДДTЧЧ:ММ.
- Если в вопросе есть дата события или срок («до 30 ноября», «в субботу»), closes_at — 23:59 накануне этой даты, resolves_at — 23:59 следующего дня после неё.
- Если срока нет, предложи разумный (обычно от недели до месяца от сегодняшнего дня), впиши его в question и считай сроки от него.
- closes_at — в будущем и не дальше чем через год от сегодняшнего дня; resolves_at — не раньше closes_at и не позже чем через год после него.

Пример. На входе: {"question": "Отменят ли пары в понедельник?", "category": null, "today": "2026-10-28, среда"}
Ответ: {"forbidden": null, "question": "Отменят ли пары в понедельник, 2 ноября?", "why": "«В понедельник» поймут по-разному — добавили дату.", "options": null, "criteria": "ДА — если 2 ноября отменят все пары группы: объявление в чате группы или на сайте вуза.\\nНЕТ — если хотя бы одна пара пройдёт по расписанию.\\nЧем подтвердить: скриншот объявления или расписания.", "category": "study", "closes_at": "2026-11-01T23:59", "resolves_at": "2026-11-03T23:59"}

Пример. На входе: {"question": "спорим, кубок факультета по футболу 30 ноября возьмёт ПИ-22, а не ПИ-21 и не ПИ-23", "category": null, "today": "2026-10-28, среда"}
Ответ: {"forbidden": null, "question": "Какая группа выиграет кубок факультета по футболу 30 ноября?", "why": null, "options": ["ПИ-21", "ПИ-22", "ПИ-23"], "criteria": "ДА — если этот вариант выиграет кубок 30 ноября по итогам финала.\\nНЕТ — если кубок выиграет другая группа.\\nЧем подтвердить: фото протокола или объявление в чате факультета.\\nЕсли турнир перенесут — ждём новой даты, но не дольше месяца.", "category": "sport", "closes_at": "2026-11-29T23:59", "resolves_at": "2026-12-01T23:59"}"""

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


def reserve(trader: Trader, props: dict | None = None) -> tuple[Event, bool]:
    """Take one of today's requests before calling the model, so parallel requests cannot exceed the limit.

    Returns the `assist_request` event and whether a request was left: when not, the event is a `limit` refusal.
    The trader lock is held only for the count, never during the HTTP call; a `pending` event already counts.
    """
    with transaction.atomic():
        Trader.objects.select_for_update().get(pk=trader.pk)
        over = left(trader) <= 0
        event = log_event(trader, EVENT, {"result": "limit" if over else "pending", **(props or {})})
    return event, not over


def finish(event: Event, **props) -> None:
    event.props = {**event.props, **props}
    event.save(update_fields=["props"])


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
    if question is None and not TITLE_MIN <= len(title.strip()) <= TITLE_MAX:
        # A chat message that is no question by itself: the model had to word one.
        raise Failed("schema")
    why = _text(data.get("why")) if question else None
    if why and len(why) > WHY_MAX:
        why = why[:WHY_MAX - 1].rstrip() + "…"
    options = _options(data.get("options"))

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
    return {"question": question, "why": why, "options": options, "criteria": criteria, "category": slug,
            "closes_at": closes_at.astimezone(MSK), "resolves_at": resolves_at.astimezone(MSK)}


def _options(value) -> list[str] | None:
    """Answer options of a «кто из…» market (spec E9.5), checked as `services.create_market` checks them."""
    if value in (None, False, "", []):
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise Failed("schema")
    options = [" ".join(item.split()) for item in value]
    options = [item for item in options if item]
    if (not OPTIONS_MIN <= len(options) <= OPTIONS_MAX or any(len(item) > OPTION_MAX for item in options)
            or len({item.casefold() for item in options}) != len(options)):
        raise Failed("schema")
    return options


def suggest(title: str, category: str | None, now: datetime | None = None) -> dict:
    """Rules for the question `title` (or a chat message's text): {"forbidden": topic} or {question, why, options,
    criteria, category, closes_at, resolves_at}, checked like a manually filled wizard. `options` is a list for a
    «кто из…» market, else None. Raises Failed when the model gives nothing usable."""
    now = now or timezone.now()
    if not isinstance(category, str) or category not in dict(CATEGORIES):
        category = None
    return check(parse(complete(request_body(title, category, now))), title, category, now)
