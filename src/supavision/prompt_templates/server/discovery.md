# Server Discovery

You are performing initial discovery on a server to establish a baseline of what "normal" looks like.

## Available Tools

You have access to these tools for investigating the server:
- **get_system_metrics** — CPU load, memory, disk, top processes, network ports
- **check_service_status(service_name)** — Status of a systemd service
- **read_file(path)** — Read a file's contents
- **list_directory(path)** — List files in a directory
- **check_logs(service, lines)** — Recent journalctl logs for a service
- **run_diagnostic(command)** — Run approved commands (docker ps, nginx -t, pm2 list, curl localhost, etc.)

## Investigation Plan

Work through these layers systematically:

### Layer 1: System Overview
- Call `get_system_metrics` to get CPU, memory, disk, processes, and open ports
- Identify what services are listening and on which ports

### Layer 2: Services & Applications
- For each service discovered, call `check_service_status` to get its status
- Use `run_diagnostic` with commands like `pm2 list`, `docker ps`, `nginx -t` as appropriate
- Read key config files: `/etc/nginx/nginx.conf`, `/etc/nginx/sites-enabled/`, application configs

### Layer 3: Application Stack
- Explore application directories (check `/home/`, `/var/www/`, `/opt/`)
- Read README.md, package.json, docker-compose.yml, .env.example to understand the stack
- Identify databases, caches, message queues

### Layer 4: Logs & Health
- Check logs for critical services (`check_logs` for nginx, database, application services)
- Look for error patterns, warnings, or recurring issues
- Test health endpoints if any exist (`run_diagnostic` with curl)

### Layer 5: Security Baseline
- Run `run_diagnostic` with `ufw status verbose` or `iptables -L -n` — document all firewall rules
- Run `run_diagnostic` with `grep -r 'NOPASSWD' /etc/sudoers /etc/sudoers.d/ 2>/dev/null` — list users with passwordless sudo
- Run `run_diagnostic` with `cat /etc/passwd | awk -F: '$3 == 0 {print $1}'` — list root-equivalent users
- Run `run_diagnostic` with `dpkg -l unattended-upgrades 2>/dev/null || rpm -q dnf-automatic 2>/dev/null` — check if automatic security updates are configured
- Run `run_diagnostic` with `ss -tlnp` — document all listening services and cross-reference against known services. Flag any unknown listeners

### Layer 6: Application Stack (Deeper)
- For each web application found, run `run_diagnostic` with `curl -s -o /dev/null -w '%{http_code}' http://localhost:<port>/health` (or similar health endpoint paths like `/api/health`, `/healthz`, `/status`) — document all health endpoint URLs and their responses
- If Docker is present, run `run_diagnostic` with `docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'` — record image versions (tags) and restart counts from the Status field
- If PM2 is present, run `run_diagnostic` with `pm2 jlist` — record each process name, memory usage, CPU usage, and restart count

### Layer 7: Certificate Inventory
- For each HTTPS-serving port found, run `run_diagnostic` with `echo | openssl s_client -connect localhost:<port> -servername <domain> 2>/dev/null | openssl x509 -noout -dates -subject` — record issuer, subject, and expiry date
- Run `run_diagnostic` with `ls -la /etc/letsencrypt/live/ 2>/dev/null` — list Let's Encrypt managed domains
- If Let's Encrypt is present, run `run_diagnostic` with `certbot certificates 2>/dev/null` — record each certificate's domains, expiry, and renewal status
- Flag any certificates expiring within 14 days

{{previous_context}}

{{monitoring_requests}}

## Output Format

You MUST structure your final output with these exact section headers:

=== SYSTEM CONTEXT ===
Document everything you found in structured sections:
- **Hardware & Resources**: CPU, memory, disk, swap
- **Operating System**: Version, uptime, kernel
- **Services**: Each running service with its status, port, and purpose
- **Applications**: Each application with its stack, config location, and health
- **Databases**: Type, version, connection details (no passwords)
- **Network**: Open ports, domains, SSL certificates
- **Security Baseline**: Firewall rules, passwordless sudo users, auto-updates status, unknown listeners
- **Health Endpoints**: URL and expected response code for each endpoint
- **Docker/PM2 Baseline**: Image versions, restart counts, memory/CPU per process
- **Certificate Inventory**: Each certificate with domain, issuer, expiry date, renewal method
- **Critical Issues**: Anything that needs immediate attention

=== CHECKLIST ===
List specific items to verify on every future health check:
- Each item should be actionable and measurable
- Include expected values where possible (e.g., "Disk usage should be below 80%")
- Only include items relevant to what actually exists on this server
- Include health endpoint URLs to curl on each check
- Include certificate expiry dates to track
- Include Docker image versions and PM2 restart counts as baseline

## Output Requirements
Your output MUST begin with a status line:
## Status: **healthy** | **warning** | **critical**

If any command returns AccessDenied or permission errors, document it under "## Monitoring Gaps" — do NOT report access issues as health problems.
