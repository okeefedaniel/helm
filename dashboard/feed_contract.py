"""Helm Feed Contract — defines the standard schema for product feeds.

Every product exposes a /api/v1/helm-feed/ endpoint returning data
conforming to this contract. Helm consumes these feeds and caches
them in CachedFeedSnapshot.
"""
from dataclasses import dataclass, field


@dataclass
class Metric:
    key: str
    label: str
    value: float | int
    unit: str | None = None         # None for counts, "USD" for money, "days" for durations
    trend: str | None = None        # "up", "down", "flat", None
    trend_value: float | int | None = None
    trend_period: str | None = None  # "day", "week", "month", "quarter"
    severity: str = 'normal'        # "normal", "warning", "critical"
    deep_link: str = ''


@dataclass
class ActionItem:
    id: str
    type: str               # "approval", "review", "signature", "submission", "response"
    title: str
    description: str = ''
    priority: str = 'medium'  # "low", "medium", "high", "critical"
    due_date: str | None = None
    assigned_to_role: str = ''
    deep_link: str = ''
    created_at: str = ''


@dataclass
class Alert:
    id: str
    type: str               # "overdue", "deadline", "variance", "milestone", "anomaly"
    title: str
    severity: str = 'info'  # "info", "warning", "critical"
    since: str = ''
    deep_link: str = ''


@dataclass
class SparklineData:
    values: list[float | int] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    period: str = 'month'


@dataclass
class ProductFeed:
    """Full feed response from a single product."""
    product: str
    product_label: str
    product_url: str
    updated_at: str = ''
    metrics: list[Metric] = field(default_factory=list)
    action_items: list[ActionItem] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    sparklines: dict[str, SparklineData] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict for caching."""
        from dataclasses import asdict
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'ProductFeed':
        """Deserialize from a cached JSON dict."""
        metrics = [Metric(**m) for m in data.get('metrics', [])]
        action_items = [ActionItem(**a) for a in data.get('action_items', [])]
        alerts = [Alert(**a) for a in data.get('alerts', [])]
        sparklines = {
            k: SparklineData(**v) for k, v in data.get('sparklines', {}).items()
        }
        return cls(
            product=data['product'],
            product_label=data['product_label'],
            product_url=data['product_url'],
            updated_at=data.get('updated_at', ''),
            metrics=metrics,
            action_items=action_items,
            alerts=alerts,
            sparklines=sparklines,
        )


# Per-user inbox contract: items where the requesting user is the gating
# dependency in a peer product. Returned by /api/v1/helm-feed/inbox/.
# Locked 2026-04-26; do not modify shape after pilot ships.

INBOX_ITEM_TYPES = ('signature', 'approval', 'review', 'response', 'assignment')
INBOX_PRIORITIES = ('low', 'normal', 'high', 'urgent')


@dataclass
class InboxItem:
    """One ball-in-this-user's-court item from a peer product."""
    id: str                         # peer-local stable id
    type: str                       # one of INBOX_ITEM_TYPES
    title: str                      # human-readable, e.g. "Sign award packet for City of Toledo"
    deep_link: str                  # absolute URL into the peer
    waiting_since: str = ''         # ISO8601 — when ball landed in this user's court
    due_date: str | None = None     # ISO8601 date, or None
    priority: str = 'normal'        # one of INBOX_PRIORITIES


@dataclass
class UnreadNotification:
    """One unread keel.notifications row from a peer product."""
    id: str
    title: str
    body: str = ''
    deep_link: str = ''
    created_at: str = ''            # ISO8601
    priority: str = 'normal'        # one of INBOX_PRIORITIES


@dataclass
class UserInbox:
    """Response shape of /api/v1/helm-feed/inbox/?user_sub=<sub>."""
    product: str
    product_label: str
    product_url: str
    user_sub: str                   # OIDC sub of the requesting user
    items: list[InboxItem] = field(default_factory=list)
    unread_notifications: list[UnreadNotification] = field(default_factory=list)
    fetched_at: str = ''            # ISO8601

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'UserInbox':
        items = [InboxItem(**i) for i in data.get('items', [])]
        notes = [UnreadNotification(**n) for n in data.get('unread_notifications', [])]
        return cls(
            product=data['product'],
            product_label=data['product_label'],
            product_url=data['product_url'],
            user_sub=data['user_sub'],
            items=items,
            unread_notifications=notes,
            fetched_at=data.get('fetched_at', ''),
        )
