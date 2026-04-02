"""API serializers for Helm."""
from rest_framework import serializers


class MetricSerializer(serializers.Serializer):
    key = serializers.CharField()
    label = serializers.CharField()
    value = serializers.FloatField()
    unit = serializers.CharField(allow_null=True, required=False)
    trend = serializers.CharField(allow_null=True, required=False)
    trend_value = serializers.FloatField(allow_null=True, required=False)
    trend_period = serializers.CharField(allow_null=True, required=False)
    severity = serializers.CharField(default='normal')
    deep_link = serializers.CharField(default='')


class ActionItemSerializer(serializers.Serializer):
    id = serializers.CharField()
    type = serializers.CharField()
    title = serializers.CharField()
    description = serializers.CharField(default='')
    priority = serializers.CharField(default='medium')
    due_date = serializers.CharField(allow_null=True, required=False)
    deep_link = serializers.CharField(default='')
    product = serializers.CharField(required=False)
    product_label = serializers.CharField(required=False)


class AlertSerializer(serializers.Serializer):
    id = serializers.CharField()
    type = serializers.CharField()
    title = serializers.CharField()
    severity = serializers.CharField(default='info')
    deep_link = serializers.CharField(default='')
    product = serializers.CharField(required=False)
    product_label = serializers.CharField(required=False)


class BriefingSerializer(serializers.Serializer):
    briefing_date = serializers.CharField()
    fiscal_context = serializers.CharField()
    action_items_count = serializers.IntegerField()
    critical_actions = serializers.ListField(child=serializers.CharField())
    alerts_count = serializers.IntegerField()
    critical_alerts = serializers.ListField(child=serializers.CharField())
    metrics_summary = serializers.DictField(child=serializers.CharField())
    fleet_health = serializers.CharField()
