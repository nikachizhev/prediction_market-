"""Manual publication review through the private, allowlisted moderation bot."""
import hashlib
import re
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
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
    return Question.objects.filter(gate.waiting_for_moderator(), parent__isnull=True).order_by('created_at', 'pk')


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
    if (gate.check_state(q) not in gate.WAITING or not decision
            or decision.decision != gate.check_state(q)
            or decision_id is not None and decision.pk != decision_id
            or gate.digest(gate.snapshot(q)) != decision.content_hash):
        raise MarketError('Текст или состояние изменились. Откройте очередь заново.')
    return q, decision


def readable_content(q):
    def value(item):
        if isinstance(item, dict):
            return '; '.join(f'{key}: {value(val)}' for key, val in item.items())
        if isinstance(item, (list, tuple)):
            return '\n'.join(f'• {value(val)}' for val in item)
        return str(item)

    def date(item):
        return timezone.localtime(item).strftime('%d.%m.%Y %H:%M %Z') if item else 'Не указан'

    def section(item):
        lines = [item.title, f'Критерий результата:\n{item.resolution_criteria or "Не указан"}']
        if item.option_label:
            lines.append(f'Вариант: {item.option_label}')
        if item.options:
            lines.append(f'Варианты ответа:\n{value(item.options)}')
        if item.source_type or item.source_ref:
            lines.append(f'Источник: {item.get_source_type_display()}\n{item.source_ref or "Не указан"}')
        lines += [f'Приём прогнозов до: {date(item.deadline)}',
                  f'Итог станет известен: {date(item.resolves_at)}']
        return '\n\n'.join(lines)

    parts = [section(q)]
    for i, child in enumerate(q.submarkets.order_by('pk'), 1):
        parts.append(f'ВАРИАНТ {i}\n{section(child)}')
    return '\n\n──────────\n\n'.join(parts)


def show_card(chat_id, pk, page=0, *, message_id=None):
    moderation._actor(telegram_id=chat_id)
    q, decision = reviewed_version(pk)
    content = readable_content(q)
    # 1600 Unicode characters use at most 3200 UTF-16 units, leaving room for the header.
    chunks = [content[i:i+1600] for i in range(0, len(content), 1600)]
    page = min(max(0, page), len(chunks)-1)
    rows = []
    paging = []
    if page:
        paging.append({'text': '← Текст', 'callback_data': f'pc:card:{q.pk}:{page-1}'})
    if page+1 < len(chunks):
        paging.append({'text': 'Текст →', 'callback_data': f'pc:card:{q.pk}:{page+1}'})
    if paging:
        rows.append(paging)
    rows.append([{'text': '✅ Одобрить', 'callback_data': f'pc:ask:approved:{decision.pk}'},
                 {'text': '⛔ Отклонить', 'callback_data': f'pc:ask:rejected:{decision.pk}'}])
    bot.render(chat_id, f'📝 Рынок #{q.pk} · {page+1}/{len(chunks)}\n{q.get_publication_state_display()}\nПричина: {REASONS.get(decision.reason, decision.reason)}\n\n{chunks[page]}',
               rows+bot.navigation(), message_id=message_id)


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
        match = re.fullmatch(r'pc:card:(\d{1,18}):(\d{1,8})', data)
        if match:
            show_card(chat_id, int(match[1]), int(match[2]), message_id=message_id)
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
        missing = PublicationDecision.objects.filter(
            Q(question__publication_state__in=gate.WAITING) | Q(question__background_check__in=gate.WAITING),
            decision__in=gate.WAITING, question__parent__isnull=True).exclude(
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
