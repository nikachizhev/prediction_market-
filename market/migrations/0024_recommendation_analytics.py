from django.db import migrations, models


SQL = """
CREATE OR REPLACE VIEW analytics.recommendation_daily AS
SELECT (created_at AT TIME ZONE 'Europe/Moscow')::date AS day,
       name AS event, count(*) AS events, count(DISTINCT trader_id) AS users
FROM market_event
WHERE name IN ('feed_impression', 'market_open', 'market_dismiss',
               'market_restore', 'interests_update')
GROUP BY 1, 2
"""


def forward(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(SQL)


def backward(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("DROP VIEW IF EXISTS analytics.recommendation_daily")


class Migration(migrations.Migration):
    dependencies = [("market", "0023_feeddismissal")]
    operations = [
        migrations.AddIndex(model_name="event", index=models.Index(
            fields=["trader", "name", "created_at"], name="event_trader_name_time")),
        migrations.RunPython(forward, backward),
    ]
