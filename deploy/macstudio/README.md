# Lexicon Render on the Mac Studio

Two services run here, next to `/codex` and `/grokbot`, and share one env file
(`~/.lexrender/env`) and one session secret:

- **Lexicon Render** at `https://marine-api.tunnel.tungo.cn/lesmots` (launchd `com.lesmots.lexrender`, port 8765)
- **The LesMots web app** at `https://marine-api.tunnel.tungo.cn/lesmots-app/` (launchd `com.lesmots.web`, port 8321).
  Users sign in here with Google, then use *Copy sign-in token* to connect an MCP client.

| Path | What |
|---|---|
| `/lesmots/mcp` | MCP endpoint (streamable HTTP, `Authorization: Bearer <LesMots sign-in token>`) |
| `/lesmots/dashboard` | Dashboard page (`?demo=1` for sample data) |
| `/lesmots/api/dashboard/*` | Dashboard data (paid plan) |
| `/lesmots/healthz` | Health check, no auth |

## 1. Install

```zsh
python3 -m venv ~/.lexrender/venv
~/.lexrender/venv/bin/pip install "lesmots[mcp] @ git+https://github.com/marinelj/lesMots@main"
mkdir -p ~/.lexrender && cp deploy/macstudio/env.example ~/.lexrender/env && chmod 600 ~/.lexrender/env
```

Fill in `~/.lexrender/env`. Generate `LESMOTS_SESSION_SECRET` once, straight into the file, and
never print it: delete the placeholder line, then
`python3 -c 'import secrets; print("LESMOTS_SESSION_SECRET=" + secrets.token_hex(32))' >> ~/.lexrender/env`.
If the LesMots app already runs somewhere else, use that app's secret instead, or its sign-ins won't verify.

## 2. Run as services

```zsh
cp deploy/macstudio/com.lesmots.lexrender.plist deploy/macstudio/com.lesmots.web.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lesmots.lexrender.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lesmots.web.plist
curl -s http://127.0.0.1:8765/healthz                  # {"ok": true, "service": "lexicon-render"}
curl -s http://127.0.0.1:8321/lesmots-app/api/auth-config   # {"client_id": "...", ...}
```

Restart after an upgrade: `launchctl kickstart -k gui/$(id -u)/com.lesmots.lexrender` (and `com.lesmots.web`).

## 3. Route `/lesmots` and `/lesmots-app` on the tunnel

The gateway keeps a registry of APIs, each with an enabled switch; an unregistered path
returns 404 "No enabled API is registered at this path". Register and enable both, the same
way `/codex` and `/grokbot` are registered:

- `/lesmots` and everything under it → `http://127.0.0.1:8765`
- `/lesmots-app` and everything under it → `http://127.0.0.1:8321`


Add a route on the same gateway that serves `/codex` and `/grokbot`: forward
`/lesmots` and everything under it to `http://127.0.0.1:8765`. Stripping the
`/lesmots` prefix or keeping it both work. Keep the `Host` header as
`marine-api.tunnel.tungo.cn` (the MCP endpoint rejects other hosts), and don't buffer
responses (MCP streams).

Check from outside:

```zsh
curl -s https://marine-api.tunnel.tungo.cn/lesmots/healthz
curl -s -o /dev/null -w "%{http_code}\n" https://marine-api.tunnel.tungo.cn/lesmots/mcp   # 401 without a token
```

## 4. Put the owner on the paid plan

```zsh
set -a; source ~/.lexrender/env; set +a
~/.lexrender/venv/bin/lexrender set-plan <LesMots user_id> paid
```
