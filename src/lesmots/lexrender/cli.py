"""``lexrender`` command: sign in, load a lexicon and run the MCP server.

Your account is your LesMots app user_id. Sign in to the app and open /api/lexicon-token to
get a sign-in token (valid 30 days); the first sign-in creates the account.

    export LEXRENDER_SESSION=<token from /api/lexicon-token>
    lexrender whoami
    lexrender set-link-base https://github.com/<owner>/<repo>/blob/main/lexicon/lexicon.md
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

from .signin import verify_sign_in
from .store import LexiconError, Store


def default_db() -> Path:
    return Path(os.environ.get("LEXRENDER_DB", Path.home() / ".lexrender" / "lexicon.db"))


def _account(store: Store, session: str | None) -> str:
    token = session or os.environ.get("LEXRENDER_SESSION", "")
    if not token:
        raise SystemExit("sign in to the LesMots app, then set LEXRENDER_SESSION to the token "
                         "from /api/lexicon-token (or pass --session)")
    app_uid = verify_sign_in(token)
    if app_uid is None:
        raise SystemExit("sign-in token is invalid or expired: get a new one from the app")
    return store.account_for_app_user(app_uid)  # type: ignore[return-value]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lexrender", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", type=Path, default=None,
                        help="SQLite file (default: $LEXRENDER_DB)")
    parser.add_argument("--session",
                        help="LesMots sign-in token (default: $LEXRENDER_SESSION)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("whoami", help="your LesMots user_id and lexicon account")

    p = sub.add_parser("import", help="load a lexicon Markdown file (replaces the lexicon)")
    p.add_argument("path", type=Path)

    sub.add_parser("export", help="print the lexicon as Markdown")
    sub.add_parser("stats", help="entry count, version and exposure totals")

    p = sub.add_parser("set-link-base", help="change the URL entry links point to")
    p.add_argument("url")

    p = sub.add_parser("set-plan", help="operator: put a LesMots user_id on a plan")
    p.add_argument("app_user_id")
    p.add_argument("plan", choices=["free", "paid"])

    p = sub.add_parser("serve", help="run the MCP server")
    p.add_argument("--http", action="store_true", help="streamable HTTP instead of stdio")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--public-url", default="", help="the URL clients reach (HTTP mode)")

    args = parser.parse_args(argv)
    store = Store(args.db or default_db())

    try:
        if args.cmd == "whoami":
            acct = _account(store, args.session)
            row = store._account(acct)
            print(json.dumps({"user_id": row["app_uid"], "account": acct,
                              "entries": len(store.entries(acct))}))
        elif args.cmd == "import":
            result = store.import_markdown(_account(store, args.session),
                                           args.path.read_text(encoding="utf-8"))
            print(json.dumps(result))
        elif args.cmd == "export":
            sys.stdout.write(store.export_markdown(_account(store, args.session)))
        elif args.cmd == "stats":
            acct = _account(store, args.session)
            counts = store.exposure_counts(acct)
            print(json.dumps({"version": store.version(acct),
                              "entries": len(store.entries(acct)),
                              "exposures": sum(counts.values()),
                              "entries_exposed": len(counts)}))
        elif args.cmd == "set-link-base":
            store.set_link_base(_account(store, args.session), args.url)
        elif args.cmd == "set-plan":
            acct = store.account_for_app_user(args.app_user_id)
            print(json.dumps({"user_id": args.app_user_id,
                              "plan": store.set_plan(acct, args.plan)}))  # type: ignore[arg-type]
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
                build_server(store, account_id=_account(store, args.session)).run()
    except LexiconError as exc:
        raise SystemExit(str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
