---
description: Switch to remote render mode (the server renders; paid plan)
---

Call the lexicon MCP server's `set_render_mode` tool with `mode: "remote"`.

- If it refuses because remote render is part of the paid plan, tell the user that in one line and keep using local render.
- Otherwise tell the user in one line that remote render is on. From now on, send each draft answer to the `render` tool and show the `rendered` text it returns. If the result has `fallback: "local"`, render that answer yourself with the local rules.
