# Lexicon Render on the Mac Studio

Public URL: `https://marine-api.tunnel.tungo.cn/lesmots`, next to `/codex` and `/grokbot`.

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

Fill in `~/.lexrender/env`. `LESMOTS_SESSION_SECRET` must match the LesMots app's secret.

## 2. Run as a service

```zsh
cp deploy/macstudio/com.lesmots.lexrender.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.lesmots.lexrender.plist
curl -s http://127.0.0.1:8765/healthz        # {"ok": true, "service": "lexicon-render"}
```

Restart after an upgrade: `launchctl kickstart -k gui/$(id -u)/com.lesmots.lexrender`.

## 3. Route `/lesmots` on the tunnel

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
