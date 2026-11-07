"""Old chat markets that already have CHAT_ANNOUNCE_PARTICIPANTS bettors are not news: the crowd rule of
phase 3d (E8.4) must not announce them just because the rule appeared. Markets made after the 3d rollout
keep the rule."""
from datetime import datetime, timezone

from django.conf import settings
from django.db import migrations

ROLLOUT = datetime(2026, 11, 6, 6, 27, tzinfo=timezone.utc)


def forwards(apps, schema_editor):
    Question = apps.get_model("market", "Question")
    Trade = apps.get_model("market", "Trade")
    GroupMembership = apps.get_model("market", "GroupMembership")
    old = (Question.objects.filter(group__kind="chat", parent=None, announced_at=None, announce=False,
                                   chat_draft=None, created_at__lt=ROLLOUT)
           .exclude(chat_posts__kind="new_market").only("pk", "group_id"))
    for market in old:
        members = GroupMembership.objects.filter(group_id=market.group_id, status="active").values("trader_id")
        bettors = (Trade.objects.filter(question__in=Question.objects.filter(parent=market.pk) | Question.objects.filter(pk=market.pk),
                                        trader_id__in=members)
                   .values("trader_id").distinct().count())
        if bettors >= settings.CHAT_ANNOUNCE_PARTICIPANTS:
            Question.objects.filter(pk=market.pk, announced_at=None).update(announced_at=ROLLOUT)


class Migration(migrations.Migration):

    dependencies = [
        ("market", "0036_phase_3d_data"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
