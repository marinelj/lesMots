"""LesMots web UI server (stdlib http.server — zero dependencies).

Endpoints:
    GET  /            -> HTML UI
    GET  /api/words   -> word bank table data
    GET  /api/loved   -> loved stories list
    GET  /api/config  -> {"language", "interests"}
    POST /api/add     -> {"text": ...} add word + explanation
    POST /api/check-spelling -> {"text": ...} -> {"suggestions": [...]}
    POST /api/show-me -> prepared content; updates exposure counts
    POST /api/keep-reading -> extend the current story; updates exposure counts
    POST /api/love    -> persist the current story to the loved list
    POST /api/unlove  -> {"id": ...} remove a loved story
    POST /api/familiarity -> {"text": ..., "level": 1-5} manual familiarity
    POST /api/remove-word -> {"text": ...} delete a bank entry
    POST /api/interests   -> {"interests": [...]} update feed topics
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from lesmots import llm, daily
from lesmots.memory import Memory
from lesmots.models import Word

UI_PATH = Path(__file__).parent / "ui.html"

# The server is threaded but every POST does load-modify-save on one JSON
# file; without serialization, rapid clicks (e.g. familiarity dots) race
# and lose updates (issue #15).
_WRITE_LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str = "application/json", code: int = 200) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200) -> None:
        self._send(json.dumps(obj, ensure_ascii=False).encode(), code=code)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self) -> None:  # noqa: N802
        if self.path in ("/", "/index.html"):
            self._send(UI_PATH.read_bytes(), ctype="text/html")
        elif self.path == "/api/words":
            memory = Memory.load()
            self._json([w.to_dict() for w in memory.words])
        elif self.path == "/api/loved":
            memory = Memory.load()
            self._json(memory.loved)
        elif self.path == "/api/config":
            memory = Memory.load()
            self._json({"language": memory.config["language"],
                        "interests": memory.config["interests"]})
        else:
            self._json({"error": "not found"}, code=404)

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/api/check-spelling":  # read-only, may hold an LLM call
            self._do_post()
            return
        with _WRITE_LOCK:
            self._do_post()

    def _do_post(self) -> None:
        try:
            if self.path == "/api/add":
                data = self._body()
                text = (data.get("text") or "").strip()
                if not text:
                    self._json({"error": "text required"}, code=400)
                    return
                memory = Memory.load()
                lang = data.get("language") or memory.config["language"]
                try:
                    explanation = llm.explain(text, lang)
                except llm.LLMNotConfigured:
                    explanation = "(Set ANTHROPIC_API_KEY for explanations.)"
                word = memory.add(Word(text=text, explanation=explanation, language=lang))
                memory.save()
                self._json(word.to_dict())
            elif self.path == "/api/show-me":
                memory = Memory.load()
                prepared = daily.show_me(memory)
                memory.save()
                self._json(prepared)
            elif self.path == "/api/keep-reading":
                memory = Memory.load()
                prepared = daily.keep_reading(memory)
                memory.save()
                self._json(prepared)
            elif self.path == "/api/love":
                memory = Memory.load()
                story = memory.love_current()
                memory.save()
                self._json(story)
            elif self.path == "/api/unlove":
                story_id = (self._body().get("id") or "").strip()
                memory = Memory.load()
                removed = memory.unlove(story_id)
                memory.save()
                self._json({"removed": removed, "loved": memory.loved})
            elif self.path == "/api/familiarity":
                data = self._body()
                memory = Memory.load()
                word = memory.set_familiarity((data.get("text") or "").strip(),
                                              int(data.get("level") or 0))
                if word is None:
                    self._json({"error": "word not in bank"}, code=404)
                    return
                memory.save()
                self._json(word.to_dict())
            elif self.path == "/api/remove-word":
                text = (self._body().get("text") or "").strip()
                memory = Memory.load()
                removed = memory.remove(text)
                memory.save()
                self._json({"removed": removed})
            elif self.path == "/api/interests":
                interests = [i.strip() for i in (self._body().get("interests") or [])
                             if isinstance(i, str) and i.strip()]
                if not interests:
                    self._json({"error": "pick at least one interest"}, code=400)
                    return
                memory = Memory.load()
                memory.config["interests"] = interests
                memory.save()
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
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"LesMots UI: http://{host}:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nBye.")
