@../keel/CLAUDE.md

# Helm — operations runbook

Helm-specific guidance on top of the keel-wide CLAUDE.md above.

## Work Management surface

The `tasks/` app is Helm's user-facing work-management surface, implementing the DockLabs Project Lifecycle Standard (the architectural standard name in keel does not change). The whole app is gated by `HELM_TASKS_ENABLED` — `helm_site/settings.py` only appends `tasks.apps.TasksConfig` to `INSTALLED_APPS` when the env var is truthy, so without it the app's models, URLs, admin, and migrations are all absent. Production deployments opt in. **Tests that import `tasks.models` at module level will fail at loader time without the env var set** — see the pre-deploy checklist below.

| Surface | URL |
|---|---|
| Dashboard — Today tab (personal inbox) | `/dashboard/` (default) or `/dashboard/?tab=today` |
| Dashboard — Across the suite tab | `/dashboard/?tab=suite` |
| Per-product drill-down (htmx panel) | `/dashboard/across/<product>/` |
| Dashboard partials (htmx) | `/dashboard/partials/{deadline-rail,inbox-column,alerts-column,action-queue,alerts,metrics,card/<product>}/` |
| Project list | `/tasks/projects/` |
| Project detail | `/tasks/projects/<slug>/` |
| Project collaborators / notes / attachments | `/tasks/projects/<slug>/{collaborators,notes,attachments}/` |
| Archive list | `/tasks/projects/archived/` |
| Calendar | `/tasks/calendar/` |
| iCal subscribe (login required) | `/tasks/calendar.ics` |
| CSV export per project | `/tasks/projects/<slug>/export.csv` |
| PDF export per project | `/tasks/projects/<slug>/export.pdf` |
| Notification preferences | `/notifications/preferences/` (keel-shipped) |
| Scheduled jobs dashboard | `/scheduling/` (keel-shipped) |

## Dashboard architecture

`/dashboard/` is split into two tabs.

**Tab 1 — "Today" (personal inbox).** Three columns:

1. **My Work** — deadline rail of the user's open tasks grouped Overdue / Today / This week / Upcoming. Source: `tasks.queries.get_user_deadline_rail()`. Reuses the same `Q(assignee=user) | Q(collaborators__user=user)` predicate as `/tasks/my_tasks/` so the two surfaces never drift.
2. **Awaiting Me** — cross-suite inbox. For each peer in `FLEET_PRODUCTS`, calls `/api/v1/helm-feed/inbox/?user_sub=<oidc_sub>` and groups items by product. Source: `dashboard.inbox.InboxAggregator`. Per-user-per-peer cache (60s TTL), parallel fetch (8 workers max), graceful fallback to the cached aggregate `ActionItem` count when a peer's inbox endpoint isn't yet implemented.
3. **Alerts** — helm-local `Notification` rows + aggregated `unread_notifications` from peers' inbox payloads + (staff-only) ops canaries from `/api/v1/metrics/`.

**Tab 2 — "Across the suite" (situational awareness).** The original aggregate dashboard: period bar, fleet metric grid with sparklines, fleet-aggregate action queue, fleet-aggregate alerts, watch list. Each metric card has an expand button that loads `/dashboard/across/<product>/` into a drill-down panel via htmx.

The active tab is controlled by the `?tab=today|suite` query string (server-rendered) and Bootstrap nav-tabs (client-side switching). Tab clicks `history.replaceState()` so reloads land on the same tab.

### Per-user inbox feed contract

The new `/api/v1/helm-feed/inbox/` endpoint each peer is expected to expose is the per-user companion to the existing aggregate `/api/v1/helm-feed/`. Returns "items where this user is the gating dependency" + that user's unread notifications, in the shape defined by `dashboard.feed_contract.UserInbox`.

- Auth: `Authorization: Bearer $HELM_FEED_API_KEY` (mirrors aggregate endpoint).
- Required query param: `?user_sub=<oidc_sub>` — resolved against `SocialAccount(provider='keel', uid=sub)` on the peer side.
- Cache: per-user-per-path (NEVER cache by path alone — that would serve user A's payload to user B).
- Unknown sub → 200 with empty `items[]` (so the aggregator renders cleanly), not 404.

The decorator lives in `keel.feed.views.helm_inbox_view` (since keel 0.18.0). Peers wrap a `build_inbox(request, user) -> dict` function with it.

**Wired peers (2026-04-26 — 8/8 complete):**

| Peer | File | Predicate |
|---|---|---|
| Manifest | `signatures/helm_inbox.py` | `SigningStep.signer=user, status=ACTIVE, packet.status=IN_PROGRESS` |
| Harbor | `api/helm_inbox.py` | `ReviewAssignment.reviewer=user` (open) + `ApplicationAssignment.assigned_to=user` (open) |
| Admiralty | `foia/helm_inbox.py` | `FOIARequest.assigned_to=user, status in {received,scope_defined,searching,under_review,package_ready}` — carries `statutory_deadline` as `due_date` |
| Purser | `purser/helm_inbox.py` | `Submission.status in {submitted,under_review}, program.reviewers=user, (reviewed_by IS NULL OR reviewed_by=user)` |
| Bounty | `api/helm_inbox.py` | `OpportunityMatch.user=user, status=NEW, relevance_score>=GRANT_MATCH_HIGH_SCORE` |
| Beacon | `api/helm_inbox.py` | `KeepInTouch.user=user, is_active=True, next_reminder_date<=today+3d` + `Company.relationship_owner=user, approval_status=pending` |
| Lookout | `api/helm_inbox.py` | `TrackedBill.tracked_by=user, status in {researching,collaborating,drafting_testimony}, archived_at IS NULL` |
| Yeoman | `yeoman/helm_inbox.py` | `Invitation.(assigned_to OR principal OR delegated_to)=user, status in {received,in_review,accepted}` |

The graceful-fallback path in `InboxAggregator` (aggregate `ActionItem` count + "~N" badge) remains the safety net for any peer that ever drops the endpoint.

## Cron jobs

Schedules are fired by [`.github/workflows/cron.yml`](.github/workflows/cron.yml), which uses the Railway CLI (auth via the `RAILWAY_TOKEN` repo secret — a project token scoped to helm/production) to SSH into the running web container and exec `python manage.py <cmd>`. `keel.scheduling` provides observability — every `@scheduled_job`-decorated command writes a `CommandRun` row visible at `/scheduling/`. The `enabled` flag on the dashboard is display-only — toggling it does NOT pause the GitHub Actions schedule.

Helm's registered jobs:

| Slug | Schedule | Command | Notes |
|---|---|---|---|
| `helm-fetch-feeds` | `*/15 * * * *` UTC | `python manage.py fetch_feeds` | Pulls each peer's `/api/v1/helm-feed/` into `CachedFeedSnapshot`. Parallel + circuit-breaker built in. |
| `helm-notify-due-tasks` | `0 9 * * *` UTC | `python manage.py notify_due_tasks` | Idempotent via `Task.last_due_soon_notif_at` / `last_overdue_notif_at` (24h cooldown). |

`startup.py` runs `sync_scheduled_jobs` on every deploy, so the dashboard stays in step with code declarations.

## Operational metrics

`GET /api/v1/metrics/` returns JSON counters useful for monitoring. Powered by `keel.ops` (see `keel/CLAUDE.md` → "Ops canary"); helm's [`api/metrics.py`](api/metrics.py) is a thin wrapper that adds product-specific gauges (project lifecycle, task buckets, FOIA queue depth) via the `extras_callable` hook. Auth: either staff session (browser) **or** `Authorization: Bearer $HELM_METRICS_TOKEN` for external pollers.

```bash
curl -H "Authorization: Bearer $HELM_METRICS_TOKEN" \
  https://helm.docklabs.ai/api/v1/metrics/ | jq .flags
# {"audit_silent_24h": false, "cron_silent_24h": false, "cron_failures_24h": false, "notifications_failing": false}
```

**Polling is wired via GitHub Actions** at [`.github/workflows/canary.yml`](.github/workflows/canary.yml) — runs every 15min, fails the workflow on non-200 or `healthy != true`, and opens (or de-dupes) a `canary`-labeled GitHub issue on failure. Auth uses the `HELM_METRICS_TOKEN` repo secret. We chose GitHub Actions over cron-job.org / BetterUptime / Pingdom because the schedule, the alert (an issue in this repo), and the secret all live in one place we already operate. This is the alert path that would have caught the 2026-04-26 silent-cron incident — without it, `flags.cron_silent_24h` flipping true is invisible. The four `flags.*` booleans are the canaries:

- `audit_silent_24h` — true means no `AuditLog` rows written in 24h. **This is the canary that would have caught the 4-week silent-audit bug on day 1.** See `incidents/2026-04-25-audit-gap.md`.
- `cron_silent_24h` — true means no `CommandRun` rows in 24h (cron isn't firing).
- `cron_failures_24h` — true means at least one scheduled job errored.
- `notifications_failing` — true means at least one delivery failed.

`flags.healthy: false` is the simplest top-level alert.

## Common operational tasks

### Re-run a scheduled job manually
```bash
railway ssh --service helm -- python manage.py notify_due_tasks
```
The `@scheduled_job` wrapper records the run in `/scheduling/<slug>/`.

### Resync the scheduled-job registry without redeploying
```bash
railway ssh --service helm -- python manage.py sync_scheduled_jobs
```
Idempotent. Preserves admin-edited `enabled` + `notes`.

### Manually unarchive a project
Either: visit `/tasks/projects/archived/`, click the project, click the unarchive button; OR via shell:
```python
from tasks.models import Project
from tasks.services import unarchive_project
p = Project.objects.get(slug='archived-thing')
unarchive_project(project=p, user=request.user)
```

### Audit a specific project's history
Django admin → Helm Tasks → Project Status History → filter by project.
Or via `/scheduling/` if the question is about cron runs.

### Reissue a stuck notification
Direct `notify()` call from shell:
```python
from keel.notifications.dispatch import notify
from tasks.models import Project
p = Project.objects.get(slug='...')
notify(event='helm_project_assigned', actor=None, target=p)
```
Or `force=True` to bypass user preferences for a critical alert.

## Demo seed

`python manage.py seed_demo_projects` — only runs under `DEMO_MODE=true` (refused in prod). Creates 4 projects exercising every workflow state. Idempotent: bails if any of the four slugs already exist.

`startup.py` invokes this automatically on demo deploys after migrations + feed fetch.

## Pre-deploy checklist

- [ ] Run `HELM_TASKS_ENABLED=true python manage.py test` locally — all tests pass. The env var is required: without it the `tasks` app is not in `INSTALLED_APPS` and 18 of the test modules under `tasks/tests/` fail at loader time with `Model class tasks.models.Project doesn't declare an explicit app_label`. CI sets it explicitly in `.github/workflows/ci.yml`.
- [ ] `python manage.py check` clean
- [ ] Migrations apply forward AND revert: `migrate helm_tasks 0002 && migrate helm_tasks`
- [ ] Latest commit pushed to main; CI green
- [ ] If keel pin changed, version bump matches PEP 440 (pip cache trap)

## Post-deploy smoke checklist

- [ ] `/health/` returns 200
- [ ] `/dashboard/` loads for an authenticated user
- [ ] `/tasks/projects/` loads (200) — project list visible
- [ ] `/tasks/projects/<existing-slug>/` loads (200)
- [ ] `/tasks/calendar/` loads (200) — FullCalendar mounts
- [ ] `/scheduling/` loads (200) — `helm-notify-due-tasks` row present
- [ ] `/api/v1/metrics/` returns `flags.healthy: true`
- [ ] Optional: hit `/tasks/projects/<slug>/export.csv` and confirm a CSV downloads
- [ ] Optional: hit `/tasks/projects/<slug>/export.pdf` and confirm a PDF downloads
- [ ] `core.AuditLog` row count went up after the deploy completed (a real action)

## Rollback

The plan migrated `Project.archived` boolean → `status='archived' + archived_at` in a single deploy (Phase 2). To roll back:

1. **Forward-fix preferred.** A bad model change is almost always cheaper to fix forward (adding a hotfix migration) than to revert.
2. If migration revert is unavoidable: `migrate helm_tasks 0002` reverts the satellite tables + adds `archived` boolean back. Loses `status` / `archived_at` / `public_id` data on existing projects; manual recovery needed from `ProjectStatusHistory` rows where they exist.
3. Code revert: `git revert <bad-commit>` + redeploy. Keel pin can be reverted in `requirements.txt` if a keel release introduced the issue.

## Incidents

| Date | Severity | Summary | Doc |
|---|---|---|---|
| 2026-03-26 → 2026-04-25 | S2 | Helm Tasks audit log gap (silent compliance) | [`incidents/2026-04-25-audit-gap.md`](incidents/2026-04-25-audit-gap.md) |

## Design Partner & Product Context

**Dan O'Keefe is the design partner.** He is the Commissioner of Economic Development for the State of Connecticut and leads the Department of Economic and Community Development (DECD). He built the DockLabs suite to solve real problems he encounters in state government. When gstack skills reference "find a design partner" or "get user feedback," Dan is that person — he's not a hypothetical future customer, he's the builder AND the user.

All product decisions should be evaluated through the lens of: "Does this solve a real problem for a state agency commissioner managing economic development, grants, FOIA, CIP, and legislative affairs?"
