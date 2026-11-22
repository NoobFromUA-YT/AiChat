"""Cost / credit counter, context size and token formatting. No Qt (unit-testable)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from . import config

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
CTX_WARN = 200_000       # context size from which Compact is worth a click (every reply re-reads all of it)
TAIL_BYTES = 1_500_000
# A resumed session re-reads its whole context every turn, so a long chat costs more per reply: past this many dollars
# the reply line suggests New Chat.
SESSION_HINT_USD = 3.0
USAGE_SCHEMA = 2


def claude_delta(total: float, base: float) -> float:
    """Cost of ONE reply. `claude -p` reports total_cost_usd for the whole session (a --resume continues the session's
    running total), so a reply costs total - base, base being the total after the previous reply of the same chat.
    A total below base means a fresh session (its total starts again at 0): then the total itself is this reply's cost."""
    return total - base if total >= base - 1e-9 else total



def migrate_usage(data: dict, start_balance: float = 0.0) -> tuple[dict, bool]:
    """Bring usage.json to schema 2: `claude_spent` is the true spend (a reply costs total - previous total of its
    session) and an anchor holds the balance the user last read in the console plus the spend counter at that moment.
    Schema 1 added every reply's cumulative session total (about 3x too much): its sum is kept as claude_spent_legacy."""
    if data.get("schema") == USAGE_SCHEMA:
        return data, False
    if "claude_spent" in data and data.get("schema") is None and data.get("claude_msgs"):
        data["claude_spent_legacy"] = data.get("claude_spent", 0.0)
    data["claude_spent"] = 0.0
    data["claude_msgs"] = 0
    if start_balance > 0:
        data["claude_anchor_balance"] = start_balance
        data["claude_anchor_spent"] = 0.0
        data["claude_anchor_ts"] = time.time()
    data["schema"] = USAGE_SCHEMA
    return data, True


def balance_left(usage: dict, key: str, default_budget: float) -> tuple[float, float, float]:
    """(left, spent since the anchor, anchor balance) for the usage key ('claude')."""
    spent = float(usage.get(f"{key}_spent", 0.0))
    a_bal = float(usage.get(f"{key}_anchor_balance", default_budget))
    since = spent - float(usage.get(f"{key}_anchor_spent", 0.0))
    return max(0.0, a_bal - since), since, a_bal


def balance_pct(left: float, budget: float) -> float:
    """Share of the full budget still left, in percent (budget = what counts as 100 %: $200)."""
    return 100.0 * left / budget if budget > 0 else 0.0


def balance_label(pct: float) -> str:
    """'92% left': whole percent, rounded half up (Python's round() would turn 92.5 into 92)."""
    return f"{int(pct + 0.5)}% left"


def fmt_tokens(n: int) -> str:
    """274 000 -> '274k', 2 924 -> '2.9k', 1 250 000 -> '1.25M'."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M".replace(".00M", "M")
    if n >= 10_000:
        return f"{round(n / 1000)}k"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def transcript_tail(sid: str | None) -> list[dict]:
    """The last records of Claude Code's transcript of session `sid`, newest last (a cut first line is dropped)."""
    if not sid:
        return []
    for f in CLAUDE_PROJECTS.glob(f"*/{sid}.jsonl"):
        try:
            with open(f, "rb") as fh:
                fh.seek(0, 2)
                size = fh.tell()
                fh.seek(max(0, size - TAIL_BYTES))
                raw = fh.read().decode("utf-8", "replace")
        except OSError:
            continue
        lines = raw.splitlines()
        if size > TAIL_BYTES:
            lines = lines[1:]
        out = []
        for line in lines:
            if '"compact_boundary"' in line or '"cache_read_input_tokens"' in line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return out
    return []


def context_tokens(sid: str | None) -> int | None:
    """Tokens the next reply re-reads: the last assistant turn's input + cache, or `postTokens` if a /compact came after."""
    for rec in reversed(transcript_tail(sid)):
        if rec.get("subtype") == "compact_boundary":
            post = (rec.get("compactMetadata") or {}).get("postTokens")
            if isinstance(post, int):
                return post
        elif rec.get("type") == "assistant" and not rec.get("isSidechain"):
            u = (rec.get("message") or {}).get("usage") or {}
            if "cache_read_input_tokens" in u:
                return int(u.get("input_tokens", 0)) + int(u.get("cache_read_input_tokens", 0)) + int(u.get("cache_creation_input_tokens", 0))
    return None


def last_compact(sid: str | None) -> tuple[int, int] | None:
    """(tokens before, tokens after) of the newest /compact of the session."""
    for rec in reversed(transcript_tail(sid)):
        if rec.get("subtype") == "compact_boundary":
            m = rec.get("compactMetadata") or {}
            if isinstance(m.get("preTokens"), int) and isinstance(m.get("postTokens"), int):
                return m["preTokens"], m["postTokens"]
    return None


def load_usage(start_balance: float = 0.0) -> dict:
    """Load persisted cumulative spend and message counts."""
    data = {"claude_spent": 0.0, "claude_msgs": 0}
    path = config.usage_path()
    if path.exists():
        try:
            data.update(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    data, changed = migrate_usage(data, start_balance)
    if changed:
        save_usage(data)
    return data


def save_usage(usage: dict) -> None:
    """Atomically save usage stats."""
    path = config.usage_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(usage, indent=2), encoding="utf-8")
    os.replace(tmp, path)



def set_anchor(usage: dict, val: float, key: str = "claude") -> None:
    """The console says the balance is `val`: anchor the counter there and forget replies that could not be priced."""
    usage[f"{key}_anchor_balance"] = val
    usage[f"{key}_anchor_spent"] = float(usage.get(f"{key}_spent", 0.0))
    usage[f"{key}_anchor_ts"] = time.time()
    usage[f"{key}_uncounted"] = 0
