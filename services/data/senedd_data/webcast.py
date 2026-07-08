"""Resolve SeneddTV webcast player GUIDs.

Speeches store a SeneddTV *clip* URL (``http://www.senedd.tv/en/{meeting_id}``),
which serves the full website — nav, cookie banner and all — not an embeddable
player. The embeddable player lives at ``player.senedd.tv/Player/Index/{guid}``
and is keyed by the meeting's webcast GUID, not the clip id. The clip URL
302-redirects to ``/Meeting/Index/{guid}``, so we resolve the GUID once per
meeting (the clip id equals ``meeting_id``) and persist it on
``meetings.webcast_guid``.
"""
import logging
import re
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_CLIP_URL = "http://www.senedd.tv/en/{meeting_id}"
_GUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

# Embeddable player. Format with ``guid``; callers append startPos/autostart.
PLAYER_URL_TEMPLATE = "https://player.senedd.tv/Player/Index/{guid}"


def resolve_webcast_guid(meeting_id: int, timeout: int = 15) -> Optional[str]:
    """Resolve a meeting's SeneddTV webcast GUID from its clip id.

    Follows the ``/en/{meeting_id}`` redirect to ``/Meeting/Index/{guid}`` and
    extracts the GUID. Best-effort: returns ``None`` on any network or parse
    failure so the caller can treat the GUID as optional (the player pane falls
    back to an external link when it is absent).

    Args:
        meeting_id: The meeting id, which equals the SeneddTV clip id.
        timeout: Per-request timeout in seconds.

    Returns:
        The lower-cased GUID string, or ``None`` if it could not be resolved.
    """
    url = _CLIP_URL.format(meeting_id=meeting_id)
    try:
        resp = requests.get(
            url, headers=_HEADERS, timeout=timeout, allow_redirects=False
        )
    except requests.RequestException as e:
        logger.warning("Webcast GUID lookup failed for meeting %s: %s", meeting_id, e)
        return None

    location = resp.headers.get("Location", "")
    match = _GUID_RE.search(location)
    if not match:
        logger.warning(
            "No webcast GUID in redirect for meeting %s (status %s, location %r).",
            meeting_id,
            resp.status_code,
            location,
        )
        return None
    return match.group(0).lower()
