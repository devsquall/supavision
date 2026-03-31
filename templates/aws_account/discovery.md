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

{{previous_context}}

{{monitoring_requests}}

## Output Format

=== SYSTEM CONTEXT ===
Document everything found:
- **Account**: Account ID, alias, configured region
- **EC2 Instances**: Each instance with ID, type, state, purpose, key pair
- **Volumes**: EBS volumes, sizes, attachment status
- **RDS Databases**: Engine, version, size, multi-AZ, backup status
- **S3 Buckets**: Names, regions
- **Lambda Functions**: Names, runtimes, memory
- **IAM Users**: Usernames, access key ages, MFA status
- **Cost Summary**: Recent monthly spend, top services
- **Security Concerns**: Old access keys (>90 days), users without MFA

=== CHECKLIST ===
- Specific items to verify on every health check
- Include expected values (e.g., "Instance i-xxx should be running")
- Track IAM key ages, cost thresholds, instance states
