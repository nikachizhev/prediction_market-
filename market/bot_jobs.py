"""Immediate outbox: chart generation and outcome broadcasts never block polling."""
import logging
import time
import uuid
from datetime import timedelta

from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from . import telegram
from .access import can_access

from .models import BotJob

log = logging.getLogger(__name__)


def enqueue_message(key, trader, question, method, payload):
    return BotJob.objects.get_or_create(key=key, defaults={
        'kind': BotJob.Kind.MESSAGE, 'trader': trader, 'question': question,
        'payload': {'method': method, **payload, 'chat_id': trader.telegram_chat_id},
    })[0]


def enqueue_chart(callback_id, trader, question):
    return BotJob.objects.get_or_create(key=f'chart:{callback_id}', defaults={
        'kind': BotJob.Kind.CHART, 'trader': trader, 'question': question,
        'payload': {'chat_id': trader.telegram_chat_id},
    })[0]


MAX_ATTEMPTS = 3
LEASE = timedelta(minutes=15)


def claim(now=None):
    now = now or timezone.now()
    ready = (Q(status=BotJob.Status.QUEUED, available_at__lte=now)
             | Q(status=BotJob.Status.RUNNING, lease_until__lte=now))
    with transaction.atomic():
        rows = BotJob.objects.filter(ready).order_by('available_at', 'pk')
        rows = rows.select_for_update(skip_locked=connection.features.has_select_for_update_skip_locked)
        job = rows.first()
        if job is None:
            return None
        job.status = BotJob.Status.RUNNING
        job.lease_token = uuid.uuid4()
        job.lease_until = now + LEASE
        if job.attempts < MAX_ATTEMPTS:
            job.attempts += 1
        else:
            # A final attempt died without acknowledging. Do not resend forever.
            job.status = BotJob.Status.FAILED
            job.finished_at = now
            job.lease_until = None
            job.lease_token = None
        job.save(update_fields=['status', 'lease_token', 'lease_until', 'attempts', 'finished_at'])
        return job


def finish(job, status, now, *, available_at=None):
    """Only the current lease owner may acknowledge or reschedule a job."""
    changes = dict(status=status, lease_token=None, lease_until=None,
                   finished_at=None if status == BotJob.Status.QUEUED else now)
    if available_at is not None:
        changes['available_at'] = available_at
    BotJob.objects.filter(pk=job.pk, status=BotJob.Status.RUNNING,
                          lease_token=job.lease_token).update(**changes)


def _deliver(job):
    trader, question = job.trader, job.question
    if (trader.telegram_chat_id != job.payload.get('chat_id')
            or not can_access(trader, question)):
        return BotJob.Status.SKIPPED
    if job.kind == BotJob.Kind.CHART:
        from .bot_markets import send_chart
        send_chart(trader.telegram_chat_id, trader, question)
    elif job.kind == BotJob.Kind.MESSAGE:
        payload = dict(job.payload)
        method = payload.pop('method')
        if method not in {'sendMessage', 'sendPhoto', 'sendDocument'}:
            raise ValueError('Unsupported bot job method')
        telegram.api(method, **payload)
    else:
        raise ValueError('Unsupported bot job kind')
    return BotJob.Status.DONE


def process_one(now=None):
    """Claim one job, send outside the DB transaction, then acknowledge its lease.

    Bounded at-least-once delivery: Telegram has no idempotency key, so a crash
    after a successful send or ambiguous network failure can duplicate a message.
    """
    job = claim(now)
    if job is None:
        return False
    if job.status == BotJob.Status.FAILED:
        log.warning('Bot job %s exhausted after worker interruption', job.pk)
        return True
    started = time.monotonic()
    try:
        status = _deliver(job)
    except Exception as exc:
        # Never log exception text: requests errors may contain the bot token.
        log.warning('Bot job %s (%s) attempt %s failed: %s',
                    job.pk, job.kind, job.attempts, type(exc).__name__)
        completed = now or timezone.now()
        if job.attempts >= MAX_ATTEMPTS:
            finish(job, BotJob.Status.FAILED, completed)
        else:
            delay = 5 if job.attempts == 1 else 30
            finish(job, BotJob.Status.QUEUED, completed,
                   available_at=completed + timedelta(seconds=delay))
    else:
        finish(job, status, now or timezone.now())
        log.info('Bot job %s (%s) %s in %.3fs', job.pk, job.kind, status, time.monotonic() - started)
    return True
