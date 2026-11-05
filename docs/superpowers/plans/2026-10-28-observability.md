# Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox syntax.

**Goal:** Running local business/technical Grafana dashboard backed by Prometheus.
**Architecture:** DB snapshot collector, multiprocess HTTP instrumentation, independent worker endpoint; isolated Compose demo stack.
**Tech Stack:** Django, prometheus-client, PostgreSQL, Prometheus, Grafana.
**Spec:** docs/superpowers/specs/2026-10-28-observability-design.md

## Global Constraints
- Local only, branch miniapp2-observability; no push or production changes.
- No PII or unbounded labels. Business snapshots are gauges.
- Financial writes stay in services.py. Demo data isolated, Telegram disabled.
- Endpoint /metrics gated by explicit enable setting and bearer token.

## Review Focus
- Database failure must not emit zero business values (Task 1).
- Replayed purchase must not inflate business totals (Task 1).
- Unknown methods/routes cannot create unbounded series (Task 2).
- Dead/restarted Gunicorn workers must retain correct counter totals (Task 3).
- Demo worker must never send real Telegram traffic (Task 2/3).

### Task 1: Business collector
Files: market/observability.py, market/tests/test_observability.py, requirements.txt.
Interface: BusinessCollector.collect() yields GaugeMetricFamily; metrics_response(request) exports it.
- [x] Write tests: empty users=0, duplicate starter does not duplicate cohort; buy 100/sell -40 gives volume 140; windows exclude old/future rows; db failure reports pm_database_up=0 without business samples; replayed purchase counted once.
- [x] Run .venv/bin/python -m pytest --ds=config.test_settings market/tests/test_observability.py -q; verify failure.
- [x] Implement grouped DB aggregates, fixed labels and failure observability.
- [x] Run tests, verify pass.

### Task 2: HTTP and worker instrumentation
Files: market/observability.py, market/worker_metrics.py, market/resolution.py, market/management/commands/run_worker.py, config/settings.py, config/urls.py, tests.
Interface: MetricsMiddleware; /metrics; worker registry endpoint on port 9101; observe_tick(callable) wraps tick; record_telegram_failure(action).
- [x] Test real Django 200/401/404/500 accounting, bounded methods/routes, endpoint disabled/unauthorized/scrape exclusion; worker success/failure and handled Telegram failures.
- [x] Verify failing tests, implement, verify green.
- [x] Run complete SQLite suite; expected no regressions.

### Task 3: Local stack and Grafana
Files: compose.observability.yaml, deploy/observability/**, config/observability_settings.py, market/management/commands/seed_observability.py, docs/observability.md.
Interfaces: metrics from Tasks 1/2; Grafana datasource UID prometheus; dashboard UID prediction-market.
- [x] Provision isolated app/db/worker/prometheus/grafana; two Gunicorn workers with multiprocess hooks; dependency versions pinned; no real Telegram token; deterministic demo seed through services.
- [x] Provision Russian dashboard, all business and technical sections, warning panels.
- [x] Start services; validate Prometheus targets, all panel expressions, Grafana health and rendered dashboard.
- [x] Verify two-worker counting and restart, database aggregates, real request codes, worker fake transport.
- [x] Run full SQLite and relevant PostgreSQL tests. Fresh final review and fix important findings.
- [x] Document URLs, credentials, start/stop, definitions and limitations. Leave services running.

## Execution record
User approved implementation without further agreements. Execute inline in existing dedicated branch (no additional worktree needed). No commit/push unless needed for final handoff; preserve inspectable working diff. Unresolved questions: none.
