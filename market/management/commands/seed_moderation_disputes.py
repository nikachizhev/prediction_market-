"""Create independent demo disputes without the Mini App or public bot."""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from market import services
from market.models import Question, Trader


class Command(BaseCommand):
    help = 'Create three demo markets with open disputes for testing the moderation bot locally.'

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('Тестовые рынки создаются только при DEBUG=True.')
        batch = uuid4().hex[:8]
        now = timezone.now()
        created = []
        files = []
        try:
            with transaction.atomic():
                for scenario in ('Подтвердить ДА', 'Изменить на НЕТ', 'Аннулировать'):
                    people = [Trader.objects.create(name=f'ТЕСТ {role} {batch}', pseudonym=f'ТЕСТ {role} {batch}')
                              for role in ('автор '+scenario, 'участник '+scenario)]
                    creator, buyer = people
                    q = services.create_market(creator, title=f'ТЕСТ спора {batch}: {scenario}',
                        criteria='Демонстрационный рынок без реального события. Исход выбирается для проверки бота.',
                        category='fun', closes_at=now+timedelta(hours=max(2, settings.MARKET_MIN_HOURS+1)),
                        resolves_at=now+timedelta(hours=max(3, settings.MARKET_MIN_HOURS+2)))
                    services.buy(buyer, q, 'yes', Decimal('10'))
                    # Only these newly created demo markets have their deadline moved into the past.
                    Question.objects.filter(pk=q.pk).update(deadline=now-timedelta(minutes=1), resolves_at=now)
                    q.refresh_from_db()
                    q = services.propose_resolution(creator, q, 'yes',
                        evidence=ContentFile(b'DEMO ONLY: choose an outcome to test the moderation bot.', name=f'demo-{batch}-{q.pk}.txt'),
                        note='Тестовое доказательство: текстовый файл. Реального события нет.')
                    files.append(q.evidence)
                    dispute = services.open_dispute(buyer, q, reason=f'ТЕСТ: проверьте действие «{scenario}».')
                    created.append((q.pk, dispute.pk, creator.pk, buyer.pk))
        except Exception:
            for file in files:
                file.delete(save=False)
            raise
        self.stdout.write(f'Созданы 3 тестовых рынка. Набор: {batch}. Существующие записи не изменены.')
        for qid, did, creator_id, buyer_id in created:
            self.stdout.write(f'Рынок #{qid}, спор #{did}, автор #{creator_id}, участник #{buyer_id}')
        self.stdout.write('В боте: /disputes. До решения у участника списаны 10 монет за покупку и залог за спор.')
