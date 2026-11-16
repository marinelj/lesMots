"""Run the hosted server behind a gateway, at a public URL that may have a path prefix.

    lexrender serve --http --port 8765 --public-url https://marine-api.tunnel.tungo.cn/lesmots

serves the MCP endpoint at ``<public-url>/mcp``, the dashboard at ``<public-url>/dashboard``
and a health check at ``<public-url>/healthz``. The gateway may forward the prefix or strip
it: requests are accepted either way. Only the public host (and localhost, for checks on the
machine itself) passes the MCP transport's Host check.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from mcp.server.transport_security import TransportSecuritySettings

LOCAL_HOSTS = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
LOCAL_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]


def prefix_of(public_url: str) -> str:
    path = urlsplit(public_url).path.rstrip("/")
    return path if path not in ("", "/") else ""


def security_for(public_url: str) -> TransportSecuritySettings:
    parts = urlsplit(public_url)
    host = parts.netloc
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[host, f"{parts.hostname}:*", *LOCAL_HOSTS],
        allowed_origins=[f"{parts.scheme}://{host}", *LOCAL_ORIGINS],
    )


class StripPrefix:
    """ASGI wrapper: '/lesmots/mcp' and '/mcp' both reach the '/mcp' route."""

    def __init__(self, app, prefix: str):
        self.app = app
        self.prefix = prefix

    async def __call__(self, scope, receive, send):
        if self.prefix and scope["type"] in ("http", "websocket"):
            path = scope["path"]
            if path == self.prefix or path.startswith(self.prefix + "/"):
                rest = path[len(self.prefix):] or "/"
                scope = dict(scope, path=rest, root_path=scope.get("root_path", "") + self.prefix)
                raw = scope.get("raw_path")
                if raw is not None and raw.startswith(self.prefix.encode()):
                    scope["raw_path"] = raw[len(self.prefix):] or b"/"
        await self.app(scope, receive, send)


def http_app(server, public_url: str, host: str = "127.0.0.1"):
    app = server.streamable_http_app(transport_security=security_for(public_url), host=host)
    return StripPrefix(app, prefix_of(public_url))


def serve(server, public_url: str, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(http_app(server, public_url, host), host=host, port=port,
                proxy_headers=True, forwarded_allow_ips="127.0.0.1")
