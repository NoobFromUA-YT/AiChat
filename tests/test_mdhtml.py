"""Markdown converter tests (pure Python). Run: tests/run.sh"""
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from aichat_desktop.mdhtml import inline, md_to_html, plain_html  # noqa: E402


class Inline(unittest.TestCase):
    def test_bold_italic_code(self):
        h = inline("**bold** and *it* and _it2_ and `code`")
        self.assertIn("<b>bold</b>", h); self.assertEqual(h.count("<i>"), 2); self.assertIn("&nbsp;code&nbsp;", h)
        self.assertNotIn("**", h)

    def test_snake_case_and_math_untouched(self):
        for s in ("my_var_name and other_thing", "2 * 3 * 4", "a*b*c"):
            self.assertNotIn("<i>", inline(s), s)

    def test_html_is_escaped(self):
        h = inline("<script>alert(1)</script> & <b>x</b>")
        self.assertNotIn("<script>", h); self.assertNotIn("<b>x", h); self.assertIn("&lt;script&gt;", h); self.assertIn("&amp;", h)

    def test_code_is_not_formatted(self):
        h = inline("`**not bold** <tag>`")
        self.assertNotIn("<b>", h); self.assertIn("&lt;tag&gt;", h)

    def test_links(self):
        h = inline("see [docs](https://example.com/a?b=1&c=2) or https://example.org/x.")
        self.assertEqual(h.count("<a "), 2)
        self.assertIn('href="https://example.org/x"', h)       # trailing dot not part of the link
        self.assertTrue(h.endswith("</a>."))


class Blocks(unittest.TestCase):
    def test_heading_and_paragraph_breaks(self):
        h = md_to_html("## Title\nline one\nline two")
        self.assertIn("font-weight:bold", h); self.assertIn("Title", h); self.assertNotIn("##", h)
        self.assertIn("line one<br>line two", h)

    def test_table(self):
        h = md_to_html("| Claim | Actual |\n|---|---|\n| a | **b** |\n| c | d |")
        self.assertEqual(h.count("<tr"), 3); self.assertEqual(h.count("<th"), 2); self.assertIn("<b>b</b>", h)
        self.assertNotIn("|---|", h)

    def test_table_alignment(self):
        h = md_to_html("| a | b |\n|:--|--:|\n| 1 | 2 |")
        self.assertIn('align="left"', h); self.assertIn('align="right"', h)

    def test_fenced_code_is_verbatim(self):
        h = md_to_html("```python\nx = **y** <z>\n  indented\n```\nafter")
        self.assertIn("x = **y** &lt;z&gt;", h); self.assertIn("  indented", h); self.assertIn("after", h)
        self.assertNotIn("<b>y</b>", h)

    def test_unclosed_fence_does_not_crash(self):
        self.assertIn("still code", md_to_html("```\nstill code"))

    def test_nested_lists(self):
        h = md_to_html("- one\n  - inner\n- two\n\n1. first\n2. second")
        self.assertEqual(h.count("<ul"), 2); self.assertEqual(h.count("<ol"), 1)
        self.assertEqual(h.count("<li>"), 5); self.assertEqual(h.count("</li>"), 5)
        self.assertEqual(h.count("</ul>"), 2)

    def test_quote_and_rule(self):
        h = md_to_html("> quoted *text*\n\n---\n\nend")
        self.assertIn("<i>text</i>", h); self.assertIn("<hr", h)

    def test_scale_applies_to_sizes_only(self):
        h = md_to_html("# Big 10px", scale=lambda n: int(n * 2))
        self.assertIn("font-size:42px", h); self.assertIn("Big 10px", h)    # content left alone

    def test_empty_and_whitespace(self):
        self.assertEqual(md_to_html(""), ""); self.assertEqual(md_to_html("  \n\n "), "")

    def test_real_reply_has_no_raw_markdown(self):
        reply = ("## Corrections\n\n| Claim | Actual |\n|---|---|\n| ~70% | **~80%** |\n\n"
                 "- **My test hit the API.** It was `k`\n- second\n\nDone ✅")
        h = md_to_html(reply)
        for raw in ("##", "**", "|---", "`"):
            self.assertNotIn(raw, h, raw)

    def test_plain_user_text(self):
        self.assertEqual(plain_html("a <b>\nc"), "a &lt;b&gt;<br>c")


if __name__ == "__main__":
    unittest.main(verbosity=2)
