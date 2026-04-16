# PostgreSQL Health Check

Compare current PostgreSQL state against the discovery baseline.

## Available Tools

- **query_database(query, db_type)** — Run read-only SQL queries (db_type = "postgresql")
- **get_system_metrics** — Host system metrics
- **check_service_status(service_name)** — Database service status
- **check_logs(service, lines)** — Database logs
- **run_diagnostic(command)** — Approved diagnostics

## Baseline (from Discovery)

{{system_context}}

## Checklist

{{checklist}}

## Recent Reports

{{recent_reports}}

{{monitoring_requests}}

## Investigation Plan

### 1. Service & System Health
- Call `check_service_status("postgresql")` to verify the service is running
- Call `get_system_metrics` for current CPU, memory, disk, and I/O
- Compare system metrics to baseline — flag significant changes

### 2. Connection Health
- Query active connections: `SELECT state, count(*) FROM pg_stat_activity GROUP BY state`
- Compare current connection count to baseline max_connections
- Check for connection leaks: `SELECT count(*) FROM pg_stat_activity WHERE state = 'idle' AND state_change < now() - interval '1 hour'`
- Check for blocked queries: `SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'`

### 3. Query Performance
- Check for slow queries currently running (> 5 seconds):
  `SELECT pid, now() - query_start as duration, LEFT(query, 100) as query_preview, state FROM pg_stat_activity WHERE state != 'idle' AND now() - query_start > interval '5 seconds' ORDER BY duration DESC`
- If pg_stat_statements is enabled, check for new expensive queries:
  `SELECT queryid, LEFT(query, 100) as query_preview, calls, total_exec_time, mean_exec_time FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 5`
- Compare top queries to baseline — flag new entries or significant execution time increases
- If pg_stat_statements is NOT available, note this as a monitoring gap

### 4. Storage & Index Health
- Check database sizes and compare to baseline:
  `SELECT datname, pg_database_size(datname) as size_bytes FROM pg_database WHERE NOT datistemplate ORDER BY size_bytes DESC`
- Calculate size growth rate since last check
- Check for new unused indexes (idx_scan = 0):
  `SELECT schemaname, indexrelname, idx_scan, pg_size_pretty(pg_relation_size(indexrelid)) as index_size FROM pg_stat_user_indexes WHERE idx_scan = 0 ORDER BY pg_relation_size(indexrelid) DESC LIMIT 10`
- Check table bloat changes (dead tuple growth):
  `SELECT schemaname, relname, n_dead_tup, n_live_tup, CASE WHEN n_live_tup > 0 THEN round(n_dead_tup::numeric / n_live_tup::numeric * 100, 2) ELSE 0 END as dead_pct FROM pg_stat_user_tables WHERE n_dead_tup > 1000 ORDER BY n_dead_tup DESC LIMIT 10`
- WAL accumulation: check WAL directory size and compare to baseline

### 5. Maintenance Status
- Check vacuum/analyze recency:
  `SELECT schemaname, relname, last_autovacuum, last_autoanalyze, n_dead_tup FROM pg_stat_user_tables WHERE last_autoanalyze < now() - interval '24 hours' OR last_autoanalyze IS NULL ORDER BY n_dead_tup DESC LIMIT 10`
- Check XID age for wraparound risk:
  `SELECT datname, age(datfrozenxid) as xid_age FROM pg_database WHERE NOT datistemplate ORDER BY xid_age DESC`
- Compare XID age to autovacuum_freeze_max_age

### 6. Replication Health (conditional)
- If replication is configured (check baseline), verify replication status:
  `SELECT client_addr, state, sent_lsn, replay_lsn, pg_wal_lsn_diff(sent_lsn, replay_lsn) as replay_lag_bytes FROM pg_stat_replication`
- Check replication lag in time:
  `SELECT client_addr, replay_lag FROM pg_stat_replication`
- Check replication slot status:
  `SELECT slot_name, active, pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn) as retained_bytes FROM pg_replication_slots`
- Check WAL sender health: all WAL senders should be in 'streaming' state
- If replication is NOT configured, skip this section entirely

### 7. Error Log Review
- Review recent logs with `check_logs("postgresql", 100)`
- Look for new FATAL, ERROR, or PANIC entries since last check
- Look for connection refused, OOM, or checkpoint warnings

### 8. Checklist Verification
- Verify each item in the checklist against current values
- Flag any item that has changed from passing to failing

## Output Format

- **Status**: Overall health (healthy / warning / critical)
- **Connections**: Current vs max, idle connections, blocked queries
- **Storage**: Current sizes vs baseline, growth rate since last check
- **Query Performance**: Slow query count (>5s), new expensive queries vs baseline
- **Index Health**: New unused indexes, bloat changes
- **Maintenance**: Tables needing vacuum/analyze, XID wraparound risk
- **Replication**: Lag in bytes and time, slot status, WAL sender state (if applicable)
- **WAL**: Current WAL size, accumulation rate
- **Errors**: New errors in logs since last check
- **Changes Since Baseline**: Size growth, new tables, schema changes
- **Recommendations**: Specific actions needed, ordered by severity

## Structured Metrics

After your narrative report, output a METRICS section with numeric measurements.
Use EXACTLY these metric names. Report numeric values only (no text, no units in the value).
If you cannot determine a metric, omit that line entirely.

```
=== METRICS ===
active_connections: <number>
max_connections: <number>
long_running_queries: <number>
db_size_gb: <number>
dead_tuples_percent: <number>
replication_lag_seconds: <number>
unused_indexes: <number>
slow_queries_24h: <number>
cache_hit_ratio: <number>
```

## Output Requirements
Your output MUST begin with a status line:
## Status: **healthy** | **warning** | **critical**

If any command returns AccessDenied or permission errors, document it under "## Monitoring Gaps" — do NOT report access issues as health problems.
