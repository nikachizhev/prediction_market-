"""Phase 3f base (spec E12): reward models, the badge notification kind, the economy parameters."""
from decimal import Decimal

import pytest
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from market import bot_notifications as notices
from market.models import NOTIFY_KINDS, Badge, LedgerEntry, Trader, WeeklyTop, WheelSpin

pytestmark = pytest.mark.django_db


def person(name="Аня"):
    return Trader.objects.create(name=name, pseudonym=name, telegram_chat_id=hash(name) % 10**9)


def duplicate_fails(create):
    create()
    with pytest.raises(IntegrityError), transaction.atomic():
        create()


def test_badge_is_given_once_per_kind():
    anya = person()
    duplicate_fails(lambda: Badge.objects.create(trader=anya, kind=Badge.Kind.PROPHET))
    Badge.objects.create(trader=anya, kind=Badge.Kind.FIRST)  # another kind is fine
    Badge.objects.create(trader=person("Боря"), kind=Badge.Kind.PROPHET)  # another person too
    assert [b.kind for b in anya.badges.all()] == ["prophet", "first"]
    assert set(Badge.Kind.values) == {"first", "prophet", "against", "judge", "leader", "duel", "optimist"}


def test_honor_board_has_one_person_per_place_and_one_place_per_person():
    anya, boris = person(), person("Боря")
    monday = timezone.localdate()
    duplicate_fails(lambda: WeeklyTop.objects.create(week_start=monday, place=1, trader=anya, profit=Decimal(540)))
    with pytest.raises(IntegrityError), transaction.atomic():
        WeeklyTop.objects.create(week_start=monday, place=1, trader=boris, profit=Decimal(1))
    with pytest.raises(IntegrityError), transaction.atomic():
        WeeklyTop.objects.create(week_start=monday, place=2, trader=anya, profit=Decimal(1))
    WeeklyTop.objects.create(week_start=monday, place=2, trader=boris, profit=Decimal(310))
    assert [(t.place, t.trader_id) for t in WeeklyTop.objects.filter(week_start=monday)] == \
        [(1, anya.pk), (2, boris.pk)]


def test_one_wheel_spin_per_day_and_the_prize_is_a_bonus():
    anya = person()
    today = timezone.localdate()
    duplicate_fails(lambda: WheelSpin.objects.create(trader=anya, day=today, prize=Decimal(5)))
    assert LedgerEntry.Kind.WHEEL in LedgerEntry.BONUS_KINDS


def test_badges_are_a_bot_notification_kind_with_the_usual_switch():
    assert ("badges", "Значки") in NOTIFY_KINDS
    anya = person()
    assert notices.notify_settings(anya)["badges"] is True
    assert notices.bot_allowed(anya, "badges")
    anya.notify_prefs = {"badges": False}
    assert not notices.bot_allowed(anya, "badges")
    anya.notify_prefs = {"quiet": True}
    assert not notices.bot_allowed(anya, "badges")  # the quiet mode mutes it
    assert not notices.bot_allowed(anya, "honor")   # and the honor-board message, which has no switch
    anya.notify_prefs = {}
    assert notices.bot_allowed(anya, "honor")


def test_reward_parameters_live_in_settings():
    table = settings.DAILY_BONUS_STREAK
    assert table == (20, 25, 30, 35, 40, 45, 50) and settings.DAILY_BONUS == table[0]
    assert list(table) == sorted(table)
    assert settings.HONOR_TOP == 3 and settings.HONOR_MIN_RESOLVED == 3
    assert settings.BADGE_PROPHET_STREAK == settings.BADGE_OPTIMIST_STREAK == 5
    assert Decimal(settings.BADGE_AGAINST_PROB) == Decimal("0.20") and settings.BADGE_AGAINST_MIN_TRADE == 10
    assert settings.BADGE_JUDGE_MARKETS == settings.BADGE_LEADER_PARTICIPANTS == 10
    assert settings.BADGE_DUELIST_WINS == 5
