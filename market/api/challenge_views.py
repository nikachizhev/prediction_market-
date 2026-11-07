"""1-on-1 challenges (spec E11; docs/api.md «Фаза 3e»). Money moves only through `services`."""
from __future__ import annotations

from django.conf import settings
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .. import services
from ..events import log_event
from ..models import Challenge, Trader
from . import serializers as s
from .views import BadRequest, amount_of, body, endpoint


def _link(challenge: Challenge) -> str:
    return (f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}/{settings.TELEGRAM_APP_NAME}"
            f"?startapp=ch_{challenge.code}")


def _visible(code: str, trader: Trader) -> Challenge:
    """Participants see theirs; anyone with the link sees it only while it can be accepted."""
    challenge = Challenge.objects.select_related("author", "opponent").get(code=code)
    if trader.pk in (challenge.author_id, challenge.opponent_id):
        return challenge
    if challenge.status != Challenge.Status.OPEN or challenge.check_state == "rejected":
        raise Challenge.DoesNotExist
    return challenge


def _result(challenge: Challenge, trader: Trader) -> tuple[str | None, str | None]:
    if challenge.status == Challenge.Status.CANCELLED:
        return "refunded", s.money(challenge.stake)
    if challenge.status != Challenge.Status.FINISHED:
        return None, None
    if challenge.winner_id == trader.pk:
        return "won", s.money(challenge.payout)
    return "lost", s.money(0)


def serialize(challenge: Challenge, trader: Trader) -> dict:
    now = timezone.now()
    author = trader.pk == challenge.author_id
    opponent = trader.pk == challenge.opponent_id
    participant = author or opponent
    other_side = "no" if challenge.side == "yes" else "yes"
    mine = challenge.author_answer if author else challenge.opponent_answer if opponent else ""
    rival = challenge.opponent_answer if author else challenge.author_answer if opponent else ""
    is_open = challenge.status == Challenge.Status.OPEN
    result, payout = _result(challenge, trader) if participant else (None, None)
    return {
        "code": challenge.code,
        "title": challenge.title,
        "criteria": challenge.criteria,
        "role": "author" if author else "opponent" if opponent else "guest",
        "my_side": challenge.side if author else other_side,
        "stake": s.money(challenge.stake),
        "pot": s.money(challenge.stake * 2),
        "prize": s.money(services.challenge_prize(challenge.stake)),
        "resolves_at": s.iso(challenge.resolves_at),
        "expires_at": s.iso(challenge.expires_at),
        "status": challenge.status,
        "author_name": challenge.author.display_name,
        "opponent_name": challenge.opponent.display_name if challenge.opponent else None,
        "link": _link(challenge) if author and is_open else None,
        "can_accept": not participant and is_open and challenge.expires_at > now,
        "can_cancel": author and is_open,
        "can_answer": (participant and challenge.status == Challenge.Status.ACTIVE
                       and challenge.resolves_at <= now and not mine),
        "my_answer": mine or None,
        "rival_answer": rival or None,
        "answer_until": s.iso(challenge.answer_until),
        "dispute_cost": s.money(settings.DISPUTE_COST),
        "proof_until": s.iso(challenge.proof_until) if challenge.status == Challenge.Status.DISPUTED else None,
        "outcome": challenge.outcome or None,
        "result": result,
        "payout": payout,
        "closed_reason": challenge.closed_reason or None,
    }


def _reply(challenge: Challenge, trader: Trader) -> dict:
    trader.refresh_from_db(fields=["balance"])
    challenge = Challenge.objects.select_related("author", "opponent").get(pk=challenge.pk)
    return {"challenge": serialize(challenge, trader), "balance": s.money(trader.balance)}


@endpoint("GET", "POST")
def challenges(request):
    trader = request.trader
    if request.method == "GET":
        mine = (Challenge.objects.filter(Q(author=trader) | Q(opponent=trader))
                .select_related("author", "opponent").order_by("-created_at", "-id")[:100])
        return {"challenges": [serialize(c, trader) for c in mine]}
    data = body(request)
    resolves_at = parse_datetime(str(data.get("resolves_at") or ""))
    if resolves_at is not None and timezone.is_naive(resolves_at):
        raise BadRequest("Укажите дату с часовым поясом.")
    challenge = services.create_challenge(
        trader, title=str(data.get("title") or ""), criteria=str(data.get("criteria") or ""),
        side=str(data.get("side") or ""), stake=amount_of(data.get("stake")), resolves_at=resolves_at,
        request_key=data.get("request_key") or None)
    log_event(trader, "challenge_create", {"stake": str(challenge.stake)})
    return _reply(challenge, trader)


@endpoint("GET")
def challenge(request, code):
    found = _visible(code, request.trader)
    if request.trader.pk not in (found.author_id, found.opponent_id):
        log_event(request.trader, "challenge_open", {})
    return _reply(found, request.trader)


@endpoint("POST")
def accept(request, code):
    found = services.accept_challenge(request.trader, _visible(code, request.trader))
    log_event(request.trader, "challenge_accept", {"stake": str(found.stake)})
    return _reply(found, request.trader)


@endpoint("POST")
def cancel(request, code):
    found = services.cancel_challenge(request.trader, _visible(code, request.trader))
    log_event(request.trader, "challenge_cancel", {})
    return _reply(found, request.trader)


@endpoint("POST")
def answer(request, code):
    data = body(request)
    found = services.answer_challenge(request.trader, _visible(code, request.trader), str(data.get("answer") or ""))
    log_event(request.trader, "challenge_answer", {"status": found.status})
    return _reply(found, request.trader)


@endpoint("POST")
def complaint(request, code):
    data = body(request)
    services.report_challenge(request.trader, _visible(code, request.trader), str(data.get("reason") or ""))
    return {"ok": True}
