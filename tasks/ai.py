"""ADD-4 — AI project summary.

Generates a 3-paragraph executive status report for a project on demand
using Claude Sonnet 4.6 via ``keel.core.ai.call_claude``. Output is
Django-cached keyed on ``public_id`` + ``updated_at`` so any project
mutation invalidates and re-generates next time the user clicks
"Summarize". TTL 1h.

The prompt assembles structured context (status / kind / FOIA clock /
recent notes / status history / open tasks) so the summary can be
genuinely useful — not "this is project X, it has tasks." A typical
output is "Project on track for July target. ARPA drawdown at 64%.
Three tasks blocked on permit review. FOIA #2026-0421 due in 18 days."

Cost protection:
- Output cache (1h TTL) means a second click within an hour is free.
- Cache key is invalidated on any Project.save(), so stale summaries
  don't persist past a real change.
- max_tokens=600 caps each call at ~$0.005-0.01 on Sonnet 4.6.

Failure modes:
- No API key → returns an honest "AI summary unavailable (no API key
  configured)." string. Doesn't raise.
- Claude API error → keel.core.ai.call_claude returns None; we surface
  "AI summary failed; please try again." Doesn't raise.

Permissions: enforced at the view layer (see tasks/access.can_summarize).
"""
from __future__ import annotations

from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

from keel.core.ai import call_claude, get_client


CACHE_TTL_SECONDS = 60 * 60  # 1h


_SYSTEM_PROMPT = """You are an executive briefing assistant for a state government
project management platform. You write concise, factual status reports for
chief information officers, agency directors, and elected officials.

Output exactly three short paragraphs:
1. PROGRESS — current status, what's been completed, percentage if useful.
2. BLOCKERS — what's stuck or at risk, with specific reasons. If nothing
   is blocked, say "No active blockers" and explain why progress looks healthy.
3. NEXT STEPS — the most important 1-3 things to do, framed as concrete
   actions.

Do not editorialize. Do not pad. Do not use marketing language. Do not
say "the team" or "our project". Do not include a heading or label per
paragraph — just the prose. Aim for 120-180 words total.

When statutory FOIA deadlines are present, lead with their urgency in
PROGRESS or BLOCKERS as appropriate. When fund sources are listed, name
them by program (ARPA, IIJA, IRA, etc.) — these names matter to the
reader."""


def _build_user_message(project) -> str:
    """Assemble the structured project context for the prompt."""
    lines = [
        f'PROJECT: {project.name}',
        f'STATUS: {project.get_status_display()}',
        f'KIND: {project.get_kind_display()}',
    ]
    if project.description:
        lines.append(f'DESCRIPTION: {project.description}')
    if project.started_at:
        lines.append(f'STARTED: {project.started_at.isoformat()}')
    if project.target_end_at:
        lines.append(f'TARGET END: {project.target_end_at.isoformat()}')
    if project.completed_at:
        lines.append(f'COMPLETED: {project.completed_at.date().isoformat()}')

    # FOIA statutory clock.
    if project.kind == project.Kind.FOIA and project.foia_statutory_deadline_at:
        from tasks.foia import days_remaining, urgency_tier
        days = days_remaining(project)
        tier = urgency_tier(project)
        lines.append(
            f'FOIA STATUTORY DEADLINE: {project.foia_statutory_deadline_at.isoformat()} '
            f'({days} business days remaining, tier={tier})'
        )
        if project.foia_metadata.get('foia_request_id'):
            lines.append(f'FOIA REQUEST ID: {project.foia_metadata["foia_request_id"]}')
        if project.foia_metadata.get('foia_agency'):
            lines.append(f'FOIA AGENCY: {project.foia_metadata["foia_agency"]}')

    # Task counts by status.
    task_counts = (project.tasks
                          .values_list('status', flat=True))
    counts = {}
    for s in task_counts:
        counts[s] = counts.get(s, 0) + 1
    if counts:
        line = 'TASK COUNTS: ' + ', '.join(
            f'{counts[s]} {s}' for s in sorted(counts.keys())
        )
        lines.append(line)
        total = sum(counts.values())
        done = counts.get('done', 0)
        if total:
            pct = int(round(100 * done / total))
            lines.append(f'COMPLETION: {pct}% ({done}/{total} tasks done)')

    # Top 10 open tasks.
    open_tasks = (project.tasks
                         .exclude(status='done')
                         .order_by('priority', 'due_date')[:10])
    if open_tasks:
        lines.append('OPEN TASKS:')
        for t in open_tasks:
            due = f' due {t.due_date.isoformat()}' if t.due_date else ''
            lines.append(f'  - [{t.priority}] {t.title} ({t.status}{due})')

    # Recent notes (last 5).
    recent_notes = project.notes.order_by('-created_at')[:5]
    notes_list = list(recent_notes)
    if notes_list:
        lines.append('RECENT NOTES:')
        for n in notes_list:
            stamp = n.created_at.date().isoformat()
            lines.append(f'  - {stamp}: {n.content[:200]}')

    # Status history (last 5).
    history = (project.status_history
                      .order_by('-changed_at')[:5])
    history_list = list(history)
    if history_list:
        lines.append('RECENT STATUS TRANSITIONS:')
        for h in history_list:
            stamp = h.changed_at.date().isoformat()
            lines.append(f'  - {stamp}: {h.old_status} → {h.new_status}')

    return '\n'.join(lines)


def _cache_key(project) -> str:
    # updated_at iso string (with microseconds) so any save invalidates.
    # Integer-second precision collapses sub-second saves and lets stale
    # summaries leak through tests + rapid-fire UI edits.
    ts = project.updated_at.isoformat() if project.updated_at else '0'
    return f'helm:project:{project.public_id}:summary:{ts}'


def summarize_project(project, *, force_refresh: bool = False) -> str:
    """Return a 3-paragraph AI-generated status summary for the project.

    Cached for 1h keyed on (public_id, updated_at). ``force_refresh=True``
    bypasses the cache and writes a fresh value. Never raises.
    """
    key = _cache_key(project)
    if not force_refresh:
        cached = cache.get(key)
        if cached is not None:
            return cached

    client = get_client()
    if client is None:
        msg = (
            'AI summary unavailable (no Anthropic API key configured). '
            'Set ANTHROPIC_API_KEY in settings to enable.'
        )
        return msg

    user_message = _build_user_message(project)
    response = call_claude(
        client,
        system=_SYSTEM_PROMPT,
        user_message=user_message,
    )
    if response is None:
        return 'AI summary failed — please try again.'

    cache.set(key, response, timeout=CACHE_TTL_SECONDS)
    return response
