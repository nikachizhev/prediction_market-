import secrets
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.db.models import Q
from .identity import get_trader
from .models import Notification, Trader, Position, Submission, GroupMembership


def notify(recipient, text, *, kind="info", question=None, group=None, key=None):
    return Notification.objects.get_or_create(key=key or secrets.token_hex(20), defaults={
        "recipient": recipient, "text": text[:500], "kind": kind, "question": question, "group": group,
    })[0]


def visible_notifications(trader):
    groups = GroupMembership.objects.filter(trader=trader, status="active").values("group_id")
    return Notification.objects.filter(recipient=trader).filter(
        Q(question__isnull=True) | Q(question__group__isnull=True) | Q(question__group_id__in=groups))


def market_finished(question):
    ids = set(Position.objects.filter(question=question).values_list("trader_id", flat=True))
    ids.update(Submission.objects.filter(question=question).values_list("trader_id", flat=True))
    ids.update(question.subscriptions.values_list("trader_id", flat=True))
    if question.creator_id:
        ids.add(question.creator_id)
    for trader in Trader.objects.filter(pk__in=ids):
        from .access import can_access
        if not can_access(trader, question):
            continue
        result = question.result_label or "Результат опубликован"
        notify(trader, f"{question.title} — {result}.", kind="market_result", question=question,
               key=f"market-result:{question.pk}:{trader.pk}")


def notification_url(item):
    if item.question_id:
        return reverse("market:question", args=[item.question_id])
    if item.group_id:
        return reverse("market:group_detail", args=[item.group_id])
    return reverse("market:friends")


@login_required
def inbox(request):
    items = visible_notifications(get_trader(request))
    return render(request, "market/notifications.html", {"notifications": items[:100]})


@login_required
def feed(request):
    items = visible_notifications(get_trader(request))
    response = JsonResponse({"unread": items.filter(read_at__isnull=True).count(), "items": [
        {"id": item.pk, "text": item.text, "read": bool(item.read_at), "url": notification_url(item)} for item in items[:8]
    ]})
    response["Cache-Control"] = "no-store"
    response["Vary"] = "Cookie"
    return response


@login_required
@require_POST
def read(request, pk):
    item = get_object_or_404(visible_notifications(get_trader(request)), pk=pk)
    if not item.read_at:
        item.read_at = timezone.now()
        item.save(update_fields=["read_at"])
    return redirect(notification_url(item))


@login_required
@require_POST
def read_all(request):
    visible_notifications(get_trader(request)).filter(read_at__isnull=True).update(read_at=timezone.now())
    return redirect("market:notifications")
