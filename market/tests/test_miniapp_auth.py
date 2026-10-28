import json
import time
from decimal import Decimal

import pytest
from django.http import JsonResponse
from django.test import RequestFactory, override_settings

from market.miniapp_auth import InvalidInitData, api_view, get_or_create_trader, sign_init_data, verify_init_data
from market.models import LedgerEntry, Trader

TOKEN = "123456:TEST-token"


def init_data(user_id=42, *, token=TOKEN, auth_date=None, start_param="", **user):
    fields = {
        "auth_date": str(int(auth_date if auth_date is not None else time.time())),
        "user": json.dumps({"id": user_id, "first_name": "Саша", **user}, ensure_ascii=False),
    }
    if start_param:
        fields["start_param"] = start_param
    return sign_init_data(fields, token)


def test_valid_signature_accepted():
    session = verify_init_data(init_data(7, start_param="ref_x"), TOKEN)
    assert session.user["id"] == 7 and session.start_param == "ref_x"


@pytest.mark.parametrize("bad", [
    lambda: init_data(token="999:other"),                      # signed with another bot
    lambda: init_data().replace("%D0%A1", "%D0%9F"),           # tampered name
    lambda: init_data() + "&user=%7B%22id%22%3A1%7D",          # duplicated field
    lambda: init_data().split("&hash=")[0],                     # no hash
    lambda: "",
])
def test_invalid_signature_rejected(bad):
    with pytest.raises(InvalidInitData):
        verify_init_data(bad(), TOKEN)


def test_stale_auth_date_rejected():
    old = init_data(auth_date=time.time() - 25 * 3600)
    with pytest.raises(InvalidInitData):
        verify_init_data(old, TOKEN, max_age=24 * 3600)


def test_no_bot_token_rejects_everything():
    with pytest.raises(InvalidInitData):
        verify_init_data(init_data(), "")


@pytest.mark.django_db
def test_first_open_creates_account_with_starter_pack_once():
    session = verify_init_data(init_data(42, username="sasha", photo_url="https://t.me/i/a.jpg"), TOKEN)
    trader, created = get_or_create_trader(session)
    assert created and trader.telegram_chat_id == 42 and trader.name == "Саша"
    assert trader.balance == Decimal(1000)
    assert trader.ledger.get().kind == LedgerEntry.Kind.STARTER
    assert not trader.show_real_name and trader.pseudonym  # @username is not shown by default
    again, created = get_or_create_trader(verify_init_data(init_data(42), TOKEN))
    assert not created and again.pk == trader.pk
    again.refresh_from_db()
    assert again.balance == Decimal(1000) and Trader.objects.count() == 1


@pytest.mark.django_db
def test_referrer_set_only_on_creation_and_not_self():
    inviter, _ = get_or_create_trader(verify_init_data(init_data(1), TOKEN))
    friend, _ = get_or_create_trader(verify_init_data(init_data(2, start_param=f"ref_{inviter.referral_code}"), TOKEN))
    assert friend.referred_by == inviter
    # Opening someone else's link later does not change the inviter.
    other, _ = get_or_create_trader(verify_init_data(init_data(3), TOKEN))
    friend, _ = get_or_create_trader(verify_init_data(init_data(2, start_param=f"ref_{other.referral_code}"), TOKEN))
    friend.refresh_from_db()
    assert friend.referred_by == inviter
    # Own link on an existing account does nothing.
    inviter, _ = get_or_create_trader(verify_init_data(init_data(1, start_param=f"ref_{inviter.referral_code}"), TOKEN))
    inviter.refresh_from_db()
    assert inviter.referred_by is None


@pytest.mark.django_db
def test_existing_bot_account_is_reused_without_second_pack():
    legacy = Trader.objects.create(name="Old", pseudonym="Old", telegram_chat_id=55)
    trader, created = get_or_create_trader(verify_init_data(init_data(55), TOKEN))
    assert not created and trader.pk == legacy.pk and trader.balance == Decimal(1000)
    assert not trader.ledger.exists() and trader.referral_code


@pytest.mark.django_db
@override_settings(TELEGRAM_BOT_TOKEN=TOKEN)
def test_api_view_requires_signed_header():
    view = api_view(lambda request: JsonResponse({"id": request.trader.telegram_chat_id}), create=True)
    rf = RequestFactory()
    assert view(rf.get("/api/me")).status_code == 401
    # A bare Telegram id without a signature is never accepted.
    assert view(rf.get("/api/me", HTTP_AUTHORIZATION="tma user=%7B%22id%22%3A42%7D")).status_code == 401
    ok = view(rf.get("/api/me", HTTP_AUTHORIZATION=f"tma {init_data(42)}"))
    assert ok.status_code == 200 and json.loads(ok.content) == {"id": 42}


def test_non_ascii_hash_is_rejected_not_crashing():
    bad = init_data().split("&hash=")[0] + "&hash=%D0%AF"
    with pytest.raises(InvalidInitData):
        verify_init_data(bad, TOKEN)
