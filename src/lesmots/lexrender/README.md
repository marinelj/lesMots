# Lexicon Render (MCP server)

Lexicon Render makes an AI agent write in your private lexicon. The agent links each
phrase it used to that phrase's entry, then reports which entries it used so you can
track exposure.

## Render modes

| Mode | Command | Who renders | What reaches the server | Tier |
| --- | --- | --- | --- | --- |
| local (default) | `/local` | The agent, from the lexicon index | Entry ids, a time and a place | Free |
| remote | `/remote` | The server's model, through `render` | The draft answer, processed in memory and never stored | Paid |

If remote rendering can't run (no model configured, a network error, an odd reply),
`render` returns the text unchanged with `fallback: "local"`, and the agent renders
it itself. The answer is never blocked.

The server's model comes from the same settings as the rest of the app: set
`ANTHROPIC_API_KEY`, or `LESMOTS_API_KEY` + `LESMOTS_API_BASE` + `LESMOTS_MODEL` for
any OpenAI-compatible provider.

## Commands

The commands are MCP prompts. Claude Code shows them as `/mcp__lexicon__<name>`, where
`lexicon` is the server's name in your MCP config.

| Command | What it does |
| --- | --- |
| `add <phrase>` | The agent drafts a definition, an example and synonyms, shows them to you, and saves the entry once you approve. |
| `load` | Loads the latest lexicon (version, entries and synonyms) into the conversation. |
| `local` | Switches the account to local render mode. |
| `remote` | Switches the account to remote render mode. |

## Synonyms

An entry can list synonyms, either as a line in `lexicon.md`
(`* **Synonyms**: refined, elaborate, polished`), through `add_entry`, or through
`set_synonyms`. When an answer contains a synonym, the renderer decides from the context
whether the entry's own phrase fits. If it does, it swaps the synonym for the phrase,
links it to the entry, and records the synonym it replaced in the exposure log (`via`).
Synonyms match in any word form, the same way phrases do.

## Tools

| Tool | What it does |
| --- | --- |
| `get_lexicon(since_version=0)` | The index: one `[id, phrase, anchor, synonyms]` row per entry, plus `version`, `link_base` and `render_mode`. |
| `get_entry(id_or_phrase)` | One full entry. Accepts an id or the phrase in any word form: "fell back to" finds `fall back to`. |
| `log_exposure(event_id, entry_ids, surface, label="", occurred_at=None, mode="local_rewrite", via=None)` | Records which entries appeared in one answer. Retrying with the same `event_id` does not count twice. |
| `add_entry(phrase, definition, example="", section="Added", synonyms=None)` | Adds an entry. Refused if the phrase is already there in any word form. |
| `set_synonyms(id_or_phrase, synonyms)` | Replaces an entry's synonyms. |
| `set_render_mode(mode)` | Sets the mode to `local` or `remote`. |
| `render(text, surface, label="")` | Remote mode: renders the answer, links the phrases and logs the exposures. |

## Run it

```bash
pip install -e ".[mcp]"
lexrender init marine --link-base https://github.com/marinelj/lesMots/blob/main/lexicon/lexicon.md
export LEXRENDER_USER_ID=u_...   # printed once by init: your only login, keep it private
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
      "env": { "LEXRENDER_USER_ID": "u_..." }
    }
  }
}
```

**Hosted (HTTP).** Each request must carry `Authorization: Bearer <user_id>`, and it only
sees that user's account:

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

- **OAuth sign-in.** The user_id is the only credential for now.
- **Postgres and encryption at rest.** The database is plain SQLite.
- **Private entry pages.** Entry links point to `--link-base`.
- **The dashboard.** This is step 3. `lexrender stats` gives totals.
