"""Where aichat-desktop keeps its files, the user's settings, the API key and how it finds the `claude` CLI.

No Qt in here (unit-testable). Every path is derived from `config_dir()`, so a test only has to set `CONFIG_DIR`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

CONFIG_DIR: Path | None = None        # tests set this; None = $AICHAT_DESKTOP_CONFIG_DIR, $XDG_CONFIG_HOME/aichat-desktop or ~/.config/aichat-desktop

DEFAULT_SETTINGS: dict = {
    "first_run_done": False,
    "budget": 0.0,            # starting credit in USD = what counts as 100 %; 0 = no credit counter
    "permission": "readonly",  # readonly | edit | full  (see PERMISSION_MODES)
    "cwd": "",                # project folder claude runs in; "" = home
    "claude_path": "",        # "" = find `claude` on PATH
    "model": "sonnet",
    "effort": "High",
    "theme": "system",        # system | dark | light | classic (see themes.py)
}

# key -> (label, tooltip, extra `claude` arguments). `claude -p` cannot ask questions, so "ask first" would mean
# "deny": what a mode does not allow is refused, and the window tells the user which tool was refused.
PERMISSION_MODES: dict[str, tuple[str, str, list[str]]] = {
    "readonly": (
        "Read only",
        "Claude may read and search files (and the web) but cannot edit files or run commands. Safest.",
        ["--permission-mode", "default", "--allowedTools", "Read,Glob,Grep,WebFetch,WebSearch"],
    ),
    "edit": (
        "Edit files",
        "Claude may read and edit files in the project folder. Shell commands are refused.",
        ["--permission-mode", "acceptEdits", "--allowedTools", "Read,Glob,Grep,WebFetch,WebSearch,Edit,Write,NotebookEdit"],
    ),
    "full": (
        "Full auto",
        "Claude may do anything you could do in a terminal: edit files, run commands, install things, delete files, "
        "without asking. Use it in folders you can afford to lose.",
        ["--dangerously-skip-permissions"],
    ),
}
DEFAULT_PERMISSION = "readonly"


APP_DIR = "aichat-desktop"          # folder name under the user's config / cache folder
LEGACY_APP_DIR = "aichat"           # versions before 0.3 used this name, which also belongs to other programs
OWN_FILES = ("settings.json", "usage.json", "env", "chats", "current_chat", "last_chat.json", "last_chat.json.imported", "icon.png")


def migrate_legacy_dir(old: Path | None = None, new: Path | None = None) -> list[str]:
    """Versions before 0.3 kept their files in a folder called `aichat`, a name other programs use too. Move only OUR
    files (OWN_FILES) to the new folder, once; anything else in the old folder stays. Returns the names moved."""
    if new is None or old is None:
        if CONFIG_DIR is not None or os.environ.get("AICHAT_DESKTOP_CONFIG_DIR"):
            return []                    # a custom location was chosen on purpose: never look for old files elsewhere
        new = new or config_dir()
        old = old or new.parent / LEGACY_APP_DIR
    if new.exists() or not old.is_dir() or old == new:
        return []
    mine = [n for n in OWN_FILES if (old / n).exists()]
    if not mine:
        return []
    new.mkdir(parents=True, exist_ok=True)
    moved = []
    for n in mine:
        shutil.move(str(old / n), str(new / n))
        moved.append(n)
    try:
        old.rmdir()                      # only if nothing else is in it
    except OSError:
        pass
    return moved


def config_dir() -> Path:
    if CONFIG_DIR is not None:
        return Path(CONFIG_DIR)
    if os.environ.get("AICHAT_DESKTOP_CONFIG_DIR"):
        return Path(os.environ["AICHAT_DESKTOP_CONFIG_DIR"])
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / APP_DIR
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / APP_DIR


def env_path() -> Path:
    return config_dir() / "env"


def settings_path() -> Path:
    return config_dir() / "settings.json"


def usage_path() -> Path:
    return config_dir() / "usage.json"


def chats_dir() -> Path:
    return config_dir() / "chats"


def legacy_chat_path() -> Path:
    return config_dir() / "last_chat.json"


def icon_override_path() -> Path:
    return config_dir() / "icon.png"


def write_private(path: Path, text: str) -> None:
    """Atomic write, mode 0600 from the first byte (keys and chats are private; on Windows the user profile's own
    permissions are what protects them, the mode bits mean nothing there)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)




# ---- settings -----------------------------------------------------------------------------------------------------------
def load_settings() -> dict:
    data = dict(DEFAULT_SETTINGS)
    try:
        raw = json.loads(settings_path().read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            data.update({k: v for k, v in raw.items() if k in DEFAULT_SETTINGS})
    except (OSError, json.JSONDecodeError):
        pass
    if data["permission"] not in PERMISSION_MODES:
        data["permission"] = DEFAULT_PERMISSION
    if data["theme"] not in ("system", "dark", "light", "classic"):
        data["theme"] = "system"
    try:
        data["budget"] = max(0.0, float(data["budget"]))
    except (TypeError, ValueError):
        data["budget"] = 0.0
    return data


def save_settings(settings: dict) -> None:
    clean = {k: settings[k] for k in DEFAULT_SETTINGS if k in settings}
    write_private(settings_path(), json.dumps(clean, indent=2))


# ---- the claude CLI -----------------------------------------------------------------------------------------------------
def find_claude(configured: str = "") -> str | None:
    """Path of the claude CLI: the configured one, else `claude` on PATH, else the usual install folders. None if missing."""
    if configured:
        p = Path(configured).expanduser()
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
    if sys.platform == "win32":          # prefer the real program: a claude.cmd shim cannot take a multi-line prompt safely
        found = shutil.which("claude.exe") or shutil.which("claude")
    else:
        found = shutil.which("claude")
    if found:
        return found
    home = Path.home()
    cands = [home / ".local" / "bin" / "claude", home / ".claude" / "local" / "claude",
             home / ".npm-global" / "bin" / "claude", Path("/usr/local/bin/claude"), Path("/opt/homebrew/bin/claude")]
    if sys.platform == "win32":
        cands = [home / ".local" / "bin" / "claude.exe", home / ".claude" / "local" / "claude.exe",
                 Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / "npm" / "claude.cmd"]
    for cand in cands:
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


CLAUDE_MISSING = (
    "The `claude` command (Claude Code) was not found.\n\n"
    "Install it from https://claude.com/claude-code (for example `npm install -g @anthropic-ai/claude-code`), "
    "run `claude` once in a terminal to sign in, then restart this window.\n"
    "If it is installed somewhere unusual, set its path under Settings."
)


# ---- the API key --------------------------------------------------------------------------------------------------------
def parse_env_text(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            k = k.strip().removeprefix("export ").strip()
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            out[k] = v
    return out


def load_env() -> dict[str, str]:
    """The process environment plus the key=value lines of the env file of the config folder (those win)."""
    env = dict(os.environ)
    try:
        env.update(parse_env_text(env_path().read_text(encoding="utf-8")))
    except OSError:
        pass
    return env


def load_own_key() -> str:
    """The key saved by this app (ANTHROPIC_API_KEY in the env file), '' if none."""
    try:
        return parse_env_text(env_path().read_text(encoding="utf-8")).get("ANTHROPIC_API_KEY", "")
    except OSError:
        return ""


def save_own_key(key: str) -> None:
    """Save `key` as ANTHROPIC_API_KEY in the env file (mode 0600, other lines kept); '' removes the line."""
    try:
        lines = env_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    lines = [ln for ln in lines if parse_env_text(ln).get("ANTHROPIC_API_KEY") is None]
    if key:
        lines.append(f"ANTHROPIC_API_KEY={key}")
    if lines:
        write_private(env_path(), "\n".join(lines) + "\n")
    else:
        env_path().unlink(missing_ok=True)


def key_format_error(key: str) -> str | None:
    """None when `key` looks like an Anthropic API key, else what is wrong with it."""
    if not key:
        return "The key is empty."
    if re.search(r"\s", key):
        return "The key contains spaces or line breaks."
    if not key.startswith("sk-ant-") or len(key) < 20:
        return "An Anthropic API key starts with sk-ant-."
    return None


def mask_key(key: str) -> str:
    return "none" if not key else (key[:7] + "…" + key[-4:] if len(key) > 14 else "…")


def check_key_online(key: str) -> bool | None:
    """Ask Anthropic if the key is accepted (GET /v1/models: free, no tokens). None = could not reach the server."""
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/models?limit=1",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            return True
    except urllib.error.HTTPError as e:
        return False if e.code in (401, 403) else None
    except (urllib.error.URLError, OSError):
        return None
