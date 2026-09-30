"""Adapter between the market and the (mock) experiments platform."""
from __future__ import annotations

from django.conf import settings

from abmock.models import Experiment
from market.models import Question

from .base import QuestionDraft, SourceUpdate

CRITERIA = (
    "Вопрос разрешается в ДА, если по итогам завершённого эксперимента односторонний z-тест "
    "для разности долей (вариант B против контроля A) даёт p < 0.05 в пользу варианта B. "
    "Иначе — НЕТ. Если эксперимент отменён, вопрос аннулируется и ставки возвращаются. "
    "Ставки закрываются в момент запуска эксперимента."
)


class ABTestSource:
    source_type = "abtest"

    def discover(self) -> list[QuestionDraft]:
        drafts = []
        for exp in Experiment.objects.exclude(status=Experiment.Status.CANCELLED).select_related("author"):
            drafts.append(
                QuestionDraft(
                    title=f"Даст ли вариант B статистически значимый рост «{exp.metric}»?",
                    resolution_criteria=CRITERIA,
                    source_ref=f"ab:{exp.pk}",
                    deadline=None,
                    initial_prob=float(settings.AB_BASE_RATE),
                    conflicted_trader_ids=[exp.author_id],
                )
            )
        return drafts

    def check(self, question: Question) -> SourceUpdate | None:
        try:
            pk = int(question.source_ref.split(":", 1)[1])
            exp = Experiment.objects.get(pk=pk)
        except (IndexError, ValueError, Experiment.DoesNotExist):
            return None
        S = Experiment.Status
        if exp.status == S.CANCELLED:
            return SourceUpdate.ANNUL
        if exp.status == S.RUNNING:
            return SourceUpdate.CLOSE_BETTING if question.status == Question.Status.OPEN else None
        if exp.status == S.FINISHED and exp.p_value is not None:
            return SourceUpdate.RESOLVE_YES if exp.p_value < 0.05 else SourceUpdate.RESOLVE_NO
        return None
