"""A compact Sky 15-key score guide and a cursor independent of playback."""

import re

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget

KEY_LABELS = ("Y", "U", "I", "O", "P", "H", "J", "K", "L", ";", "N", "M", ",", ".", "/")
SOLFEGE = ("1", "2", "3", "4", "5", "6", "7", "1·", "2·", "3·", "4·", "5·", "6·", "7·", "1··")


class ScoreCursor:
    def __init__(self):
        self.groups = []
        self.index = 0
        self.held = set()
        self.fresh = set()

    def set_score(self, notes_by_time):
        self.held.clear()
        groups = []
        for time_ms in sorted(notes_by_time):
            keys = set()
            for key in notes_by_time[time_ms]:
                match = re.fullmatch(r"[12]Key(\d+)", str(key))
                if match and 0 <= int(match[1]) < 15:
                    keys.add(int(match[1]))
            if keys:
                groups.append((time_ms, frozenset(keys)))
        self.groups = groups
        self.seek(0)

    @property
    def target(self):
        return self.groups[self.index][1] if self.index < len(self.groups) else frozenset()

    def seek(self, index):
        self.index = max(0, min(int(index), len(self.groups)))
        self.fresh.clear()

    def move(self, step):
        self.seek(self.index + step)

    def feed(self, keys):
        held = set()
        for key in keys:
            match = re.fullmatch(r"[12]Key(\d+)", str(key))
            if match and 0 <= int(match[1]) < 15:
                held.add(int(match[1]))
        self.fresh.intersection_update(held)
        self.fresh.update(held - self.held)
        self.held = held
        if self.target and held == self.target and self.target <= self.fresh:
            self.index += 1
            self.fresh.clear()
            return True
        return False


class FloatingScoreView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.cursor = ScoreCursor()
        self.setMinimumSize(300, 232)
        self.setAccessibleName("光遇悬浮琴谱：亮起的琴键表示当前音符组")
        self.set_theme({})

    def set_theme(self, replacements):
        self.colors = {name: QColor(replacements.get(value, value)) for name, value in {
            "background": "#0C1118", "text": "#F5F7FB", "muted": "#98A3B4",
            "key": "#151C27", "border": "#344054", "highlight": "#5B86FF",
        }.items()}
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        c = self.colors
        painter.setPen(Qt.NoPen)
        painter.setBrush(c["background"])
        painter.drawRoundedRect(QRectF(self.rect()), 6, 6)
        if not self.cursor.groups:
            painter.setPen(c["muted"])
            painter.drawText(self.rect(), Qt.AlignCenter, "选择曲谱后显示 15 键琴谱")
            return
        index, groups = self.cursor.index, self.cursor.groups
        painter.setPen(c["text"])
        font = painter.font()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        title = "演奏完成" if index >= len(groups) else f"第 {index + 1} / {len(groups)} 组"
        painter.drawText(QRectF(10, 5, self.width() - 20, 24), Qt.AlignLeft | Qt.AlignVCenter, title)
        if index < len(groups):
            painter.setPen(c["muted"])
            seconds = groups[index][0] / 1000
            painter.drawText(QRectF(10, 5, self.width() - 20, 24), Qt.AlignRight | Qt.AlignVCenter,
                             f"{int(seconds // 60)}:{int(seconds % 60):02d}")
        width = min(self.width() - 24, 330)
        left = (self.width() - width) / 2
        diameter = min(38, (width - 4 * 8) / 5)
        for key in range(15):
            x = left + (key % 5) * width / 5 + (width / 5 - diameter) / 2
            y = 34 + (key // 5) * 44
            active = key in self.cursor.target
            painter.setBrush(c["highlight"] if active else c["key"])
            painter.setPen(QPen(c["highlight"] if active else c["border"], 1.5))
            rect = QRectF(x, y, diameter, diameter)
            painter.drawEllipse(rect)
            painter.setPen(c["background"] if active else c["text"])
            painter.drawText(rect, Qt.AlignCenter, KEY_LABELS[key])
        painter.setPen(c["muted"])
        font.setBold(False)
        font.setPointSize(8)
        painter.setFont(font)
        painter.drawText(QRectF(10, 168, self.width() - 20, 18), Qt.AlignLeft, "接下来")
        cell_width = (self.width() - 20) / 4
        for offset in range(1, 5):
            pos = index + offset
            rect = QRectF(10 + (offset - 1) * cell_width, 188, cell_width - 5, 36)
            painter.setPen(QPen(c["border"], 1))
            painter.setBrush(c["key"])
            painter.drawRoundedRect(rect, 4, 4)
            painter.setPen(c["text"])
            text = "—" if pos >= len(groups) else " ".join(KEY_LABELS[k] for k in sorted(groups[pos][1]))
            painter.drawText(rect.adjusted(3, 2, -3, -2), Qt.AlignCenter | Qt.TextWordWrap, text)
        self.setToolTip("当前组：" + " + ".join(f"{KEY_LABELS[k]}（{SOLFEGE[k]}）" for k in sorted(self.cursor.target)))
