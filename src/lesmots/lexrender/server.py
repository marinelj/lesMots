"""The Lexicon Render MCP server.

Two render modes, switched per account with the ``/local`` and ``/remote`` commands:

* local (default, free tier): the agent downloads the lexicon index, writes its answer with
  those phrases and links them itself. Only entry ids, a time and a place come back to the
  server; the answer text never does.
* remote (paid tier, "MCP render"): the agent sends its draft to ``render`` and the server's
  model rewrites and links it. The text is processed in memory and never stored. If the
  server can't render, it says so and the agent falls back to local rendering.

Commands (MCP prompts): ``add``, ``load``, ``local``, ``remote``. Clients show them as slash
commands, e.g. ``/mcp__lexicon__add`` in Claude Code.

Two transports:

* stdio, for a local client: the account comes from the ``LEXRENDER_SESSION`` sign-in token,
  checked once at start-up.
* streamable HTTP, for a hosted service: every request carries ``Authorization: Bearer
  <token>``, a sign-in token from the LesMots app, and is served for that user's account.

Accounts are keyed by the LesMots app user_id (Google or WeChat). Signing in to the app is
the only way to prove it; the first sign-in creates an empty account (see ``signin``).

Needs the optional ``mcp`` dependency (``pip install 'lesmots[mcp]'``).
"""

from __future__ import annotations

from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .render import Complete
from .render import render as server_render
from .signin import VerifySignIn, verify_sign_in
from .store import LexiconError, Store

LOCAL_RULES = """\
Local render mode. Before you show the user an answer:
1. Call get_lexicon once per conversation (pass since_version later to fetch only changes).
   Each row is [id, phrase, anchor, synonyms]; an entry's link is link_base + "#" + anchor.
2. Write the answer using every phrase that fits naturally. Inflect verbs to fit the
   sentence ("fall back to" may appear as "fell back to"). Do not force a phrase that does
   not fit. Call get_entry when you need an entry's definition or examples.
3. Synonyms: when you would write one of an entry's synonyms, judge from the context
   whether the entry's own phrase is appropriate. If it is, use the entry's phrase instead.
4. Render each phrase you used as a Markdown link to its entry: [fell back to](link).
5. Call log_exposure once per answer with a new event_id and the ids of the entries you
   used; for an entry you used in place of a synonym, add it to `via` as
   {entry_id: synonym}. Never send the answer text to this server."""

REMOTE_RULES = """\
Remote render mode. Before you show the user an answer, call render with your full draft,
a surface (e.g. "Claude Code") and an optional label. Show the `rendered` text it returns.
The server links the phrases and logs the exposures, so do not call log_exposure.
If the result has fallback "local", render the answer yourself with the local rules:
call get_lexicon, use the phrases that fit, link them, then call log_exposure."""

INSTRUCTIONS = f"""\
This server holds the user's private lexicon: words, phrases and sentence frames they want
to see used in your writing. get_lexicon tells you the account's render_mode.

{LOCAL_RULES}

{REMOTE_RULES}

Commands the user can run: add (draft and save a new entry), load (load the latest
lexicon), local and remote (switch the render mode)."""


class TokenVerifier:
    """Maps a LesMots sign-in token to the user's account (created on first sign-in). The
    account id travels as the client_id."""

    def __init__(self, store: Store, verify: VerifySignIn = verify_sign_in):
        self.store = store
        self.verify = verify

    async def verify_token(self, token: str) -> AccessToken | None:
        app_uid = self.verify(token)
        account_id = self.store.account_for_app_user(app_uid) if app_uid else None
        if account_id is None:
            return None
        return AccessToken(token=token, client_id=account_id, scopes=["lexicon"])


def build_server(store: Store, account_id: str | None = None,
                 public_url: str | None = None,
                 complete: Complete | None = None,
                 verify: VerifySignIn = verify_sign_in) -> MCPServer:
    """``account_id`` set = a single-account (stdio) server. Otherwise ``public_url`` is
    required and each HTTP request is authenticated by its LesMots sign-in token.
    ``complete`` and ``verify`` override the model and the sign-in check (tests)."""
    if account_id is None and not public_url:
        raise ValueError("either account_id (stdio) or public_url (HTTP) is required")

    kwargs: dict[str, Any] = {}
    if account_id is None:
        base = public_url.rstrip("/")  # type: ignore[union-attr]
        kwargs = {
            "token_verifier": TokenVerifier(store, verify),
            # Tokens are LesMots sessions, checked by their signature, so the verifier
            # itself is the audience check.
            "auth": AuthSettings(issuer_url=base, resource_server_url=f"{base}/mcp",
                                 required_scopes=["lexicon"], validate_token_resource=False),
        }
    server = MCPServer(name="lexicon-render", instructions=INSTRUCTIONS, **kwargs)

    def current_account() -> str:
        if account_id is not None:
            return account_id
        token = get_access_token()
        if token is None:
            raise ToolError("not signed in")
        return token.client_id

    def guarded(fn):
        try:
            return fn()
        except LexiconError as exc:
            raise ToolError(str(exc)) from exc

    def lexicon_index(acct: str, since_version: int = 0) -> dict[str, Any]:
        entries = store.entries(acct, since_version=since_version,
                                include_archived=since_version > 0)
        return {
            "version": store.version(acct),
            "render_mode": store.render_mode(acct),
            "link_base": store._account(acct)["link_base"],
            "entries": [[e.id, e.phrase, e.anchor, e.synonyms]
                        for e in entries if e.status == "active"],
            "archived": [e.id for e in entries if e.status != "active"],
        }

    # ---------- tools ----------

    @server.tool()
    def get_lexicon(since_version: int = 0) -> dict[str, Any]:
        """The lexicon index: one [id, phrase, anchor, synonyms] row per active entry, plus
        the version, link_base and the account's render_mode. With since_version, only
        entries changed after that version; removed ones are listed under `archived`."""
        acct = current_account()
        return guarded(lambda: lexicon_index(acct, since_version))

    @server.tool()
    def get_entry(id_or_phrase: str) -> dict[str, Any]:
        """One full entry: definition, usage notes, examples, synonyms and link. Accepts an
        entry id or the phrase in any word form ("fell back to" finds "fall back to")."""
        acct = current_account()
        entry = guarded(lambda: store.lookup(acct, id_or_phrase))
        if entry is None:
            raise ToolError(f"no entry matches '{id_or_phrase}'")
        return entry.full()

    @server.tool()
    def log_exposure(event_id: str, entry_ids: list[str], surface: str, label: str = "",
                     occurred_at: str | None = None, mode: str = "local_rewrite",
                     via: dict[str, str] | None = None) -> dict[str, Any]:
        """Record which entries appeared in one rendered answer. event_id: a new random id
        per answer (retries with the same id are not counted twice). surface: where the
        answer was shown, e.g. 'Claude Code'. label: an optional context such as a project
        name. occurred_at: ISO 8601, defaults to now. via: {entry_id: synonym} for entries
        used in place of a synonym. Send ids only, never the answer text."""
        acct = current_account()
        return guarded(lambda: store.log_exposures(acct, event_id, entry_ids, surface, label,
                                                   occurred_at, mode, via))

    @server.tool()
    def add_entry(phrase: str, definition: str, example: str = "", section: str = "Added",
                  synonyms: list[str] | None = None) -> dict[str, Any]:
        """Add a new entry to the lexicon. Refused if the phrase, in any word form, is
        already there. synonyms: words the entry's phrase can replace when the context fits.
        Show the drafted entry to the user and get their OK first."""
        acct = current_account()
        return guarded(lambda: store.add_entry(acct, phrase, definition, example, section,
                                               synonyms or [])).full()

    @server.tool()
    def set_synonyms(id_or_phrase: str, synonyms: list[str]) -> dict[str, Any]:
        """Replace an entry's synonyms (an empty list removes them)."""
        acct = current_account()
        return guarded(lambda: store.set_synonyms(acct, id_or_phrase, synonyms)).full()

    @server.tool()
    def set_render_mode(mode: str) -> dict[str, Any]:
        """Switch the account's render mode: 'local' (you render) or 'remote' (the server
        renders through the render tool)."""
        acct = current_account()
        return {"render_mode": guarded(lambda: store.set_render_mode(acct, mode))}

    @server.tool()
    def render(text: str, surface: str, label: str = "") -> dict[str, Any]:
        """Remote mode: rewrite a draft answer with the user's lexicon, link the phrases used
        and log the exposures. Returns `rendered`, the entry ids `used` and `via` (synonyms
        replaced). If `fallback` is "local", show nothing from it and render locally."""
        acct = current_account()
        return guarded(lambda: server_render(store, acct, text, surface, label, complete))

    # ---------- commands (prompts) ----------

    @server.prompt(name="add", description="Draft a new lexicon entry and save it after you OK it")
    def add_command(phrase: str) -> str:
        return (
            f'Draft a lexicon entry for "{phrase}". Check first with get_entry whether it is '
            "already in the lexicon; if it is, show that entry and stop. Otherwise draft:\n"
            "- definition: one plain sentence\n"
            "- example: one sentence that uses it naturally, in a software or work context\n"
            "- synonyms: 3 to 6 common words or phrases this entry can replace when the "
            "context fits\n"
            "- section: the best fit among the lexicon's sections, or 'Added'\n"
            "Show the draft to the user. Only after they approve it, call add_entry with "
            "those fields, then confirm the new entry's id."
        )

    @server.prompt(name="load", description="Load the latest lexicon into this conversation")
    def load_command() -> str:
        acct = current_account()
        index = guarded(lambda: lexicon_index(acct))
        rows = "\n".join(
            f"{eid} | {phrase} | {anchor}" + (f" | synonyms: {', '.join(syns)}" if syns else "")
            for eid, phrase, anchor, syns in index["entries"])
        rules = LOCAL_RULES if index["render_mode"] == "local" else REMOTE_RULES
        return (
            f"Lexicon version {index['version']}, {len(index['entries'])} entries, render "
            f"mode: {index['render_mode']}. Links: {index['link_base'] or '(none set)'}#anchor\n"
            "Tell the user the version, entry count and render mode in one line, then use "
            f"this lexicon for the rest of the conversation.\n\n{rules}\n\n"
            f"id | phrase | anchor | synonyms\n{rows}"
        )

    @server.prompt(name="local", description="Switch to local render mode (you render)")
    def local_command() -> str:
        acct = current_account()
        guarded(lambda: store.set_render_mode(acct, "local"))
        return ("The lexicon is now in local render mode. Tell the user in one line, then "
                f"follow these rules for every answer from now on:\n\n{LOCAL_RULES}")

    @server.prompt(name="remote", description="Switch to remote render mode (the server renders)")
    def remote_command() -> str:
        acct = current_account()
        guarded(lambda: store.set_render_mode(acct, "remote"))
        return ("The lexicon is now in remote render mode. Tell the user in one line, then "
                f"follow these rules for every answer from now on:\n\n{REMOTE_RULES}")

    return server
