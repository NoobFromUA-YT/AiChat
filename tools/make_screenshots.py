#!/usr/bin/env python3
"""Render docs/screenshot-dark.png and docs/screenshot-light.png from a scripted demo chat (offscreen, temp config,
neutral project folder). Run: python3 tools/make_screenshots.py"""
import os
import sys
import tempfile
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
for k in ("DISPLAY", "QT_SCREEN_SCALE_FACTORS", "SESSION_MANAGER"):
    os.environ.pop(k, None)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from aichat_desktop import app, config, costs, display  # noqa: E402
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


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="aichat-desktop-shots-"))
    config.CONFIG_DIR = tmp / "config"
    costs.CLAUDE_PROJECTS = tmp / "projects"
    project = tmp / "my-project"
    project.mkdir()
    config.save_settings({**config.DEFAULT_SETTINGS, "first_run_done": True, "budget": 50.0, "cwd": str(project)})
    qapp = QApplication([])
    qapp.setStyle("Fusion")
    qapp.setFont(display.crisp_font(15))
    w = app.AIChatWindow()
    w.resize(920, 700)
    w.launch_worker = lambda prompt, imgs: None
    w.input_box.setPlainText("The streaming parser drops events with leading spaces. Fix it and run the tests.")
    w.send_message()
    w.on_reply(REPLY, "sonnet · 4 tool calls · $0.0412 · chat $0.04 · 21s", 0.0, ("sid", 0.0, True, False))
    w.input_box.setPlainText("Now add a test for blank lines too.")
    w.send_message()
    w.on_progress("tool", "Editing test_streaming.py")
    w.on_progress("text", "Adding a case for blank and whitespace-only lines next to the existing\nparser tests…")
    w.show()
    (ROOT / "docs").mkdir(exist_ok=True)
    for name in ("dark", "light"):
        w.apply_settings({"theme": name})
        w.live_text.setHtml(w.live_text.toHtml())
        w.flush_live_text()
        qapp.processEvents()
        w.grab().save(str(ROOT / "docs" / f"screenshot-{name}.png"))
    print("wrote docs/screenshot-dark.png and docs/screenshot-light.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
