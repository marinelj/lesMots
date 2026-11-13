"""``lexrender`` command: create an account, load a lexicon and run the MCP server.

    lexrender init "marine" --link-base https://github.com/<owner>/<repo>/blob/main/lexicon/lexicon.md
    export LEXRENDER_USER_ID=u_...                    # printed once by init
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


def _account(store: Store, user_id: str | None) -> str:
    # LEXRENDER_TOKEN is the older name of the same credential
    user_id = (user_id or os.environ.get("LEXRENDER_USER_ID")
               or os.environ.get("LEXRENDER_TOKEN", ""))
    if not user_id:
        raise SystemExit("set LEXRENDER_USER_ID or pass --user-id")
    account_id = store.account_for_user_id(user_id)
    if account_id is None:
        raise SystemExit("unknown user_id")
    return account_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lexrender", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=None,
                        help="SQLite file (default: $LEXRENDER_DB)")
    parser.add_argument("--user-id", dest="user_id",
                        help="your user_id, the only login (default: $LEXRENDER_USER_ID)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init", help="create an account and print its user_id")
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
            _, user_id = store.create_account(args.name, args.link_base)
            print(f"user_id: {user_id}\n"
                  "This user_id is your only login. Keep it private: anyone who has it can "
                  "read and change your lexicon. It is shown only once.")
        elif args.cmd == "import":
            result = store.import_markdown(_account(store, args.user_id),
                                           args.path.read_text(encoding="utf-8"))
            print(json.dumps(result))
        elif args.cmd == "export":
            sys.stdout.write(store.export_markdown(_account(store, args.user_id)))
        elif args.cmd == "stats":
            acct = _account(store, args.user_id)
            counts = store.exposure_counts(acct)
            print(json.dumps({"version": store.version(acct),
                              "entries": len(store.entries(acct)),
                              "exposures": sum(counts.values()),
                              "entries_exposed": len(counts)}))
        elif args.cmd == "set-link-base":
            store.set_link_base(_account(store, args.user_id), args.url)
        elif args.cmd == "serve":
            try:
                from .server import build_server
            except ImportError:
                raise SystemExit("the MCP server needs: pip install 'lesmots[mcp]'")
            if args.http:
                public = args.public_url or f"http://{args.host}:{args.port}"
                build_server(store, public_url=public).run(
                    "streamable-http", host=args.host, port=args.port)
            else:
                build_server(store, account_id=_account(store, args.user_id)).run()
    except LexiconError as exc:
        raise SystemExit(str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
