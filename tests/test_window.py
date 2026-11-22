"""Window behaviour: red Thinking badge, message queue, stop / error pause, New Chat race, restore after restart.
Offscreen, temp files, no real claude (launch_worker is replaced). Run: tests/run.sh"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
for k in ("DISPLAY", "QT_SCREEN_SCALE_FACTORS", "SESSION_MANAGER"):
    os.environ.pop(k, None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aichat_desktop import app, config, costs, dialogs, worker as worker_mod  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])


class Base(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp(prefix="aichat-win-"))
        config.CONFIG_DIR = d                                  # every file of the app lives here: the real config is never touched
        config.save_settings({**config.DEFAULT_SETTINGS, "first_run_done": True, "budget": 200.0})
        costs.CLAUDE_PROJECTS = d / "projects"
        self.new_window()

    def new_window(self):
        self.w = app.AIChatWindow()
        self.launched, self.stopped = [], []
        w, outer = self.w, self

        def launch(prompt, imgs):
            class W:
                model = "sonnet"
                epoch = w.epoch
                def stop(self_): outer.stopped.append(1)
            outer.launched.append(prompt)
            w.worker = W()
        w.launch_worker = launch

    def say(self, text):
        self.w.input_box.setPlainText(text)
        self.w.send_message()

    def reply(self, text="ok", ok=True, total=0.1, cost=0.1):
        self.w.on_reply(text, "", cost, ("sid-1", total, ok))

    def state(self):
        return self.w.badge.property("state")


class Badge(Base):
    def test_idle_is_green_ready(self):
        self.assertEqual(self.state(), "ready"); self.assertIn("Ready", self.w.badge.text())
        self.assertEqual(self.w.send_btn.text(), "Send"); self.assertFalse(self.w.stop_btn.isEnabled())

    def test_thinking_is_red_with_timer_and_title(self):
        self.say("hello")
        self.assertEqual(self.state(), "busy"); self.assertIn("Thinking", self.w.badge.text())
        self.assertRegex(self.w.badge.text(), r"\d+:\d\d")
        self.assertIn("thinking", self.w.windowTitle()); self.assertTrue(self.w.stop_btn.isEnabled())
        self.assertEqual(self.w.send_btn.text(), "Queue")
        self.assertIn("#d13438", self.w.styleSheet().lower())          # the red of #badge[state="busy"]
        self.reply()
        self.assertEqual(self.state(), "ready"); self.assertNotIn("thinking", self.w.windowTitle())
        self.assertEqual(self.w.send_btn.text(), "Send")


class Queue(Base):
    def test_messages_wait_and_go_out_in_order(self):
        self.say("one"); self.say("two"); self.say("three")
        self.assertEqual(self.launched, ["one"])
        self.assertEqual([p for p, _ in self.w.queue], ["two", "three"])
        self.assertFalse(self.w.queue_box.isHidden()); self.assertIn("2 queued", self.w.badge.text())
        self.reply(); self.assertEqual(self.launched, ["one", "two"]); self.assertIn("1 queued", self.w.badge.text())
        self.reply(); self.assertEqual(self.launched, ["one", "two", "three"]); self.assertTrue(self.w.queue_box.isHidden())
        self.reply(); self.assertEqual(self.state(), "ready"); self.assertEqual(self.w.queue, [])

    def test_user_messages_appear_in_the_chat_when_they_start(self):
        self.say("one"); self.say("two")
        self.assertEqual([m["text"] for m in self.w.messages], ["one"])
        self.reply("answer 1")
        self.assertEqual([m["text"] for m in self.w.messages], ["one", "answer 1", "two"])

    def test_stop_pauses_the_queue_until_resume(self):
        self.say("one"); self.say("two")
        self.w.stop_generation()
        self.assertEqual(self.stopped, [1]); self.assertIn("Stopping", self.w.badge.text())
        self.reply("(stopped)", ok=False)
        self.assertEqual(self.launched, ["one"])                      # "two" did not start by itself
        self.assertEqual(self.state(), "paused"); self.assertEqual(self.w.send_btn.text(), "Resume")
        self.w.send_message()                                         # empty input = resume
        self.assertEqual(self.launched, ["one", "two"]); self.assertEqual(self.state(), "busy")

    def test_new_message_after_stop_goes_behind_the_old_ones(self):
        self.say("one"); self.say("two"); self.w.stop_generation(); self.reply("(stopped)", ok=False)
        self.say("three")
        self.assertEqual(self.launched, ["one", "two"])
        self.assertEqual([p for p, _ in self.w.queue], ["three"])

    def test_error_pauses_the_queue(self):
        self.say("one"); self.say("two")
        self.reply("Error: boom", ok=False)
        self.assertEqual(self.launched, ["one"]); self.assertEqual(self.state(), "paused")
        self.assertEqual(self.w.messages[-1]["kind"], "error")

    def test_remove_from_queue(self):
        self.say("one"); self.say("two"); self.say("three")
        self.w.remove_queued(0)
        self.assertEqual([p for p, _ in self.w.queue], ["three"])
        self.w.remove_queued(0)
        self.assertTrue(self.w.queue_box.isHidden()); self.assertIn("Thinking", self.w.badge.text())

    def test_images_travel_with_their_queued_message(self):
        self.say("one")
        self.w.pending_images.append(("image/png", b"PNG", "a.png")); self.say("with picture")
        self.assertEqual(self.w.queue[0][1], [("image/png", b"PNG")]); self.assertEqual(self.w.pending_images, [])


class NewChat(Base):
    def test_late_reply_of_the_old_chat_is_dropped_but_paid(self):
        self.say("old question"); self.say("queued")
        old_worker = self.w.worker
        self.w.reset_chat()
        self.assertEqual(self.w.queue, []); self.assertEqual(self.w.messages, []); self.assertEqual(self.stopped, [1])
        self.w.worker = old_worker                                     # the old thread is still finishing
        self.reply("late answer", ok=False, total=0.4, cost=0.4)
        self.assertEqual(self.w.messages, []); self.assertIsNone(self.w.claude_sid)
        self.assertAlmostEqual(self.w.usage["claude_spent"], 0.4)
        self.assertEqual(self.state(), "ready")
        self.say("fresh"); self.assertEqual(self.launched[-1], "fresh")


class Restore(Base):
    def test_chat_and_session_survive_a_restart(self):
        self.say("remember me"); self.reply("I will **try**", total=0.25)
        self.new_window()                                              # "restart"
        self.assertEqual([m["text"] for m in self.w.messages], ["remember me", "I will **try**"])
        self.assertEqual(self.w.claude_sid, "sid-1"); self.assertAlmostEqual(self.w.claude_cost_base, 0.25)
        self.assertIn("Restored", self.w.badge.text())
        self.assertIn("try", self.w.chat_view.toPlainText()); self.assertNotIn("**", self.w.chat_view.toPlainText())

    def test_new_chat_is_what_gets_restored_next_time(self):
        self.say("a"); self.reply(); self.w.reset_chat(); self.new_window()
        self.assertEqual(self.w.messages, []); self.assertIn("Ask Claude anything", self.w.chat_view.toPlainText())

    def test_seeded_chat_with_unknown_cost_base(self):
        import json
        config.legacy_chat_path().write_text(json.dumps({"sid": "abc", "cost_base": None, "messages": [
            {"who": "You", "text": "hi", "kind": "user", "meta": "", "ts": 1.0}]}))
        self.new_window()
        self.assertEqual(self.w.claude_sid, "abc"); self.assertIsNone(self.w.claude_cost_base)
        self.say("next"); self.reply(total=30.0, cost=0.0)             # worker booked nothing; base becomes the real total
        self.assertEqual(self.w.claude_cost_base, 30.0); self.assertEqual(self.w.usage["claude_spent"], 0.0)

    def test_unpriced_reply_marks_the_balance_until_calibrated(self):
        import json
        config.legacy_chat_path().write_text(json.dumps({"sid": "abc", "cost_base": None, "messages": [
            {"who": "You", "text": "hi", "kind": "user", "meta": "", "ts": 1.0}]}))
        self.new_window()
        self.assertFalse(self.w.balance_lbl.text().endswith("*"))
        self.say("next"); self.w.on_reply("ok", "chat $9.00", 0.0, ("abc", 9.0, True, True))
        self.assertTrue(self.w.balance_lbl.text().endswith("*")); self.assertIn("could not be priced", self.w.balance_lbl.toolTip())
        self.w.apply_calibration(183.91)
        self.assertFalse(self.w.balance_lbl.text().endswith("*")); self.assertEqual(self.w.usage["claude_uncounted"], 0)
        self.assertEqual(self.w.usage["claude_anchor_balance"], 183.91)
        self.assertEqual(self.w.balance_lbl.text(), "92% left")                  # 183.91 of 200 = 91.96

    def test_damaged_file_is_ignored(self):
        config.legacy_chat_path().write_text("{not json"); self.new_window()
        self.assertEqual(self.w.messages, [])
        config.legacy_chat_path().write_text('{"messages": [{"kind": "bogus", "text": 5}]}'); self.new_window()
        self.assertEqual(self.w.messages, [])


class Rendering(Base):
    def test_reply_renders_markdown_not_raw_symbols(self):
        self.say("q")
        self.reply("## Title\n\n| a | b |\n|---|---|\n| **x** | `y` |\n\n- one\n- two")
        text = self.w.chat_view.toPlainText()
        for raw in ("##", "**", "|---", "`"):
            self.assertNotIn(raw, text, raw)
        self.assertIn("Title", text); self.assertIn("one", text)

    def test_html_in_a_reply_is_shown_as_text(self):
        self.say("q"); self.reply("<b>not bold</b> <script>x</script>")
        self.assertIn("<b>not bold</b>", self.w.chat_view.toPlainText())



def write_transcript(root, sid, records, filler=0):
    import json
    d = Path(root) / "-home-test"
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{sid}.jsonl"
    lines = ["x" * 1000 for _ in range(filler)] + [json.dumps(r) for r in records]
    f.write_text("\n".join(lines) + "\n")
    return f


def asst(inp, cr, cw, side=False):
    return {"type": "assistant", "isSidechain": side,
            "message": {"usage": {"input_tokens": inp, "cache_read_input_tokens": cr, "cache_creation_input_tokens": cw}}}


def boundary(pre, post):
    return {"type": "system", "subtype": "compact_boundary",
            "compactMetadata": {"trigger": "manual", "preTokens": pre, "postTokens": post}}


class Context(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="aichat-proj-"))
        costs.CLAUDE_PROJECTS = self.root

    def test_last_assistant_turn_is_the_context(self):
        write_transcript(self.root, "s1", [asst(2, 100, 5), asst(2, 273_000, 845)])
        self.assertEqual(costs.context_tokens("s1"), 273_847)

    def test_compact_after_the_last_reply_wins(self):
        write_transcript(self.root, "s2", [asst(2, 273_000, 845), boundary(274_000, 2_924)])
        self.assertEqual(costs.context_tokens("s2"), 2_924)
        self.assertEqual(costs.last_compact("s2"), (274_000, 2_924))
        write_transcript(self.root, "s2", [boundary(274_000, 2_924), asst(2, 5_000, 300)])      # a reply after it
        self.assertEqual(costs.context_tokens("s2"), 5_302)

    def test_sidechain_and_unknown_sessions(self):
        write_transcript(self.root, "s3", [asst(2, 1_000, 0), asst(2, 999_999, 0, side=True)])
        self.assertEqual(costs.context_tokens("s3"), 1_002)
        self.assertIsNone(costs.context_tokens("nope")); self.assertIsNone(costs.context_tokens(None))
        self.assertIsNone(costs.last_compact("s3"))

    def test_big_file_reads_only_the_tail(self):
        old = costs.TAIL_BYTES; costs.TAIL_BYTES = 20_000
        try:
            write_transcript(self.root, "s4", [asst(1, 10, 0)], filler=0)
            write_transcript(self.root, "s4", [asst(1, 10, 0)] + [asst(3, 50_000, 7)], filler=400)   # 400 KB of junk first
            self.assertEqual(costs.context_tokens("s4"), 50_010)
        finally:
            costs.TAIL_BYTES = old

    def test_fmt_tokens(self):
        for n, want in ((950, "950"), (2_924, "2.9k"), (18_031, "18k"), (273_847, "274k"), (1_250_000, "1.25M"), (2_000_000, "2M")):
            self.assertEqual(costs.fmt_tokens(n), want, n)


class Compact(Base):
    def setUp(self):
        super().setUp()
        self.root = Path(tempfile.mkdtemp(prefix="aichat-proj-"))
        costs.CLAUDE_PROJECTS = self.root

    def test_button_needs_a_chat_and_an_idle_window(self):
        self.assertFalse(self.w.compact_btn.isEnabled())                  # nothing to compact yet
        self.say("hi"); self.assertFalse(self.w.compact_btn.isEnabled())  # busy
        self.reply(); self.assertTrue(self.w.compact_btn.isEnabled())

    def test_context_label_and_warning_level(self):
        self.say("hi")
        write_transcript(self.root, "sid-1", [asst(2, 120_000, 500)])
        self.reply()
        self.assertEqual(self.w.ctx_lbl.text(), "context 121k"); self.assertEqual(self.w.ctx_lbl.property("level"), "ok")
        self.say("more"); write_transcript(self.root, "sid-1", [asst(2, 272_000, 845)]); self.reply()
        self.assertEqual(self.w.ctx_lbl.text(), "context 273k"); self.assertEqual(self.w.ctx_lbl.property("level"), "warn")
        self.assertEqual(self.w.compact_btn.property("level"), "warn"); self.assertIn("272,847", self.w.ctx_lbl.toolTip())

    def test_compact_flow(self):
        self.say("hi"); write_transcript(self.root, "sid-1", [asst(2, 272_000, 845)]); self.reply()
        n_before = len(self.w.messages)
        self.w.compact_chat()
        self.assertEqual(self.launched[-1], "/compact"); self.assertIn("Compacting", self.w.badge.text())
        self.assertEqual(self.state(), "busy"); self.assertFalse(self.w.compact_btn.isEnabled())
        write_transcript(self.root, "sid-1", [asst(2, 272_000, 845), boundary(272_847, 2_924)])
        self.reply("", total=0.5)
        new = self.w.messages[n_before:]
        self.assertEqual(len(new), 1)                                       # a note, no "You" message, no empty reply
        self.assertEqual(new[0]["kind"], "note"); self.assertIn("273k \u2192 2.9k", new[0]["text"])
        self.assertEqual(self.w.ctx_lbl.text(), "context 2.9k"); self.assertEqual(self.w.ctx_lbl.property("level"), "ok")
        self.assertEqual(self.state(), "ready"); self.assertFalse(self.w.compacting)

    def test_queued_message_still_goes_out_after_a_compact(self):
        self.say("hi"); self.reply()
        self.w.compact_chat(); self.say("after compact")                    # queued behind the compaction
        self.assertEqual(self.launched, ["hi", "/compact"]); self.reply("")
        self.assertEqual(self.launched[-1], "after compact")

    def test_failed_compact_is_an_error_not_a_note(self):
        self.say("hi"); self.reply(); self.w.compact_chat(); self.reply("Error: boom", ok=False)
        self.assertEqual(self.w.messages[-1]["kind"], "error")

    def test_new_chat_during_compact_drops_the_late_note(self):
        self.say("hi"); self.reply(); self.w.compact_chat(); old = self.w.worker
        self.w.reset_chat(); self.w.worker = old; self.reply("")
        self.assertEqual(self.w.messages, []); self.assertFalse(self.w.compacting); self.assertEqual(self.state(), "ready")

    def test_note_cards_survive_a_restart(self):
        self.say("hi"); self.reply()
        write_transcript(self.root, "sid-1", [boundary(9_000, 1_000)])
        self.w.compact_chat(); self.reply("")
        self.new_window()
        self.assertEqual([m["kind"] for m in self.w.messages][-1], "note")
        self.assertIn("compacted", self.w.chat_view.toPlainText())


class ApiKeyTests(Base):
    """The API key can be changed in the window; it is saved in the app's own env file, mode 0600."""
    DEFAULT = "sk-ant-default-0000000000000000"
    NEW = "sk-ant-another-1111111111111111"

    def setUp(self):
        self._env = os.environ.get("ANTHROPIC_API_KEY")
        os.environ["ANTHROPIC_API_KEY"] = self.DEFAULT           # a key from the environment is the default
        super().setUp()
        self.answers = []
        self.online = True
        self._orig = (config.check_key_online, app.QMessageBox.question, app.QMessageBox.warning)
        config.check_key_online = lambda key: self.online
        app.QMessageBox.question = staticmethod(lambda *a, **k: self.answers.pop(0) if self.answers else app.QMessageBox.StandardButton.No)
        app.QMessageBox.warning = staticmethod(lambda *a, **k: self.answers.append("warned"))
        self.new_window()

    def tearDown(self):
        config.check_key_online, app.QMessageBox.question, app.QMessageBox.warning = self._orig
        if self._env is None:
            os.environ.pop("ANTHROPIC_API_KEY", None)
        else:
            os.environ["ANTHROPIC_API_KEY"] = self._env

    def key(self):
        return self.w.env["ANTHROPIC_API_KEY"]

    def test_default_key_until_one_is_entered(self):
        self.assertEqual(self.key(), self.DEFAULT)
        self.assertIn("environment", self.w.key_btn.toolTip())

    def test_new_key_is_used_and_saved_private(self):
        self.w.apply_key(self.NEW)
        self.assertEqual(self.key(), self.NEW)
        if sys.platform != "win32":
            self.assertEqual(config.env_path().stat().st_mode & 0o777, 0o600)
        self.assertEqual(os.environ["ANTHROPIC_API_KEY"], self.DEFAULT)      # the process environment is untouched
        self.assertNotIn(self.NEW, self.w.key_btn.toolTip())                 # only masked
        self.assertNotIn(self.NEW, self.w.chat_view.toPlainText())
        self.new_window()
        self.assertEqual(self.key(), self.NEW)                               # survives a restart

    def test_later_replies_carry_the_new_key(self):
        seen = []
        orig = worker_mod.ChatWorker.__init__
        worker_mod.ChatWorker.__init__ = lambda self_, model, effort, prompt, imgs, sid, env, base, **kw: seen.append(env["ANTHROPIC_API_KEY"]) or None
        try:
            self.w.apply_key(self.NEW)
            del self.w.launch_worker
            self.w.worker = None
            try: self.w.launch_worker("hi", [])
            except Exception: pass
        finally:
            worker_mod.ChatWorker.__init__ = orig
        self.assertEqual(seen, [self.NEW])

    def test_remove_saved_key_falls_back(self):
        self.w.apply_key(self.NEW); self.w.apply_key("")
        self.assertEqual(self.key(), self.DEFAULT)
        self.assertFalse(config.env_path().exists())

    def test_other_env_lines_survive(self):
        config.env_path().write_text("FOO=bar\n")
        self.w.apply_key(self.NEW); self.w.apply_key("")
        self.assertEqual(config.env_path().read_text(), "FOO=bar\n")

    def test_rejected_key_is_not_saved(self):
        self.online = False
        self.w.apply_key(self.NEW)
        self.assertEqual(self.key(), self.DEFAULT); self.assertFalse(config.env_path().exists())

    def test_unreachable_server_asks_first(self):
        self.online = None
        self.w.apply_key(self.NEW)                         # answer defaults to No
        self.assertFalse(config.env_path().exists())
        self.answers[:] = [app.QMessageBox.StandardButton.Yes, app.QMessageBox.StandardButton.No]
        self.w.apply_key(self.NEW)
        self.assertEqual(self.key(), self.NEW)

    def test_format_check_and_mask(self):
        self.assertIsNotNone(config.key_format_error(""))
        self.assertIsNotNone(config.key_format_error("hello world 1234567890123"))
        self.assertIsNotNone(config.key_format_error("sk-ant-abc def-123456789012345"))
        self.assertIsNone(config.key_format_error(self.NEW))
        self.assertNotIn("another", config.mask_key(self.NEW))

    def test_dialog_validates_before_accepting(self):
        d = dialogs.KeyDialog(self.w, self.DEFAULT, False)
        d.edit.setText("nonsense"); d.accept_key()
        self.assertIsNone(d.result_key); self.assertTrue(d.error.text())
        d.edit.setText(f"  {self.NEW}\n"); d.accept_key()
        self.assertEqual(d.result_key, self.NEW)
        self.assertFalse(d.default_btn.isEnabled())        # nothing to go back from


if __name__ == "__main__":
    unittest.main(verbosity=2)
