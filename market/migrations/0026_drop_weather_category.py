"""The «Погода» category is gone (28.10.2026): its markets move to «Фан», onboarding interests forget it."""
from django.db import migrations


def drop_weather(apps, schema_editor):
    Question = apps.get_model("market", "Question")
    Trader = apps.get_model("market", "Trader")
    Question.objects.filter(category="weather").update(category="fun")
    for trader in Trader.objects.only("pk", "interests").iterator():
        if "weather" in (trader.interests or []):
            trader.interests = [slug for slug in trader.interests if slug != "weather"]
            trader.save(update_fields=["interests"])


class Migration(migrations.Migration):

    # Also merges the two leaves that came from main: recommendations (0024) and moderation (0025).
    dependencies = [
        ("market", "0024_recommendation_analytics"),
        ("market", "0025_moderation_cancellation"),
    ]

    operations = [
        migrations.RunPython(drop_weather, migrations.RunPython.noop),
    ]
