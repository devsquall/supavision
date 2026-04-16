# PostgreSQL Discovery

You are performing initial discovery on a PostgreSQL database to establish a performance and health baseline.

## Available Tools

- **query_database(query, db_type)** — Run read-only SQL queries (db_type = "postgresql")
- **get_system_metrics** — Host system metrics
- **check_service_status(service_name)** — Check database service status
- **check_logs(service, lines)** — Database service logs
- **run_diagnostic(command)** — Approved diagnostic commands

## Investigation Plan

### Layer 1: Service Health
- Call `check_service_status("postgresql")` to verify the service is running
- Call `get_system_metrics` for host-level CPU, memory, disk, and I/O usage

### Layer 2: Database Metadata
- Run `query_database` with `SELECT version()` to get PostgreSQL version
- Run `query_database` with `SELECT datname, pg_database_size(datname) as size_bytes FROM pg_database WHERE NOT datistemplate ORDER BY size_bytes DESC`
- List schemas: `SELECT schema_name FROM information_schema.schemata WHERE schema_name NOT IN ('pg_catalog', 'information_schema')`
- List tables with sizes: `SELECT schemaname, tablename, pg_total_relation_size(schemaname || '.' || tablename) as total_bytes, n_live_tup as row_estimate FROM pg_stat_user_tables ORDER BY pg_total_relation_size(schemaname || '.' || tablename) DESC LIMIT 20`

### Layer 3: Connection Baseline
- Active connections: `SELECT state, count(*) FROM pg_stat_activity GROUP BY state`
- Connection limits: `SHOW max_connections`
- Connection age: `SELECT max(age(clock_timestamp(), backend_start)) as oldest_connection FROM pg_stat_activity`
- Per-database connections: `SELECT datname, count(*) FROM pg_stat_activity GROUP BY datname`

### Layer 4: Query Performance Baseline
- If pg_stat_statements is enabled, get top queries by total execution time:
  `SELECT queryid, LEFT(query, 100) as query_preview, calls, total_exec_time, mean_exec_time, rows FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 5`
- If pg_stat_statements is NOT available, note this as a monitoring gap
- Sequential scans on large tables (potential missing indexes):
  `SELECT schemaname, relname, seq_scan, seq_tup_read, idx_scan, n_live_tup FROM pg_stat_user_tables WHERE n_live_tup > 10000 AND seq_scan > 0 ORDER BY seq_tup_read DESC LIMIT 10`
- Check slow query log settings: `SHOW log_min_duration_statement`

### Layer 5: Index Inventory
- Unused indexes (idx_scan = 0 since last stats reset):
  `SELECT schemaname, indexrelname, idx_scan, pg_size_pretty(pg_relation_size(indexrelid)) as index_size FROM pg_stat_user_indexes WHERE idx_scan = 0 ORDER BY pg_relation_size(indexrelid) DESC LIMIT 10`
- Largest indexes:
  `SELECT schemaname, indexrelname, pg_size_pretty(pg_relation_size(indexrelid)) as index_size FROM pg_stat_user_indexes ORDER BY pg_relation_size(indexrelid) DESC LIMIT 10`
- Index-to-table size ratio (tables where indexes are larger than data):
  `SELECT schemaname, relname, pg_size_pretty(pg_table_size(relid)) as table_size, pg_size_pretty(pg_indexes_size(relid)) as indexes_size, CASE WHEN pg_table_size(relid) > 0 THEN round(pg_indexes_size(relid)::numeric / pg_table_size(relid)::numeric, 2) ELSE 0 END as index_ratio FROM pg_stat_user_tables WHERE pg_table_size(relid) > 0 ORDER BY index_ratio DESC LIMIT 10`

### Layer 6: Maintenance Status
- Last VACUUM and ANALYZE per table:
  `SELECT schemaname, relname, last_vacuum, last_autovacuum, last_analyze, last_autoanalyze, n_dead_tup, n_live_tup FROM pg_stat_user_tables ORDER BY n_dead_tup DESC LIMIT 15`
- Dead tuple counts (bloat indicator):
  `SELECT schemaname, relname, n_dead_tup, n_live_tup, CASE WHEN n_live_tup > 0 THEN round(n_dead_tup::numeric / n_live_tup::numeric * 100, 2) ELSE 0 END as dead_pct FROM pg_stat_user_tables WHERE n_dead_tup > 0 ORDER BY n_dead_tup DESC LIMIT 10`
- Transaction ID age (wraparound risk):
  `SELECT datname, age(datfrozenxid) as xid_age, current_setting('autovacuum_freeze_max_age')::bigint as freeze_max FROM pg_database WHERE NOT datistemplate ORDER BY age(datfrozenxid) DESC`

### Layer 7: Replication & WAL
- If replication is configured, check replication status:
  `SELECT client_addr, state, sent_lsn, write_lsn, flush_lsn, replay_lsn, pg_wal_lsn_diff(sent_lsn, replay_lsn) as replay_lag_bytes FROM pg_stat_replication`
- If replication slots exist:
  `SELECT slot_name, slot_type, active, restart_lsn, confirmed_flush_lsn FROM pg_replication_slots`
- WAL directory size: `run_diagnostic` with `du -sh pg_wal/` or equivalent
- Check key performance settings: `SHOW shared_buffers; SHOW work_mem; SHOW effective_cache_size; SHOW maintenance_work_mem`

### Layer 8: Error Log Review
- Review recent error logs with `check_logs("postgresql", 200)`
- Look for FATAL, ERROR, PANIC entries
- Look for connection refused or too-many-connections errors

{{previous_context}}

{{monitoring_requests}}

## Output Format

=== SYSTEM CONTEXT ===
- **Engine & Version**: PostgreSQL version, OS, configuration
- **Databases**: Names, sizes, owners
- **Tables**: Largest tables with row counts and sizes
- **Connections**: Current active vs max allowed, per-database breakdown
- **Replication**: Status, lag in bytes (if applicable)
- **Storage**: Total database size, tablespace usage, WAL size
- **Performance Settings**: shared_buffers, work_mem, effective_cache_size, maintenance_work_mem
- **Query Baseline**: Top 5 queries by execution time (if pg_stat_statements available)
- **Sequential Scans**: Large tables with high sequential scan counts
- **Index Inventory**: Unused indexes, largest indexes, index-to-table ratios
- **Maintenance**: Last vacuum/analyze times, dead tuple counts, XID age
- **Issues**: Errors in logs, long-running queries, wraparound risk

=== CHECKLIST ===
- Connection count should be below 80% of max_connections
- No queries running longer than 60 seconds
- Dead tuple percentage should be under 20% for all tables
- XID age should be below 50% of autovacuum_freeze_max_age
- No unused indexes larger than 100 MB
- Replication lag should be under 10 MB (if applicable)
- All tables should have been auto-analyzed within the last 24 hours
- WAL directory size should be reasonable for the workload

## Output Requirements
Your output MUST begin with a status line:
## Status: **healthy** | **warning** | **critical**

If any command returns AccessDenied or permission errors, document it under "## Monitoring Gaps" — do NOT report access issues as health problems.
