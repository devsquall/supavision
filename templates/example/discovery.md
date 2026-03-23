# Discovery Template — Example Resource

You are performing initial discovery on a monitored resource.

## Resource Information
- Name: {{resource_name}}
- Type: {{resource_type}}

## Your Task

Explore this environment and document what you find. Produce a structured report with two clearly delimited sections.

## Investigation Areas

1. What services or components are present
2. What the current operational state looks like
3. Any configuration details worth noting
4. Anything that looks unusual or concerning

{{monitoring_requests}}

## Output Format

You MUST structure your output with these exact section headers:

=== SYSTEM CONTEXT ===
(Write a structured summary of what you found. This will be used as the baseline for future health checks.)

=== CHECKLIST ===
(Write a list of specific things to verify on every future health check, based on what you found.)
- Each item should be actionable and specific
- Only include items relevant to what actually exists
