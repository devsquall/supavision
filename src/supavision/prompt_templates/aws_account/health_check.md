# AWS Account Health Check

Compare current AWS state against the discovery baseline.

## Available Tools

- **run_diagnostic(command)** — Run approved commands including AWS CLI
- **read_file(path)** — Read files
- **list_directory(path)** — List directories

## Baseline (from Discovery)

{{system_context}}

## Checklist

{{checklist}}

## Recent Reports

{{recent_reports}}

{{monitoring_requests}}

## Investigation Plan

1. Run `aws sts get-caller-identity` to verify access
2. Run `aws ec2 describe-instances` — compare states against baseline
3. Check for stopped instances that should be running (or vice versa)
4. Run `aws iam list-access-keys` — check for keys older than 90 days
5. Run `aws ce get-cost-and-usage` — compare against baseline spend
6. Check each item on the checklist

### CloudWatch Performance (if running instances were found in discovery)
- For each running instance, run `run_diagnostic` with `aws cloudwatch get-metric-statistics --namespace AWS/EC2 --metric-name CPUUtilization --dimensions Name=InstanceId,Value=<instance-id> --start-time $(date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%S) --end-time $(date -u +%Y-%m-%dT%H:%M:%S) --period 3600 --statistics Average Maximum`
- Flag instances with **average CPU below 5%** as potentially oversized
- Flag instances with **max CPU above 80%** as potentially undersized
- For EBS volumes attached to running instances, check `aws cloudwatch get-metric-statistics --namespace AWS/EBS --metric-name VolumeReadOps --dimensions Name=VolumeId,Value=<vol-id> --start-time $(date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%S) --end-time $(date -u +%Y-%m-%dT%H:%M:%S) --period 3600 --statistics Sum` (and VolumeWriteOps) — flag volumes with zero IOPS as potentially unused

### RDS Health (if RDS instances were found in discovery)
- For each RDS instance, run `run_diagnostic` with `aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name DatabaseConnections --dimensions Name=DBInstanceIdentifier,Value=<db-id> --start-time $(date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%S) --end-time $(date -u +%Y-%m-%dT%H:%M:%S) --period 3600 --statistics Average Maximum`
- Run `run_diagnostic` with `aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name FreeStorageSpace --dimensions Name=DBInstanceIdentifier,Value=<db-id> --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) --end-time $(date -u +%Y-%m-%dT%H:%M:%S) --period 300 --statistics Minimum` — calculate storage usage percentage, flag if above 85%
- Run `run_diagnostic` with `aws rds describe-pending-maintenance-actions` — flag any pending maintenance
- For read replicas, run `run_diagnostic` with `aws cloudwatch get-metric-statistics --namespace AWS/RDS --metric-name ReplicaLag --dimensions Name=DBInstanceIdentifier,Value=<replica-id> --start-time $(date -u -d '1 hour ago' +%Y-%m-%dT%H:%M:%S) --end-time $(date -u +%Y-%m-%dT%H:%M:%S) --period 300 --statistics Maximum` — flag lag above 30 seconds

### Lambda Health (if Lambda functions were found in discovery)
- For each Lambda function, run `run_diagnostic` with `aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Errors --dimensions Name=FunctionName,Value=<function-name> --start-time $(date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%S) --end-time $(date -u +%Y-%m-%dT%H:%M:%S) --period 86400 --statistics Sum`
- Run `run_diagnostic` with `aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Invocations --dimensions Name=FunctionName,Value=<function-name> --start-time $(date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%S) --end-time $(date -u +%Y-%m-%dT%H:%M:%S) --period 86400 --statistics Sum` — calculate error rate (Errors/Invocations). Flag functions with error rate above 5%
- Run `run_diagnostic` with `aws cloudwatch get-metric-statistics --namespace AWS/Lambda --metric-name Duration --dimensions Name=FunctionName,Value=<function-name> --start-time $(date -u -d '24 hours ago' +%Y-%m-%dT%H:%M:%S) --end-time $(date -u +%Y-%m-%dT%H:%M:%S) --period 86400 --statistics Maximum` — compare max duration against the function's configured timeout. Flag if duration exceeds 80% of timeout
- Flag functions with **zero invocations** in the last 24 hours as potentially unused

## Output Format

- **Status**: Overall health (healthy / warning / critical)
- **Instance States**: Each instance — running/stopped, changes from baseline
- **CloudWatch Performance**: CPU averages/maximums per instance, oversized/undersized flags, EBS IOPS
- **Cost**: Current period vs baseline, any significant increases
- **Security**: IAM key ages, new users, MFA changes
- **RDS Health**: Connection counts, storage usage %, pending maintenance, replica lag
- **Lambda Health**: Error rates, duration vs timeout ratio, zero-invocation functions
- **Changes Since Baseline**: New/removed instances, volumes, functions
- **Recommendations**: Specific actions needed

## Structured Metrics

After your narrative report, output a METRICS section with numeric measurements.
Use EXACTLY these metric names. Report numeric values only (no text, no units in the value).
If you cannot determine a metric, omit that line entirely.

```
=== METRICS ===
monthly_cost_usd: <number>
ec2_running: <number>
ec2_stopped: <number>
rds_instances: <number>
s3_bucket_count: <number>
lambda_function_count: <number>
iam_users: <number>
old_access_keys: <number>
unattached_volumes: <number>
security_groups_open: <number>
```

## Output Requirements
Your output MUST begin with a status line:
## Status: **healthy** | **warning** | **critical**

If any command returns AccessDenied or permission errors, document it under "## Monitoring Gaps" — do NOT report access issues as health problems.
