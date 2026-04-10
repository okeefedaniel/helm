"""Helm dashboard views."""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from .models import CachedFeedSnapshot, DashboardBookmark
from .services import FeedAggregator, get_user_product_keys


def _aggregator_for(user):
    """Return a FeedAggregator filtered to the user's product access."""
    return FeedAggregator(product_keys=get_user_product_keys(user))


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
