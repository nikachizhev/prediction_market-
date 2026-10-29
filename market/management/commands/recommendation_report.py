"""Aggregate diagnostics only: no account identifiers or message content."""
import json
from datetime import timedelta
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Count
from django.utils import timezone
from market.models import Event


class Command(BaseCommand):
    help = "Recommendation activity report (JSON; counts are not causal conversions)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30)

    def handle(self, *args, **options):
        days = options["days"]
        if not 1 <= days <= 365:
            raise CommandError("--days: 1..365")
        since = timezone.now() - timedelta(days=days)
        rows = Event.objects.filter(created_at__gte=since, name__in=[
            "feed_impression", "market_open", "market_dismiss", "market_restore", "interests_update",
        ]).values("name").annotate(events=Count("id"), users=Count("trader_id", distinct=True)).order_by("name")
        self.stdout.write(json.dumps({"since": since.isoformat(), "days": days,
                                     "metrics": list(rows)}, ensure_ascii=False))
