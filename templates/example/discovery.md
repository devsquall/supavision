# Discovery — {{resource_type}}

You are performing initial discovery on a monitored resource.

## Available Tools

You have access to these tools for investigating the system:
- **get_system_metrics** — CPU load, memory, disk, top processes, network ports
- **check_service_status(service_name)** — Status of a systemd service
- **read_file(path)** — Read a file's contents
- **list_directory(path)** — List files in a directory
- **check_logs(service, lines)** — Recent journalctl logs for a service
- **run_diagnostic(command)** — Run approved commands (docker ps, nginx -t, pm2 list, curl localhost, etc.)

## Your Task

Explore this environment and document what you find. Use the tools above to gather real data — do not guess or assume.

### Investigation Areas

1. Call `get_system_metrics` first for a system overview
2. Identify running services and check their status
3. Explore key directories and configuration files
4. Check logs for any errors or warnings
5. Note anything unusual or concerning

{{previous_context}}

{{monitoring_requests}}

## Output Format

You MUST structure your output with these exact section headers:

=== SYSTEM CONTEXT ===
(Write a structured summary of what you found. This will be used as the baseline for future health checks.)

=== CHECKLIST ===
(Write a list of specific things to verify on every future health check, based on what you found.)
- Each item should be actionable and specific
- Include expected values where possible
- Only include items relevant to what actually exists
