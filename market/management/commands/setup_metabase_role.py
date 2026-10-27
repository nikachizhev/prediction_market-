"""Create or update the read-only PostgreSQL role Metabase connects with (spec E7)."""
import os
import re

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

ROLE = "metabase_ro"
SCHEMA = "analytics"


def quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class Command(BaseCommand):
    help = ("Create or update the role metabase_ro (password from METABASE_DB_PASSWORD): it can read the "
            "views of schema analytics and nothing else. Re-run after migrations that add views.")

    def add_arguments(self, parser):
        parser.add_argument("--role", default=ROLE, help=f"role name, {ROLE} by default")

    def handle(self, *args, role: str, **options):
        if connection.vendor != "postgresql":
            raise CommandError("Нужна база PostgreSQL.")
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
            raise CommandError("Имя роли: латинские буквы в нижнем регистре, цифры и _.")
        password = os.environ.get("METABASE_DB_PASSWORD", "")
        if not password or "\x00" in password:
            raise CommandError("Задайте METABASE_DB_PASSWORD в .env.")
        database = connection.settings_dict["NAME"]
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", [SCHEMA])
            if cursor.fetchone() is None:
                raise CommandError("Схемы analytics нет: сначала выполните manage.py migrate.")
            cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", [role])
            verb = "ALTER" if cursor.fetchone() else "CREATE"
            cursor.execute(f"{verb} ROLE {role} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT "
                           f"PASSWORD {quote_literal(password)}")
            # Read-only sessions that cannot hang the database with a runaway dashboard query.
            cursor.execute(f"ALTER ROLE {role} SET default_transaction_read_only = on")
            cursor.execute(f"ALTER ROLE {role} SET statement_timeout = '60s'")
            cursor.execute(f"ALTER ROLE {role} SET search_path = {SCHEMA}")
            # Exactly: connect, see schema analytics, select its views. Nothing in public.
            quoted_db = '"' + database.replace('"', '""') + '"'
            cursor.execute(f"GRANT CONNECT ON DATABASE {quoted_db} TO {role}")
            cursor.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}")
            cursor.execute(f"REVOKE CREATE ON SCHEMA public FROM {role}")
            cursor.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {role}")
            cursor.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO {role}")
            cursor.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA {SCHEMA} GRANT SELECT ON TABLES TO {role}")
        self.stdout.write(f"Роль {role}: только чтение схемы {SCHEMA} в базе {database}.")
