"""Small Markdown -> Qt rich-text HTML converter for the chat view (no Qt imports, unit-testable).

Qt's QTextBrowser understands only a subset of HTML, so this emits plain tables / spans with inline styles.
Sizes are passed through `scale` (logical px -> device px) because the window draws hard-pixel text at 2x.
Everything from the model is HTML-escaped first; nothing in `text` can inject markup.
"""

from __future__ import annotations

import html
import re
from typing import Callable

CODE_BG = "#eef0f3"
BORDER = "#c9ced8"
HEAD_BG = "#e9ecf1"
MUTED = "#5f6672"
LINK = "#0b62c4"
MONO = "'DejaVu Sans Mono', monospace"

def set_palette(code_bg: str, border: str, head_bg: str, muted: str, link: str) -> None:
    """Colours of code blocks, table borders, table heads, quotes and links (the window calls this when the theme changes)."""
    global CODE_BG, BORDER, HEAD_BG, MUTED, LINK
    CODE_BG, BORDER, HEAD_BG, MUTED, LINK = code_bg, border, head_bg, muted, link


_FENCE = re.compile(r"^\s*(```|~~~)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_HR = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_LIST = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_QUOTE = re.compile(r"^\s*>\s?(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def plain_html(text: str) -> str:
    """User text: escaped, line breaks kept, no markdown."""
    return html.escape(text, quote=False).replace("\n", "<br>")


def inline(s: str) -> str:
    """Inline markdown (code, bold, italic, links) of one already-split piece of text."""
    s = html.escape(s, quote=False)
    codes: list[str] = []

    def stash(m: re.Match) -> str:
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    s = re.sub(r"`([^`\n]+)`", stash, s)
    s = re.sub(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])", r"<i>\1</i>", s)
    s = re.sub(r"(?<!\w)_(?=[^\s_])(.+?)(?<=[^\s_])_(?!\w)", r"<i>\1</i>", s)
    s = re.sub(
        r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)",
        lambda m: f'<a href="{m.group(2)}" style="color:{LINK}">{m.group(1)}</a>',
        s,
    )

    def bare(m: re.Match) -> str:
        url = m.group(0)
        tail = ""
        while url and url[-1] in ".,;:!?)":
            tail = url[-1] + tail
            url = url[:-1]
        return f'<a href="{url}" style="color:{LINK}">{url}</a>{tail}'

    s = re.sub(r'(?<![\w"=>/])https?://[^\s<]+', bare, s)
    return re.sub(
        "\x00(\\d+)\x00",
        lambda m: f'<span style="font-family:{MONO};background-color:{CODE_BG}">&nbsp;{codes[int(m.group(1))]}&nbsp;</span>',
        s,
    )


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    return [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line)]


def _aligns(sep: str) -> list[str]:
    out = []
    for c in _split_row(sep):
        if c.startswith(":") and c.endswith(":"):
            out.append("center")
        elif c.endswith(":"):
            out.append("right")
        else:
            out.append("left")
    return out


def md_to_html(text: str, scale: Callable[[float], int] = lambda n: int(n), code_blocks: list[str] | None = None) -> str:
    """Markdown text -> HTML for QTextBrowser. `scale` turns logical px into device px.
    With `code_blocks` (a list the caller owns) every fenced code block gets a "Copy" link `aichat-copy:<index>` and its
    raw text is appended to the list at that index."""
    px = lambda n: f"{scale(n)}px"  # noqa: E731
    gap = f'<p style="margin:0;font-size:{px(5)}">&nbsp;</p>'
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    i, n = 0, len(lines)

    def starts_block(k: int) -> bool:
        ln = lines[k]
        if not ln.strip():
            return True
        if _FENCE.match(ln) or _HEADING.match(ln) or _HR.match(ln) or _LIST.match(ln) or _QUOTE.match(ln):
            return True
        return "|" in ln and k + 1 < n and bool(_TABLE_SEP.match(lines[k + 1])) and "|" in lines[k + 1]

    while i < n:
        ln = lines[i]
        if not ln.strip():
            i += 1
            continue

        if _FENCE.match(ln):  # fenced code block: shown verbatim
            fence = _FENCE.match(ln).group(1)
            i += 1
            code: list[str] = []
            while i < n and not lines[i].strip().startswith(fence):
                code.append(lines[i])
                i += 1
            i += 1  # closing fence (or end of text)
            body = html.escape("\n".join(code), quote=False)
            copy_row = ""
            if code_blocks is not None:
                code_blocks.append("\n".join(code))
                copy_row = (f'<p align="right" style="margin:0 0 {px(4)} 0;font-size:{px(12)}">'
                            f'<a href="aichat-copy:{len(code_blocks) - 1}" style="color:{LINK}">Copy</a></p>')
            out.append(
                f'<table width="100%" cellspacing="0" cellpadding="{scale(9)}"><tr>'
                f'<td bgcolor="{CODE_BG}">{copy_row}<pre style="margin:0;white-space:pre-wrap;font-family:{MONO};'
                f'font-size:{px(13)}">{body}</pre></td></tr></table>{gap}'
            )
            continue

        m = _HEADING.match(ln)
        if m:
            size = {1: 21, 2: 18.5, 3: 16.5}.get(len(m.group(1)), 15)
            out.append(
                f'<p style="margin:{px(8)} 0 {px(3)} 0;font-size:{px(size)};font-weight:bold">{inline(m.group(2))}</p>'
            )
            i += 1
            continue

        if _HR.match(ln):
            out.append(f'<hr style="color:{BORDER}">')
            i += 1
            continue

        if "|" in ln and i + 1 < n and "|" in lines[i + 1] and _TABLE_SEP.match(lines[i + 1]):
            head = _split_row(ln)
            aligns = _aligns(lines[i + 1])
            i += 2
            rows: list[list[str]] = []
            while i < n and lines[i].strip() and "|" in lines[i]:
                rows.append(_split_row(lines[i]))
                i += 1
            ncol = len(head)

            def cell(tag: str, val: str, col: int) -> str:
                al = aligns[col] if col < len(aligns) else "left"
                inner = inline(val)
                return f'<{tag} align="{al}" style="padding:{px(2)}">{"<b>" + inner + "</b>" if tag == "th" else inner}</{tag}>'

            t = [
                f'<table width="100%" border="1" cellspacing="0" cellpadding="{scale(6)}" '
                f'style="border-color:{BORDER};border-style:solid">'
            ]
            t.append(f'<tr bgcolor="{HEAD_BG}">' + "".join(cell("th", c, k) for k, c in enumerate(head)) + "</tr>")
            for r in rows:
                r = (r + [""] * ncol)[:ncol]
                t.append("<tr>" + "".join(cell("td", c, k) for k, c in enumerate(r)) + "</tr>")
            t.append("</table>")
            out.append("".join(t) + gap)
            continue

        if _QUOTE.match(ln):
            quote: list[str] = []
            while i < n and _QUOTE.match(lines[i]):
                quote.append(_QUOTE.match(lines[i]).group(1))
                i += 1
            out.append(
                f'<table width="100%" cellspacing="0" cellpadding="0"><tr><td width="{scale(3)}" bgcolor="{BORDER}"></td>'
                f'<td style="padding-left:{px(10)};color:{MUTED}">{"<br>".join(inline(q) for q in quote)}</td></tr></table>{gap}'
            )
            continue

        if _LIST.match(ln):
            stack: list[tuple[int, str]] = []  # (indent, "ul" | "ol")
            parts: list[str] = []
            while i < n:
                lm = _LIST.match(lines[i])
                if lm:
                    indent = len(lm.group(1).expandtabs(4))
                    kind = "ol" if lm.group(2)[0].isdigit() else "ul"
                    while stack and indent < stack[-1][0]:
                        parts.append(f"</li></{stack.pop()[1]}>")
                    if stack and indent == stack[-1][0]:
                        parts.append("</li>")
                        if stack[-1][1] != kind:
                            parts.append(f"</{stack.pop()[1]}>")
                    if not stack or indent > stack[-1][0]:
                        parts.append(f'<{kind} style="margin-top:{px(2)};margin-bottom:{px(2)}">')
                        stack.append((indent, kind))
                    parts.append(f"<li>{inline(lm.group(3))}")
                    i += 1
                elif lines[i].strip() and lines[i].startswith((" ", "\t")) and stack:
                    parts.append("<br>" + inline(lines[i].strip()))  # continuation of the current item
                    i += 1
                elif not lines[i].strip() and i + 1 < n and _LIST.match(lines[i + 1]):
                    i += 1  # blank line inside a loose list
                else:
                    break
            while stack:
                parts.append(f"</li></{stack.pop()[1]}>")
            out.append("".join(parts) + gap)
            continue

        para = [ln]
        i += 1
        while i < n and not starts_block(i):
            para.append(lines[i])
            i += 1
        out.append(f'<p style="margin:0">{"<br>".join(inline(p) for p in para)}</p>{gap}')

    return "".join(out)
