"""Schema `analytics` for the team dashboard in Metabase (spec E7).

Read-only views without personal data: no names, pseudonyms, usernames, Telegram ids, photos,
emails, free-text reasons. The role `metabase_ro` (command `setup_metabase_role`) can read this
schema only. PostgreSQL only; on other databases (SQLite in tests) the migration does nothing.

Days are Moscow calendar days. A view depends on the columns it reads: a later migration that
drops or retypes such a column must drop the view first (and recreate it).
"""
from django.db import migrations

MSK = "'Europe/Moscow'"

VIEWS = [
    # --- raw tables, non-personal columns only ---------------------------------------------
    ("traders", """
        SELECT id, created_at, referred_by_id IS NOT NULL AS referred, onboarded_at, balance
        FROM market_trader"""),
    ("markets", """
        SELECT id, category, status, outcome, created_at, deadline, resolves_at, resolved_at,
               resolution_state, creator_at_fault, subsidy, fee_pool
        FROM market_question"""),
    ("trades", """
        SELECT id, trader_id, question_id, side, cost, fee, prob_before, prob_after, created_at
        FROM market_trade"""),
    ("ledger", """
        SELECT trader_id, kind, amount, question_id, created_at
        FROM market_ledgerentry"""),
    ("events", """
        SELECT trader_id, name, props, created_at
        FROM market_event"""),
    ("disputes", """
        SELECT question_id, upheld, created_at
        FROM market_dispute"""),
    # --- helpers for the dashboards ----------------------------------------------------------
    # A trader is active on a day when they opened the app (any event) or traded.
    ("activity", f"""
        SELECT DISTINCT trader_id, (created_at AT TIME ZONE {MSK})::date AS day
        FROM market_event WHERE trader_id IS NOT NULL
        UNION
        SELECT DISTINCT trader_id, (created_at AT TIME ZONE {MSK})::date AS day
        FROM market_trade"""),
    # «Аудитория»: DAU, WAU and MAU (rolling 7 and 30 days ending that day).
    ("active_users", f"""
        WITH days AS (
            SELECT generate_series(min(day), (now() AT TIME ZONE {MSK})::date, interval '1 day')::date AS day
            FROM analytics.activity)
        SELECT d.day,
               (SELECT count(DISTINCT a.trader_id) FROM analytics.activity a WHERE a.day = d.day) AS dau,
               (SELECT count(DISTINCT a.trader_id) FROM analytics.activity a
                 WHERE a.day BETWEEN d.day - 6 AND d.day) AS wau,
               (SELECT count(DISTINCT a.trader_id) FROM analytics.activity a
                 WHERE a.day BETWEEN d.day - 29 AND d.day) AS mau
        FROM days d"""),
    # Signup day: account creation, or the first activity for old accounts without it.
    ("signups", f"""
        SELECT t.id AS trader_id, t.referred_by_id IS NOT NULL AS referred,
               COALESCE((t.created_at AT TIME ZONE {MSK})::date,
                        (SELECT min(a.day) FROM analytics.activity a WHERE a.trader_id = t.id)) AS signup_day
        FROM market_trader t"""),
    # «Удержание по когортам»: share of a signup-day cohort active exactly N days later.
    # NULL while day N is still in the future.
    ("retention", f"""
        WITH today AS (SELECT (now() AT TIME ZONE {MSK})::date AS day),
        flags AS (
            SELECT s.signup_day, s.referred,
                   EXISTS (SELECT 1 FROM analytics.activity a WHERE a.trader_id = s.trader_id
                           AND a.day = s.signup_day + 1) AS d1,
                   EXISTS (SELECT 1 FROM analytics.activity a WHERE a.trader_id = s.trader_id
                           AND a.day = s.signup_day + 7) AS d7,
                   EXISTS (SELECT 1 FROM analytics.activity a WHERE a.trader_id = s.trader_id
                           AND a.day = s.signup_day + 30) AS d30
            FROM analytics.signups s WHERE s.signup_day IS NOT NULL)
        SELECT f.signup_day AS cohort_day, count(*) AS cohort_size,
               CASE WHEN f.signup_day + 1 <= t.day THEN count(*) FILTER (WHERE f.d1) END AS returned_d1,
               CASE WHEN f.signup_day + 7 <= t.day THEN count(*) FILTER (WHERE f.d7) END AS returned_d7,
               CASE WHEN f.signup_day + 30 <= t.day THEN count(*) FILTER (WHERE f.d30) END AS returned_d30,
               CASE WHEN f.signup_day + 1 <= t.day
                    THEN round(count(*) FILTER (WHERE f.d1)::numeric / count(*), 4) END AS retention_d1,
               CASE WHEN f.signup_day + 7 <= t.day
                    THEN round(count(*) FILTER (WHERE f.d7)::numeric / count(*), 4) END AS retention_d7,
               CASE WHEN f.signup_day + 30 <= t.day
                    THEN round(count(*) FILTER (WHERE f.d30)::numeric / count(*), 4) END AS retention_d30
        FROM flags f CROSS JOIN today t
        GROUP BY f.signup_day, t.day"""),
    # «Воронка» per trader: each step requires the previous one.
    ("funnel_users", """
        WITH steps AS (
            SELECT s.trader_id, s.signup_day, s.referred,
                   EXISTS (SELECT 1 FROM market_event e WHERE e.trader_id = s.trader_id
                           AND e.name = 'app_open') AS opened,
                   EXISTS (SELECT 1 FROM market_trade tr WHERE tr.trader_id = s.trader_id
                           AND tr.cost > 0) AS traded,
                   EXISTS (SELECT 1 FROM analytics.activity a WHERE a.trader_id = s.trader_id
                           AND a.day = s.signup_day + 1) AS returned,
                   EXISTS (SELECT 1 FROM market_question q WHERE q.creator_id = s.trader_id) AS created
            FROM analytics.signups s)
        SELECT trader_id, signup_day, referred,
               opened,
               opened AND traded AS first_trade,
               opened AND traded AND returned AS returned_next_day,
               opened AND traded AND returned AND created AS created_market
        FROM steps"""),
    # «Воронка» totals, one row per step and segment (referred = came by an invite link).
    ("funnel", """
        SELECT referred, step_no, step, count(*) FILTER (WHERE reached) AS users
        FROM analytics.funnel_users f
        CROSS JOIN LATERAL (VALUES (1, 'opened_app', f.opened),
                                   (2, 'first_trade', f.first_trade),
                                   (3, 'returned_next_day', f.returned_next_day),
                                   (4, 'created_market', f.created_market)) AS s(step_no, step, reached)
        GROUP BY referred, step_no, step"""),
    # «Экономика» per day. Issued: bonus coins (starter, daily, insurance, friend rewards).
    # Burned: what left circulation in markets settled that day (the burned half of fees, lost
    # subsidies and dispute stakes, rounding) = minus the sum of all ledger lines of those markets.
    # Supply: issued minus burned so far (coins on balances plus coins held by open markets).
    # Balances: each trader's latest balance at the end of the day.
    ("economy_daily", f"""
        WITH days AS (
            SELECT generate_series(min((created_at AT TIME ZONE {MSK})::date),
                                   (now() AT TIME ZONE {MSK})::date, interval '1 day')::date AS day
            FROM market_ledgerentry),
        issued AS (
            SELECT (created_at AT TIME ZONE {MSK})::date AS day,
                   sum(amount) FILTER (WHERE kind = 'starter') AS starter,
                   sum(amount) FILTER (WHERE kind = 'daily') AS daily,
                   sum(amount) FILTER (WHERE kind = 'insurance') AS insurance,
                   sum(amount) FILTER (WHERE kind = 'referral') AS referral,
                   sum(amount) AS total
            FROM market_ledgerentry WHERE kind IN ('starter', 'daily', 'insurance', 'referral')
            GROUP BY 1),
        burned AS (
            SELECT (q.resolved_at AT TIME ZONE {MSK})::date AS day, -sum(l.amount) AS total
            FROM market_ledgerentry l JOIN market_question q ON q.id = l.question_id
            WHERE q.resolved_at IS NOT NULL AND q.status IN ('resolved', 'annulled')
              AND l.kind NOT IN ('starter', 'daily', 'insurance', 'referral')
            GROUP BY 1),
        balances AS (
            SELECT d.day,
                   (SELECT l.balance_after FROM market_ledgerentry l
                     WHERE l.trader_id = t.id AND l.created_at < (d.day + 1)::timestamp AT TIME ZONE {MSK}
                     ORDER BY l.created_at DESC, l.id DESC LIMIT 1) AS balance
            FROM days d CROSS JOIN market_trader t),
        balance_stats AS (
            SELECT day, sum(balance) AS on_balances, count(balance) AS users,
                   count(*) FILTER (WHERE balance < 100) AS low_balance_users
            FROM balances GROUP BY day)
        SELECT d.day,
               COALESCE(i.starter, 0) AS issued_starter, COALESCE(i.daily, 0) AS issued_daily,
               COALESCE(i.insurance, 0) AS issued_insurance, COALESCE(i.referral, 0) AS issued_referral,
               COALESCE(i.total, 0) AS issued, COALESCE(b.total, 0) AS burned,
               sum(COALESCE(i.total, 0) - COALESCE(b.total, 0)) OVER (ORDER BY d.day) AS supply,
               COALESCE(s.on_balances, 0) AS on_balances, COALESCE(s.users, 0) AS users,
               COALESCE(s.low_balance_users, 0) AS low_balance_users,
               CASE WHEN s.users > 0 THEN round(s.low_balance_users::numeric / s.users, 4) END AS low_balance_share
        FROM days d
        LEFT JOIN issued i ON i.day = d.day
        LEFT JOIN burned b ON b.day = d.day
        LEFT JOIN balance_stats s ON s.day = d.day"""),
    # «Рынки»: hours from the announced result date to the final settlement, per market.
    ("market_resolution_delays", """
        SELECT id AS question_id, category, status, resolves_at, resolved_at,
               round((extract(epoch FROM resolved_at - resolves_at) / 3600)::numeric, 1) AS hours_to_resolution
        FROM market_question
        WHERE resolved_at IS NOT NULL AND resolves_at IS NOT NULL"""),
    # «Рынки» per day: created, resolved, annulled, disputed (markets with a new dispute that day).
    ("markets_daily", f"""
        WITH days AS (
            SELECT generate_series(min((created_at AT TIME ZONE {MSK})::date),
                                   (now() AT TIME ZONE {MSK})::date, interval '1 day')::date AS day
            FROM market_question),
        created AS (SELECT (created_at AT TIME ZONE {MSK})::date AS day, count(*) AS n
                    FROM market_question GROUP BY 1),
        settled AS (SELECT (resolved_at AT TIME ZONE {MSK})::date AS day,
                           count(*) FILTER (WHERE status = 'resolved') AS resolved,
                           count(*) FILTER (WHERE status = 'annulled') AS annulled
                    FROM market_question WHERE resolved_at IS NOT NULL GROUP BY 1),
        disputed AS (SELECT (created_at AT TIME ZONE {MSK})::date AS day, count(DISTINCT question_id) AS n
                     FROM market_dispute GROUP BY 1),
        delays AS (SELECT (resolved_at AT TIME ZONE {MSK})::date AS day,
                          round(avg(hours_to_resolution), 1) AS avg_hours
                   FROM analytics.market_resolution_delays WHERE status = 'resolved' GROUP BY 1)
        SELECT d.day, COALESCE(c.n, 0) AS created, COALESCE(s.resolved, 0) AS resolved,
               COALESCE(s.annulled, 0) AS annulled, COALESCE(x.n, 0) AS disputed,
               l.avg_hours AS avg_hours_to_resolution
        FROM days d
        LEFT JOIN created c ON c.day = d.day
        LEFT JOIN settled s ON s.day = d.day
        LEFT JOIN disputed x ON x.day = d.day
        LEFT JOIN delays l ON l.day = d.day"""),
]


def create_views(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("CREATE SCHEMA IF NOT EXISTS analytics")
    for name, sql in VIEWS:
        schema_editor.execute(f"CREATE OR REPLACE VIEW analytics.{name} AS {sql}")


def drop_views(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute("DROP SCHEMA IF EXISTS analytics CASCADE")


class Migration(migrations.Migration):

    dependencies = [
        ("market", "0021_phase2_notifications_referrals_charts"),
    ]

    operations = [
        migrations.RunPython(create_views, drop_views, elidable=False),
    ]
