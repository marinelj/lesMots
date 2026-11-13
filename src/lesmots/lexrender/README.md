# Lexicon Render (MCP server)

Lexicon Render makes an AI agent write in your private lexicon. The agent links each
phrase it used to that phrase's entry, then reports which entries it used so you can
track exposure.

This is the first version of the MCP server, step 2 of the product plan. It runs in
**Local rewrite** mode: the agent downloads the lexicon index and rewrites its own answer.
Only entry ids, a time and a place come back to the server. The answer text never does.

## Tools

| Tool | What it does |
| --- | --- |
| `get_lexicon(since_version=0)` | The index: one `[id, phrase, anchor]` row per entry, plus `version` and `link_base`. Pass `since_version` to get only the changes. |
| `get_entry(id_or_phrase)` | One full entry. Accepts an id or the phrase in any word form: "fell back to" finds `fall back to`. |
| `log_exposure(event_id, entry_ids, surface, label="", occurred_at=None, mode="local_rewrite")` | Records which entries appeared in one answer. Retrying with the same `event_id` does not count twice. |
| `add_entry(phrase, definition, example="", section="Added")` | Adds an entry. Refused if the phrase is already there in any word form. |

The server's instructions tell the agent how to render an answer: use the phrases that
fit, link them, then log the exposure.

## Run it

```bash
pip install -e ".[mcp]"
lexrender init marine --link-base https://github.com/marinelj/lesMots/blob/main/lexicon/lexicon.md
export LEXRENDER_TOKEN=lr_...    # printed once by init
lexrender import lexicon/lexicon.md
lexrender stats
```

**Local (stdio).** Add the server to a client such as Claude Code:

```json
{
  "mcpServers": {
    "lexicon": {
      "command": "lexrender",
      "args": ["serve"],
      "env": { "LEXRENDER_TOKEN": "lr_..." }
    }
  }
}
```

**Hosted (HTTP).** Each request must carry `Authorization: Bearer <token>`, and it only
sees that token's account:

```bash
lexrender serve --http --host 0.0.0.0 --port 8765 --public-url https://lexicon.example.com
```

The database is SQLite, stored at `$LEXRENDER_DB` (default `~/.lexrender/lexicon.db`).
Run `lexrender export` to get the lexicon back as Markdown in the same format, ready to
import again.

## Word-form matching

An entry stores its phrase in base form. The first word also matches in its -s, -ed and
-ing forms, and irregular verbs (fall/fell/fallen) come from a built-in table.
Placeholders such as `[topic]`, `someone` or `...` match a few words in the middle of a
phrase and are dropped at either end. Headings longer than eight words, such as the case
excerpts, are not matched.

## Not in this version

- **Full mode.** The server does not rewrite text. This comes later.
- **OAuth sign-in.** Clients use a bearer token for now.
- **Postgres and encryption at rest.** The database is plain SQLite.
- **Private entry pages.** Entry links point to `--link-base`.
- **The dashboard.** This is step 3. `lexrender stats` gives totals.
