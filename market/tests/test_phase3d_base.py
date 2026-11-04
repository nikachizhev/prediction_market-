"""Phase 3d base: migration 0033, per-market notify levels, `create_market(announce=)`, `/market` routing."""
import importlib
from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.apps import apps
from django.utils import timezone

from market import chats, notify_levels, services, telegram
from market.models import ChatPost, Group, GroupMembership, MarketNotifyLevel, MarketSubscription, Question, Trader

pytestmark = pytest.mark.django_db

D = Decimal
migration = importlib.import_module("market.migrations.0033_phase_3d")


def make(name, balance=1000, tg=None):
    return Trader.objects.create(name=name, pseudonym=name, balance=D(balance), telegram_chat_id=tg)


def chat(**kw):
    fields = dict(name="Группа ПИ-24", kind="chat", telegram_chat_id=-100123, code=chats.new_code(),
                  bot_active=True, member_count=28)
    fields.update(kw)
    return Group.objects.create(**fields)


def new_market(creator, **kw):
    now = timezone.now()
    args = dict(title="Перенесут ли коллоквиум?", criteria="По объявлению в чате", category="study",
                closes_at=now + timedelta(days=3), resolves_at=now + timedelta(days=4))
    args.update(kw)
    return services.create_market(creator, **args)


def approve(question):
    Question.objects.filter(pk=question.pk).update(publication_state="approved")
    question.refresh_from_db()
    return question


# --- migration ------------------------------------------------------------------------------

def test_migration_marks_old_outcomes_as_the_creators():
    creator = make("Автор")
    stated, silent = new_market(creator), new_market(creator, title="Сдадим ли все зачёт вовремя?")
    Question.objects.filter(pk=stated.pk).update(resolution_state="pending", proposed_outcome="yes")
    migration.forwards(apps, None)
    assert Question.objects.get(pk=stated.pk).proposed_by_id == creator.pk
    assert Question.objects.get(pk=silent.pk).proposed_by_id is None


def test_migration_keeps_announced_chat_markets_announced():
    group, creator = chat(), make("Автор")
    GroupMembership.objects.create(group=group, trader=creator, status="active", checked_at=timezone.now())
    posted, quiet = new_market(creator, group=group), new_market(creator, group=group, title="Будет ли пара в субботу?")
    ChatPost.objects.create(group=group, kind="new_market", key=f"new:{posted.pk}", text="…", question=posted,
                            send_after=timezone.now())
    migration.forwards(apps, None)
    assert Question.objects.get(pk=posted.pk).announced_at is not None
    assert Question.objects.get(pk=quiet.pk).announced_at is None


@pytest.mark.parametrize("old, mode", [
    ({}, "important"),
    ({"new_market": False}, "important"),
    ({"new_market": False, "results": False, "weekly_top": False}, "quiet"),
    ({"mode": "all"}, "all"),
])
def test_migration_turns_post_switches_into_a_mode(old, mode):
    group = chat(post_settings=old)
    migration.forwards(apps, None)
    group.refresh_from_db()
    assert group.post_settings["mode"] == mode


# --- notify levels --------------------------------------------------------------------------

def test_level_defaults_follow_star_and_position():
    creator, fan = make("Автор"), make("Фанат")
    market = approve(new_market(creator))
    assert notify_levels.level(fan, market) == "none"
    services.buy(fan, market, "yes", D(10))
    assert notify_levels.level(fan, market) == "result"
    MarketSubscription.objects.create(trader=fan, question=market)
    assert notify_levels.level(fan, market) == "all"


def test_explicit_level_wins_and_lives_on_the_root():
    creator, fan = make("Автор"), make("Фанат")
    market = approve(new_market(creator, options=["Аня", "Борис"]))
    option = market.submarkets.first()
    MarketSubscription.objects.create(trader=fan, question=market)
    notify_levels.set_level(fan, option, "none")
    assert MarketNotifyLevel.objects.get(trader=fan).question_id == market.pk
    assert notify_levels.level(fan, market) == "none"
    assert notify_levels.level(fan, option) == "none"
    with pytest.raises(ValueError):
        notify_levels.set_level(fan, market, "loud")


# --- create_market(announce=) ----------------------------------------------------------------

def test_announce_applies_only_to_chat_markets():
    group, creator = chat(), make("Автор")
    GroupMembership.objects.create(group=group, trader=creator, status="active", checked_at=timezone.now())
    assert new_market(creator, group=group, announce=True).announce is True
    assert new_market(creator, group=group, title="Будет ли пара в субботу?").announce is False
    assert new_market(creator, title="Выпадет ли снег до декабря?", announce=True).announce is False


# --- routing ---------------------------------------------------------------------------------

def test_market_command_in_a_chat_goes_to_bot_draft():
    group = chat()
    update = {"update_id": 1, "message": {"message_id": 7, "chat": {"id": group.telegram_chat_id, "type": "supergroup"},
                                          "from": {"id": 501}, "text": "/market"}}
    with mock.patch("market.bot_draft.handle_command") as handle:
        telegram.handle_update(update)
    message, passed = handle.call_args.args
    assert message["message_id"] == 7 and passed == group


def test_draft_callbacks_go_to_bot_draft():
    update = {"update_id": 2, "callback_query": {"id": "c", "data": "draft:create:abc", "from": {"id": 501}}}
    with mock.patch("market.bot_draft.handle_callback") as handle:
        telegram.handle_update(update)
    handle.assert_called_once()
