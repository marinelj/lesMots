"""The web app can sit behind a gateway path prefix (LESMOTS_BASE_PATH)."""

import json
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from lesmots import web


@pytest.fixture
def request_app(tmp_path, monkeypatch):
    monkeypatch.setenv("LESMOTS_HOME", str(tmp_path))
    monkeypatch.setenv("LESMOTS_SESSION_SECRET", "base-path-test-secret")
    monkeypatch.delenv("LESMOTS_GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("LESMOTS_WX_APPID", raising=False)
    monkeypatch.delenv("LESMOTS_WX_SECRET", raising=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def send(method, path):
        connection = HTTPConnection(*server.server_address, timeout=5)
        try:
            connection.request(method, path, body=b"{}" if method == "POST" else None,
                               headers={"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    yield send
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


@pytest.mark.parametrize("base", ["/lesmots-app", "lesmots-app/", "/lesmots-app/"])
def test_base_path_is_normalised(monkeypatch, base):
    monkeypatch.setenv("LESMOTS_BASE_PATH", base)
    assert web._base_path() == "/lesmots-app"


def test_no_base_path_by_default(monkeypatch):
    monkeypatch.delenv("LESMOTS_BASE_PATH", raising=False)
    assert web._base_path() == ""


@pytest.mark.parametrize("path", ["/lesmots-app/api/auth-config", "/api/auth-config"])
def test_api_works_with_or_without_prefix(request_app, monkeypatch, path):
    monkeypatch.setenv("LESMOTS_BASE_PATH", "/lesmots-app")
    status, _, body = request_app("GET", path)
    assert status == 200
    assert "client_id" in json.loads(body)


@pytest.mark.parametrize("path", ["/lesmots-app/", "/"])
def test_ui_served_under_prefix(request_app, monkeypatch, path):
    monkeypatch.setenv("LESMOTS_BASE_PATH", "/lesmots-app")
    status, headers, body = request_app("GET", path)
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert b"<html" in body.lower()


@pytest.mark.parametrize(("path", "location"), [
    ("/lesmots-app", "/lesmots-app/"),
    ("/lesmots-app?x=1", "/lesmots-app/?x=1"),
])
def test_bare_prefix_redirects_to_slash(request_app, monkeypatch, path, location):
    monkeypatch.setenv("LESMOTS_BASE_PATH", "/lesmots-app")
    status, headers, _ = request_app("GET", path)
    assert status == 308
    assert headers["Location"] == location


def test_post_under_prefix(request_app, monkeypatch):
    monkeypatch.setenv("LESMOTS_BASE_PATH", "/lesmots-app")
    status, _, body = request_app("POST", "/lesmots-app/api/logout")
    assert status == 200
    assert json.loads(body) == {"ok": True}


def test_similar_prefix_is_not_stripped(request_app, monkeypatch):
    monkeypatch.setenv("LESMOTS_BASE_PATH", "/lesmots-app")
    status, _, _ = request_app("GET", "/lesmots-application/api/auth-config")
    assert status == 404


def test_ui_calls_are_relative():
    ui = (Path(web.__file__).parent / "frontends" / "web" / "ui.html").read_text()
    assert "'/api/" not in ui and '"/api/' not in ui and "`/api/" not in ui
    assert "'api/auth-config'" in ui
