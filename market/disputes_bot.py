"""Dispute workspace in the private moderation bot; settlement stays in services."""
import re
from datetime import timedelta
import requests

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from . import moderation, moderation_bot as bot
from .models import Dispute, DisputeDelivery, Question
from .services import MarketError, by_participant, evidence_deadline, has_evidence

LABELS = {'yes': 'ДА', 'no': 'НЕТ', 'cancel': 'ОТМЕНА'}


def show_queue(chat_id, page=0, *, history=False, message_id=None):
    questions = Question.objects.filter(disputes__upheld__isnull=False) if history else Question.objects.filter(
        resolution_state=Question.Resolution.DISPUTED, disputes__upheld__isnull=True)
    questions = questions.filter(disputes__isnull=False).distinct().order_by('-pk')
    total = questions.count()
    page = min(max(page, 0), max(0, (total-1)//bot.PAGE_SIZE))
    kind = 'history' if history else 'queue'
    rows = [[{'text': f'#{q.pk} · {q.title[:46]}', 'callback_data': f'ds:open:{q.pk}'}]
            for q in questions[page*bot.PAGE_SIZE:(page+1)*bot.PAGE_SIZE]]
    paging = []
    if page:
        paging.append({'text': '← Назад', 'callback_data': f'ds:{kind}:{page-1}'})
    if (page+1)*bot.PAGE_SIZE < total:
        paging.append({'text': 'Далее →', 'callback_data': f'ds:{kind}:{page+1}'})
    if paging:
        rows.append(paging)
    heading = '📜 ИСТОРИЯ СПОРОВ' if history else '⚖️ СПОРЫ ОБ ИСХОДЕ'
    bot.render(chat_id, f'{heading}\n\nРынков: {total}. Страница {page+1}.\nВыберите рынок.' if total else f'{heading}\n\nПока пусто.',
               rows+bot.navigation(), message_id=message_id)


def show_card(chat_id, question, page=0, *, message_id=None, result=''):
    disputes = question.disputes.select_related('trader').order_by('pk')
    total = disputes.count()
    page = min(max(page, 0), max(0, (total-1)//3))
    subs = list(question.submarkets.all().order_by('id'))
    if subs and question.proposed_outcome != 'cancel':
        proposed_str = ', '.join(f'{s.option_label or s.title}: {LABELS.get(s.proposed_outcome, "—")}' for s in subs)
    else:
        proposed_str = LABELS.get(question.proposed_outcome, "Не указан")
    if by_participant(question):
        proposer = question.proposed_by
        who = f'участник {proposer.pseudonym or proposer.name} (автор рынка не указал итог)'
    else:
        who = 'автор рынка'
    proof = 'Доказательство: прислано' if has_evidence(question) else 'Доказательство не прислано'
    deadline = evidence_deadline(question)
    if deadline and not has_evidence(question):
        proof += f' (ждём до {timezone.localtime(deadline):%d.%m %H:%M})' if deadline > timezone.now() else ' (срок вышел)'
    text = (f'⚖️ СПОР ОБ ИСХОДЕ\n\nРынок #{question.pk}: {question.title[:250]}\n'
            f'Критерий: {question.resolution_criteria[:600] or "Не указан"}\n'
            f'Итог указал: {who}\n'
            f'Указанный итог: {proposed_str}\n'
            f'Комментарий: {question.evidence_note[:500] or "Нет"}\n'
            f'{proof}\n'
            f'Обращений: {total}. Страница {page+1}.')
    for dispute in disputes[page*3:(page+1)*3]:
        status = 'Ожидает решения' if dispute.upheld is None else ('Признан' if dispute.upheld else 'Отклонён')
        text += f'\n\n#{dispute.pk} · {dispute.trader.pseudonym or dispute.trader.name}\nЗалог: {dispute.stake}. {status}.\n{dispute.reason or "Причина не указана"}'
    rows = []
    if question.evidence or question.evidence_file_id:
        rows.append([{'text': '🖼 Получить доказательство', 'callback_data': f'ds:proof:{question.pk}'}])
    rows.append([{'text': '🔎 Полная карточка и доказательство', 'url': f'{settings.SITE_URL}/admin/market/question/{question.pk}/change/'}])
    latest = disputes.filter(upheld__isnull=True).order_by('-pk').first()
    if latest and question.resolution_state == Question.Resolution.DISPUTED:
        if subs:
            if question.proposed_outcome != 'cancel':
                rows.append([{'text': 'Подтвердить указанный итог',
                              'callback_data': f'ds:ask:yes:{question.pk}:{latest.pk}'}])
            rows.append([{'text': 'Аннулировать по правилам спора',
                          'callback_data': f'ds:ask:cancel:{question.pk}:{latest.pk}'}])
        else:
            for decision, label in LABELS.items():
                caption = f'Подтвердить указанный итог: {label}' if decision == question.proposed_outcome else f'Изменить исход на {label}'
                if decision == 'cancel':
                    caption = 'Аннулировать по правилам спора'
                rows.append([{'text': caption, 'callback_data': f'ds:ask:{decision}:{question.pk}:{latest.pk}'}])
    else:
        text += f'\n\nИтог: {LABELS.get(question.outcome, question.get_status_display())}.'
        record = getattr(question, 'dispute_decision', None)
        if record:
            text += f' Решение: {LABELS[record.decision]}, {record.actor}, {timezone.localtime(record.created_at):%d.%m %H:%M}.'
    paging = []
    if page:
        paging.append({'text': '← Аргументы', 'callback_data': f'ds:card:{question.pk}:{page-1}'})
    if (page+1)*3 < total:
        paging.append({'text': 'Ещё аргументы →', 'callback_data': f'ds:card:{question.pk}:{page+1}'})
    if paging:
        rows.append(paging)
    bot.render(chat_id, (result+'\n\n' if result else '')+text, rows+bot.navigation(), message_id=message_id)


def handle_callback(chat_id, data, *, message_id=None):
    try:
        proof = re.fullmatch(r'ds:proof:(\d{1,18})', data)
        if proof:
            question = Question.objects.get(pk=int(proof[1]))
            send_proof(chat_id, question)
            return
        queue = re.fullmatch(r'ds:(queue|history):(\d{1,8})', data)
        if queue:
            show_queue(chat_id, int(queue[2]), history=queue[1] == 'history', message_id=message_id)
            return
        opened = re.fullmatch(r'ds:(open|card):(\d{1,18})(?::(\d{1,8}))?', data)
        if opened:
            show_card(chat_id, Question.objects.get(pk=int(opened[2])), int(opened[3] or 0), message_id=message_id)
            return
        action = re.fullmatch(r'ds:(ask|do):(yes|no|cancel):(\d{1,18}):(\d{1,18})', data)
        if not action:
            raise MarketError('Кнопка устарела. Откройте очередь споров.')
        stage, decision, qid, version = action.groups()
        question = Question.objects.get(pk=int(qid))
        latest = question.disputes.filter(upheld__isnull=True).order_by('-pk').first()
        if question.resolution_state != Question.Resolution.DISPUTED or not latest or latest.pk != int(version):
            raise MarketError('Споры изменились или уже рассмотрены. Откройте рынок заново.')
        if stage == 'ask':
            changed = decision == 'cancel' or decision != question.proposed_outcome
            participant = by_participant(question)
            if participant:
                effect = ('Споры признаются: залоги возвращаются, участник теряет залог за итог, автор рынка не наказывается.' if changed else
                          'Споры отклоняются: залоги не возвращаются, итог участника подтверждается, ему возвращается залог и доля комиссии.')
            else:
                effect = ('Споры признаются: залоги возвращаются, автор теряет стоимость создания.' if changed else
                          'Споры отклоняются: залоги не возвращаются, исход автора подтверждается.')
            if decision == 'cancel' and participant:
                effect += '\nРынок отменяется: вложения возвращаются, автору рынка — неиспользованная стоимость создания.'
            elif decision == 'cancel':
                effect += '\nРынок аннулируется: полностью возвращаются вложения с комиссиями за вычетом выручки от продаж. Недостающие виртуальные монеты добавит платформа. Уже полученная прибыль сохраняется.'
            else:
                effect += '\nПобедившие акции оплачиваются по правилам рынка, проигравшие — 0.'
            bot.render(chat_id, f'⚠️ ОКОНЧАТЕЛЬНОЕ РЕШЕНИЕ\n\n#{question.pk}: {question.title[:250]}\nИтог: {LABELS[decision]}\n\n{effect}\n\nВыплаты выполнятся сразу. Повторное решение невозможно.',
                       [[{'text': f'Подтвердить: {LABELS[decision]}', 'callback_data': data.replace('ds:ask:', 'ds:do:', 1)}],
                        [{'text': '← Назад без решения', 'callback_data': f'ds:open:{qid}'}]], message_id=message_id)
        else:
            question = moderation.decide_dispute(int(qid), decision, expected_dispute=int(version), telegram_id=chat_id)
            show_card(chat_id, question, message_id=message_id, result='✅ Решение принято. Расчёты выполнены.')
    except Question.DoesNotExist:
        bot.render(chat_id, 'Рынок больше не существует.', bot.navigation(), message_id=message_id)
    except (MarketError, PermissionDenied) as exc:
        bot.render(chat_id, str(exc), bot.navigation(), message_id=message_id)


def send_proof(chat_id, question):
    """Telegram file IDs belong to a bot: download with the public bot, upload with the moderator bot."""
    from . import telegram
    token = settings.MODERATION_BOT_TOKEN
    if not token or token == settings.TELEGRAM_BOT_TOKEN:
        raise MarketError('Настройте отдельный токен бота модерации.')
    if not question.evidence and not question.evidence_file_id:
        raise MarketError('Доказательство не найдено. Откройте карточку заново.')
    try:
        if question.evidence:
            with question.evidence.open('rb') as evidence:
                content = evidence.read(telegram.MAX_DOWNLOAD_BYTES+1)
            path = question.evidence.name
            if len(content) > telegram.MAX_DOWNLOAD_BYTES:
                raise MarketError('Файл слишком большой для отправки ботом.')
        else:
            content, path = telegram.download_file(question.evidence_file_id)
        response = requests.post(f'https://api.telegram.org/bot{token}/sendDocument',
            data={'chat_id': chat_id, 'caption': f'Доказательство автора · рынок #{question.pk}'},
            files={'document': (path.rsplit('/', 1)[-1], content)}, timeout=30)
        result = response.json()
        if not response.ok or not result.get('ok'):
            raise MarketError('Telegram не принял доказательство. Попробуйте позже.')
    except (telegram.TelegramError, requests.RequestException, ValueError, OSError):
        raise MarketError('Не удалось получить доказательство. Попробуйте позже.') from None


def flush_notifications(limit=10):
    ids = moderation.admin_ids()
    for admin_id in ids:
        for dispute in Dispute.objects.filter(upheld__isnull=True).exclude(moderation_deliveries__admin_id=admin_id).order_by('pk')[:100]:
            DisputeDelivery.objects.get_or_create(dispute=dispute, admin_id=admin_id)
    sent = 0
    for _ in range(limit):
        with transaction.atomic():
            delivery = DisputeDelivery.objects.select_for_update().filter(admin_id__in=ids, sent_at__isnull=True,
                next_attempt__lte=timezone.now()).order_by('pk').first()
            if not delivery:
                break
            delivery.attempts += 1
            delivery.next_attempt = timezone.now()+timedelta(minutes=2)
            delivery.save(update_fields=['attempts', 'next_attempt'])
        try:
            count = Question.objects.filter(resolution_state=Question.Resolution.DISPUTED).count()
            complaints = Question.objects.filter(complaints__status='pending').distinct().count()
            bot.render(delivery.admin_id, f'🔔 МОДЕРАЦИЯ\n\nСпоры об исходе: {count} рынков.\nЖалобы: {complaints} рынков.\nНовое обращение к рынку #{delivery.dispute.question_id}.', bot.navigation(), notice=True)
        except bot.ModerationTelegramError:
            DisputeDelivery.objects.filter(pk=delivery.pk, attempts=delivery.attempts).update(
                next_attempt=timezone.now()+timedelta(seconds=min(3600, 15*2**min(delivery.attempts, 8))))
        else:
            DisputeDelivery.objects.filter(pk=delivery.pk, attempts=delivery.attempts).update(sent_at=timezone.now())
            sent += 1
    return sent
