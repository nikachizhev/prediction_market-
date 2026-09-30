"""Thin views: all money/state changes go through market.services."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.contrib import messages
from django.db.models import Count, Sum
from django.db.models.functions import Abs
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from . import services
from .models import Position, Question, Trade, Trader
from .services import MarketError


def _trader(request: HttpRequest) -> Trader | None:
    tid = request.session.get("trader_id")
    return Trader.objects.filter(pk=tid).first() if tid else None


def _need_trader(request: HttpRequest) -> HttpResponse:
    messages.info(request, "Сначала выберите пользователя.")
    return redirect(f"{reverse('market:switch_user')}?next={request.path}")


def _dec(raw: str | None) -> Decimal:
    try:
        return Decimal((raw or "").strip().replace(",", "."))
    except InvalidOperation:
        raise MarketError("Введите число.")


def question_list(request: HttpRequest) -> HttpResponse:
    questions = list(
        Question.objects.annotate(n_traders=Count("trades__trader", distinct=True))
    )
    for q in questions:
        q.pct = round(q.prob * 100)
    return render(request, "market/question_list.html", {"questions": questions})


def question_detail(request: HttpRequest, pk: int) -> HttpResponse:
    question = get_object_or_404(Question, pk=pk)
    trader = _trader(request)
    trades = list(question.trades.all())

    labels = [question.created_at.astimezone().strftime("%d.%m %H:%M")]
    points = [round(question.initial_prob * 100, 1)]
    for t in trades:
        labels.append(t.created_at.astimezone().strftime("%d.%m %H:%M"))
        points.append(round(t.prob_after * 100, 1))

    feed = [
        {
            "verb": "купил" if t.shares > 0 else "продал",
            "side": t.get_side_display(),
            "amount": abs(t.cost),
            "before": round(t.prob_before * 100),
            "after": round(t.prob_after * 100),
            "at": t.created_at,
        }
        for t in reversed(trades)
    ][:30]

    agg = question.trades.aggregate(volume=Sum(Abs("cost")))
    ctx = {
        "question": question,
        "pct": round(question.prob * 100, 1),
        "chart_labels": labels,
        "chart_points": points,
        "feed": feed,
        "volume": agg["volume"] or Decimal(0),
        "participants": question.trades.values("trader").distinct().count(),
        "max_bet": settings.MAX_BET,
        "position": None,
        "conflicted": False,
        "can_bet": False,
    }
    if trader:
        ctx["conflicted"] = question.conflicted_traders.filter(pk=trader.pk).exists()
        ctx["can_bet"] = question.status == Question.Status.OPEN and not ctx["conflicted"]
        pos = Position.objects.filter(trader=trader, question=question).first()
        if pos and (pos.yes_shares > 0 or pos.no_shares > 0 or pos.spent > 0):
            p = question.prob
            ctx["position"] = {
                "pos": pos,
                "value": (float(pos.yes_shares) * p + float(pos.no_shares) * (1 - p)),
                "win_yes": pos.yes_shares.quantize(Decimal("0.01")),
                "win_no": pos.no_shares.quantize(Decimal("0.01")),
            }
    return render(request, "market/question_detail.html", ctx)


def quote_view(request: HttpRequest, pk: int) -> JsonResponse:
    question = get_object_or_404(Question, pk=pk)
    try:
        q = services.quote(question, request.GET.get("side", ""), _dec(request.GET.get("amount")))
    except MarketError as e:
        return JsonResponse({"error": str(e)}, status=400)
    return JsonResponse(
        {
            "side": q["side"],
            "shares": float(q["shares"]),
            "cost": float(q["cost"]),
            "prob_before": q["prob_before"],
            "new_prob": q["new_prob"],
            "payout_if_win": float(q["payout_if_win"]),
        }
    )


@require_POST
def buy_view(request: HttpRequest, pk: int) -> HttpResponse:
    question = get_object_or_404(Question, pk=pk)
    trader = _trader(request)
    if not trader:
        return _need_trader(request)
    try:
        trade = services.buy(trader, question, request.POST.get("side", ""), _dec(request.POST.get("amount")))
        messages.success(
            request,
            f"Ставка принята: {trade.shares:.2f} акций {trade.get_side_display()} за {trade.cost} монет.",
        )
    except MarketError as e:
        messages.error(request, str(e))
    return redirect("market:question", pk=pk)


@require_POST
def sell_view(request: HttpRequest, pk: int) -> HttpResponse:
    question = get_object_or_404(Question, pk=pk)
    trader = _trader(request)
    if not trader:
        return _need_trader(request)
    try:
        trade = services.sell(trader, question, request.POST.get("side", ""), _dec(request.POST.get("shares")))
        messages.success(request, f"Продано {abs(trade.shares):.2f} акций, получено {abs(trade.cost)} монет.")
    except MarketError as e:
        messages.error(request, str(e))
    return redirect("market:question", pk=pk)


def leaderboard(request: HttpRequest) -> HttpResponse:
    me_ = _trader(request)
    rows = [{"trader": t, "profit": services.profit(t)} for t in Trader.objects.all()]
    rows.sort(key=lambda r: (-r["profit"], r["trader"].pk))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    top = rows[:10]
    my_row = None
    if me_ and all(r["trader"].pk != me_.pk for r in top):
        my_row = next((r for r in rows if r["trader"].pk == me_.pk), None)
    return render(request, "market/leaderboard.html", {"rows": top, "my_row": my_row, "me": me_})


def me(request: HttpRequest) -> HttpResponse:
    trader = _trader(request)
    if not trader:
        return _need_trader(request)
    if request.method == "POST":
        trader.show_real_name = request.POST.get("show_real_name") == "on"
        pseud = (request.POST.get("pseudonym") or "").strip()
        if pseud:
            trader.pseudonym = pseud[:100]
        login = (request.POST.get("github_login") or "").strip()
        trader.github_login = login or None
        trader.save(update_fields=["show_real_name", "pseudonym", "github_login"])
        messages.success(request, "Настройки сохранены.")
        return redirect("market:me")
    positions = []
    for p in Position.objects.filter(trader=trader).select_related("question").order_by("-question__created_at"):
        if p.yes_shares <= 0 and p.no_shares <= 0 and p.spent <= 0:
            continue
        q = p.question
        active = q.status in (Question.Status.OPEN, Question.Status.CLOSED)
        value = float(p.yes_shares) * q.prob + float(p.no_shares) * (1 - q.prob) if active else None
        positions.append({"pos": p, "question": q, "value": value})
    return render(
        request,
        "market/me.html",
        {
            "trader": trader,
            "positions": positions,
            "profit": services.profit(trader),
            "portfolio": services.portfolio_value(trader),
        },
    )


def switch_user(request: HttpRequest) -> HttpResponse:
    nxt = request.POST.get("next") or request.GET.get("next") or reverse("market:index")
    if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}):
        nxt = reverse("market:index")
    if request.method == "POST":
        trader = Trader.objects.filter(pk=request.POST.get("trader_id") or 0).first()
        if trader:
            request.session["trader_id"] = trader.pk
            messages.success(request, f"Вы вошли как {trader.name}.")
            return redirect(nxt)
        messages.error(request, "Пользователь не найден.")
    return render(request, "market/switch_user.html", {"traders": Trader.objects.order_by("name"), "next": nxt})
