# Local Prometheus and Grafana

Status: approved. User requested implementation without further agreements.

## Outcome and scope

Provide a running local Prometheus and Grafana dashboard for business activity and service health. Work on `miniapp2-observability`, rebased onto `main` (`c75699a`), originally based on `miniapp2`; no push or production deployment. Superpowers skills are installed.

## Approaches

Use database aggregates for business data and process counters/histograms for HTTP traffic. This was recommended and the user asked to continue. Database records survive restarts, include bot operations, and avoid counting idempotent API retries twice.

Rejected alternative: count all business events in web processes. It loses prior history and misses other writers; Event trade entries can repeat on an idempotent retry. A standalone BI stack adds unnecessary services to the requested Prometheus/Grafana solution.

## Business definitions

All metrics use the `pm_` prefix. Business snapshots are gauges, including cumulative database totals: deleting demo records may decrease them. Do not apply counter `rate()` to snapshots. Export fixed 24-hour and 7-day rolling windows where needed.

- Users: registered Mini App accounts identified by starter ledger entries; new accounts in 24h/7d, active users with distinct `Event.trader_id` and `name=app_open` in 24h/7d, accounts that ever traded. Legacy accounts remain distinguishable by this cohort definition.
- Trades: counts and coin volume split into buy/sell, all time and rolling 24h/7d. Include committed Trade rows on user-created LMSR markets (`creator` and `resolves_at` present), matching the worker's market cohort. Volume is `abs(cost)`, explicitly labeled cash turnover including fees; show fees separately. Sales have negative cost. Users and trade aggregates use the same account cohort.
- Markets: count by public lifecycle state (open, closed, pending, disputed, resolved, annulled), total created and created in 24h/7d. Use the same user-created LMSR cohort.
- Economy: summed user balances; ledger bonus issuance by starter/daily/insurance/referral, payouts, refunds and creator fees, all time and rolling 24h. These are virtual coins, not revenue. No inferred burned-coins metric without reliable accounting evidence.
- Disputes: pending/upheld/rejected counts for the market cohort.

Avoid names, Telegram IDs, market IDs, raw URLs, request bodies, or arbitrary event props in labels. Empty datasets produce zeroes, not absent series.

## Technical metrics

- HTTP request counter by bounded method, resolved route name and status; unmatched routes use one fallback label. Exclude metrics scrapes from application traffic.
- Request duration histogram in seconds; Grafana shows RPS, status distribution, error ratio and p50/p95/p99. Capture framework-generated 4xx/5xx as well as normal responses.
- Prometheus target availability, database availability, business collector duration/failures and latest successful collection time. Failed database queries must not masquerade as empty business data.
- Worker last successful tick, tick duration, failed ticks, Telegram action failures. A completed tick with handled Telegram errors still records those errors separately. Export worker metrics from its own local/internal endpoint; worker down is distinct from zero errors. Local runs use a fake Telegram adapter explicitly enabled only for demo mode.

Use `prometheus-client`: standard exposition and multiprocess support justify the dependency. Gunicorn workers share a dedicated multiprocess directory, initialized before import and cleared only at fresh web-master startup. Worker lifecycle hooks remove dead gauges. Database collection occurs once per scrape, never once per Gunicorn worker; no duplicate custom registration. Test two-worker aggregation and worker restart.

## Runtime and dashboard

Add an isolated Compose configuration/project with app, PostgreSQL, worker, Prometheus and Grafana. Keep existing compose/deployment behavior intact. Dedicated volumes and localhost-bound ports avoid touching existing data. Internal metrics endpoints are reachable by Prometheus, not published externally. Keep services running after validation.

Provision datasource and dashboard JSON from version-controlled files. Two dashboards (user refinement): business contains overview, users, trading/markets and economy; technical contains HTTP performance and worker/collection health. Keep the existing prediction-market UID for business; technical uses prediction-market-technical. Link both dashboards with the selected time range preserved. Russian labels, explicit units/windows and descriptions. Zero-traffic latency may be unavailable; no fabricated values. Provision useful warning panels for scrape failures, 5xx and stale worker heartbeat; external alert delivery is out of scope.

Use clearly marked local synthetic data in the isolated database so business panels are populated. Generate HTTP traffic against the real app, including 200/404/auth rejection, and verify observed response metrics. Never contact the real Telegram bot during demo validation. Document demo traffic and remove temporary traffic generators after verification.

## Validation and handoff

1. Tests first for aggregate arithmetic, cohorts, windows, sales/fees, repeated purchase keys, empty data and database failures.
2. Middleware tests for response codes, route cardinality, timings and scrape exclusion; worker success/failure metrics.
3. Existing SQLite suite plus relevant PostgreSQL checks; configuration validation.
4. Running two-worker app exposes combined counts. Prometheus targets are healthy and queries return matching business totals.
5. Grafana API confirms provisioned datasource/dashboard; visually inspect rendered panels and verify representative panel PromQL against Prometheus.
6. Show local URLs, credentials location, start/stop commands and limitations. Do not claim success until the dashboard is actually running.

Implementation details left to builder: pinned compatible image/client versions, free localhost ports, histogram buckets, filenames and panel layout. Docker daemon is currently stopped; start it through the approved local mechanism before runtime validation.

## Open questions

- None.
