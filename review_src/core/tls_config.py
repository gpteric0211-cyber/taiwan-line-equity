"""Application-entrypoint TLS setup using the platform's trusted certificates."""

from __future__ import annotations
import os

_configured = False


def configure_tls():
    global _configured
    if _configured:
        return
    mode = os.getenv("EQUITY_TLS_TRUST", "system").strip().lower()
    if mode not in {"system", "python"}:
        raise ValueError("EQUITY_TLS_TRUST must be system or python")
    if mode == "system":
        import truststore

        truststore.inject_into_ssl()
    _configured = True
