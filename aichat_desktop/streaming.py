"""Turns `claude -p --output-format stream-json --include-partial-messages` lines into live progress. No Qt.

Event shapes (checked against Claude Code 2.1, 2026-11-22):
  {"type":"system","subtype":"init","session_id":...}
  {"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"..."}}}
  {"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":...}}]}}
  {"type":"system","subtype":"permission_denied","tool_name":"Write",...}
  {"type":"result","subtype":"success","is_error":false,"result":"...","total_cost_usd":0.01,"session_id":...,
   "permission_denials":[{"tool_name":...,"tool_input":{...}}]}
Anything unknown is ignored, so a newer Claude Code adding events cannot break the window.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


def _short(path: str, n: int = 60) -> str:
    base = os.path.basename(path.rstrip("/")) or path
    return base if len(base) <= n else base[: n - 1] + "…"


def describe_tool(name: str, inp: dict | None) -> str:
    """'Reading app.py' / 'Running: ls -la' ... one short line for the live panel."""
    inp = inp or {}
    path = str(inp.get("file_path") or inp.get("path") or inp.get("notebook_path") or "")
    if name == "Read":
        return f"Reading {_short(path)}" if path else "Reading a file"
    if name in ("Edit", "MultiEdit"):
        return f"Editing {_short(path)}" if path else "Editing a file"
    if name == "Write":
        return f"Writing {_short(path)}" if path else "Writing a file"
    if name == "NotebookEdit":
        return f"Editing notebook {_short(path)}"
    if name == "Bash":
        cmd = " ".join(str(inp.get("command", "")).split())
        return "Running: " + (cmd if len(cmd) <= 80 else cmd[:79] + "…")
    if name == "Glob":
        return f"Finding files: {inp.get('pattern', '')}"
    if name == "Grep":
        return f"Searching for: {inp.get('pattern', '')}"
    if name == "WebFetch":
        return f"Fetching {inp.get('url', 'a page')}"
    if name == "WebSearch":
        return f"Searching the web: {inp.get('query', '')}"
    if name in ("Task", "Agent"):
        return f"Subagent: {inp.get('description', '')}".rstrip(": ")
    if name == "TodoWrite":
        return "Updating the to-do list"
    return f"Using {name}"


@dataclass
class StreamState:
    """What has been seen so far in one `claude` call."""

    session_id: str | None = None
    text: str = ""                  # the answer text streamed so far (current assistant message)
    tools: list[str] = field(default_factory=list)       # every tool line, in order
    denied: list[str] = field(default_factory=list)      # tools refused by the permission mode
    result: dict | None = None      # the final {"type":"result"} object
    _seen_tool_ids: set = field(default_factory=set)


def parse_line(line: str) -> dict | None:
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def feed(state: StreamState, obj: dict) -> list[tuple[str, str]]:
    """Update `state` with one event; return UI updates: ('text', whole text so far) or ('tool', line) or ('denied', tool)."""
    out: list[tuple[str, str]] = []
    t = obj.get("type")
    if isinstance(obj.get("session_id"), str):
        state.session_id = obj["session_id"]
    if t == "stream_event":
        ev = obj.get("event") or {}
        if ev.get("type") == "message_start":
            state.text = ""                      # a new assistant message (after tool calls) starts a new text
        elif ev.get("type") == "content_block_delta":
            d = ev.get("delta") or {}
            if d.get("type") == "text_delta" and isinstance(d.get("text"), str):
                state.text += d["text"]
                out.append(("text", state.text))
    elif t == "assistant" and not obj.get("parent_tool_use_id"):
        for b in (obj.get("message") or {}).get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and isinstance(b.get("text"), str) and b["text"] != state.text:
                state.text = b["text"]           # no partial events (older Claude Code): the whole message at once
                out.append(("text", state.text))
            elif b.get("type") == "tool_use" and b.get("id") not in state._seen_tool_ids:
                state._seen_tool_ids.add(b.get("id"))
                line = describe_tool(str(b.get("name", "")), b.get("input") if isinstance(b.get("input"), dict) else {})
                state.tools.append(line)
                out.append(("tool", line))
    elif t == "system" and obj.get("subtype") == "permission_denied":
        name = str(obj.get("tool_name", "a tool"))
        state.denied.append(name)
        out.append(("denied", name))
    elif t == "result":
        state.result = obj
    return out


def denial_note(denials: list[dict], permission_label: str) -> str:
    """One sentence for the chat when the permission mode refused tools (empty if nothing was refused)."""
    names: list[str] = []
    for d in denials or []:
        n = str(d.get("tool_name", "")) if isinstance(d, dict) else ""
        if n and n not in names:
            names.append(n)
    if not names:
        return ""
    return (f"Refused in \"{permission_label}\" mode: {', '.join(names)}. "
            "Change the mode next to the model box if you want Claude to do that.")
