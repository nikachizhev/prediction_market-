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

    dependencies = [
        ("market", "0022_analytics_views"),
    ]

    operations = [
        migrations.RunPython(drop_weather, migrations.RunPython.noop),
    ]
