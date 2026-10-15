from __future__ import annotations

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from market.models import Trader

from . import services
from .models import Experiment


def _sync(request) -> None:
    from sources.sync import sync_all  # abmock may import sources; market must not import abmock

    for m in sync_all():
        messages.info(request, m)


def _float(raw, default, lo=0.0, hi=None):
    try:
        v = float((raw or "").replace(",", "."))
    except ValueError:
        return default
    if v < lo or (hi is not None and v > hi):
        return default
    return v


def index(request):
    if request.method == "POST":
        name = (request.POST.get("name") or "").strip()
        metric = (request.POST.get("metric") or "").strip()
        author = Trader.objects.filter(pk=request.POST.get("author") or 0).first()
        if not (name and metric and author):
            messages.error(request, "Заполните название, метрику и автора.")
        else:
            try:
                n = int(request.POST.get("n_per_group") or 20000)
            except ValueError:
                n = 20000
            exp = Experiment.objects.create(
                name=name, hypothesis=(request.POST.get("hypothesis") or "").strip(),
                metric=metric, author=author,
                baseline_rate=_float(request.POST.get("baseline_rate"), 0.04, 0.0001, 0.99),
                true_lift=_float(request.POST.get("true_lift"), 0.0, -0.99),
                n_per_group=max(n, 10),
            )
            messages.success(request, f"Эксперимент «{exp.name}» создан.")
            _sync(request)
            return redirect("abmock:detail", pk=exp.pk)
    return render(request, "abmock/index.html", {
        "experiments": Experiment.objects.select_related("author"),
        "traders": Trader.objects.order_by("name"),
    })


def detail(request, pk):
    from market.models import Question

    exp = get_object_or_404(Experiment.objects.select_related("author"), pk=pk)
    question = Question.objects.filter(source_type="abtest", source_ref=f"ab:{exp.pk}").first()
    return render(request, "abmock/detail.html", {"exp": exp, "question": question})


@require_POST
def action(request, pk, action):
    exp = get_object_or_404(Experiment, pk=pk)
    fn = {"start": services.start, "finish": services.finish, "cancel": services.cancel}.get(action)
    if fn is None:
        messages.error(request, "Неизвестное действие.")
        return redirect("abmock:detail", pk=pk)
    try:
        fn(exp)
    except services.ExperimentError as e:
        messages.error(request, str(e))
    else:
        _sync(request)
    return redirect("abmock:detail", pk=pk)
