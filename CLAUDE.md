@../keel/CLAUDE.md

# Helm — operations runbook

Helm-specific guidance on top of the keel-wide CLAUDE.md above.

## Project Management surface

The `tasks/` app implements the DockLabs Project Lifecycle Standard. URLs are gated by `HELM_TASKS_ENABLED` (env var); production deployments opt in.

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

The reference implementation lives in Manifest at `signatures/helm_inbox.py`. The decorator `helm_inbox_view` will be promoted to `keel.feed.views` when peer #2 (Harbor) adopts; for now it's local to Manifest to avoid premature abstraction.

Helm's `InboxAggregator` degrades gracefully when a peer hasn't implemented the endpoint: it falls back to the aggregate `ActionItem` count from `CachedFeedSnapshot` and renders a "~N" badge with a "this product hasn't enabled per-user inbox" tooltip. So peer rollout can be incremental.

## Cron jobs

Schedules run externally (Railway cron service / GitHub Actions / cron-job.org). `keel.scheduling` provides observability — every `@scheduled_job`-decorated command writes a `CommandRun` row visible at `/scheduling/`. The `enabled` flag on the dashboard is display-only — toggling it does NOT pause the cron itself.

Helm's registered jobs:

| Slug | Schedule | Command | Notes |
|---|---|---|---|
| `helm-notify-due-tasks` | `0 9 * * *` UTC | `python manage.py notify_due_tasks` | Idempotent via `Task.last_due_soon_notif_at` / `last_overdue_notif_at` (24h cooldown). |

`startup.py` runs `sync_scheduled_jobs` on every deploy, so the dashboard stays in step with code declarations.

## Operational metrics

`GET /api/v1/metrics/` (staff-only) returns JSON counters useful for monitoring:

```bash
curl -b "sessionid=..." https://helm.docklabs.ai/api/v1/metrics/ | jq .flags
# {"audit_silent_24h": false, "cron_silent_24h": false, "cron_failures_24h": false, "notifications_failing": false}
```

Wire this into BetterUptime / Pingdom / Boswell daily check. The four `flags.*` booleans are the canaries:

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

- [ ] Run `python manage.py test tasks` locally — all tests pass
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
