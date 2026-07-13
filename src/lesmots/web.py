"""LesMots web UI server (stdlib http.server — zero dependencies).

Endpoints:
    GET  /            -> HTML UI
    GET  /api/auth-config -> {"client_id", "user"} (Google SSO config/session)
    GET  /api/words   -> word bank table data
    GET  /api/loved   -> loved stories list
    GET  /api/config  -> {"language", "interests"}
    POST /api/login   -> {"credential": <Google ID token>} start a session
    POST /api/logout  -> clear the session
    POST /api/add     -> {"text": ...} add word + explanation
    POST /api/check-spelling -> {"text": ...} -> {"suggestions": [...]}
    POST /api/show-me -> prepared content; updates exposure counts
    POST /api/keep-reading -> extend the current story; updates exposure counts
    POST /api/love    -> persist the current story to the loved list
    POST /api/unlove  -> {"id": ...} remove a loved story
    POST /api/familiarity -> {"text": ..., "level": 1-5} manual familiarity
    POST /api/remove-word -> {"text": ...} delete a bank entry
    POST /api/interests   -> {"interests": [...]} update feed topics

Auth (issue #18): set LESMOTS_GOOGLE_CLIENT_ID to require Google sign-in;
every user then gets an isolated store under $LESMOTS_HOME/users/. With
the variable unset the app stays single-user with no login.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from lesmots import llm, daily
from lesmots.memory import Memory, data_path
from lesmots.models import Word

UI_PATH = Path(__file__).parent / "ui.html"
GOOGLE_TOKENINFO = "https://oauth2.googleapis.com/tokeninfo?id_token="
SESSION_COOKIE = "lesmots_session"
SESSION_DAYS = 30

# The server is threaded but every POST does load-modify-save on one JSON
# file; without serialization, rapid clicks (e.g. familiarity dots) race
# and lose updates (issue #15).
_WRITE_LOCK = threading.Lock()

# POSTs that must not hold the write lock (read-only and/or slow network).
_UNLOCKED_POSTS = {"/api/check-spelling", "/api/login", "/api/logout"}


# ---------- auth helpers ----------

def _client_id() -> str:
    return os.environ.get("LESMOTS_GOOGLE_CLIENT_ID", "")


def _auth_enabled() -> bool:
    return bool(_client_id())


def _session_secret() -> bytes:
    env = os.environ.get("LESMOTS_SESSION_SECRET")
    if env:
        return env.encode()
    p = data_path().parent / "session-secret"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(os.urandom(32).hex())
    return p.read_text().strip().encode()


def _sign(payload: str) -> str:
    return hmac.new(_session_secret(), payload.encode(), hashlib.sha256).hexdigest()


def make_session(uid: str, email: str) -> str:
    data = {"uid": uid, "email": email, "exp": int(time.time()) + SESSION_DAYS * 86400}
    payload = base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")
    return payload + "." + _sign(payload)


def check_session(token: str) -> Optional[dict]:
    try:
        payload, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, _sign(payload)):
            return None
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if data.get("exp", 0) < time.time():
            return None
        return data
    except Exception:
        return None


def verify_google_token(credential: str) -> Optional[dict]:
    """Have Google validate the ID token (signature + expiry checked server-side)."""
    try:
        url = GOOGLE_TOKENINFO + urllib.parse.quote(credential)
        with urllib.request.urlopen(url, timeout=15) as resp:
            info = json.loads(resp.read())
    except Exception:
        return None
    if info.get("aud") != _client_id() or not info.get("sub"):
        return None
    return info


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str = "application/json", code: int = 200,
              headers: Optional[list] = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")  # always serve the deployed version
        for key, value in headers or []:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200, headers: Optional[list] = None) -> None:
        self._send(json.dumps(obj, ensure_ascii=False).encode(), code=code, headers=headers)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    # ---------- session / per-user storage ----------

    def _session(self) -> Optional[dict]:
        for part in (self.headers.get("Cookie") or "").split(";"):
            key, _, value = part.strip().partition("=")
            if key == SESSION_COOKIE:
                return check_session(value)
        return None

    def _resolve_user(self) -> Optional[str]:
        if not _auth_enabled():
            return None  # single-user mode
        session = self._session()
        if not session:
            return None
        return re.sub(r"[^\w-]", "", str(session["uid"])) or None

    def _mem(self) -> Memory:
        return Memory.load(data_path(self._uid))

    def _msave(self, memory: Memory) -> None:
        memory.save(data_path(self._uid))

    def _login_required(self) -> bool:
        """True (and responds 401) when auth is on and there is no session."""
        if _auth_enabled() and self._uid is None:
            self._json({"error": "login required"}, code=401)
            return True
        return False

    # ---------- GET ----------

    def do_GET(self) -> None:  # noqa: N802
        self._uid = self._resolve_user()
        if self.path in ("/", "/index.html"):
            self._send(UI_PATH.read_bytes(), ctype="text/html")
        elif self.path == "/api/auth-config":
            session = self._session()
            self._json({"client_id": _client_id(),
                        "user": (session or {}).get("email") if _auth_enabled() else None})
        elif self._login_required():
            return
        elif self.path == "/api/words":
            self._json([w.to_dict() for w in self._mem().words])
        elif self.path == "/api/loved":
            self._json(self._mem().loved)
        elif self.path == "/api/config":
            memory = self._mem()
            self._json({"language": memory.config["language"],
                        "interests": memory.config["interests"]})
        else:
            self._json({"error": "not found"}, code=404)

    # ---------- POST ----------

    def do_POST(self) -> None:  # noqa: N802
        self._uid = self._resolve_user()
        if self.path != "/api/login" and self._login_required():
            return
        if self.path in _UNLOCKED_POSTS:
            self._do_post()
            return
        with _WRITE_LOCK:
            self._do_post()

    def _do_post(self) -> None:
        try:
            if self.path == "/api/login":
                credential = (self._body().get("credential") or "").strip()
                info = verify_google_token(credential) if credential else None
                if not info:
                    self._json({"error": "Google sign-in failed"}, code=401)
                    return
                token = make_session(info["sub"], info.get("email", ""))
                cookie = (f"{SESSION_COOKIE}={token}; Path=/; Max-Age={SESSION_DAYS * 86400}; "
                          f"HttpOnly; SameSite=Lax; Secure")
                self._json({"email": info.get("email", "")}, headers=[("Set-Cookie", cookie)])
            elif self.path == "/api/logout":
                cookie = f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax; Secure"
                self._json({"ok": True}, headers=[("Set-Cookie", cookie)])
            elif self.path == "/api/add":
                data = self._body()
                text = (data.get("text") or "").strip()
                if not text:
                    self._json({"error": "text required"}, code=400)
                    return
                memory = self._mem()
                lang = data.get("language") or memory.config["language"]
                try:
                    explanation = llm.explain(text, lang)
                except llm.LLMNotConfigured:
                    explanation = "(Set ANTHROPIC_API_KEY for explanations.)"
                word = memory.add(Word(text=text, explanation=explanation, language=lang))
                self._msave(memory)
                self._json(word.to_dict())
            elif self.path == "/api/show-me":
                memory = self._mem()
                prepared = daily.show_me(memory)
                self._msave(memory)
                self._json(prepared)
            elif self.path == "/api/keep-reading":
                memory = self._mem()
                prepared = daily.keep_reading(memory)
                self._msave(memory)
                self._json(prepared)
            elif self.path == "/api/love":
                memory = self._mem()
                story = memory.love_current()
                self._msave(memory)
                self._json(story)
            elif self.path == "/api/unlove":
                story_id = (self._body().get("id") or "").strip()
                memory = self._mem()
                removed = memory.unlove(story_id)
                self._msave(memory)
                self._json({"removed": removed, "loved": memory.loved})
            elif self.path == "/api/familiarity":
                data = self._body()
                memory = self._mem()
                word = memory.set_familiarity((data.get("text") or "").strip(),
                                              int(data.get("level") or 0))
                if word is None:
                    self._json({"error": "word not in bank"}, code=404)
                    return
                self._msave(memory)
                self._json(word.to_dict())
            elif self.path == "/api/remove-word":
                text = (self._body().get("text") or "").strip()
                memory = self._mem()
                removed = memory.remove(text)
                self._msave(memory)
                self._json({"removed": removed})
            elif self.path == "/api/interests":
                interests = [i.strip() for i in (self._body().get("interests") or [])
                             if isinstance(i, str) and i.strip()]
                if not interests:
                    self._json({"error": "pick at least one interest"}, code=400)
                    return
                memory = self._mem()
                memory.config["interests"] = interests
                self._msave(memory)
                self._json({"interests": interests})
            elif self.path == "/api/check-spelling":
                text = (self._body().get("text") or "").strip()
                if not text:
                    self._json({"error": "text required"}, code=400)
                    return
                try:
                    suggestions = llm.suggest_corrections(text)
                except llm.LLMNotConfigured:
                    suggestions = []  # no LLM -> skip the check
                self._json({"suggestions": suggestions})
            else:
                self._json({"error": "not found"}, code=404)
        except ValueError as e:  # user-fixable (e.g. no story yet)
            self._json({"error": str(e)}, code=400)
        except Exception as e:  # surface errors to the UI
            self._json({"error": str(e)}, code=500)

    def log_message(self, fmt: str, *args) -> None:
        pass  # quiet


def serve(port: Optional[int] = None, host: str = "0.0.0.0") -> None:
    if port is None:
        port = int(os.environ.get("PORT", "8321"))
    if _auth_enabled():
        _session_secret()  # create once up front, not in racing request threads
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"LesMots UI: http://{host}:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBye.")
