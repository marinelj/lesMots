"""Persistence for 微信云托管 (M2): mirror $LESMOTS_HOME into cloud object storage.

云托管 containers have no persistent disk, the service config offers no volume
mount, and min-instances is 0 — so /data is wiped on every scale-to-zero.
Instead, every saved file is mirrored into the environment's built-in object
storage through WeChat's tcb HTTP API (authenticated with the same cached
mini-program access token wechat.py uses for moderation):

    restore()  — at server start: pull every known file back into $LESMOTS_HOME
    push(path) — after each save: upload that file, plus a manifest of paths
                 (the tcb API cannot list a directory, so we keep our own index)

Enabled by LESMOTS_TCB_ENV=<cloud env id>; off otherwise (local, Cloud Run).
Pushes are best-effort — storage hiccups must never take the app down — but a
failed restore() disables pushes for the process lifetime: an instance that
could not read the cloud copy must not overwrite it with its own empty state.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Optional

from lesmots import wechat
from lesmots.memory import data_path

API = "https://api.weixin.qq.com"
PREFIX = "lesmots/"  # folder inside the environment's object storage
MANIFEST = "manifest.json"

_lock = threading.Lock()
_manifest: set[str] = set()
_restore_failed = False
_restore_error = ""
RESTORE_ATTEMPTS = 5  # boot-time network can be flaky for a few seconds


def _env() -> str:
    return os.environ.get("LESMOTS_TCB_ENV", "")


def enabled() -> bool:
    return bool(_env())


def _home() -> Path:
    return data_path().parent


def status() -> str:
    """"off" (not configured), "error" (restore failed), or "ok"."""
    if not enabled():
        return "off"
    return "error" if _restore_failed else "ok"


def selftest() -> dict:
    """Round-trip a probe object; for /api/storage-selftest (LESMOTS_DEBUG)."""
    if not enabled():
        return {"status": "off"}
    try:
        _upload(PREFIX + ".probe", b"ok")
        data = _download(PREFIX + ".probe")
        probe = "ok" if data == b"ok" else f"mismatch: {data!r}"
    except Exception as e:
        probe = f"{type(e).__name__}: {e}"
    return {"status": status(), "probe": probe, "restore_error": _restore_error}


def _api(path: str, body: dict) -> dict:
    url = f"{API}{path}?access_token={wechat._access_token()}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read())
    if result.get("errcode", 0) != 0:
        raise RuntimeError(f"{path}: {result.get('errcode')} {result.get('errmsg')}")
    return result


def _upload(cloud_path: str, content: bytes) -> None:
    meta = _api("/tcb/uploadfile", {"env": _env(), "path": cloud_path})
    boundary = uuid.uuid4().hex
    fields = {
        "key": cloud_path,
        "Signature": meta["authorization"],
        "x-cos-security-token": meta["token"],
        "x-cos-meta-fileid": meta["cos_file_id"],
    }
    parts = [(f'--{boundary}\r\nContent-Disposition: form-data; '
              f'name="{name}"\r\n\r\n{value}\r\n').encode()
             for name, value in fields.items()]
    parts.append((f'--{boundary}\r\nContent-Disposition: form-data; '
                  f'name="file"; filename="file"\r\n\r\n').encode())
    parts.append(content)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        meta["url"], data=b"".join(parts),
        headers={"content-type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


def _download(cloud_path: str) -> Optional[bytes]:
    """Fetch one object; None when it does not exist (a fresh environment)."""
    # The uploadfile metadata call is the only way to learn a path's file_id;
    # it does not modify anything unless the returned URL is actually POSTed.
    file_id = _api("/tcb/uploadfile", {"env": _env(), "path": cloud_path})["file_id"]
    entry = _api("/tcb/batchdownloadfile", {
        "env": _env(), "file_list": [{"fileid": file_id, "max_age": 600}],
    })["file_list"][0]
    if entry.get("status", 0) != 0:  # STORAGE_FILE_NONEXIST and friends
        return None
    try:
        with urllib.request.urlopen(entry["download_url"], timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def restore() -> None:
    """Pull the cloud copy of $LESMOTS_HOME. Call once, before serving.

    Retries are safe here because nothing has been written locally yet; once
    the server is taking writes, a late restore would clobber them.
    """
    global _restore_failed, _restore_error
    if not enabled():
        return
    for attempt in range(1, RESTORE_ATTEMPTS + 1):
        try:
            raw = _download(PREFIX + MANIFEST)
            names = json.loads(raw) if raw else []
            for rel in names:
                if ".." in Path(rel).parts or Path(rel).is_absolute():
                    continue
                content = _download(PREFIX + rel)
                if content is None:
                    continue
                target = _home() / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            _manifest.update(names)
            print(f"cloudsync: restored {len(names)} file(s) from env {_env()}")
            return
        except Exception as e:
            _restore_error = f"{type(e).__name__}: {e}"
            print(f"cloudsync: restore attempt {attempt} failed: {_restore_error}")
            if attempt < RESTORE_ATTEMPTS:
                time.sleep(2)
    _restore_failed = True
    print("cloudsync: restore FAILED; pushes disabled to protect the cloud copy")


def push(path: Path) -> None:
    """Mirror one saved file into cloud storage; never raises."""
    if not enabled() or _restore_failed:
        return
    try:
        rel = path.resolve().relative_to(_home().resolve()).as_posix()
        _upload(PREFIX + rel, path.read_bytes())
        with _lock:
            if rel not in _manifest:
                _manifest.add(rel)
                _upload(PREFIX + MANIFEST, json.dumps(sorted(_manifest)).encode())
    except Exception as e:
        print(f"cloudsync: push of {path.name} failed: {e}")
