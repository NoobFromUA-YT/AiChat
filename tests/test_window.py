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

        self.pushed_to_claude, self.push_ok = [], False

        def launch(prompt, imgs):
            class W:
                model = "sonnet"
                epoch = w.epoch
                def stop(self_): outer.stopped.append(1)
                def push(self_, text, images=None):
                    if outer.push_ok:
                        outer.pushed_to_claude.append(text)
                    return outer.push_ok
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
        self.assertEqual(self.w.send_btn.text(), "Push")               # Enter hands the message to the running reply
        self.assertIn("#d13438", self.w.styleSheet().lower())          # the red of #badge[state="busy"]
        self.reply()
        self.assertEqual(self.state(), "ready"); self.assertNotIn("thinking", self.w.windowTitle())
        self.assertEqual(self.w.send_btn.text(), "Send")


class Pushing(Base):
    """Enter while Claude works hands the message to the running reply; Ctrl+Enter / the Queue button wait for its end."""

    def setUp(self):
        super().setUp()
        self.push_ok = True

    def test_enter_pushes_and_the_buttons_say_so(self):
        self.say("first")
        self.assertEqual(self.w.send_btn.text(), "Push"); self.assertFalse(self.w.queue_btn.isHidden())
        self.assertIn("pushes your message", self.w.input_box.placeholderText())
        self.say("also this")
        self.assertEqual(self.pushed_to_claude, ["also this"]); self.assertEqual(self.w.queue, [])
        self.assertEqual([p for p, _ in self.w.pushed], ["also this"]); self.assertFalse(self.w.queue_box.isHidden())
        self.assertEqual(self.w.input_box.toPlainText(), "")
        self.assertEqual(self.launched, ["first"])                       # no second call: it goes into the running one

    def test_the_pushed_message_appears_in_the_chat_when_claude_takes_it_in(self):
        self.say("first"); self.say("also this")
        self.assertEqual([m["text"] for m in self.w.messages], ["first"])        # not before Claude has it
        self.w.on_push_delivered(self.w.epoch)
        self.assertEqual([m["text"] for m in self.w.messages], ["first", "also this"])
        self.assertEqual(self.w.messages[-1]["kind"], "user"); self.assertIn("pushed", self.w.messages[-1]["meta"])
        self.assertEqual(self.w.pushed, []); self.assertTrue(self.w.queue_box.isHidden())

    def test_a_delivery_of_an_old_chat_is_ignored(self):
        self.say("first"); self.say("also this")
        self.w.on_push_delivered(self.w.epoch - 1)
        self.assertEqual(len(self.w.messages), 1); self.assertEqual(len(self.w.pushed), 1)

    def test_ctrl_enter_and_the_queue_button_wait_for_the_reply(self):
        self.say("first")
        self.w.input_box.setPlainText("later"); self.w.queue_message()
        self.assertEqual(self.pushed_to_claude, []); self.assertEqual([p for p, _ in self.w.queue], ["later"])
        self.reply(); self.assertEqual(self.launched, ["first", "later"])

    def test_ctrl_enter_in_the_box_queues(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        self.say("first")
        self.w.input_box.setPlainText("later")
        QTest.keyClick(self.w.input_box, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual([p for p, _ in self.w.queue], ["later"]); self.assertEqual(self.pushed_to_claude, [])
        self.w.input_box.setPlainText("now")
        QTest.keyClick(self.w.input_box, Qt.Key.Key_Return)
        self.assertEqual(self.pushed_to_claude, ["now"])

    def test_a_refused_push_is_queued_instead(self):
        self.say("first"); self.push_ok = False
        self.say("too late")
        self.assertEqual([p for p, _ in self.w.queue], ["too late"]); self.assertEqual(self.w.pushed, [])

    def test_images_travel_with_the_push(self):
        self.say("first")
        self.w.pending_images = [("image/png", b"PNG", "x")]
        self.say("look at this")
        self.assertEqual(self.w.pushed[0][1], [("image/png", b"PNG")]); self.assertEqual(self.w.pending_images, [])

    def test_a_push_without_words_but_with_an_image_is_allowed(self):
        self.say("first")
        self.w.pending_images = [("image/png", b"PNG", "x")]
        self.w.input_box.clear(); self.w.send_message()
        self.assertEqual(self.pushed_to_claude, ["Describe/analyze this image."])

    def test_not_while_compacting_or_stopping(self):
        self.say("first")
        self.w.compacting = True; self.w.refresh_badge()
        self.assertEqual(self.w.send_btn.text(), "Queue"); self.assertTrue(self.w.queue_btn.isHidden())
        self.say("x"); self.assertEqual(self.pushed_to_claude, []); self.assertEqual(len(self.w.queue), 1)
        self.w.compacting = False; self.w.stopping = True
        self.say("y"); self.assertEqual(self.pushed_to_claude, [])

    def test_an_undelivered_push_goes_out_next_after_the_reply(self):
        self.say("first"); self.say("pushed one")
        self.reply()                                                     # the call ended without taking it in
        self.assertEqual(self.launched, ["first", "pushed one"]); self.assertEqual(self.w.pushed, [])

    def test_an_undelivered_push_waits_in_front_of_the_queue_after_a_stop(self):
        self.say("first"); self.say("pushed one")
        self.w.input_box.setPlainText("queued one"); self.w.queue_message()
        self.w.stop_generation(); self.reply("(stopped)", ok=False)
        self.assertEqual([p for p, _ in self.w.queue], ["pushed one", "queued one"])
        self.assertEqual(self.state(), "paused"); self.assertEqual(self.launched, ["first"])

    def test_new_chat_forgets_pushed_messages(self):
        self.say("first"); self.say("pushed one")
        self.w.reset_chat(); self.assertEqual(self.w.pushed, []); self.assertTrue(self.w.queue_box.isHidden())

    def test_a_second_turn_is_shown_while_claude_keeps_working(self):
        self.say("first"); self.say("pushed one")
        self.w.on_reply("answer one", "", 0.1, ("sid-1", 0.1, True, False, False), final=False)
        self.assertTrue(self.w.busy); self.assertEqual(self.state(), "busy")
        self.assertEqual([m["text"] for m in self.w.messages if m["kind"] == "assistant"], ["answer one"])
        self.assertEqual(self.w.pushed and len(self.w.pushed), 1)       # still waiting for the delivery
        self.w.on_push_delivered(self.w.epoch)
        self.w.on_reply("answer two", "", 0.2, ("sid-1", 0.3, True, False, False))
        self.assertFalse(self.w.busy)
        self.assertEqual([m["text"] for m in self.w.messages], ["first", "answer one", "pushed one", "answer two"])
        self.assertAlmostEqual(self.w.usage["claude_spent"], 0.3); self.assertEqual(self.launched, ["first"])

    def test_the_closing_event_of_a_give_up_shows_no_empty_card(self):
        self.say("first")
        self.w.on_reply("answer one", "", 0.1, ("sid-1", 0.1, True, False, False), final=False)
        self.w.on_reply("", "", 0.0, ("sid-1", 0.1, True, False, True))
        self.assertEqual([m["text"] for m in self.w.messages], ["first", "answer one"]); self.assertFalse(self.w.busy)

    def test_push_now_moves_a_queued_message_into_the_reply(self):
        self.say("first")
        self.w.input_box.setPlainText("waiting"); self.w.queue_message()
        item = self.w.queue[0]
        self.assertEqual(len(self.w.row_push_btns), 1); self.assertFalse(self.w.row_push_btns[0].isHidden())
        self.w.push_queued(item)
        self.assertEqual(self.pushed_to_claude, ["waiting"]); self.assertEqual(self.w.queue, [])
        self.assertEqual([p for p, _ in self.w.pushed], ["waiting"])

    def test_push_now_that_fails_keeps_the_message_queued(self):
        self.say("first")
        self.w.input_box.setPlainText("waiting"); self.w.queue_message()
        self.push_ok = False
        self.w.push_queued(self.w.queue[0])
        self.assertEqual([p for p, _ in self.w.queue], ["waiting"]); self.assertEqual(self.w.pushed, [])

    def test_push_now_is_hidden_when_nothing_runs(self):
        self.say("first"); self.w.input_box.setPlainText("waiting"); self.w.queue_message()
        self.w.stop_generation(); self.reply("(stopped)", ok=False)
        self.assertTrue(self.w.row_push_btns[0].isHidden())


class EditQueue(Base):
    """A queued message can be changed, not just deleted."""

    def edit_with(self, item, new_text, before=None):
        """Run edit_queued with a stand-in dialog that 'saves' new_text (before() runs while the dialog is open)."""
        outer = self

        class FakeDialog:
            def __init__(self_, parent, text, n_images=0):
                outer.dialog_args = (text, n_images)
            def exec(self_):
                if before:
                    before()
                return True
            def text(self_):
                return new_text
        orig, app.EditMessageDialog = app.EditMessageDialog, FakeDialog
        try:
            self.w.edit_queued(item)
        finally:
            app.EditMessageDialog = orig

    def test_edit_keeps_position_and_images(self):
        self.say("one"); self.say("two")
        self.w.pending_images = [("image/png", b"PNG", "x")]
        self.say("three"); self.say("four")
        item = self.w.queue[1]
        self.edit_with(item, "THREE, fixed")
        self.assertEqual([p for p, _ in self.w.queue], ["two", "THREE, fixed", "four"])
        self.assertEqual(self.w.queue[1][1], [("image/png", b"PNG")]); self.assertEqual(self.dialog_args, ("three", 1))

    def test_edited_message_is_what_gets_sent(self):
        self.say("one"); self.say("two"); self.edit_with(self.w.queue[0], "two, better")
        self.reply(); self.assertEqual(self.launched, ["one", "two, better"])

    def test_row_has_edit_push_and_remove_buttons(self):
        from PyQt6.QtWidgets import QPushButton
        self.say("one"); self.say("two")
        names = [b.text() for b in self.w.queue_box.findChildren(QPushButton)]
        self.assertEqual(sorted(names), sorted(["Edit", "Push now", "\u00d7"]))

    def test_message_that_was_sent_while_the_dialog_was_open(self):
        from unittest import mock
        self.say("one"); self.say("two")
        item = self.w.queue[0]
        with mock.patch.object(app.QMessageBox, "information") as info:
            self.edit_with(item, "changed", before=lambda: self.reply())          # the queue moves on: 'two' is sent
        self.assertTrue(info.called); self.assertEqual(self.launched, ["one", "two"]); self.assertEqual(self.w.queue, [])

    def test_a_message_no_longer_queued_is_ignored(self):
        self.say("one"); self.say("two")
        item = self.w.queue[0]; self.w.remove_queued(0)
        self.edit_with(item, "x"); self.assertEqual(self.w.queue, [])
        self.assertFalse(hasattr(self, "dialog_args"))

    def test_the_dialog_needs_words_or_an_image(self):
        from aichat_desktop.dialogs import EditMessageDialog
        from PyQt6.QtWidgets import QDialogButtonBox
        d = EditMessageDialog(self.w, "hello", 0)
        save = d.buttons.button(QDialogButtonBox.StandardButton.Save)
        self.assertTrue(save.isEnabled()); d.edit.setPlainText("   "); self.assertFalse(save.isEnabled())
        d.edit.setPlainText("new  \n"); self.assertTrue(save.isEnabled()); self.assertEqual(d.text(), "new")
        d2 = EditMessageDialog(self.w, "", 2)
        self.assertTrue(d2.buttons.button(QDialogButtonBox.StandardButton.Save).isEnabled()); self.assertIn("2 images", d2.findChild(app.QLabel).text())


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
        self.assertEqual(config.env_path().read_text(encoding="utf-8"), "FOO=bar\n")

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
