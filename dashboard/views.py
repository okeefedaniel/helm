"""Helm dashboard views."""
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.utils import timezone
from django.views.generic import TemplateView

from .inbox import InboxAggregator
from .models import CachedFeedSnapshot, DashboardBookmark
from .services import FeedAggregator, PRODUCT_META, get_user_product_keys


def _current_period_context():
    """Build the header period label (fiscal year + current month).

    CT's fiscal year runs July–June, so FY2026 covers July 2025 through
    June 2026. The label follows the calendar month the dashboard is
    viewed in — no hardcoded month strings.
    """
    now = timezone.localtime()
    fiscal_year = now.year + 1 if now.month >= 7 else now.year
    return {
        'period_fiscal_year': f'FY{fiscal_year}',
        'period_month': now.strftime('%B'),
        'period_status': 'Under Review',
    }


def _aggregator_for(user):
    """Return a FeedAggregator filtered to the user's product access."""
    return FeedAggregator(product_keys=get_user_product_keys(user))


def _today_tab_context(request) -> dict:
    """Build the data for tab 1 ("Today") — deadline rail, inbox, alerts.

    Tasks integration is gated on HELM_TASKS_ENABLED so installs without
    the tasks app don't import-error.
    """
    user = request.user
    ctx: dict = {}

    # Deadline rail (column 1) — only when the tasks app is installed.
    if getattr(settings, 'HELM_TASKS_ENABLED', False):
        from tasks.queries import (
            get_user_open_task_count, get_user_project_deadline_rail,
            get_user_undated_count,
        )
        ctx['deadline_rail'] = get_user_project_deadline_rail(user)
        ctx['my_open_task_count'] = get_user_open_task_count(user)
        ctx['my_undated_task_count'] = get_user_undated_count(user)
    else:
        ctx['deadline_rail'] = {
            'overdue': [], 'today': [], 'this_week': [], 'upcoming': [],
        }
        ctx['my_open_task_count'] = 0
        ctx['my_undated_task_count'] = 0

    # Cross-suite inbox (column 2)
    inbox_agg = InboxAggregator(user, request)
    ctx['inbox_per_product'] = inbox_agg.get_per_product()
    ctx['inbox_total_count'] = inbox_agg.get_total_item_count()
    ctx['inbox_user_sub'] = inbox_agg.user_sub

    # Alerts column (column 3) — peer notifications only here; helm-local
    # notifications are added by the dashboard view itself so we don't
    # double-count.
    ctx['peer_notifications'] = inbox_agg.get_aggregated_unread_notifications()

    # Helm-local unread notifications.
    try:
        from core.models import Notification
        ctx['helm_unread_notifications'] = list(
            Notification.objects
            .filter(recipient=user, is_read=False)
            .order_by('-created_at')[:50]
        )
    except Exception:
        ctx['helm_unread_notifications'] = []

    return ctx


class DashboardView(LoginRequiredMixin, TemplateView):
    """Main executive dashboard — the single-page overview."""
    template_name = 'dashboard/index.html'

    def get(self, request, *args, **kwargs):
        response = super().get(request, *args, **kwargs)
        # Mark session so the welcome greeting only shows once per session
        request.session['helm_greeted'] = True
        return response

    # Max action items shown in the above-the-fold summary strip
    ACTION_ITEM_SUMMARY_LIMIT = 5

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        agg = _aggregator_for(self.request.user)

        all_actions = agg.get_all_action_items()
        context['action_items'] = all_actions
        context['action_items_count'] = len(all_actions)
        context['top_action_items'] = all_actions[:self.ACTION_ITEM_SUMMARY_LIMIT]
        context['has_more_actions'] = len(all_actions) > self.ACTION_ITEM_SUMMARY_LIMIT
        context['alerts'] = agg.get_all_alerts()
        context['alerts_count'] = len(context['alerts'])
        context['metrics_by_product'] = agg.get_metrics_by_product()
        context['fleet_health'] = agg.get_fleet_health()
        context['bookmarks'] = DashboardBookmark.objects.filter(
            user=self.request.user, is_active=True
        )[:10]
        context.update(_current_period_context())
        context.update(_today_tab_context(self.request))
        # Default tab — read from query string so deep-linked tab state
        # survives reload. Hash-based switching on the client doesn't
        # round-trip to the server.
        context['active_tab'] = (
            'suite' if self.request.GET.get('tab') == 'suite' else 'today'
        )
        return context


class NotificationInboxView(LoginRequiredMixin, TemplateView):
    """Full notification management view across all products."""
    template_name = 'dashboard/notifications.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from core.models import Notification
        qs = Notification.objects.filter(recipient=self.request.user)

        filter_product = self.request.GET.get('product')
        filter_read = self.request.GET.get('read')

        if filter_product:
            qs = qs.filter(title__icontains=filter_product)
        if filter_read == 'unread':
            qs = qs.filter(is_read=False)
        elif filter_read == 'read':
            qs = qs.filter(is_read=True)

        context['notifications'] = qs.order_by('-created_at')[:100]
        context['unread_count'] = Notification.objects.filter(
            recipient=self.request.user, is_read=False
        ).count()
        return context


class PeriodDetailView(LoginRequiredMixin, TemplateView):
    """Focused view of a single fiscal period across the fleet."""
    template_name = 'dashboard/period.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from keel.periods.models import FiscalPeriod
        period_id = self.kwargs.get('period_id')

        try:
            period = FiscalPeriod.objects.select_related('fiscal_year').get(pk=period_id)
        except FiscalPeriod.DoesNotExist:
            period = None

        agg = _aggregator_for(self.request.user)
        context['period'] = period
        context['metrics_by_product'] = agg.get_metrics_by_product()
        context['alerts'] = agg.get_all_alerts()
        return context


class MyProgramsView(LoginRequiredMixin, TemplateView):
    """Program director cockpit — filtered to assigned programs."""
    template_name = 'dashboard/programs.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        agg = _aggregator_for(self.request.user)
        context['metrics_by_product'] = agg.get_metrics_by_product()
        context['action_items'] = agg.get_all_action_items()
        context['alerts'] = agg.get_all_alerts()
        return context


# --- htmx partial views ---

class ActionQueuePartialView(LoginRequiredMixin, TemplateView):
    """htmx partial: action queue refresh."""
    template_name = 'partials/action_queue.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        agg = _aggregator_for(self.request.user)
        all_actions = agg.get_all_action_items()
        limit = DashboardView.ACTION_ITEM_SUMMARY_LIMIT
        context['action_items'] = all_actions
        context['action_items_count'] = len(all_actions)
        context['top_action_items'] = all_actions[:limit]
        context['has_more_actions'] = len(all_actions) > limit
        return context


class AlertPanelPartialView(LoginRequiredMixin, TemplateView):
    """htmx partial: alert panel refresh."""
    template_name = 'partials/alert_panel.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        agg = _aggregator_for(self.request.user)
        context['alerts'] = agg.get_all_alerts()
        context['alerts_count'] = len(context['alerts'])
        return context


class MetricsGridPartialView(LoginRequiredMixin, TemplateView):
    """htmx partial: metrics grid refresh."""
    template_name = 'partials/metrics_grid.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        agg = _aggregator_for(self.request.user)
        context['metrics_by_product'] = agg.get_metrics_by_product()
        return context


class ProductCardPartialView(LoginRequiredMixin, TemplateView):
    """htmx partial: single product card refresh."""
    template_name = 'partials/product_card.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        product_key = self.kwargs.get('product')
        agg = _aggregator_for(self.request.user)
        all_metrics = agg.get_metrics_by_product()
        context['card'] = all_metrics.get(product_key, {})
        context['product_key'] = product_key
        return context


# --- Today-tab partials ---

class InboxColumnPartialView(LoginRequiredMixin, TemplateView):
    """htmx partial: cross-suite inbox column refresh."""
    template_name = 'dashboard/_inbox_column.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(_today_tab_context(self.request))
        return context


class DeadlineRailPartialView(LoginRequiredMixin, TemplateView):
    """htmx partial: my-tasks deadline rail refresh."""
    template_name = 'dashboard/_deadline_rail.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(_today_tab_context(self.request))
        return context


class AlertsColumnPartialView(LoginRequiredMixin, TemplateView):
    """htmx partial: alerts column (helm + peer notifications)."""
    template_name = 'dashboard/_alerts_column.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update(_today_tab_context(self.request))
        return context


# --- Suite-tab drill-down ---

class ProductDrillDownView(LoginRequiredMixin, TemplateView):
    """In-tab drill-down for a single peer product.

    Shows the full metric grid + sparklines + action queue + alerts for
    one product. Reached by clicking a metric card on tab 2.
    """
    template_name = 'dashboard/_product_drilldown.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        product_key = self.kwargs.get('product')
        if product_key not in PRODUCT_META:
            raise Http404(f'Unknown product: {product_key}')
        # ACL: deny if the user doesn't have access to this product.
        accessible = get_user_product_keys(self.request.user)
        if accessible is not None and product_key not in accessible:
            raise Http404()
        agg = _aggregator_for(self.request.user)
        all_metrics = agg.get_metrics_by_product()
        feed = agg.get_feed(product_key)
        context['product_key'] = product_key
        context['product_meta'] = PRODUCT_META[product_key]
        context['card'] = all_metrics.get(product_key, {})
        context['action_items'] = [
            {**(a.__dict__ if hasattr(a, '__dict__') else a),
             'product': product_key,
             'product_label': PRODUCT_META[product_key].get('label', product_key)}
            for a in (feed.action_items if feed else [])
        ]
        context['alerts'] = [
            {**(a.__dict__ if hasattr(a, '__dict__') else a),
             'product': product_key,
             'product_label': PRODUCT_META[product_key].get('label', product_key)}
            for a in (feed.alerts if feed else [])
        ]
        snap = CachedFeedSnapshot.objects.filter(product=product_key).first()
        context['fetched_at'] = snap.fetched_at if snap else None
        context['is_stale'] = bool(snap and snap.is_stale)
        return context
