---
description: Switch to local render mode (your agent renders; answer text stays on your machine)
---

Call the lexicon MCP server's `set_render_mode` tool with `mode: "local"`, then tell the user in one line that local render is on.

From now on, before you show an answer: write it with the lexicon phrases that fit naturally, render each phrase you used as a Markdown link to its entry, and call `log_exposure` with a new event id and the entry ids. Never send the answer text to the server.
