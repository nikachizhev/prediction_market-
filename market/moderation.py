"""Complaint decisions shared by the bot and admin; money moves only via services."""
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone
from .models import MarketComplaint, ModerationAction, Question
from .services import MarketError


def admin_ids() -> set[int]:
    try:
        ids = {int(value) for value in settings.MODERATION_ADMIN_IDS}
        return ids if all(value > 0 for value in ids) else set()
    except (ValueError, TypeError):
        return set()


def _actor(*, telegram_id=None, user=None) -> str:
    if user is not None and telegram_id is None and user.is_active and user.is_staff and user.has_perm('market.change_question'):
        return f"django:{user.pk}"
    if user is None and type(telegram_id) is int and telegram_id in admin_ids():
        return f"telegram:{telegram_id}"
    raise PermissionDenied("Нет доступа к модерации.")


@transaction.atomic
def review(complaint_id: int, decision: str, *, telegram_id=None, user=None) -> Question:
    actor = _actor(telegram_id=telegram_id, user=user)
    if decision not in ('hide', 'dismiss', 'cancel'):
        raise MarketError("Неизвестное решение.")
    report = MarketComplaint.objects.get(pk=complaint_id)
    question = Question.objects.select_for_update().get(pk=report.question_id)
    report.refresh_from_db()
    if report.status != MarketComplaint.Status.PENDING and not (decision == 'cancel' and report.status == MarketComplaint.Status.UPHELD):
        raise MarketError("Жалоба уже рассмотрена. Обновите очередь.")
    now = timezone.now()
    if decision == 'cancel':
        from .services import cancel_moderated
        question = cancel_moderated(question)
    elif decision == 'hide':
        question.moderation_hidden = True
        question.hidden_at = question.hidden_at or now
    elif not question.moderation_hidden:
        question.hidden_at = None
    question.save(update_fields=['moderation_hidden', 'hidden_at'])
    pending = MarketComplaint.objects.filter(question=question, status=MarketComplaint.Status.PENDING)
    if decision == 'cancel' and report.status == MarketComplaint.Status.UPHELD:
        from django.db.models import Q
        pending = MarketComplaint.objects.filter(question=question).filter(Q(status='pending') | Q(pk=report.pk))
    count = pending.update(
        status=MarketComplaint.Status.UPHELD if decision in ('hide', 'cancel') else MarketComplaint.Status.DISMISSED,
        reviewed_at=now, reviewed_by=actor)
    ModerationAction.objects.create(question=question, action=decision, actor=actor, complaint_count=count)
    return question


@transaction.atomic
def restore(question_id: int, *, expected_action=None, telegram_id=None, user=None) -> Question:
    actor = _actor(telegram_id=telegram_id, user=user)
    question = Question.objects.select_for_update().get(pk=question_id)
    if question.status == Question.Status.ANNULLED:
        raise MarketError("Отменённый рынок нельзя восстановить. Возвраты уже выполнены.")
    latest = question.moderation_actions.first()
    if expected_action is not None and (latest.pk if latest else 0) != expected_action:
        raise MarketError("Решение уже изменилось. Обновите историю.")
    if question.complaints.filter(status=MarketComplaint.Status.PENDING).exists():
        raise MarketError("Сначала рассмотрите новые жалобы на этот рынок.")
    if not question.hidden_at:
        raise MarketError("Рынок уже виден в ленте.")
    question.moderation_hidden = False
    question.hidden_at = None
    question.save(update_fields=['moderation_hidden', 'hidden_at'])
    ModerationAction.objects.create(question=question, action='restore', actor=actor)
    return question
