"""Dashboard HTTP routes (paid tier), mounted on the hosted MCP server.

    GET /dashboard                    the page (static/dashboard.html)
    GET /api/dashboard/summary        entry count, exposures, coverage by section
    GET /api/dashboard/entries        per-entry exposure count, last seen, synonym hits
    GET /api/dashboard/timeline       exposures per UTC day (?days=30)
    GET /api/dashboard/surfaces       exposures per place (Claude Code, Notion, ...)
    GET /api/dashboard/entry/{id}     one entry and its exposure history

Every API call needs ``Authorization: Bearer <LesMots sign-in token>`` and only reads that
user's account. 401 = not signed in, 402 = the account is on the free plan.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, Response

from . import analytics
from .signin import VerifySignIn
from .store import LexiconError, Store

PAGE = Path(__file__).parent / "static" / "dashboard.html"
DEMO = Path(__file__).parent / "static" / "demo"
DEMO_NAME = re.compile(r"^[a-z0-9-]+\.json$")
PLACEHOLDER = ("<!doctype html><meta charset=utf-8><title>Lexicon dashboard</title>"
               "<p>The dashboard page is not installed yet. The API is under /api/dashboard/.")

Handler = Callable[[Request, str], object]


def _error(message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status,
                        headers={"Cache-Control": "no-store"})


def add_routes(server, store: Store, verify: VerifySignIn) -> None:
    def api(path: str, handler: Handler) -> None:
        async def endpoint(request: Request) -> Response:
            auth = request.headers.get("authorization", "")
            app_uid = verify(auth[7:].strip()) if auth.lower().startswith("bearer ") else None
            if not app_uid:
                return _error("sign in to LesMots and send its token as a Bearer header", 401)
            account_id = store.account_for_app_user(app_uid)
            if account_id is None:
                return _error("unknown account", 401)
            if not store.is_paid(account_id):
                return _error("the dashboard is part of the paid plan", 402)
            try:
                result = handler(request, account_id)
            except (LexiconError, ValueError) as exc:
                return _error(str(exc), 400)
            if result is None:
                return _error("not found", 404)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})

        server.custom_route(path, methods=["GET"])(endpoint)

    q = lambda request, key, default: request.query_params.get(key, default)

    api("/api/dashboard/summary", lambda r, a: analytics.summary(store, a))
    api("/api/dashboard/entries",
        lambda r, a: analytics.entries(store, a, sort=q(r, "sort", "exposures")))
    api("/api/dashboard/timeline",
        lambda r, a: analytics.timeline(store, a, days=int(q(r, "days", "30"))))
    api("/api/dashboard/surfaces", lambda r, a: analytics.surfaces(store, a))
    api("/api/dashboard/entry/{entry_id}",
        lambda r, a: analytics.entry_detail(store, a, r.path_params["entry_id"]))

    async def page(request: Request) -> Response:
        # the page itself is public; it holds no data until it calls the API with a token
        return FileResponse(PAGE) if PAGE.exists() else HTMLResponse(PLACEHOLDER)

    async def demo_file(request: Request) -> Response:
        # canned sample data for /dashboard?demo=1; public, holds nobody's lexicon
        name = request.path_params["name"]
        path = DEMO / name
        if not DEMO_NAME.match(name) or not path.is_file():
            return _error("not found", 404)
        return FileResponse(path, media_type="application/json")

    async def healthz(request: Request) -> Response:
        return JSONResponse({"ok": True, "service": "lexicon-render"})

    server.custom_route("/healthz", methods=["GET"])(healthz)
    server.custom_route("/dashboard", methods=["GET"])(page)
    server.custom_route("/dashboard/demo/{name}", methods=["GET"])(demo_file)

