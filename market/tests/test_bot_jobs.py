"""Slow Telegram work must not run in the update poller."""
from unittest.mock import patch

import pytest
from django.db import transaction

from market.telegram import handle_update
from market.tests.test_bot_resolution_dialog import (
    bot, media, people, market, press, CREATOR, ANYA, BORIS,
)


def test_chart_click_does_not_render_in_poller(bot, people, market):
    with patch('market.charts.render_animation', side_effect=AssertionError('poller rendered chart')):
        handle_update(press(ANYA, f'mk:{market.pk}'))
    from market.models import BotJob
    job = BotJob.objects.get()
    assert job.kind == 'chart'
    assert job.trader == people[1] and job.question == market
    assert bot.calls[0][0] == 'answerCallbackQuery'
    assert not bot.sent('sendAnimation')


def prepare_cancel(market):
    from market.models import ResolutionDraft
    ResolutionDraft.objects.create(trader=market.creator, question=market, outcome='cancel', stage='preview')


def test_confirmation_queues_recipients_and_acknowledges_first(bot, people, market):
    prepare_cancel(market)
    handle_update(press(CREATOR, f'res:{market.pk}:cancel'))
    assert bot.calls[0][0] == 'answerCallbackQuery'
    assert not bot.sent(chat_id=ANYA) and not bot.sent(chat_id=BORIS)
    from market.models import BotJob
    assert BotJob.objects.filter(kind='message').count() == 2
    assert set(BotJob.objects.values_list('trader_id', flat=True)) == {people[1].pk, people[2].pk}
    handle_update(press(CREATOR, f'res:{market.pk}:cancel'))
    assert BotJob.objects.count() == 2


def test_outcome_rollback_does_not_leave_jobs(bot, people, market):
    prepare_cancel(market)
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            handle_update(press(CREATOR, f'res:{market.pk}:cancel'))
            raise RuntimeError('rollback')
    from market.models import BotJob
    assert BotJob.objects.count() == 0
    market.refresh_from_db()
    assert market.resolution_state == ''


def make_job(people, market, key='test'):
    from market.bot_jobs import enqueue_message
    return enqueue_message(key, people[1], market, 'sendMessage', {'text': 'Outcome'})


def test_worker_sends_once_and_keeps_payload(bot, people, market):
    from market.bot_jobs import process_one
    job = make_job(people, market)
    assert process_one()
    assert not process_one()
    job.refresh_from_db()
    assert job.status == 'done' and job.attempts == 1 and job.finished_at
    assert bot.sent('sendMessage', ANYA) == [{'chat_id': ANYA, 'text': 'Outcome'}]


def test_failure_retries_only_failed_recipient_and_stops_after_three(bot, people, market):
    from datetime import timedelta
    from django.utils import timezone
    from market.bot_jobs import process_one, enqueue_message
    from market.telegram import TelegramError
    first = make_job(people, market)
    second = enqueue_message('second', people[2], market, 'sendMessage', {'text': 'Other'})
    now = timezone.now()
    def send(method, **data):
        if data['chat_id'] == ANYA:
            raise TelegramError('failure')
        return bot(method, **data)
    with patch('market.telegram.api', side_effect=send):
        assert process_one(now)
        assert process_one(now)
        assert not process_one(now)
        assert process_one(now + timedelta(seconds=6))
        assert process_one(now + timedelta(seconds=40))
        assert not process_one(now + timedelta(hours=1))
    first.refresh_from_db(); second.refresh_from_db()
    assert first.status == 'failed' and first.attempts == 3
    assert second.status == 'done' and second.attempts == 1
    assert len(bot.sent('sendMessage', BORIS)) == 1


def test_live_lease_is_not_claimed_and_expired_lease_recovers(bot, people, market):
    from datetime import timedelta
    from django.utils import timezone
    from market.bot_jobs import claim, process_one
    job = make_job(people, market)
    now = timezone.now()
    claimed = claim(now)
    assert claimed.pk == job.pk
    assert not process_one(now + timedelta(seconds=10))
    assert process_one(now + timedelta(minutes=16))
    job.refresh_from_db()
    assert job.status == 'done' and job.attempts == 2


def test_stale_worker_cannot_finish_reclaimed_job(bot, people, market):
    from datetime import timedelta
    from django.utils import timezone
    from market.bot_jobs import claim, finish
    job = make_job(people, market)
    now = timezone.now()
    old = claim(now)
    fresh = claim(now + timedelta(minutes=16))
    finish(old, 'done', now)
    job.refresh_from_db()
    assert job.status == 'running' and job.lease_token == fresh.lease_token


def test_exhausted_crashed_job_is_failed_without_resend(bot, people, market):
    from datetime import timedelta
    from django.utils import timezone
    from market.bot_jobs import process_one
    job = make_job(people, market)
    job.status = 'running'; job.attempts = 3
    job.lease_until = timezone.now() - timedelta(seconds=1)
    job.save()
    assert process_one()
    job.refresh_from_db()
    assert job.status == 'failed' and not bot.calls


@pytest.mark.parametrize('revoke', ['chat', 'access', 'hidden'])
def test_worker_rechecks_access_and_recipient(bot, people, market, revoke):
    from market.bot_jobs import process_one
    from market.models import Group, Question
    job = make_job(people, market)
    if revoke == 'chat':
        people[1].telegram_chat_id = 999
        people[1].save()
    elif revoke == 'access':
        market.group = Group.objects.create(name='Private', owner=people[0])
        market.save()
    else:
        Question.objects.filter(pk=market.pk).update(publication_state='pending')
    assert process_one()
    job.refresh_from_db()
    assert job.status == 'skipped' and not bot.calls


def test_chart_job_renders_only_in_worker_and_duplicate_update_is_deduplicated(bot, people, market):
    from market.bot_jobs import process_one
    from market.models import BotJob
    update = press(ANYA, f'mk:{market.pk}')
    handle_update(update); handle_update(update)
    assert BotJob.objects.count() == 1
    from market.charts import ChartError
    with patch('market.charts.render_animation', side_effect=ChartError('no renderer')):
        assert process_one()
    assert bot.sent('sendMessage', ANYA)[-1]['text'].startswith(market.title)
    assert BotJob.objects.get().status == 'done'


@pytest.mark.django_db(transaction=True)
def test_worker_command_once_drains_ready_jobs(bot, people, market):
    from django.core.management import call_command
    from market.models import BotJob
    make_job(people, market, 'a'); make_job(people, market, 'b')
    call_command('run_bot_jobs', once=True)
    assert BotJob.objects.filter(status='done').count() == 2


@pytest.mark.django_db(transaction=True)
def test_postgres_slow_delivery_does_not_block_poller_or_allow_duplicate_claim(bot, people, market):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from django.db import connection, connections
    from market.bot_jobs import process_one
    from market.tests.test_bot_resolution_dialog import text
    if connection.vendor != 'postgresql':
        pytest.skip('PostgreSQL row-lock concurrency test')
    job = make_job(people, market)
    entered, release = Event(), Event()
    def send(method, **data):
        if data.get('text') == 'Outcome':
            entered.set()
            assert release.wait(10), 'test did not release delivery'
        return bot(method, **data)
    def work():
        try:
            return process_one()
        finally:
            connections.close_all()
    with patch('market.telegram.api', side_effect=send), ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(work)
        try:
            assert entered.wait(5)
            assert executor.submit(work).result(timeout=3) is False
            handle_update(text(BORIS, '/start'))
            assert bot.sent('sendMessage', BORIS)
        finally:
            release.set()
        assert first.result(timeout=5)
    job.refresh_from_db()
    assert job.status == 'done' and job.attempts == 1
    assert len(bot.sent('sendMessage', ANYA)) == 1


@pytest.mark.django_db(transaction=True)
def test_postgres_workers_racing_claim_each_job_once(bot, people, market):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from django.db import connection, connections
    from market.bot_jobs import process_one
    from market.models import BotJob
    if connection.vendor != 'postgresql':
        pytest.skip('PostgreSQL row-lock concurrency test')
    make_job(people, market, 'one'); make_job(people, market, 'two')
    barrier = Barrier(2)
    def work():
        try:
            barrier.wait(timeout=5)
            return process_one()
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(work) for _ in range(2)]
        assert all(f.result(timeout=5) for f in futures)
    assert BotJob.objects.filter(status='done', attempts=1).count() == 2
    assert len(bot.sent('sendMessage', ANYA)) == 2


def test_creator_delivery_failure_does_not_lose_participant_jobs(bot, people, market):
    from market.models import BotJob
    from market.telegram import TelegramError
    prepare_cancel(market)
    def send(method, **data):
        if method == 'sendMessage' and data.get('chat_id') == CREATOR:
            raise TelegramError('creator unavailable')
        return bot(method, **data)
    with patch('market.telegram.api', side_effect=send), pytest.raises(TelegramError):
        handle_update(press(CREATOR, f'res:{market.pk}:cancel'))
    market.refresh_from_db()
    assert market.resolution_state == 'pending'
    assert BotJob.objects.count() == 2


def test_queue_write_failure_rolls_back_resolution_and_keeps_draft(bot, people, market):
    from market.models import BotJob, ResolutionDraft
    prepare_cancel(market)
    with patch('market.bot_jobs.enqueue_message', side_effect=RuntimeError('storage unavailable')):
        with pytest.raises(RuntimeError):
            handle_update(press(CREATOR, f'res:{market.pk}:cancel'))
    market.refresh_from_db()
    assert market.resolution_state == ''
    assert ResolutionDraft.objects.filter(question=market).exists()
    assert not BotJob.objects.exists()
