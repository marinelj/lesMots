"""The Lexicon Render MCP server.

Tools: ``get_lexicon``, ``get_entry``, ``log_exposure``, ``add_entry``. The default mode is
Local rewrite: the agent downloads the lexicon index, writes its answer with those phrases
and links them itself. Only entry ids, a time and a place come back to the server; the
answer text never does.

Two transports:

* stdio, for a local client: the account comes from the ``LEXRENDER_TOKEN`` environment
  variable, checked once at start-up.
* streamable HTTP, for a hosted service: every request carries ``Authorization: Bearer
  <token>`` and is served for that token's account only.

Needs the optional ``mcp`` dependency (``pip install 'lesmots[mcp]'``, Python 3.10+).
"""

from __future__ import annotations

from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .store import LexiconError, Store

INSTRUCTIONS = """\
This server holds the user's private lexicon: words, phrases and sentence frames they want
to see used in your writing.

Before you show the user an answer:
1. Call get_lexicon once per conversation (pass since_version on later calls to fetch only
   changes). It returns one row per entry: [id, phrase, anchor]; an entry's link is
   link_base + "#" + anchor.
2. Write the answer using every phrase that fits naturally. Inflect verbs to fit the
   sentence ("fall back to" may appear as "fell back to"). Do not force a phrase that does
   not fit. Call get_entry when you need an entry's definition or examples.
3. Render each phrase you used as a Markdown link to its entry: [fell back to](link).
4. Call log_exposure once per answer with a new event_id and the ids of the entries you
   used. Never send the answer text to this server.
"""


class TokenVerifier:
    """Maps a bearer token to its account. The account id travels as the token's client_id."""

    def __init__(self, store: Store):
        self.store = store

    async def verify_token(self, token: str) -> AccessToken | None:
        account_id = self.store.account_for_token(token)
        if account_id is None:
            return None
        return AccessToken(token=token, client_id=account_id, scopes=["lexicon"])


def build_server(store: Store, account_id: str | None = None,
                 public_url: str | None = None) -> MCPServer:
    """``account_id`` set = a single-account (stdio) server. Otherwise ``public_url`` is
    required and each HTTP request is authenticated by its bearer token."""
    if account_id is None and not public_url:
        raise ValueError("either account_id (stdio) or public_url (HTTP) is required")

    kwargs: dict[str, Any] = {}
    if account_id is None:
        base = public_url.rstrip("/")  # type: ignore[union-attr]
        kwargs = {
            "token_verifier": TokenVerifier(store),
            # Tokens are opaque and issued by this server for this server only, so the
            # verifier itself is the audience check.
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

    @server.tool()
    def get_lexicon(since_version: int = 0) -> dict[str, Any]:
        """The lexicon index: one [id, phrase, anchor] row per active entry, plus the
        version and link_base. With since_version, only entries changed after that version;
        their ids are listed under `archived` when they were removed."""
        acct = current_account()

        def run() -> dict[str, Any]:
            entries = store.entries(acct, since_version=since_version,
                                    include_archived=since_version > 0)
            link_base = store._account(acct)["link_base"]
            return {
                "version": store.version(acct),
                "link_base": link_base,
                "entries": [[e.id, e.phrase, e.anchor] for e in entries if e.status == "active"],
                "archived": [e.id for e in entries if e.status != "active"],
            }
        return guarded(run)

    @server.tool()
    def get_entry(id_or_phrase: str) -> dict[str, Any]:
        """One full entry: definition, usage notes, examples and link. Accepts an entry id
        or the phrase in any word form ("fell back to" finds "fall back to")."""
        acct = current_account()
        entry = guarded(lambda: store.lookup(acct, id_or_phrase))
        if entry is None:
            raise ToolError(f"no entry matches '{id_or_phrase}'")
        return entry.full()

    @server.tool()
    def log_exposure(event_id: str, entry_ids: list[str], surface: str, label: str = "",
                     occurred_at: str | None = None,
                     mode: str = "local_rewrite") -> dict[str, Any]:
        """Record which entries appeared in one rendered answer. event_id: a new random id
        per answer (retries with the same id are not counted twice). surface: where the
        answer was shown, e.g. 'Claude Code'. label: an optional context such as a project
        name. occurred_at: ISO 8601, defaults to now. Send ids only, never the answer text."""
        acct = current_account()
        return guarded(lambda: store.log_exposures(acct, event_id, entry_ids, surface, label,
                                                   occurred_at, mode))

    @server.tool()
    def add_entry(phrase: str, definition: str, example: str = "",
                  section: str = "Added") -> dict[str, Any]:
        """Add a new entry to the lexicon. Refused if the phrase, in any word form, is
        already there. Show the drafted entry to the user and get their OK first."""
        acct = current_account()
        return guarded(lambda: store.add_entry(acct, phrase, definition, example, section)).full()

    return server
