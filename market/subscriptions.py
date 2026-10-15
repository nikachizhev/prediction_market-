from datetime import timedelta
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from .access import visible_questions, can_access
from .identity import get_trader
from .models import MarketSubscription, Question
from .notifications import notify


@login_required
@require_POST
@transaction.atomic
def toggle(request, pk):
    trader = get_trader(request)
    question = get_object_or_404(visible_questions(trader).select_for_update(), pk=pk)
    if request.POST.get("action") == "unsubscribe":
        MarketSubscription.objects.filter(trader=trader, question=question).delete()
        messages.info(request, "Вы больше не следите за этим вопросом.")
    elif question.display_state in ("resolved", "annulled"):
        messages.info(request, "Этот вопрос уже завершён.")
    else:
        MarketSubscription.objects.get_or_create(trader=trader, question=question,
            defaults={"last_probability": question.prob if question.is_trading else None})
        messages.success(request, "Подписка включена. Результат и существенные изменения появятся в уведомлениях.")
    return redirect("market:question", pk=pk)


def price_changed(question):
    now = timezone.now()
    for subscription in question.subscriptions.select_related("trader"):
        if not can_access(subscription.trader, question):
            continue
        baseline = subscription.last_probability
        if baseline is None or abs(question.prob-baseline) < .10 - 1e-9:
            continue
        if subscription.last_alert_at and subscription.last_alert_at > now-timedelta(hours=1):
            continue
        notify(subscription.trader,
               f"{question.title}: вероятность ДА изменилась с {baseline:.0%} до {question.prob:.0%}.",
               kind="market_change", question=question)
        subscription.last_probability = question.prob
        subscription.last_alert_at = now
        subscription.save(update_fields=["last_probability", "last_alert_at"])


@login_required
def index(request):
    trader = get_trader(request)
    questions = visible_questions(trader).filter(subscriptions__trader=trader).order_by("-subscriptions__created_at")
    return render(request, "market/subscriptions.html", {"questions": questions})
