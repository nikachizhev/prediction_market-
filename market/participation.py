from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from .models import Question, Submission
from .services import check_open, check_access, MarketError, close_betting, resolve


def serialize(value):
    return str(value) if isinstance(value, Decimal) else value


@transaction.atomic
def submit(trader, question, value):
    question = Question.objects.select_for_update().get(pk=question.pk)
    check_access(trader, question)
    check_open(question)
    if question.is_trading:
        raise MarketError("Для рынка используйте покупку акций.")
    if question.creator_id == trader.pk or question.conflicted_traders.filter(pk=trader.pk).exists():
        raise MarketError("Автор и участники с конфликтом интересов не могут отвечать.")
    from .question_forms import ResponseForm
    form = ResponseForm({"value": value}, question=question)
    if not form.is_valid():
        raise MarketError("Проверьте ответ и допустимый диапазон.")
    if Submission.objects.filter(trader=trader, question=question).exists():
        raise MarketError("Ответ уже принят и не может быть изменён.")
    return Submission.objects.create(trader=trader, question=question, value=serialize(form.cleaned_data["value"]))


@transaction.atomic
def finish(question, trader, value=None, *, administrator=False):
    question = Question.objects.select_for_update().get(pk=question.pk)
    if not administrator:
        check_access(trader, question)
    if not administrator and question.creator_id != trader.pk:
        raise MarketError("Завершить вопрос может только автор.")
    if question.status in ("resolved", "annulled"):
        raise MarketError("Вопрос уже завершён.")
    if question.kind == "poll":
        question.status = "closed"
        question.resolved_at = timezone.now()
        question.save(update_fields=["status", "resolved_at"])
        from .notifications import market_finished
        market_finished(question)
        return
    from .question_forms import ResultForm
    form = ResultForm({"value": value}, question=question)
    if not form.is_valid():
        raise MarketError("Укажите корректный результат.")
    if question.is_trading:
        close_betting(question)
        resolve(question, form.cleaned_data["value"])
    else:
        question.answer = serialize(form.cleaned_data["value"])
        question.status = "resolved"
        question.resolved_at = timezone.now()
        question.save(update_fields=["answer", "status", "resolved_at"])
        from .notifications import market_finished
        market_finished(question)
