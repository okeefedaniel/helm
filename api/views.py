"""Helm API views."""
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from dashboard.services import FeedAggregator, get_user_product_keys
from .serializers import BriefingSerializer


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
