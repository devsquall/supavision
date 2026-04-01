# Changelog

## 0.1.0 (2026-03-31)

Initial release.

### Features
- AI-powered server discovery and health checks via Claude Code CLI
- Web dashboard with dark theme, resource management, real-time updates
- 5 resource types: Server, AWS Account, Database, GitHub Organization
- REST API with API key authentication and OpenAPI docs
- Slack webhook notifications with smart dedup (24h TTL)
- Rule-based severity evaluation (zero additional LLM cost)
- Type-aware resource creation wizard
- Resource pause/resume, search/filter, pagination
- Responsive design (desktop + mobile)
- Custom CSS design system (zero framework dependencies)
- 340 tests, CI with GitHub Actions
- Docker support with healthcheck

### Resource Types
- **Server** — SSH-based monitoring of Linux servers
- **AWS Account** — CloudWatch, Lambda, EC2, IAM, cost monitoring
- **Database** — PostgreSQL/MySQL health, schema, replication
- **GitHub Organization** — branch protection, security alerts, PRs

### Backends
- **claude_cli** (default) — uses Claude Code CLI, covered by Claude subscription
- **openrouter** — uses OpenRouter API, pay-per-token
