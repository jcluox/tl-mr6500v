import ipaddress
import logging
from collections.abc import Callable

from .logging_setup import LOGGER_NAME

log = logging.getLogger(f"{LOGGER_NAME}.access")

# Sources in the local networks are always allowed. When LAN access is on, other private networks are allowed too. Public sources are always rejected.


def is_allowed(client_host: str | None, local_networks: list, allow_lan: bool) -> bool:
    try:
        ip = ipaddress.ip_address(client_host or "")
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if any(ip in net for net in local_networks):
        return True
    return allow_lan and (ip.is_private or ip.is_link_local) and not ip.is_unspecified


class LanAccessMiddleware:
    """Blocks sources other than this machine, depending on allow_lan_access (plain ASGI, so static files and WebSockets are blocked too)."""

    def __init__(self, app, local_networks: list, allow_lan: Callable[[], bool]):
        self.app, self.local_networks, self.allow_lan = app, local_networks, allow_lan
        self._reported: set[str | None] = set()

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            host = scope["client"][0] if scope.get("client") else None
            if not is_allowed(host, self.local_networks, self.allow_lan()):
                if host not in self._reported and len(self._reported) < 100:
                    # Log each source only once. Under Docker the source is often the bridge gateway, not 127.0.0.1, and this line helps find it
                    self._reported.add(host)
                    log.warning("Rejected a connection from %s (not this machine, and LAN access is off or the address is not in LOCAL_CLIENT_CIDRS)", host)
                if scope["type"] == "http":
                    await send({"type": "http.response.start", "status": 403,
                                "headers": [(b"content-type", b"text/plain; charset=utf-8")]})
                    await send({"type": "http.response.body",
                                "body": "Local access only (you can allow the local network in the Dashboard settings)".encode()})
                else:
                    await send({"type": "websocket.close", "code": 1008})
                return
        await self.app(scope, receive, send)
