"""Migration 0026: weather markets move to «Фан», the slug leaves onboarding interests."""
import importlib
from datetime import timedelta

import pytest
from django.apps import apps
from django.utils import timezone

from market import services
from market.models import Question, Trader

pytestmark = pytest.mark.django_db

migration = importlib.import_module("market.migrations.0026_drop_weather_category")


def question(category: str) -> Question:
    now = timezone.now()
    q = services.create_question(title="Рынок", resolution_criteria="Итог", category="study",
                                 deadline=now + timedelta(days=2), resolves_at=now + timedelta(days=3))
    Question.objects.filter(pk=q.pk).update(category=category)  # "weather" is no longer accepted by the service
    return q


def test_weather_markets_move_to_fun_and_others_stay():
    weather, sport = question("weather"), question("sport")
    migration.drop_weather(apps, None)
    assert Question.objects.get(pk=weather.pk).category == "fun"
    assert Question.objects.get(pk=sport.pk).category == "sport"


def test_weather_leaves_interests_keeping_the_rest():
    fan = Trader.objects.create(name="Аня", pseudonym="Лиса", interests=["games", "weather", "sport"])
    other = Trader.objects.create(name="Борис", pseudonym="Барсук", interests=["study"])
    empty = Trader.objects.create(name="Вика", pseudonym="Сова")
    migration.drop_weather(apps, None)
    assert Trader.objects.get(pk=fan.pk).interests == ["games", "sport"]
    assert Trader.objects.get(pk=other.pk).interests == ["study"]
    assert Trader.objects.get(pk=empty.pk).interests == []
