from __future__ import annotations

import logging

from django.db import IntegrityError, transaction

from market import services
from market.models import Question, Trader
from market.services import MarketError

from .abtest import ABTestSource
from .base import QuestionDraft, QuestionSource, SourceUpdate
from .github import GitHubSource

log = logging.getLogger(__name__)
FINAL = (Question.Status.RESOLVED, Question.Status.ANNULLED)


def default_sources() -> list[QuestionSource]:
    return [ABTestSource(), GitHubSource()]


def _conflicted(draft: QuestionDraft) -> list[Trader]:
    ids = set(draft.conflicted_trader_ids)
    traders = list(Trader.objects.filter(pk__in=ids)) if ids else []
    for login in draft.conflicted_logins:
        traders += list(Trader.objects.filter(github_login__iexact=login))
    return traders


def _create(src: QuestionSource, draft: QuestionDraft) -> Question | None:
    if Question.objects.filter(source_type=src.source_type, source_ref=draft.source_ref).exists():
        return None
    try:
        with transaction.atomic():
            return services.create_question(
                title=draft.title, resolution_criteria=draft.resolution_criteria,
                source_type=src.source_type, source_ref=draft.source_ref, deadline=draft.deadline,
                initial_prob=draft.initial_prob, conflicted_traders=_conflicted(draft),
            )
    except IntegrityError:
        return None


def _apply(q: Question, upd: SourceUpdate) -> str | None:
    if q.status in FINAL:
        return None
    if upd is SourceUpdate.CLOSE_BETTING:
        if q.status != Question.Status.OPEN:
            return None
        services.close_betting(q)
        return f"Ставки закрыты: {q.title}"
    if upd is SourceUpdate.ANNUL:
        services.annul(q)
        return f"Аннулировано: {q.title}"
    outcome = "yes" if upd is SourceUpdate.RESOLVE_YES else "no"
    services.close_betting(q)
    services.resolve(q, outcome)
    return f"Разрешено ({'ДА' if outcome == 'yes' else 'НЕТ'}): {q.title}"


def sync_all(sources: list[QuestionSource] | None = None) -> list[str]:
    msgs: list[str] = []
    for src in sources if sources is not None else default_sources():
        name = src.source_type
        try:
            created = 0
            for draft in src.discover():
                if _create(src, draft):
                    created += 1
            if created:
                msgs.append(f"[{name}] создано вопросов: {created}")
            for q in Question.objects.filter(source_type=name).exclude(status__in=FINAL):
                try:
                    upd = src.check(q)
                    if upd is not None:
                        m = _apply(q, upd)
                        if m:
                            msgs.append(m)
                except MarketError as e:
                    log.warning("sync %s: %s", q.source_ref, e)
                except Exception as e:
                    log.exception("check failed for %s", q.source_ref)
                    msgs.append(f"[{name}] ошибка проверки {q.source_ref}: {e}")
        except Exception as e:
            log.exception("source %s failed", name)
            msgs.append(f"[{name}] источник недоступен: {e}")
    if not msgs:
        msgs.append("Изменений нет.")
    return msgs
