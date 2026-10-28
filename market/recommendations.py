"""Explainable cold-start ranking, computed in SQL before pagination."""
import math
from datetime import timedelta
from django.db.models import Case, Count, Exists, F, IntegerField, OuterRef, Q, Value, When, Window
from django.db.models.functions import RowNumber
from .models import Event, FeedDismissal, Question, Trade

VERSION = "rules-v3-text"


def behavioral_scores(trader, now):
    """Bound influence per category; repeated clicks and money cannot buy ranking."""
    since = now - timedelta(days=30)
    opened = set(Event.objects.filter(trader=trader, name="market_open", created_at__gte=since)
                 .values_list("props__market", flat=True))
    opened = {pk for pk in opened if type(pk) is int}
    purchased = set(Trade.objects.filter(trader=trader, shares__gt=0, created_at__gte=since)
                    .values_list("question_id", flat=True))
    scores = {}
    for pk, category in Question.objects.filter(pk__in=opened | purchased).values_list("pk", "category"):
        scores[category] = min(6, scores.get(category, 0) + (2 if pk in purchased else 1))
    return scores


def exclude_dismissed(queryset, trader):
    return queryset.exclude(pk__in=FeedDismissal.objects.filter(trader=trader).values("question_id"))


def recommended(queryset, trader, now):
    # Mini App lists LMSR: odds >=9 imply probability >=90%.
    log_odds = math.log(9)
    skew = Q(q_yes__gte=F("q_no") + F("b") * log_odds) | Q(q_no__gte=F("q_yes") + F("b") * log_odds)
    unavailable = ~Q(status="open") | Q(deadline__lte=now) | Q(creator_id=trader.pk)
    bought = Trade.objects.filter(trader=trader, question_id=OuterRef("pk"), shares__gt=0)
    scores = behavioral_scores(trader, now)
    from .text_recommendations import title_scores
    since = now - timedelta(days=30)
    positive_ids = {pk for pk in Event.objects.filter(trader=trader, name="market_open", created_at__gte=since)
                    .values_list("props__market", flat=True) if type(pk) is int}
    positive_ids.update(Trade.objects.filter(trader=trader, shares__gt=0, created_at__gte=since)
                        .values_list("question_id", flat=True))
    negative_ids = set(FeedDismissal.objects.filter(trader=trader).values_list("question_id", flat=True))
    # Only accessible candidates supply text, including the user's history examples.
    from .access import visible_questions
    history = visible_questions(trader)
    text = title_scores(list(queryset.values_list("pk", "title")),
                        list(history.filter(pk__in=positive_ids-negative_ids).values_list("title", flat=True)),
                        list(history.filter(pk__in=negative_ids).values_list("title", flat=True))) if positive_ids or negative_ids else {}
    return queryset.annotate(
        feed_traded=Exists(bought),
        feed_text=Case(*[When(pk=pk, then=Value(score)) for pk, score in text.items() if score], default=Value(0), output_field=IntegerField()),
        feed_behavior=Case(*[When(category=category, then=Value(score)) for category, score in scores.items()], default=Value(0), output_field=IntegerField()),
        feed_interest=Case(When(category__in=trader.interests or [], then=Value(1)), default=Value(0), output_field=IntegerField()),
        feed_skew=Case(When(skew, then=Value(1)), default=Value(0), output_field=IntegerField()),
        feed_unavailable=Case(When(unavailable, then=Value(1)), default=Value(0), output_field=IntegerField()),
        feed_own=Case(When(creator_id=trader.pk, then=Value(1)), default=Value(0), output_field=IntegerField()),
        feed_recent=Count("trades__trader", filter=Q(trades__created_at__gte=now-timedelta(days=1), trades__shares__gt=0), distinct=True),
    ).annotate(feed_category_rank=Window(
        expression=RowNumber(),
        partition_by=[F("category"), F("feed_unavailable"), F("feed_traded"), F("feed_skew")],
        order_by=[F("feed_text").desc(), F("feed_recent").desc(), F("created_at").desc(), F("id").desc()],
    )).order_by("feed_unavailable", "feed_traded", "feed_skew", "-feed_interest", "feed_category_rank", "-feed_text", "-feed_behavior", "-feed_recent", "-created_at", "-id")


def reason(question):
    if question.feed_unavailable:
        return "Ваш рынок" if question.feed_own else "Ожидает результата"
    if question.feed_traded:
        return "Вы уже делали прогноз"
    if question.feed_skew:
        return "Большинство склоняется к одному исходу"
    if question.feed_interest:
        return "По вашим интересам"
    if question.feed_behavior:
        return "Вы интересовались этой темой"
    return "Популярно сейчас" if question.feed_recent else "Новый рынок"
