"""Menu entry + icons for installs that do not come from a package (pipx, uv tool, a checkout):
`aichat-desktop --install-desktop` / `aichat-desktop --uninstall-desktop`. No Qt."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from . import __version__

ASSETS = Path(__file__).resolve().parent / "assets"
SIZES = (16, 32, 48, 64, 128, 512)


def data_home() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    return Path(base) if base else Path.home() / ".local" / "share"


def exec_command() -> str:
    """How the menu entry starts the app: the `aichat-desktop` script of the environment this runs from (pipx / uv tool / venv),
    else the one on PATH, else this Python with -m aichat_desktop."""
    own = Path(sys.executable).parent / "aichat-desktop"
    found = str(own) if own.is_file() else shutil.which("aichat-desktop")
    if found:
        return '"%s"' % found
    return '"%s" -m aichat_desktop' % sys.executable


def desktop_entry(exec_cmd: str) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=AI Chat\n"
        "GenericName=Claude chat\n"
        "Comment=A chat window for Claude Code\n"
        f"Exec={exec_cmd}\n"
        "Icon=aichat-desktop\n"
        "Terminal=false\n"
        "Categories=Development;\n"
        "Keywords=claude;ai;chat;assistant;\n"
        "StartupWMClass=aichat-desktop\n"
        f"X-AIChat-Version={__version__}\n"
    )


def _paths(home: Path) -> list[Path]:
    out = [home / "applications" / "aichat-desktop.desktop", home / "icons" / "hicolor" / "256x256" / "apps" / "aichat-desktop.png",
           home / "icons" / "hicolor" / "scalable" / "apps" / "aichat-desktop.svg"]
    out += [home / "icons" / "hicolor" / f"{n}x{n}" / "apps" / "aichat-desktop.png" for n in SIZES]
    return out


def install(home: Path | None = None, exec_cmd: str | None = None) -> list[Path]:
    if sys.platform == "win32" and home is None:
        raise SystemExit("--install-desktop is for Linux desktops. On Windows pin the aichat-desktop.exe that pip / pipx created to the Start menu.")
    home = home or data_home()
    written = []
    app_file = home / "applications" / "aichat-desktop.desktop"
    app_file.parent.mkdir(parents=True, exist_ok=True)
    app_file.write_text(desktop_entry(exec_cmd or exec_command()), encoding="utf-8")
    written.append(app_file)
    pairs = [(ASSETS / "icon.png", home / "icons" / "hicolor" / "256x256" / "apps" / "aichat-desktop.png"),
             (ASSETS / "icon.svg", home / "icons" / "hicolor" / "scalable" / "apps" / "aichat-desktop.svg")]
    pairs += [(ASSETS / f"icon-{n}.png", home / "icons" / "hicolor" / f"{n}x{n}" / "apps" / "aichat-desktop.png") for n in SIZES]
    for src, dst in pairs:
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            written.append(dst)
    return written


def uninstall(home: Path | None = None) -> list[Path]:
    removed = []
    for p in _paths(home or data_home()):
        if p.exists():
            p.unlink()
            removed.append(p)
    return removed
