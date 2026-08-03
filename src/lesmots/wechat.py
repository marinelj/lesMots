"""WeChat Mini Program integration (M2).

Enabled by LESMOTS_WX_APPID + LESMOTS_WX_SECRET. Two capabilities:

- code2session: exchange a wx.login() code for the user's openid (login)
- msg_sec_check: WeChat's mandatory content moderation for Mini Programs

Everything degrades gracefully: unconfigured or unreachable WeChat APIs
never take the app down.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from typing import Optional

CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"
ACCESS_TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
MSG_SEC_CHECK_URL = "https://api.weixin.qq.com/wxa/msg_sec_check"

_token_cache = {"token": "", "expires": 0.0}


def _appid() -> str:
    return os.environ.get("LESMOTS_WX_APPID", "")


def _secret() -> str:
    return os.environ.get("LESMOTS_WX_SECRET", "")


def is_configured() -> bool:
    return bool(_appid() and _secret())


def _get_json(url: str, body: Optional[dict] = None) -> dict:
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode()
        headers["content-type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def code2session(code: str) -> Optional[dict]:
    """Exchange a wx.login() code for {"openid", ...}; None on any failure."""
    query = urllib.parse.urlencode({
        "appid": _appid(), "secret": _secret(),
        "js_code": code, "grant_type": "authorization_code",
    })
    try:
        info = _get_json(f"{CODE2SESSION_URL}?{query}")
    except Exception:
        return None
    if not info.get("openid"):
        return None
    return info


def _access_token() -> str:
    if _token_cache["expires"] > time.time():
        return _token_cache["token"]
    query = urllib.parse.urlencode({
        "grant_type": "client_credential", "appid": _appid(), "secret": _secret(),
    })
    info = _get_json(f"{ACCESS_TOKEN_URL}?{query}")
    _token_cache["token"] = info["access_token"]
    _token_cache["expires"] = time.time() + int(info.get("expires_in", 7200)) - 300
    return _token_cache["token"]


def msg_sec_check(openid: str, content: str) -> bool:
    """True = content passes (or the check is unavailable); False = flagged.

    WeChat requires moderating user-generated content in Mini Programs.
    Availability problems must never block the app, so any error passes.
    """
    if not is_configured() or not content.strip():
        return True
    try:
        token = _access_token()
        result = _get_json(
            f"{MSG_SEC_CHECK_URL}?access_token={token}",
            body={"version": 2, "openid": openid, "scene": 2, "content": content[:2500]},
        )
        return result.get("result", {}).get("suggest", "pass") == "pass"
    except Exception:
        return True
