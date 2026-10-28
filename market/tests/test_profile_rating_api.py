"""Profile calibration, best prediction, invites and the rating rows of the Mini App API."""
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.test import override_settings
from django.utils import timezone

from market import services
from market.models import Question, Trade, Trader
from market.tests.test_api import Api, anya, boris, bot_token, market_by, trader  # noqa: F401

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("bot_token")]


def team_market(title="Рынок"):
    return services.create_question(title=title, resolution_criteria="Итог", category="weather",
                                    deadline=timezone.now() + timedelta(days=2),
                                    resolves_at=timezone.now() + timedelta(days=3))


def resolved_predictions(user_id, count, *, side="yes", outcome="yes"):
    for i in range(count):
        m = team_market(f"Рынок {i}")
        services.buy(trader(user_id), m, side, Decimal(10))
        services.resolve(m, outcome)


def fake_buy(user_id, market, side, prob_before, *, shares="20", cost="10"):
    """A buy at a chosen price; the real services.buy cannot start a market at 12%."""
    return Trade.objects.create(trader=trader(user_id), question=market, side=side, shares=Decimal(shares),
                                cost=Decimal(cost), prob_before=prob_before, prob_after=prob_before)


def test_calibration_buckets_buys_by_price_of_bought_side(anya):
    won, lost, open_market = team_market("A"), team_market("B"), team_market("C")
    fake_buy(1001, won, "yes", 0.12)        # bucket 10, hit
    fake_buy(1001, won, "no", 0.35)         # NO at 65% -> bucket 70, miss
    fake_buy(1001, lost, "yes", 0.71)       # bucket 70, miss (resolved NO)
    fake_buy(1001, lost, "no", 0.05)        # NO at 95% -> bucket 90, hit
    fake_buy(1001, open_market, "yes", 0.5)  # not resolved: ignored
    Trade.objects.create(trader=trader(1001), question=won, side="yes", shares=Decimal("-5"),
                         cost=Decimal("-3"), prob_before=0.3, prob_after=0.3)  # sale: ignored
    services.resolve(won, "yes")
    services.resolve(lost, "no")
    _, profile = anya.get("profile")
    assert profile["calibration"] == [{"price": 10, "count": 1, "hits": 1},
                                      {"price": 70, "count": 2, "hits": 0},
                                      {"price": 90, "count": 1, "hits": 1}]


def test_best_prediction_is_cheapest_winning_buy(anya):
    m1, m2 = team_market("Отменят ли пары?"), team_market("Пойдёт снег?")
    fake_buy(1001, m1, "yes", 0.12, shares="100", cost="15")
    fake_buy(1001, m2, "yes", 0.30, shares="300", cost="100")
    fake_buy(1001, m2, "no", 0.95)  # NO at 5%, but it lost
    services.resolve(m1, "yes")
    services.resolve(m2, "yes")
    _, profile = anya.get("profile")
    assert profile["best_prediction"] == {"market": {"id": m1.pk, "title": "Отменят ли пары?"}, "side": "yes",
                                          "price": 0.12, "gain": "85.00"}


def test_best_prediction_empty_without_wins(anya):
    _, profile = anya.get("profile")
    assert profile["best_prediction"] is None and profile["calibration"] == []


def test_invited_count_joined_at_and_profile_stats(anya, boris):
    Trader.objects.filter(telegram_chat_id=1002).update(referred_by=trader(1001))
    Trader.objects.create(name="Вика", pseudonym="Вика", referred_by=trader(1001))
    _, profile = anya.get("profile")
    assert profile["invited"] == 2 and profile["joined_at"]
    assert boris.get("profile")[1]["invited"] == 0
    resolved_predictions(1001, 2)
    stats = anya.get("profile")[1]["stats"]
    assert stats["correct"] == 2 and stats["evaluated"] == 2 and stats["rank"] == 1
    assert stats["disputes_lost"] == 0


def test_rating_rows_have_predictions_and_my_place(anya, boris):
    resolved_predictions(1002, 3)
    _, rating = anya.get("rating")
    rows = {r["display_name"]: r for r in rating["items"]}
    boris_row = rows[trader(1002).display_name]
    assert boris_row["rank"] == 1 and boris_row["predictions"] == 3 and boris_row["accuracy"] == 1.0
    assert set(boris_row) == {"rank", "value", "predictions", "accuracy", "profit", "display_name", "photo_url",
                              "is_me"}
    assert rating["me"]["rank"] == 2 and rating["me"]["predictions"] == 0 and rating["me"]["accuracy"] is None
    assert rating["min_resolved"] == 20


def test_accuracy_rating_needs_twenty_resolved_predictions(anya, boris):
    resolved_predictions(1001, 19)
    resolved_predictions(1002, 20, side="no")  # all wrong, but enough of them
    _, acc = anya.get("rating", by="accuracy")
    assert [r["display_name"] for r in acc["items"]] == [trader(1002).display_name]
    assert acc["items"][0]["predictions"] == 20 and acc["items"][0]["value"] == "0.0000"
    assert acc["me"] is None
    resolved_predictions(1001, 1)
    _, acc = anya.get("rating", by="accuracy")
    assert acc["me"]["rank"] == 1 and acc["me"]["predictions"] == 20 and acc["me"]["value"] == "1.0000"


@override_settings(RATING_ACCURACY_MIN_RESOLVED=5)
def test_accuracy_threshold_comes_from_settings(anya):
    resolved_predictions(1001, 5)
    _, acc = anya.get("rating", by="accuracy")
    assert acc["me"]["rank"] == 1 and acc["min_resolved"] == 5


def test_friends_accuracy_rating_is_not_limited_by_threshold(anya, boris):
    Trader.objects.filter(telegram_chat_id=1002).update(referred_by=trader(1001))
    resolved_predictions(1001, 1)
    resolved_predictions(1002, 2, side="no")
    _, friends = anya.get("rating", scope="friends", by="accuracy")
    assert [(r["is_me"], r["predictions"]) for r in friends["items"]] == [(True, 1), (False, 2)]
    _, everyone = anya.get("rating", by="accuracy")
    assert everyone["items"] == [] and everyone["me"] is None


def test_rating_rows_hide_others_trades(anya, boris):
    m = market_by(1002)
    anya.post(f"markets/{m.pk}/buy", {"side": "yes", "amount": 50, "request_key": str(uuid.uuid4())})
    _, rating = boris.get("rating")
    for row in rating["items"]:
        assert not {"trades", "positions", "side", "market"} & set(row)
    assert Question.objects.count() == 1
