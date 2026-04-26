"""Per-user cross-suite inbox aggregator.

Reads each peer product's ``/api/v1/helm-feed/inbox/?user_sub=<sub>``
endpoint and assembles "ball-in-this-user's-court" items grouped by
product, plus the unread notifications each peer reports for the user.

Design notes:
- Per-user-per-peer cache: 60s TTL, key includes the OIDC sub so users
  never see each other's inbox.
- Graceful degradation: if a peer hasn't yet implemented the inbox
  endpoint (404), fall back to that peer's existing aggregate
  ``ActionItem`` count from the cached helm-feed snapshot. The user
  sees a count + "Open in <peer>" link instead of itemized titles.
- Standalone-safe: in the absence of FLEET_PRODUCTS or an API key,
  returns an empty inbox without errors.
"""
from __future__ import annotations

import concurrent.futures
import logging
import time
from urllib.parse import urljoin

import requests
from django.conf import settings
from django.core.cache import cache

from .models import CachedFeedSnapshot
from .services import PRODUCT_META

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60
PER_PEER_TIMEOUT = (3, 8)  # connect, read
MAX_WORKERS = 8


def get_user_oidc_sub(user, request) -> str:
    """Resolve the OIDC ``sub`` for the request's user.

    Primary source: the session blob written by ``KeelSocialAccountAdapter``
    (`request.session['keel_oidc_claims']['sub']`). For local-auth users
    or sessions that pre-date Phase 2b, fall back to looking up the sub
    on the user's keel-provider SocialAccount.
    """
    if user is None or not getattr(user, 'is_authenticated', False):
        return ''
    if request is not None:
        try:
            sub = (request.session.get('keel_oidc_claims') or {}).get('sub') or ''
            if sub:
                return sub
        except Exception:
            pass
    try:
        from allauth.socialaccount.models import SocialAccount
        sa = SocialAccount.objects.filter(user=user, provider='keel').first()
        return sa.uid if sa else ''
    except Exception:
        return ''


def _peer_inbox_cache_key(product_key: str, user_sub: str) -> str:
    return f'helm:peer_inbox:{product_key}:{user_sub}'


def _fetch_peer_inbox(product: dict, user_sub: str, api_key: str) -> dict:
    """Fetch one peer's inbox. Returns a normalized dict with ``ok`` flag.

    Result shape:
        {
          'ok': bool,
          'status': int,           # HTTP status (0 on connection error)
          'data': UserInbox dict,  # populated when ok=True
          'error': str,
          'duration_ms': int,
        }
    """
    feed_url = product.get('feed_url') or ''
    if not feed_url:
        return {'ok': False, 'status': 0, 'data': None,
                'error': 'feed_url not configured', 'duration_ms': 0}

    inbox_url = urljoin(feed_url.rstrip('/') + '/', 'inbox/')
    headers = {'Authorization': f'Bearer {api_key}'} if api_key else {}
    params = {'user_sub': user_sub}
    start = time.monotonic()
    # Retry once on timeout — Railway services can pay a 5-15s cold-boot
    # cost on the first request after idle, which exceeds our 8s read
    # deadline. A second attempt usually lands on a warmed container.
    last_exc: Exception | None = None
    for attempt in (1, 2):
        try:
            resp = requests.get(inbox_url, headers=headers, params=params,
                                timeout=PER_PEER_TIMEOUT)
            duration_ms = int((time.monotonic() - start) * 1000)
            if resp.status_code != 200:
                return {
                    'ok': False, 'status': resp.status_code, 'data': None,
                    'error': f'HTTP {resp.status_code}', 'duration_ms': duration_ms,
                }
            return {
                'ok': True, 'status': 200, 'data': resp.json(),
                'error': '', 'duration_ms': duration_ms,
            }
        except requests.Timeout as e:
            last_exc = e
            if attempt == 1:
                continue
            break
        except requests.RequestException as e:
            last_exc = e
            break
    duration_ms = int((time.monotonic() - start) * 1000)
    return {
        'ok': False, 'status': 0, 'data': None,
        'error': str(last_exc), 'duration_ms': duration_ms,
    }


def _fallback_from_snapshot(product_key: str) -> dict:
    """Synthesize an inbox payload from the cached aggregate feed.

    Used when a peer hasn't implemented the per-user endpoint yet. Reports
    aggregate counts only — no per-user filtering — flagged with
    ``unfiltered=True`` so the template can render the disclaimer.
    """
    meta = PRODUCT_META.get(product_key, {})
    base = {
        'product': product_key,
        'product_label': meta.get('label', product_key.title()),
        'product_url': meta.get('url', ''),
        'user_sub': '',
        'items': [],
        'unread_notifications': [],
        'fetched_at': '',
        'unfiltered': True,
        'fallback_reason': 'peer_inbox_endpoint_unavailable',
    }
    snap = CachedFeedSnapshot.objects.filter(product=product_key).first()
    if not snap:
        base['aggregate_count'] = 0
        return base
    feed_data = snap.feed_data or {}
    base['aggregate_count'] = len(feed_data.get('action_items') or [])
    return base


class InboxAggregator:
    """Per-user inbox + per-peer notifications, cached briefly per user."""

    def __init__(self, user, request=None):
        self.user = user
        self.request = request
        self.user_sub = get_user_oidc_sub(user, request)
        self._fleet = list(getattr(settings, 'FLEET_PRODUCTS', []) or [])
        self._api_key = getattr(settings, 'HELM_FEED_API_KEY', '') or ''
        self._per_product_memo: list[dict] | None = None

    def _per_peer(self, product: dict) -> dict:
        key = product['key']
        # Per-user cache hit
        if self.user_sub:
            cache_key = _peer_inbox_cache_key(key, self.user_sub)
            cached = cache.get(cache_key)
            if cached is not None:
                return cached
        else:
            cache_key = None

        if not self.user_sub or not self._api_key:
            payload = _fallback_from_snapshot(key)
            if cache_key:
                cache.set(cache_key, payload, timeout=CACHE_TTL_SECONDS)
            return payload

        result = _fetch_peer_inbox(product, self.user_sub, self._api_key)
        if result['ok']:
            payload = result['data'] or {}
            payload.setdefault('product', key)
            payload.setdefault('product_label', product.get('label', key.title()))
            payload.setdefault('product_url', product.get('url', ''))
            payload.setdefault('items', [])
            payload.setdefault('unread_notifications', [])
            payload['unfiltered'] = False
        elif result['status'] == 404:
            # Peer hasn't implemented the endpoint yet — fall back to
            # the aggregate snapshot count.
            payload = _fallback_from_snapshot(key)
        else:
            # Connection failure / 5xx / 401 etc. Don't claim "fallback
            # available" — surface the failure so the template can show
            # a stale chip.
            payload = _fallback_from_snapshot(key)
            payload['fallback_reason'] = f'peer_unreachable: {result["error"][:80]}'
            payload['unreachable'] = True

        if cache_key:
            cache.set(cache_key, payload, timeout=CACHE_TTL_SECONDS)
        return payload

    def get_per_product(self) -> list[dict]:
        """Return one payload per fleet product, ordered by FLEET_PRODUCTS.

        Each payload is a dict with the UserInbox shape extended with
        product metadata (icon, tagline) and the dashboard's degradation
        flags (``unfiltered``, ``unreachable``, ``fallback_reason``,
        ``aggregate_count``).
        """
        if self._per_product_memo is not None:
            return self._per_product_memo
        if not self._fleet:
            self._per_product_memo = []
            return self._per_product_memo

        # Parallel fetch — cap workers to MAX_WORKERS so a tiny fleet
        # doesn't spin up a giant pool.
        workers = min(MAX_WORKERS, len(self._fleet))
        results: list[dict] = [None] * len(self._fleet)
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {
                ex.submit(self._per_peer, p): i
                for i, p in enumerate(self._fleet)
            }
            for fut in concurrent.futures.as_completed(futures):
                i = futures[fut]
                try:
                    results[i] = fut.result()
                except Exception as e:
                    logger.exception('Inbox fetch failed for %s', self._fleet[i].get('key'))
                    results[i] = _fallback_from_snapshot(self._fleet[i]['key'])
                    results[i]['unreachable'] = True
                    results[i]['fallback_reason'] = f'aggregator_error: {e}'

        # Decorate with product metadata for the template.
        for i, payload in enumerate(results):
            meta = self._fleet[i]
            payload['product_icon'] = meta.get('icon', 'bi-app')
            payload['product_tagline'] = meta.get('tagline', '')
            payload['item_count'] = len(payload.get('items') or [])
            payload['notification_count'] = len(payload.get('unread_notifications') or [])
        self._per_product_memo = results
        return results

    def get_total_item_count(self) -> int:
        return sum(p['item_count'] for p in self.get_per_product())

    def get_aggregated_unread_notifications(self) -> list[dict]:
        """Flatten unread notifications from every peer + decorate w/ product."""
        out = []
        for payload in self.get_per_product():
            for n in payload.get('unread_notifications') or []:
                out.append({
                    **n,
                    'product': payload['product'],
                    'product_label': payload['product_label'],
                    'product_icon': payload['product_icon'],
                })
        # Newest first; rely on ISO8601 lexicographic sort.
        out.sort(key=lambda n: n.get('created_at', ''), reverse=True)
        return out
