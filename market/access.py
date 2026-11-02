"""Shared visibility rules for web, API and bot."""
from django.db.models import Q
from .models import Question, GroupMembership


def visible_questions(trader=None):
    public = Q(group__isnull=True)
    if trader:
        groups = GroupMembership.objects.filter(trader=trader, status="active").values("group_id")
        return Question.objects.filter(publication_state="approved").filter(public | Q(group_id__in=groups))
    return Question.objects.filter(publication_state="approved").filter(public)


def can_access(trader, question):
    if question.publication_state != "approved":
        return False
    return not question.group_id or bool(trader and GroupMembership.objects.filter(group_id=question.group_id, trader=trader, status="active").exists())
