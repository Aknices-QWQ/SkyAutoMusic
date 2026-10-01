"""A compact editor for estimated song sections (times use original score)."""

from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QPushButton, QVBoxLayout,
)

from song_sections import estimate_sections, note_groups, score_end_ms, validate_sections


def time_text(milliseconds):
    minutes, seconds = divmod(milliseconds / 1000, 60)
    return f"{int(minutes)}:{seconds:06.3f}"


class SectionEditor(QDialog):
    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.rows = []
        self.selected = -1
        self.loading = False
        self.setWindowTitle("歌曲段落")
        self.resize(660, 550)
        layout = QVBoxLayout(self)
        self.title = QLabel()
        self.title.setObjectName("CardTitle")
        layout.addWidget(self.title)
        hint = QLabel("自动结果按旋律、和弦与重复结构估计；A/B 代表相似材料，高潮候选需自行确认。\n起止时间使用原始谱子的时间，不随播放速度变化。结束时间不包含下一段音符。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.list = QListWidget()
        self.list.setAccessibleName("歌曲段落列表")
        self.list.currentRowChanged.connect(self.choose_row)
        layout.addWidget(self.list, 1)
        form = QFormLayout()
        self.name = QLineEdit()
        self.start, self.end = QDoubleSpinBox(), QDoubleSpinBox()
        for spin in (self.start, self.end):
            spin.setDecimals(3)
            spin.setSingleStep(.5)
            spin.setSuffix(" 秒")
        form.addRow("段落名称", self.name)
        form.addRow("开始时间", self.start)
        form.addRow("结束时间", self.end)
        layout.addLayout(form)
        self.name.textEdited.connect(self.edit_row)
        self.start.valueChanged.connect(self.edit_row)
        self.end.valueChanged.connect(self.edit_row)
        edit_buttons = QHBoxLayout()
        for text, callback in (("新增段落", self.add_row), ("删除段落", self.delete_row),
                               ("重新识别", self.reidentify), ("保存修改", self.apply)):
            button = QPushButton(text)
            button.clicked.connect(callback)
            edit_buttons.addWidget(button)
        layout.addLayout(edit_buttons)
        options = QHBoxLayout()
        self.loop = QCheckBox("循环本段")
        self.immediate = QCheckBox("演奏时跳过倒计时")
        options.addWidget(self.loop)
        options.addWidget(self.immediate)
        layout.addLayout(options)
        play_buttons = QHBoxLayout()
        for text, callback in (("试听本段", lambda: self.play(True)),
                               ("演奏本段", lambda: self.play(False)),
                               ("停止", host.stop_play), ("关闭", self.close)):
            button = QPushButton(text)
            button.clicked.connect(callback)
            play_buttons.addWidget(button)
        layout.addLayout(play_buttons)
        self.message = QLabel()
        self.message.setWordWrap(True)
        layout.addWidget(self.message)

    def set_song(self):
        self.loading = True
        self.title.setText(self.host.name_label.text())
        self.groups = note_groups(self.host.current_score_notes)
        self.rows = [section.to_dict() for section in self.host.song_sections]
        self.start.setMaximum(score_end_ms(self.groups) / 1000)
        self.end.setMaximum(score_end_ms(self.groups) / 1000)
        self.loading = False
        self.message.setText("修改后点击保存；试听或演奏本段也会保存当前修改。")
        self.rebuild()

    def label(self, row):
        suffix = " · 估计" if row.get("estimated") else " · 手动"
        return f"{row['name']}   {time_text(row['start_ms'])} — {time_text(row['end_ms'])}{suffix}"

    def rebuild(self, index=0):
        self.list.blockSignals(True)
        self.list.clear()
        self.list.addItems([self.label(row) for row in self.rows])
        self.list.blockSignals(False)
        self.selected = -1
        self.list.setCurrentRow(min(index, len(self.rows)-1))
        self.choose_row(self.list.currentRow())

    def choose_row(self, index):
        self.selected = index
        self.loading = True
        for control in (self.name, self.start, self.end):
            control.setEnabled(index >= 0)
        if 0 <= index < len(self.rows):
            row = self.rows[index]
            self.name.setText(row["name"])
            self.start.setValue(row["start_ms"] / 1000)
            self.end.setValue(row["end_ms"] / 1000)
        self.loading = False

    def edit_row(self, *_):
        if self.loading or not 0 <= self.selected < len(self.rows):
            return
        row = self.rows[self.selected]
        row.update(name=self.name.text(), start_ms=round(self.start.value()*1000),
                   end_ms=round(self.end.value()*1000), estimated=False)
        self.list.item(self.selected).setText(self.label(row))

    def add_row(self):
        # Prefer uncovered material; otherwise create a draft for manual adjustment.
        last = max((row["end_ms"] for row in self.rows), default=0)
        limit = score_end_ms(self.groups)
        self.rows.append({"name": "新段落", "start_ms": last if last < limit else 0,
                          "end_ms": limit, "estimated": False})
        self.rebuild(len(self.rows)-1)

    def delete_row(self):
        if 0 <= self.selected < len(self.rows):
            del self.rows[self.selected]
            self.rebuild(self.selected)

    def reidentify(self):
        self.rows = [section.to_dict() for section in estimate_sections(self.host.current_score_notes)]
        self.rebuild()
        self.message.setText("已重新估计。点击保存修改以替换原来的段落。")

    def apply(self):
        try:
            sections = validate_sections(self.rows, self.groups)
        except ValueError as error:
            self.message.setText(str(error))
            return False
        if sections != self.host.song_sections:
            self.host.store_song_sections(sections)
        self.message.setText("段落已保存到本机，原始曲谱未修改。")
        return True

    def play(self, preview):
        if not 0 <= self.selected < len(self.rows):
            self.message.setText("请先选择一个段落")
            return
        chosen = self.rows[self.selected].copy()
        if not self.apply():
            return
        section = next(section for section in self.host.song_sections
                       if section.start_ms == chosen["start_ms"] and section.end_ms == chosen["end_ms"])
        self.host.play_section(section, preview=preview, loop=self.loop.isChecked(),
                               immediate=self.immediate.isChecked())
