# Supavision — Product-Grade Frontend Blueprint (Workflow 1)

## Objective

Build a **product-grade frontend** by focusing on a single flagship workflow:

> **Add codebase → scan → evaluate → approve → implement**

This workflow is the **quality benchmark**. If this feels polished, the product feels polished.

---

# 1. Guiding Principles

### 1.1 From Data → Decision

❌ Do NOT design around data
✅ Design around decisions

Each screen must answer:

* What’s happening?
* What matters?
* What should I do next?

---

### 1.2 No Dead UI

Every interaction must:

* Show feedback immediately
* Change UI state visibly
* Never leave user guessing

---

### 1.3 One Primary Action per Screen

At any moment, the user should clearly see:
👉 the next step

---

### 1.4 Continuous Flow

The experience must feel:

* fast
* connected
* alive

Avoid:

* full page reloads
* disjoint transitions

---

# 2. Workflow 1 (Build First)

## Goal

A new user can:

1. Add a codebase
2. Run scan
3. See findings
4. Evaluate one
5. Approve it
6. Implement fix
7. See result

All within **5 minutes** without confusion.

---

# 3. Screens to Build (Only These First)

1. Dashboard
2. Add Resource
3. Resource Detail
4. Finding Detail

Everything else waits.

---

# 4. Page Layout Blueprint

---

## 4.1 Dashboard

### Purpose

Entry point. Drive user into workflow.

### Layout

```
Topbar
Page Header
Primary CTA Section
Summary Cards
Resource Cards
Recent Activity
```

### Content Order

1. **Header**

   * Title: Dashboard
   * Subtitle: Monitor infrastructure and improve codebases

2. **Primary CTA**

   * Title: Start with a codebase scan
   * Description: Add a codebase and let Supavision find issues
   * Buttons:

     * Scan a Codebase (primary)
     * Add Infrastructure Resource (secondary)

3. **Summary Cards**

   * Resources
   * Critical
   * Warning
   * Healthy

4. **Resource Cards**
   Each card shows:

   * Name
   * Type
   * Status badge
   * Short summary
   * Action button

5. **Recent Activity**

   * Last 3–5 events
   * Link to full activity

### Key Rule

👉 Dashboard is NOT a report
👉 It is a **launchpad**

---

## 4.2 Add Resource

### Purpose

Smooth onboarding step

### Layout

```
Breadcrumb
Title
Resource Type Cards
Dynamic Form
Validation
Footer Actions
```

### Behavior

When selecting Codebase:

* Show form immediately
* Fields:

  * name
  * path

### Inline Validation

Example:

```
✓ Directory exists
✓ 247 files detected
✓ Languages: Python, JS
```

### Errors

* Must be inline
* Must be specific

### Actions

* Create Resource (primary)
* Cancel

### Critical Rule

👉 After creation → redirect to resource detail
👉 NOT dashboard

---

## 4.3 Resource Detail

### Purpose

Guide user to scan + findings

### Layout

```
Breadcrumb
Header (name + status + actions)
Setup or Action Panel
Overview
Findings Section
Recent Activity
Details (collapsed)
```

### Content

#### Header

* Resource name
* Type badge
* Status badge
* Actions:

  * Run Scan (primary)
  * Edit

---

#### If No Findings

Show:

```
No findings yet
Run a scan to discover issues

[Run Scan]
```

---

#### Overview

* Path
* Last scan
* Findings count

---

#### Findings Section

After scan:

Each item shows:

* Severity badge
* Title (human readable)
* File:line
* Stage badge
* Action button

---

#### Recent Activity

* Scan started
* Scan completed
* Evaluation events

---

#### Details (Collapsed)

* Config
* Metadata
* Raw output

---

### Key Rule

👉 This is NOT a data page
👉 This is a **“what to do next” page**

---

## 4.4 Finding Detail

### Purpose

Decision screen (most important page)

### Layout

```
Breadcrumb
Header
Decision Panel
Evidence Panel
Live Job Panel
Result Panel
History
```

---

### Content

#### Header

* Title
* Severity badge
* Stage badge
* Action:

  * Evaluate → Approve → Implement

---

#### Decision Panel (TOP)

Must include:

* Verdict
* Explanation
* Recommended fix
* Effort

Buttons:

* Approve
* Reject

---

#### Evidence Panel

* File path
* Code snippet
* Highlighted line

---

#### Live Job Panel

* Streaming output
* Status
* Live indicator

---

#### Result Panel

* Branch
* Commit
* Diff viewer

---

#### History

* Stage transitions
* Previous jobs

---

### Key Rule

👉 Decision comes FIRST
👉 Code is supporting evidence

---

# 5. Interaction Rules

---

## Buttons

* Show spinner instantly
* Disable on click
* No duplicate actions

---

## Loading

* Use skeletons
* Never blank screen

---

## Errors

* Always specific
* Always actionable

---

## Live Updates

* Show "Live" indicator
* No reloads
* SSE/HTMX

---

## No Dead Clicks

Every click must:

* update UI
* redirect
* show feedback

---

# 6. Components (Only What’s Needed)

### Required

* Page Header
* Alert / Callout
* Empty State
* Spinner Button
* Status Badge
* Code Block
* Diff Viewer
* Live Indicator

### Not Needed Yet

* Modal
* Tooltips
* Keyboard shortcuts
* Bulk actions

---

# 7. Build Order

---

## Phase W1-A

* Base layout (topbar + shell)
* Page header
* Empty / loading / error states
* Dashboard CTA

---

## Phase W1-B

* Add Resource flow
* Inline validation
* Redirect to resource detail

---

## Phase W1-C

* Resource detail
* Scan flow
* Findings appear

---

## Phase W1-D

* Finding detail
* Evaluate → Approve → Implement
* Live output + diff

---

## Phase W1-E

* Polish entire workflow

---

# 8. Acceptance Checklist

The team must demo:

1. Start on dashboard
2. Click Scan Codebase
3. Create resource
4. Land on detail page
5. Run scan
6. Findings appear without reload
7. Open finding
8. Evaluate (live feedback)
9. Approve
10. Implement
11. See diff
12. No confusion at any step

---

# 9. Final Rule

> If a change does NOT improve this workflow, it does NOT get built yet.

---

# 10. Dev Instruction (Final)

Build only Workflow 1 as the product-quality benchmark. Limit scope to Dashboard, Add Resource, Resource Detail, and Finding Detail. Each page must clearly show the next action and provide immediate feedback. Resource Detail must focus on scanning and findings, and Finding Detail must be decision-first. Do not expand to other pages until this workflow feels polished end-to-end.

---

**End of Blueprint**
