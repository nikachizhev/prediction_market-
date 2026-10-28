from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from .identity import get_trader
from .models import Question
from .question_forms import CreateQuestionForm, ResponseForm, ResultForm
from .participation import submit, finish
from .services import MarketError
from .access import visible_questions


def create(request):
    trader = get_trader(request)
    if not trader:
        return redirect("market:login")
    form = CreateQuestionForm(request.POST if request.method == "POST" else None, trader=trader, initial={"group": request.GET.get("group")})
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            question = form.save(commit=False)
            question.creator = trader
            question.options = form.cleaned_data["options"]
            question.pool_yes = question.pool_no = question.b
            question.save()
            question.conflicted_traders.add(trader)
        return redirect("market:question", pk=question.pk)
    return render(request, "market/create.html", {"form": form})


def detail(request, question):
    trader = get_trader(request)
    response = question.submissions.filter(trader=trader).first() if trader else None
    counts = []
    if question.kind in ("poll", "multiple"):
        answers = list(question.submissions.values_list("value", flat=True))
        for i, label in enumerate(question.options):
            count = sum(str(i) in a if isinstance(a, list) else str(i) == a for a in answers)
            counts.append({"label": label, "count": count, "correct": str(i) in (question.answer or [])})
    return render(request, "market/participation.html", {
        "question": question, "response": response,
        "subscribed": bool(trader and question.subscriptions.filter(trader=trader).exists()),
        "response_form": ResponseForm(question=question), "result_form": ResultForm(question=question),
        "counts": counts, "participants": question.submissions.count(),
        "is_creator": bool(trader and question.creator_id == trader.pk),
    })


@require_POST
def respond(request, pk):
    question = get_object_or_404(visible_questions(get_trader(request)), pk=pk)
    trader = get_trader(request)
    if not trader:
        return redirect("market:login")
    value = request.POST.getlist("value") if question.kind == "multiple" else request.POST.get("value")
    try:
        submit(trader, question, value)
        messages.success(request, "Ответ принят.")
    except MarketError as exc:
        messages.error(request, str(exc))
    return redirect("market:question", pk=pk)


@require_POST
def finish_view(request, pk):
    question = get_object_or_404(visible_questions(get_trader(request)), pk=pk)
    trader = get_trader(request)
    if not trader:
        return redirect("market:login")
    value = request.POST.getlist("value") if question.kind == "multiple" else request.POST.get("value")
    try:
        finish(question, trader, value)
        messages.success(request, "Вопрос завершён.")
    except MarketError as exc:
        messages.error(request, str(exc))
    return redirect("market:question", pk=pk)
