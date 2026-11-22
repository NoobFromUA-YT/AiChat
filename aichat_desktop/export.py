"""Chat -> Markdown file. No Qt."""

from __future__ import annotations

import time


def export_markdown(messages: list[dict], title: str = "Chat", cwd: str = "") -> str:
    """The whole chat as Markdown: one section per message, replies verbatim (they already are Markdown)."""
    lines = [f"# {title}", ""]
    if cwd:
        lines += [f"Project folder: `{cwd}`", ""]
    lines += [f"Exported {time.strftime('%Y-%m-%d %H:%M')} from aichat_desktop.", ""]
    for m in messages:
        who = {"user": "You", "assistant": "Claude", "error": "Error", "note": "Note"}.get(m.get("kind"), "Claude")
        if m.get("kind") == "user":
            who = "You"
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(m.get("ts") or 0))) if m.get("ts") else ""
        meta = " · ".join(b for b in (stamp, str(m.get("meta") or "")) if b)
        lines += [f"## {who}" + (f"  \n*{meta}*" if meta else ""), "", str(m.get("text", "")).rstrip(), ""]
    return "\n".join(lines).rstrip() + "\n"
