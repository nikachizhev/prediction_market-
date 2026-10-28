from datetime import timedelta
from decimal import Decimal
import math
import pytest
from django.utils import timezone
from market.models import Event, FeedDismissal, Group, Question, Trade, Trader
from market.tests.test_api import Api
from market.tests.test_miniapp_auth import TOKEN

pytestmark = pytest.mark.django_db


@pytest.fixture
def viewer(client, settings):
    settings.TELEGRAM_BOT_TOKEN = TOKEN
    api = Api(client, 9001, first_name="Viewer")
    api.post("session")
    return api


def make_market(title, **kwargs):
    return Question.objects.create(title=title, category="games", deadline=timezone.now()+timedelta(days=2), **kwargs)


def ids(api, **params):
    status, response = api.get("markets", **params)
    assert status == 200
    return [row["id"] for row in response["items"]]


def bought(trader, market):
    return Trade.objects.create(trader=trader, question=market, side="yes", shares=Decimal(10), cost=Decimal(5), prob_before=.5, prob_after=.55)


def test_selected_interests_change_order_and_can_be_cleared(viewer):
    first = make_market("Weather")
    first.category = "weather"
    first.save()
    second = make_market("Games")
    assert ids(viewer, sort="recommended") == [second.pk, first.pk]
    viewer.patch("me", {"interests": ["weather"]})
    status, feed = viewer.get("markets", sort="recommended")
    assert [row["id"] for row in feed["items"]] == [first.pk, second.pk]
    assert feed["items"][0]["recommendation_reason"] == "По вашим интересам"
    viewer.patch("me", {"interests": []})
    assert ids(viewer, sort="recommended") == [second.pk, first.pk]
    assert ids(viewer, sort="new") == [second.pk, first.pk]


@pytest.mark.parametrize("sign", [1, -1])
def test_bought_and_skewed_markets_go_lower_but_remain_visible(viewer, sign):
    trader = Trader.objects.get(telegram_chat_id=9001)
    fresh = make_market("Fresh")
    skewed = make_market("Skewed", q_yes=sign*200*math.log(10))
    previous = make_market("Bought")
    bought(trader, previous)
    assert ids(viewer, sort="recommended") == [fresh.pk, skewed.pk, previous.pk]
    assert set(ids(viewer, sort="hot")) == {fresh.pk, skewed.pk, previous.pk}


def test_own_and_closed_markets_go_after_open_candidates(viewer):
    trader = Trader.objects.get(telegram_chat_id=9001)
    open_market = make_market("Open")
    make_market("Own", creator=trader)
    make_market("Closed", status="closed")
    expired = make_market("Expired")
    expired.deadline = timezone.now()-timedelta(seconds=1)
    expired.save()
    assert ids(viewer, sort="recommended")[0] == open_market.pk


def test_dismiss_is_personal_idempotent_and_reversible(viewer, client):
    market = make_market("Market")
    owner = Trader.objects.get(telegram_chat_id=9001)
    bought(owner, market)
    other = Api(client, 9002, first_name="Other")
    other.post("session")
    viewer.post(f"markets/{market.pk}/star", {"starred": True})
    for _ in range(2):
        assert viewer.post(f"markets/{market.pk}/dismiss", {"dismissed": True}) == (200, {"dismissed": True})
    assert FeedDismissal.objects.count() == 1
    assert Event.objects.filter(name="market_dismiss").count() == 1
    for sort in ("recommended", "hot", "new", "soon"):
        assert ids(viewer, sort=sort) == []
    assert ids(other, sort="recommended") == [market.pk]
    assert viewer.get(f"markets/{market.pk}")[0] == 200
    assert owner.trades.count() == 1 and owner.market_subscriptions.count() == 1
    assert viewer.post(f"markets/{market.pk}/dismiss", {"dismissed": False})[0] == 200
    assert ids(viewer, sort="recommended") == [market.pk]


def test_restore_all_only_touches_requesting_user(viewer, client):
    market = make_market("Market")
    other = Api(client, 9002)
    other.post("session")
    for user in (viewer, other):
        user.post(f"markets/{market.pk}/dismiss", {"dismissed": True})
    assert viewer.post("feed/reset-hidden")[1] == {"restored": 1}
    assert ids(other) == [] and ids(viewer) == [market.pk]


def test_private_markets_and_invalid_feedback_are_rejected(viewer, client):
    owner = Trader.objects.create(name="Owner", pseudonym="Owner")
    group = Group.objects.create(name="Private", owner=owner)
    private = make_market("Secret", group=group)
    public = make_market("Public")
    assert viewer.post(f"markets/{private.pk}/dismiss", {"dismissed": True})[0] == 404
    assert viewer.post(f"markets/{public.pk}/dismiss", {"dismissed": "false"})[0] == 400
    assert client.post(f"/api/markets/{public.pk}/dismiss", data='{"dismissed":true}', content_type="application/json").status_code == 401
    assert ids(viewer, sort="recommended") == [public.pk]


def test_recommendations_are_sorted_before_pagination(viewer):
    favored = make_market("Favorite")
    favored.category = "weather"
    favored.save()
    for number in range(24):
        make_market(str(number))
    viewer.patch("me", {"interests": ["weather"]})
    first = ids(viewer, sort="recommended", page=1)
    second = ids(viewer, sort="recommended", page=2)
    assert first[0] == favored.pk and len(first) == 20
    assert len(second) == 5 and not set(first).intersection(second)


def test_feedback_events_validate_access_and_discard_free_text(viewer):
    market = make_market("Market")
    assert viewer.post("events", {"name": "feed_impression", "props": {"market": market.pk, "sort": "recommended", "secret": "do not store"}})[0] == 200
    assert Event.objects.get(name="feed_impression").props == {"market": market.pk, "sort": "recommended"}
    assert viewer.post("events", {"name": "market_open", "props": {"market": True}})[0] == 400


def test_behavior_is_recent_personal_and_capped(viewer):
    from market.recommendations import behavioral_scores
    trader = Trader.objects.get(telegram_chat_id=9001)
    weather = make_market("Weather")
    weather.category = "weather"
    weather.save()
    games = make_market("Games")
    for _ in range(10):
        viewer.post("events", {"name": "market_open", "props": {"market": weather.pk}})
    assert Event.objects.filter(name="market_open").count() == 1
    assert behavioral_scores(trader, timezone.now()) == {"weather": 1}
    assert ids(viewer, sort="recommended")[0] == weather.pk
    viewer.patch("me", {"interests": ["games"]})
    assert ids(viewer, sort="recommended")[0] == games.pk
    Event.objects.update(created_at=timezone.now()-timedelta(days=31))
    assert behavioral_scores(trader, timezone.now()) == {}


def test_report_only_contains_aggregates(viewer):
    import io
    import json
    from django.core.management import call_command
    market = make_market("Market")
    viewer.post("events", {"name": "market_open", "props": {"market": market.pk}})
    output = io.StringIO()
    call_command("recommendation_report", days=7, stdout=output)
    report = json.loads(output.getvalue())
    assert report["metrics"] == [{"name": "market_open", "events": 1, "users": 1}]
    assert "9001" not in output.getvalue()


def test_categories_alternate_without_breaking_pagination(viewer):
    weather = make_market("Weather")
    weather.category = "weather"
    weather.save()
    for i in range(5):
        make_market(f"Games {i}")
    result = ids(viewer, sort="recommended")
    assert result[1] == weather.pk
    assert len(result) == len(set(result)) == 6


def test_titles_promote_similar_and_demote_hidden_topics(viewer):
    source = make_market("Выйдет ли релиз Python осенью?")
    similar = make_market("Выйдет ли новый релиз Python зимой?")
    unrelated = make_market("Победит ли Спартак в чемпионате?")
    viewer.post("events", {"name": "market_open", "props": {"market": source.pk}})
    result = ids(viewer, sort="recommended")
    assert result.index(similar.pk) < result.index(unrelated.pk)
    viewer.post(f"markets/{source.pk}/dismiss", {"dismissed": True})
    result = ids(viewer, sort="recommended")
    assert source.pk not in result
    assert result.index(unrelated.pk) < result.index(similar.pk)


def test_text_similarity_ignores_question_boilerplate():
    from market.text_recommendations import title_scores
    scores = title_scores([(1, "Будет ли снег завтра?"), (2, "Когда выйдет Python?")],
                          ["Будет ли релиз Python завтра?"], [])
    assert scores[2] > scores[1]
    assert title_scores([(1, "Будет ли?")], ["Когда будет?"], [])[1] == 0
