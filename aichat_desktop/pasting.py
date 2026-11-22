"""What the message box takes from the clipboard: plain text only. No Qt widgets, so it is easy to test.

Web pages, Word and chat apps put HTML next to the text (fonts, colours, backgrounds, sizes). The box ignores that and
also removes the invisible characters those sources add, so Claude gets the words and nothing else.
"""

from __future__ import annotations

import re

from PyQt6.QtCore import QMimeData
from PyQt6.QtGui import QTextDocument

# no-break and figure spaces become normal spaces; zero-width, soft-hyphen, BOM and direction marks vanish
_SPACES = dict.fromkeys(map(ord, "   "), " ")
_INVISIBLE = dict.fromkeys(map(ord, "​⁠﻿­‎‏‪‫‬‭‮⁦⁧⁨⁩"), None)
_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(text: str) -> str:
    """Normalise pasted text: Unix newlines, ordinary spaces, no invisible or control characters."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(" ", "\n").replace(" ", "\n\n")
    text = text.translate(_SPACES).translate(_INVISIBLE)
    return _CONTROLS.sub("", text)


def html_to_text(markup: str) -> str:
    doc = QTextDocument()
    doc.setHtml(markup)
    return doc.toPlainText()


def plain_text_of(md: QMimeData) -> str:
    """The text of a clipboard / drop, without any formatting ('' when it holds none)."""
    real_text = "text/plain" in md.formats()                  # Qt's text() also turns a list of files into file:// lines
    text = md.text() if real_text else ""
    if not text.strip() and md.hasHtml() and not md.hasImage():
        text = html_to_text(md.html())
    if not text.strip() and md.hasUrls():
        text = "\n".join(u.toLocalFile() if u.isLocalFile() else u.toString() for u in md.urls())
    return clean_text(text)
