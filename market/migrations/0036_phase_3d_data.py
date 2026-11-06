"""Data of phase 3d, apart from the schema of 0034: PostgreSQL refuses deferred CREATE INDEX on a table
with rows updated in the same transaction («pending trigger events»)."""
from django.db import migrations


def forwards(apps, schema_editor):
    Question = apps.get_model("market", "Question")
    Group = apps.get_model("market", "Group")
    ChatPost = apps.get_model("market", "ChatPost")
    # Every outcome stated before phase 3d came from the creator.
    for question in Question.objects.exclude(resolution_state="").exclude(creator=None).only("pk", "creator_id"):
        Question.objects.filter(pk=question.pk).update(proposed_by_id=question.creator_id)
    # Chat markets already announced keep getting their results.
    for post in ChatPost.objects.filter(kind="new_market").exclude(question=None).only("question_id", "created_at"):
        Question.objects.filter(pk=post.question_id, announced_at=None).update(announced_at=post.created_at)
    # Per-type switches become a mode: everything off → quiet, otherwise the default «important».
    for group in Group.objects.filter(kind="chat"):
        old = group.post_settings or {}
        if "mode" in old:
            continue
        kinds = ("new_market", "results", "weekly_top")
        mode = "quiet" if all(old.get(k, True) is False for k in kinds) else "important"
        group.post_settings = {"mode": mode}
        group.save(update_fields=["post_settings"])


class Migration(migrations.Migration):

    dependencies = [
        ("market", "0035_market_draft_shown_cost"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
