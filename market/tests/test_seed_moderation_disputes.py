from io import StringIO

import pytest
from django.core.management import call_command, CommandError
from market.models import Question, Dispute, Trader

pytestmark = pytest.mark.django_db


def test_seed_creates_ready_disputes_without_touching_existing(settings, tmp_path):
    settings.DEBUG = True
    settings.MEDIA_ROOT = tmp_path
    existing = Trader.objects.create(name='Existing', pseudonym='Existing')
    before = existing.balance
    call_command('seed_moderation_disputes', stdout=StringIO())
    assert Question.objects.filter(resolution_state='disputed').count() == 3
    assert Dispute.objects.filter(upheld__isnull=True).count() == 3
    assert all(q.evidence.storage.exists(q.evidence.name) for q in Question.objects.all())
    existing.refresh_from_db()
    assert existing.balance == before
    call_command('seed_moderation_disputes', stdout=StringIO())
    assert Dispute.objects.count() == 6


def test_seed_rejects_non_debug(settings):
    settings.DEBUG = False
    with pytest.raises(CommandError):
        call_command('seed_moderation_disputes')
    assert not Question.objects.exists()
