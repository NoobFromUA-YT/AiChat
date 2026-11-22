#!/usr/bin/env python3
"""Draw the app's icon (original artwork: a speech bubble with three dots on a blue-violet tile) and write
aichat_desktop/assets/icon.svg + icon.png (256 px) + icon-<n>.png + hicolor sizes. Run: QT_QPA_PLATFORM=offscreen python3 tools/make_icon.py"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "aichat_desktop" / "assets"
SIZES = (16, 32, 48, 64, 128, 256, 512)
TOP, BOTTOM = "#3b82f6", "#7c3aed"          # tile gradient
# geometry on a 512 grid
TILE = (32, 32, 448, 448, 104)               # x, y, w, h, corner radius
BUBBLE = (104, 128, 304, 200, 56)            # rounded body
TAIL = ((168, 316), (168, 392), (244, 328))  # triangle under the body
DOTS = ((188, 228), (256, 228), (324, 228))
DOT_R = 20


def svg() -> str:
    tx, ty, tw, th, tr = TILE
    bx, by, bw, bh, br = BUBBLE
    tail = " ".join(f"{x},{y}" for x, y in TAIL)
    dots = "".join(f'<circle cx="{x}" cy="{y}" r="{DOT_R}" fill="{TOP}"/>' for x, y in DOTS)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">'
            f'<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{TOP}"/>'
            f'<stop offset="1" stop-color="{BOTTOM}"/></linearGradient></defs>'
            f'<rect x="{tx}" y="{ty}" width="{tw}" height="{th}" rx="{tr}" fill="url(#g)"/>'
            f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="{br}" fill="#fff"/>'
            f'<polygon points="{tail}" fill="#fff"/>{dots}</svg>\n')


def render() -> QImage:
    img = QImage(512, 512, QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
    p.setPen(Qt.PenStyle.NoPen)
    grad = QLinearGradient(0, TILE[1], 0, TILE[1] + TILE[3])
    grad.setColorAt(0, QColor(TOP)); grad.setColorAt(1, QColor(BOTTOM))
    p.setBrush(grad)
    p.drawRoundedRect(QRectF(*TILE[:4]), TILE[4], TILE[4])
    p.setBrush(QColor("#ffffff"))
    p.drawRoundedRect(QRectF(*BUBBLE[:4]), BUBBLE[4], BUBBLE[4])
    tail = QPainterPath()
    tail.moveTo(QPointF(*TAIL[0])); tail.lineTo(QPointF(*TAIL[1])); tail.lineTo(QPointF(*TAIL[2])); tail.closeSubpath()
    p.drawPath(tail)
    p.setBrush(QColor(TOP))
    for x, y in DOTS:
        p.drawEllipse(QPointF(x, y), DOT_R, DOT_R)
    p.end()
    return img


def main() -> int:
    QGuiApplication(sys.argv[:1])
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "icon.svg").write_text(svg(), encoding="utf-8")
    big = render()
    for n in SIZES:
        img = big if n == 512 else big.scaled(n, n, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
        path = OUT / ("icon.png" if n == 256 else f"icon-{n}.png")
        img.save(str(path))
    print("wrote", ", ".join(sorted(p.name for p in OUT.iterdir())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
