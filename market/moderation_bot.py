"""Separate, allowlisted moderation bot. Never uses the public bot's token."""
import logging
import hashlib
import re
from datetime import timedelta

import requests
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone
from . import moderation
from .models import MarketComplaint, ModerationAction, ModerationDelivery, ModerationPanel, Question
from .services import MarketError

log = logging.getLogger(__name__)
PAGE_SIZE = 5


class ModerationTelegramError(Exception):
    pass


class MissingMessage(ModerationTelegramError):
    pass


def api(method: str, *, http_timeout=10, **data):
    token = settings.MODERATION_BOT_TOKEN
    if not token or token == settings.TELEGRAM_BOT_TOKEN:
        raise ModerationTelegramError("Настройте отдельный MODERATION_BOT_TOKEN.")
    try:
        response = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=data, timeout=http_timeout)
        result = response.json()
        if isinstance(result, dict) and method == 'editMessageText' and result.get('error_code') == 400:
            description = str(result.get('description', '')).lower()
            if 'message is not modified' in description:
                return {'message_id': data['message_id']}
            if 'message to edit not found' in description or "message can't be edited" in description:
                raise MissingMessage('Сообщение удалено или больше не редактируется.')
        if not isinstance(result, dict) or not response.ok or not result.get('ok') or 'result' not in result:
            raise ModerationTelegramError("Telegram отклонил запрос бота модерации.")
        return result['result']
    except (requests.RequestException, ValueError):
        # requests exceptions may contain the secret URL: never log or expose them.
        raise ModerationTelegramError("Не удалось связаться с ботом модерации в Telegram.") from None


def send(chat_id: int, text: str, buttons=None):
    # Telegram counts UTF-16 units, not Python code points (e.g. emoji take two).
    text = text.encode('utf-16-le')[:7800].decode('utf-16-le', errors='ignore')
    return api('sendMessage', chat_id=chat_id, text=text,
               disable_web_page_preview=True, reply_markup={'inline_keyboard': buttons or []})


def _panel(chat_id: int):
    fingerprint = hashlib.sha256(settings.MODERATION_BOT_TOKEN.encode()).hexdigest()
    return ModerationPanel.objects.get_or_create(token_fingerprint=fingerprint, admin_id=chat_id)[0]


def render(chat_id: int, text: str, buttons=None, *, message_id=None, notice=False):
    """One persistent navigation panel and one compact notification, per admin and bot."""
    panel = _panel(chat_id)
    field = 'notice_id' if notice else 'message_id'
    if not notice and message_id and message_id != panel.message_id:
        old_id = panel.message_id
        panel.message_id = message_id
        if panel.notice_id == message_id:
            panel.notice_id = None
        panel.save(update_fields=['message_id', 'notice_id'])
        if old_id:
            try:
                # Only our previously recorded panel, never arbitrary chat history.
                api('deleteMessage', chat_id=chat_id, message_id=old_id)
            except ModerationTelegramError:
                pass
    current = getattr(panel, field)
    text = text.encode('utf-16-le')[:7800].decode('utf-16-le', errors='ignore')
    if current:
        try:
            return api('editMessageText', chat_id=chat_id, message_id=current, text=text,
                       disable_web_page_preview=True, reply_markup={'inline_keyboard': buttons or []})
        except MissingMessage:
            pass
    result = send(chat_id, text, buttons)
    if isinstance(result, dict) and type(result.get('message_id')) is int:
        setattr(panel, field, result['message_id'])
        panel.save(update_fields=[field])
    return result


def navigation():
    return [[{'text': '⚖️ Споры об исходе', 'callback_data': 'ds:queue:0'},
             {'text': '📜 История споров', 'callback_data': 'ds:history:0'}],
            [{'text': '📥 Жалобы', 'callback_data': 'mb:queue:0'},
             {'text': '🕘 История', 'callback_data': 'mb:history:0'}],
            [{'text': '⌂ Главное меню', 'callback_data': 'mb:home'}]]


def renew_panel(chat_id: int):
    """Commands must visibly reply, even after Telegram clears history only on the client.

    Telegram can still accept edits of a message no longer visible in the user's history.
    Inline navigation edits in place; an explicit command creates a fresh panel instead.
    """
    panel = _panel(chat_id)
    old_ids = {value for value in (panel.message_id, panel.notice_id) if value}
    panel.message_id = None
    panel.notice_id = None
    panel.save(update_fields=['message_id', 'notice_id'])
    for message_id in old_ids:
        try:
            api('deleteMessage', chat_id=chat_id, message_id=message_id)
        except ModerationTelegramError:
            # Old/already deleted messages must never prevent a new visible response.
            pass


def show_home(chat_id, *, message_id=None):
    count = Question.objects.filter(complaints__status='pending').distinct().count()
    disputes = Question.objects.filter(resolution_state=Question.Resolution.DISPUTED).count()
    render(chat_id, f'🛡 МОДЕРАЦИЯ\n\nЖалобы: {count} рынков.\nСпоры об исходе: {disputes} рынков.\n\n⚖️ Споры — проверка результата и выплаты\n📥 Жалобы — нарушения правил\n\nВыберите раздел кнопкой ниже.', navigation(), message_id=message_id)


def card(question: Question, complaint=None):
    pending = question.complaints.filter(status=MarketComplaint.Status.PENDING).order_by('-id')
    count = pending.count()
    rows = list(pending[:6])
    latest = question.moderation_actions.first()
    title = f"Рынок #{question.pk}: {question.title}"
    visibility = 'скрыт' if question.hidden_at else 'виден в ленте'
    text = f"{title}\nСтатус: {question.get_status_display()}, {visibility}.\nНовых жалоб: {count}. Всего: {question.complaints.count()}."
    text += f"\nКритерий результата: {question.resolution_criteria[:600] or 'Не указан'}"
    if complaint is not None:
        text += f"\n\nЖалоба #{complaint.pk}: {complaint.get_status_display()}\nПричина: {complaint.reason or 'Не указана'}"
    for row in rows:
        if complaint is None or row.pk != complaint.pk:
            text += f"\n• #{row.pk}: {row.reason or 'Причина не указана'}"
    if not rows and complaint is None:
        for row in question.complaints.order_by('-id')[:6]:
            text += f"\n• #{row.pk} ({row.get_status_display()}): {row.reason or 'Причина не указана'}"
    if count > len(rows):
        text += '\nОстальные жалобы доступны в Django-админке.'
    if latest:
        decision = {'hide': 'Нарушение есть — рынок скрыт', 'cancel': 'Рынок отменён с возвратом участникам', 'dismiss': 'Новые жалобы без оснований', 'restore': 'Рынок возвращён в ленту'}[latest.action]
        text += f"\nПоследнее решение: {decision}, {latest.actor}, {timezone.localtime(latest.created_at):%d.%m %H:%M}."
    buttons = []
    if rows:
        target = rows[0].pk
        dismiss_text = '✅ Новые жалобы без оснований' if question.moderation_hidden else '✅ Оставить рынок — нарушения нет'
        buttons += [[{'text': '🚫 Скрыть рынок — есть нарушение', 'callback_data': f'mb:ask:hide:{target}'}],
                    [{'text': dismiss_text, 'callback_data': f'mb:ask:dismiss:{target}'}]]
    if question.status in (Question.Status.OPEN, Question.Status.CLOSED) and question.is_trading:
        cancel_report = rows[0] if rows else question.complaints.filter(status=MarketComplaint.Status.UPHELD).order_by('-id').first()
        if cancel_report:
            buttons.append([{'text': '⛔ Отменить рынок и вернуть монеты', 'callback_data': f'mb:ask:cancel:{cancel_report.pk}'}])
    if not rows and question.hidden_at and question.status != Question.Status.ANNULLED:
        buttons.append([{'text': 'Вернуть в ленту', 'callback_data': f'mb:ask:restore:{question.pk}:{latest.pk if latest else 0}'}])
    buttons.append([{'text': 'Django-админка', 'url': f'{settings.SITE_URL}/admin/market/question/{question.pk}/change/'}])
    return text[:3900], buttons


def show_card(chat_id: int, question: Question, complaint=None, *, message_id=None, result=''):
    text, buttons = card(question, complaint)
    render(chat_id, f'{result}\n\n{text}' if result else text, buttons+navigation(), message_id=message_id)


def show_queue(chat_id: int, page=0, *, history=False, message_id=None):
    if history:
        questions = Question.objects.filter(moderation_actions__isnull=False).distinct().order_by('-id')
    else:
        questions = Question.objects.filter(complaints__status=MarketComplaint.Status.PENDING).distinct().order_by('-id')
    total = questions.count()
    page = min(max(0, page), max(0, (total-1)//PAGE_SIZE))
    items = list(questions[page*PAGE_SIZE:(page+1)*PAGE_SIZE])
    name = 'История рассмотренных рынков' if history else 'Очередь жалоб'
    buttons = [[{'text': f'#{q.pk} · {q.title[:46]}', 'callback_data': f'mb:open:{q.pk}'}] for q in items]
    pages = []
    kind = 'history' if history else 'queue'
    if page:
        pages.append({'text': '← Назад', 'callback_data': f'mb:{kind}:{page-1}'})
    if (page+1)*PAGE_SIZE < total:
        pages.append({'text': 'Далее →', 'callback_data': f'mb:{kind}:{page+1}'})
    if pages:
        buttons.append(pages)
    heading = '🕘 ИСТОРИЯ РЕШЕНИЙ' if history else '📥 НОВЫЕ ЖАЛОБЫ'
    text = f'{heading}\n\n{name}: {total}. Страница {page+1}.\nВыберите рынок для просмотра причин и действий.' if total else f'{heading}\n\nПока пусто.'
    render(chat_id, text, buttons+navigation(), message_id=message_id)


def show_notice(chat_id, report=None):
    count = Question.objects.filter(complaints__status='pending').distinct().count()
    text = f'🔔 ЖАЛОБЫ · УВЕДОМЛЕНИЕ\n\nВ очереди: {count} рынков.'
    disputes = Question.objects.filter(resolution_state=Question.Resolution.DISPUTED).count()
    text += f'\nСпоры об исходе: {disputes} рынков.'
    if report:
        text += f'\nПоследнее обращение: жалоба #{report.pk} на рынок #{report.question_id}.'
    elif not count:
        text += '\n✅ Все жалобы рассмотрены.'
    render(chat_id, text, [[{'text': '📥 Открыть очередь', 'callback_data': 'mb:queue:0'}],
                          [{'text': '⚖️ Споры об исходе', 'callback_data': 'ds:queue:0'}]], notice=True)


def _answer(callback, text=''):
    try:
        api('answerCallbackQuery', callback_query_id=callback['id'], text=text[:180])
    except ModerationTelegramError:
        pass


def handle_update(update: dict):
    callback = update.get('callback_query')
    message = callback.get('message', {}) if callback else update.get('message', {})
    sender = (callback or message).get('from') or {}
    chat = message.get('chat') or {}
    sender_id = sender.get('id')
    private = chat.get('type') == 'private' and type(sender_id) is int and chat.get('id') == sender_id and not sender.get('is_bot')
    text = message.get('text', '').strip()
    if not private:
        if callback:
            _answer(callback, 'Доступ только в личном чате администратора.')
        return
    if sender_id not in moderation.admin_ids():
        if callback:
            _answer(callback, 'Нет доступа.')
        else:
            send(sender_id, f'Доступ только для модераторов. Ваш Telegram ID: {sender_id}.')
        return
    if not callback:
        renew_panel(sender_id)
        command = text.split(maxsplit=1)[0].split('@')[0] if text else ''
        if command == '/queue':
            show_queue(sender_id)
        elif command == '/history':
            show_queue(sender_id, history=True)
        elif command in ('/disputes', '/dispute_history'):
            from .disputes_bot import show_queue as show_disputes
            show_disputes(sender_id, history=command == '/dispute_history')
        elif command == '/id':
            render(sender_id, f'Ваш Telegram ID: {sender_id}.', navigation())
        else:
            show_home(sender_id)
        return
    _answer(callback)
    data = callback.get('data', '')
    panel_id = message.get('message_id')
    if data.startswith('ds:'):
        from .disputes_bot import handle_callback
        handle_callback(sender_id, data, message_id=panel_id)
        return
    if data == 'mb:home':
        show_home(sender_id, message_id=panel_id)
        return
    opened = re.fullmatch(r'mb:open:(\d{1,18})', data)
    if opened:
        question = Question.objects.filter(pk=int(opened[1])).first()
        if question:
            show_card(sender_id, question, message_id=panel_id)
        else:
            render(sender_id, 'Рынок больше не существует.', navigation(), message_id=panel_id)
        return
    queue = re.fullmatch(r'mb:(queue|history):(\d{1,8})', data)
    if queue:
        show_queue(sender_id, int(queue[2]), history=queue[1] == 'history', message_id=panel_id)
        return
    match = re.fullmatch(r'mb:(ask|do):(hide|dismiss|restore|cancel):(\d{1,18})(?::(\d{1,18}))?', data)
    if not match or (match[2] == 'restore') != bool(match[4]):
        render(sender_id, 'Кнопка устарела. Выберите раздел ниже.', navigation(), message_id=panel_id)
        return
    stage, action, target, version = match.groups()
    try:
        if action == 'restore':
            question = Question.objects.get(pk=int(target))
        else:
            report = MarketComplaint.objects.select_related('question').get(pk=int(target))
            question = report.question
            if report.status != MarketComplaint.Status.PENDING and not (action == 'cancel' and report.status == MarketComplaint.Status.UPHELD):
                raise MarketError('Жалоба уже рассмотрена. Обновите очередь.')
        if action == 'cancel' and question.status not in (Question.Status.OPEN, Question.Status.CLOSED):
            raise MarketError('Рынок уже завершён или отменён. Повторный возврат невозможен.')
        if stage == 'ask':
            labels = {'hide': '🚫 СКРЫТЬ РЫНОК БЕЗ ВОЗВРАТОВ', 'cancel': '⛔ ОТМЕНИТЬ РЫНОК С ВОЗВРАТОМ', 'dismiss': '✅ НАРУШЕНИЕ НЕ ПОДТВЕРЖДЕНО', 'restore': 'ВЕРНУТЬ РЫНОК В ЛЕНТУ'}
            effects = {
                'hide': 'Вы считаете жалобы обоснованными. Все новые жалобы будут подтверждены, рынок будет скрыт из ленты.',
                'dismiss': 'Вы считаете новые жалобы необоснованными. Они будут отклонены; автоматическое скрытие будет снято.',
                'restore': 'Прежнее скрытие будет снято. Это не открывает заново истёкший дедлайн или завершённый рынок.',
                'cancel': 'Рынок будет окончательно отменён. Участникам вернутся вложения с комиссией за вычетом уже полученного от продаж. Уже полученная прибыль не отбирается, повторно не выплачивается.\n\nАвтор теряет стоимость создания рынка и не получает комиссию. Открытые залоги за споры возвращаются. Если резерва не хватит, платформа добавит виртуальные монеты.\n\nВосстановить этот рынок после возвратов нельзя.',
            }
            confirms = {'hide': 'Да, скрыть рынок', 'cancel': 'Да, отменить и вернуть монеты', 'dismiss': 'Да, оставить рынок', 'restore': 'Да, вернуть в ленту'}
            if action == 'dismiss' and question.moderation_hidden:
                effects['dismiss'] = 'Новые жалобы будут отклонены. Рынок ОСТАНЕТСЯ СКРЫТЫМ по прежнему решению модератора. Для восстановления есть отдельная кнопка в истории.'
                confirms['dismiss'] = 'Закрыть новые жалобы'
                if question.status == Question.Status.ANNULLED:
                    effects['dismiss'] = 'Новые жалобы будут отклонены. Рынок останется отменённым: возвраты уже выполнены, восстановление невозможно.'
            money_note = '' if action == 'cancel' else '\n\nБалансы и исход рынка не изменятся.'
            render(sender_id, f"⚠️ ПОДТВЕРЖДЕНИЕ\n{labels[action]}\n\nРынок #{question.pk} «{question.title}»\n\n{effects[action]}{money_note}",
                 [[{'text': confirms[action], 'callback_data': data.replace('mb:ask:', 'mb:do:', 1)}],
                  [{'text': '← Отмена · к рынку', 'callback_data': f'mb:open:{question.pk}'}]], message_id=panel_id)
            return
        if action == 'restore':
            question = moderation.restore(int(target), expected_action=int(version), telegram_id=sender_id)
        else:
            question = moderation.review(int(target), action, telegram_id=sender_id)
        labels = {'hide': 'Рынок скрыт: нарушение подтверждено, жалобы рассмотрены.',
                  'cancel': 'Рынок отменён. Полные возвраты участникам начислены, стоимость создания автору не возвращается.',
                  'dismiss': 'Нарушение по новым жалобам не подтверждено. Жалобы закрыты; рынок не скрыт модерацией.',
                  'restore': 'Рынок возвращён в ленту.'}
        if action == 'dismiss' and question.moderation_hidden:
            labels['dismiss'] = 'Новые жалобы закрыты без подтверждения нарушения. Рынок остаётся скрытым по прежнему решению.'
        show_card(sender_id, question, message_id=panel_id, result='✅ '+labels[action])
        if _panel(sender_id).notice_id:
            show_notice(sender_id)
    except (Question.DoesNotExist, MarketComplaint.DoesNotExist):
        render(sender_id, 'Рынок или жалоба больше не существует.', navigation(), message_id=panel_id)
    except (MarketError, PermissionDenied) as exc:
        render(sender_id, str(exc), navigation(), message_id=panel_id)


def flush_notifications(limit=10) -> int:
    """Durable per-admin deliveries, including pre-existing reports; Telegram is outside transactions."""
    ids = moderation.admin_ids()
    for admin_id in ids:
        missing = MarketComplaint.objects.exclude(moderation_deliveries__admin_id=admin_id).order_by('id')[:100]
        for complaint in missing:
            ModerationDelivery.objects.get_or_create(complaint=complaint, admin_id=admin_id)
    sent = 0
    for _ in range(limit):
        now = timezone.now()
        with transaction.atomic():
            delivery = ModerationDelivery.objects.select_for_update().filter(
                admin_id__in=ids, sent_at__isnull=True, next_attempt__lte=now).order_by('id').first()
            if not delivery:
                break
            delivery.attempts += 1
            delivery.next_attempt = now+timedelta(minutes=2)
            delivery.save(update_fields=['attempts', 'next_attempt'])
        try:
            report = MarketComplaint.objects.select_related('question').get(pk=delivery.complaint_id)
            show_notice(delivery.admin_id, report)
        except ModerationTelegramError:
            ModerationDelivery.objects.filter(pk=delivery.pk, attempts=delivery.attempts).update(
                next_attempt=timezone.now()+timedelta(seconds=min(3600, 15*2**min(delivery.attempts, 8))))
            log.warning('Moderation delivery %s failed; retry scheduled', delivery.pk)
        except MarketComplaint.DoesNotExist:
            continue
        else:
            ModerationDelivery.objects.filter(pk=delivery.pk, attempts=delivery.attempts).update(sent_at=timezone.now())
            sent += 1
    from .disputes_bot import flush_notifications as flush_disputes
    return sent + flush_disputes(limit=limit)
