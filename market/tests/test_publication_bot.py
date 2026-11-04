from unittest.mock import patch
import pytest
from django.core.exceptions import PermissionDenied
from market import premoderation as gate, publication_bot as pub, moderation_bot as bot
from market.models import Question, PublicationDecision, PublicationDelivery
from market import services
from market.services import MarketError
from market.tests.test_moderation_bot import callback, message

pytestmark = pytest.mark.django_db


@pytest.fixture
def draft(settings):
    settings.PREMODERATION_ENABLED = True
    settings.MODERATION_ADMIN_IDS = ('123', '456')
    settings.MODERATION_BOT_TOKEN = '555:MODERATION-TEST'
    settings.TELEGRAM_BOT_TOKEN = '111:PUBLIC-TEST'
    q = Question.objects.create(title='Выйдет ли релиз?', resolution_criteria='По релизу GitHub')
    gate.apply_decision(q.pk, 'review', 'uncertain', actor='automatic', expected_hash=gate.digest(gate.snapshot(q)))
    return q


def test_queue_and_confirmation(draft):
    version = draft.publication_decisions.latest('pk')
    with patch.object(bot, 'api', return_value={}) as api:
        bot.handle_update(message(text='/publications'))
        assert f'pc:open:{draft.pk}' in str(api.call_args_list)
        bot.handle_update(callback(f'pc:open:{draft.pk}'))
        assert 'По релизу GitHub' in str(api.call_args_list)
        bot.handle_update(callback(f'pc:ask:approved:{version.pk}'))
        assert 'Подтвердить' in str(api.call_args_list)
    draft.refresh_from_db()
    assert draft.publication_state == 'review'


@pytest.mark.parametrize('user,chat,kind', [(999, None, 'private'), (123, -10, 'group')])
def test_no_access_for_outsiders_or_groups(draft, user, chat, kind):
    version = draft.publication_decisions.latest('pk')
    with patch.object(bot, 'api', return_value={}) as api:
        bot.handle_update(callback(f'pc:do:approved:{version.pk}', user, chat, kind))
        assert draft.title not in str(api.call_args_list)
    draft.refresh_from_db()
    assert draft.publication_state == 'review'
    with pytest.raises(PermissionDenied):
        pub.decide(999, version.pk, 'approved')


def test_approve_whole_market_and_audit_once(draft):
    child = Question.objects.create(title='Вариант', parent=draft)
    gate.apply_decision(draft.pk, 'review', 'uncertain', actor='automatic', expected_hash=gate.digest(gate.snapshot(draft)))
    version = draft.publication_decisions.latest('pk')
    assert pub.decide(123, version.pk, 'approved')
    draft.refresh_from_db()
    child.refresh_from_db()
    assert draft.publication_state == child.publication_state == 'approved'
    assert PublicationDecision.objects.filter(actor='telegram:123', decision='approved').count() == 1
    with pytest.raises(MarketError, match='изменились'):
        pub.decide(123, version.pk, 'approved')


def test_changed_content_rejects_old_confirmation(draft):
    version = draft.publication_decisions.latest('pk')
    draft.title = 'Другой вопрос'
    draft.save(update_fields=['title'])
    with pytest.raises(MarketError, match='изменились'):
        pub.decide(123, version.pk, 'approved')
    draft.refresh_from_db()
    assert draft.publication_state == 'pending'


def test_rejection_uses_existing_refund_service(draft):
    version = draft.publication_decisions.latest('pk')
    with patch('market.services.annul', wraps=services.annul) as annul:
        assert pub.decide(123, version.pk, 'rejected')
        assert annul.call_count == 1
    draft.refresh_from_db()
    assert draft.publication_state == 'rejected' and draft.status == 'annulled'


def test_delivery_retry_and_deduplication(draft):
    with patch.object(bot, 'render', side_effect=bot.ModerationTelegramError()):
        assert pub.flush_notifications() == 0
    assert PublicationDelivery.objects.count() == 2
    assert not PublicationDelivery.objects.filter(sent_at__isnull=False).exists()
    from django.utils import timezone
    PublicationDelivery.objects.update(next_attempt=timezone.now())
    with patch.object(bot, 'render') as render:
        assert pub.flush_notifications() == 2
        assert pub.flush_notifications() == 0
        assert render.call_count == 2


def test_old_reviews_delivered_but_resolved_not_sent(draft):
    with patch.object(bot, 'render'):
        assert pub.flush_notifications() == 2
    version = draft.publication_decisions.latest('pk')
    pub.decide(123, version.pk, 'rejected')
    PublicationDelivery.objects.all().delete()
    with patch.object(bot, 'render') as render:
        assert pub.flush_notifications() == 0
        render.assert_not_called()


def test_other_admin_cannot_overwrite_decision(draft):
    version = draft.publication_decisions.latest('pk')
    with patch.object(bot, 'api', return_value={}):
        bot.handle_update(callback(f'pc:do:approved:{version.pk}'))
        bot.handle_update(callback(f'pc:do:rejected:{version.pk}', user=456))
    draft.refresh_from_db()
    assert draft.publication_state == 'approved'
    assert draft.publication_decisions.filter(actor__startswith='telegram:').count() == 1


def test_error_available_for_manual_review_and_new_version_invalidates_button(draft):
    old = draft.publication_decisions.latest('pk')
    gate.apply_decision(draft.pk, 'error', 'unavailable', actor='automatic', expected_hash=old.content_hash)
    latest = draft.publication_decisions.latest('pk')
    assert pub.queue().filter(pk=draft.pk).exists()
    with pytest.raises(MarketError):
        pub.decide(123, old.pk, 'approved')
    assert pub.decide(123, latest.pk, 'rejected')


def test_long_rules_are_delivered_completely(draft):
    draft.resolution_criteria = 'Начало ' + 'длинный текст ' * 400 + ' КОНЕЦ КРИТЕРИЯ'
    draft.save(update_fields=['resolution_criteria'])
    gate.apply_decision(draft.pk, 'review', 'too_large', actor='automatic', expected_hash=gate.digest(gate.snapshot(draft)))
    content = pub.readable_content(draft)
    with patch.object(bot, 'render') as render, patch.object(bot, 'send') as send:
        for page in range((len(content)+1599)//1600):
            pub.show_card(123, draft.pk, page, message_id=42)
        send.assert_not_called()
    texts = [call.args[1] for call in render.call_args_list]
    assert any('КОНЕЦ КРИТЕРИЯ' in text for text in texts)
    assert all(len(text.encode('utf-16-le')) < 7800 for text in texts)
    assert all(call.kwargs['message_id'] == 42 for call in render.call_args_list)
    assert ''.join(text.split('\n\n', 1)[1] for text in texts) == content


def test_card_navigation_and_decision_replace_same_message(draft):
    version = draft.publication_decisions.latest('pk')
    with patch.object(bot, 'api', return_value={'message_id': 42}) as api:
        pub.show_card(123, draft.pk)
        pub.handle_callback(123, f'pc:card:{draft.pk}:0', message_id=42)
        pub.handle_callback(123, f'pc:ask:rejected:{version.pk}', message_id=42)
        pub.handle_callback(123, f'pc:do:rejected:{version.pk}', message_id=42)
    assert sum(call.args[0] == 'sendMessage' for call in api.call_args_list) == 1
    edits = [call for call in api.call_args_list if call.args[0] == 'editMessageText']
    assert len(edits) == 3
    assert all(call.kwargs['message_id'] == 42 for call in edits)
    assert 'Критерий результата:' in edits[0].kwargs['text']
    assert 'resolution_criteria' not in edits[0].kwargs['text']
    assert 'Решение сохранено' in edits[-1].kwargs['text']
