# AWS Account Discovery

You are performing initial discovery on an AWS account to establish a baseline.

## Available Tools

- **get_system_metrics** — System overview (if running on the AWS instance)
- **run_diagnostic(command)** — Run approved commands including AWS CLI
- **read_file(path)** — Read credential/config files
- **list_directory(path)** — List directories

## Investigation Plan

### Layer 1: Account Identity
- Run `run_diagnostic` with `aws sts get-caller-identity` to confirm account access
- Run `run_diagnostic` with `aws configure list` to check configured region

### Layer 2: Compute Resources
- Run `run_diagnostic` with `aws ec2 describe-instances` to list EC2 instances
- Note instance types, states (running/stopped), and their purposes
- Run `run_diagnostic` with `aws ec2 describe-volumes` to check EBS volumes

### Layer 3: Database & Storage
- Run `run_diagnostic` with `aws rds describe-db-instances` to list RDS databases
- Run `run_diagnostic` with `aws s3 ls` to list S3 buckets

### Layer 4: Serverless & Other
- Run `run_diagnostic` with `aws lambda list-functions` to check Lambda functions
- Run `run_diagnostic` with `aws iam list-users` to check IAM users

### Layer 5: Cost & Security
- Run `run_diagnostic` with `aws ce get-cost-and-usage` for recent billing
- Run `run_diagnostic` with `aws iam list-access-keys` to check key ages

### Layer 6: Security Posture
- Run `run_diagnostic` with `aws ec2 describe-security-groups` — flag any inbound rules allowing 0.0.0.0/0 on ports other than 80 and 443
- Run `run_diagnostic` with `aws s3api list-buckets` then for each bucket run `aws s3api get-bucket-acl --bucket <name>` and `aws s3api get-public-access-block --bucket <name>` — flag buckets with public access
- Run `run_diagnostic` with `aws ec2 describe-volumes --filters Name=encrypted,Values=false` — flag unencrypted EBS volumes
- Run `run_diagnostic` with `aws rds describe-db-instances` — check StorageEncrypted field for each instance, flag unencrypted databases
- Run `run_diagnostic` with `aws cloudtrail describe-trails` and `aws cloudtrail get-trail-status --name <trail>` — verify CloudTrail is enabled and logging
- Run `run_diagnostic` with `aws guardduty list-detectors` — check if GuardDuty is active

### Layer 7: Cost Intelligence
- Run `run_diagnostic` with `aws ce get-cost-and-usage --time-period Start=$(date -d '-2 months' +%Y-%m-01),End=$(date +%Y-%m-01) --granularity MONTHLY --metrics UnblendedCost --group-by Type=DIMENSION,Key=SERVICE` — compare this month vs last month by service
- Identify idle resources:
  - Stopped EC2 instances that still have attached EBS volumes (cost from storage)
  - Run `run_diagnostic` with `aws ec2 describe-volumes --filters Name=status,Values=available` — unattached EBS volumes
  - Run `run_diagnostic` with `aws ec2 describe-addresses` — unused Elastic IPs (those without an associated instance)

### Layer 8: Networking
- Run `run_diagnostic` with `aws elbv2 describe-load-balancers` — list ALBs and NLBs
- For each load balancer, run `run_diagnostic` with `aws elbv2 describe-target-groups --load-balancer-arn <arn>` then `aws elbv2 describe-target-health --target-group-arn <tg-arn>` — flag unhealthy targets
- Run `run_diagnostic` with `aws ec2 describe-nat-gateways --filter Name=state,Values=available` — list active NAT Gateways and note their subnets

{{previous_context}}

{{monitoring_requests}}

## Output Format

=== SYSTEM CONTEXT ===
Document everything found:
- **Account**: Account ID, alias, configured region
- **EC2 Instances**: Each instance with ID, type, state, purpose, key pair
- **Volumes**: EBS volumes, sizes, attachment status, encryption status
- **RDS Databases**: Engine, version, size, multi-AZ, backup status, encryption status
- **S3 Buckets**: Names, regions, public access status
- **Lambda Functions**: Names, runtimes, memory
- **IAM Users**: Usernames, access key ages, MFA status
- **Cost Summary**: Recent monthly spend, top services, month-over-month change
- **Security Posture**: Open security groups, public buckets, unencrypted resources, CloudTrail/GuardDuty status
- **Idle Resources**: Stopped instances with EBS, unattached volumes, unused Elastic IPs
- **Networking**: Load balancers with target health, NAT Gateways

=== CHECKLIST ===
- Specific items to verify on every health check
- Include expected values (e.g., "Instance i-xxx should be running")
- Track IAM key ages, cost thresholds, instance states
- Track security group rules, encryption status, CloudTrail logging
- Track load balancer target health states
- Track idle resource counts for cost monitoring

## Output Requirements
Your output MUST begin with a status line:
## Status: **healthy** | **warning** | **critical**

If any command returns AccessDenied or permission errors, document it under "## Monitoring Gaps" — do NOT report access issues as health problems.
