"""Chat markets stuck in the publication queue open now and get checked in the background (spec E3)."""
from django.db import migrations


def forwards(apps, schema_editor):
    Question = apps.get_model("market", "Question")
    stuck = Question.objects.filter(parent=None, group__kind="chat",
                                    publication_state__in=("pending", "checking", "review", "error"))
    for root in stuck.only("pk", "publication_state"):
        check = "review" if root.publication_state == "review" else "pending"
        Question.objects.filter(pk=root.pk).update(publication_state="approved", background_check=check,
                                                   publication_job=None, publication_retry_at=None)
        Question.objects.filter(parent=root.pk).update(publication_state="approved", publication_job=None,
                                                       publication_retry_at=None)


class Migration(migrations.Migration):

    dependencies = [
        ("market", "0038_phase_3e"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
