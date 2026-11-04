"""Manual publication review through the private, allowlisted moderation bot."""
import hashlib
import json
import re
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from django.conf import settings
from . import moderation, moderation_bot as bot, premoderation as gate
from .models import Question, PublicationDecision, PublicationDelivery
from .services import MarketError

REASONS = {
    'uncertain': 'Модель не уверена в решении',
    'sensitive': 'Чувствительная тема или недостаточно контекста',
    'external_link': 'Нужно проверить внешний источник',
    'too_large': 'Слишком большой текст для автоматической проверки',
    'unavailable': 'Автоматическая проверка недоступна',
    'expired': 'Истёк срок или рынок уже закрыт',
}


def queue():
    return Question.objects.filter(parent__isnull=True, publication_state__in=('review', 'error')).order_by('created_at', 'pk')


def show_queue(chat_id, page=0, *, message_id=None):
    moderation._actor(telegram_id=chat_id)
    total = queue().count()
    page = min(max(0, page), max(0, (total-1)//bot.PAGE_SIZE))
    rows = [[{'text': f'#{q.pk} · {q.title[:46]}', 'callback_data': f'pc:open:{q.pk}'}]
            for q in queue()[page*bot.PAGE_SIZE:(page+1)*bot.PAGE_SIZE]]
    paging = []
    if page:
        paging.append({'text': '← Назад', 'callback_data': f'pc:queue:{page-1}'})
    if (page+1)*bot.PAGE_SIZE < total:
        paging.append({'text': 'Далее →', 'callback_data': f'pc:queue:{page+1}'})
    if paging:
        rows.append(paging)
    bot.render(chat_id, f'📝 ПРОВЕРКА ПУБЛИКАЦИИ\n\nОжидают решения: {total}. Страница {page+1}.', rows+bot.navigation(), message_id=message_id)


def reviewed_version(pk, decision_id=None):
    q = Question.objects.get(pk=pk, parent__isnull=True)
    decision = q.publication_decisions.order_by('-pk').first()
    if (q.publication_state not in ('review', 'error') or not decision
            or decision.decision != q.publication_state
            or decision_id is not None and decision.pk != decision_id
            or gate.digest(gate.snapshot(q)) != decision.content_hash):
        raise MarketError('Текст или состояние изменились. Откройте очередь заново.')
    return q, decision


def show_card(chat_id, pk, *, message_id=None):
    moderation._actor(telegram_id=chat_id)
    q, decision = reviewed_version(pk)
    # Split complete content into pages, never silently truncate rules/options before approval.
    content = json.dumps(gate.snapshot(q), ensure_ascii=False, indent=2, default=str)
    chunks = [content[i:i+1400] for i in range(0, len(content), 1400)]
    for i, chunk in enumerate(chunks, 1):
        bot.send(chat_id, f'Рынок #{q.pk} · текст {i}/{len(chunks)}\n{chunk}')
    bot.render(chat_id, f'📝 Рынок #{q.pk}\nСостояние: {q.get_publication_state_display()}\nПричина: {REASONS.get(decision.reason, decision.reason)}\n\nПолный текст и варианты отправлены выше. Одобрение публикует рынок. Отклонение отменяет его и возвращает резерв создания.',
        [[{'text': '✅ Одобрить', 'callback_data': f'pc:ask:approved:{decision.pk}'},
          {'text': '⛔ Отклонить', 'callback_data': f'pc:ask:rejected:{decision.pk}'}]]+bot.navigation(), message_id=message_id)


@transaction.atomic
def decide(chat_id, decision_id, state):
    actor = moderation._actor(telegram_id=chat_id)
    if state not in ('approved', 'rejected'):
        raise MarketError('Неизвестное решение.')
    decision = PublicationDecision.objects.get(pk=decision_id)
    Question.objects.select_for_update().get(pk=decision.question_id)
    q, decision = reviewed_version(decision.question_id, decision_id)
    return gate.apply_decision(q.pk, state, 'manual', actor=actor, expected_hash=decision.content_hash)


def handle_callback(chat_id, data, *, message_id=None):
    moderation._actor(telegram_id=chat_id)
    try:
        match = re.fullmatch(r'pc:queue:(\d{1,8})', data)
        if match:
            show_queue(chat_id, int(match[1]), message_id=message_id)
            return
        match = re.fullmatch(r'pc:open:(\d{1,18})', data)
        if match:
            show_card(chat_id, int(match[1]), message_id=message_id)
            return
        match = re.fullmatch(r'pc:(ask|do):(approved|rejected):(\d{1,18})', data)
        if not match:
            raise MarketError('Кнопка устарела. Откройте очередь заново.')
        stage, state, version = match.groups()
        decision = PublicationDecision.objects.get(pk=int(version))
        q, _ = reviewed_version(decision.question_id, int(version))
        if stage == 'ask':
            label = 'Опубликовать' if state == 'approved' else 'Отклонить и вернуть резерв'
            bot.render(chat_id, f'Подтвердить: {label.lower()} рынок #{q.pk}?\n{q.title}',
                [[{'text': label, 'callback_data': f'pc:do:{state}:{version}'}],
                 [{'text': '← Проверить текст', 'callback_data': f'pc:open:{q.pk}'}]], message_id=message_id)
            return
        changed = decide(chat_id, int(version), state)
        q.refresh_from_db()
        result = f'Решение сохранено: {q.get_publication_state_display()}.' if changed else 'Состояние изменилось. Обновите очередь.'
        bot.render(chat_id, result, bot.navigation(), message_id=message_id)
    except (Question.DoesNotExist, PublicationDecision.DoesNotExist, MarketError) as exc:
        text = str(exc) if isinstance(exc, MarketError) else 'Рынок или решение не найдены.'
        bot.render(chat_id, text, bot.navigation(), message_id=message_id)


def flush_notifications(limit=10):
    ids = moderation.admin_ids()
    fingerprint = hashlib.sha256(settings.MODERATION_BOT_TOKEN.encode()).hexdigest()
    for admin_id in ids:
        missing = PublicationDecision.objects.filter(decision__in=('review', 'error'),
            question__parent__isnull=True, question__publication_state__in=('review', 'error')).exclude(
            pk__in=PublicationDelivery.objects.filter(admin_id=admin_id, token_fingerprint=fingerprint).values('decision_id')).order_by('pk')[:100]
        for decision in missing:
            PublicationDelivery.objects.get_or_create(decision=decision, admin_id=admin_id, token_fingerprint=fingerprint)
    sent = 0
    for _ in range(limit):
        now = timezone.now()
        with transaction.atomic():
            delivery = PublicationDelivery.objects.select_for_update().filter(admin_id__in=ids,
                token_fingerprint=fingerprint, sent_at__isnull=True, next_attempt__lte=now).order_by('pk').first()
            if not delivery:
                break
            delivery.attempts += 1
            delivery.next_attempt = now+timedelta(minutes=2)
            delivery.save(update_fields=['attempts', 'next_attempt'])
        try:
            decision = delivery.decision
            q, _ = reviewed_version(decision.question_id, decision.pk)
            bot.render(delivery.admin_id, f'🔔 ПРОВЕРКА ПУБЛИКАЦИИ\n\nРынок #{q.pk}: {q.title[:300]}\nПричина: {REASONS.get(decision.reason, decision.reason)}\nВ очереди: {queue().count()}.',
                [[{'text': 'Проверить рынок', 'callback_data': f'pc:open:{q.pk}'}]]+bot.navigation(), notice=True)
        except bot.ModerationTelegramError:
            PublicationDelivery.objects.filter(pk=delivery.pk).update(
                next_attempt=timezone.now()+timedelta(seconds=min(3600, 15*2**min(delivery.attempts, 8))))
        except (MarketError, Question.DoesNotExist):
            PublicationDelivery.objects.filter(pk=delivery.pk).update(sent_at=timezone.now())
        else:
            PublicationDelivery.objects.filter(pk=delivery.pk).update(sent_at=timezone.now())
            sent += 1
    return sent
