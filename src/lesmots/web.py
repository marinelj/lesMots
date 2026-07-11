"""LesMots web UI server (stdlib http.server — zero dependencies).

Endpoints:
    GET  /            -> HTML UI
    GET  /api/words   -> word bank table data
    POST /api/add     -> {"text": ...} add word + explanation
    POST /api/show-me -> prepared content; updates exposure counts
    POST /api/keep-reading -> extend the current story; updates exposure counts
    POST /api/love    -> persist the current story to the loved list
"""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from lesmots import llm, daily
from lesmots.memory import Memory
from lesmots.models import Word

UI_PATH = Path(__file__).parent / "ui.html"


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
        else:
            self._json({"error": "not found"}, code=404)

    def do_POST(self) -> None:  # noqa: N802
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
