# GitHub Organization Discovery

You are performing initial discovery on a GitHub organization to audit security posture and configuration.

## Available Tools

- **run_diagnostic(command)** — Run approved commands including GitHub CLI (gh)
- **read_file(path)** — Read files
- **list_directory(path)** — List directories

## Investigation Plan

### Layer 1: Organization Overview
- Run `run_diagnostic` with `gh api /orgs/{org}` to get org details (replace {org} with org name from resource config)
- Run `run_diagnostic` with `gh repo list {org} --limit 50` to list repositories

### Layer 2: Repository Security
- For key repositories, check branch protection:
  `gh api /repos/{owner}/{repo}/branches/main/protection`
- Check if secret scanning is enabled
- Check for Dependabot alerts

### Layer 3: Members & Access
- Run `run_diagnostic` with `gh api /orgs/{org}/members` to list members
- Check for outside collaborators
- Review team structure

### Layer 4: Activity & Health
- Check for stale repos (no activity in 90+ days)
- Look for repos without branch protection on default branch
- Check for public repos that should be private

### Layer 5: CI/CD Inventory
- For orgs with more than 30 repos, sample only the 10 most recently active repos. Otherwise check all.
- For each sampled repo, check recent workflow runs:
  `gh run list --repo {org}/{repo} --limit 5`
- Identify repos with GitHub Actions configured (have `.github/workflows/`)
- Note repos with recently failed workflows (any failure in last 5 runs)
- Summarize: total repos with CI, repos with recent failures, repos with no CI

### Layer 6: Dependency Security
- For each sampled repo, check Dependabot alert counts:
  `gh api /repos/{org}/{repo}/vulnerability-alerts` (check if enabled)
  `gh api /repos/{org}/{repo}/dependabot/alerts?state=open&per_page=1 --jq 'length'`
- Check code scanning status:
  `gh api /repos/{org}/{repo}/code-scanning/alerts?state=open&per_page=1` (note if 404 = not enabled)
- Check secret scanning status:
  `gh api /repos/{org}/{repo}/secret-scanning/alerts?state=open&per_page=1` (note if 404 = not enabled)
- Summarize: repos with Dependabot enabled, open alert counts, code scanning coverage, secret scanning coverage

{{previous_context}}

{{monitoring_requests}}

## Output Format

=== SYSTEM CONTEXT ===
- **Organization**: Name, plan, member count, billing info
- **Repositories**: List with visibility (public/private), default branch, last push date
- **Branch Protection**: Which repos have protection on default branch, rules
- **Security Features**: Secret scanning, Dependabot, code scanning status per repo
- **Members**: Count, roles (admin/member), 2FA enforcement
- **Stale Repos**: Repos with no activity in 90+ days
- **CI/CD**: Repos with GitHub Actions, recent workflow failure summary
- **Dependency Security**: Dependabot alert counts, code scanning coverage, secret scanning coverage
- **Concerns**: Public repos, unprotected branches, disabled security features, failing CI, open alerts

=== CHECKLIST ===
- All production repos should have branch protection enabled
- Secret scanning should be enabled on all private repos
- No new public repos without approval
- 2FA should be enforced for all members
- No outside collaborators on private repos
- CI workflows should be passing on default branch for all active repos
- Dependabot should be enabled on all repos with dependencies
- No critical/high Dependabot alerts older than 30 days

## Output Requirements
Your output MUST begin with a status line:
## Status: **healthy** | **warning** | **critical**

If any command returns AccessDenied or permission errors, document it under "## Monitoring Gaps" — do NOT report access issues as health problems.
