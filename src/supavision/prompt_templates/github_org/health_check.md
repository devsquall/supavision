# GitHub Organization Health Check

Audit current GitHub organization security posture against the discovery baseline.

## Available Tools

- **run_diagnostic(command)** — Run approved commands including GitHub CLI (gh)
- **read_file(path)** — Read files

## Baseline (from Discovery)

{{system_context}}

## Checklist

{{checklist}}

## Recent Reports

{{recent_reports}}

{{monitoring_requests}}

## Rate Limit Check (do this first)
- Run `run_diagnostic` with `gh api /rate_limit --jq '.rate.remaining'`
- If remaining < 500, stop and report what you have. Do not retry rate-limited requests.
- If remaining < 1000, limit per-repo checks to top 10 repos by last push date.

## Severity Thresholds

Flag as **critical** if any of:
- Branch protection missing on default branch of any production/active repo
- >5 critical-severity Dependabot alerts open
- Secret scanning alerts older than 7 days unresolved
- 2FA enforcement disabled for the organization

Flag as **warning** if any of:
- >3 high-severity Dependabot alerts open
- Any workflow failure rate >50% in the last 7 days
- Secret scanning alerts older than 30 days (non-critical)
- Outside collaborators with write access to private repos
- >5 repos without CODEOWNERS file

## Investigation Plan

1. List current repositories and compare to baseline
2. Check branch protection on key repos (for each of top 20 by recent push): `gh api /repos/{org}/{repo}/branches/{default_branch}/protection` — if 404, flag as unprotected
3. Verify secret scanning: `gh api /orgs/{org}/secret-scanning/alerts?state=open` — if 403/404, note GHAS not enabled as a gap
4. Check for new members or outside collaborators: `gh api /orgs/{org}/members --jq 'length'`, `gh api /orgs/{org}/outside_collaborators --jq 'length'`
5. Look for newly public repos
6. Check for stale repos with no recent activity
7. Check CODEOWNERS: `gh api /repos/{org}/{repo}/contents/.github/CODEOWNERS` (404 fallback to root)
7. **CI/CD Health**: For repos tracked in baseline, check recent workflow runs:
   `gh run list --repo {org}/{repo} --limit 5`
   - Identify repos where CI went from passing (in baseline) to failing
   - Note any new workflow failures since last check
   - For orgs with >30 repos, sample only the 10 most recently active repos
8. **Dependency Alerts**: Check for new Dependabot alerts since last check:
   `gh api /repos/{org}/{repo}/dependabot/alerts?state=open&sort=created&direction=desc&per_page=5`
   - Flag new alerts created since last check
   - Flag open alerts older than 30 days (unpatched vulnerabilities)
   - Check for any new critical/high severity alerts
9. Verify each checklist item

## Output Format

- **Status**: Overall health (healthy / warning / critical)
- **Repository Changes**: New, deleted, or visibility-changed repos
- **Branch Protection**: Any repos that lost protection
- **Security Features**: Changes to secret scanning, Dependabot
- **Member Changes**: New members, role changes, removed members
- **Compliance**: 2FA enforcement status
- **CI/CD Health**: Repos with new failures, repos that went from passing to failing
- **Dependency Alerts**: New alerts since last check, alerts >30 days old (unpatched)
- **Stale Repos**: Repos with no activity since last check
- **Recommendations**: Specific actions needed, ordered by severity

## Structured Metrics

After your narrative report, output a METRICS section with numeric measurements.
Use EXACTLY these metric names. Report numeric values only (no text, no units in the value).
If you cannot determine a metric, omit that line entirely.

```
=== METRICS ===
total_repos: <number>
repos_unprotected: <number>
dependabot_alerts_critical: <number>
dependabot_alerts_high: <number>
workflow_failures_7d: <number>
members_without_2fa: <number>
```

## Output Requirements
Your output MUST begin with a status line:
## Status: **healthy** | **warning** | **critical**

If any command returns AccessDenied or permission errors, document it under "## Monitoring Gaps" — do NOT report access issues as health problems.
