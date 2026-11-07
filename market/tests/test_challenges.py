"""1-on-1 challenges (spec E11): money, limits, access, the bot. Telegram is always mocked."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal as D
from threading import Barrier
from unittest.mock import patch

import pytest
from django.db import close_old_connections, connection
from django.test import override_settings
from django.utils import timezone

from market import bot_challenges, premoderation as gate, rating, services
from market.miniapp_auth import _referrer
from market.models import BotMessage, Challenge, LedgerEntry, Trader
from market.services import MarketError
from market.telegram import handle_update
from market.tests.test_bot_resolution_dialog import ANYA, BORIS, CREATOR, bot, make, media, people, press, text  # noqa: F401

pytestmark = pytest.mark.django_db
START = D(1000)


def soon(hours=2):
    return timezone.now() + timedelta(hours=hours)


def create(author, **changes):
    fields = {"title": "Сдам ли я матанализ до января?", "criteria": "ДА, если в зачётке стоит 4 или 5.", "side": "yes",
              "stake": D(50), "resolves_at": soon(), **changes}
    return services.create_challenge(author, **fields)


def due(challenge):
    """The outcome date has come."""
    Challenge.objects.filter(pk=challenge.pk).update(resolves_at=timezone.now() - timedelta(minutes=1))
    challenge.refresh_from_db()
    return challenge


def active(people, **changes):
    creator, anya, *_ = people
    challenge = create(creator, **changes)
    services.accept_challenge(anya, challenge)
    return due(challenge)


def balance(trader):
    return Trader.objects.get(pk=trader.pk).balance


def total():
    return sum(Trader.objects.values_list("balance", flat=True), D(0))


def burned():
    return sum(Challenge.objects.values_list("burned", flat=True), D(0))


def in_challenges():
    """Stakes frozen right now."""
    frozen = D(0)
    for c in Challenge.objects.exclude(status__in=[Challenge.Status.FINISHED, Challenge.Status.CANCELLED]):
        frozen += c.stake * (2 if c.opponent_id else 1) + c.dispute_stake
    return frozen


def assert_conserved(start=START * 4):
    assert total() + in_challenges() + burned() == start


# --- creating --------------------------------------------------------------------------------------

def test_creating_freezes_the_stake_once_for_one_key(people):
    creator = people[0]
    first = create(creator, request_key="k1")
    again = create(creator, request_key="k1")
    assert first.pk == again.pk and balance(creator) == START - 50
    assert first.status == Challenge.Status.OPEN and first.opponent is None
    assert LedgerEntry.objects.filter(trader=creator, kind=LedgerEntry.Kind.CHALLENGE_STAKE).count() == 1
    assert_conserved()


@pytest.mark.parametrize("changes, message", [
    ({"title": "Коротко"}, "Вопрос"),
    ({"criteria": "мало"}, "результат"),
    ({"side": "maybe"}, "сторону"),
    ({"stake": D(9)}, "от 10"),
    ({"stake": D(201)}, "лимита"),
    ({"stake": D("10.005")}, "двумя знаками"),
    ({"resolves_at": timezone.now() + timedelta(minutes=30)}, "не раньше"),
    ({"resolves_at": timezone.now() + timedelta(days=31)}, "не позже"),
])
def test_bad_fields_are_refused_without_a_charge(people, changes, message):
    with pytest.raises(MarketError, match=message):
        create(people[0], **changes)
    assert balance(people[0]) == START and not Challenge.objects.exists()


def test_stake_obeys_the_share_of_balance(people):
    creator = people[0]
    Trader.objects.filter(pk=creator.pk).update(balance=D(100))
    with pytest.raises(MarketError, match="лимита"):
        create(creator, stake=D(21))
    assert create(creator, stake=D(20)).stake == 20


def test_daily_limit(people):
    creator = people[0]
    for _ in range(3):
        create(creator)
    with pytest.raises(MarketError, match="3 новых"):
        create(creator)
    assert balance(creator) == START - 150


def test_acceptance_window_is_the_earlier_of_a_day_and_the_outcome_date(people):
    near = create(people[0], resolves_at=soon(5))
    far = create(people[0], resolves_at=soon(72))
    assert near.expires_at == near.resolves_at
    assert far.expires_at < far.resolves_at
    assert abs((far.expires_at - timezone.now()).total_seconds() - 24 * 3600) < 60


# --- accepting -------------------------------------------------------------------------------------

def test_first_to_accept_takes_the_other_side(people):
    creator, anya, boris, _ = people
    challenge = create(creator)
    accepted = services.accept_challenge(anya, challenge)
    assert accepted.opponent_id == anya.pk and accepted.status == Challenge.Status.ACTIVE
    assert balance(anya) == START - 50
    assert services.accept_challenge(anya, challenge).pk == challenge.pk  # a repeat is free
    assert balance(anya) == START - 50
    with pytest.raises(MarketError, match="уже приняли"):
        services.accept_challenge(boris, challenge)
    assert balance(boris) == START
    assert_conserved()


def test_author_cannot_accept_their_own(people):
    with pytest.raises(MarketError, match="Свой"):
        services.accept_challenge(people[0], create(people[0]))


def test_expired_cannot_be_accepted(people):
    challenge = create(people[0])
    Challenge.objects.filter(pk=challenge.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    with pytest.raises(MarketError, match="вышло"):
        services.accept_challenge(people[1], challenge)


def test_accepting_obeys_the_trade_limit(people):
    creator, anya, *_ = people
    Trader.objects.filter(pk=anya.pk).update(balance=D(100))
    with pytest.raises(MarketError, match="лимита"):
        services.accept_challenge(anya, create(creator))
    assert balance(anya) == 100


def test_same_two_people_are_limited_per_day(people):
    creator, anya, boris, _ = people
    services.accept_challenge(anya, create(creator))
    services.accept_challenge(creator, create(anya))
    third = create(creator)
    with pytest.raises(MarketError, match="одними и теми же"):
        services.accept_challenge(anya, third)
    services.accept_challenge(boris, third)  # another person is fine


def test_author_cancels_before_anyone_accepts(people):
    creator, anya, *_ = people
    challenge = create(creator)
    services.cancel_challenge(creator, challenge)
    services.cancel_challenge(creator, challenge)  # a repeat doesn't pay twice
    assert balance(creator) == START
    with pytest.raises(MarketError):
        services.accept_challenge(anya, challenge)
    accepted = create(creator)
    services.accept_challenge(anya, accepted)
    with pytest.raises(MarketError, match="вместе"):
        services.cancel_challenge(creator, accepted)
    with pytest.raises(MarketError, match="автор"):
        services.cancel_challenge(anya, create(creator))


# --- answers ---------------------------------------------------------------------------------------

def test_same_answers_settle_at_once_with_the_fee_burned(people):
    creator, anya, *_ = people
    challenge = active(people)
    services.answer_challenge(creator, challenge, "yes")
    assert balance(creator) == START - 50
    services.answer_challenge(anya, challenge, "yes")
    challenge.refresh_from_db()
    assert challenge.status == Challenge.Status.FINISHED and challenge.winner_id == creator.pk
    assert challenge.payout == 98 and challenge.burned == 2 and challenge.closed_reason == "agreed"
    assert balance(creator) == START + 48 and balance(anya) == START - 50
    assert_conserved()


def test_opponent_wins_when_the_answer_is_the_other_side(people):
    creator, anya, *_ = people
    challenge = active(people)
    services.answer_challenge(creator, challenge, "no")
    services.answer_challenge(anya, challenge, "no")
    challenge.refresh_from_db()
    assert challenge.winner_id == anya.pk and balance(anya) == START + 48


def test_rounding_never_prints_coins(people):
    creator, anya, *_ = people
    challenge = active(people, stake=D("33.33"))
    services.answer_challenge(creator, challenge, "yes")
    services.answer_challenge(anya, challenge, "yes")
    challenge.refresh_from_db()
    assert challenge.payout == D("65.32")  # 66.66 * 0.98 = 65.3268, down
    assert challenge.payout + challenge.burned == D("66.66")
    assert_conserved()


def test_both_cancel_returns_stakes_without_a_fee(people):
    creator, anya, *_ = people
    challenge = active(people)
    services.answer_challenge(creator, challenge, "cancel")
    services.answer_challenge(anya, challenge, "cancel")
    challenge.refresh_from_db()
    assert challenge.status == Challenge.Status.CANCELLED and challenge.closed_reason == "both_cancelled"
    assert balance(creator) == START and balance(anya) == START and challenge.burned == 0


def test_no_answers_before_the_outcome_date(people):
    creator, anya, *_ = people
    challenge = create(creator)
    services.accept_challenge(anya, challenge)
    with pytest.raises(MarketError, match="дата итога"):
        services.answer_challenge(creator, challenge, "yes")


def test_an_answer_cannot_be_changed_and_a_double_tap_is_free(people):
    creator = people[0]
    challenge = active(people)
    services.answer_challenge(creator, challenge, "yes")
    services.answer_challenge(creator, challenge, "yes")
    with pytest.raises(MarketError, match="уже принят"):
        services.answer_challenge(creator, challenge, "no")


def test_stranger_cannot_answer(people):
    with pytest.raises(Challenge.DoesNotExist):
        services.answer_challenge(people[2], active(people), "yes")


def test_silence_of_the_second_lets_the_first_answer_stand(people):
    creator, anya, *_ = people
    challenge = active(people)
    services.answer_challenge(creator, challenge, "yes")
    with pytest.raises(MarketError):
        services.settle_unanswered_challenge(challenge)
    Challenge.objects.filter(pk=challenge.pk).update(answer_until=timezone.now() - timedelta(seconds=1))
    bot_challenges.tick()
    challenge.refresh_from_db()
    assert challenge.winner_id == creator.pk and challenge.closed_reason == "timeout"


def test_silence_of_both_returns_the_stakes(people):
    creator, anya, *_ = people
    challenge = active(people)
    bot_challenges.tick()
    challenge.refresh_from_db()
    assert challenge.status == Challenge.Status.ACTIVE
    Challenge.objects.filter(pk=challenge.pk).update(resolves_at=timezone.now() - timedelta(hours=73))
    bot_challenges.tick()
    challenge.refresh_from_db()
    assert challenge.closed_reason == "silent" and balance(creator) == START and balance(anya) == START


def test_nobody_accepted_returns_the_authors_stake(people):
    creator = people[0]
    challenge = create(creator)
    Challenge.objects.filter(pk=challenge.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert bot_challenges.tick()["expired"] == 1
    challenge.refresh_from_db()
    assert challenge.closed_reason == "expired" and balance(creator) == START
    assert bot_challenges.tick()["expired"] == 0 and balance(creator) == START


# --- disputes --------------------------------------------------------------------------------------

def disputed(people):
    creator, anya, *_ = people
    challenge = active(people)
    services.answer_challenge(creator, challenge, "yes")
    services.answer_challenge(anya, challenge, "no")
    challenge.refresh_from_db()
    return challenge


def test_different_answers_open_a_dispute_paid_by_the_second(people):
    creator, anya, *_ = people
    challenge = disputed(people)
    assert challenge.status == Challenge.Status.DISPUTED and challenge.disputed_by_id == anya.pk
    assert balance(anya) == START - 70 and balance(creator) == START - 50
    assert challenge.proof_until is not None
    assert_conserved()
    with pytest.raises(MarketError, match="модератор"):
        Challenge.objects.filter(pk=challenge.pk).update(opponent_answer="")
        services.answer_challenge(anya, challenge, "no")


def test_no_coins_for_a_dispute_lets_the_first_answer_stand(people):
    creator, anya, *_ = people
    challenge = active(people)
    services.answer_challenge(creator, challenge, "yes")
    Trader.objects.filter(pk=anya.pk).update(balance=D(10))
    with pytest.raises(MarketError, match="не хватает"):
        services.answer_challenge(anya, challenge, "no")
    challenge.refresh_from_db()
    assert challenge.status == Challenge.Status.ACTIVE and challenge.opponent_answer == ""
    Challenge.objects.filter(pk=challenge.pk).update(answer_until=timezone.now() - timedelta(seconds=1))
    bot_challenges.tick()
    challenge.refresh_from_db()
    assert challenge.winner_id == creator.pk


def test_moderator_backs_the_second_and_returns_the_dispute_stake(people):
    creator, anya, *_ = people
    challenge = disputed(people)
    services.decide_challenge(challenge, "no", actor="django:1")
    challenge.refresh_from_db()
    assert challenge.winner_id == anya.pk and challenge.closed_reason == "moderator" and challenge.decided_by == "django:1"
    assert balance(anya) == START + 48 and balance(creator) == START - 50
    assert_conserved()


def test_moderator_backs_the_first_and_the_dispute_stake_burns(people):
    creator, anya, *_ = people
    challenge = disputed(people)
    services.decide_challenge(challenge, "yes", actor="django:1")
    assert balance(creator) == START + 48 and balance(anya) == START - 70
    assert LedgerEntry.objects.filter(kind=LedgerEntry.Kind.CHALLENGE_DISPUTE_BACK).count() == 0


def test_moderator_cancels_and_everyone_gets_stakes_back(people):
    creator, anya, *_ = people
    challenge = disputed(people)
    services.decide_challenge(challenge, "cancel", actor="django:1")
    challenge.refresh_from_db()
    assert challenge.status == Challenge.Status.CANCELLED and challenge.burned == 0
    assert balance(creator) == START and balance(anya) == START  # the first answer didn't stand


def test_decision_is_final(people):
    challenge = disputed(people)
    services.decide_challenge(challenge, "yes", actor="django:1")
    with pytest.raises(MarketError, match="нет открытого"):
        services.decide_challenge(challenge, "no", actor="django:1")


def test_proof_is_kept_for_each_and_only_in_time(people):
    creator, anya, _, stranger = people
    challenge = disputed(people)
    services.attach_challenge_proof(creator, challenge, note="https://example.test/zachetka")
    challenge.refresh_from_db()
    assert challenge.author_proof_note.endswith("zachetka") and challenge.opponent_proof_note == ""
    with pytest.raises(Challenge.DoesNotExist):
        services.attach_challenge_proof(stranger, challenge, note="x")
    with pytest.raises(MarketError, match="Пришлите"):
        services.attach_challenge_proof(anya, challenge)
    Challenge.objects.filter(pk=challenge.pk).update(proof_until=timezone.now() - timedelta(seconds=1))
    with pytest.raises(MarketError, match="вышло"):
        services.attach_challenge_proof(anya, challenge, note="поздно")


# --- out of the rating ------------------------------------------------------------------------------

def test_challenges_stay_out_of_profit_and_rating(people):
    creator, anya, *_ = people
    assert services.profit(creator) == 0
    challenge = active(people)
    assert services.profit(creator) == 0  # a frozen stake is no loss
    services.answer_challenge(creator, challenge, "yes")
    services.answer_challenge(anya, challenge, "yes")
    assert services.profit(creator) == 0 and services.profit(anya) == 0
    assert rating.realized_profit(creator, None) == 0


def test_open_challenge_counts_as_a_position_for_the_insurance(people):
    creator = people[0]
    challenge = create(creator, stake=D(50))
    Trader.objects.filter(pk=creator.pk).update(balance=D(10))
    assert services.has_open_positions(creator)
    assert services.claim_insurance(Trader.objects.get(pk=creator.pk)) is None
    services.cancel_challenge(creator, challenge)
    assert not services.has_open_positions(creator)


def test_a_challenges_link_makes_its_author_the_inviter(people):
    challenge = create(people[0])
    assert _referrer(f"ch_{challenge.code}").pk == people[0].pk
    assert _referrer("ch_нет-такого") is None


# --- the check of topics ----------------------------------------------------------------------------

@pytest.fixture
def checking():
    with override_settings(PREMODERATION_ENABLED=True, PREMODERATION_API_KEY="k"):
        yield


def test_challenge_works_at_once_and_a_clean_check_changes_nothing(people, checking):
    creator, anya, *_ = people
    challenge = create(creator)
    assert challenge.check_state == "pending"
    services.accept_challenge(anya, challenge)
    with patch.object(gate, "classify", return_value=("approved", "allowed_topic")):
        assert gate.process_challenges() == 1
        assert gate.process_challenges() == 0
    challenge.refresh_from_db()
    assert challenge.check_state == "clear" and challenge.status == Challenge.Status.ACTIVE


def test_forbidden_topic_returns_everything_and_closes_the_link(people, checking):
    creator, anya, *_ = people
    challenge = create(creator)
    services.accept_challenge(anya, challenge)
    with patch.object(gate, "classify", return_value=("rejected", "policy")):
        gate.process_challenges()
    challenge.refresh_from_db()
    assert challenge.check_state == "rejected" and challenge.closed_reason == "rejected"
    assert balance(creator) == START and balance(anya) == START


def test_doubt_keeps_it_working_for_the_moderator(people, checking):
    challenge = create(people[0])
    with patch.object(gate, "classify", return_value=("review", "uncertain")):
        gate.process_challenges()
    challenge.refresh_from_db()
    assert challenge.check_state == "review" and challenge.status == Challenge.Status.OPEN


def test_failures_go_to_the_moderator(people, checking):
    challenge = create(people[0])
    for _ in range(4):
        with patch.object(gate, "classify", return_value=("error", "unavailable")):
            gate.process_challenges()
        Challenge.objects.filter(pk=challenge.pk).update(check_retry_at=timezone.now() - timedelta(seconds=1))
    challenge.refresh_from_db()
    assert challenge.check_state == "review"


def test_complaint_calls_the_moderator_and_a_rejection_after_it_is_final(people):
    creator, anya, *_ = people
    challenge = create(creator)
    services.report_challenge(anya, challenge, "Про чужую личную жизнь")
    challenge.refresh_from_db()
    assert challenge.check_state == "review" and "личную" in challenge.complaint
    services.reject_challenge(challenge)
    services.reject_challenge(challenge)  # no second refund
    assert balance(creator) == START


def test_rejection_during_a_dispute_also_returns_its_stake(people):
    creator, anya, *_ = people
    services.reject_challenge(disputed(people))
    assert balance(creator) == START and balance(anya) == START


# --- the bot ---------------------------------------------------------------------------------------

def messages(trader):
    return BotMessage.objects.filter(trader=trader, kind="challenge")


def test_accepted_goes_to_the_author_even_with_everything_switched_off(people):
    from market.bot_notifications import bot_allowed
    creator, anya, *_ = people
    creator.notify_prefs = {"creator": False, "results": False, "quiet": True}
    creator.save(update_fields=["notify_prefs"])
    challenge = create(creator)
    services.accept_challenge(anya, challenge)
    bot_challenges.challenge_accepted(challenge.pk)
    message = messages(creator).get()
    assert "Твой вызов приняли" in message.text and bot_allowed(creator, "challenge")


def test_outcome_date_asks_both_once(people):
    creator, anya, *_ = people
    challenge = active(people)
    assert bot_challenges.tick()["asked"] == 1
    assert bot_challenges.tick()["asked"] == 0
    for trader in (creator, anya):
        message = messages(trader).get()
        assert "Чем закончилось?" in message.text
        data = [b["callback_data"] for b in message.payload["reply_markup"]["inline_keyboard"][0]]
        assert data == [f"chal:{challenge.code}:yes", f"chal:{challenge.code}:no", f"chal:{challenge.code}:cancel"]


def test_rival_gets_the_first_answer_and_prices_of_a_dispute(people):
    creator, anya, *_ = people
    challenge = active(people)
    services.answer_challenge(creator, challenge, "yes")
    bot_challenges.challenge_rival_answered(challenge.pk, creator.pk)
    message = messages(anya).get()
    assert "Соперник указал: ДА" in message.text
    labels = [b["text"] for b in message.payload["reply_markup"]["inline_keyboard"][0]]
    assert labels == ["ДА", "НЕТ за 20", "Отменить за 20"]


def test_buttons_settle_through_the_service(bot, people):
    creator, anya, *_ = people
    challenge = active(people)
    handle_update(press(CREATOR, f"chal:{challenge.code}:yes"))
    handle_update(press(ANYA, f"chal:{challenge.code}:yes"))
    challenge.refresh_from_db()
    assert challenge.status == Challenge.Status.FINISHED and balance(creator) == START + 48
    assert bot.sent("answerCallbackQuery")


def test_someone_elses_button_does_nothing(bot, people):
    challenge = active(people)
    handle_update(press(BORIS, f"chal:{challenge.code}:yes"))
    challenge.refresh_from_db()
    assert challenge.author_answer == "" and challenge.opponent_answer == ""
    assert "устарела" in bot.sent("sendMessage", BORIS)[-1]["text"]


def test_a_dispute_asks_both_for_proof_and_takes_it_from_a_message(bot, people):
    challenge = disputed(people)
    bot_challenges.challenge_disputed(challenge.pk)
    assert "Пришли доказательство" in messages(people[0]).get().text
    handle_update(text(CREATOR, "https://example.test/zachetka"))
    challenge.refresh_from_db()
    assert challenge.author_proof_note == "https://example.test/zachetka"
    assert "получено" in bot.sent("sendMessage", CREATOR)[-1]["text"]


def test_a_proof_photo_is_kept_with_the_challenge(bot, people):
    from market.tests.test_bot_resolution_dialog import photo
    challenge = disputed(people)
    handle_update(photo(ANYA, "Зачётка"))
    challenge.refresh_from_db()
    assert challenge.opponent_proof and challenge.opponent_proof_note == "Зачётка"


def test_ordinary_text_is_no_proof_without_a_dispute(bot, people):
    challenge = active(people)
    handle_update(text(CREATOR, "привет"))
    challenge.refresh_from_db()
    assert challenge.author_proof_note == ""


def test_the_end_is_told_to_both(people):
    creator, anya, *_ = people
    challenge = active(people)
    services.answer_challenge(creator, challenge, "yes")
    services.answer_challenge(anya, challenge, "yes")
    bot_challenges.challenge_closed(challenge.pk)
    assert "Ты выиграл вызов: +98" in messages(creator).get().text
    assert "Ты проиграл вызов" in messages(anya).get().text


def test_notification_is_not_lost_over_the_daily_cap(bot, people):
    from market import bot_notifications as notices
    creator, anya, *_ = people
    challenge = create(creator)
    services.accept_challenge(anya, challenge)
    with patch.object(notices, "_quiet_until", return_value=None), override_settings(NOTIFY_DAILY_CAP=0):
        bot_challenges.challenge_accepted(challenge.pk)
        notices.flush(timezone.now() + timedelta(minutes=1))
    assert messages(creator).get().status == BotMessage.Status.SENT


# --- PostgreSQL -------------------------------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_two_people_accepting_at_once_get_one_opponent():
    if connection.vendor != "postgresql":
        pytest.skip("needs real row locks")
    author, first, second = (Trader.objects.create(name=f"Игрок {i}", pseudonym=f"p{i}", balance=START) for i in range(3))
    challenge = create(author)
    barrier = Barrier(2)

    def accept(trader):
        close_old_connections()
        barrier.wait()
        try:
            services.accept_challenge(trader, challenge)
            return True
        except MarketError:
            return False
        finally:
            connection.close()

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(accept, [first, second]))
    assert sorted(results) == [False, True]
    assert sum(balance(t) for t in (first, second)) == START * 2 - 50


@pytest.mark.django_db(transaction=True)
def test_both_answering_at_once_settle_once():
    if connection.vendor != "postgresql":
        pytest.skip("needs real row locks")
    author, rival = (Trader.objects.create(name=f"Игрок {i}", pseudonym=f"q{i}", balance=START) for i in range(2))
    challenge = create(author)
    services.accept_challenge(rival, challenge)
    due(challenge)
    barrier = Barrier(2)

    def answer(trader):
        close_old_connections()
        barrier.wait()
        try:
            services.answer_challenge(trader, challenge, "yes")
        finally:
            connection.close()

    with ThreadPoolExecutor(2) as pool:
        list(pool.map(answer, [author, rival]))
    assert LedgerEntry.objects.filter(kind=LedgerEntry.Kind.CHALLENGE_PAYOUT).count() == 1
    assert balance(author) == START + 48


# --- the API ----------------------------------------------------------------------------------------

@pytest.fixture
def api(client, settings):
    from market.tests.test_api import Api
    from market.tests.test_miniapp_auth import TOKEN
    settings.TELEGRAM_BOT_TOKEN = TOKEN
    settings.TELEGRAM_BOT_USERNAME = "market_bot"
    settings.TELEGRAM_APP_NAME = "app"
    author = Api(client, 91001)
    author.post("session")
    return author


def other_api(client, tg):
    from django.test import Client
    from market.tests.test_api import Api
    person = Api(Client(), tg)
    person.post("session")
    return person


def payload(**changes):
    return {"title": "Сдам ли я матанализ до января?", "criteria": "ДА, если в зачётке стоит 4 или 5.", "side": "yes",
            "stake": 50, "resolves_at": soon().isoformat(), "request_key": "k1", **changes}


def test_api_creates_and_guests_see_the_terms_only_while_open(api, client):
    code, data = api.post("challenges", payload())
    assert code == 200 and float(data["balance"]) == 950
    item = data["challenge"]
    assert item["role"] == "author" and item["my_side"] == "yes" and float(item["prize"]) == 98
    assert item["link"].endswith(f"startapp=ch_{item['code']}") and item["can_cancel"] and not item["can_accept"]
    assert api.post("challenges", payload())[1]["challenge"]["code"] == item["code"]  # no second charge
    guest = other_api(client, 91002)
    code, seen = guest.get(f"challenges/{item['code']}")
    assert code == 200 and seen["challenge"]["role"] == "guest" and seen["challenge"]["my_side"] == "no"
    assert seen["challenge"]["can_accept"] and seen["challenge"]["link"] is None
    code, accepted = guest.post(f"challenges/{item['code']}/accept", {})
    assert code == 200 and accepted["challenge"]["role"] == "opponent" and accepted["challenge"]["status"] == "active"
    stranger = other_api(client, 91003)
    assert stranger.get(f"challenges/{item['code']}")[0] == 404
    assert stranger.post(f"challenges/{item['code']}/accept", {})[0] == 404
    assert stranger.post(f"challenges/{item['code']}/answer", {"answer": "yes"})[0] == 404
    assert stranger.get("challenges")[1]["challenges"] == []


def test_api_rules_errors_are_400_and_a_bad_stake_too(api):
    assert api.post("challenges", payload(side="maybe"))[0] == 400
    assert api.post("challenges", payload(stake="много"))[0] == 400
    assert api.post("challenges", payload(resolves_at="завтра"))[0] == 400
    assert api.get("challenges/нет-такого")[0] == 404


def test_api_whole_way_to_the_payout(api, client):
    code = api.post("challenges", payload())[1]["challenge"]["code"]
    guest = other_api(client, 91002)
    guest.post(f"challenges/{code}/accept", {})
    Challenge.objects.filter(code=code).update(resolves_at=timezone.now() - timedelta(minutes=1))
    assert api.get(f"challenges/{code}")[1]["challenge"]["can_answer"]
    first = api.post(f"challenges/{code}/answer", {"answer": "yes"})[1]["challenge"]
    assert first["my_answer"] == "yes" and first["rival_answer"] is None and first["answer_until"]
    assert guest.get(f"challenges/{code}")[1]["challenge"]["rival_answer"] == "yes"
    done = guest.post(f"challenges/{code}/answer", {"answer": "yes"})[1]["challenge"]
    assert done["status"] == "finished" and done["result"] == "lost" and done["closed_reason"] == "agreed"
    mine = api.get("challenges")[1]["challenges"][0]
    assert mine["result"] == "won" and float(mine["payout"]) == 98


def test_api_complaint_goes_to_the_moderator(api, client):
    code = api.post("challenges", payload())[1]["challenge"]["code"]
    guest = other_api(client, 91002)
    assert guest.post(f"challenges/{code}/complaint", {"reason": "Запрещённая тема"})[1] == {"ok": True}
    assert Challenge.objects.get(code=code).check_state == "review"


# --- the admin --------------------------------------------------------------------------------------

def test_admin_decides_a_dispute_with_an_action(people, client, django_user_model):
    challenge = disputed(people)
    admin = django_user_model.objects.create_superuser("mod", "m@example.com", "x")
    client.force_login(admin)
    response = client.post("/admin/market/challenge/", {"action": "decide_no", "_selected_action": [challenge.pk]})
    assert response.status_code == 302
    challenge.refresh_from_db()
    assert challenge.winner_id == people[1].pk and challenge.decided_by == f"django:{admin.pk}"
