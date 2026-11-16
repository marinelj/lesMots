---
description: Load the latest lexicon into this conversation
---

Load the user's lexicon from the lexicon MCP server and use it for the rest of this conversation.

1. Use the server's `load` prompt if your client exposes it; otherwise call `get_lexicon`.
2. Tell the user, in one line, the lexicon version, the number of entries and the render mode.
3. From now on follow the render mode's rules from the server's instructions for every answer: in local mode, write with the phrases that fit, link each one and call `log_exposure`; in remote mode, send your draft to `render` and show what it returns.
