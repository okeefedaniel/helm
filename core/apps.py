from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = 'core'
    label = 'helm_core'
    verbose_name = 'Helm Core'

    def ready(self):
        from keel.notifications import NotificationType, register

        register(NotificationType(
            key='briefing_ready',
            label='Morning Briefing Ready',
            description='Your daily executive briefing is ready for review.',
            category='Dashboard',
            default_channels=['in_app'],
            default_roles=['executive', 'program_director'],
            priority='low',
        ))
        register(NotificationType(
            key='action_item_overdue',
            label='Action Item Overdue',
            description='A pending action item has passed its due date.',
            category='Dashboard',
            default_channels=['in_app', 'email'],
            default_roles=['executive', 'program_director'],
            priority='high',
        ))
        register(NotificationType(
            key='fleet_health_degraded',
            label='Fleet Health Degraded',
            description='One or more products are reporting errors or degraded performance.',
            category='Dashboard',
            default_channels=['in_app'],
            default_roles=['executive'],
            priority='medium',
        ))
        register(NotificationType(
            key='compliance_alert',
            label='Compliance Alert',
            description='A compliance item across the fleet requires attention.',
            category='Dashboard',
            default_channels=['in_app', 'email'],
            default_roles=['executive', 'program_director'],
            priority='high',
        ))
