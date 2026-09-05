from __future__ import annotations

"""Small URL boundary for citation metadata that is displayed but never fetched."""

import ipaddress
from typing import Any
from urllib.parse import urlparse


def normalize_public_https_url(value: Any, *, maximum_length: int = 1_000) -> str | None:
    """Return a bounded public HTTPS URL, rejecting local/private direct targets.

    This validates citation metadata only.  It does not authorize fetching the
    destination and intentionally performs no DNS lookup.
    """

    text = str(value or "").strip()
    if not text or len(text) > max(100, int(maximum_length)):
        return None
    parsed = urlparse(text)
    hostname = str(parsed.hostname or "").strip().lower().rstrip(".")
    try:
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.lower() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or hostname == "localhost"
        or hostname.endswith((".localhost", ".local", ".internal"))
    ):
        return None
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return text
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return None
    return text
