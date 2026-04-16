# MySQL Health Check

Compare current MySQL state against the discovery baseline.

## Available Tools

- **query_database(query, db_type)** — Run read-only SQL queries (db_type = "mysql")
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
- Call `check_service_status("mysql")` to verify the service is running
- Call `get_system_metrics` for current CPU, memory, disk, and I/O
- Compare system metrics to baseline — flag significant changes

### 2. Connection Health
- Current connections: `SHOW STATUS LIKE 'Threads_connected'`
- Connection summary: `SELECT user, db, command, count(*) as count FROM information_schema.PROCESSLIST GROUP BY user, db, command`
- Compare to baseline max_connections
- Check for sleeping connections older than 1 hour:
  `SELECT id, user, host, db, time, state FROM information_schema.PROCESSLIST WHERE command = 'Sleep' AND time > 3600 ORDER BY time DESC LIMIT 10`
- Check for blocked queries: `SELECT * FROM sys.innodb_lock_waits LIMIT 5`
  (If sys schema unavailable: `SHOW ENGINE INNODB STATUS` and look for LATEST DEADLOCK section)

### 3. Query Performance
- Check for slow queries currently running (> 5 seconds):
  `SELECT id, user, host, db, time, LEFT(info, 100) as query_preview FROM information_schema.PROCESSLIST WHERE command != 'Sleep' AND time > 5 ORDER BY time DESC`
- If performance_schema is enabled, check for new expensive queries:
  `SELECT DIGEST_TEXT, COUNT_STAR as calls, ROUND(SUM_TIMER_WAIT/1000000000000, 3) as total_time_sec, ROUND(AVG_TIMER_WAIT/1000000000000, 3) as avg_time_sec FROM performance_schema.events_statements_summary_by_digest ORDER BY SUM_TIMER_WAIT DESC LIMIT 5`
- Compare top queries to baseline — flag new entries or significant time increases
- If performance_schema is NOT available, note this as a monitoring gap

### 4. Storage & Index Health
- Check database sizes and compare to baseline:
  `SELECT table_schema, ROUND(SUM(data_length + index_length)) as total_bytes FROM information_schema.TABLES GROUP BY table_schema ORDER BY total_bytes DESC`
- Calculate size growth rate since last check
- Check for new unused indexes:
  `SELECT object_schema, object_name, index_name FROM performance_schema.table_io_waits_summary_by_index_usage WHERE index_name IS NOT NULL AND count_star = 0 AND object_schema NOT IN ('mysql', 'performance_schema', 'sys') LIMIT 10`
  (If performance_schema unavailable, skip and note)
- Table fragmentation changes:
  `SELECT table_schema, table_name, ROUND(data_free / 1024 / 1024, 2) as free_mb, ROUND(data_free / (data_length + index_length) * 100, 2) as frag_pct FROM information_schema.TABLES WHERE data_free > 10485760 AND table_schema NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys') ORDER BY data_free DESC LIMIT 10`
- Binary log accumulation: `SHOW BINARY LOGS` — compare total size to baseline

### 5. InnoDB Health
- Buffer pool status: `SHOW STATUS LIKE 'Innodb_buffer_pool%'`
- Calculate buffer pool hit ratio: `1 - (Innodb_buffer_pool_reads / Innodb_buffer_pool_read_requests)` — should be > 99%
- Dirty pages: `SHOW STATUS LIKE 'Innodb_buffer_pool_pages_dirty'`
- Row lock waits: `SHOW STATUS LIKE 'Innodb_row_lock%'`
- Deadlock check: parse `SHOW ENGINE INNODB STATUS` for LATEST DEADLOCK section

### 6. Replication Health (conditional)
- If replication is configured (check baseline), verify replica status:
  `SHOW REPLICA STATUS` (MySQL 8.0.22+) or `SHOW SLAVE STATUS`
- Key checks:
  - Replica_IO_Running must be Yes
  - Replica_SQL_Running must be Yes
  - Seconds_Behind_Source — compare to baseline, flag increases
  - Last_Error — must be empty
- If this is a source, check connected replicas:
  `SHOW REPLICAS` or `SHOW SLAVE HOSTS`
- If replication is NOT configured, skip this section entirely

### 7. Error Log Review
- Review recent logs with `check_logs("mysql", 100)`
- Look for new [ERROR] or [Warning] entries since last check
- Look for connection abort, deadlock, or table corruption messages

### 8. Checklist Verification
- Verify each item in the checklist against current values
- Flag any item that has changed from passing to failing

## Output Format

- **Status**: Overall health (healthy / warning / critical)
- **Connections**: Current vs max, sleeping connections, blocked queries
- **Storage**: Current sizes vs baseline, growth rate since last check
- **Query Performance**: Slow query count (>5s), new expensive queries vs baseline
- **Index Health**: New unused indexes, fragmentation changes
- **InnoDB**: Buffer pool hit ratio, dirty pages, row lock waits, deadlocks
- **Replication**: IO/SQL thread status, lag in seconds, last error (if applicable)
- **Binary Logs**: Current size, accumulation rate
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
