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
`lexicon` is the server's name in your MCP config. With the [Claude Code plugin](../../../plugins/lexicon-render/)
they are short: `/add`, `/load`, `/local` and `/remote`.

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

## Sign in

Your lexicon account is your LesMots app user_id, the same one the app gives you when you
sign in with Google or WeChat. You prove it by signing in to the app; nothing else logs in.

1. Sign in to the LesMots app.
2. In the app's **Lexicon Render** section, click **Copy sign-in token**. The token is
   valid for 30 days; the export command below the button is ready to paste into a terminal.
3. Give that token to the CLI or your MCP client. The first sign-in creates an empty account.

The app and the Lexicon Render server must share `LESMOTS_SESSION_SECRET` (or run on the
same LesMots data directory), since the server checks the app's signature on the token.

## Run it

```bash
pip install -e ".[mcp]"
export LEXRENDER_SESSION=<token from /api/lexicon-token>
lexrender whoami                 # your user_id and account
lexrender set-link-base https://github.com/marinelj/lesMots/blob/main/lexicon/lexicon.md
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
      "env": { "LEXRENDER_SESSION": "<token>" }
    }
  }
}
```

**Hosted (HTTP).** Each request must carry `Authorization: Bearer <token>`, a LesMots
sign-in token, and it only sees that user's account:

```bash
lexrender serve --http --port 8765 --public-url https://marine-api.tunnel.tungo.cn/lesmots
```

The public URL may have a path prefix (`/lesmots` here): the MCP endpoint is then
`<public-url>/mcp`, the dashboard `<public-url>/dashboard` and the health check
`<public-url>/healthz`. Only the public host passes the MCP Host check. The Mac Studio
setup is in [deploy/macstudio](../../../deploy/macstudio/README.md).

The database is SQLite, stored at `$LEXRENDER_DB` (default `~/.lexrender/lexicon.db`).
Run `lexrender export` to get the lexicon back as Markdown in the same format, ready to
import again.

## Plans and the dashboard

Accounts are on the `free` plan (local render and the lexicon bank) or the `paid` plan,
which adds remote render and the dashboard. On the free plan, `/remote` and `render` say
remote render is paid and the agent keeps rendering locally. The operator sets a plan with
`lexrender set-plan <LesMots user_id> paid`; billing comes later.

The hosted server also serves the dashboard. `GET /dashboard` is the page, and the data
comes from these endpoints, each needing `Authorization: Bearer <token>` and a paid account
(401 when signed out, 402 on the free plan):

| Endpoint | Returns |
|---|---|
| `/api/dashboard/summary` | entries, version, total exposures, entries exposed, coverage by section |
| `/api/dashboard/entries?sort=exposures\|recent\|phrase` | per entry: exposures, last seen, synonym hits |
| `/api/dashboard/timeline?days=30` | exposures per UTC day, zero-filled |
| `/api/dashboard/surfaces` | exposures per place, such as Claude Code or Notion |
| `/api/dashboard/entry/<id>` | the entry and its exposure history (when, where, label, synonym replaced) |

## Word-form matching

An entry stores its phrase in base form. The first word also matches in its -s, -ed and
-ing forms, and irregular verbs (fall/fell/fallen) come from a built-in table.
Placeholders such as `[topic]`, `someone` or `...` match a few words in the middle of a
phrase and are dropped at either end. Headings longer than eight words, such as the case
excerpts, are not matched.

## Not in this version

- **OAuth sign-in.** Clients paste a 30-day token from the app; an OAuth flow that fetches
  it for them comes later.
- **Postgres and encryption at rest.** The database is plain SQLite.
- **Private entry pages.** Entry links point to `--link-base`.

## Dashboard page

The [static dashboard](static/dashboard.html) shows totals, a 30-day exposure chart,
sortable entries, exposure histories, and surface and section breakdowns. It connects
to the same-host `/api/dashboard/*` endpoints with the user's bearer token. See the
[dashboard guide](../../../docs/dashboard.md) for its API contract, fixture demo,
and browser checks.
