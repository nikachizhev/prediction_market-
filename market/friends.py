"""Mutual friendships and personal invitations to existing questions."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .identity import get_trader
from .models import Friendship, MarketInvitation, Question, Trader


def friends_of(trader):
    pairs = Friendship.objects.filter(Q(first=trader) | Q(second=trader), accepted=True)
    ids = [p.second_id if p.first_id == trader.pk else p.first_id for p in pairs]
    return Trader.objects.filter(pk__in=ids, user__is_active=True).select_related("user")


@login_required
def index(request):
    trader = get_trader(request)
    if request.method == "POST":
        action = request.POST.get("action")
        with transaction.atomic():
            if action == "request":
                target = Trader.objects.filter(user__username__iexact=request.POST.get("username", "").strip(), user__is_active=True).first()
                if not target or target.pk == trader.pk:
                    messages.error(request, "Участник не найден или вы указали себя.")
                else:
                    first, second = sorted([trader.pk, target.pk])
                    pair, created = Friendship.objects.get_or_create(first_id=first, second_id=second, defaults={"requested_by": trader})
                    messages.info(request, "Заявка отправлена." if created else "Заявка уже существует или вы уже друзья.")
            elif action in ("accept", "remove"):
                pair = get_object_or_404(Friendship.objects.select_for_update().filter(Q(first=trader) | Q(second=trader)), pk=request.POST.get("pair_id"))
                if action == "accept":
                    if pair.requested_by_id == trader.pk or pair.accepted:
                        messages.error(request, "Эту заявку нельзя принять.")
                    else:
                        pair.accepted = True
                        pair.save(update_fields=["accepted"])
                        messages.success(request, "Друг добавлен.")
                else:
                    pair.delete()
                    messages.success(request, "Заявка или дружба удалена.")
        return redirect("market:friends")
    pairs = Friendship.objects.filter(Q(first=trader) | Q(second=trader)).select_related("first__user", "second__user")
    rows = [{"pair": p, "person": p.second if p.first_id == trader.pk else p.first,
             "incoming": p.requested_by_id != trader.pk} for p in pairs]
    query = request.GET.get("q", "").strip()[:100]
    results = Trader.objects.none()
    if len(query) >= 2:
        results = Trader.objects.filter(user__is_active=True, user__username__icontains=query).exclude(pk=trader.pk).select_related("user")[:20]
    return render(request, "market/friends.html", {
        "rows": rows, "results": results, "query": query,
        "invitations": MarketInvitation.objects.filter(recipient=trader).select_related("sender", "question")[:30],
    })


@login_required
def invite(request, pk):
    trader = get_trader(request)
    question = get_object_or_404(Question, pk=pk)
    available = question.status == Question.Status.OPEN and (not question.deadline or question.deadline > timezone.now())
    friends = friends_of(trader)
    if request.method == "POST":
        if not available:
            messages.error(request, "Этот вопрос больше не принимает ответы.")
        else:
            recipient = get_object_or_404(friends, pk=request.POST.get("friend_id"))
            _, created = MarketInvitation.objects.get_or_create(sender=trader, recipient=recipient, question=question)
            messages.success(request, "Приглашение появится у друга в разделе «Друзья»." if created else "Вы уже пригласили этого друга.")
        return redirect("market:invite_friend", pk=pk)
    return render(request, "market/invite_friend.html", {"question": question, "friends": friends, "available": available})
