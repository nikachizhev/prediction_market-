"""Schema `analytics` for Metabase (spec E7): PostgreSQL-only views without personal data, read-only role."""
import importlib
import uuid
from decimal import Decimal

import pytest
from django.core.management import CommandError, call_command
from django.db import DatabaseError, connection, transaction

from market import services
from market.models import Dispute, Event, LedgerEntry, Trader
from market.tests.test_api import Api, anya, boris, bot_token, market_by, trader  # noqa: F401

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("bot_token")]

migration = importlib.import_module("market.migrations.0022_analytics_views")

BASE_COLUMNS = {
    "traders": ["id", "created_at", "referred", "onboarded_at", "balance"],
    "markets": ["id", "category", "status", "outcome", "created_at", "deadline", "resolves_at", "resolved_at",
                "resolution_state", "creator_at_fault", "subsidy", "fee_pool"],
    "trades": ["id", "trader_id", "question_id", "side", "cost", "fee", "prob_before", "prob_after", "created_at"],
    "ledger": ["trader_id", "kind", "amount", "question_id", "created_at"],
    "events": ["trader_id", "name", "props", "created_at"],
    "disputes": ["question_id", "upheld", "created_at"],
}
HELPERS = {"activity", "active_users", "signups", "retention", "funnel_users", "funnel", "economy_daily",
           "market_resolution_delays", "markets_daily"}
PERSONAL = {"name", "pseudonym", "show_real_name", "telegram_chat_id", "telegram_username", "photo_url",
            "referral_code", "referred_by_id", "user_id", "github_login", "birth_date", "city", "workplace",
            "education", "bio", "email", "first_name", "username", "title", "reason", "text", "evidence",
            "evidence_note", "chat_id"}


def postgres_only():
    if connection.vendor != "postgresql":
        pytest.skip("Requires PostgreSQL")


class RecordingEditor:
    def __init__(self, vendor):
        self.connection = type("Conn", (), {"vendor": vendor})()
        self.sql = []

    def execute(self, sql, params=None):
        self.sql.append(sql)


def test_migration_is_a_noop_outside_postgres():
    editor = RecordingEditor("sqlite")
    migration.create_views(None, editor)
    migration.drop_views(None, editor)
    assert editor.sql == []
    postgres = RecordingEditor("postgresql")
    migration.create_views(None, postgres)
    assert postgres.sql[0] == "CREATE SCHEMA IF NOT EXISTS analytics" and len(postgres.sql) == 1 + len(migration.VIEWS)


def test_setup_role_needs_postgres(monkeypatch):
    if connection.vendor == "postgresql":
        pytest.skip("SQLite-only check")
    monkeypatch.setenv("METABASE_DB_PASSWORD", "secret")
    with pytest.raises(CommandError):
        call_command("setup_metabase_role")


def columns() -> dict[str, list[str]]:
    with connection.cursor() as cursor:
        cursor.execute("SELECT table_name, column_name FROM information_schema.columns "
                       "WHERE table_schema = 'analytics' ORDER BY table_name, ordinal_position")
        result: dict[str, list[str]] = {}
        for table, column in cursor.fetchall():
            result.setdefault(table, []).append(column)
    return result


def test_analytics_views_expose_no_personal_columns():
    postgres_only()
    with connection.cursor() as cursor:
        cursor.execute("SELECT table_name FROM information_schema.views WHERE table_schema = 'analytics'")
        views = {row[0] for row in cursor.fetchall()}
        cursor.execute("SELECT count(*) FROM information_schema.tables "
                       "WHERE table_schema = 'analytics' AND table_type <> 'VIEW'")
        assert cursor.fetchone()[0] == 0
    assert views == set(BASE_COLUMNS) | HELPERS
    found = columns()
    for view, expected in BASE_COLUMNS.items():
        assert found[view] == expected
    for view, cols in found.items():
        allowed = {"name"} if view == "events" else set()  # the event name, e.g. "app_open"
        assert not (PERSONAL - allowed) & set(cols), view


def test_analytics_views_return_rows(anya, boris):
    postgres_only()
    m = market_by(1002)
    anya.post(f"markets/{m.pk}/buy", {"side": "yes", "amount": 50, "request_key": str(uuid.uuid4())})
    Dispute.objects.create(question=m, trader=trader(1001), stake=Decimal(20), reason="Неправда")
    services.resolve(m, "yes")
    with connection.cursor() as cursor:
        for name, _ in migration.VIEWS:
            cursor.execute(f"SELECT * FROM analytics.{name}")
            assert cursor.fetchall(), name
        cursor.execute("SELECT dau, wau, mau FROM analytics.active_users ORDER BY day DESC LIMIT 1")
        assert cursor.fetchone() == (2, 2, 2)
        cursor.execute("SELECT created, resolved, disputed FROM analytics.markets_daily ORDER BY day DESC LIMIT 1")
        assert cursor.fetchone() == (1, 1, 1)
        cursor.execute("SELECT step, users FROM analytics.funnel WHERE NOT referred ORDER BY step_no")
        assert cursor.fetchall() == [("opened_app", 2), ("first_trade", 1), ("returned_next_day", 0),
                                     ("created_market", 0)]
        cursor.execute("SELECT name FROM analytics.events ORDER BY created_at")
        assert "app_open" in {row[0] for row in cursor.fetchall()}


def test_economy_supply_matches_balances_once_markets_settle(anya, boris):
    postgres_only()
    m = market_by(1002)
    anya.post(f"markets/{m.pk}/buy", {"side": "yes", "amount": 100, "request_key": str(uuid.uuid4())})
    services.resolve(m, "no")
    with connection.cursor() as cursor:
        cursor.execute("SELECT issued_starter, issued, burned, supply, on_balances, users "
                       "FROM analytics.economy_daily ORDER BY day DESC LIMIT 1")
        starter, issued, burned, supply, on_balances, users = cursor.fetchone()
    balances = sum(t.balance for t in Trader.objects.all())
    assert starter == issued == 2000
    fees = LedgerEntry.objects.filter(question=m).exclude(kind__in=LedgerEntry.BONUS_KINDS)
    assert burned == -sum(e.amount for e in fees) > 0  # half of the fee burns
    assert supply == on_balances == balances and users == 2


def test_metabase_role_reads_analytics_only(monkeypatch, anya):
    postgres_only()
    role = f"metabase_ro_test_{uuid.uuid4().hex[:8]}"
    monkeypatch.setenv("METABASE_DB_PASSWORD", "it's a test")
    call_command("setup_metabase_role", role=role)
    call_command("setup_metabase_role", role=role)  # re-running updates the role
    Event.objects.create(trader=trader(1001), name="share")
    with connection.cursor() as cursor:
        cursor.execute(f"SET ROLE {role}")
        try:
            cursor.execute("SELECT count(*) FROM analytics.traders")
            assert cursor.fetchone()[0] == 1
            cursor.execute("SELECT count(*) FROM analytics.economy_daily")
            for table in ("public.market_trader", "public.market_ledgerentry", "public.auth_user"):
                with pytest.raises(DatabaseError, match="permission denied"), transaction.atomic():
                    cursor.execute(f"SELECT 1 FROM {table} LIMIT 1")
            with pytest.raises(DatabaseError), transaction.atomic():
                cursor.execute("CREATE TABLE analytics.x (id int)")
        finally:
            cursor.execute("RESET ROLE")
    with connection.cursor() as cursor:
        cursor.execute("SELECT has_table_privilege(%s, 'analytics.trades', 'SELECT'), "
                       "has_table_privilege(%s, 'public.market_trade', 'SELECT'), "
                       "has_schema_privilege(%s, 'analytics', 'CREATE')", [role, role, role])
        assert cursor.fetchone() == (True, False, False)


def test_setup_role_requires_password(monkeypatch):
    postgres_only()
    monkeypatch.delenv("METABASE_DB_PASSWORD", raising=False)
    with pytest.raises(CommandError, match="METABASE_DB_PASSWORD"):
        call_command("setup_metabase_role", role="metabase_ro_nopass")
