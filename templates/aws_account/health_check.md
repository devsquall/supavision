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

## Output Format

- **Status**: Overall health (healthy / warning / critical)
- **Instance States**: Each instance — running/stopped, changes from baseline
- **Cost**: Current period vs baseline, any significant increases
- **Security**: IAM key ages, new users, MFA changes
- **Changes Since Baseline**: New/removed instances, volumes, functions
- **Recommendations**: Specific actions needed
