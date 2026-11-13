---
description: Draft a new lexicon entry and save it once you approve it
argument-hint: <phrase>
---

Draft a lexicon entry for "$ARGUMENTS" with the lexicon MCP server.

1. Call the server's `get_entry` tool with the phrase. If an entry already matches (in any word form), show it and stop.
2. Otherwise draft:
   - definition: one plain sentence
   - example: one sentence that uses it naturally, in a software or work context
   - synonyms: 3 to 6 common words or phrases this entry can replace when the context fits
   - section: the best fit among the lexicon's sections, or "Added"
3. Show the draft and wait for the user's OK. Change it if they ask.
4. Only after they approve, call `add_entry` with those fields and confirm the new entry's id.

Never call `add_entry` before the user approves the draft.
