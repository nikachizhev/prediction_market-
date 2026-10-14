import math
from decimal import Decimal
from django.shortcuts import redirect, render
from django.contrib import messages
from django.views.decorators.http import require_POST
from .identity import get_trader
from .models import Trade, Submission

CHARACTERS = [
    {"key": "vesta", "name": "Веста", "role": "Ведьмочка", "description": "Собирает созвездия в хрустальном шаре и находит магию в смелых прогнозах."},
    {"key": "klep", "name": "Клёп", "role": "Звёздный ворон", "description": "Замечает детали, которые упускают другие. Верные предсказания зажигают новые звёзды."},
    {"key": "fox", "name": "Фокс", "role": "Лис-предсказатель", "description": "Доверяет любопытству и чутью. Его талисманы хранят историю ваших открытий."},
]
LEVELS = [(0, 0, "Ученик"), (100, 2, "Предсказатель"), (300, 7, "Звёздный проводник"), (700, 15, "Оракул")]


def progression(trader, stats):
    trades = Trade.objects.filter(trader=trader).exclude(question__status="annulled")
    answers = Submission.objects.filter(trader=trader).exclude(question__status="annulled").exclude(question__kind="poll")
    count = len(set(trades.values_list("question_id", flat=True)) | set(answers.values_list("question_id", flat=True)))
    spent = sum(trades.filter(cost__gt=0).values_list("cost", flat=True), Decimal(0))
    money_xp = min(50, math.isqrt(int(spent)))
    xp = count*10 + stats["correct"]*20 + money_xp
    level = 0
    for i, (threshold, wins, _) in enumerate(LEVELS):
        if xp >= threshold and stats["correct"] >= wins:
            level = i
    result = {"character": next((c for c in CHARACTERS if c["key"] == trader.mascot), None),
              "level": level+1, "title": LEVELS[level][2], "xp": xp,
              "participations": count, "money_xp": money_xp, "stars": range(min(stats["correct"], 15))}
    if level < 3:
        threshold, wins, title = LEVELS[level+1]
        result.update(next_title=title, missing_xp=max(0, threshold-xp), missing_correct=max(0, wins-stats["correct"]), progress=min(100, int(xp/threshold*100)))
    else:
        result["progress"] = 100
    return result


def choose(request):
    trader = get_trader(request)
    if not trader:
        return redirect("market:login")
    if request.method == "POST":
        key = request.POST.get("mascot")
        if key not in {c["key"] for c in CHARACTERS}:
            messages.error(request, "Выберите одного из трёх проводников.")
        else:
            trader.mascot = key
            trader.save(update_fields=["mascot"])
            messages.success(request, "Проводник выбран. Опыт сохранится при смене персонажа.")
            return redirect("market:personal_rating")
    return render(request, "market/choose_mascot.html", {"characters": CHARACTERS, "selected": trader.mascot})


def welcome(request):
    trader = get_trader(request)
    return redirect("market:mascot" if trader and not trader.mascot else "market:markets")


@require_POST
def visibility(request):
    trader = get_trader(request)
    if not trader:
        return redirect("market:login")
    trader.show_companion = request.POST.get("visible") == "1"
    trader.save(update_fields=["show_companion"])
    from django.http import JsonResponse
    return JsonResponse({"visible": trader.show_companion})
