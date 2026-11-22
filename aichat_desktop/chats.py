"""Named chats stored one file each in the config folder's chats/<id>.json (mode 0600). No Qt."""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path

from . import config

MAX_SAVED_MSGS = 500
KINDS = ("user", "assistant", "error", "note")


def new_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _valid_id(chat_id: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,64}", chat_id or ""))


def _path(chat_id: str) -> Path:
    if not _valid_id(chat_id):
        raise ValueError("bad chat id")
    return config.chats_dir() / f"{chat_id}.json"


def title_from(messages: list[dict]) -> str:
    for m in messages:
        if m.get("kind") == "user" and str(m.get("text", "")).strip():
            t = " ".join(str(m["text"]).split())
            return t if len(t) <= 60 else t[:59] + "…"
    return "New chat"


def save(chat: dict) -> None:
    """chat: id, messages, sid, cost_base, cwd, [title, created]. Adds updated; keeps a user-set title."""
    chat = dict(chat)
    chat["messages"] = list(chat.get("messages", []))[-MAX_SAVED_MSGS:]
    now = time.time()
    old = load(chat["id"]) or {}
    chat["created"] = chat.get("created") or old.get("created") or now
    chat["updated"] = now
    if old.get("title_auto") is False:           # renamed by the user: keep that name
        chat["title"], chat["title_auto"] = old.get("title"), False
    else:
        chat["title"], chat["title_auto"] = title_from(chat["messages"]), True
    config.write_private(_path(chat["id"]), json.dumps(chat))


def load(chat_id: str) -> dict | None:
    try:
        data = json.loads(_path(chat_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    data["id"] = chat_id
    data["messages"] = [m for m in data.get("messages", [])
                        if isinstance(m, dict) and m.get("kind") in KINDS and isinstance(m.get("text"), str)]
    return data


def list_chats() -> list[dict]:
    """Summaries (id, title, updated, cwd, n), newest first."""
    out = []
    for f in config.chats_dir().glob("*.json"):
        c = load(f.stem)
        if c and c["messages"]:
            out.append({"id": c["id"], "title": c.get("title") or title_from(c["messages"]),
                        "updated": float(c.get("updated") or f.stat().st_mtime),
                        "cwd": c.get("cwd") or "", "n": len(c["messages"])})
    return sorted(out, key=lambda c: c["updated"], reverse=True)


def delete(chat_id: str) -> None:
    _path(chat_id).unlink(missing_ok=True)


def rename(chat_id: str, title: str) -> None:
    c = load(chat_id)
    title = " ".join(title.split())
    if c is None or not title:
        return
    c["title"], c["title_auto"] = title, False
    config.write_private(_path(chat_id), json.dumps(c))


def get_current() -> str | None:
    p = config.config_dir() / "current_chat"
    try:
        cid = p.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return cid if _valid_id(cid) else None


def set_current(chat_id: str | None) -> None:
    p = config.config_dir() / "current_chat"
    if chat_id:
        config.write_private(p, chat_id + "\n")
    else:
        p.unlink(missing_ok=True)


def import_legacy() -> str | None:
    """The single-chat file of version 0.1 (last_chat.json) becomes a normal chat once. Returns its id or None."""
    p = config.legacy_chat_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    msgs = [m for m in data.get("messages", [])
            if isinstance(m, dict) and m.get("kind") in KINDS and isinstance(m.get("text"), str)]
    if msgs:
        cid = new_id()
        save({"id": cid, "messages": msgs, "sid": data.get("sid"), "cost_base": data.get("cost_base", 0.0), "cwd": ""})
        set_current(cid)
    else:
        cid = None
    p.rename(p.with_name("last_chat.json.imported"))
    return cid
