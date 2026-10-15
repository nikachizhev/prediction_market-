from decimal import Decimal
from datetime import timedelta
import pytest
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from market.models import Question, Trader
from market import services, mechanisms
from market.participation import submit, finish
from market.statistics import statistics


@pytest.mark.django_db
@pytest.mark.parametrize("kind", ["lmsr", "ls_lmsr", "cpmm"])
@pytest.mark.parametrize("side", ["yes", "no"])
def test_trade_roundtrip_and_payout(kind, side):
    q = Question.objects.create(title="Test", kind=kind)
    t = Trader.objects.create(name="Trader", pseudonym="Trader")
    initial = q.pool_yes*q.pool_no
    trade = services.buy(t, q, side, 100)
    q.refresh_from_db()
    assert (q.prob > .5) == (side == "yes")
    if kind == "cpmm":
        assert q.pool_yes*q.pool_no == pytest.approx(initial)
    services.sell(t, q, side, trade.shares)
    q.refresh_from_db()
    t.refresh_from_db()
    assert q.prob == pytest.approx(.5)
    assert Decimal("999.98") <= t.balance <= 1000
    services.buy(t, q, side, 20)
    services.resolve(q, side)
    assert t.payouts.count() == 1


def test_ls_full_gradient_and_depth():
    q = Question(kind="ls_lmsr")
    y, n = mechanisms.ls_prices(q, 20, 10)
    epsilon = .001
    slope = (mechanisms.ls_cost(q, 20+epsilon, 10)-mechanisms.ls_cost(q, 20-epsilon, 10))/(2*epsilon)
    assert y == pytest.approx(slope, rel=1e-6)
    assert y+n > 1
    thin = mechanisms.probability(q, 10, 0)-.5
    deep = mechanisms.probability(q, 10010, 10000)-.5
    assert 0 < deep < thin


@pytest.mark.django_db
@pytest.mark.parametrize("kind,value,answer,correct", [
    ("number", "42", "43", 1), ("number", "40", "43", 0),
    ("multiple", ["0", "2"], ["2", "0"], 1),
    ("multiple", ["0"], ["0", "2"], 0),
])
def test_answers_and_statistics(kind, value, answer, correct):
    author = Trader.objects.create(name="Author", pseudonym="Author")
    t = Trader.objects.create(name="Trader", pseudonym="Trader")
    q = Question.objects.create(title="Test", kind=kind, creator=author, options=["A", "B", "C"], tolerance=1)
    submit(t, q, value)
    with pytest.raises(services.MarketError):
        submit(t, q, value)
    with pytest.raises(services.MarketError):
        finish(q, t, answer)
    finish(q, author, answer)
    stats = statistics(t)
    assert stats["correct"] == correct
    assert stats["evaluated"] == 1
    assert stats["spent"] == 0


@pytest.mark.django_db
def test_poll_privacy_and_deadline(client):
    author = Trader.objects.create(name="Author", pseudonym="Author")
    t = Trader.objects.create(name="Secret voter", pseudonym="Secret nickname")
    q = Question.objects.create(title="Poll", kind="poll", creator=author, options=["Pizza", "Sushi"])
    submit(t, q, "0")
    finish(q, author)
    page = client.get(reverse("market:question", args=[q.pk])).content.decode()
    assert "Pizza: 1" in page
    assert "Secret voter" not in page and "Secret nickname" not in page
    assert statistics(t)["evaluated"] == 0
    q.status = "open"
    q.deadline = timezone.now()-timedelta(seconds=1)
    q.save()
    with pytest.raises(services.MarketError):
        submit(author, q, "1")


@pytest.mark.django_db
@pytest.mark.parametrize("kind", Question.Kind.values)
def test_create_and_render_all_kinds(client, kind):
    user = User.objects.create_user(username="creator")
    client.force_login(user)
    response = client.post(reverse("market:create"), {
        "title": "New question", "kind": kind, "resolution_criteria": "Official result",
        "b": "200", "number_min": "0", "number_max": "100", "tolerance": "1",
        "option_lines": "A\nB\nC",
    })
    assert response.status_code == 302
    q = Question.objects.get()
    assert q.kind == kind
    assert client.get(response.url).status_code == 200
    assert client.get(reverse("market:personal_rating")).status_code == 200
