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

## Investigation Plan

1. List current repositories and compare to baseline
2. Check branch protection on key repos
3. Verify secret scanning is enabled
4. Check for new members or outside collaborators
5. Look for newly public repos
6. Check for stale repos with no recent activity
7. Verify each checklist item

## Output Format

- **Status**: Overall health (healthy / warning / critical)
- **Repository Changes**: New, deleted, or visibility-changed repos
- **Branch Protection**: Any repos that lost protection
- **Security Features**: Changes to secret scanning, Dependabot
- **Member Changes**: New members, role changes, removed members
- **Compliance**: 2FA enforcement status
- **Stale Repos**: Repos with no activity since last check
- **Recommendations**: Specific actions needed
