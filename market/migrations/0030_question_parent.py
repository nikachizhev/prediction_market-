import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("market", "0029_moderationcontact"),
    ]

    operations = [
        migrations.AddField(
            model_name="question",
            name="parent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="submarkets",
                to="market.question",
                verbose_name="родительский рынок",
            ),
        ),
        migrations.AddField(
            model_name="question",
            name="option_label",
            field=models.CharField(blank=True, default="", max_length=120, verbose_name="название варианта"),
        ),
    ]

