"""LesMots command-line interface (the "chat version").

Usage:
    lesmots add <text> [--lang LANGUAGE]     add + explain a word/phrase/sentence
    lesmots show-me                          show prepared content, update exposures
    lesmots daily                            run the daily job (cron-friendly)
    lesmots words                            word bank table
    lesmots config [--lang L] [--interests "a,b"]
    lesmots serve [--port N]                 launch the HTML UI
"""

from __future__ import annotations

import argparse
import sys

from lesmots import __version__, llm, daily
from lesmots.memory import Memory
from lesmots.models import Word


def cmd_add(args: argparse.Namespace) -> int:
    memory = Memory.load()
    lang = args.lang or memory.config["language"]
    try:
        explanation = llm.explain(args.text, lang)
    except llm.LLMNotConfigured:
        explanation = ""
        print("Note: set ANTHROPIC_API_KEY to get explanations.", file=sys.stderr)
    word = memory.add(Word(text=args.text, explanation=explanation, language=lang))
    memory.save()
    print(f"Added [{word.kind}] {word.text}")
    if explanation:
        print(explanation)
    return 0


def cmd_show_me(args: argparse.Namespace) -> int:
    memory = Memory.load()
    prepared = daily.show_me(memory)
    memory.save()
    print("=== Demonstration ===")
    print(prepared["rewritten"])
    print("\n=== Original (no rewrite) ===")
    o = prepared["original"]
    print(f"{o['title']} — {o['source']} ({o['points']} points)\n{o['url']}")
    used = prepared.get("words_used", [])
    print(f"\nWords exposed: {', '.join(used) if used else '(none — add more words)'}")
    return 0


def cmd_daily(args: argparse.Namespace) -> int:
    memory = Memory.load()
    prepared = daily.generate(memory)
    memory.save()
    print(f"Prepared content for {prepared['date']}: "
          f"{prepared['original']['title']} "
          f"(uses {len(prepared['words_used'])} bank word(s))")
    return 0


def cmd_words(args: argparse.Namespace) -> int:
    memory = Memory.load()
    if not memory.words:
        print("Word bank is empty. Add with: lesmots add <text>")
        return 0
    print(f"{'text':30} {'kind':9} {'added':10} {'exposure':8} familiarity")
    for w in sorted(memory.words, key=lambda w: w.added):
        print(f"{w.text[:29]:30} {w.kind:9} {w.added[:10]:10} {w.exposure_count:<8} {w.familiarity}")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    memory = Memory.load()
    if args.lang:
        memory.config["language"] = args.lang
    if args.interests:
        memory.config["interests"] = [i.strip() for i in args.interests.split(",") if i.strip()]
    memory.save()
    print(f"language:  {memory.config['language']}")
    print(f"interests: {', '.join(memory.config['interests'])}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    from lesmots.web import serve
    serve(port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lesmots",
                                description="LesMots — learn words inside content you care about.")
    p.add_argument("--version", action="version", version=f"lesmots {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="add a word/phrase/sentence to the bank")
    add.add_argument("text")
    add.add_argument("--lang", help="explanation language (default from config: Chinese)")
    add.set_defaults(func=cmd_add)

    show = sub.add_parser("show-me", help="show prepared content, update exposure counts")
    show.set_defaults(func=cmd_show_me)

    dly = sub.add_parser("daily", help="run the daily content-preparation job")
    dly.set_defaults(func=cmd_daily)

    words = sub.add_parser("words", help="show the word bank")
    words.set_defaults(func=cmd_words)

    cfg = sub.add_parser("config", help="view/update language and interests")
    cfg.add_argument("--lang")
    cfg.add_argument("--interests", help='comma-separated, e.g. "LLM,AI,robotics"')
    cfg.set_defaults(func=cmd_config)

    srv = sub.add_parser("serve", help="launch the HTML UI")
    srv.add_argument("--port", type=int, default=None,
                     help="port to listen on (default: $PORT or 8321)")
    srv.set_defaults(func=cmd_serve)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
