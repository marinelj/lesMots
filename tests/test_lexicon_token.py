"""The token endpoint requires a real app session and preserves its identity."""

import json
import time
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from threading import Thread

import pytest

from lesmots import web


@pytest.fixture
def token_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("LESMOTS_HOME", str(tmp_path))
    monkeypatch.setenv("LESMOTS_SESSION_SECRET", "lexicon-token-test-secret")
    monkeypatch.setenv("LESMOTS_GOOGLE_CLIENT_ID", "test-client")
    monkeypatch.delenv("LESMOTS_WX_APPID", raising=False)
    monkeypatch.delenv("LESMOTS_WX_SECRET", raising=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def get(headers=None):
        connection = HTTPConnection(*server.server_address, timeout=5)
        try:
            connection.request("GET", "/api/lexicon-token", headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), json.loads(response.read())
        finally:
            connection.close()

    yield get
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.mark.parametrize("auth_enabled", [True, False])
@pytest.mark.parametrize("headers", [{}, {"Cookie": f"{web.SESSION_COOKIE}=forged"}])
def test_lexicon_token_requires_sign_in(token_endpoint, monkeypatch, auth_enabled, headers):
    if not auth_enabled:
        monkeypatch.delenv("LESMOTS_GOOGLE_CLIENT_ID")
    status, _, body = token_endpoint(headers)
    assert status == 401
    assert "error" in body and "token" not in body


@pytest.mark.parametrize("transport", ["cookie", "bearer"])
def test_lexicon_token_for_signed_in_user(token_endpoint, monkeypatch, transport):
    now = int(time.time())
    monkeypatch.setattr(web.time, "time", lambda: now)
    session = web.make_session("google-user-123", "reader@example.com")
    headers = ({"Cookie": f"{web.SESSION_COOKIE}={session}"} if transport == "cookie"
               else {"Authorization": f"Bearer {session}"})
    # Issuing a token refreshes the lifetime, rather than copying an older session.
    monkeypatch.setattr(web.time, "time", lambda: now + 86400)
    status, response_headers, body = token_endpoint(headers)
    assert status == 200
    assert response_headers["Cache-Control"] == "no-store"
    assert body["user_id"] == "google-user-123"
    assert body["expires_in_days"] == 30
    issued = web.check_session(body["token"])
    assert issued == {"uid": "google-user-123", "email": "reader@example.com",
                      "exp": now + 86400 + 30 * 86400}
    assert body["token"] != session
