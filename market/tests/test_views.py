from decimal import Decimal

import pytest
from django.urls import reverse

from market import services
from market.models import Trader


@pytest.fixture
def q(db):
    return services.create_question(title="Будет ли релиз?", resolution_criteria="Крит", initial_prob=0.25)


@pytest.fixture
def anya(db):
    return Trader.objects.create(name="Аня", pseudonym="Лиса")


@pytest.fixture
def boris(db):
    return Trader.objects.create(name="Борис", pseudonym="Волк")


def login(client, trader):
    s = client.session
    s["trader_id"] = trader.pk
    s.save()


def test_list_and_detail_readable_anonymously(client, q):
    r = client.get(reverse("market:index"))
    assert r.status_code == 200 and "Будет ли релиз?" in r.content.decode()
    assert "25%" in r.content.decode()
    r = client.get(reverse("market:question", args=[q.pk]))
    assert r.status_code == 200


def test_me_redirects_without_trader(client, db):
    r = client.get(reverse("market:me"))
    assert r.status_code == 302 and reverse("market:switch_user") in r["Location"]


def test_switch_user_sets_session(client, anya):
    r = client.post(reverse("market:switch_user"), {"trader_id": anya.pk})
    assert r.status_code == 302
    assert client.session["trader_id"] == anya.pk


def test_quote_json_and_error(client, q):
    r = client.get(reverse("market:quote", args=[q.pk]), {"side": "yes", "amount": "100"})
    j = r.json()
    assert r.status_code == 200 and j["new_prob"] > 0.25 and j["shares"] > 100 and j["payout_if_win"] > 0
    r = client.get(reverse("market:quote", args=[q.pk]), {"side": "yes", "amount": "abc"})
    assert r.status_code == 400 and "error" in r.json()
    r = client.get(reverse("market:quote", args=[q.pk]), {"side": "zzz", "amount": "10"})
    assert r.status_code == 400


def test_buy_flow_and_privacy(client, q, anya, boris):
    login(client, anya)
    r = client.post(reverse("market:buy", args=[q.pk]), {"side": "yes", "amount": "50"})
    assert r.status_code == 302
    anya.refresh_from_db()
    assert anya.balance < Decimal("1000")
    page = client.get(reverse("market:question", args=[q.pk])).content.decode()
    assert "Ваша позиция" in page and "кто-то купил" in page

    # another trader sees the anonymous feed but no names / no position
    client.logout()
    login(client, boris)
    page = client.get(reverse("market:question", args=[q.pk])).content.decode()
    assert "кто-то купил" in page
    assert "Ваша позиция" not in page
    assert "Аня" not in page and "Лиса" not in page


def test_buy_errors_shown_as_message(client, q, anya):
    login(client, anya)
    r = client.post(reverse("market:buy", args=[q.pk]), {"side": "yes", "amount": "99999"}, follow=True)
    assert "Максимальная ставка" in r.content.decode() or "Недостаточно" in r.content.decode()
    anya.refresh_from_db()
    assert anya.balance == Decimal("1000")


def test_conflicted_trader_cannot_bet(client, q, anya):
    q.conflicted_traders.add(anya)
    login(client, anya)
    page = client.get(reverse("market:question", args=[q.pk])).content.decode()
    assert "конфликт интересов" in page and 'id="betform"' not in page
    client.post(reverse("market:buy", args=[q.pk]), {"side": "yes", "amount": "10"})
    anya.refresh_from_db()
    assert anya.balance == Decimal("1000")


def test_sell_view(client, q, anya):
    login(client, anya)
    client.post(reverse("market:buy", args=[q.pk]), {"side": "yes", "amount": "100"})
    pos = anya.positions.get()
    client.post(reverse("market:sell", args=[q.pk]), {"side": "yes", "shares": str(pos.yes_shares)})
    pos.refresh_from_db()
    assert pos.yes_shares == 0


def test_leaderboard_uses_display_name(client, q, anya, boris):
    login(client, anya)
    page = client.get(reverse("market:leaderboard")).content.decode()
    assert "Лиса" in page and "Аня" not in page.split("<nav>")[-1].split("</nav>")[-1]
    anya.show_real_name = True
    anya.save()
    assert "Аня" in client.get(reverse("market:leaderboard")).content.decode()


def test_leaderboard_shows_own_row_outside_top10(client, db):
    ts = [Trader.objects.create(name=f"T{i}", pseudonym=f"P{i}", balance=Decimal(2000 - i)) for i in range(12)]
    login(client, ts[-1])
    page = client.get(reverse("market:leaderboard")).content.decode()
    assert "(вы)" in page


def test_me_update_settings(client, anya):
    login(client, anya)
    r = client.post(reverse("market:me"), {"show_real_name": "on", "pseudonym": "Кот", "github_login": "anya"})
    assert r.status_code == 302
    anya.refresh_from_db()
    assert anya.show_real_name and anya.pseudonym == "Кот" and anya.github_login == "anya"
    assert client.get(reverse("market:me")).status_code == 200
