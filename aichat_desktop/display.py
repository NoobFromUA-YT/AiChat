"""Sizes, fonts and the icon. Qt lives here (not in config / costs / chats).

Two modes:
* **normal** (default): Qt's own high-DPI scaling does the work, the font is the first installed one of FONT_FAMILIES
  (else the system font), text is antialiased. `S()` is the identity.
* **hard pixels**: for sessions that ask for an integer scale through QT_SCREEN_SCALE_FACTORS / QT_SCALE_FACTOR (Xfce
  at 200 %): Qt's scaling is switched off in `main()`, the font DPI is multiplied instead, glyphs are hinted and
  not antialiased, and every size goes through `S()` (device pixels). `AICHAT_DESKTOP_HARD_PIXELS=0` opts out.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QIcon, QImage, QPainter, QPolygon

from . import config

UI_SCALE = 1
HARD_PIXELS = False
FONT_BASE_PX = 15          # logical px of the text (11.25 pt)
FONT_FAMILIES = ("Arial", "Liberation Sans", "Arimo", "Helvetica", "Noto Sans", "DejaVu Sans")
SCALE_ENV_KEYS = ("FREETYPE_PROPERTIES", "QT_ENABLE_HIGHDPI_SCALING", "QT_SCREEN_SCALE_FACTORS",
                  "QT_SCALE_FACTOR", "QT_FONT_DPI")
ASSETS = Path(__file__).resolve().parent / "assets"


def S(n: int | float) -> int:
    """Logical size n in device pixels (UI_SCALE x in hard-pixel mode, otherwise unchanged)."""
    return int(round(n * UI_SCALE))


def detect_ui_scale() -> int:
    """The integer Qt scale factor the session asks for (QT_SCREEN_SCALE_FACTORS=2 under Xfce), 1 if none."""
    if os.environ.get("AICHAT_DESKTOP_HARD_PIXELS") == "0":
        return 1
    raw = os.environ.get("QT_SCREEN_SCALE_FACTORS") or os.environ.get("QT_SCALE_FACTOR") or "1"
    best = 1.0
    for part in raw.replace(";", " ").split():
        try:
            best = max(best, float(part.split("=")[-1]))
        except ValueError:
            pass
    return int(round(best))


def set_scale(scale: int) -> None:
    global UI_SCALE, HARD_PIXELS
    UI_SCALE = max(1, int(scale))
    HARD_PIXELS = UI_SCALE > 1


def scale_px(sheet: str) -> str:
    """Style sheet text with every Npx value turned into device pixels."""
    return re.sub(r"(\d+)px", lambda m: f"{S(int(m.group(1)))}px", sheet)


def pick_family(available: list[str] | None = None) -> str:
    """The first of FONT_FAMILIES that is installed; '' = let Qt use the system font."""
    have = {f.lower(): f for f in (available if available is not None else QFontDatabase.families())}
    for name in FONT_FAMILIES:
        if name.lower() in have:
            return have[name.lower()]
    return ""


def arrow_image(color: str = "#c8ccd4") -> str:
    """Path of a hard-pixel down arrow of S(12) device px (a style sheet arrow needs an image)."""
    n = S(12)
    path = cache_dir() / f"arrow-down-{n}-{color.lstrip('#')}.png"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        img = QImage(n, n, QImage.Format.Format_ARGB32)
        img.fill(0)
        p = QPainter(img)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawPolygon(QPolygon([QPoint(0, n // 3), QPoint(n, n // 3), QPoint(n // 2, n * 2 // 3 + 1)]))
        p.end()
        img.save(str(path))
    return str(path)



def cache_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / config.APP_DIR / "cache"
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / config.APP_DIR


def crisp_font(px: int) -> QFont:
    """The UI font at ``px`` device pixels: hinted and not antialiased in hard-pixel mode, normal otherwise."""
    fam = pick_family()
    f = QFont(fam) if fam else QFont()
    f.setPixelSize(px)
    if HARD_PIXELS:
        f.setStyleStrategy(QFont.StyleStrategy.NoAntialias)
        f.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    return f


def system_is_dark() -> bool | None:
    """True / False when the desktop says dark / light (Qt 6.5+ colour scheme), None when it does not say."""
    try:
        from PyQt6.QtCore import Qt as _Qt
        from PyQt6.QtGui import QGuiApplication
        scheme = QGuiApplication.styleHints().colorScheme()
    except (AttributeError, ImportError, RuntimeError):
        return None
    if scheme == _Qt.ColorScheme.Dark:
        return True
    if scheme == _Qt.ColorScheme.Light:
        return False
    return None


def icon_path() -> Path | None:
    """icon.png in the config folder if the user dropped one there, else the icon shipped with the app."""
    for p in (config.icon_override_path(), ASSETS / "icon.png"):
        if p.is_file():
            return p
    return None


def app_icon() -> QIcon:
    p = icon_path()
    return QIcon(str(p)) if p else QIcon()
