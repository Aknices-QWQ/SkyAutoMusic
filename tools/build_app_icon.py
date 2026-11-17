"""Render the hand-authored SVG app mark as a PNG preview and Windows ICO."""

from __future__ import annotations

import struct
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assets" / "app-icon.svg"
PREVIEW = ROOT / "assets" / "app-icon.png"
OUTPUT = ROOT / "assets" / "app-icon.ico"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def render_png(renderer: QSvgRenderer, size: int) -> bytes:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise RuntimeError("Could not open in-memory PNG buffer")
    if not image.save(buffer, "PNG"):
        raise RuntimeError("Could not render app icon PNG")
    buffer.close()
    return bytes(data)


def build_icon() -> None:
    renderer = QSvgRenderer(str(SOURCE))
    if not renderer.isValid():
        raise ValueError(f"Invalid SVG icon: {SOURCE}")
    PREVIEW.write_bytes(render_png(renderer, 512))
    images = [(size, render_png(renderer, size)) for size in ICON_SIZES]

    directory = bytearray(struct.pack("<HHH", 0, 1, len(images)))
    payload = bytearray()
    offset = 6 + 16 * len(images)
    for size, png_data in images:
        directory.extend(struct.pack(
            "<BBBBHHII",
            0 if size == 256 else size,
            0 if size == 256 else size,
            0,
            0,
            1,
            32,
            len(png_data),
            offset,
        ))
        payload.extend(png_data)
        offset += len(png_data)
    OUTPUT.write_bytes(directory + payload)
    print(f"Wrote {PREVIEW} and {OUTPUT}")


if __name__ == "__main__":
    build_icon()
