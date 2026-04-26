"""Helm API views."""
from collections import defaultdict

from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from dashboard.services import FeedAggregator, get_user_product_keys
from .serializers import BriefingSerializer


def _fund_source_rollup(user):
    """ADD-6 — aggregate fund_sources across visible CIP projects, joined
    against Harbor's per-fund-source drawdown/payment activity.

    Returns a list of dicts, one per fund source code, with:
        committed_cents (Helm) — sum of CIP project fund_sources.amount_cents
        obligated_cents (Harbor) — sum of active Award.award_amount * 100
        drawn_cents (Harbor) — sum of paid DrawdownRequest.amount * 100
        paid_cents (Harbor) — sum of PAYMENT Transaction.amount * 100
        remaining_cents — committed - drawn (signed; can be negative if
                          Harbor reports more drawn than Helm has committed)
        project_count, projects[]
        harbor_unavailable: True when the join couldn't read Harbor data

    The join is best-effort: when Harbor's snapshot is missing or stale,
    the Helm-only fields are still returned and `harbor_unavailable` is
    set so the UI can disclose the gap.
    """
    # Lazy import to avoid the API app importing tasks at module load.
    try:
        from tasks.models import Project
    except ImportError:
        return []
    visible = Project.objects.visible_to(user).active().filter(
        kind=Project.Kind.CIP,
    )
    rollup = defaultdict(lambda: {
        'committed_cents': 0,
        'obligated_cents': 0,
        'drawn_cents': 0,
        'paid_cents': 0,
        'project_count': 0,
        'projects': [],
    })
    for project in visible:
        for fs in (project.fund_sources or []):
            source = fs.get('source')
            if not source:
                continue
            amount = int(fs.get('amount_cents') or 0)
            entry = rollup[source]
            entry['committed_cents'] += amount
            entry['project_count'] += 1
            entry['projects'].append({
                'slug': project.slug,
                'name': project.name,
                'amount_cents': amount,
            })

    # Harbor join — read fund_source_breakdown from the cached snapshot.
    harbor_breakdown, harbor_unavailable = _harbor_fund_breakdown()
    for source, harbor in harbor_breakdown.items():
        entry = rollup[source]
        entry['obligated_cents'] += int(harbor.get('award_value_cents') or 0)
        entry['drawn_cents'] += int(harbor.get('drawn_cents') or 0)
        entry['paid_cents'] += int(harbor.get('paid_cents') or 0)

    out = []
    for source, data in sorted(rollup.items()):
        out.append({
            'source': source,
            'committed_cents': data['committed_cents'],
            'obligated_cents': data['obligated_cents'],
            'drawn_cents': data['drawn_cents'],
            'paid_cents': data['paid_cents'],
            'remaining_cents': data['committed_cents'] - data['drawn_cents'],
            'project_count': data['project_count'],
            'projects': data['projects'],
            'harbor_unavailable': harbor_unavailable,
        })
    return out


def _harbor_fund_breakdown():
    """Return (breakdown_dict, harbor_unavailable_bool) from Harbor's snapshot.

    Reads `fund_source_breakdown` out of the most recent CachedFeedSnapshot
    for product='harbor'. Returns ({}, True) when Harbor data isn't
    available (no snapshot, snapshot stale, or older Harbor not yet
    emitting the new key).
    """
    try:
        from dashboard.models import CachedFeedSnapshot
        snap = CachedFeedSnapshot.objects.filter(product='harbor').first()
    except Exception:
        return {}, True
    if snap is None:
        return {}, True
    feed = snap.feed_data or {}
    breakdown = feed.get('fund_source_breakdown')
    if not isinstance(breakdown, dict) or not breakdown:
        # Harbor hasn't been redeployed with the dimension yet — degrade.
        return {}, True
    return breakdown, bool(getattr(snap, 'is_stale', False))


class BriefingAPIView(APIView):
    """DISPATCH integration endpoint — morning briefing data.

    GET /api/v1/briefing/
    Returns a structured summary optimized for Telegram delivery.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        agg = FeedAggregator(product_keys=get_user_product_keys(request.user))
        data = agg.get_briefing_data(request.user)

        data['briefing_date'] = timezone.now().date().isoformat()

        # Try to get current fiscal period context
        try:
            from keel.periods.models import FiscalPeriod
            current = FiscalPeriod.objects.filter(
                fiscal_year__is_current=True,
            ).order_by('-month').first()
            if current:
                data['fiscal_context'] = (
                    f'{current.fiscal_year.name} \u00b7 '
                    f'{current.label} \u00b7 '
                    f'{current.get_status_display()}'
                )
            else:
                data['fiscal_context'] = ''
        except Exception:
            data['fiscal_context'] = ''

        # ADD-6 — opt-in fund-source rollup for executive briefings.
        # Only included when the caller passes ?include=fund_sources to keep
        # the default briefing payload light.
        include = (request.GET.get('include') or '').split(',')
        if 'fund_sources' in include:
            data['fund_sources'] = _fund_source_rollup(request.user)

        serializer = BriefingSerializer(data)
        return Response(serializer.data)


class DashboardDataAPIView(APIView):
    """JSON endpoint for dashboard widget data.

    GET /api/v1/dashboard/
    Returns all metrics, action items, and alerts.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        agg = FeedAggregator(product_keys=get_user_product_keys(request.user))
        return Response({
            'metrics_by_product': agg.get_metrics_by_product(),
            'action_items': agg.get_all_action_items(),
            'alerts': agg.get_all_alerts(),
            'fleet_health': agg.get_fleet_health(),
        })
