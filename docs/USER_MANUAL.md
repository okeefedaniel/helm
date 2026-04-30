# Helm User Manual

Helm is the executive dashboard for the DockLabs suite. It rolls up real-time
metrics, action items, and alerts from every product (Admiralty, Beacon,
Bounty, Harbor, Lookout, Manifest, Purser, Yeoman) into a single view, and
hosts a lightweight project-management surface for cross-cutting work the
peer products don't already own.

This manual covers the user-facing surface end to end. For operations, see
[CLAUDE.md](../CLAUDE.md) and [incidents/](../incidents/).

---

## Contents

1. [Overview](#overview)
2. [Roles](#roles)
3. [Getting Started](#getting-started)
4. [Dashboard — Today tab](#dashboard--today-tab)
5. [Dashboard — Across the suite tab](#dashboard--across-the-suite-tab)
6. [My Work](#my-work)
7. [Projects](#projects)
8. [Tasks](#tasks)
9. [Collaboration](#collaboration)
10. [FOIA Projects](#foia-projects)
11. [Capital Improvement Plan (CIP) Projects](#capital-improvement-plan-cip-projects)
12. [Public Transparency](#public-transparency)
13. [Calendar](#calendar)
14. [Notifications](#notifications)
15. [Exports](#exports)
16. [Scheduled Jobs & Health Metrics](#scheduled-jobs--health-metrics)
17. [Status Reference](#status-reference)
18. [Keyboard Shortcuts](#keyboard-shortcuts)
19. [Support](#support)

---

## Overview

Helm has two complementary surfaces:

- **The Dashboard** — `/dashboard/` — split into a personal *Today* tab
  (your inbox across the suite) and an *Across the suite* tab (executive
  view of every product). This is the canonical post-login URL.
- **Tasks** — `/tasks/projects/` — a project / task / collaborator surface
  for work that doesn't fit any single peer product (interagency
  coordination, FOIA case management, capital improvement plans). Gated
  by `HELM_TASKS_ENABLED`; deployments opt in.

Helm does not store grants, FOIA requests, opportunities, signing packets,
etc. Those live in the products that own them. Helm aggregates and links.

---

## Roles

Helm-specific roles are issued via Keel SSO and surfaced in your sidebar
profile.

| Role | Capability |
|---|---|
| **Admin** (`helm_admin`) | Full access. Manage projects, collaborators, archives, public visibility, FOIA tolling, GovQA push, and Project Online imports. |
| **Director** (`helm_director`) | Read all dashboards and projects. Create projects, claim, collaborate, transition, comment, and attach. |
| **Viewer** (`helm_viewer`) | Read-only across the dashboard and projects assigned to or shared with you. |

Suite-level roles (`system_admin`) inherit Admin behavior on Helm.

---

## Getting Started

### Signing in

1. From any DockLabs product, click **Helm** in the fleet switcher, or visit
   `https://helm.docklabs.ai/`.
2. Click **Sign in with DockLabs** (the suite OIDC button).
3. You'll land on `/dashboard/`.

If you're already signed in to another DockLabs product, the redirect is
seamless — no second login form.

### What you'll see first

- **Today tab** — your personal inbox: My Work, Awaiting Me, and Alerts.
- **Across the suite tab** — fleet-wide metrics and drill-downs.

The active tab is sticky via the `?tab=today|suite` query string, so
reloads land you where you left off.

---

## Dashboard — Today tab

The Today tab is your personal stand-up view. Three columns:

### 1. My Work

A deadline rail of every open task assigned to you, grouped:

- **Overdue** — past due, not done.
- **Today** — due today.
- **This week** — due in the next 7 days.
- **Upcoming** — anything later.

Tasks are pulled from Helm's task store. The same predicate powers
`/tasks/` — the two surfaces never drift apart.

### 2. Awaiting Me

Cross-suite inbox. For each product where you have a role, Helm queries
that product's `/api/v1/helm-feed/inbox/` endpoint and lists the items
where *you* are the gating dependency, grouped by product:

| Peer | What appears here |
|---|---|
| **Manifest** | Active signing steps where you are the signer and the packet is in progress. |
| **Harbor** | Open review assignments and processing assignments. |
| **Admiralty** | FOIA requests assigned to you in an open status, with the statutory deadline as the due date. |
| **Purser** | Submissions you can review (your program, not yet reviewed). |
| **Bounty** | New high-relevance opportunity matches for you. |
| **Beacon** | Keep-in-touch reminders due in 3 days; pending company approvals you own. |
| **Lookout** | Tracked bills you're driving (researching, drafting testimony, collaborating). |
| **Yeoman** | Speaking invitations assigned, principaled, or delegated to you. |

If a peer hasn't yet implemented `/inbox/` (or it's offline), Helm falls
back to a coarse count from that peer's aggregate feed and renders a "~N"
badge with a tooltip. Nothing breaks — you just see less detail for that
product.

Per-user-per-peer cache is 60 seconds; fetches run in parallel (8 workers
max).

### 3. Alerts

- **Local notifications** — anything from Helm's own `Notification` table
  (e.g. `task_due_soon`, `task_overdue`, `helm_project_assigned`).
- **Aggregated unread notifications** — unread counts surfaced by each
  peer's inbox payload.
- **Ops canaries** (staff only) — pulled live from `/api/v1/metrics/`.
  Surfaces audit silence, cron silence, cron failures, and notification
  delivery failures. See [Scheduled Jobs & Health Metrics](#scheduled-jobs--health-metrics).

---

## Dashboard — Across the suite tab

The executive view. Renders:

- **Period bar** — pick a window (current period, last period, custom).
- **Fleet metric grid** — one card per product, each carrying a primary
  KPI plus a sparkline. Click the **expand** button on any card to load a
  drill-down panel via htmx (`/dashboard/across/<product>/`) without
  leaving the page.
- **Fleet-aggregate action queue** — the same items that drive Awaiting
  Me, but rolled up across users for an org-wide view.
- **Fleet-aggregate alerts** — peer alerts plus ops canaries.
- **Watch list** — pinned products / metrics you've bookmarked.

### Drill-down panels

Each per-product drill-down shows:

- The product's full metric card (large numbers, trend, comparison).
- The product's open action items.
- The product's active alerts.
- The data freshness timestamp + a stale-data warning if the snapshot is
  older than the cache window.

Drill-downs are read-only. To act on an item, click through into the
peer product.

---

## My Work

`/tasks/` — the kanban-flavored personal queue of every task assigned to
or being collaborated on by you. Filters: status, priority, due date,
project. Inline status changes via the workflow engine (Start, Block,
Complete, Reopen).

This page and the dashboard's My Work column share the same database
predicate: `assignee=user OR collaborators__user=user`. They will never
disagree.

---

## Projects

`/tasks/projects/` — Helm's container for any work that has more than one
task and more than one person. Each project carries:

- **Kind** — Standard, FOIA, or CIP. (See the dedicated sections.)
- **Status** — Active / On hold / Completed / Cancelled / Archived.
- **Lead** — the principal driver. Set by the **Claim** action.
- **Collaborators** — people invited with explicit roles (LEAD,
  CONTRIBUTOR, REVIEWER, OBSERVER).
- **Tasks** — the work breakdown.
- **Notes** — internal-only by default (visibility controls available).
- **Attachments** — typed files (briefing, draft, signed PDF, evidence).
- **Status history** — every transition logged with actor + comment.
- **Public ID** — UUID powering the `/p/<uuid>/` transparency view when
  enabled.

### Lifecycle

Helm follows the **DockLabs Project Lifecycle Standard**:

> claim → invite collaborators → diligence (notes + attachments) →
> stage progression → optional handoff to Manifest for signing →
> signed-doc roundtrip → optional downstream export

Status transitions:

```
active ⇄ on_hold
active/on_hold → completed/cancelled
completed/cancelled → archived (terminal)
archived → active/completed/cancelled (unarchive restores prior state)
```

Pause and Cancel require a comment. Archive does not. Unarchive restores
the project to whatever terminal status it had before (recorded in
`previous_terminal_status`).

### Creating a project

1. **Projects → New project**.
2. Pick a **kind** (Standard, FOIA, or CIP). FOIA and CIP unlock
   kind-specific fields.
3. Set name, description, color, target end date.
4. Save.

After creation, click **Claim** to make yourself the LEAD, then **Invite
collaborators** to add the rest of the team.

---

## Tasks

Tasks live inside a project. Each carries:

- **Title** + optional description.
- **Status** — todo, in_progress, blocked, done.
- **Priority** — low, medium, high, urgent.
- **Assignee** — exactly one person.
- **Due date** — drives the My Work rail and overdue notifications.
- **Position** — manual sort order within the project.
- **Status history** — full audit trail.

### Workflow transitions

| From | To | Action | Required |
|---|---|---|---|
| todo | in_progress | Start | — |
| todo | done | Skip to done | — |
| in_progress | blocked | Block | comment |
| blocked | in_progress | Unblock | — |
| in_progress | done | Complete | — |
| done | in_progress | Reopen | — |

Anyone with access to the project can transition a task. Status changes
fire `task_status_changed` notifications to the task assignee and project
collaborators.

### Due-date notifications

A daily cron (`helm-notify-due-tasks`, 09:00 UTC) fires:

- `task_due_soon` — 24 hours before due_date.
- `task_overdue` — once when the task crosses due_date.

Both are idempotent (24-hour cooldown stamped on the task), so re-running
the job won't spam.

---

## Collaboration

### Roles (per project)

| Role | What they can do |
|---|---|
| **LEAD** | Drive the project. Transition status, archive/unarchive, manage collaborators, toggle public visibility, run AI summary, push to GovQA / Manifest. |
| **CONTRIBUTOR** | Create and update tasks, add notes, attach files, comment. |
| **REVIEWER** | Read everything; comment on notes and attachments. |
| **OBSERVER** | Read-only access. Useful for executive visibility without blast radius. |

Only LEAD / CONTRIBUTOR / REVIEWER / OBSERVER are valid. Custom role names
are not supported (suite-wide convention).

### Inviting

1. Open a project → **Collaborators**.
2. Pick a user (or enter an email for an external invitee) and a role.
3. The invitee gets a `helm_collaborator_invited` notification.

### Notes

`/tasks/projects/<slug>/notes/` — internal staff-only notes by default.
Mark a note "external" to expose it on the public transparency view if
the project is public.

### Attachments

`/tasks/projects/<slug>/attachments/` — drag-and-drop file uploads. Each
attachment carries a **source** (manual upload, manifest_signed, etc.)
and a **visibility** (internal vs external). Files attached as
`manifest_signed` are produced automatically by the Manifest signing
roundtrip.

---

## FOIA Projects

A project with **kind = FOIA** unlocks statutory-clock fields and a
tolling control bar.

### Statutory clock

| Field | Meaning |
|---|---|
| **Jurisdiction** | Connecticut (CGS §1-206 — 4 business days to acknowledge) is the default. Federal (5 USC 552 — 20 business days) is also supported. |
| **Received at** | The date the request was received. Triggers the clock. |
| **Statutory deadline** | Computed deadline. Recomputes when received_at, jurisdiction, or tolling changes. Indexed; visible as a countdown badge on the project card and the Awaiting Me column. |
| **Tolled at / Tolled until** | Pause window. The deadline shifts forward by the tolled duration. |

### Tolling

LEAD users can pause and resume the clock:

- **Toll** — `/tasks/projects/<slug>/foia/toll/` — record the start of a
  tolling period (e.g. waiting on a fee agreement, requester
  clarification). Surface label and end date on submission.
- **Untoll** — `/tasks/projects/<slug>/foia/untoll/` — resume the clock.
  The statutory deadline recomputes, advancing forward by the tolled
  duration.

Every toll/untoll is logged in status history.

### Granicus GovQA push

If `GRANICUS_GOVQA_URL` and `GRANICUS_GOVQA_API_KEY` are set, LEAD users
see a **Push to GovQA** button that creates / updates the matching record
in Granicus GovQA. The button is hidden when the integration isn't
configured — there are no silent no-ops.

---

## Capital Improvement Plan (CIP) Projects

A project with **kind = CIP** unlocks:

### Fund sources

A structured list of `{source, amount_cents, label}` entries. Sources:

- ARPA — American Rescue Plan Act
- IIJA — Infrastructure Investment and Jobs Act
- IRA — Inflation Reduction Act
- BEAD — Broadband Equity, Access, and Deployment
- SLCGP — State and Local Cybersecurity Grant
- CDBG — Community Development Block Grant
- General Obligation Bond / Revenue Bond
- State Match / Local Match / General Fund

Totals per project are visible on the public transparency page when
public visibility is enabled.

### Federal compliance flags

Surface on the project for audit-ready posture:

- **Davis-Bacon** — federal construction prevailing wage requirements apply.
- **Build America, Buy America (BABA)** — domestic procurement applies.
- **NEPA** — federal environmental review required.
- **Environmental review** — state or local environmental review required.

These are advisory flags driving the project header and downstream FOIA
exportability — they don't enforce blocking rules. Treat them as
documentation a future reviewer (or auditor) can rely on.

---

## Public Transparency

Each project has a `public_id` (UUID) and a `public_visibility` flag
(default **private**). LEAD-only toggle at
`/tasks/projects/<slug>/visibility/`.

When **public**, the project is reachable at `/p/<public_id>/` to anyone,
no authentication required, and renders:

- Project name, status, target dates.
- Task completion percentage.
- Fund sources and totals (CIP only).
- The FOIA statutory deadline countdown (FOIA only).

It does **not** expose: notes, attachments, collaborators, comments, or
any PII. The public view is a transparency primitive, not a sharing
primitive.

---

## Calendar

`/tasks/calendar/` — FullCalendar-mounted view of:

- Project target end dates.
- Task due dates.
- FOIA statutory deadlines.

### iCal subscribe

`/tasks/calendar.ics` — login-required iCal feed of the same items, so
you can subscribe from Outlook / Apple Calendar / Google Calendar. The
URL is per-user; deadlines on projects you can't see won't appear.

---

## Notifications

Helm's notification catalog (event types you may receive):

| Event | When it fires |
|---|---|
| `helm_project_assigned` | You were claimed as LEAD on a project. |
| `helm_project_status_changed` | A project you're on transitioned. |
| `helm_collaborator_invited` | You were added to a project. |
| `helm_task_assigned` | A task in your project was assigned to you. |
| `helm_task_status_changed` | A task you're on changed status. |
| `task_due_soon` | A task you own is due in 24 hours. |
| `task_overdue` | A task you own is past due. |
| `helm_foia_deadline_warning` | A FOIA project you're on is approaching its statutory deadline. |

Channels (in-app + email) are user-configurable at
`/notifications/preferences/`. The link is in the sidebar user-menu
dropdown.

---

## Exports

### Per project

| Format | URL | Notes |
|---|---|---|
| **CSV** | `/tasks/projects/<slug>/export.csv` | Task list with status, priority, assignee, dates. |
| **PDF** | `/tasks/projects/<slug>/export.pdf` | Status report — branded, suitable for stakeholder distribution. |

### AI summary

`/tasks/projects/<slug>/summarize/` — LEAD or CONTRIBUTOR runs a
Claude-powered narrative summary of the project's status, recent
activity, and upcoming deadlines. GET returns the cached summary; POST
forces a refresh. Summaries are stamped with the model version and the
generation timestamp.

### Project Online (PWA) import

Staff-only wizard at `/tasks/import/project-online/` for ingesting
projects from a Microsoft Project Online instance into Helm. Maps PWA
fields to Helm projects + tasks; preserves status and assignees where
matchable.

---

## Scheduled Jobs & Health Metrics

### Scheduled jobs dashboard

Staff-only at `/scheduling/`. One row per scheduled command in this
service, with the cron expression (display only — the scheduler is
external), last run, status, recent-24-runs sparkline, and a per-job
detail page showing the last 100 runs.

Helm's registered jobs:

- **`helm-notify-due-tasks`** (daily, 09:00 UTC) — fires
  `task_due_soon` and `task_overdue`. Idempotent.

The `enabled` flag is **display-only** — toggling it in the dashboard
does NOT pause the underlying cron. To actually pause a job, change the
external scheduler.

### Operational metrics

`GET /api/v1/metrics/` returns JSON counters. Auth: staff session OR
`Authorization: Bearer $HELM_METRICS_TOKEN` (for external pollers).

The four canary booleans under `flags`:

| Flag | What flipping `true` means |
|---|---|
| `audit_silent_24h` | No `AuditLog` rows written in 24 hours. The compliance trail has gone silent. |
| `cron_silent_24h` | No `CommandRun` rows in 24 hours — the scheduler isn't firing. |
| `cron_failures_24h` | At least one scheduled job errored in the last 24 hours. |
| `notifications_failing` | At least one delivery failed. |

`flags.healthy: false` is the simplest top-level alert. Surface in your
external monitoring (cron-job.org, BetterUptime, Pingdom) as
"non-200 status OR response body NOT containing `"healthy":true`".

---

## Status Reference

### Project status

| Status | Meaning |
|---|---|
| **Active** | Open, work in progress. |
| **On hold** | Paused. Comment required to enter; resume returns to Active. |
| **Completed** | Successfully wrapped. |
| **Cancelled** | Closed without completion. Comment required. |
| **Archived** | Terminal. Hidden from the default project list. Restorable to its prior terminal status. |

### Task status

| Status | Meaning |
|---|---|
| **To Do** | Not started. |
| **In Progress** | Actively being worked. |
| **Blocked** | Stalled on an external dependency. Comment required to enter. |
| **Done** | Complete. Sets `completed_at`. |

### Collaborator role

| Role | Read | Comment | Edit tasks | Manage |
|---|---|---|---|---|
| **LEAD** | ✓ | ✓ | ✓ | ✓ |
| **CONTRIBUTOR** | ✓ | ✓ | ✓ | — |
| **REVIEWER** | ✓ | ✓ | — | — |
| **OBSERVER** | ✓ | — | — | — |

### FOIA jurisdiction

| Jurisdiction | Statute | Initial window |
|---|---|---|
| **Connecticut** | CGS §1-206 | 4 business days to acknowledge |
| **Federal** | 5 USC 552 | 20 business days |

### CIP fund source

ARPA, IIJA, IRA, BEAD, SLCGP, CDBG, GO Bond, Revenue Bond, State Match,
Local Match, General Fund.

---

## Keyboard Shortcuts

| Key | Action |
|---|---|
| **⌘K** / **Ctrl+K** | Open the suite-wide search modal. |

---

## Support

- **Email** — info@docklabs.ai (1–2 business day response).
- **Feedback widget** — bottom-right corner of every page; routes to the
  shared support queue.
- **Per-product help** — for questions specific to Harbor, Admiralty,
  Beacon, etc., open the help link inside that product.

---

*Last updated: 2026-04-30.*
