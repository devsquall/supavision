# Database Health Check

Compare current database state against the discovery baseline.

## Available Tools

- **query_database(query, db_type)** — Run read-only SQL queries
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

1. Check database service status
2. Query active connections and compare to baseline limits
3. Check for long-running queries (> 60 seconds)
4. Query database sizes and compare to baseline
5. Check replication lag if applicable
6. Review error logs for new issues
7. Verify each checklist item

## Output Format

Your output MUST begin with a status line:
## Status: **healthy** | **warning** | **critical**

Then include:
- **Connections**: Current vs max, any connection pool issues
- **Storage**: Current sizes vs baseline, growth rate
- **Performance**: Long-running queries, lock contention
- **Replication**: Lag, status (if applicable)
- **Errors**: New errors in logs since last check
- **Changes Since Baseline**: Schema changes, size growth, new tables
- **Recommendations**: Specific actions needed
