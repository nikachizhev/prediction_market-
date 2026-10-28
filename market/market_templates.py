"""Market templates for the creation wizard (spec E9.2, docs/api.md «MarketTemplate»).

The one place the templates live: screens get them from GET /api/templates. Texts come from the
approved mockup (docs/mockups/quick-create/Main.dc.html, `blankDefs()` and `templates()`).
The deadline rule (entries until 23:59 the day before {date}, outcome the day after) is applied
by the Mini App; every template has a `date` blank for it.
"""
from __future__ import annotations

from copy import deepcopy

from .models import CATEGORIES

# Blank descriptions shared by the templates: key -> (label, type, placeholder, hint).
BLANKS: dict[str, tuple[str, str, str, str]] = {
    "date": ("Когда", "date", "", "[дата]"),
    "subject": ("Предмет", "text", "матанализ", "[предмет]"),
    "team": ("Команда", "text", "сборная вуза", "[команда]"),
    "player": ("Кто", "text", "наш капитан", "[игрок]"),
    "game": ("Игра", "text", "название игры", "[игра]"),
    "rank": ("Что возьмёшь", "text", "Легенду", "[ранг]"),
    "product": ("Что покажут", "text", "новый телефон", "[что]"),
    "app": ("Чего", "text", "нашего бота", "[чего]"),
    "video": ("Видео", "text", "наш клип", "[видео]"),
    "num": ("Сколько просмотров", "number", "10000", "[N]"),
    "what": ("Что", "text", "курсовую", "[что]"),
}

# Category slug -> templates in the mockup's order: (id, blanks, text, criteria).
TEMPLATES: dict[str, tuple[tuple[str, tuple[str, ...], str, str], ...]] = {
    "study": (
        ("exam", ("subject", "date"), "Сдам ли {subject} на 4 или 5 до {date}?",
         "ДА — если до {date} в зачётке по предмету «{subject}» стоит 4 или 5.\n"
         "НЕТ — если стоит 3 или ниже, незачёт или экзамен не сдан к этой дате.\n"
         "Чем подтвердить: скриншот зачётки или ведомости.\n"
         "Если экзамен перенесут — ждём новой даты, но не дольше месяца."),
        ("classes", ("date",), "Отменят ли пары {date}?",
         "ДА — если {date} отменят все пары группы: объявление в чате группы или на сайте вуза.\n"
         "НЕТ — если хотя бы одна пара пройдёт по расписанию.\n"
         "Чем подтвердить: скриншот объявления или расписания."),
    ),
    "sport": (
        ("win", ("team", "date"), "Победит ли {team} в матче {date}?",
         "ДА — если {team} победит в матче {date} по официальному протоколу, с учётом овертайма и серии пенальти.\n"
         "НЕТ — если ничья или поражение.\n"
         "Чем подтвердить: скриншот результата матча.\n"
         "Если матч перенесут или отменят — рынок отменяется."),
        ("goal", ("player", "date"), "Забьёт ли {player} в матче {date}?",
         "ДА — если {player} забьёт хотя бы один гол в матче {date}; серия пенальти не считается.\n"
         "НЕТ — если не забьёт или не выйдет на поле.\n"
         "Чем подтвердить: скриншот протокола матча.\n"
         "Если матч перенесут или отменят — рынок отменяется."),
    ),
    "games": (
        ("release", ("game", "date"), "Выйдет ли {game} до {date}?",
         "ДА — если {game} официально выйдет до {date}: релиз в магазине или у издателя.\n"
         "НЕТ — если к этой дате релиза не будет; ранний доступ и бета не считаются.\n"
         "Чем подтвердить: ссылка на страницу игры.\n"
         "Если игру отменят — итог НЕТ."),
        ("rank", ("rank", "game", "date"), "Возьму ли я {rank} в {game} до {date}?",
         "ДА — если до {date} я возьму {rank} в {game}.\n"
         "НЕТ — если к этой дате не возьму.\n"
         "Чем подтвердить: скриншот профиля с рангом и датой."),
    ),
    "tech": (
        ("show", ("product", "date"), "Покажут ли {product} на презентации {date}?",
         "ДА — если {product} официально покажут на презентации {date}.\n"
         "НЕТ — если не покажут или покажут только тизер.\n"
         "Чем подтвердить: ссылка на запись презентации или новость компании.\n"
         "Если презентацию перенесут — рынок отменяется."),
        ("update", ("app", "date"), "Выйдет ли обновление {app} до {date}?",
         "ДА — если до {date} выйдет обновление {app}: новая версия в магазине или на сайте.\n"
         "НЕТ — если к этой дате обновления не будет.\n"
         "Чем подтвердить: скриншот страницы с номером версии и датой."),
    ),
    "fun": (
        ("views", ("video", "num", "date"), "Наберёт ли {video} {num} просмотров до {date}?",
         "ДА — если до {date} у видео «{video}» будет {num} просмотров или больше.\n"
         "НЕТ — если меньше.\n"
         "Чем подтвердить: скриншот счётчика просмотров с датой."),
        ("finish", ("what", "date"), "Доделаем ли мы {what} до {date}?",
         "ДА — если до {date} работа «{what}» готова и показана всем.\n"
         "НЕТ — если к этой дате не готова.\n"
         "Чем подтвердить: ссылка или фото результата."),
    ),
}


def blank(key: str) -> dict:
    label, kind, placeholder, hint = BLANKS[key]
    return {"key": key, "label": label, "type": kind, "placeholder": placeholder, "hint": hint}


def _catalog() -> list[dict]:
    return [
        {"id": tid, "category": slug, "text": text, "criteria": criteria, "blanks": [blank(k) for k in keys]}
        for slug, _ in CATEGORIES
        for tid, keys, text, criteria in TEMPLATES.get(slug, ())
    ]


_CATALOG = _catalog()


def market_templates() -> list[dict]:
    """All templates as `MarketTemplate`, grouped in the order of CATEGORIES; a fresh copy per call."""
    return deepcopy(_CATALOG)


def template_ids() -> set[str]:
    return {t["id"] for t in _CATALOG}
