# Lexicon Render plugin for Claude Code

Makes Claude write with your own lexicon. It uses the phrases you're learning, links each one
to its entry in your lexicon bank, and counts every use for the dashboard.

## Install

```bash
pip install 'lesmots[mcp]'          # provides the lexrender command
export LEXRENDER_SESSION=<token>    # from "Copy sign-in token" in the LesMots app
```

In Claude Code:

```
/plugin marketplace add marinelj/lesMots
/plugin install lexicon-render@lesmots
```

## Commands

| Command | What it does |
|---|---|
| `/add <phrase>` | Drafts an entry (definition, example, synonyms) and saves it once you approve. |
| `/load` | Loads the latest lexicon and tells you its version, size and render mode. |
| `/local` | Local render: Claude rewrites and links; your answer text never leaves your machine. |
| `/remote` | Remote render (paid plan): the server rewrites and links. Falls back to local if it can't. |

If another plugin also has an `/add`, use the full name, `/lexicon-render:add`.

## Hosted server

The plugin runs the server locally over stdio by default. To use a hosted server instead,
replace `.mcp.json` with:

```json
{
  "mcpServers": {
    "lexicon": {
      "type": "http",
      "url": "https://<your-host>/mcp",
      "headers": { "Authorization": "Bearer ${LEXRENDER_SESSION}" }
    }
  }
}
```
