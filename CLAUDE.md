@../keel/CLAUDE.md

# Helm — operations runbook

Helm-specific guidance on top of the keel-wide CLAUDE.md above.

## Project Management surface

The `tasks/` app implements the DockLabs Project Lifecycle Standard. URLs are gated by `HELM_TASKS_ENABLED` (env var); production deployments opt in.

| Surface | URL |
|---|---|
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
