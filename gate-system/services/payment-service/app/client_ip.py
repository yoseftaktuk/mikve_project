from __future__ import annotations

from fastapi import Request


def resolve_callback_source_ip(request: Request) -> str | None:
    """Pick the real client IP for a Nedarim CallBack.

    Prefer CF-Connecting-IP (Cloudflare Tunnel / CDN). Fall back to X-Real-IP
    set by nginx, then the socket peer. Spoofable headers are only trusted when
    the request actually arrived through our reverse proxy / tunnel path —
    direct callers cannot set CF-Connecting-IP unless they reach us through CF.
    """
    cf = request.headers.get("cf-connecting-ip")
    if cf and cf.strip():
        return cf.strip()

    real_ip = request.headers.get("x-real-ip")
    if real_ip and real_ip.strip():
        return real_ip.strip().split(",")[0].strip()

    if request.client is not None and request.client.host:
        return request.client.host
    return None
