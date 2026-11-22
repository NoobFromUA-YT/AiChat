#!/usr/bin/env python3
"""Render the screenshots in docs/ from scripted demo data (offscreen, temp config, neutral project folder, 2x pixels).
   docs/screenshot-dark.png / -light.png      the main window, a reply streaming in
   docs/screenshot-reply-dark.png             a finished reply (code block with Copy, table)
   docs/screenshot-queue-dark.png             a message pushed into the running reply, two queued ones
   docs/screenshot-settings.png               Settings
   docs/screenshot-first-run.png              the first-run dialog
   docs/screenshot-chats.png                  saved chats
Run: python3 tools/make_screenshots.py"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
for k in ("DISPLAY", "QT_SCREEN_SCALE_FACTORS", "SESSION_MANAGER"):
    os.environ.pop(k, None)
for _k in ("LC_ALL", "LC_TIME", "LC_NUMERIC", "LANGUAGE"):
    os.environ[_k] = "C.UTF-8" if _k != "LANGUAGE" else "en"      # English dates and decimal points, whatever the machine uses
os.environ["AICHAT_DESKTOP_HARD_PIXELS"] = "0"      # normal (antialiased) text, sharpness comes from the 2x scale below
os.environ["QT_SCALE_FACTOR"] = "2"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from aichat_desktop import app, chats, config, costs, dialogs, display  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

REPLY = """Both tests pass now. The bug was in `parse_line`: it returned `None` for events with extra whitespace.

```python
def parse_line(line: str) -> dict | None:
    line = line.strip()          # the fix
    return json.loads(line) if line.startswith("{") else None
```

| file | change |
|---|---|
| `streaming.py` | strip the line first |
| `tests/test_streaming.py` | new regression test |
"""
META = "4 tool calls \u00b7 $0.0412 \u00b7 chat $0.04"


def seed_chats() -> None:
    """A few older chats for the Chats dialog."""
    now = time.time()
    demo = [("Why does the export button stay disabled?", 3 * 3600, "my-project"),
            ("Write a migration for the new settings file", 26 * 3600, "my-project"),
            ("Explain this stack trace from CI", 3 * 86400, "api-server"),
            ("Refactor the worker thread to stream events", 5 * 86400, "my-project")]
    for title, age, folder in demo:
        cid = chats.new_id()
        chats.save({"id": cid, "cwd": f"/home/dev/{folder}", "sid": None, "cost_base": 0.0, "messages": [
            {"who": "You", "text": title, "kind": "user", "meta": "", "ts": now - age},
            {"who": "Claude", "text": "Done.", "kind": "assistant", "meta": "", "ts": now - age + 30}]})
        p = config.chats_dir() / f"{cid}.json"
        os.utime(p, (now - age, now - age))
        import json
        data = json.loads(p.read_text(encoding="utf-8"))
        data["updated"] = now - age
        p.write_text(json.dumps(data), encoding="utf-8")


def shot(widget, name: str, qapp) -> None:
    qapp.processEvents()
    widget.grab().save(str(ROOT / "docs" / f"screenshot-{name}.png"))
    print("wrote", f"docs/screenshot-{name}.png")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="aichat-desktop-shots-"))
    config.CONFIG_DIR = tmp / "config"
    costs.CLAUDE_PROJECTS = tmp / "projects"
    project = tmp / "my-project"
    project.mkdir()
    shown_folder = "/home/dev/my-project"      # what the Chats list should show for the main demo chat
    base = {**config.DEFAULT_SETTINGS, "first_run_done": True, "budget": 50.0, "cwd": str(project), "theme": "dark"}
    config.save_settings(base)
    qapp = QApplication([])
    qapp.setStyle("Fusion")
    qapp.setFont(display.crisp_font(15))
    (ROOT / "docs").mkdir(exist_ok=True)

    w = app.AIChatWindow()
    w.resize(920, 700)
    w.launch_worker = lambda prompt, imgs: None
    w.input_box.setPlainText("The streaming parser drops events with leading spaces. Fix it and run the tests.")
    w.send_message()
    w.t0 = time.time() - 21                 # the reply line shows "21s"
    w.on_reply(REPLY, META, 0.0, ("sid", 0.0, True, False))
    w.apply_calibration(50.0)               # starting credit $50 ...
    w.usage["claude_spent"] = 4.2           # ... and $4.20 spent since: "92% left"
    w.update_balance_lbl()
    w.show()

    # finished reply (dark), then the streaming scene
    shot(w, "reply-dark", qapp)
    w.input_box.setPlainText("Now add a test for blank lines too.")
    w.send_message()
    class Pusher:                           # stands in for the running call: it takes pushed messages
        epoch = w.epoch
        model = "sonnet"
        def push(self, text, images=None): return True
        def stop(self): pass
    w.worker = Pusher()
    w.refresh_badge()
    w.on_progress("tool", "Editing test_streaming.py")
    w.on_progress("text", "Adding a case for blank and whitespace-only lines next to the existing\nparser tests…")
    for name in ("dark", "light"):
        w.apply_settings({"theme": name})
        w.flush_live_text()
        shot(w, name, qapp)

    # a message pushed into the running reply, and two queued ones (the stand-in worker accepts the push)
    w.apply_settings({"theme": "dark"})
    w.input_box.setPlainText("Also keep the old behaviour for events without a type field.")
    w.send_message()
    for text in ("When that is done, update the CHANGELOG.", "Then bump the version to 0.4.0 and tag it."):
        w.input_box.setPlainText(text)
        w.queue_message()
    w.refresh_badge()
    w.chat_view.verticalScrollBar().setValue(0)
    shot(w, "queue-dark", qapp)

    # dialogs (dark)
    w.apply_settings({"theme": "dark"})
    config.find_claude = lambda configured="": "/usr/local/bin/claude"      # never show this machine's real path
    s = dialogs.SettingsDialog(w, config.load_settings())
    s.show()
    shot(s, "settings", qapp)
    s.close()
    first = dialogs.FirstRunDialog(w, "/usr/local/bin/claude")
    first.budget.setValue(50.0)
    first.show()
    shot(first, "first-run", qapp)
    first.close()
    seed_chats()
    main = chats.load(w.chat_id)
    main["cwd"] = shown_folder
    chats.save(main)
    c = dialogs.ChatsDialog(w, None)
    c.show()
    shot(c, "chats", qapp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
