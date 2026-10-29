"""Closed chats (spec E8): shared helpers, chat markets in the service and the chat rating."""
from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
from django.test import override_settings
from django.utils import timezone

from market import chats, rating, services
from market.access import visible_questions
from market.models import Group, GroupMembership, LedgerEntry, Trader
from market.services import MarketError
from market.telegram import TelegramError

D = Decimal


def make(name, balance=1000, tg=None):
    return Trader.objects.create(name=name, pseudonym=name, balance=D(balance), telegram_chat_id=tg)


def chat(members=28, **kw):
    fields = dict(name="Группа ПИ-24", kind="chat", telegram_chat_id=-100123, code=chats.new_code(),
                  bot_active=True, member_count=members)
    fields.update(kw)
    return Group.objects.create(**fields)


def join(group, trader, checked=True):
    return GroupMembership.objects.create(group=group, trader=trader, status="active",
                                          checked_at=timezone.now() if checked else None)


def new_market(creator, **kw):
    now = timezone.now()
    args = dict(title="Перенесут ли коллоквиум?", criteria="По объявлению в чате", category="study",
                closes_at=now + timedelta(days=3), resolves_at=now + timedelta(days=4))
    args.update(kw)
    return services.create_market(creator, **args)


@pytest.mark.parametrize("members, cost, b", [(3, 40, 57), (15, 40, 57), (16, 70, 100), (50, 70, 100),
                                              (51, 100, 144), (5000, 100, 144)])
def test_price_and_depth_by_chat_size(members, cost, b):
    assert chats.size_cost(members) == cost
    assert chats.depth(cost) == b
    assert chats.depth(cost) * 0.6931471805599453 <= cost


@pytest.mark.parametrize("cost, b", [(30, 43), (200, 288)])
def test_depth_at_admin_price_bounds(cost, b):
    assert chats.depth(cost) == b


def test_admin_price_overrides_size(db):
    assert chats.creation_cost(chat(members=10, creation_cost=150)) == 150


@override_settings(TELEGRAM_BOT_USERNAME="pm_bot", TELEGRAM_APP_NAME="app")
def test_link_uses_random_code_not_chat_id(db):
    group = chat()
    assert chats.link(group) == f"https://t.me/pm_bot/app?startapp=chat_{group.code}"
    assert str(group.telegram_chat_id) not in group.code


def test_check_member_asks_telegram_once_per_ten_minutes(db):
    group, trader = chat(), make("Сова", tg=501)
    with mock.patch("market.chats.telegram.api", return_value={"status": "member"}) as api:
        assert chats.check_member(trader, group)
        assert chats.check_member(trader, group)
    api.assert_called_once()
    assert api.call_args.kwargs["user_id"] == 501
    later = timezone.now() + timedelta(minutes=11)
    with mock.patch("market.chats.telegram.api", return_value={"status": "left"}):
        assert not chats.check_member(trader, group, now=later)
    assert GroupMembership.objects.get(group=group, trader=trader).status == "left"


def test_restricted_member_counts_only_while_in_chat(db):
    assert chats.is_member_status({"status": "restricted", "is_member": True})
    assert not chats.is_member_status({"status": "restricted", "is_member": False})
    assert not chats.is_member_status({"status": "kicked"})


def test_stranger_gets_no_membership_row(db):
    group, trader = chat(), make("Чужой", tg=777)
    with mock.patch("market.chats.telegram.api", return_value={"status": "left"}):
        assert not chats.check_member(trader, group)
    assert not GroupMembership.objects.filter(group=group, trader=trader).exists()


def test_frozen_chat_keeps_last_known_members_without_calls(db):
    group, trader = chat(bot_active=False), make("Ёж", tg=502)
    join(group, trader, checked=False)
    with mock.patch("market.chats.telegram.api") as api:
        assert chats.check_member(trader, group)
    api.assert_not_called()


def test_telegram_down_keeps_last_answer(db):
    group, trader = chat(), make("Барсук", tg=503)
    join(group, trader, checked=False)
    with mock.patch("market.chats.telegram.api", side_effect=TelegramError("down")):
        assert chats.check_member(trader, group)


def test_chat_market_costs_chat_price_and_fixes_depth(db):
    group, creator = chat(members=12), make("Автор")
    join(group, creator)
    market = new_market(creator, group=group)
    assert market.group_id == group.pk
    assert market.subsidy == D(40) and market.b == 57
    creator.refresh_from_db()
    assert creator.balance == D(960)
    assert LedgerEntry.objects.get(trader=creator, kind=LedgerEntry.Kind.CREATION).amount == D(-40)
    # Later changes of the chat do not touch an existing market.
    group.member_count, group.creation_cost = 300, 200
    group.save()
    market.refresh_from_db()
    assert market.b == 57 and market.subsidy == D(40)


def test_chat_market_needs_membership_and_active_bot(db):
    group, outsider = chat(), make("Чужой")
    with pytest.raises(MarketError, match="только участник"):
        new_market(outsider, group=group)
    member = make("Свой")
    join(group, member)
    group.bot_active = False
    group.save()
    with pytest.raises(MarketError, match="не админ"):
        new_market(member, group=group)
    member.refresh_from_db()
    assert member.balance == D(1000)


def test_chat_market_balance_message_names_chat_price(db):
    group, poor = chat(members=60, creation_cost=150), make("Бедный", balance=120)
    join(group, poor)
    with pytest.raises(MarketError, match="150 монет"):
        new_market(poor, group=group)


def test_request_key_replay_must_match_chat(db):
    group, creator = chat(), make("Автор")
    join(group, creator)
    key = "6c1f1d0e-8a4e-4c1e-9d7a-2b9f8f0a1c11"
    first = new_market(creator, group=group, request_key=key)
    assert new_market(creator, group=group, request_key=key).pk == first.pk
    with pytest.raises(MarketError, match="идентификатор"):
        new_market(creator, request_key=key)


def test_chat_market_visible_only_to_active_members(db):
    group, creator, member, left = chat(), make("Автор"), make("Свой"), make("Ушёл")
    join(group, creator)
    join(group, member)
    GroupMembership.objects.create(group=group, trader=left, status="left")
    market = new_market(creator, group=group)
    assert visible_questions(member).filter(pk=market.pk).exists()
    assert not visible_questions(left).filter(pk=market.pk).exists()
    assert not visible_questions(make("Чужой")).filter(pk=market.pk).exists()


def test_chat_rating_counts_only_chat_markets(db):
    group, creator, sova, ezh = chat(), make("Автор"), make("Сова"), make("Ёж")
    for t in (creator, sova, ezh):
        join(group, t)
    in_chat = new_market(creator, group=group)
    public = new_market(creator, title="Выпадет ли снег до декабря?")
    services.buy(sova, in_chat, "yes", D(20))
    services.buy(ezh, in_chat, "no", D(20))
    services.buy(sova, public, "no", D(50))
    services.resolve(in_chat, "yes")
    services.resolve(public, "yes")
    board = rating.leaderboard(None, group=group)
    names = [row["display_name"] for row in board["items"]]
    assert names[0] == "Сова" and "Ёж" in names
    sova_row = next(r for r in board["items"] if r["display_name"] == "Сова")
    expected = rating.realized_profit(sova, None, group)
    assert D(sova_row["profit"]) == expected > 0
    assert board["me"] is None and board["min_resolved"] == 1
    mine = rating.leaderboard(ezh, group=group, by="accuracy")
    assert mine["me"]["accuracy"] == 0.0


def test_old_site_group_pages_hide_chats(client, db):
    from django.contrib.auth.models import User
    user = User.objects.create_user("u", password="p")
    trader = Trader.objects.create(user=user, name="u", pseudonym="u", balance=D(0))
    group = chat()
    join(group, trader)
    client.force_login(user)
    assert client.get(f"/groups/{group.pk}/").status_code == 404
    assert "Группа ПИ-24" not in client.get("/groups/?q=Группа").content.decode()
