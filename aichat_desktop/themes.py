"""Colour themes. No Qt: a theme is a dict of colours, `stylesheet()` fills the window's style sheet from it.

* **classic** - dark window, light chat area (the look of versions 0.1 and 0.2)
* **dark**    - dark everywhere, including the chat area
* **light**   - light everywhere
* **system**  - dark or light, whatever the desktop uses (see `resolve`)
"""

from __future__ import annotations

import re

CHOICES = [
    ("system", "Follow the system"),
    ("dark", "Dark"),
    ("light", "Light"),
    ("classic", "Dark window, light chat"),
]
DEFAULT = "system"

CLASSIC: dict[str, str] = {
    "bg": "#181a1f", "fg": "#e6e8ec", "muted": "#9aa0ab", "placeholder": "#8c939d",
    "panel": "#22262e", "btn": "#2a2f3a", "btn_hover": "#353b49", "border": "#3e4452", "border_soft": "#30343f",
    "border_input": "#3a3f4b", "accent": "#4c8eda", "sel": "#35527a", "sel_fg": "#ffffff", "disabled_fg": "#777777",
    "scroll": "#4a5060", "scroll_hover": "#60687d", "arrow": "#c8ccd4",
    "ready_bg": "#1f3a2a", "ready_fg": "#8be0a4", "ready_border": "#2c5a3f",
    "warn_bg": "#6b4e12", "warn_fg": "#ffd166", "warn_border": "#8c6a1c",
    "busy_bg": "#d13438", "busy_border": "#f05a5e", "on_color": "#ffffff",
    "send_bg": "#2f6fb8", "send_hover": "#3a7fcc", "stop_bg": "#7a2326",
    "chat_bg": "#ffffff", "chat_fg": "#1a1a1a", "chat_border": "#30343f", "live_tool": "#107c10",
    "card_user": "#2b79d6", "card_user_bg": "#eef4fc", "card_assistant": "#107c10", "card_assistant_bg": "#ffffff",
    "card_error": "#d13438", "card_error_bg": "#fdf1f1", "card_note": "#8a909c", "card_note_bg": "#f3f4f7",
    "meta": "#8a909c", "link": "#0b62c4",
    "md_code_bg": "#eef0f3", "md_border": "#c9ced8", "md_head_bg": "#e9ecf1", "md_muted": "#5f6672",
    "hint": "#9aa0ab", "error_fg": "#ff7b7f", "notice_fg": "#ffb86b",
}

DARK: dict[str, str] = {
    **CLASSIC,
    "chat_bg": "#1d2026", "chat_fg": "#e6e8ec", "chat_border": "#30343f", "live_tool": "#5fd27a",
    "card_user": "#4c8eda", "card_user_bg": "#222e41", "card_assistant": "#4fbf67", "card_assistant_bg": "#262a32",
    "card_error": "#f05a5e", "card_error_bg": "#3a2326", "card_note": "#8a909c", "card_note_bg": "#2a2d34",
    "meta": "#9aa0ab", "link": "#6cb0ff",
    "md_code_bg": "#14161a", "md_border": "#3a3f4b", "md_head_bg": "#2a2f3a", "md_muted": "#a4abb7",
}

LIGHT: dict[str, str] = {
    **CLASSIC,
    "bg": "#f3f4f7", "fg": "#1a1a1a", "muted": "#5f6672", "placeholder": "#7b828e",
    "panel": "#ffffff", "btn": "#e8eaee", "btn_hover": "#dde1e8", "border": "#c3c8d2", "border_soft": "#cfd3db",
    "border_input": "#c3c8d2", "accent": "#2b79d6", "sel": "#cfe2fb", "sel_fg": "#0a2a55", "disabled_fg": "#9aa0ab",
    "scroll": "#b6bcc8", "scroll_hover": "#9aa2b1", "arrow": "#4a5060",
    "ready_bg": "#dff3e4", "ready_fg": "#146c2e", "ready_border": "#a9d9b6",
    "warn_bg": "#fff1cc", "warn_fg": "#7a5200", "warn_border": "#e6c46a",
    "send_bg": "#2b79d6", "send_hover": "#1f67bd", "stop_bg": "#d13438",
    "chat_border": "#cfd3db", "hint": "#6b7280", "error_fg": "#b3261e", "notice_fg": "#8a5a00",
}

THEMES = {"classic": CLASSIC, "dark": DARK, "light": LIGHT}

TEMPLATE = """
QMainWindow, #root { background: @bg@; color: @fg@; }
QLabel { background: transparent; color: @fg@; }
QDialog { background: @bg@; color: @fg@; }
QCheckBox, QRadioButton { color: @fg@; background: transparent; spacing: 8px; }
QCheckBox::indicator, QRadioButton::indicator { width: 16px; height: 16px; background: @panel@; border: 1px solid @border@; border-radius: 4px; }
QRadioButton::indicator { border-radius: 9px; }
QCheckBox::indicator:hover, QRadioButton::indicator:hover { border-color: @accent@; }
QCheckBox::indicator:checked, QRadioButton::indicator:checked { background: @accent@; border-color: @accent@; }
QCheckBox::indicator:disabled, QRadioButton::indicator:disabled { background: @btn@; border-color: @border_soft@; }
QLineEdit, QDoubleSpinBox, QListWidget { background: @panel@; color: @fg@; border: 1px solid @border_input@; border-radius: 4px; padding: 4px; selection-background-color: @sel@; selection-color: @sel_fg@; }
QListWidget::item { padding: 8px 6px; border-bottom: 1px solid @border_soft@; }
QListWidget::item:selected { background: @sel@; color: @sel_fg@; }
#errLbl { color: @error_fg@; }
#warnLbl { color: @notice_fg@; }
#chatView, #chatView QWidget { background: @chat_bg@; color: @chat_fg@; }
#chatView { border: 1px solid @chat_border@; border-radius: 6px; padding: 10px; }
#inputBox, #inputBox QWidget { background: @panel@; color: @fg@; }
#inputBox { border: 1px solid @border_input@; border-radius: 6px; padding: 6px; }
#inputBox:focus { border: 1px solid @accent@; }
QComboBox, QPushButton {
    background: @btn@; color: @fg@;
    border: 1px solid @border@; border-radius: 5px; padding: 6px 10px;
}
QComboBox::drop-down { width: 22px; border: none; }
QComboBox::down-arrow { image: url(@ARROW@); width: 12px; height: 12px; }
QComboBox:hover, QPushButton:hover { background: @btn_hover@; border-color: @accent@; }
QPushButton:pressed { background: @panel@; }
QPushButton:disabled { color: @disabled_fg@; background: @panel@; border-color: @border_soft@; }
QComboBox QAbstractItemView {
    background: @panel@; color: @fg@; border: 1px solid @border@;
    selection-background-color: @sel@; selection-color: @sel_fg@;
}
QMenu { background: @panel@; color: @fg@; border: 1px solid @border@; padding: 4px 0; }
QMenu::item { padding: 5px 24px; }
QMenu::item:selected { background: @sel@; color: @sel_fg@; }
QScrollBar:vertical { background: @bg@; width: 12px; margin: 0; }
QScrollBar:horizontal { background: @bg@; height: 12px; margin: 0; }
QScrollBar::handle:vertical { background: @scroll@; min-height: 24px; border-radius: 4px; margin: 2px; }
QScrollBar::handle:horizontal { background: @scroll@; min-width: 24px; border-radius: 4px; margin: 2px; }
QScrollBar::handle:hover { background: @scroll_hover@; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: @bg@; }
#badge {
    border-radius: 12px; padding: 4px 14px; font-weight: bold;
    background: @ready_bg@; color: @ready_fg@; border: 1px solid @ready_border@;
}
#badge[state="busy"] { background: @busy_bg@; color: @on_color@; border: 1px solid @busy_border@; }
#badge[state="paused"] { background: @warn_bg@; color: @warn_fg@; border: 1px solid @warn_border@; }
#queueRow { background: @panel@; border: 1px solid @border_input@; border-radius: 6px; }
#queueRow QLabel { background: transparent; }
#queueTag { color: @warn_fg@; font-weight: bold; }
#xbtn { padding: 0px; border-radius: 4px; background: @btn@; }
#xbtn:hover { background: @busy_bg@; border-color: @busy_border@; color: @on_color@; }
#sendBtn { background: @send_bg@; border-color: @accent@; color: @on_color@; font-weight: bold; }
#sendBtn:hover { background: @send_hover@; }
#sendBtn[queueing="true"] { background: @warn_bg@; border-color: @warn_border@; color: @warn_fg@; }
#liveBox { background: @chat_bg@; border: 1px solid @chat_border@; border-radius: 6px; }
#liveBox QWidget { background: @chat_bg@; color: @chat_fg@; }
#liveTool { color: @live_tool@; font-weight: bold; }
#folderBtn { text-align: left; }
#ctxLbl { color: @muted@; }
#ctxLbl[level="warn"] { color: @warn_fg@; font-weight: bold; }
#compactBtn[level="warn"] { border: 1px solid @warn_border@; color: @warn_fg@; }
#stopBtn:enabled { background: @stop_bg@; border-color: @busy_bg@; color: @on_color@; }
"""


def resolve(choice: str, system_dark: bool | None = None) -> str:
    """The concrete theme name for a setting value ('system' follows `system_dark`; unknown = classic)."""
    if choice == "system":
        return "classic" if system_dark is None else ("dark" if system_dark else "light")
    return choice if choice in THEMES else "classic"


def get(choice: str, system_dark: bool | None = None) -> dict[str, str]:
    return THEMES[resolve(choice, system_dark)]


def stylesheet(theme: dict[str, str], arrow_path: str = "") -> str:
    """The window's style sheet in `theme`'s colours (px values are scaled by the caller)."""
    sheet = TEMPLATE.replace("@ARROW@", arrow_path)
    return re.sub(r"@([a-z_]+)@", lambda m: theme[m.group(1)], sheet)
