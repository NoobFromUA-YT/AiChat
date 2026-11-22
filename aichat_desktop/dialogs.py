"""Dialogs: first run, API key, settings, chat history. Strings and layout only; storage lives in config / chats."""

from __future__ import annotations

import time
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from . import chats, config, themes
from .display import S


def _wrapped(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setContentsMargins(0, 0, 0, S(3))          # a wrapped label's last line was clipped by a pixel or two
    return lbl


def _key_row(parent_layout: QVBoxLayout) -> QLineEdit:
    edit = QLineEdit()
    edit.setEchoMode(QLineEdit.EchoMode.Password)
    edit.setMinimumWidth(S(520))
    edit.setPlaceholderText("sk-ant-\u2026")
    parent_layout.addWidget(edit)
    show = QCheckBox("Show the key")
    show.toggled.connect(lambda on: edit.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
    parent_layout.addWidget(show)
    return edit


def _error_label() -> QLabel:
    err = QLabel("")
    err.setObjectName("errLbl")
    err.setWordWrap(True)
    return err


class KeyDialog(QDialog):
    """Enter a new API key, or go back to the default one. result_key: new key, '' = default, None = cancelled."""

    def __init__(self, parent: QWidget | None, current: str, own: bool) -> None:
        super().__init__(parent)
        self.setWindowTitle("API key")
        self.result_key: str | None = None
        lay = QVBoxLayout(self)
        lay.setSpacing(S(8))
        lay.addWidget(QLabel(f"In use now: {config.mask_key(current)} ({'entered here' if own else 'from the environment / none'})"))
        lay.addWidget(QLabel("New Anthropic API key:"))
        self.edit = _key_row(lay)
        lay.addWidget(_wrapped(
            f"Saved only on this PC, in {config.env_path()} (readable by you only). Old chats keep working; the credit "
            "counter belongs to the old key's account, so you set it again after."))
        self.error = _error_label()
        lay.addWidget(self.error)
        box = QDialogButtonBox()
        self.save_btn = box.addButton("Use this key", QDialogButtonBox.ButtonRole.AcceptRole)
        self.default_btn = box.addButton("Remove saved key", QDialogButtonBox.ButtonRole.ResetRole)
        self.default_btn.setEnabled(own)
        box.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.save_btn.clicked.connect(self.accept_key)
        self.default_btn.clicked.connect(self.accept_default)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def accept_key(self) -> None:
        key = self.edit.text().strip()
        err = config.key_format_error(key)
        if err:
            self.error.setText(err)
            return
        self.result_key = key
        self.accept()

    def accept_default(self) -> None:
        self.result_key = ""
        self.accept()


class FirstRunDialog(QDialog):
    """Shown once: how to sign in (subscription or API key) and the starting credit for the counter.
    After exec(): result_key ('' = use the signed-in `claude`), result_budget (0 = no counter)."""

    def __init__(self, parent: QWidget | None, claude_path: str | None, has_env_key: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("Welcome to AI Chat")
        self.setMinimumWidth(S(560))          # wrapped labels need a real width, else their last line is clipped
        self.result_key = ""
        self.result_budget = 0.0
        lay = QVBoxLayout(self)
        lay.setSpacing(S(10))
        lay.addWidget(_wrapped("A chat window for Claude Code. Two things to set up (you can change both later)."))
        if claude_path:
            lay.addWidget(_wrapped(f"Claude Code found: {claude_path}"))
        else:
            warn = _wrapped(config.CLAUDE_MISSING)
            warn.setObjectName("warnLbl")
            lay.addWidget(warn)

        lay.addWidget(QLabel("1. How do you pay for Claude?"))
        self.r_sub = QRadioButton("I am signed in with a Claude subscription (run `claude` once in a terminal)")
        self.r_key = QRadioButton("I use an Anthropic API key")
        lay.addWidget(self.r_sub)
        lay.addWidget(self.r_key)
        self.key_edit = _key_row(lay)
        self.key_edit.setEnabled(False)
        self.r_key.toggled.connect(self.key_edit.setEnabled)
        (self.r_key if has_env_key else self.r_sub).setChecked(True)
        if has_env_key:
            lay.addWidget(_wrapped("An ANTHROPIC_API_KEY is already set in your environment; leave the field empty to keep using it."))

        lay.addWidget(QLabel("2. Credit counter (optional)"))
        lay.addWidget(_wrapped("Enter the credit on your Anthropic account in USD (console.anthropic.com > Billing) and the "
                               "window shows how much is left as a percentage. 0 = no counter."))
        self.budget = QDoubleSpinBox()
        self.budget.setRange(0.0, 1_000_000.0)
        self.budget.setDecimals(2)
        self.budget.setPrefix("$ ")
        lay.addWidget(self.budget)
        self.error = _error_label()
        lay.addWidget(self.error)
        box = QDialogButtonBox()
        self.ok_btn = box.addButton("Start", QDialogButtonBox.ButtonRole.AcceptRole)
        self.skip_btn = box.addButton("Skip", QDialogButtonBox.ButtonRole.RejectRole)
        self.ok_btn.clicked.connect(self.accept_values)
        self.skip_btn.clicked.connect(self.reject)
        lay.addWidget(box)

    def showEvent(self, ev) -> None:  # noqa: N802
        super().showEvent(ev)
        lay = self.layout()
        need = lay.totalHeightForWidth(self.width())     # wrapped labels are taller when the dialog is narrow
        if need > self.height():
            self.resize(self.width(), need)

    def accept_values(self) -> None:
        key = self.key_edit.text().strip() if self.r_key.isChecked() else ""
        if self.r_key.isChecked() and key:
            err = config.key_format_error(key)
            if err:
                self.error.setText(err)
                return
        self.result_key = key
        self.result_budget = float(self.budget.value())
        self.accept()


class SettingsDialog(QDialog):
    """Path of the claude CLI and the starting credit. result: dict of the changed settings."""

    def __init__(self, parent: QWidget | None, settings: dict) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.result_settings: dict | None = None
        lay = QVBoxLayout(self)
        lay.setSpacing(S(8))
        lay.addWidget(QLabel("Claude Code command (empty = find `claude` automatically):"))
        row = QHBoxLayout()
        self.path = QLineEdit(settings.get("claude_path", ""))
        self.path.setPlaceholderText(config.find_claude() or "not found")
        self.path.setMinimumWidth(S(420))
        browse = QPushButton("Browse\u2026")
        browse.clicked.connect(self.browse)
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        lay.addLayout(row)
        lay.addWidget(QLabel("Credit that counts as 100 % in the counter (USD, 0 = hide the counter):"))
        self.budget = QDoubleSpinBox()
        self.budget.setRange(0.0, 1_000_000.0)
        self.budget.setDecimals(2)
        self.budget.setPrefix("$ ")
        self.budget.setValue(float(settings.get("budget", 0.0)))
        lay.addWidget(self.budget)
        lay.addWidget(QLabel("Colours:"))
        self.theme = QComboBox()
        for key, label in themes.CHOICES:
            self.theme.addItem(label, key)
        self.theme.setCurrentIndex(max(0, self.theme.findData(settings.get("theme", themes.DEFAULT))))
        lay.addWidget(self.theme)
        self.error = _error_label()
        lay.addWidget(self.error)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(self.accept_values)
        box.rejected.connect(self.reject)
        lay.addWidget(box)
        self.settings = settings

    def browse(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Choose the claude command")
        if f:
            self.path.setText(f)

    def accept_values(self) -> None:
        p = self.path.text().strip()
        if p and not Path(p).expanduser().is_file():
            self.error.setText("That file does not exist or is not executable.")
            return
        self.result_settings = {"claude_path": p, "budget": float(self.budget.value()), "theme": self.theme.currentData()}
        self.accept()


class ChatsDialog(QDialog):
    """Saved chats: open, rename, delete. After exec(): open_id (or None), deleted (ids removed here)."""

    def __init__(self, parent: QWidget | None, current_id: str | None, busy: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("Chats")
        self.current_id = current_id
        self.open_id: str | None = None
        self.deleted: list[str] = []
        self.resize(S(640), S(460))
        lay = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self.open_selected())
        lay.addWidget(self.list, 1)
        self.hint = QLabel("A reply is running: finish or stop it before switching chats." if busy else "")
        lay.addWidget(self.hint)
        row = QHBoxLayout()
        self.open_btn = QPushButton("Open")
        self.rename_btn = QPushButton("Rename\u2026")
        self.delete_btn = QPushButton("Delete")
        close = QPushButton("Close")
        self.open_btn.setEnabled(not busy)
        self.open_btn.clicked.connect(self.open_selected)
        self.rename_btn.clicked.connect(self.rename_selected)
        self.delete_btn.clicked.connect(self.delete_selected)
        close.clicked.connect(self.reject)
        for b in (self.open_btn, self.rename_btn, self.delete_btn):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close)
        lay.addLayout(row)
        self.refill()

    def refill(self) -> None:
        self.list.clear()
        for c in chats.list_chats():
            when = time.strftime("%d %b %Y %H:%M", time.localtime(c["updated"]))
            where = f" \u00b7 {Path(c['cwd']).name or c['cwd']}" if c["cwd"] else ""
            mark = "\u25cf " if c["id"] == self.current_id else ""
            item = QListWidgetItem(f"{mark}{c['title']}\n{when} \u00b7 {c['n']} messages{where}")
            item.setData(Qt.ItemDataRole.UserRole, c["id"])
            item.setToolTip(c["cwd"] or "")
            self.list.addItem(item)
        has = self.list.count() > 0
        if has:
            self.list.setCurrentRow(0)
        for b in (self.rename_btn, self.delete_btn):
            b.setEnabled(has)

    def selected_id(self) -> str | None:
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def open_selected(self) -> None:
        cid = self.selected_id()
        if cid and self.open_btn.isEnabled():
            self.open_id = cid
            self.accept()

    def rename_selected(self) -> None:
        cid = self.selected_id()
        if not cid:
            return
        old = next((c["title"] for c in chats.list_chats() if c["id"] == cid), "")
        title, ok = QInputDialog.getText(self, "Rename chat", "Name:", text=old)
        if ok and title.strip():
            chats.rename(cid, title)
            self.refill()

    def delete_selected(self) -> None:
        cid = self.selected_id()
        if not cid:
            return
        if QMessageBox.question(self, "Delete chat", "Delete this chat for good?") == QMessageBox.StandardButton.Yes:
            chats.delete(cid)
            self.deleted.append(cid)
            self.refill()
