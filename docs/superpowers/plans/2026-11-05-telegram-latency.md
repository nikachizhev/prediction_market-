# Telegram latency implementation plan

> **For agentic workers:** Use executing-plans for the queue and isolated orchestration for transport.

**Goal:** Remove diagnosed IPv6 stalls and keep slow charts/broadcasts out of the poller.
**Architecture:** Telegram-specific IPv4 HTTPS adapter with separate connection/read timeouts. Durable DB jobs, committed with market outcomes, processed by a separate low-latency worker. No production deployment.
**Tech Stack:** Existing Django, PostgreSQL, requests/urllib3, systemd; no new service dependency.
**Spec:** User-approved two-stage fix; docs/spec.md E4, E6.1–E6.3.

## Constraints and decisions

- Branch from origin/main 928224e; PR to main, never merge or deploy automatically.
- Preserve TLS verification, long polling, Russian message content and access checks.
- No global network monkeypatch. No HTTP-level POST retry (ambiguous delivery).
- Existing rate-limited notification queues remain unchanged; explicit responses and outcome evidence use a separate queue without quiet hours.
- Jobs survive restarts; lease ownership prevents stale acknowledgements, retries are bounded. Telegram has no idempotency token: crash after delivery can duplicate a message.

## Review focus

- IPv4 limited to Telegram; upload/download and proxy behavior do not bypass it.
- Failed participant delivery does not prevent other jobs or polling.
- Transaction rollback creates no orphan notification; duplicate confirmation does not duplicate jobs.
- Worker rechecks private-market access and recipient binding.
- Worker crash recovery, expired leases, retry exhaustion, and real PostgreSQL claim concurrency.

## Task 1: Transport

Files: market/telegram_transport.py, market/telegram.py, transport tests.
- [x] RED: socket-boundary IPv4 isolation and split timeouts, preserved long polling and file operations.
- [x] GREEN: scoped adapter, no global changes, reusable sessions, sanitized errors.
- [x] Full backend suite; commit transport independently.

## Task 2: Durable jobs and callback integration

Files: market/models.py, one migration, market/bot_jobs.py, market/bot_markets.py, market/bot_resolution_dialog.py, run_bot_jobs command, deploy scripts/unit, README, tests.
Interface: enqueue_message(key, trader, question, method, payload); enqueue_chart(callback_id, trader, question); process_one(now=None) -> bool.
- [x] RED: chart callback enqueues without render; confirmation queues recipients atomically and acknowledges before handler work.
- [x] GREEN: job model and leased worker (3 attempts, 15-minute lease, retry after 5/30 seconds); explicit jobs retain original payload.
- [x] RED/GREEN: worker success/retry/exhaustion/recovery/access revocation; transaction rollback and PostgreSQL concurrency.
- [x] Update existing end-to-end tests to drain jobs explicitly; preserve payload checks.
- [x] Add run_bot_jobs (1-second idle interval), systemd service and deploy lifecycle; document delivery semantics.
- [x] Full backend, Django checks, migration consistency, PostgreSQL and existing frontend gates.

## Task 3: Review and PR

- [x] Integrate transport; independent review, resolve findings.
- [x] Update docs/tasks.md with exact validation and deployment requirements.
- Delivery: commit and push feature branch; open PR to main (tracked in GitHub).

Unresolved questions: none.


## Execution record

- Baseline origin/main: 734 passed, 12 skipped (SQLite).
- Transport: separate worktree/commit, 15 regression tests; integrated as 3a38509.
- Queue RED: chart rendered in poller; callback acknowledged after slow work; no durable jobs.
- Queue GREEN: 13 initial tests passed; outcome payload tests drain jobs explicitly.
- PostgreSQL found test harness issue: command connection cleanup was called inside pytest's wrapping transaction. Delivery tests now drain process_one; command/concurrency tests use real transactions. No production-only workaround.
- Focused PostgreSQL: 55 passed, 1 skipped, including simultaneous claims and poller responsiveness while delivery is blocked.
- Final SQLite: 764 passed, 14 skipped; frontend: 220 passed, typecheck/build passed.
- Independent review: no Critical/Important findings. Clarified connect timeout is per IP, excluding DNS.
- Ruling: worker uses a separate durable outbox without quiet hours, rather than extending the rate-limited BotMessage queue — explicit chart requests and outcome evidence must be delivered promptly.
- Ruling: callback error notes become chat messages after immediate acknowledgement; redundant success toasts removed, existing full responses retained.

- Final PostgreSQL: 776 passed, 2 skipped, including queue concurrency. Django check, migration drift and shell syntax checks passed.
