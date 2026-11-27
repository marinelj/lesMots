"""The naturalness check: would a fluent writer choose each linked phrase anyway?

The structural checks (``evaluate.score``, ``distill.check``) only prove that a marker
names an offered entry and that its words are a form of that phrase. They cannot tell a
phrase that fits from one squeezed in to earn a link ("nudged her to get started with
writing"). Rules cannot judge that, so a model does: the *critic*, any ``(prompt, system)
-> reply`` callable (Codex, Grok, an OpenAI-compatible endpoint).

Two uses:

* the **judge guardrail** (``evaluate.judge``): best-of-N ranks replies by structural
  checks, then by *fewest forced phrases*, and only then by phrase count, so eagerness
  wins only among replies that read naturally;
* the **naturalness pass** in distillation (``distill natural``): the critic undoes forced
  phrases in the teacher's rewrites, restoring the answer's own wording, before export.

The critic sees each phrase's definition and, when the lexicon has one, its "Not for"
note. A critic failure never blocks anything: the reply is treated as having no forced
phrase (fail open), and the failure is reported on stderr.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable

from .render import MARKER

Critic = Callable[[str, str], str]  # (prompt, system) -> reply

CRITIC_SYSTEM = (
    "You are a strict copy editor for a language-learning tool. A rewrite links phrases "
    "from the reader's lexicon. You decide, for each linked phrase, whether a fluent "
    "writer would have chosen it there with no lexicon in sight. Output JSON only."
)

QUESTION = (
    "For each numbered PHRASE, answer: without the lexicon, would a fluent native writer "
    "choose this phrase here? Mark it \"forced\" when it is grammatical but unidiomatic, "
    "adds words without adding meaning, has the wrong sense for the situation (see its "
    "meaning and any \"not for\" note), or changes the meaning. Otherwise mark it \"ok\". "
    "Be strict about \"forced\", but do not mark a phrase forced only because a simpler "
    "word exists: the reader is learning these phrases, and a phrase that fits is wanted."
)

RESTORE = (
    "Then return the REWRITE with every forced phrase undone: put back the ORIGINAL's own "
    "wording for the sentence it was in, and remove its marker. Keep every \"ok\" phrase "
    "and its {{entry-id|words}} marker exactly as it is, and change nothing else."
)


def markers(reply: str) -> list[tuple[str, str]]:
    """The ``(entry id, words)`` of each marker, in order."""
    return [(m.group(1), m.group(2)) for m in MARKER.finditer(reply or "")]


def describe(lex, entry_id: str) -> str:
    entry = lex.by_id.get(entry_id)
    if entry is None:
        return entry_id
    text = f'"{entry.phrase}": {entry.definition[:160]}'
    if getattr(entry, "not_for", ""):
        text += f" Not for: {entry.not_for}"
    return text


def critic_prompt(lex, items: list[tuple[str, str, str]], restore: bool = False) -> str:
    """One prompt for several ``(id, original, rewrite)`` items."""
    shape = ('{"<id>": {"verdicts": ["ok" or "forced", one per phrase, in order]'
             + (', "rewrite": "<the rewrite with forced phrases undone>"' if restore else "")
             + "}, ...}")
    parts = [QUESTION, *([RESTORE] if restore else []), "",
             f"Reply with only a JSON object: {shape}"]
    for item_id, original, reply in items:
        parts += ["", f"=== ITEM {item_id} ===", "ORIGINAL:", original, "REWRITE:", reply,
                  "PHRASES:"]
        for n, (eid, words) in enumerate(markers(reply), 1):
            parts.append(f"{n}. {{{{{eid}|{words}}}}}: {describe(lex, eid)}")
    return "\n".join(parts)


def _parse_json(reply: str) -> object:
    match = re.search(r"\{.*\}", reply or "", re.DOTALL)
    if not match:
        raise ValueError("no JSON object in the critic's reply")
    return json.loads(match.group(0))


def parse_verdicts(reply: str, items: list[tuple[str, str, str]]) -> dict[str, dict]:
    """``{id: {"forced": [entry ids judged forced], "rewrite": str | None}}``.

    An item the critic skipped, or whose verdict list has the wrong length, gets no
    forced phrases and no rewrite: a malformed verdict never removes a phrase."""
    data = _parse_json(reply)
    out: dict[str, dict] = {}
    for item_id, _original, rewrite in items:
        found = data.get(item_id) if isinstance(data, dict) else None
        marks = markers(rewrite)
        verdicts = found.get("verdicts") if isinstance(found, dict) else None
        if not isinstance(verdicts, list) or len(verdicts) != len(marks):
            out[item_id] = {"forced": [], "rewrite": None}
            continue
        forced = [eid for (eid, _), v in zip(marks, verdicts, strict=True)
                  if str(v).strip().lower() == "forced"]
        new = found.get("rewrite")
        out[item_id] = {"forced": forced,
                        "rewrite": new if isinstance(new, str) and new.strip() else None}
    return out


def forced_count(critic: Critic, lex, original: str, reply: str) -> int:
    """How many of ``reply``'s phrases the critic judges forced (0 on any failure)."""
    if not markers(reply):
        return 0
    items = [("1", original, reply)]
    try:
        verdict = parse_verdicts(critic(critic_prompt(lex, items), CRITIC_SYSTEM), items)
    except Exception as exc:
        print(f"critic failed, counting no forced phrase: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 0
    return len(verdict["1"]["forced"])


# --- command line ----------------------------------------------------------------------

def add_critic_args(parser) -> None:
    """``--critic-*`` options: a Codex gateway or an OpenAI-compatible endpoint."""
    parser.add_argument("--critic-codex-url", default="",
                        help="naturalness guardrail: Codex gateway base, e.g. .../codex")
    parser.add_argument("--critic-base-url", default="",
                        help="naturalness guardrail: OpenAI-compatible endpoint instead")
    parser.add_argument("--critic-model", default="gpt-6.1-sol")
    parser.add_argument("--critic-effort", default="low")
    parser.add_argument("--critic-api-key", default="")
    parser.add_argument("--critic-timeout", type=float, default=600.0)


def make_critic(args) -> Critic | None:
    """The critic the ``--critic-*`` options describe, or None (no guardrail)."""
    if args.critic_codex_url:
        from .codex import CodexConfig, codex_complete

        complete = codex_complete(CodexConfig(
            base_url=args.critic_codex_url.rstrip("/"), users=frozenset({"*"}),
            model=args.critic_model, effort=args.critic_effort,
            timeout=args.critic_timeout, fresh=True))
        return lambda prompt, system: complete(prompt, system, 4000)
    if args.critic_base_url:
        from .evaluate import openai_complete

        return openai_complete(args.critic_base_url, args.critic_model, args.critic_api_key,
                               args.critic_timeout)
    return None
