"""One `claude -p` call in a background thread, streaming its progress to the window."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from . import config, display, streaming
from .costs import SESSION_HINT_USD, claude_delta


IMAGE_KEEP_DAYS = 7


def image_dir() -> Path:
    """Where pasted / attached images are handed to claude (private folder; old files are removed at start-up)."""
    d = display.cache_dir() / "images"
    d.mkdir(parents=True, exist_ok=True)
    try:
        d.chmod(0o700)
    except OSError:
        pass
    return d


def cleanup_images(max_age_days: float = IMAGE_KEEP_DAYS) -> int:
    """Delete images older than `max_age_days` from image_dir(); returns how many went."""
    gone = 0
    cutoff = time.time() - max_age_days * 86400
    for f in image_dir().glob("aichat_*"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                gone += 1
        except OSError:
            pass
    return gone


class ChatWorker(QThread):
    """Background worker for one Claude CLI call."""

    finished_reply = pyqtSignal(str, str, float, object)   # reply, meta, cost, (session_id, session_total, ok, uncounted)
    progress = pyqtSignal(str, str)                         # ('text', all text so far) | ('tool', line) | ('denied', tool)

    def __init__(
        self,
        model: str,
        effort: str,
        prompt: str,
        images: list[tuple[str, bytes]],
        claude_sid: str | None,
        env: dict[str, str],
        cost_base: float | None = 0.0,
        cwd: str = "",
        permission: str = config.DEFAULT_PERMISSION,
        claude_bin: str | None = None,
    ) -> None:
        super().__init__()
        self.model = model
        self.effort = effort
        self.prompt = prompt
        self.images = images
        self.claude_sid = claude_sid
        self.env = env
        self.cwd = cwd
        self.permission = permission
        self.claude_bin = claude_bin
        self.cost_base = cost_base      # session running total after the previous reply
        self.cost_total = cost_base     # ... after this one
        self.proc: subprocess.Popen[str] | None = None
        self._cancelled = False
        self.ok = True                  # False after a stop or an error
        self.uncounted = False          # True when the reply cost money but could not be priced (unknown session total)
        self.epoch = 0                  # set by the window: which chat the reply belongs to
        self.tools_used = 0

    def stop(self) -> None:
        self._cancelled = True
        p = self.proc
        if p and p.poll() is None:
            p.terminate()

    def run(self) -> None:
        try:
            reply, meta, cost, new_sid = self._run_claude()
            self.finished_reply.emit(reply, meta, cost, (new_sid, self.cost_total, self.ok, self.uncounted))
        except Exception as exc:
            self.ok = False
            self.finished_reply.emit(f"Error: {exc}", "", 0.0, (self.claude_sid, self.cost_total, self.ok, False))

    def build_command(self, full_prompt: str) -> list[str]:
        binary = self.claude_bin or config.find_claude()
        if not binary:
            raise RuntimeError(config.CLAUDE_MISSING)
        cmd = [binary, "-p", full_prompt, "--output-format", "stream-json", "--verbose", "--include-partial-messages",
               "--model", self.model, "--effort", self.effort.lower()]
        cmd += config.PERMISSION_MODES.get(self.permission, config.PERMISSION_MODES[config.DEFAULT_PERMISSION])[2]
        if self.claude_sid:
            cmd += ["--resume", self.claude_sid]
        return cmd

    def _run_claude(self) -> tuple[str, str, float, str | None]:
        full_prompt = self.prompt
        if self.images:
            paths = []
            for mime, raw_bytes in self.images:
                ext = ".png" if "png" in mime else ".jpg"
                tf = tempfile.NamedTemporaryFile(prefix="aichat_", suffix=ext, dir=image_dir(), delete=False)
                tf.write(raw_bytes)
                tf.close()
                paths.append(tf.name)
            full_prompt += "\n\nAttached image(s) (inspect with Read tool): " + ", ".join(paths)

        cmd = self.build_command(full_prompt)
        clean_env = {k: v for k, v in self.env.items() if not k.startswith("CLAUDECODE")}
        cwd = self.cwd if self.cwd and Path(self.cwd).is_dir() else str(Path.home())
        err_file = tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace")
        extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}   # no console flash on Windows
        self.proc = subprocess.Popen(
            cmd, cwd=cwd, env=clean_env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=err_file, text=True, encoding="utf-8", errors="replace", bufsize=1, **extra,
        )
        state = streaming.StreamState()
        raw_lines: list[str] = []
        proc = self.proc
        for line in proc.stdout:                       # events arrive as the reply is produced
            raw_lines.append(line)
            obj = streaming.parse_line(line)
            if obj is None:
                continue
            for kind, value in streaming.feed(state, obj):
                if kind == "tool":
                    self.tools_used += 1
                self.progress.emit(kind, value)
        proc.stdout.close()
        proc.wait()
        rc = proc.returncode
        self.proc = None
        err_file.seek(0)
        stderr = err_file.read()
        err_file.close()
        if self._cancelled:
            self.ok = False
            return "(stopped)", "", 0.0, self.claude_sid
        data = state.result
        if data is None:
            self.ok = False
            tail = "".join(raw_lines).strip()
            return "Error: " + (stderr.strip() or tail or f"claude exited with code {rc}"), "", 0.0, state.session_id or self.claude_sid
        reply = str(data.get("result") or data.get("error") or "")
        if data.get("is_error"):
            self.ok = False
            reply = reply or "Error: claude reported an error"
        cost = data.get("total_cost_usd")
        c_val = 0.0
        if isinstance(cost, (int, float)):
            # cost_base None = running total unknown (restored from a transcript): book nothing this once
            self.uncounted = self.cost_base is None and float(cost) > 0
            c_val = claude_delta(float(cost), self.cost_base) if self.cost_base is not None else 0.0
            self.cost_total = float(cost)
        meta = ""
        if isinstance(cost, (int, float)):
            meta = (f"${c_val:.4f} · " if self.cost_base is not None else "") + f"chat ${self.cost_total:.2f}"
            if self.uncounted:
                meta += " · cost of this reply NOT counted (restored chat)"
            if self.cost_total >= SESSION_HINT_USD:
                meta += " · long chat: Compact or New Chat is cheaper"
        if self.tools_used:
            meta = f"{self.tools_used} tool call{'s' if self.tools_used != 1 else ''}" + (f" · {meta}" if meta else "")
        note = streaming.denial_note(data.get("permission_denials") or [], config.PERMISSION_MODES.get(self.permission, ("?",))[0])
        if note:
            reply = (reply + "\n\n" if reply else "") + "> " + note
        return reply, meta, c_val, data.get("session_id") or state.session_id or self.claude_sid
