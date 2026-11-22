"""The chat window (PyQt6) around the `claude -p` CLI. Run: `aichat-desktop` or `python3 -m aichat_desktop`."""

from __future__ import annotations

import html
import os
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QBuffer, QIODevice, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QGuiApplication, QFont, QFontMetrics, QImage, QKeyEvent, QPalette, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QTextEdit,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from . import __version__, chats, config, display, mdhtml, pasting, themes
from .costs import (
    CTX_WARN,
    balance_label,
    balance_left,
    balance_pct,
    context_tokens,
    fmt_tokens,
    last_compact,
    load_usage,
    save_usage,
    set_anchor,
)
from .dialogs import ChatsDialog, EditMessageDialog, FirstRunDialog, KeyDialog, SettingsDialog
from .display import S
from .export import export_markdown
from .mdhtml import md_to_html, plain_html
from .worker import ChatWorker, cleanup_images


class ClickLabel(QLabel):
    """A QLabel that emits clicked on a left-button release."""

    clicked = pyqtSignal()

    def mouseReleaseEvent(self, ev) -> None:  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(ev)


class PromptEdit(QTextEdit):
    """Message box: Enter sends / pushes, Ctrl+Enter queues, Shift+Enter adds a line. Pastes plain text and screenshots only."""

    send_requested = pyqtSignal()
    queue_requested = pyqtSignal()
    image_pasted = pyqtSignal(QImage)

    def __init__(self, *args) -> None:
        super().__init__(*args)
        self.setAcceptRichText(False)          # typed and dropped text stays plain too

    def keyPressEvent(self, event: QKeyEvent | None) -> None:  # noqa: N802
        if event and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            mods = event.modifiers()
            if mods & Qt.KeyboardModifier.ControlModifier:
                self.queue_requested.emit()
                return
            if not (mods & Qt.KeyboardModifier.ShiftModifier):
                self.send_requested.emit()
                return
        super().keyPressEvent(event)

    def canInsertFromMimeData(self, source) -> bool:  # noqa: N802
        return bool(source and (source.hasText() or source.hasHtml() or source.hasImage() or source.hasUrls()))

    def insertFromMimeData(self, source) -> None:  # noqa: N802
        """Ctrl+V, Shift+Insert, the Paste menu entry and drag & drop all end here."""
        if source is None:
            return
        text = pasting.plain_text_of(source)
        if text:
            self.insertPlainText(text)
            return
        if source.hasImage():
            img = source.imageData()
            if isinstance(img, QImage) and not img.isNull():
                self.image_pasted.emit(img)


class AIChatWindow(QMainWindow):
    """Main desktop chat window."""

    MODELS = [
        ("Claude Sonnet", "claude", "sonnet"),
        ("Claude Opus", "claude", "opus"),
        ("Claude Haiku", "claude", "haiku"),
    ]
    def __init__(self) -> None:
        super().__init__()
        self.base_title = "AI Chat (Claude)"
        self.setWindowTitle(self.base_title)
        self.setWindowIcon(display.app_icon())
        self.resize(S(900), S(760))
        self.settings = config.load_settings()
        self.env = config.load_env()
        self.usage = load_usage(float(self.settings["budget"]))
        self.cwd: str = self.settings["cwd"] if Path(self.settings["cwd"] or "/nonexistent").is_dir() else ""
        self.chat_id: str | None = None
        self.code_blocks: list[str] = []      # raw text behind the "Copy" links of the open chat
        self.claude_sid: str | None = None
        self.claude_cost_base: float | None = 0.0   # running total_cost_usd of the current claude session
        self.pending_images: list[tuple[str, bytes, str]] = []
        self.worker: ChatWorker | None = None
        # run state: one reply at a time, more messages wait in `queue`
        self.busy = False
        self.stopping = False
        self.compacting = False       # the running call is a /compact, not a chat reply
        self.epoch = 0                # New Chat bumps it: a late reply of an older chat is not shown
        self.queue: list[tuple[str, list[tuple[str, bytes]]]] = []
        self.queue_paused = False     # after Stop or an error the queue waits for the user
        self.pushed: list[tuple[str, list[tuple[str, bytes]]]] = []   # handed to the running reply, not yet taken in by Claude
        self.row_push_btns: list[QPushButton] = []
        self.t0 = 0.0
        self.ready_text = "\u25cf Ready"
        self.messages: list[dict] = []
        self.showing_hint = False

        root = QWidget()
        root.setObjectName("root")
        root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(S(12), S(12), S(12), S(12))
        layout.setSpacing(S(8))

        top = QHBoxLayout()
        top.setSpacing(S(8))
        self.model_box = QComboBox()
        for label, _, _ in self.MODELS:
            self.model_box.addItem(label)
        idx = next((i for i, m in enumerate(self.MODELS) if m[2] == self.settings["model"]), 0)
        self.model_box.setCurrentIndex(idx)
        self.model_box.currentIndexChanged.connect(self.on_pick_changed)
        top.addWidget(self.model_box)

        self.effort_box = QComboBox()
        self.effort_box.addItems(["High", "Medium", "Low"])
        self.effort_box.setToolTip("Thinking / Effort level")
        self.effort_box.setCurrentText(self.settings["effort"] if self.settings["effort"] in ("High", "Medium", "Low") else "High")
        self.effort_box.currentIndexChanged.connect(self.on_pick_changed)
        top.addWidget(self.effort_box)

        self.perm_box = QComboBox()
        for key, (label, tip, _) in config.PERMISSION_MODES.items():
            self.perm_box.addItem(label, key)
            self.perm_box.setItemData(self.perm_box.count() - 1, tip, Qt.ItemDataRole.ToolTipRole)
        self.perm_box.setCurrentIndex(max(0, self.perm_box.findData(self.settings["permission"])))
        self.perm_box.activated.connect(self.on_permission_picked)
        top.addWidget(self.perm_box)

        self.folder_btn = QPushButton("")
        self.folder_btn.setObjectName("folderBtn")
        self.folder_btn.clicked.connect(self.choose_folder)
        top.addWidget(self.folder_btn)
        top.addStretch(1)

        self.badge = QLabel("")
        self.badge.setObjectName("badge")
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge.setMinimumWidth(S(200))
        top.addWidget(self.badge)
        layout.addLayout(top)

        top2 = QHBoxLayout()
        top2.setSpacing(S(8))
        self.ctx_lbl = QLabel("")
        self.ctx_lbl.setObjectName("ctxLbl")
        top2.addWidget(self.ctx_lbl)

        self.balance_lbl = ClickLabel("")
        self.balance_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self.balance_lbl.clicked.connect(self.calibrate_balance)
        top2.addWidget(self.balance_lbl)
        top2.addStretch(1)
        self.update_balance_lbl()

        self.chats_btn = QPushButton("Chats")
        self.chats_btn.setToolTip("Saved chats: open an old one, rename, delete")
        self.chats_btn.clicked.connect(self.show_chats)
        top2.addWidget(self.chats_btn)

        self.export_btn = QPushButton("Export")
        self.export_btn.setToolTip("Save this chat as a Markdown file")
        self.export_btn.clicked.connect(self.export_chat)
        top2.addWidget(self.export_btn)

        self.key_btn = QPushButton("API key")
        self.key_btn.setObjectName("keyBtn")
        self.key_btn.clicked.connect(self.change_key)
        top2.addWidget(self.key_btn)

        self.settings_btn = QPushButton("Settings")
        self.settings_btn.clicked.connect(self.show_settings)
        top2.addWidget(self.settings_btn)

        self.compact_btn = QPushButton("Compact")
        self.compact_btn.setObjectName("compactBtn")
        self.compact_btn.setToolTip("Summarise the chat so far (keeps the same chat, makes every next reply cheaper)")
        self.compact_btn.clicked.connect(self.compact_chat)
        top2.addWidget(self.compact_btn)

        self.new_btn = QPushButton("New Chat")
        self.new_btn.clicked.connect(self.reset_chat)
        top2.addWidget(self.new_btn)
        layout.addLayout(top2)
        self.refresh_folder_ui()

        self.chat_view = QTextBrowser()
        self.chat_view.setObjectName("chatView")
        self.chat_view.setOpenLinks(False)                 # http(s) links are opened by on_link, "Copy" links copy
        self.chat_view.anchorClicked.connect(self.on_link)
        layout.addWidget(self.chat_view, 1)

        # live view of the running reply: the tool Claude is using and the text so far
        self.live_box = QFrame()
        self.live_box.setObjectName("liveBox")
        live_lay = QVBoxLayout(self.live_box)
        live_lay.setContentsMargins(S(10), S(6), S(10), S(6))
        live_lay.setSpacing(S(4))
        self.live_tool = QLabel("")
        self.live_tool.setObjectName("liveTool")
        self.live_text = QTextBrowser()
        self.live_text.setObjectName("liveText")
        self.live_text.setFixedHeight(S(36))      # grows with the text up to S(190), see flush_live_text
        self.live_text.setFrameShape(QFrame.Shape.NoFrame)
        live_lay.addWidget(self.live_tool)
        live_lay.addWidget(self.live_text)
        self.live_box.setVisible(False)
        layout.addWidget(self.live_box)
        self.live_pending: str | None = None
        self.live_timer = QTimer(self)
        self.live_timer.setSingleShot(True)
        self.live_timer.setInterval(90)           # several deltas arrive per paint: render the newest text only
        self.live_timer.timeout.connect(self.flush_live_text)

        self.queue_box = QWidget()
        self.queue_lay = QVBoxLayout(self.queue_box)
        self.queue_lay.setContentsMargins(0, 0, 0, 0)
        self.queue_lay.setSpacing(S(4))
        self.queue_box.setVisible(False)
        layout.addWidget(self.queue_box)

        self.attach_bar = QHBoxLayout()
        self.attach_lbl = QLabel("")
        self.attach_lbl.setVisible(False)
        self.clear_img_btn = QPushButton("Clear images")
        self.clear_img_btn.setVisible(False)
        self.clear_img_btn.clicked.connect(self.clear_images)
        self.attach_bar.addWidget(self.attach_lbl)
        self.attach_bar.addWidget(self.clear_img_btn)
        self.attach_bar.addStretch()
        layout.addLayout(self.attach_bar)

        bottom = QHBoxLayout()
        bottom.setSpacing(S(8))
        self.attach_btn = QPushButton("+")
        self.attach_btn.setToolTip("Attach screenshot/image")
        self.attach_btn.setMinimumHeight(S(40))
        self.attach_btn.setFixedWidth(S(40))
        self.attach_btn.clicked.connect(self.pick_image)
        bottom.addWidget(self.attach_btn)

        self.input_box = PromptEdit()
        self.input_box.setObjectName("inputBox")
        self.input_box.setMaximumHeight(S(84))
        self.input_box.send_requested.connect(self.send_message)
        self.input_box.queue_requested.connect(self.queue_message)
        self.input_box.image_pasted.connect(self.add_qimage)
        bottom.addWidget(self.input_box, 1)

        self.send_btn = QPushButton("Send")
        self.send_btn.setObjectName("sendBtn")
        self.send_btn.setMinimumHeight(S(40))
        self.send_btn.setMinimumWidth(S(76))
        self.send_btn.clicked.connect(lambda _=False: self.send_message())
        bottom.addWidget(self.send_btn)

        self.queue_btn = QPushButton("Queue")           # only while Claude works: send after the reply instead of pushing
        self.queue_btn.setObjectName("queueBtn")
        self.queue_btn.setMinimumHeight(S(40))
        self.queue_btn.setMinimumWidth(S(76))
        self.queue_btn.setToolTip("Send this message when the running reply is done (Ctrl+Enter)")
        self.queue_btn.clicked.connect(lambda _=False: self.queue_message())
        self.queue_btn.setVisible(False)
        bottom.addWidget(self.queue_btn)

        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setMinimumHeight(S(40))
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self.stop_generation)
        bottom.addWidget(self.stop_btn)
        layout.addLayout(bottom)

        self.busy_timer = QTimer(self)
        self.busy_timer.setInterval(1000)
        self.busy_timer.timeout.connect(self.refresh_badge)

        self.theme_name = "classic"
        self.theme = themes.CLASSIC
        self.apply_theme()
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):           # Qt 6.5+: follow the desktop's dark / light switch live
            hints.colorSchemeChanged.connect(lambda *_: self.settings.get("theme") == "system" and self.apply_theme())

        if self.restore_chat():
            self.ready_text = "\u25cf Restored chat"
        else:
            self.show_hint()
        self.refresh_ctx()
        self.refresh_badge()
        self.refresh_key_ui()

    def add_qimage(self, img: QImage) -> None:
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        self.pending_images.append(("image/png", bytes(buf.data()), f"clipboard-{len(self.pending_images) + 1}.png"))
        self.update_attach_ui()

    def pick_image(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, "Attach Screenshot / Image", str(Path.home() / "Desktop" / "Screenshots"),
            "Images (*.png *.jpg *.jpeg *.webp)"
        )
        for fp in files:
            p = Path(fp)
            mime = "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"
            self.pending_images.append((mime, p.read_bytes(), p.name))
        self.update_attach_ui()

    def clear_images(self) -> None:
        self.pending_images.clear()
        self.update_attach_ui()

    def update_attach_ui(self) -> None:
        n = len(self.pending_images)
        self.attach_lbl.setVisible(n > 0)
        self.clear_img_btn.setVisible(n > 0)
        if n > 0:
            names = ", ".join(name for _, _, name in self.pending_images)
            self.attach_lbl.setText(f"Attached ({n}): {names}")

    def _balance_key(self) -> tuple[str, float]:
        return ("claude", float(self.settings["budget"]))

    def update_balance_lbl(self) -> None:
        key, budget = self._balance_key()
        self.balance_lbl.setVisible(budget > 0)      # no starting credit entered = no counter
        if budget <= 0:
            return
        left, since, a_bal = balance_left(self.usage, key, budget)
        msgs = int(self.usage.get(f"{key}_msgs", 0))
        avg = (float(self.usage.get(f"{key}_spent", 0.0)) / msgs) if msgs > 0 else 0.03
        est_msgs = int(left / avg) if avg > 0 else 0
        ts = self.usage.get(f"{key}_anchor_ts")
        when = time.strftime("%d %b %H:%M", time.localtime(ts)) if ts else "starting credit"
        unc = int(self.usage.get(f"{key}_uncounted", 0))
        self.balance_lbl.setText(balance_label(balance_pct(left, budget)) + ("*" if unc else ""))
        warn = (f"\n* {unc} reply(s) cost money but could not be priced (restored chat), so the real balance is lower "
                "than shown. Click and enter the console value to correct it.") if unc else ""
        self.balance_lbl.setToolTip(
            f"About ${left:.2f} of ${budget:.0f} (100 %). ${a_bal:.2f} real balance ({when}) minus ${since:.4f} spent here since "
            f"({msgs} msgs, ~{est_msgs:,} left at ${avg:.4f}/msg).\n"
            "Other users of the same API key are not counted. Click to set the real balance from the console." + warn
        )

    # ---- API key, settings, first run -------------------------------------------------------------------------------
    def refresh_key_ui(self) -> None:
        key = self.env.get("ANTHROPIC_API_KEY", "")
        src = "saved by this app" if config.load_own_key() else ("from the environment" if key else "none: using the signed-in claude")
        self.key_btn.setToolTip(f"API key in use: {config.mask_key(key)} ({src}). Click to enter another one.")

    def change_key(self) -> None:
        """Let the user type another API key (or remove the saved one); later replies use it."""
        own = config.load_own_key()
        dlg = KeyDialog(self, self.env.get("ANTHROPIC_API_KEY", ""), bool(own))
        if dlg.exec() != QDialog.DialogCode.Accepted or dlg.result_key is None:
            return
        self.apply_key(dlg.result_key)

    def store_key(self, key: str) -> bool:
        """Save `key` ('' = remove the saved one). A key the server rejects is not saved. False = nothing changed."""
        if key:
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)       # the check is one short web request
            try:
                ok = config.check_key_online(key)
            finally:
                QApplication.restoreOverrideCursor()
            if ok is False:
                QMessageBox.warning(self, "API key", "Anthropic rejected this key (invalid or revoked). Nothing was changed.")
                return False
            if ok is None and QMessageBox.question(
                self, "API key", "Could not reach Anthropic to check the key. Use it anyway?"
            ) != QMessageBox.StandardButton.Yes:
                return False
        config.save_own_key(key)
        self.env = config.load_env()
        self.refresh_key_ui()
        return True

    def apply_key(self, key: str) -> None:
        if not self.store_key(key):
            return
        shown = config.mask_key(self.env.get("ANTHROPIC_API_KEY", ""))
        self.append_msg("", f"API key changed: next replies use {shown}." if key else "Saved API key removed.", "note", store=False)
        if self.settings["budget"] > 0 and QMessageBox.question(
            self, "API key", "The credit counter still counts the old key's account. Enter the balance of this key's account now?"
        ) == QMessageBox.StandardButton.Yes:
            self.calibrate_balance()

    def claude_path(self) -> str | None:
        return config.find_claude(self.settings.get("claude_path", ""))

    def show_settings(self) -> None:
        dlg = SettingsDialog(self, self.settings)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.result_settings:
            self.apply_settings(dlg.result_settings)

    def apply_settings(self, changes: dict) -> None:
        old_budget = float(self.settings["budget"])
        old_theme = self.settings.get("theme")
        self.settings.update(changes)
        config.save_settings(self.settings)
        if self.settings.get("theme") != old_theme:
            self.apply_theme()
        if float(self.settings["budget"]) > 0 and old_budget <= 0 and "claude_anchor_balance" not in self.usage:
            set_anchor(self.usage, float(self.settings["budget"]))
            save_usage(self.usage)
        self.update_balance_lbl()
        self.show_hint_if_empty()

    def maybe_first_run(self) -> None:
        """Ask once for the sign-in method and the starting credit instead of making the user edit files."""
        if self.settings["first_run_done"]:
            return
        dlg = FirstRunDialog(self, self.claude_path(), bool(os.environ.get("ANTHROPIC_API_KEY")))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if dlg.result_key:
                self.store_key(dlg.result_key)
            self.settings["budget"] = dlg.result_budget
            if dlg.result_budget > 0:
                set_anchor(self.usage, dlg.result_budget)
                save_usage(self.usage)
            self.update_balance_lbl()
        self.settings["first_run_done"] = True
        config.save_settings(self.settings)
        self.show_hint_if_empty()

    # ---- model / permission / project folder -----------------------------------------------------------------------
    def on_pick_changed(self) -> None:
        self.settings["model"] = self.MODELS[self.model_box.currentIndex()][2]
        self.settings["effort"] = self.effort_box.currentText()
        config.save_settings(self.settings)
        self.update_balance_lbl()

    def permission(self) -> str:
        return self.perm_box.currentData() or config.DEFAULT_PERMISSION

    def on_permission_picked(self, _idx: int = 0) -> None:
        mode = self.permission()
        if mode == "full" and self.settings["permission"] != "full":
            warn = QMessageBox.warning(
                self, "Full auto",
                "Claude will edit and delete files and run any command in the project folder without asking you.\n\n"
                f"Folder: {self.cwd or str(Path.home())}\n\nTurn on Full auto?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if warn != QMessageBox.StandardButton.Yes:
                self.perm_box.setCurrentIndex(max(0, self.perm_box.findData(self.settings["permission"])))
                return
        self.settings["permission"] = mode
        config.save_settings(self.settings)
        self.refresh_folder_ui()

    def refresh_folder_ui(self) -> None:
        folder = self.cwd or str(Path.home())
        name = Path(folder).name or folder
        self.folder_btn.setText("\U0001F4C1 " + (name if len(name) <= 22 else name[:21] + "…"))
        self.folder_btn.setToolTip(f"Claude works in: {folder}\nClick to choose another project folder (starts a new chat).")

    def choose_folder(self) -> None:
        start = self.cwd or str(Path.home())
        path = QFileDialog.getExistingDirectory(self, "Project folder for Claude", start)
        if not path or path == self.cwd:
            return
        if self.messages and QMessageBox.question(
            self, "Project folder",
            f"A chat belongs to one folder. Start a new chat in\n{path}?\n(The current chat stays in Chats.)",
        ) != QMessageBox.StandardButton.Yes:
            return
        self.set_cwd(path)

    def set_cwd(self, path: str) -> None:
        """Use `path` as the project folder; a running session cannot move folders, so this starts a new chat."""
        if not Path(path).is_dir():
            return
        if self.messages:
            self.reset_chat()
        self.cwd = path
        self.settings["cwd"] = path
        config.save_settings(self.settings)
        self.refresh_folder_ui()

    def calibrate_balance(self) -> None:
        """Set the anchor to the balance the user reads in the console (Organization credits)."""
        key, budget = self._balance_key()
        left, _, _ = balance_left(self.usage, key, budget)
        where = "console.anthropic.com > Billing"
        val, ok = QInputDialog.getDouble(
            self, "Set real balance", f"Balance now shown in {where} (USD):", left, 0.0, 1_000_000.0, 2
        )
        if ok:
            self.apply_calibration(float(val))

    def apply_calibration(self, val: float) -> None:
        """The console says `val`: anchor the counter there and forget replies that could not be priced."""
        set_anchor(self.usage, val)
        save_usage(self.usage)
        self.update_balance_lbl()

    # ---- what the user sees: badge, queue rows, message cards -------------------------------------------------------
    def refresh_badge(self) -> None:
        """Red 'Thinking' badge (with a timer) while a reply is running; amber when the queue is paused."""
        n = len(self.queue)
        if self.busy:
            if self.stopping:
                text = "\u25cf Stopping\u2026"
            else:
                secs = int(time.time() - self.t0)
                text = f"\u25cf {'Compacting' if self.compacting else 'Thinking'}\u2026 {secs // 60}:{secs % 60:02d}"
            if n:
                text += f" \u00b7 {n} queued"
            state = "busy"
        elif self.queue and self.queue_paused:
            text, state = f"\u25a0 Paused \u00b7 {n} queued", "paused"
        else:
            text, state = self.ready_text, "ready"
        self.badge.setText(text)
        if self.badge.property("state") != state:
            self.badge.setProperty("state", state)
            self.badge.style().unpolish(self.badge)
            self.badge.style().polish(self.badge)
        # the title shows it in the taskbar when the window is behind others
        self.setWindowTitle(f"\u25cf {self.base_title} \u2014 thinking\u2026" if self.busy else self.base_title)
        pushing = self.can_push()
        resume = bool(self.queue) and self.queue_paused and not self.busy
        self.send_btn.setText(("Push" if pushing else "Queue") if self.busy else ("Resume" if resume else "Send"))
        self.send_btn.setToolTip(
            "Claude reads this at its next step, while it is still working (Enter)" if pushing else
            "Send this message when the running reply is done" if self.busy else "")
        self.queue_btn.setVisible(pushing)
        for b in self.row_push_btns:
            b.setVisible(pushing)
        self.stop_btn.setEnabled(self.busy and not self.stopping)
        self.compact_btn.setEnabled(not self.busy and bool(self.claude_sid))
        if not self.busy:
            hint = "Message Claude\u2026  Enter sends \u00b7 Shift+Enter new line \u00b7 Ctrl+V pastes a screenshot"
        elif pushing:
            hint = "Claude is working \u2014 Enter pushes your message into the reply \u00b7 Ctrl+Enter sends it after the reply"
        else:
            hint = "Claude is working \u2014 Enter adds your message to the queue, it is sent when the reply is done"
        self.input_box.setPlaceholderText(hint)

    def can_push(self) -> bool:
        """A message can be handed to the running reply (not while stopping, not during /compact, not with a worker that cannot)."""
        return self.busy and not self.stopping and not self.compacting and callable(getattr(self.worker, "push", None))

    def refresh_queue_ui(self) -> None:
        while self.queue_lay.count():
            item = self.queue_lay.takeAt(0)
            gone = item.widget() if item else None
            if gone is not None:
                gone.hide()
                gone.setParent(None)              # not painted again while it waits for deleteLater
                gone.deleteLater()
        self.row_push_btns = []

        def row_for(tag_text: str, prompt: str, imgs: list) -> tuple[QFrame, QHBoxLayout]:
            row = QFrame()
            row.setObjectName("queueRow")
            row.setMinimumHeight(S(38))
            h = QHBoxLayout(row)
            h.setContentsMargins(S(10), S(4), S(4), S(4))
            h.setSpacing(S(10))
            tag = QLabel(tag_text)
            tag.setObjectName("queueTag")
            text = (prompt or "(image)").replace("\n", " ")
            text = text if len(text) <= 110 else text[:109] + "\u2026"
            if imgs:
                text += f"  [+{len(imgs)} img]"
            body = QLabel(text)
            body.setToolTip(prompt)
            h.addWidget(tag)
            h.addWidget(body, 1)
            self.queue_lay.addWidget(row)
            return row, h

        for prompt, imgs in self.pushed:
            row, _ = row_for("Pushed", prompt, imgs)
            row.setToolTip("Handed to Claude: it reads this at its next step and the message then appears in the chat")
        for idx, (prompt, imgs) in enumerate(self.queue):
            row, h = row_for(f"Queued {idx + 1}", prompt, imgs)
            item = self.queue[idx]
            push = QPushButton("Push now")
            push.setToolTip("Hand this message to the running reply instead of waiting")
            push.setMinimumHeight(S(28))
            push.clicked.connect(lambda _=False, it=item: self.push_queued(it))
            push.setVisible(self.can_push())
            self.row_push_btns.append(push)
            edit = QPushButton("Edit")
            edit.setToolTip("Change the text of this message")
            edit.setMinimumHeight(S(28))
            edit.clicked.connect(lambda _=False, it=item: self.edit_queued(it))
            x = QPushButton("\u00d7")
            x.setObjectName("xbtn")
            x.setFixedSize(S(28), S(28))
            x.setToolTip("Remove from the queue")
            x.clicked.connect(lambda _=False, i=idx: self.remove_queued(i))
            h.addWidget(push)
            h.addWidget(edit)
            h.addWidget(x)
        self.queue_box.setVisible(bool(self.queue or self.pushed))

    def _queue_index(self, item: tuple) -> int:
        """Where `item` stands now (the queue moves on while a dialog is open); -1 when it has been sent already."""
        for i, q in enumerate(self.queue):
            if q is item:
                return i
        return -1

    def edit_queued(self, item: tuple[str, list[tuple[str, bytes]]]) -> None:
        if self._queue_index(item) < 0:
            return
        prompt, imgs = item
        dlg = EditMessageDialog(self, prompt, len(imgs))
        if not dlg.exec():
            return
        idx = self._queue_index(item)
        if idx < 0:
            QMessageBox.information(self, "Edit message", "That message has already been sent.")
            return
        self.queue[idx] = (dlg.text(), imgs)
        self.refresh_queue_ui()

    def push_queued(self, item: tuple[str, list[tuple[str, bytes]]]) -> None:
        """'Push now' on a queued message: it goes to the running reply instead of waiting for it."""
        idx = self._queue_index(item)
        if idx < 0 or not self.can_push():
            return
        if self.try_push(item):
            del self.queue[idx]
            self.refresh_queue_ui()
            self.refresh_badge()

    def remove_queued(self, idx: int) -> None:
        if 0 <= idx < len(self.queue):
            del self.queue[idx]
        if not self.queue:
            self.queue_paused = False
        self.refresh_queue_ui()
        self.refresh_badge()

    def card_style(self, kind: str) -> tuple[str, str]:
        """(accent bar, card background) of a message kind in the current theme."""
        t = self.theme
        k = kind if kind in ("user", "assistant", "error", "note") else "assistant"
        return t[f"card_{k}"], t[f"card_{k}_bg"]

    def apply_theme(self) -> None:
        """Colour the window, the dialogs, the Markdown and the chat cards from settings["theme"]."""
        self.theme_name = themes.resolve(self.settings.get("theme", themes.DEFAULT), display.system_is_dark())
        t = self.theme = themes.THEMES[self.theme_name]
        qapp = QApplication.instance()
        if qapp is not None:
            apply_palette(qapp, t)
        mdhtml.set_palette(t["md_code_bg"], t["md_border"], t["md_head_bg"], t["md_muted"], t["link"])
        self.setStyleSheet(display.scale_px(themes.stylesheet(t, display.arrow_image(t["arrow"]))))
        if self.showing_hint:
            self.show_hint()
        elif self.messages:
            self.render_messages()
            self.scroll_to_end()

    def message_html(self, who: str, text: str, kind: str, meta: str, ts: float) -> str:
        accent, bg = self.card_style(kind)
        px = lambda n: f"{S(n)}px"  # noqa: E731
        info = time.strftime("%H:%M", time.localtime(ts)) + (f" · {html.escape(meta)}" if meta else "")
        copy = ""
        if kind == "assistant":                                # "Copy" puts the whole reply on the clipboard
            self.code_blocks.append(text)
            copy = f'&nbsp;&nbsp;<a href="aichat-copy:{len(self.code_blocks) - 1}" style="color:{self.theme["link"]};font-size:{px(12)}">Copy</a>'
        head = (f'<span style="color:{accent};font-weight:bold">{html.escape(who)}</span>'
                f'&nbsp;&nbsp;<span style="color:{self.theme["meta"]};font-size:{px(12)}">{info}</span>{copy}')
        body = plain_html(text) if kind == "user" else md_to_html(text, S, self.code_blocks)
        return (
            '<table width="100%" cellspacing="0" cellpadding="0"><tr>'
            f'<td width="{S(4)}" bgcolor="{accent}"></td>'
            f'<td bgcolor="{bg}" style="padding:{px(9)} {px(12)}">{head}'
            f'<p style="margin:0;font-size:{px(5)}">&nbsp;</p>{body}</td></tr></table>'
            f'<p style="margin:0;font-size:{px(10)}">&nbsp;</p>'
        )

    def on_link(self, url: QUrl) -> None:
        """'Copy' links copy a reply or a code block; web links open in the browser; nothing else is opened."""
        if url.scheme() == "aichat-copy":
            try:
                text = self.code_blocks[int(url.path())]
            except (ValueError, IndexError):
                return
            QApplication.clipboard().setText(text)
            QToolTip.showText(self.cursor().pos(), "Copied", self.chat_view)
        elif url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)

    def append_msg(self, who: str, text: str, kind: str, meta: str = "", ts: float | None = None, store: bool = True) -> None:
        if self.showing_hint:
            self.chat_view.clear()
            self.showing_hint = False
        ts = ts or time.time()
        cur = self.chat_view.textCursor()
        cur.movePosition(QTextCursor.MoveOperation.End)
        cur.insertHtml(self.message_html(who, text, kind, meta, ts))
        self.chat_view.setTextCursor(cur)
        self.scroll_to_end()
        QTimer.singleShot(0, self.scroll_to_end)       # once the new layout is known
        if store:
            self.messages.append({"who": who, "text": text, "kind": kind, "meta": meta, "ts": ts})
            self.save_chat()

    def scroll_to_end(self) -> None:
        bar = self.chat_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def show_hint(self) -> None:
        self.chat_view.clear()
        self.code_blocks = []
        extra = ""
        if not self.claude_path():
            extra = (f'<p align="center" style="color:{self.theme["notice_fg"]};font-size:{S(14)}px">'
                     + html.escape(config.CLAUDE_MISSING).replace("\n", "<br>") + "</p>")
        self.chat_view.setHtml(
            f'<p align="center" style="color:{self.theme["hint"]};margin-top:{S(60)}px;font-size:{S(17)}px">Ask Claude anything.</p>'
            f'<p align="center" style="color:{self.theme["hint"]};font-size:{S(14)}px">Enter sends · Shift+Enter adds a line · '
            'Ctrl+V pastes a screenshot<br>While Claude is working, Enter pushes your next message into the reply, Ctrl+Enter queues it.</p>' + extra
        )
        self.showing_hint = True

    def show_hint_if_empty(self) -> None:
        if self.showing_hint or not self.messages:
            self.show_hint()

    # ---- live view of the running reply ---------------------------------------------------------------------------
    def on_progress(self, kind: str, value: str, epoch: int | None = None) -> None:
        """A streamed event of the running reply: tool lines, refused tools, the answer text so far."""
        if not self.busy or (epoch is not None and epoch != self.epoch):
            return
        self.live_box.setVisible(True)
        if kind == "tool":
            self.live_tool.setText("▶ " + value)
        elif kind == "denied":
            self.live_tool.setText(f"⛔ {value} refused ({config.PERMISSION_MODES[self.permission()][0]} mode)")
        elif kind == "text":
            self.live_pending = value
            if not self.live_timer.isActive():
                self.live_timer.start()

    def flush_live_text(self) -> None:
        if self.live_pending is None:
            return
        text, self.live_pending = self.live_pending, None
        self.live_text.setHtml(md_to_html(text[-6000:], S))
        self.live_text.setFixedHeight(max(S(36), min(S(190), int(self.live_text.document().size().height()) + S(10))))
        bar = self.live_text.verticalScrollBar()
        bar.setValue(bar.maximum())

    def clear_live(self) -> None:
        self.live_timer.stop()
        self.live_pending = None
        self.live_tool.setText("")
        self.live_text.clear()
        self.live_box.setVisible(False)

    # ---- chats: the open one survives a restart, the others live in Chats -------------------------------------------
    def save_chat(self) -> None:
        if not self.messages:
            return
        if self.chat_id is None:
            self.chat_id = chats.new_id()
        try:
            chats.save({"id": self.chat_id, "messages": self.messages, "sid": self.claude_sid,
                        "cost_base": self.claude_cost_base, "cwd": self.cwd})
            chats.set_current(self.chat_id)
        except OSError:
            pass

    def restore_chat(self) -> bool:
        """Reopen the chat that was open at the last exit (a version 0.1 last_chat.json is imported once)."""
        cid = chats.get_current()
        if cid is None:
            cid = chats.import_legacy()
        data = chats.load(cid) if cid else None
        return bool(data) and self.load_chat(data)

    def load_chat(self, data: dict) -> bool:
        msgs = data.get("messages") or []
        if not msgs:
            return False
        self.chat_id = data["id"]
        self.claude_sid = data.get("sid") if isinstance(data.get("sid"), str) else None
        try:
            cb = data.get("cost_base", 0.0)
            self.claude_cost_base = None if cb is None else float(cb)   # null = unknown, see ChatWorker._run_claude
        except (TypeError, ValueError):
            self.claude_cost_base = 0.0
        cwd = data.get("cwd") or ""
        if cwd and Path(cwd).is_dir():
            self.cwd = cwd
            self.refresh_folder_ui()
        self.messages = list(msgs)
        self.render_messages()
        return True

    def render_messages(self) -> None:
        """Draw every message of the open chat again (after opening a chat or changing the theme)."""
        self.chat_view.clear()
        self.code_blocks = []
        self.showing_hint = False
        for m in self.messages:
            try:
                ts = float(m.get("ts") or time.time())
            except (TypeError, ValueError):
                ts = time.time()
            self.append_msg(str(m.get("who", "?")), m["text"], m["kind"], str(m.get("meta", "")), ts, store=False)

    def open_chat(self, chat_id: str) -> None:
        """Switch to a saved chat (the one that was open stays saved)."""
        if self.busy:
            return
        data = chats.load(chat_id)
        if not data or not data["messages"]:
            return
        self.epoch += 1
        self.queue.clear()
        self.pushed.clear()
        self.queue_paused = False
        self.clear_images()
        self.clear_live()
        if self.load_chat(data):
            chats.set_current(chat_id)
            self.ready_text = "● Opened chat"
        self.refresh_queue_ui()
        self.refresh_ctx()
        self.refresh_badge()

    def show_chats(self) -> None:
        dlg = ChatsDialog(self, self.chat_id, self.busy)
        dlg.exec()
        if self.chat_id in dlg.deleted:                  # the open chat was deleted: start empty
            self.reset_chat()
        if dlg.open_id and dlg.open_id != self.chat_id:
            self.open_chat(dlg.open_id)

    def export_chat(self) -> None:
        if not self.messages:
            QMessageBox.information(self, "Export", "There is nothing to export yet.")
            return
        title = chats.title_from(self.messages)
        stem = "".join(c if c.isalnum() or c in " -_" else "_" for c in title).strip()[:40] or "chat"
        start = str(Path(self.cwd or Path.home()) / f"{stem}.md")
        path, _ = QFileDialog.getSaveFileName(self, "Export chat as Markdown", start, "Markdown (*.md)")
        if path:
            self.write_export(path)

    def write_export(self, path: str) -> None:
        Path(path).write_text(export_markdown(self.messages, chats.title_from(self.messages), self.cwd), encoding="utf-8")

    # ---- sending, queueing, stopping ---------------------------------------------------------------------------------
    def reset_chat(self) -> None:
        self.epoch += 1               # a reply still on its way belongs to the old chat and is dropped
        self.queue.clear()
        self.pushed.clear()
        self.queue_paused = False
        if self.busy and self.worker is not None:
            self.stopping = True
            self.worker.stop()
        self.claude_sid = None
        self.claude_cost_base = 0.0
        self.messages = []
        self.chat_id = None           # the old chat stays in Chats; the next message starts a new file
        chats.set_current(None)
        self.clear_images()
        self.clear_live()
        self.show_hint()
        self.ready_text = "● New chat"
        self.refresh_queue_ui()
        self.refresh_ctx()
        self.refresh_badge()

    def send_message(self) -> None:
        """Enter / the Send button: starts a reply; while Claude works it pushes the message into that reply."""
        self._submit(push=True)

    def queue_message(self) -> None:
        """Ctrl+Enter / the Queue button: the message waits until the running reply is done."""
        self._submit(push=False)

    def try_push(self, item: tuple[str, list[tuple[str, bytes]]]) -> bool:
        """Hand a message to the running reply. False = Claude can no longer take it (the call just ended)."""
        prompt, imgs = item
        try:
            ok = bool(self.worker.push(prompt or "Describe/analyze this image.", imgs))     # type: ignore[union-attr]
        except Exception:
            ok = False
        if ok:
            self.pushed.append(item)
            self.refresh_queue_ui()
            self.refresh_badge()
        return ok

    def _submit(self, push: bool) -> None:
        prompt = self.input_box.toPlainText().strip()
        imgs = [(m, b) for m, b, _ in self.pending_images]
        if not prompt and not imgs:
            if self.queue and self.queue_paused and not self.busy:      # the button reads "Resume"
                self.queue_paused = False
                self.start_next()
            return
        self.input_box.clear()
        self.clear_images()
        item = (prompt, imgs)
        if self.busy:
            if push and self.can_push() and self.try_push(item):
                return
            self.queue.append(item)                                      # wait behind the running reply
            self.refresh_queue_ui()
            self.refresh_badge()
        elif self.queue:                                                 # a paused queue: new message goes last, all go on
            self.queue.append(item)
            self.queue_paused = False
            self.start_next()
        else:
            self.start_item(item)

    def on_push_delivered(self, epoch: int) -> None:
        """Claude took the oldest pushed message in: it now shows in the chat, at the point where Claude read it."""
        if epoch != self.epoch or not self.pushed:
            return
        prompt, imgs = self.pushed.pop(0)
        img_note = f" [+{len(imgs)} img]" if imgs else ""
        self.append_msg("You", (prompt or "(image)") + img_note, "user", "pushed while working")
        self.refresh_queue_ui()
        self.refresh_badge()

    def start_next(self) -> None:
        if not self.queue:
            self.queue_paused = False
            self.refresh_queue_ui()
            self.refresh_badge()
            return
        item = self.queue.pop(0)
        self.refresh_queue_ui()
        self.start_item(item)

    def start_item(self, item: tuple[str, list[tuple[str, bytes]]]) -> None:
        prompt, imgs = item
        img_note = f" [+{len(imgs)} img]" if imgs else ""
        self.append_msg("You", (prompt or "(image)") + img_note, "user")
        self.busy = True
        self.stopping = False
        self.t0 = time.time()
        self.ready_text = "\u25cf Ready"
        self.busy_timer.start()
        self.clear_live()
        self.launch_worker(prompt or "Describe/analyze this image.", imgs)
        self.refresh_badge()

    def launch_worker(self, prompt: str, imgs: list[tuple[str, bytes]]) -> None:
        _, _, model = self.MODELS[self.model_box.currentIndex()]
        effort = self.effort_box.currentText().lower()
        self.worker = ChatWorker(model, effort, prompt, imgs, self.claude_sid, self.env, self.claude_cost_base,
                                 cwd=self.cwd, permission=self.permission(), claude_bin=self.claude_path())
        self.worker.epoch = self.epoch
        self.worker.progress.connect(lambda kind, value, ep=self.epoch: self.on_progress(kind, value, ep))
        self.worker.finished_reply.connect(self.on_reply)
        self.worker.turn_done.connect(lambda reply, meta, cost, st: self.on_reply(reply, meta, cost, st, final=False))
        self.worker.push_delivered.connect(lambda ep=self.epoch: self.on_push_delivered(ep))
        self.worker.start()

    def stop_generation(self) -> None:
        if self.busy and self.worker is not None and not self.stopping:
            self.stopping = True
            self.queue_paused = True          # a stopped reply must not start the next queued message by itself
            self.worker.stop()
            self.refresh_badge()

    def on_reply(self, reply: str, meta: str, cost: float, state_tuple: object, final: bool = True) -> None:
        """One finished reply. final=False: a pushed message started another turn, so Claude is still working."""
        csid, ctotal, *rest = state_tuple  # type: ignore[misc]
        ok = rest[0] if rest else True
        uncounted = bool(rest[1]) if len(rest) > 1 else False
        silent = bool(rest[2]) if len(rest) > 2 else False        # an earlier turn already showed the reply
        if uncounted:                     # it cost money we cannot price: say so until the next calibration
            self.usage["claude_uncounted"] = int(self.usage.get("claude_uncounted", 0)) + 1
            save_usage(self.usage)
            self.update_balance_lbl()
        stale = getattr(self.worker, "epoch", self.epoch) != self.epoch      # reply of a chat the user already left
        model = getattr(self.worker, "model", "")
        compacting = self.compacting
        if final:
            self.compacting = False
        if not stale:
            self.claude_sid = csid
            self.claude_cost_base = ctotal
        if cost > 0:                      # the money was spent either way
            self.usage["claude_spent"] = float(self.usage.get("claude_spent", 0.0)) + cost
            self.usage["claude_msgs"] = int(self.usage.get("claude_msgs", 0)) + 1
            save_usage(self.usage)
            self.update_balance_lbl()
        if not stale and not silent:
            secs = int(time.time() - self.t0)
            if ok and compacting:
                lc = last_compact(csid)
                text = (f"Chat compacted: {fmt_tokens(lc[0])} \u2192 {fmt_tokens(lc[1])} tokens. Every next reply re-reads less."
                        if lc else (reply or "Chat compacted."))
                self.append_msg("Context", text, "note", " \u00b7 ".join(b for b in (meta, f"{secs}s") if b))
            elif ok:
                bits = [b for b in (model, meta, f"{secs}s") if b]
                self.append_msg("Claude", reply, "assistant", " \u00b7 ".join(bits))
            elif self.stopping:
                self.append_msg("Claude", reply, "assistant", "stopped")
            else:
                self.append_msg("Claude", reply, "error", "failed")
        if not final:
            self.t0 = time.time()                       # the next turn is timed on its own
            self.clear_live()
            self.refresh_ctx()
            self.refresh_badge()
            return
        self.busy = False
        self.busy_timer.stop()
        self.clear_live()
        self.refresh_ctx()
        if self.pushed:                                 # Claude never took these in: they go out next, in order
            if not stale:
                self.queue[:0] = self.pushed
            self.pushed = []
            self.refresh_queue_ui()
        if not ok and not stale and self.queue:
            self.queue_paused = True
        self.stopping = False
        if self.queue and not self.queue_paused:
            self.start_next()
        else:
            if not self.queue:
                self.queue_paused = False
            self.refresh_badge()
        self.input_box.setFocus()

    def refresh_ctx(self) -> None:
        """'context 274k' in the top bar; amber from CTX_WARN, when Compact starts to pay off."""
        n = context_tokens(self.claude_sid)
        warn = n is not None and n >= CTX_WARN
        self.ctx_lbl.setText("" if n is None else f"context {fmt_tokens(n)}")
        self.ctx_lbl.setToolTip(
            "" if n is None else
            f"{n:,} tokens: what every next reply re-reads (from the cache). The longer it gets, the more each reply costs.\n"
            "Compact summarises it; New Chat starts empty."
        )
        for w in (self.ctx_lbl, self.compact_btn):
            level = "warn" if warn else "ok"
            if w.property("level") != level:
                w.setProperty("level", level)
                w.style().unpolish(w)
                w.style().polish(w)

    def compact_chat(self) -> None:
        """Send /compact to the running session (works through claude -p: same session, shorter context)."""
        if self.busy or not self.claude_sid:
            return
        self.compacting = True
        self.busy = True
        self.stopping = False
        self.t0 = time.time()
        self.ready_text = "\u25cf Ready"
        self.busy_timer.start()
        self.launch_worker("/compact", [])
        self.refresh_badge()

    def closeEvent(self, ev) -> None:  # noqa: N802
        w = self.worker
        if self.busy and w is not None and hasattr(w, "isRunning") and w.isRunning():
            w.stop()
            w.wait(3000)
        super().closeEvent(ev)


def apply_palette(app: QApplication, t: dict[str, str]) -> None:
    """The application palette in a theme's colours (native widgets, dialogs, tooltips)."""
    pal = QPalette()
    roles = QPalette.ColorRole
    for role, key in ((roles.Window, "bg"), (roles.WindowText, "fg"), (roles.Base, "panel"), (roles.AlternateBase, "btn"),
                      (roles.Text, "fg"), (roles.PlaceholderText, "placeholder"), (roles.Button, "btn"),
                      (roles.ButtonText, "fg"), (roles.Highlight, "sel"), (roles.HighlightedText, "sel_fg"),
                      (roles.ToolTipBase, "panel"), (roles.ToolTipText, "fg")):
        pal.setColor(role, QColor(t[key]))
    app.setPalette(pal)


HELP = f"""aichat-desktop {__version__}: a desktop chat window for Claude Code.

usage: aichat-desktop [--version] [--help] [--install-desktop | --uninstall-desktop]

--install-desktop    add AI Chat to the application menu (menu entry + icons in ~/.local/share)
--uninstall-desktop  remove them again

Needs the `claude` command (https://claude.com/claude-code). Settings, chats and the optional API key live in
the aichat-desktop folder of your user config (~/.config/aichat-desktop on Linux, %APPDATA%\\aichat-desktop on Windows;
override with AICHAT_DESKTOP_CONFIG_DIR). Hard-pixel text for integer-scaled screens can be turned off with
AICHAT_DESKTOP_HARD_PIXELS=0.
"""


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if "--version" in args:
        print(f"aichat-desktop {__version__}")
        return 0
    if "--help" in args or "-h" in args:
        print(HELP)
        return 0
    if "--install-desktop" in args or "--uninstall-desktop" in args:
        from . import desktop
        files = desktop.install() if "--install-desktop" in args else desktop.uninstall()
        print("\n".join(("wrote " if "--install-desktop" in args else "removed ") + str(f) for f in files) or "nothing to do")
        return 0
    config.migrate_legacy_dir()           # version 0.1 / 0.2 kept its files in ~/.config/aichat
    saved_env = {k: os.environ.get(k) for k in display.SCALE_ENV_KEYS}
    detect = display.detect_ui_scale()
    display.set_scale(detect)
    if display.HARD_PIXELS:
        # The session asks for an integer scale (Xfce at 200 %): draw hinted, non-antialiased glyphs on whole device pixels.
        # FreeType's classic TrueType interpreter follows the font's own hints (even stems and spacing).
        os.environ["FREETYPE_PROPERTIES"] = "truetype:interpreter-version=35"
        os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
        os.environ.pop("QT_SCREEN_SCALE_FACTORS", None)
        os.environ.pop("QT_SCALE_FACTOR", None)
        os.environ["QT_FONT_DPI"] = str(96 * detect)
    app = QApplication([sys.argv[0]])
    app.setApplicationName("aichat-desktop")
    app.setDesktopFileName("aichat-desktop")
    QFontMetrics(QFont(display.pick_family() or app.font().family())).height()   # FreeType starts now, with the properties above
    # Programs the chat starts (claude, its bash tools) get the normal environment back.
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    app.setStyle("Fusion")
    font = display.crisp_font(S(display.FONT_BASE_PX))
    app.setFont(font)
    # Some Qt platform themes (qt6ct) set their own font once the event loop starts: set ours again then.
    QTimer.singleShot(0, lambda: QApplication.setFont(display.crisp_font(S(display.FONT_BASE_PX))))
    app.setWindowIcon(display.app_icon())
    cleanup_images()                      # pasted screenshots older than a week
    win = AIChatWindow()
    win.chat_view.setFont(font)
    win.chat_view.document().setDefaultFont(font)
    win.input_box.setFont(font)
    win.show()
    QTimer.singleShot(0, win.maybe_first_run)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
