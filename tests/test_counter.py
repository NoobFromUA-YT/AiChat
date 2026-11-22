"""Credit counter tests. Run: tests/run.sh  (offscreen, temp usage file; never touches the real config folder)."""
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"
for k in ("DISPLAY", "QT_SCREEN_SCALE_FACTORS", "SESSION_MANAGER"):
    os.environ.pop(k, None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aichat_desktop import app, config, costs, worker as worker_mod  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])   # must stay referenced for the whole run


class Delta(unittest.TestCase):
    def test_cumulative_series_sums_to_last_total(self):
        # the real series from the session log of 2026-11-22 (total_cost_usd after each reply)
        totals = [0.17097750, 0.5, 0.7851956, 1.0675962, 1.156525, 1.5470307]
        base, spent = 0.0, 0.0
        for t in totals:
            spent += costs.claude_delta(t, base)
            base = t
        self.assertAlmostEqual(spent, totals[-1], places=9)
        self.assertGreater(sum(totals), 3 * spent)   # the old code added the totals themselves

    def test_new_session_restarts_at_zero(self):
        self.assertAlmostEqual(costs.claude_delta(0.2, 1.5), 0.2)

    def test_same_total_is_free(self):
        self.assertAlmostEqual(costs.claude_delta(1.0, 1.0), 0.0)


class Pct(unittest.TestCase):
    def test_pct(self):
        self.assertAlmostEqual(costs.balance_pct(194.0, 200.0), 97.0)
        self.assertAlmostEqual(costs.balance_pct(100.0, 200.0), 50.0)
        self.assertEqual(costs.balance_pct(5.0, 0.0), 0.0)


class Label(unittest.TestCase):
    def test_whole_percent_no_decimals_no_tilde(self):
        for pct, want in ((92.0, "92% left"), (92.4, "92% left"), (92.5, "93% left"), (97.75, "98% left"),
                          (100.0, "100% left"), (0.4, "0% left"), (0.0, "0% left")):
            self.assertEqual(costs.balance_label(pct), want, pct)


class Migration(unittest.TestCase):
    def test_schema1_is_reset_and_anchored(self):
        old = {"vertex_spent": 0.1, "vertex_msgs": 8, "claude_spent": 9.6885, "claude_msgs": 23}
        new, changed = costs.migrate_usage(dict(old), 150.0)
        self.assertTrue(changed)
        self.assertEqual(new["claude_spent"], 0.0)
        self.assertEqual(new["claude_spent_legacy"], 9.6885)
        self.assertEqual(new["vertex_spent"], 0.1)            # keys the app no longer uses are left alone
        left, since, a_bal = costs.balance_left(new, "claude", 200.0)
        self.assertAlmostEqual(left, 150.0)
        self.assertEqual(costs.migrate_usage(dict(new))[1], False)   # runs once

    def test_fresh_install_without_credit_has_no_anchor(self):
        new, changed = costs.migrate_usage({"claude_spent": 0.0, "claude_msgs": 0})
        self.assertTrue(changed); self.assertNotIn("claude_anchor_balance", new); self.assertNotIn("claude_spent_legacy", new)

    def test_balance_after_spend_and_recalibration(self):
        u, _ = costs.migrate_usage({}, 200.0)
        u["claude_spent"] = 1.25
        self.assertAlmostEqual(costs.balance_left(u, "claude", 200.0)[0], 198.75)
        u["claude_anchor_balance"], u["claude_anchor_spent"] = 190.0, 1.25   # user reads 190.00 in the console
        u["claude_spent"] = 1.75
        self.assertAlmostEqual(costs.balance_left(u, "claude", 200.0)[0], 189.50)

    def test_default_budget_without_anchor(self):
        self.assertAlmostEqual(costs.balance_left({"claude_spent": 10.0}, "claude", 200.0)[0], 190.0)


class Window(unittest.TestCase):
    def test_window_books_reply_deltas(self):
        with tempfile.TemporaryDirectory() as d:
            config.CONFIG_DIR = Path(d)
            config.save_settings({**config.DEFAULT_SETTINGS, "first_run_done": True, "budget": 200.0})
            costs.CLAUDE_PROJECTS = Path(d) / "projects"
            config.usage_path().write_text(json.dumps({"claude_spent": 9.6885, "claude_msgs": 23}))
            w = app.AIChatWindow()
            self.assertEqual(w.balance_lbl.text(), "100% left")               # migrated: the starting credit is the anchor
            idx = next(i for i, m in enumerate(w.MODELS) if m[1] == "claude")
            w.model_box.setCurrentIndex(idx)

            class W:   # stand-in worker
                model = w.MODELS[idx][2]
            w.worker = W()
            for total in (0.40, 0.55, 1.00):                  # three replies of one chat, cumulative totals
                w.on_reply("hi", "", costs.claude_delta(total, w.claude_cost_base), ("sid", total))
            self.assertAlmostEqual(w.usage["claude_spent"], 1.00, places=9)
            self.assertEqual(w.usage["claude_msgs"], 3)
            self.assertEqual(w.balance_lbl.text(), "100% left")                # 199 of 200 = 99.5 -> rounds half up
            self.assertIn("About $199.00 of $200", w.balance_lbl.toolTip())
            w.reset_chat()
            self.assertEqual(w.claude_cost_base, 0.0)
            saved = json.loads(config.usage_path().read_text(encoding="utf-8"))
            self.assertEqual(saved["schema"], 2)
            self.assertAlmostEqual(saved["claude_spent"], 1.00, places=9)
            w.worker = None
            w.close()

    def test_no_credit_entered_hides_the_counter(self):
        with tempfile.TemporaryDirectory() as d:
            config.CONFIG_DIR = Path(d)
            config.save_settings({**config.DEFAULT_SETTINGS, "first_run_done": True, "budget": 0.0})
            w = app.AIChatWindow()
            self.assertFalse(w.balance_lbl.isVisibleTo(w)); w.close()


FAKE_OK = (
    "#!/usr/bin/env python3\nimport json, sys\n"
    "def out(o): print(json.dumps(o), flush=True)\n"
    "out({'type': 'system', 'subtype': 'init', 'session_id': 's1'})\n"
    "out({'type': 'stream_event', 'event': {'type': 'message_start'}})\n"
    "out({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 't1', 'name': 'Read', 'input': {'file_path': '/x/app.py'}}]}})\n"
    "out({'type': 'stream_event', 'event': {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'o'}}})\n"
    "out({'type': 'stream_event', 'event': {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'k'}}})\n"
    "out({'type': 'result', 'is_error': False, 'result': 'ok', 'session_id': 's1', 'total_cost_usd': %s, 'permission_denials': %s})\n"
)


def make_fake(script: str) -> str:
    import stat
    d = tempfile.mkdtemp(prefix="aichat-test-")
    fake = Path(d) / "claude"
    fake.write_text(script)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return str(fake)


@unittest.skipIf(sys.platform == "win32", "the fake claude is a #! script")
class Claude(unittest.TestCase):
    """The worker against a fake `claude` binary that speaks stream-json (no network, no real session)."""

    def _worker(self, total, base, denials="[]", **kw):
        return worker_mod.ChatWorker("sonnet", "high", "hi", [], None, dict(os.environ), base,
                                     claude_bin=make_fake(FAKE_OK % (total, denials)), **kw)

    def test_meta_shows_reply_and_chat_cost(self):
        wk = self._worker(0.50, 0.20)
        reply, meta, cost, sid = wk._run_claude()
        self.assertEqual((reply, sid), ("ok", "s1"))
        self.assertAlmostEqual(cost, 0.30)
        self.assertEqual(meta, "1 tool call · $0.3000 · chat $0.50")

    def test_progress_is_streamed(self):
        wk = self._worker(0.5, 0.2)
        seen = []
        wk.progress.connect(lambda k, v: seen.append((k, v)))
        wk._run_claude()
        self.assertEqual(seen, [("tool", "Reading app.py"), ("text", "o"), ("text", "ok")])

    def test_refused_tools_are_explained(self):
        wk = self._worker(0.5, 0.2, denials='[{"tool_name": "Bash", "tool_input": {}}]', permission="readonly")
        reply = wk._run_claude()[0]
        self.assertIn("Refused in \"Read only\" mode: Bash", reply)

    def test_runs_in_the_project_folder(self):
        proj = tempfile.mkdtemp(prefix="aichat-proj-")
        script = "#!/usr/bin/env python3\nimport json, os\nprint(json.dumps({'type': 'result', 'result': os.getcwd(), 'total_cost_usd': 0}))\n"
        wk = worker_mod.ChatWorker("sonnet", "high", "hi", [], None, dict(os.environ), 0.0, cwd=proj, claude_bin=make_fake(script))
        self.assertEqual(os.path.realpath(wk._run_claude()[0]), os.path.realpath(proj))

    def test_failed_call_is_flagged(self):
        wk = worker_mod.ChatWorker("sonnet", "high", "hi", [], None, dict(os.environ), 0.0,
                                   claude_bin=make_fake("#!/bin/sh\necho 'boom' >&2\nexit 3\n"))
        reply = wk._run_claude()[0]
        self.assertEqual(reply, "Error: boom")
        self.assertFalse(wk.ok)

    def test_claude_error_result_is_flagged(self):
        script = "#!/usr/bin/env python3\nimport json\nprint(json.dumps({'type': 'result', 'is_error': True, 'result': 'Invalid API key', 'total_cost_usd': 0}))\n"
        wk = worker_mod.ChatWorker("sonnet", "high", "hi", [], None, {}, 0.0, claude_bin=make_fake(script))
        self.assertEqual(wk._run_claude()[0], "Invalid API key"); self.assertFalse(wk.ok)

    def test_unknown_session_total_books_nothing_once(self):
        wk = self._worker(12.0, None)
        reply, meta, cost, sid = wk._run_claude()
        self.assertEqual(cost, 0.0); self.assertIn("chat $12.00", meta); self.assertAlmostEqual(wk.cost_total, 12.0)

    def test_unpriced_reply_is_flagged(self):
        wk = self._worker(12.0, None)
        meta = wk._run_claude()[1]
        self.assertTrue(wk.uncounted); self.assertIn("NOT counted", meta)
        wk2 = self._worker(0.5, 0.2)
        self.assertNotIn("NOT counted", wk2._run_claude()[1]); self.assertFalse(wk2.uncounted)

    def test_long_chat_hint(self):
        meta = self._worker(3.20, 3.00)._run_claude()[1]
        self.assertIn("New Chat is cheaper", meta)

    def test_no_vertex_or_personal_paths_left(self):
        pkg = Path(app.__file__).parent
        for f in list(pkg.glob("*.py")) + [p for p in (pkg.parent / "README.md", pkg.parent / "pyproject.toml") if p.exists()]:
            low = f.read_text(encoding="utf-8").lower()
            for word in ("vertex", "gemini", "aiplatform", "exec_bash", "functioncall"):
                self.assertFalse(word in low, f"{word!r} still in {f.name}")
            self.assertIsNone(re.search(r"/home/\w+", low), f"a personal home path is still in {f.name}")


FAKE_HEAD = (
    "#!/usr/bin/env python3\nimport json, os, sys, time, select\n"
    "def out(o): print(json.dumps(o), flush=True)\n"
    "def echo(m): out({'type': 'user', 'message': m['message'], 'session_id': 's1'})\n"
    "buf = b''\n"
    "def wait_line(secs):   # raw reads: a buffered readline would hide a second line from select\n"
    "    global buf\n"
    "    end = time.time() + secs\n"
    "    while True:\n"
    "        if b'\\n' in buf:\n"
    "            line, buf = buf.split(b'\\n', 1)\n"
    "            return json.loads(line)\n"
    "        if time.time() >= end: return 'timeout'\n"
    "        if select.select([0], [], [], 0.05)[0]:\n"
    "            data = os.read(0, 65536)\n"
    "            if not data: return None\n"
    "            buf += data\n"
    "first = wait_line(5)\n"
    "out({'type': 'system', 'subtype': 'init', 'session_id': 's1'})\n"
    "echo(first)\n"
)
# a message pushed while the tool runs is read at the end of the tool: one reply answers both
FAKE_STEER = FAKE_HEAD + (
    "out({'type': 'assistant', 'message': {'content': [{'type': 'tool_use', 'id': 't1', 'name': 'Bash', 'input': {'command': 'sleep'}}]}})\n"
    "got = wait_line(1.0)\n"
    "n = 0\n"
    "if isinstance(got, dict): echo(got); n = 1\n"
    "out({'type': 'result', 'is_error': False, 'result': 'done:%d' % n, 'session_id': 's1', 'total_cost_usd': 0.5})\n"
)
# a message pushed while only text is written becomes a second turn after the first result
FAKE_TWO_TURNS = FAKE_HEAD + (
    "time.sleep(0.6)\n"
    "out({'type': 'result', 'is_error': False, 'result': 'r1', 'session_id': 's1', 'total_cost_usd': 0.1})\n"
    "got = wait_line(5)\n"
    "if isinstance(got, dict):\n"
    "    echo(got); out({'type': 'result', 'is_error': False, 'result': 'r2', 'session_id': 's1', 'total_cost_usd': 0.3})\n"
    "    wait_line(5)\n"
)
# the first result, then it never answers the pushed message
FAKE_DEAF = FAKE_HEAD + (
    "time.sleep(0.6)\n"
    "out({'type': 'result', 'is_error': False, 'result': 'r1', 'session_id': 's1', 'total_cost_usd': 0.1})\n"
    "while wait_line(5) not in (None, 'timeout'): pass\n"
)


@unittest.skipIf(sys.platform == "win32", "the fake claude is a #! script")
class Pushing(unittest.TestCase):
    """A message handed to the running call (stdin stays open until the last turn is done)."""

    def _run(self, script, push_after=None, texts=("extra",), base=0.0):
        import threading
        wk = worker_mod.ChatWorker("sonnet", "high", "start", [], None, dict(os.environ), base, claude_bin=make_fake(script))
        self.turns, self.delivered, self.pushes = [], [], []
        wk.turn_done.connect(lambda reply, meta, cost, st: self.turns.append((reply, cost, st)))
        wk.push_delivered.connect(lambda: self.delivered.append(1))
        if push_after is not None:
            def later():
                for t in texts:
                    self.pushes.append(wk.push(t))
            threading.Timer(push_after, later).start()
        self.final = wk._run_claude()
        return wk

    def test_command_reads_the_messages_from_stdin(self):
        cmd = worker_mod.ChatWorker("sonnet", "high", "hi", [], None, {}, 0.0, claude_bin="/bin/claude").build_command()
        self.assertEqual(cmd[cmd.index("--input-format") + 1], "stream-json")
        self.assertIn("--replay-user-messages", cmd); self.assertNotIn("hi", cmd)

    def test_push_during_a_tool_is_answered_in_the_same_reply(self):
        wk = self._run(FAKE_STEER, push_after=0.3)
        self.assertEqual(self.pushes, [True]); self.assertEqual(self.final[0], "done:1")
        self.assertEqual(self.delivered, [1]); self.assertEqual(self.turns, []); self.assertEqual(wk.undelivered, 0)
        self.assertAlmostEqual(wk.cost_total, 0.5)

    def test_without_a_push_the_call_ends_by_itself(self):
        wk = self._run(FAKE_STEER)
        self.assertEqual(self.final[0], "done:0"); self.assertEqual(self.delivered, [])
        self.assertFalse(wk.push("too late"))                       # the call is over: the window queues it instead

    def test_push_while_writing_text_becomes_a_second_turn(self):
        wk = self._run(FAKE_TWO_TURNS, push_after=0.2, base=0.0)
        self.assertEqual(self.pushes, [True])
        self.assertEqual([t[0] for t in self.turns], ["r1"]); self.assertAlmostEqual(self.turns[0][1], 0.1)
        self.assertEqual(self.turns[0][2][:3], ("s1", 0.1, True))
        self.assertEqual(self.final[0], "r2"); self.assertAlmostEqual(self.final[2], 0.2)      # priced as the step from the first total
        self.assertEqual(self.delivered, [1]); self.assertAlmostEqual(wk.cost_total, 0.3); self.assertTrue(wk.ok)

    def test_two_pushes_are_taken_in_one_by_one(self):
        script = FAKE_HEAD + (
            "time.sleep(0.6)\nout({'type': 'result', 'result': 'r1', 'session_id': 's1', 'total_cost_usd': 0.1})\n"
            "for i in range(2):\n"
            "    g = wait_line(5)\n"
            "    if isinstance(g, dict): echo(g)\n"
            "out({'type': 'result', 'result': 'r2', 'session_id': 's1', 'total_cost_usd': 0.2})\n"
        )
        wk = self._run(script, push_after=0.2, texts=("one", "two"))
        self.assertEqual(self.pushes, [True, True]); self.assertEqual(self.delivered, [1, 1]); self.assertEqual(wk.undelivered, 0)
        self.assertEqual(self.final[0], "r2")

    def test_a_push_claude_never_answers_is_given_back(self):
        old = worker_mod.GIVE_UP_SECONDS
        worker_mod.GIVE_UP_SECONDS = 0.5
        try:
            wk = self._run(FAKE_DEAF, push_after=0.2)
        finally:
            worker_mod.GIVE_UP_SECONDS = old
        self.assertEqual([t[0] for t in self.turns], ["r1"])         # the first reply was shown at once
        self.assertEqual(self.final[0], ""); self.assertTrue(wk.silent_final)       # nothing more to show
        self.assertEqual(wk.undelivered, 1); self.assertEqual(self.delivered, [])

    def test_stop_during_a_push_wait_ends_the_call(self):
        import threading
        wk = worker_mod.ChatWorker("sonnet", "high", "start", [], None, dict(os.environ), 0.0, claude_bin=make_fake(FAKE_DEAF))
        threading.Timer(0.3, lambda: (wk.push("x"), wk.stop())).start()
        self.assertEqual(wk._run_claude()[0], "(stopped)"); self.assertFalse(wk.ok)
        self.assertFalse(wk.push("after stop"))

    def test_images_of_a_push_are_saved_and_named_in_the_text(self):
        with tempfile.TemporaryDirectory() as d:
            old_env = os.environ.get("XDG_CACHE_HOME")
            os.environ["XDG_CACHE_HOME"] = d
            try:
                text = worker_mod.ChatWorker.with_images("look", [("image/png", b"\x89PNG")])
                saved = list((Path(d) / config.APP_DIR / "images").glob("aichat_*.png"))
            finally:
                if old_env is None:
                    os.environ.pop("XDG_CACHE_HOME", None)
                else:
                    os.environ["XDG_CACHE_HOME"] = old_env
            self.assertEqual(len(saved), 1); self.assertIn(str(saved[0]), text); self.assertTrue(text.startswith("look"))


class Portable(unittest.TestCase):
    """Worker pieces that need no fake program (run on every OS)."""

    def test_command_line_follows_permission_mode_and_folder(self):
        for mode, want, forbid in (("readonly", "--permission-mode", "--dangerously-skip-permissions"),
                                   ("edit", "acceptEdits", "--dangerously-skip-permissions"),
                                   ("full", "--dangerously-skip-permissions", "--permission-mode")):
            wk = worker_mod.ChatWorker("sonnet", "high", "hi", [], "sid9", {}, 0.0, permission=mode, claude_bin="/bin/claude")
            cmd = wk.build_command()
            self.assertIn(want, cmd); self.assertNotIn(forbid, cmd)
            self.assertEqual(cmd[cmd.index("--resume") + 1], "sid9")
            self.assertIn("stream-json", cmd); self.assertIn("--include-partial-messages", cmd)

    def test_missing_claude_gives_install_hint(self):
        orig = config.find_claude
        config.find_claude = lambda configured="": None
        try:
            wk = worker_mod.ChatWorker("sonnet", "high", "hi", [], None, {}, 0.0)
            got = []
            wk.finished_reply.connect(lambda reply, *a: got.append(reply))
            wk.run()
        finally:
            config.find_claude = orig
        self.assertIn("was not found", got[0]); self.assertIn("Install", got[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
