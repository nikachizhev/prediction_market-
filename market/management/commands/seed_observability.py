"""Synthetic local data. Financial operations still use the production services."""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from market import services
from market.models import Event, Question, Trader


class Command(BaseCommand):
    help = 'Seed isolated observability demo; safe to repeat'

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.OBSERVABILITY_DEMO:
            raise CommandError('Only allowed in observability demo settings')
        if Trader.objects.filter(pseudonym='obs-demo-0').exists():
            self.stdout.write('Demo already seeded')
            return
        now = timezone.now()
        users = []
        for i in range(8):
            user = Trader.objects.create(name=f'Демо {i+1}', pseudonym=f'obs-demo-{i}', balance=0)
            services.grant_starter_pack(user)
            Event.objects.create(trader=user, name='app_open')
            users.append(user)
        for i, title in enumerate(['Демо: релиз до пятницы?', 'Демо: победа команды?',
                                   'Демо: дождь завтра?', 'Демо: экзамен сдан?',
                                   'Демо: турнир завершён?', 'Демо: новая версия?']):
            creator = users[i % 2]
            q = services.create_market(creator, title=title, criteria='Синтетические данные для мониторинга',
                                       category='tech', closes_at=now+timedelta(days=2),
                                       resolves_at=now+timedelta(days=3))
            for j in range(2, 8):
                services.buy(users[j], q, 'yes' if j % 2 else 'no', Decimal(20+j*2))
            services.sell(users[3], q, 'yes', Decimal(5))
            if i == 1:
                services.resolve(q, 'yes')
            elif i == 2:
                services.annul(q)
            elif i in (3, 4, 5):
                services.close_betting(q)
                if i in (4, 5):
                    services.propose_resolution(creator, q, 'yes', evidence_file_id='demo-only')
                if i == 5:
                    services.open_dispute(users[3], q, 'Демонстрационный спор')
        self.stdout.write('Seeded 8 synthetic users and 6 markets')
