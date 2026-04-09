# Architecture: Two-Lane Design

Supavision monitors two kinds of resources: **infrastructure** (servers, AWS, databases) and **codebases** (local projects). These produce fundamentally different outputs and follow different lifecycles, so the data model splits into two parallel lanes.

## The Two Lanes

```
                          Resource
                         /        \
              ┌─────────┘          └──────────┐
              │                               │
         LANE 1: Health                  LANE 2: Work
    (resource-level pulse)          (per-issue lifecycle)
              │                               │
         Run → Report → Evaluation       WorkItem (Finding | ManualTask)
              │                               │
    "Is this resource healthy?"      "Is this specific issue real?
     Severity: healthy/warning/       Should we fix it? Track it
     critical. One per run.           through eval → approve →
     Aggregate narrative."            implement → complete."
```

## Rules

### Lane 1 (Health)
- **Report** = aggregate narrative about a resource's overall state. One per Run. Used for health dashboards and alerting. Never contains per-issue lifecycle state.
- **Evaluation** = severity assessment of a Report. Answers "how healthy is this resource?" Uses `Severity` (healthy/warning/critical). Stored in the `evaluations` table.

### Lane 2 (Work)
- **WorkItem** = a single actionable issue with its own lifecycle. Has a stage (scanned/evaluated/approved/implementing/completed/rejected/dismissed), its own agent jobs, feedback, and transitions.
- **Finding-level evaluation** is stored as fields ON the WorkItem (`evaluation_verdict`, `evaluation_reasoning`, `fix_approach`), NOT as a row in the `evaluations` table.

### The Boundary
- Code that touches Lane 1 must never import WorkItem models.
- Code that touches Lane 2 must never write to the `evaluations` table.
- The only place both lanes appear together is the resource detail page in the UI.

## Import Rules

```
models/
├── core.py    ← Shared: Resource, Run, Credential, Schedule (both lanes)
├── health.py  ← Lane 1: Report, Evaluation, Severity, SystemContext, Checklist
└── work.py    ← Lane 2: Finding, ManualTask, AgentJob, Transition, BlocklistEntry
```

| Domain | Imports from |
|--------|-------------|
| Infrastructure (engine.py, evaluator.py, tools.py, executor.py, discovery_diff.py) | `models.core` + `models.health` only |
| Codebase (scanner.py, blocklist.py, agent_runner.py, code_evaluator.py) | `models.core` + `models.work` only |
| Shared (db.py, web/, cli.py, scheduler.py, mcp.py) | All models (via `models.__init__`) |

Enforced by `tests/test_lane_boundary.py` (AST-based import verification).

## Anti-Patterns (Do Not)

1. **Do not add lifecycle stages to Reports.** Reports are snapshots, not workflows.
2. **Do not use WorkItems for infrastructure health.** "High CPU" is a Report with severity=warning, not a WorkItem.
3. **Do not nest WorkItems inside Reports.** They share a parent Resource and a Run ID, but are siblings, not parent-child.
4. **Do not write finding verdicts to the evaluations table.** Finding-level judgments live on the WorkItem model.
