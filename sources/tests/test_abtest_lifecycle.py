import random

import pytest

from abmock import services as ab
from abmock.models import Experiment
from market import services
from market.models import Payout, Question, Trader
from sources.sync import sync_all


@pytest.fixture
def people(db):
    return [Trader.objects.create(name=n, pseudonym=n + "!") for n in ("Аня", "Борис", "Вика")]


def mk(author, lift=0.3):
    return Experiment.objects.create(name="E", metric="конверсия", author=author, true_lift=lift, n_per_group=20000)


def get_q(exp):
    return Question.objects.get(source_type="abtest", source_ref=f"ab:{exp.pk}")


def test_draft_creates_question_idempotently(people):
    anya = people[0]
    e = mk(anya)
    sync_all()
    sync_all()
    q = get_q(e)
    assert Question.objects.count() == 1
    assert q.prob == pytest.approx(0.25)
    assert q.status == "open"
    assert list(q.conflicted_traders.all()) == [anya]
    assert "p < 0.05" in q.resolution_criteria


def test_running_closes_then_finished_resolves_with_payouts(people):
    anya, boris, vika = people
    e = mk(anya, lift=0.3)
    sync_all()
    q = get_q(e)
    services.buy(boris, q, "yes", 100)
    services.buy(vika, q, "no", 50)
    ab.start(e)
    sync_all()
    q.refresh_from_db()
    assert q.status == "closed"
    with pytest.raises(services.MarketError):
        services.buy(boris, q, "yes", 10)
    ab.finish(e, random.Random(3))
    e.refresh_from_db()
    assert e.p_value < 0.05
    sync_all()
    q.refresh_from_db()
    assert q.status == "resolved" and q.outcome == "yes"
    pays = Payout.objects.filter(question=q)
    assert pays.count() == 1 and pays[0].trader == boris
    boris.refresh_from_db()
    assert boris.balance > 1000 - 100 + 100  # payout > cost


def test_finished_no_effect_resolves_no(people):
    e = mk(people[0], lift=0.0)
    sync_all()
    ab.start(e)
    ab.finish(e, random.Random(5))
    e.refresh_from_db()
    e.p_value = 0.4  # force deterministic non-significance
    e.save()
    sync_all()
    assert get_q(e).outcome == "no"


def test_cancelled_annuls_and_refunds(people):
    anya, boris, vika = people
    e = mk(anya)
    sync_all()
    q = get_q(e)
    services.buy(boris, q, "yes", 100)
    boris.refresh_from_db()
    assert boris.balance < 1000
    ab.cancel(e)
    sync_all()
    q.refresh_from_db()
    boris.refresh_from_db()
    assert q.status == "annulled"
    assert boris.balance == 1000
    assert Payout.objects.get(question=q).kind == "refund"


def test_author_cannot_bet(people):
    e = mk(people[0])
    sync_all()
    with pytest.raises(services.MarketError):
        services.buy(people[0], get_q(e), "yes", 10)


def test_finished_unknown_experiment_resolved_immediately(people):
    e = mk(people[0])
    ab.start(e)
    ab.finish(e, random.Random(3))
    sync_all()
    assert get_q(e).status == "resolved"
