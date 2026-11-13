"""``lexrender`` command: create an account, load a lexicon and run the MCP server.

    lexrender init "marine" --link-base https://github.com/<owner>/<repo>/blob/main/lexicon/lexicon.md
    export LEXRENDER_TOKEN=lr_...                     # printed once by init
    lexrender import lexicon/lexicon.md
    lexrender serve                                   # stdio, for a local MCP client
    lexrender serve --http --public-url https://lexicon.example.com

The database lives at $LEXRENDER_DB (default ~/.lexrender/lexicon.db).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .store import LexiconError, Store


def default_db() -> Path:
    return Path(os.environ.get("LEXRENDER_DB", Path.home() / ".lexrender" / "lexicon.db"))


def _account(store: Store, token: str | None) -> str:
    token = token or os.environ.get("LEXRENDER_TOKEN", "")
    if not token:
        raise SystemExit("set LEXRENDER_TOKEN or pass --token")
    account_id = store.account_for_token(token)
    if account_id is None:
        raise SystemExit("unknown token")
    return account_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lexrender", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=None,
                        help="SQLite file (default: $LEXRENDER_DB)")
    parser.add_argument("--token", help="account token (default: $LEXRENDER_TOKEN)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="create an account and print its token")
    p.add_argument("name")
    p.add_argument("--link-base", default="", help="URL that entry anchors are appended to")

    p = sub.add_parser("import", help="load a lexicon Markdown file (replaces the lexicon)")
    p.add_argument("path", type=Path)

    sub.add_parser("export", help="print the lexicon as Markdown")
    sub.add_parser("stats", help="entry count, version and exposure totals")

    p = sub.add_parser("set-link-base", help="change the URL entry links point to")
    p.add_argument("url")

    p = sub.add_parser("serve", help="run the MCP server")
    p.add_argument("--http", action="store_true", help="streamable HTTP instead of stdio")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--public-url", default="", help="the URL clients reach (HTTP mode)")

    args = parser.parse_args(argv)
    store = Store(args.db or default_db())

    try:
        if args.cmd == "init":
            account_id, token = store.create_account(args.name, args.link_base)
            print(f"account: {account_id}\ntoken:   {token}\n"
                  "Keep the token secret; it is shown only once.")
        elif args.cmd == "import":
            result = store.import_markdown(_account(store, args.token),
                                           args.path.read_text(encoding="utf-8"))
            print(json.dumps(result))
        elif args.cmd == "export":
            sys.stdout.write(store.export_markdown(_account(store, args.token)))
        elif args.cmd == "stats":
            acct = _account(store, args.token)
            counts = store.exposure_counts(acct)
            print(json.dumps({"version": store.version(acct),
                              "entries": len(store.entries(acct)),
                              "exposures": sum(counts.values()),
                              "entries_exposed": len(counts)}))
        elif args.cmd == "set-link-base":
            store.set_link_base(_account(store, args.token), args.url)
        elif args.cmd == "serve":
            try:
                from .server import build_server
            except ImportError:
                raise SystemExit("the MCP server needs: pip install 'lesmots[mcp]' (Python 3.10+)")
            if args.http:
                public = args.public_url or f"http://{args.host}:{args.port}"
                build_server(store, public_url=public).run(
                    "streamable-http", host=args.host, port=args.port)
            else:
                build_server(store, account_id=_account(store, args.token)).run()
    except LexiconError as exc:
        raise SystemExit(str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
