"""Portability, onboarding and developer-UX features: config, settings, streaming parser, chat history, export,
copy links, project folder, permission modes, first run, live panel. Offscreen, temp config dir, no real claude."""
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
for k in ("DISPLAY", "QT_SCREEN_SCALE_FACTORS", "SESSION_MANAGER"):
    os.environ.pop(k, None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aichat_desktop import app, chats, config, costs, dialogs, display, mdhtml, streaming, themes  # noqa: E402
from aichat_desktop.export import export_markdown  # noqa: E402
from aichat_desktop.mdhtml import md_to_html  # noqa: E402
from PyQt6.QtCore import QUrl  # noqa: E402
from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

APP = QApplication.instance() or QApplication([])
YES, NO = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No


def fresh_config(**settings):
    d = Path(tempfile.mkdtemp(prefix="aichat-feat-"))
    config.CONFIG_DIR = d
    config.save_settings({**config.DEFAULT_SETTINGS, "first_run_done": True, **settings})
    costs.CLAUDE_PROJECTS = d / "projects"
    return d


class Config(unittest.TestCase):
    def setUp(self):
        self.d = fresh_config()

    def test_defaults_are_safe(self):
        self.assertEqual(config.DEFAULT_SETTINGS["permission"], "readonly")
        os.remove(config.settings_path())
        s = config.load_settings()
        self.assertEqual((s["permission"], s["budget"], s["first_run_done"]), ("readonly", 0.0, False))

    def test_bad_settings_file_falls_back(self):
        config.settings_path().write_text(json.dumps({"permission": "root!", "budget": "lots", "unknown": 1}))
        s = config.load_settings()
        self.assertEqual((s["permission"], s["budget"]), ("readonly", 0.0)); self.assertNotIn("unknown", s)
        config.settings_path().write_text("{broken"); self.assertEqual(config.load_settings()["permission"], "readonly")

    @unittest.skipIf(sys.platform == "win32", "mode bits mean nothing on Windows")
    def test_settings_file_is_private(self):
        config.save_settings({"budget": 5})
        self.assertEqual(config.settings_path().stat().st_mode & 0o777, 0o600)

    @unittest.skipIf(sys.platform == "win32", "Windows uses %APPDATA% (see test_windows_uses_appdata_and_claude_exe)")
    def test_config_dir_follows_env(self):
        config.CONFIG_DIR = None
        old = {k: os.environ.get(k) for k in ("AICHAT_DESKTOP_CONFIG_DIR", "XDG_CONFIG_HOME")}
        try:
            os.environ.pop("AICHAT_DESKTOP_CONFIG_DIR", None); os.environ["XDG_CONFIG_HOME"] = "/tmp/xdg-x"
            self.assertEqual(config.config_dir(), Path("/tmp/xdg-x/aichat-desktop"))
            os.environ["AICHAT_DESKTOP_CONFIG_DIR"] = "/tmp/own-dir"
            self.assertEqual(config.config_dir(), Path("/tmp/own-dir"))
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
            config.CONFIG_DIR = self.d

    @unittest.skipIf(sys.platform == "win32", "uses a #! stand-in and the execute bit")
    def test_find_claude(self):
        bindir = Path(tempfile.mkdtemp(prefix="aichat-bin-"))
        fake = bindir / "claude"
        fake.write_text("#!/bin/sh\n"); fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        old = os.environ["PATH"]
        try:
            os.environ["PATH"] = str(bindir)
            self.assertEqual(config.find_claude(), str(fake))
            self.assertEqual(config.find_claude(str(fake)), str(fake))              # configured path wins
            self.assertEqual(config.find_claude("/nonexistent/claude"), str(fake))   # bad configured path: look further
            os.environ["PATH"] = "/nonexistent"
            home = os.environ.get("HOME")
            os.environ["HOME"] = str(bindir)                                         # no ~/.local/bin/claude there
            self.assertIsNone(config.find_claude() if not Path("/usr/local/bin/claude").exists() else None)
            if home is not None:
                os.environ["HOME"] = home
        finally:
            os.environ["PATH"] = old

    def test_windows_uses_appdata_and_claude_exe(self):
        from unittest import mock
        config.CONFIG_DIR = None
        with mock.patch.object(config.sys, "platform", "win32"), \
                mock.patch.dict(os.environ, {"APPDATA": "/tmp/fake-appdata", "AICHAT_DESKTOP_CONFIG_DIR": ""}, clear=False):
            os.environ.pop("AICHAT_DESKTOP_CONFIG_DIR")
            self.assertEqual(config.config_dir(), Path("/tmp/fake-appdata") / "aichat-desktop")
            with mock.patch.object(config.shutil, "which", lambda name: "/c/claude.exe" if name == "claude.exe" else "/c/claude.cmd"):
                self.assertEqual(config.find_claude(), "/c/claude.exe")           # the .exe wins over the .cmd shim
        config.CONFIG_DIR = self.d

    def test_env_file_parsing(self):
        got = config.parse_env_text("# c\nexport A=1\nB = 'two words'\nC=\"3\"\nbad line\nD=x=y\n")
        self.assertEqual(got, {"A": "1", "B": "two words", "C": "3", "D": "x=y"})

    def test_every_mode_has_label_tip_args(self):
        for key, (label, tip, args) in config.PERMISSION_MODES.items():
            self.assertTrue(label and tip and args, key)
        self.assertNotIn("--dangerously-skip-permissions", config.PERMISSION_MODES["readonly"][2])
        self.assertNotIn("Bash", ",".join(config.PERMISSION_MODES["edit"][2]))


class WindowsBehaviour(unittest.TestCase):
    """What the code does when sys.platform says win32 (checked with stand-ins: there is no Windows machine in the test run)."""

    def test_claude_is_started_without_a_console_window_and_with_utf8_pipes(self):
        from unittest import mock
        from aichat_desktop import worker
        seen = {}

        class FakePopen:
            returncode = 0
            def __init__(self, cmd, **kw): seen.update(kw)
            def wait(self): pass
            def poll(self): return 0

        FakePopen.stdout = type("S", (), {"__iter__": lambda s: iter(['{"type": "result", "result": "ok", "total_cost_usd": 0}\n']),
                                         "close": lambda s: None})()
        with mock.patch.object(worker.sys, "platform", "win32"), \
                mock.patch.object(worker.subprocess, "CREATE_NO_WINDOW", 0x08000000, create=True), \
                mock.patch.object(worker.subprocess, "Popen", FakePopen):
            wk = worker.ChatWorker("sonnet", "high", "hi", [], None, {}, 0.0, claude_bin="C:/x/claude.exe")
            self.assertEqual(wk._run_claude()[0], "ok")
        self.assertEqual(seen["creationflags"], 0x08000000)
        self.assertEqual((seen["encoding"], seen["errors"]), ("utf-8", "replace"))

    def test_cache_folder_is_under_localappdata(self):
        from unittest import mock
        with mock.patch.object(display.sys, "platform", "win32"), mock.patch.dict(os.environ, {"LOCALAPPDATA": "/tmp/fake-local"}):
            self.assertEqual(display.cache_dir(), Path("/tmp/fake-local") / "aichat-desktop" / "cache")

    def test_install_desktop_refuses_politely(self):
        from unittest import mock
        from aichat_desktop import desktop
        with mock.patch.object(desktop.sys, "platform", "win32"):
            with self.assertRaises(SystemExit) as cm:
                desktop.install()
        self.assertIn("Linux", str(cm.exception))


class Streaming(unittest.TestCase):
    def feed(self, events):
        st = streaming.StreamState(); out = []
        for e in events:
            out += streaming.feed(st, e)
        return st, out

    def test_tool_lines(self):
        for name, inp, want in (
            ("Read", {"file_path": "/a/b/c.py"}, "Reading c.py"),
            ("Edit", {"file_path": "/a/b/c.py"}, "Editing c.py"),
            ("Bash", {"command": "ls   -la\n/tmp"}, "Running: ls -la /tmp"),
            ("Grep", {"pattern": "foo"}, "Searching for: foo"),
            ("WebFetch", {"url": "https://x.y"}, "Fetching https://x.y"),
            ("Mystery", {}, "Using Mystery"),
        ):
            self.assertEqual(streaming.describe_tool(name, inp), want)
        self.assertTrue(streaming.describe_tool("Bash", {"command": "x" * 500}).endswith("…"))

    def test_text_accumulates_and_new_message_resets(self):
        ev = lambda t: {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": t}}}  # noqa: E731
        st, out = self.feed([ev("Hel"), ev("lo"), {"type": "stream_event", "event": {"type": "message_start"}}, ev("Next")])
        self.assertEqual(out, [("text", "Hel"), ("text", "Hello"), ("text", "Next")])

    def test_assistant_message_without_partials(self):
        st, out = self.feed([{"type": "assistant", "message": {"content": [{"type": "text", "text": "whole"}]}}])
        self.assertEqual(out, [("text", "whole")])

    def test_tool_use_reported_once_and_subagents_ignored(self):
        tu = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "1", "name": "Read", "input": {"file_path": "/x"}}]}}
        st, out = self.feed([tu, tu, {**tu, "parent_tool_use_id": "p"}])
        self.assertEqual(out, [("tool", "Reading x")])

    def test_denied_and_result_and_session(self):
        st, out = self.feed([{"type": "system", "subtype": "init", "session_id": "S"},
                             {"type": "system", "subtype": "permission_denied", "tool_name": "Write"},
                             {"type": "result", "result": "done", "session_id": "S"}])
        self.assertEqual((st.session_id, st.denied, st.result["result"]), ("S", ["Write"], "done"))
        self.assertEqual(out, [("denied", "Write")])

    def test_garbage_and_unknown_events_are_ignored(self):
        self.assertIsNone(streaming.parse_line("Warning: no stdin data"))
        self.assertIsNone(streaming.parse_line("{broken"))
        self.assertIsNone(streaming.parse_line("[1, 2]"))
        st, out = self.feed([{"type": "future_thing", "x": 1}, {"type": "stream_event", "event": {"type": "new_kind"}}])
        self.assertEqual(out, [])

    def test_denial_note(self):
        self.assertEqual(streaming.denial_note([], "Read only"), "")
        n = streaming.denial_note([{"tool_name": "Bash"}, {"tool_name": "Bash"}, {"tool_name": "Write"}], "Read only")
        self.assertIn("Bash, Write", n); self.assertIn("Read only", n)


class ChatStore(unittest.TestCase):
    def setUp(self):
        fresh_config()

    def msgs(self, text="hello there"):
        return [{"who": "You", "text": text, "kind": "user", "meta": "", "ts": 1.0},
                {"who": "Claude", "text": "hi", "kind": "assistant", "meta": "", "ts": 2.0}]

    def test_save_load_list(self):
        a, b = chats.new_id(), chats.new_id()
        chats.save({"id": a, "messages": self.msgs("first chat"), "sid": "s", "cost_base": 0.1, "cwd": "/tmp"})
        chats.save({"id": b, "messages": self.msgs("second chat")})
        self.assertEqual({c["title"] for c in chats.list_chats()}, {"first chat", "second chat"})
        self.assertEqual(chats.load(a)["sid"], "s")
        if sys.platform != "win32":
            self.assertEqual((config.chats_dir() / f"{a}.json").stat().st_mode & 0o777, 0o600)

    def test_rename_sticks_and_delete(self):
        a = chats.new_id()
        chats.save({"id": a, "messages": self.msgs()})
        chats.rename(a, "  My   thread ")
        chats.save({"id": a, "messages": self.msgs() + self.msgs("more")})        # a later save keeps the typed name
        self.assertEqual(chats.list_chats()[0]["title"], "My thread")
        chats.delete(a); self.assertEqual(chats.list_chats(), []); chats.delete(a)

    def test_bad_ids_cannot_escape_the_folder(self):
        for bad in ("../x", "a/b", "", "x" * 100):
            with self.assertRaises(ValueError):
                chats.save({"id": bad, "messages": self.msgs()})
            self.assertIsNone(chats.load(bad) if chats._valid_id(bad) else None)

    def test_damaged_and_empty_chats_are_skipped(self):
        config.chats_dir().mkdir(parents=True)
        (config.chats_dir() / "broken.json").write_text("{nope")
        (config.chats_dir() / "empty.json").write_text(json.dumps({"messages": []}))
        self.assertEqual(chats.list_chats(), [])

    def test_current_pointer(self):
        self.assertIsNone(chats.get_current())
        chats.set_current("abc-1"); self.assertEqual(chats.get_current(), "abc-1")
        (config.config_dir() / "current_chat").write_text("../../etc/passwd"); self.assertIsNone(chats.get_current())
        chats.set_current(None); self.assertIsNone(chats.get_current())

    def test_legacy_single_chat_is_imported_once(self):
        config.legacy_chat_path().write_text(json.dumps({"sid": "old", "cost_base": 0.5, "messages": self.msgs("from 0.1")}))
        cid = chats.import_legacy()
        self.assertEqual(chats.load(cid)["sid"], "old"); self.assertEqual(chats.get_current(), cid)
        self.assertFalse(config.legacy_chat_path().exists()); self.assertIsNone(chats.import_legacy())
        self.assertEqual(len(chats.list_chats()), 1)

    def test_message_cap(self):
        a = chats.new_id()
        chats.save({"id": a, "messages": [{"who": "You", "text": str(i), "kind": "user", "ts": 1.0} for i in range(chats.MAX_SAVED_MSGS + 30)]})
        self.assertEqual(len(chats.load(a)["messages"]), chats.MAX_SAVED_MSGS)


class LegacyFolder(unittest.TestCase):
    """Versions before 0.3 used ~/.config/aichat, a name other programs use too."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="aichat-legacy-"))
        self.old, self.new = self.base / "aichat", self.base / "aichat-desktop"
        (self.old / "chats").mkdir(parents=True)
        (self.old / "chats" / "c1.json").write_text("{}")
        (self.old / "env").write_text("ANTHROPIC_API_KEY=sk-ant-x\n")
        (self.old / "settings.json").write_text("{}")

    def test_only_our_files_move(self):
        (self.old / "config.yaml").write_text("someone else's file")        # e.g. the other `aichat` program
        moved = config.migrate_legacy_dir(self.old, self.new)
        self.assertEqual(set(moved), {"chats", "env", "settings.json"})
        self.assertTrue((self.new / "chats" / "c1.json").is_file()); self.assertTrue((self.new / "env").is_file())
        self.assertEqual((self.old / "config.yaml").read_text(encoding="utf-8"), "someone else's file")   # untouched, folder kept
        self.assertFalse((self.old / "env").exists())

    def test_empty_old_folder_is_removed(self):
        config.migrate_legacy_dir(self.old, self.new)
        self.assertFalse(self.old.exists())

    def test_runs_once_and_never_over_an_existing_new_folder(self):
        self.new.mkdir()
        self.assertEqual(config.migrate_legacy_dir(self.old, self.new), [])
        self.assertTrue((self.old / "env").exists())

    def test_nothing_of_ours_means_nothing_moves(self):
        other = self.base / "x"; (other / "aichat").mkdir(parents=True); (other / "aichat" / "config.yaml").write_text("y")
        self.assertEqual(config.migrate_legacy_dir(other / "aichat", other / "aichat-desktop"), [])
        self.assertFalse((other / "aichat-desktop").exists())

    def test_custom_config_location_never_looks_elsewhere(self):
        config.CONFIG_DIR = self.new
        try:
            self.assertEqual(config.migrate_legacy_dir(), [])
        finally:
            config.CONFIG_DIR = None
        self.assertTrue((self.old / "env").exists())

    def test_key_mode_survives_the_move(self):
        if sys.platform == "win32":
            self.skipTest("mode bits mean nothing on Windows")
        (self.old / "env").chmod(0o600)
        config.migrate_legacy_dir(self.old, self.new)
        self.assertEqual((self.new / "env").stat().st_mode & 0o777, 0o600)


class Packaging(unittest.TestCase):
    ROOT = Path(__file__).resolve().parent.parent

    def test_version_is_the_same_everywhere(self):
        import re
        proj = (self.ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertEqual(re.search(r'^version = "([^"]+)"', proj, re.M).group(1), app.__version__)

    def test_files_a_release_needs_exist(self):
        for rel in ("LICENSE", "README.md", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md", "pyproject.toml", ".github/dependabot.yml",
                    ".github/ISSUE_TEMPLATE/bug_report.md", "docs/screenshot-dark.png", "packaging/aichat-desktop.desktop", "packaging/aichat-desktop.metainfo.xml", "packaging/aichat-desktop.1", "packaging/build-deb.sh",
                    ".github/workflows/ci.yml", "aichat_desktop/assets/icon.png", "aichat_desktop/assets/icon.svg"):
            self.assertTrue((self.ROOT / rel).is_file(), rel)

    @unittest.skipIf(sys.version_info < (3, 11), "tomllib needs Python 3.11+ (other CI versions run this)")
    def test_names_do_not_clash_with_other_projects(self):
        import tomllib
        proj = tomllib.loads((self.ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(list(proj["project"]["gui-scripts"]), ["aichat-desktop"])           # not `aichat`
        self.assertEqual(proj["tool"]["setuptools"]["packages"], ["aichat_desktop", "aichat_desktop.assets"])
        self.assertFalse((self.ROOT / "aichat.py").exists()); self.assertFalse((self.ROOT / "aichat").exists())
        text = "\n".join(p.read_text(encoding="utf-8") for p in (self.ROOT / "aichat_desktop").glob("*.py"))
        self.assertNotIn("AICHAT_CONFIG_DIR", text.replace("AICHAT_DESKTOP_CONFIG_DIR", ""))   # that variable belongs to the other aichat

    @unittest.skipIf(sys.version_info < (3, 11), "tomllib needs Python 3.11+ (other CI versions run this)")
    def test_author_and_links_are_filled_in(self):
        import tomllib
        proj = tomllib.loads((self.ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        self.assertEqual(proj["authors"], [{"name": "NoobFromUA"}])
        self.assertTrue(all(u.startswith("https://github.com/NoobFromUA-YT/AiChat") for u in proj["urls"].values()))
        self.assertIn("NoobFromUA", (self.ROOT / "LICENSE").read_text(encoding="utf-8"))
        self.assertNotIn("example.com", (self.ROOT / "packaging" / "build-deb.sh").read_text(encoding="utf-8"))

    def test_main_prints_version_and_help_without_a_display(self):
        import io
        from contextlib import redirect_stdout
        for flag, want in (("--version", f"aichat-desktop {app.__version__}"), ("--help", "--install-desktop")):
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(app.main([flag]), 0)
            self.assertIn(want, buf.getvalue())

    def test_desktop_entry_install_and_uninstall(self):
        from aichat_desktop import desktop
        home = Path(tempfile.mkdtemp(prefix="aichat-xdg-"))
        written = desktop.install(home, '"/usr/bin/aichat-desktop"')
        names = {p.name for p in written}
        self.assertIn("aichat-desktop.desktop", names); self.assertIn("aichat-desktop.svg", names)
        entry = (home / "applications" / "aichat-desktop.desktop").read_text(encoding="utf-8")
        self.assertIn('Exec="/usr/bin/aichat-desktop"', entry); self.assertIn("Icon=aichat-desktop", entry)
        self.assertTrue((home / "icons/hicolor/256x256/apps/aichat-desktop.png").is_file())
        self.assertEqual(len(desktop.uninstall(home)), len(written))
        self.assertEqual([p for p in home.rglob("*") if p.is_file()], [])


class Themes(unittest.TestCase):
    def test_every_theme_is_complete_and_fills_the_template(self):
        keys = set(themes.CLASSIC)
        for name, t in themes.THEMES.items():
            self.assertEqual(set(t), keys, name)
            css = themes.stylesheet(t, "/x/arrow.png")
            self.assertNotIn("@", css, name); self.assertIn("/x/arrow.png", css)
            for k, v in t.items():
                self.assertRegex(v, r"^#[0-9a-f]{6}$", f"{name}.{k}")

    def test_resolve(self):
        self.assertEqual(themes.resolve("dark"), "dark"); self.assertEqual(themes.resolve("light"), "light")
        self.assertEqual(themes.resolve("system", True), "dark"); self.assertEqual(themes.resolve("system", False), "light")
        self.assertEqual(themes.resolve("system", None), "classic")           # the desktop does not say: the old look
        self.assertEqual(themes.resolve("bogus"), "classic")

    def test_dark_chat_is_really_dark_and_light_is_really_light(self):
        lum = lambda c: sum(int(c[i:i + 2], 16) for i in (1, 3, 5)) / 3  # noqa: E731
        self.assertLess(lum(themes.DARK["chat_bg"]), 70); self.assertGreater(lum(themes.DARK["chat_fg"]), 180)
        self.assertGreater(lum(themes.LIGHT["bg"]), 200); self.assertLess(lum(themes.LIGHT["fg"]), 60)
        self.assertGreater(lum(themes.CLASSIC["chat_bg"]), 200); self.assertLess(lum(themes.CLASSIC["bg"]), 50)

    def test_bad_theme_setting_falls_back(self):
        fresh_config(theme="neon"); self.assertEqual(config.load_settings()["theme"], "system")


class Images(unittest.TestCase):
    def setUp(self):
        self.cache = tempfile.mkdtemp(prefix="aichat-cache-")
        self._old = {k: os.environ.get(k) for k in ("XDG_CACHE_HOME", "LOCALAPPDATA")}      # Linux / Windows cache roots
        os.environ["XDG_CACHE_HOME"] = os.environ["LOCALAPPDATA"] = self.cache

    def tearDown(self):
        for k, v in self._old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def test_old_images_are_cleaned_up_and_new_ones_stay(self):
        from aichat_desktop import worker
        d = worker.image_dir()
        old, new, other = d / "aichat_old.png", d / "aichat_new.png", d / "keep.txt"
        for f in (old, new, other):
            f.write_text("x")
        t = old.stat().st_mtime - 9 * 86400
        os.utime(old, (t, t))
        self.assertEqual(worker.cleanup_images(), 1)
        self.assertFalse(old.exists()); self.assertTrue(new.exists()); self.assertTrue(other.exists())

    def test_images_go_to_the_private_cache_folder(self):
        from aichat_desktop import worker
        wk = worker.ChatWorker("sonnet", "high", "look", [("image/png", b"\x89PNG")], None, {}, 0.0, claude_bin="/nonexistent/claude")
        got = []
        wk.finished_reply.connect(lambda reply, *a: got.append(reply))
        wk.run()
        files = list(worker.image_dir().glob("aichat_*.png"))
        self.assertEqual(len(files), 1); self.assertTrue(str(files[0]).startswith(self.cache))


class Export(unittest.TestCase):
    def test_markdown(self):
        md = export_markdown([{"kind": "user", "text": "How?", "ts": 0},
                              {"kind": "assistant", "text": "Like **this**:\n```py\nx=1\n```", "meta": "sonnet · 3s", "ts": 1.0},
                              {"kind": "error", "text": "boom"}], "Title", "/proj")
        self.assertTrue(md.startswith("# Title\n"))
        for want in ("Project folder: `/proj`", "## You", "## Claude", "## Error", "```py\nx=1\n```", "sonnet · 3s"):
            self.assertIn(want, md)
        self.assertTrue(md.endswith("\n") and not md.endswith("\n\n"))


class Display(unittest.TestCase):
    def test_font_fallback(self):
        self.assertEqual(display.pick_family(["Foo", "dejavu sans", "Liberation Sans"]), "Liberation Sans")
        self.assertEqual(display.pick_family(["Arial", "Liberation Sans"]), "Arial")
        self.assertEqual(display.pick_family(["Foo", "Bar"]), "")          # nothing known: Qt's system font
        self.assertEqual(display.pick_family([]), "")

    def test_scale_detection(self):
        keys = ("QT_SCREEN_SCALE_FACTORS", "QT_SCALE_FACTOR", "AICHAT_DESKTOP_HARD_PIXELS")
        old = {k: os.environ.get(k) for k in keys}
        try:
            for k in keys:
                os.environ.pop(k, None)
            self.assertEqual(display.detect_ui_scale(), 1)
            os.environ["QT_SCREEN_SCALE_FACTORS"] = "HDMI-0=2;DP-1=1"; self.assertEqual(display.detect_ui_scale(), 2)
            os.environ["AICHAT_DESKTOP_HARD_PIXELS"] = "0"; self.assertEqual(display.detect_ui_scale(), 1)
            os.environ.pop("AICHAT_DESKTOP_HARD_PIXELS"); os.environ["QT_SCREEN_SCALE_FACTORS"] = "garbage"
            self.assertEqual(display.detect_ui_scale(), 1)
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def test_normal_mode_is_identity_and_antialiased(self):
        display.set_scale(1)
        self.assertEqual(display.S(15), 15)
        self.assertNotEqual(display.crisp_font(15).styleStrategy(), display.crisp_font(15).styleStrategy().NoAntialias)

    def test_hard_pixel_mode_scales_and_turns_antialiasing_off(self):
        display.set_scale(2)
        try:
            self.assertEqual(display.S(15), 30); self.assertTrue(display.HARD_PIXELS)
            self.assertTrue(display.crisp_font(30).styleStrategy() & display.crisp_font(30).styleStrategy().NoAntialias)
        finally:
            display.set_scale(1)

    def test_bundled_icon_exists_and_is_used(self):
        self.assertTrue((display.ASSETS / "icon.png").is_file())
        fresh_config()
        self.assertEqual(display.icon_path(), display.ASSETS / "icon.png")
        config.icon_override_path().write_bytes((display.ASSETS / "icon.png").read_bytes())
        self.assertEqual(display.icon_path(), config.icon_override_path())    # a user icon wins
        self.assertFalse(display.app_icon().isNull())


class CopyLinks(unittest.TestCase):
    def test_code_blocks_get_copy_links(self):
        sink = []
        h = md_to_html("a\n\n```py\nprint('<x>')\n```\n\nb\n\n```\nsecond\n```", code_blocks=sink)
        self.assertEqual(sink, ["print('<x>')", "second"])
        self.assertIn('href="aichat-copy:0"', h); self.assertIn('href="aichat-copy:1"', h)
        self.assertNotIn("aichat-copy", md_to_html("```\nx\n```"))               # no sink: no links


class Base(unittest.TestCase):
    def setUp(self):
        self.d = fresh_config(budget=200.0)
        self.answers = []
        self._orig = (QMessageBox.question, QMessageBox.warning, QMessageBox.information)
        QMessageBox.question = staticmethod(lambda *a, **k: self.answers.pop(0) if self.answers else NO)
        QMessageBox.warning = staticmethod(lambda *a, **k: self.answers.pop(0) if self.answers else NO)
        QMessageBox.information = staticmethod(lambda *a, **k: None)
        self.new_window()

    def tearDown(self):
        QMessageBox.question, QMessageBox.warning, QMessageBox.information = self._orig

    def new_window(self):
        self.w = app.AIChatWindow()
        self.launched = []
        w, outer = self.w, self

        def launch(prompt, imgs):
            class W:
                model, epoch = "sonnet", w.epoch
                def stop(self_): pass
            outer.launched.append(prompt)
            w.worker = W()
        w.launch_worker = launch

    def say(self, text):
        self.w.input_box.setPlainText(text); self.w.send_message()

    def reply(self, text="ok"):
        self.w.on_reply(text, "", 0.0, ("sid-1", 0.0, True, False))


class Folder(Base):
    def test_default_is_home_and_remembered(self):
        self.assertEqual(self.w.cwd, ""); self.assertIn(Path.home().name or "/", self.w.folder_btn.text())
        proj = tempfile.mkdtemp(prefix="aichat-proj-")
        self.w.set_cwd(proj)
        self.assertEqual(self.w.cwd, proj); self.assertEqual(config.load_settings()["cwd"], proj)
        self.assertIn(proj, self.w.folder_btn.toolTip())
        self.new_window(); self.assertEqual(self.w.cwd, proj)

    def test_missing_folder_is_ignored(self):
        self.w.set_cwd("/nonexistent/dir/xyz"); self.assertEqual(self.w.cwd, "")
        config.save_settings({**config.load_settings(), "cwd": "/gone"}); self.new_window(); self.assertEqual(self.w.cwd, "")

    def test_changing_folder_starts_a_new_chat_and_keeps_the_old_one(self):
        self.say("in home"); self.reply()
        old = self.w.chat_id
        self.w.set_cwd(tempfile.mkdtemp(prefix="aichat-proj-"))
        self.assertEqual(self.w.messages, []); self.assertIsNone(self.w.claude_sid)
        self.assertIn(old, [c["id"] for c in chats.list_chats()])

    def test_worker_gets_the_folder(self):
        proj = tempfile.mkdtemp(prefix="aichat-proj-")
        self.w.set_cwd(proj)
        seen = {}
        orig = app.ChatWorker.__init__
        app.ChatWorker.__init__ = lambda s, *a, **k: seen.update(k) or None
        try:
            del self.w.launch_worker; self.w.worker = None
            try: self.w.launch_worker("x", [])
            except Exception: pass
        finally:
            app.ChatWorker.__init__ = orig
        self.assertEqual(seen["cwd"], proj); self.assertEqual(seen["permission"], "readonly")

    def test_chat_remembers_its_folder(self):
        proj = tempfile.mkdtemp(prefix="aichat-proj-")
        self.w.set_cwd(proj); self.say("here"); self.reply(); cid = self.w.chat_id
        self.w.set_cwd(tempfile.mkdtemp(prefix="aichat-other-"))
        self.w.open_chat(cid); self.assertEqual(self.w.cwd, proj)


class Permission(Base):
    def test_new_users_start_read_only(self):
        self.assertEqual(self.w.permission(), "readonly")

    def test_full_auto_needs_a_yes(self):
        self.w.perm_box.setCurrentIndex(self.w.perm_box.findData("full"))
        self.answers[:] = [NO]; self.w.on_permission_picked()
        self.assertEqual(self.w.permission(), "readonly"); self.assertEqual(config.load_settings()["permission"], "readonly")
        self.w.perm_box.setCurrentIndex(self.w.perm_box.findData("full"))
        self.answers[:] = [YES]; self.w.on_permission_picked()
        self.assertEqual(self.w.permission(), "full"); self.assertEqual(config.load_settings()["permission"], "full")
        self.new_window(); self.assertEqual(self.w.permission(), "full")

    def test_safer_modes_need_no_question(self):
        self.w.perm_box.setCurrentIndex(self.w.perm_box.findData("edit")); self.w.on_permission_picked()
        self.assertEqual(config.load_settings()["permission"], "edit")

    def test_model_and_effort_are_remembered(self):
        self.w.model_box.setCurrentIndex(1); self.w.effort_box.setCurrentText("Low")
        self.new_window()
        self.assertEqual((self.w.model_box.currentIndex(), self.w.effort_box.currentText()), (1, "Low"))


class FirstRun(Base):
    def setUp(self):
        super().setUp()
        config.save_settings({**config.DEFAULT_SETTINGS})          # first run not done
        self._ex = dialogs.FirstRunDialog.exec
        self._env = os.environ.pop("ANTHROPIC_API_KEY", None)
        self._chk = config.check_key_online
        config.check_key_online = lambda key: True
        self.new_window()

    def tearDown(self):
        dialogs.FirstRunDialog.exec = self._ex
        config.check_key_online = self._chk
        if self._env is not None:
            os.environ["ANTHROPIC_API_KEY"] = self._env
        super().tearDown()

    def run_dialog(self, key, budget, accept=True):
        def fake_exec(dlg):
            if key:
                dlg.r_key.setChecked(True); dlg.key_edit.setText(key)
            dlg.budget.setValue(budget)
            if accept:
                dlg.ok_btn.click()
                return dlg.result()
            dlg.skip_btn.click()
            return dlg.result()
        dialogs.FirstRunDialog.exec = fake_exec
        self.w.maybe_first_run()

    def test_key_and_credit_are_saved(self):
        self.run_dialog("sk-ant-first-run-key-000000000", 50.0)
        self.assertEqual(config.load_own_key(), "sk-ant-first-run-key-000000000")
        if sys.platform != "win32":
            self.assertEqual(config.env_path().stat().st_mode & 0o777, 0o600)
        s = config.load_settings(); self.assertEqual((s["budget"], s["first_run_done"]), (50.0, True))
        self.assertEqual(self.w.balance_lbl.text(), "100% left"); self.assertIn("$50", self.w.balance_lbl.toolTip())
        self.assertEqual(self.w.env["ANTHROPIC_API_KEY"], "sk-ant-first-run-key-000000000")

    def test_subscription_user_needs_no_key_and_no_counter(self):
        self.run_dialog("", 0.0)
        self.assertEqual(config.load_own_key(), ""); self.assertFalse(config.env_path().exists())
        self.assertTrue(config.load_settings()["first_run_done"]); self.assertFalse(self.w.balance_lbl.isVisibleTo(self.w))

    def test_skip_is_remembered_and_nothing_saved(self):
        self.run_dialog("sk-ant-zzzzzzzzzzzzzzzzzzzzzzz", 10.0, accept=False)
        self.assertTrue(config.load_settings()["first_run_done"]); self.assertFalse(config.env_path().exists())
        self.assertEqual(config.load_settings()["budget"], 0.0)

    def test_bad_key_stays_in_the_dialog(self):
        dlg = dialogs.FirstRunDialog(None, "/bin/claude")
        dlg.r_key.setChecked(True); dlg.key_edit.setText("hello"); dlg.accept_values()
        self.assertTrue(dlg.error.text()); self.assertEqual(dlg.result(), 0)

    def test_only_asked_once(self):
        called = []
        dialogs.FirstRunDialog.exec = lambda dlg: called.append(1) or 0
        self.w.maybe_first_run(); self.w.maybe_first_run(); self.assertEqual(len(called), 1)

    def test_missing_claude_is_explained_in_the_empty_chat(self):
        orig = config.find_claude
        config.find_claude = lambda configured="": None
        try:
            self.new_window()
        finally:
            config.find_claude = orig
        self.assertIn("was not found", self.w.chat_view.toPlainText())


class SettingsTests(Base):
    def test_apply_settings_saves_and_sets_the_anchor(self):
        fresh_config(budget=0.0)
        self.new_window(); self.assertNotIn("claude_anchor_balance", self.w.usage)
        self.w.apply_settings({"budget": 30.0, "claude_path": ""})
        self.assertEqual(config.load_settings()["budget"], 30.0)
        self.assertEqual(self.w.usage["claude_anchor_balance"], 30.0); self.assertEqual(self.w.balance_lbl.text(), "100% left")

    def test_dialog_rejects_a_missing_claude_path(self):
        d = dialogs.SettingsDialog(None, config.load_settings())
        d.path.setText("/nonexistent/claude"); d.accept_values()
        self.assertIsNone(d.result_settings); self.assertTrue(d.error.text())
        d.path.setText(""); d.accept_values(); self.assertEqual(d.result_settings["claude_path"], "")


class ThemeWindow(Base):
    def set_theme(self, name):
        self.w.apply_settings({"theme": name, "budget": 200.0, "claude_path": ""})

    def test_switching_theme_recolours_window_cards_and_code(self):
        self.say("q"); self.reply("text\n\n```\ncode\n```")
        self.set_theme("dark")
        self.assertEqual(self.w.theme_name, "dark")
        self.assertIn(themes.DARK["chat_bg"], self.w.styleSheet())
        html = self.w.chat_view.toHtml().lower()
        self.assertIn(themes.DARK["card_assistant_bg"], html); self.assertIn(themes.DARK["md_code_bg"], html)
        self.assertEqual(mdhtml.CODE_BG, themes.DARK["md_code_bg"])
        self.set_theme("light")
        self.assertEqual(self.w.theme_name, "light"); self.assertIn(themes.LIGHT["bg"], self.w.styleSheet())
        self.assertEqual(APP.palette().window().color().name(), themes.LIGHT["bg"])
        self.set_theme("classic")
        self.assertEqual(APP.palette().window().color().name(), themes.CLASSIC["bg"])

    def test_messages_and_copy_links_survive_a_theme_change(self):
        self.say("q"); self.reply("```\necho hi\n```")
        self.set_theme("dark")
        self.assertEqual([m["text"] for m in self.w.messages], ["q", "```\necho hi\n```"])
        i = self.w.code_blocks.index("echo hi")
        self.w.on_link(QUrl(f"aichat-copy:{i}")); self.assertEqual(QApplication.clipboard().text(), "echo hi")

    def test_empty_chat_hint_follows_the_theme(self):
        self.set_theme("light")
        self.assertIn(themes.LIGHT["hint"], self.w.chat_view.toHtml().lower())

    def test_choice_is_remembered_and_follows_the_system(self):
        self.set_theme("dark"); self.assertEqual(config.load_settings()["theme"], "dark")
        self.new_window(); self.assertEqual(self.w.theme_name, "dark")
        orig = display.system_is_dark
        try:
            display.system_is_dark = lambda: True
            self.set_theme("system"); self.assertEqual(self.w.theme_name, "dark")
            display.system_is_dark = lambda: False
            self.w.apply_theme(); self.assertEqual(self.w.theme_name, "light")
        finally:
            display.system_is_dark = orig

    def test_settings_dialog_offers_every_theme(self):
        d = dialogs.SettingsDialog(None, {**config.load_settings(), "theme": "light"})
        self.assertEqual([d.theme.itemData(i) for i in range(d.theme.count())], [k for k, _ in themes.CHOICES])
        self.assertEqual(d.theme.currentData(), "light")
        d.theme.setCurrentIndex(d.theme.findData("dark")); d.accept_values()
        self.assertEqual(d.result_settings["theme"], "dark")


class LiveAndCopy(Base):
    def test_live_panel_follows_a_running_reply(self):
        self.assertFalse(self.w.live_box.isVisibleTo(self.w))
        self.w.show(); APP.processEvents()                  # the panel's height follows the laid-out document
        self.say("go")
        self.w.on_progress("tool", "Reading app.py")
        self.assertTrue(self.w.live_box.isVisibleTo(self.w)); self.assertIn("Reading app.py", self.w.live_tool.text())
        self.w.on_progress("text", "Partial **answer**"); self.w.flush_live_text()
        self.assertIn("Partial", self.w.live_text.toPlainText()); self.assertNotIn("**", self.w.live_text.toPlainText())
        short = self.w.live_text.minimumHeight()
        self.w.on_progress("text", "line\n\n" * 80); self.w.flush_live_text()
        self.assertGreater(self.w.live_text.minimumHeight(), short); self.assertLessEqual(self.w.live_text.minimumHeight(), display.S(190))
        self.w.on_progress("denied", "Bash"); self.assertIn("Bash refused", self.w.live_tool.text())
        self.reply("final")
        self.assertFalse(self.w.live_box.isVisibleTo(self.w)); self.assertEqual(self.w.live_tool.text(), "")

    def test_progress_of_an_old_chat_or_idle_window_is_ignored(self):
        self.w.on_progress("tool", "Reading x"); self.assertFalse(self.w.live_box.isVisibleTo(self.w))     # idle
        self.say("go"); self.w.on_progress("tool", "Reading old", self.w.epoch - 1)
        self.assertEqual(self.w.live_tool.text(), "")

    def test_new_chat_hides_the_live_panel(self):
        self.say("go"); self.w.on_progress("tool", "Reading x"); self.w.reset_chat()
        self.assertFalse(self.w.live_box.isVisibleTo(self.w))

    def test_copy_links_fill_the_clipboard(self):
        self.say("q"); self.reply("Intro\n\n```bash\necho hi\n```")
        blocks = self.w.code_blocks
        self.assertIn("echo hi", blocks); self.assertTrue(any(b.startswith("Intro") for b in blocks))
        self.w.on_link(QUrl(f"aichat-copy:{blocks.index('echo hi')}"))
        self.assertEqual(QApplication.clipboard().text(), "echo hi")
        self.w.on_link(QUrl("aichat-copy:999")); self.w.on_link(QUrl("aichat-copy:abc"))       # junk is harmless
        self.assertEqual(QApplication.clipboard().text(), "echo hi")

    def test_only_web_links_open_outside(self):
        opened = []
        orig = app.QDesktopServices.openUrl
        app.QDesktopServices.openUrl = staticmethod(lambda u: opened.append(u.toString()))
        try:
            self.w.on_link(QUrl("https://example.com/x")); self.w.on_link(QUrl("file:///etc/passwd")); self.w.on_link(QUrl("javascript:alert(1)"))
        finally:
            app.QDesktopServices.openUrl = orig
        self.assertEqual(opened, ["https://example.com/x"])


class ChatsAndExport(Base):
    def test_new_chat_keeps_the_old_one_in_history(self):
        self.say("one"); self.reply("a"); first = self.w.chat_id
        self.w.reset_chat(); self.say("two"); self.reply("b")
        ids = [c["id"] for c in chats.list_chats()]
        self.assertEqual(len(ids), 2); self.assertIn(first, ids); self.assertNotEqual(self.w.chat_id, first)

    def test_open_old_chat_restores_messages_and_session(self):
        self.say("one"); self.reply("answer one"); first = self.w.chat_id
        self.w.reset_chat(); self.say("two"); self.w.on_reply("answer two", "", 0.0, ("sid-2", 0.0, True, False))
        self.w.open_chat(first)
        self.assertEqual([m["text"] for m in self.w.messages], ["one", "answer one"]); self.assertEqual(self.w.claude_sid, "sid-1")
        self.assertIn("answer one", self.w.chat_view.toPlainText()); self.assertNotIn("answer two", self.w.chat_view.toPlainText())
        self.new_window(); self.assertEqual(self.w.chat_id, first)               # the opened chat is what comes back

    def test_cannot_switch_while_a_reply_runs(self):
        self.say("one"); self.reply("a"); first = self.w.chat_id
        self.w.reset_chat(); self.say("two")                                      # busy
        self.w.open_chat(first); self.assertEqual([m["text"] for m in self.w.messages], ["two"])

    def test_dialog_lists_renames_deletes(self):
        self.say("alpha"); self.reply("a"); self.w.reset_chat(); self.say("beta"); self.reply("b")
        d = dialogs.ChatsDialog(self.w, self.w.chat_id)
        self.assertEqual(d.list.count(), 2)
        d.list.setCurrentRow(1); sel = d.selected_id()
        d.open_selected(); self.assertEqual(d.open_id, sel)
        d2 = dialogs.ChatsDialog(self.w, self.w.chat_id, busy=True)
        d2.list.setCurrentRow(0); d2.open_selected(); self.assertIsNone(d2.open_id)      # busy: opening is off
        orig = dialogs.QMessageBox.question
        dialogs.QMessageBox.question = staticmethod(lambda *a, **k: YES)
        try:
            d3 = dialogs.ChatsDialog(self.w, self.w.chat_id); d3.list.setCurrentRow(0); gone = d3.selected_id(); d3.delete_selected()
        finally:
            dialogs.QMessageBox.question = orig
        self.assertEqual(d3.list.count(), 1); self.assertEqual(d3.deleted, [gone])

    def test_export_writes_markdown(self):
        self.say("How do I?"); self.reply("Like this:\n```py\nx = 1\n```")
        out = Path(tempfile.mkdtemp(prefix="aichat-exp-")) / "chat.md"
        self.w.write_export(str(out))
        text = out.read_text(encoding="utf-8")
        self.assertIn("## You", text); self.assertIn("How do I?", text); self.assertIn("```py\nx = 1\n```", text)

    def test_export_with_nothing_to_export_does_not_ask_for_a_file(self):
        called = []
        orig = app.QFileDialog.getSaveFileName
        app.QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: called.append(1) or ("", ""))
        try:
            self.w.export_chat()
        finally:
            app.QFileDialog.getSaveFileName = orig
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
