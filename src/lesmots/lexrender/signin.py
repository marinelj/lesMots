"""Sign-in through the LesMots app.

A lexicon account belongs to a LesMots app user_id: the Google ``sub`` or ``wx-<openid>``
the app signs people in with. The user proves it by signing in to the app, which issues a
signed session token (``GET /api/lexicon-token`` returns a fresh one). Clients send that
token as ``Authorization: Bearer <token>``; this module checks the app's signature and
expiry and returns the user_id. The user_id itself is never accepted as a password.

The app and this server must share the session secret (``LESMOTS_SESSION_SECRET``, or the
same LesMots data directory).
"""

from __future__ import annotations

import re
from collections.abc import Callable

# token -> LesMots user_id, or None when the token is not a valid, unexpired sign-in
VerifySignIn = Callable[[str], "str | None"]


def app_user_id(session: dict | None) -> str | None:
    """The user_id exactly as the app derives it (see ``web.Handler._resolve_user``)."""
    if not session:
        return None
    return re.sub(r"[^\w-]", "", str(session.get("uid", ""))) or None


def verify_sign_in(token: str) -> str | None:
    from lesmots.web import check_session

    return app_user_id(check_session(token.strip())) if token else None
