"""One `claude -p` call in a background thread, streaming its progress to the window."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

from . import config, display, streaming
from .costs import SESSION_HINT_USD, claude_delta


IMAGE_KEEP_DAYS = 7
GIVE_UP_SECONDS = 12.0     # after a reply, how long to wait for a pushed message that claude has not taken in yet


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
    turn_done = pyqtSignal(str, str, float, object)         # a reply that is NOT the last one: a pushed message started another turn
    push_delivered = pyqtSignal()                           # Claude has taken the oldest pushed message into the conversation

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
        # pushing: more messages can be written to the running claude (stdin stays open until the last turn is done)
        self._lock = threading.Lock()
        self._stdin_open = False
        self._stdin_file = None         # kept apart from self.proc, which is cleared when the process is gone
        self._expect: list[str] = []    # texts written to claude and not yet echoed back (--replay-user-messages), in order
        self._pushed_waiting = 0        # pushed messages written but not yet taken in by claude
        self._give_up: threading.Timer | None = None
        self.undelivered = 0            # pushed messages claude never took in (the process ended first)
        self.silent_final = False       # the last event only ends the call: every reply was already shown by turn_done

    def stop(self) -> None:
        self._cancelled = True
        self._close_stdin()
        p = self.proc
        if p and p.poll() is None:
            p.terminate()

    # ---- pushing a message into the reply that is running -----------------------------------------------------------
    @staticmethod
    def with_images(prompt: str, images: list[tuple[str, bytes]]) -> str:
        """The prompt plus the paths of its images (saved in the private cache folder; Claude reads them with the Read tool)."""
        if not images:
            return prompt
        paths = []
        for mime, raw_bytes in images:
            ext = ".png" if "png" in mime else ".jpg"
            tf = tempfile.NamedTemporaryFile(prefix="aichat_", suffix=ext, dir=image_dir(), delete=False)
            tf.write(raw_bytes)
            tf.close()
            paths.append(tf.name)
        return prompt + "\n\nAttached image(s) (inspect with Read tool): " + ", ".join(paths)

    def _write_user(self, text: str) -> bool:
        """One user message as a stream-json line. False when claude no longer listens."""
        stdin = self._stdin_file
        if stdin is None or not self._stdin_open:
            return False
        line = json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}})
        try:
            stdin.write(line + "\n")
            stdin.flush()
        except (OSError, ValueError):
            self._stdin_open = False
            return False
        self._expect.append(text.strip())
        return True

    def push(self, prompt: str, images: list[tuple[str, bytes]] | None = None) -> bool:
        """Hand one more message to the running reply. Claude reads it at its next step (after the tool that is running now);
        while it only writes text it becomes the next turn. False = too late or not possible: the caller queues it instead."""
        if self._cancelled or self.proc is None:
            return False
        full = self.with_images(prompt, images or [])
        with self._lock:
            if not self._write_user(full):
                return False
            self._pushed_waiting += 1
        return True

    def _close_stdin(self) -> None:
        with self._lock:
            if self._give_up is not None:
                self._give_up.cancel()
                self._give_up = None
            if not self._stdin_open:
                return
            self._stdin_open = False
            self._close_stdin_file()

    def _close_stdin_file(self) -> None:
        try:
            if self._stdin_file is not None:
                self._stdin_file.close()
        except (OSError, ValueError):
            pass

    def _on_user_event(self, obj: dict) -> None:
        """A user message echoed back by claude: the oldest written message is now part of the conversation."""
        if obj.get("parent_tool_use_id"):
            return
        content = (obj.get("message") or {}).get("content")
        texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"] if isinstance(content, list) else []
        if not texts:
            return
        with self._lock:
            if not self._expect or texts[0].strip() != self._expect[0]:
                return
            self._expect.pop(0)
            is_push = self._pushed_waiting > 0 and len(self._expect) < self._pushed_waiting   # the first echo is the opening prompt
            if is_push:
                self._pushed_waiting -= 1
            if self._give_up is not None:         # claude started the turn we were waiting for
                self._give_up.cancel()
                self._give_up = None
        if is_push:
            self.push_delivered.emit()

    def _give_up_waiting(self) -> None:
        """A pushed message that claude never answered after the last result: stop waiting, the window re-queues it."""
        with self._lock:
            self._give_up = None
        self._close_stdin()

    def run(self) -> None:
        try:
            reply, meta, cost, new_sid = self._run_claude()
            self.finished_reply.emit(reply, meta, cost, (new_sid, self.cost_total, self.ok, self.uncounted, self.silent_final))
        except Exception as exc:
            self.ok = False
            self._close_stdin()
            self.finished_reply.emit(f"Error: {exc}", "", 0.0, (self.claude_sid, self.cost_total, self.ok, False, False))

    def build_command(self) -> list[str]:
        """`claude -p` fed through stdin (stream-json): the first message goes in at start, pushed ones while it works."""
        binary = self.claude_bin or config.find_claude()
        if not binary:
            raise RuntimeError(config.CLAUDE_MISSING)
        cmd = [binary, "-p", "--input-format", "stream-json", "--replay-user-messages",
               "--output-format", "stream-json", "--verbose", "--include-partial-messages",
               "--model", self.model, "--effort", self.effort.lower()]
        cmd += config.PERMISSION_MODES.get(self.permission, config.PERMISSION_MODES[config.DEFAULT_PERMISSION])[2]
        if self.claude_sid:
            cmd += ["--resume", self.claude_sid]
        return cmd

    def _finish_turn(self, state: streaming.StreamState) -> tuple[str, str, float, str | None]:
        """The result event of one turn -> (reply, meta, cost of this turn, session id). Books the turn in cost_total."""
        data = state.result or {}
        local_ok = True
        reply = str(data.get("result") or data.get("error") or "")
        if data.get("is_error"):
            local_ok = False
            reply = reply or "Error: claude reported an error"
        self.ok = local_ok
        cost = data.get("total_cost_usd")
        c_val = 0.0
        if isinstance(cost, (int, float)):
            # cost_base None = running total unknown (restored from a transcript): book nothing this once
            self.uncounted = self.uncounted or (self.cost_base is None and float(cost) > 0)
            c_val = claude_delta(float(cost), self.cost_base) if self.cost_base is not None else 0.0
            priced = self.cost_base is not None
            self.cost_total = float(cost)
            self.cost_base = self.cost_total                  # the next turn of this process continues from here
        meta = ""
        if isinstance(cost, (int, float)):
            meta = (f"${c_val:.4f} · " if priced else "") + f"chat ${self.cost_total:.2f}"
            if self.uncounted and not priced:
                meta += " · cost of this reply NOT counted (restored chat)"
            if self.cost_total >= SESSION_HINT_USD:
                meta += " · long chat: Compact or New Chat is cheaper"
        if self.tools_used:
            meta = f"{self.tools_used} tool call{'s' if self.tools_used != 1 else ''}" + (f" · {meta}" if meta else "")
        self.tools_used = 0
        note = streaming.denial_note(data.get("permission_denials") or [], config.PERMISSION_MODES.get(self.permission, ("?",))[0])
        if note:
            reply = (reply + "\n\n" if reply else "") + "> " + note
        return reply, meta, c_val, data.get("session_id") or state.session_id or self.claude_sid

    def _after_result(self) -> bool:
        """True = a pushed message is still on its way, so another turn follows (stdin stays open, a timer guards against
        waiting forever). False = this was the last turn: stdin is closed here, in the same step, so a late push is refused."""
        with self._lock:
            if self._pushed_waiting > 0 and self._stdin_open and not self._cancelled:
                if self._give_up is None:
                    self._give_up = threading.Timer(GIVE_UP_SECONDS, self._give_up_waiting)
                    self._give_up.daemon = True
                    self._give_up.start()
                return True
            if self._stdin_open:
                self._stdin_open = False
                self._close_stdin_file()
            return False

    def _run_claude(self) -> tuple[str, str, float, str | None]:
        full_prompt = self.with_images(self.prompt, self.images)
        cmd = self.build_command()
        clean_env = {k: v for k, v in self.env.items() if not k.startswith("CLAUDECODE")}
        cwd = self.cwd if self.cwd and Path(self.cwd).is_dir() else str(Path.home())
        err_file = tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace")
        extra = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}   # no console flash on Windows
        try:
            self.proc = subprocess.Popen(
                cmd, cwd=cwd, env=clean_env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=err_file, text=True, encoding="utf-8", errors="replace", bufsize=1, **extra,
            )
        except Exception:
            err_file.close()
            raise
        with self._lock:
            self._stdin_file = getattr(self.proc, "stdin", None)
            self._stdin_open = self._stdin_file is not None
            self._write_user(full_prompt)
        state = streaming.StreamState()
        raw_lines: list[str] = []
        proc = self.proc
        last: tuple[str, str, float, str | None] | None = None      # the final turn
        shown = 0                                                   # earlier turns already handed to the window
        for line in proc.stdout:                       # events arrive as the reply is produced
            raw_lines.append(line)
            obj = streaming.parse_line(line)
            if obj is None:
                continue
            if obj.get("type") == "user":
                self._on_user_event(obj)
                continue
            for kind, value in streaming.feed(state, obj):
                if kind == "tool":
                    self.tools_used += 1
                self.progress.emit(kind, value)
            if state.result is not None and last is None:
                turn = self._finish_turn(state)
                if self._after_result():               # a pushed message starts another turn: show this reply now
                    self.turn_done.emit(turn[0], turn[1], turn[2],
                                        (turn[3], self.cost_total, self.ok, self.uncounted, False))
                    shown += 1
                    state = streaming.StreamState(session_id=state.session_id)
                else:
                    last = turn
        proc.stdout.close()
        proc.wait()
        rc = proc.returncode
        self.proc = None
        self._close_stdin()
        self._close_stdin_file()
        with self._lock:
            self.undelivered = self._pushed_waiting
        err_file.seek(0)
        stderr = err_file.read()
        err_file.close()
        if self._cancelled:
            self.ok = False
            return "(stopped)", "", 0.0, self.claude_sid
        if last is not None:
            return last
        if shown:                                      # ended after earlier turns were shown (the wait for a push timed out)
            self.silent_final = True
            return "", "", 0.0, state.session_id or self.claude_sid
        self.ok = False
        tail = "".join(raw_lines).strip()
        return "Error: " + (stderr.strip() or tail or f"claude exited with code {rc}"), "", 0.0, state.session_id or self.claude_sid
