"""Simple editor for creating and previewing SkyAutoMusic scores."""

import json
import re
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)
from midi_keyboard import MAJOR_STEPS, WindowsMidi, note_name


SKY_KEYS = [f"1Key{index}" for index in range(15)]
KEY_LABELS = ["Do", "Re", "Mi", "Fa", "Sol", "La", "Ti", "Do+", "Re+", "Mi+", "Fa+", "Sol+", "La+", "Ti+", "Do++"]
KEYBOARD_LABELS = ["Y", "U", "I", "O", "P", "H", "J", "K", "L", ";", "N", "M", ",", ".", "/"]


class ScoreEditorPage(QWidget):
    midi_connecting = Signal()

    def __init__(self, score_dir, preview_note, saved_callback=None, parent=None, midi_backend=None):
        super().__init__(parent)
        self.setObjectName("ScoreEditorPage")
        self.score_dir = Path(score_dir)
        self.preview_note = preview_note
        self.saved_callback = saved_callback
        self.events = []
        self.midi_backend = midi_backend if midi_backend is not None else WindowsMidi()
        self.connected_device = None
        self.device_snapshot = None
        self.held_notes = set()
        self.input_chord = set()
        self.preview_events = []
        self.preview_index = 0
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self._preview_tick)
        self._build_ui()
        self.on_input_mode_changed()
        self._render_events()
        self.midi_timer = QTimer(self)
        self.midi_timer.setInterval(10)
        self.midi_timer.timeout.connect(self.poll_midi)
        self.midi_timer.start()
        self.device_timer = QTimer(self)
        self.device_timer.setInterval(2000)
        self.device_timer.timeout.connect(self.refresh_midi_devices)
        self.device_timer.start()
        QApplication.instance().installEventFilter(self)
        self.refresh_midi_devices()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 18, 24, 14)
        root.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("制谱")
        title.setObjectName("BrandTitle")
        header.addWidget(title)
        header.addStretch()
        self.status_label = QLabel("新建曲谱")
        self.status_label.setObjectName("MutedText")
        header.addWidget(self.status_label)
        root.addLayout(header)

        meta = QHBoxLayout()
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("曲谱名称")
        self.title_input.setAccessibleName("曲谱名称")
        self.author_input = QLineEdit()
        self.author_input.setPlaceholderText("作者（可选）")
        self.author_input.setAccessibleName("作者")
        self.transcriber_input = QLineEdit()
        self.transcriber_input.setPlaceholderText("制谱者（可选）")
        self.transcriber_input.setAccessibleName("制谱者")
        self.tempo = QSpinBox()
        self.tempo.setRange(20, 300)
        self.tempo.setValue(120)
        self.tempo.setSuffix(" BPM")
        self.tempo.setAccessibleName("曲谱速度")
        meta.addWidget(self.title_input, 2)
        meta.addWidget(self.author_input, 1)
        meta.addWidget(self.transcriber_input, 1)
        meta.addWidget(self.tempo)
        root.addLayout(meta)

        sound_row = QHBoxLayout()
        self.sound_combo = QComboBox()
        self.sound_combo.setAccessibleName("试听音色")
        self.sound_combo.setMinimumWidth(185)
        self.sound_download_button = QPushButton("下载所选音色")
        self.sound_import_button = QPushButton("选择本地音色")
        self.sound_source_button = QPushButton("查看来源")
        self.sound_status = QLabel("合成音")
        self.sound_status.setObjectName("MutedText")
        self.sound_status.setWordWrap(True)
        sound_row.addWidget(QLabel("试听音色"))
        sound_row.addWidget(self.sound_combo)
        sound_row.addWidget(self.sound_download_button)
        sound_row.addWidget(self.sound_import_button)
        sound_row.addWidget(self.sound_source_button)
        sound_row.addWidget(self.sound_status, 1)
        root.addLayout(sound_row)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("录入模式"))
        self.input_mode = QComboBox()
        self.input_mode.addItem("琴键模式", "piano")
        self.input_mode.addItem("分离录制", "separate")
        self.input_mode.setAccessibleName("制谱录入模式")
        self.input_mode.setToolTip("分离录制可逐个选择和弦音；切换模式会清空尚未写入的音高。")
        mode_row.addWidget(self.input_mode)
        self.mode_hint = QLabel()
        self.mode_hint.setObjectName("MutedText")
        self.mode_hint.setWordWrap(True)
        mode_row.addWidget(self.mode_hint, 1)
        self.commit_button = QPushButton("写入本组 · 空格")
        self.commit_button.setObjectName("PrimaryButton")
        self.commit_button.clicked.connect(self.add_event)
        mode_row.addWidget(self.commit_button)
        root.addLayout(mode_row)

        body_content = QWidget()
        body_content.setObjectName("ScoreEditorBody")
        body_content.setMinimumHeight(380)
        body = QHBoxLayout(body_content)
        body.setContentsMargins(0, 0, 8, 0)
        body.setSpacing(12)
        editor = QFrame()
        editor.setObjectName("Card")
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(14, 12, 14, 12)
        editor_layout.setSpacing(9)
        note_header = QHBoxLayout()
        note_title = QLabel("光遇 15 音")
        note_title.setObjectName("CardTitle")
        self.position = QSpinBox()
        self.position.setRange(0, 600000)
        self.position.setSingleStep(125)
        self.position.setSuffix(" ms")
        self.position.setAccessibleName("音符时间")
        self.position.setFixedWidth(120)
        self.step_size = QComboBox()
        self.step_size.addItem("四分音符", 1.0)
        self.step_size.addItem("八分音符", 0.5)
        self.step_size.addItem("十六分音符", 0.25)
        self.step_size.setAccessibleName("添加音符后的步进")
        self.step_size.setFixedWidth(110)
        note_header.addWidget(note_title)
        note_header.addStretch()
        note_header.addWidget(QLabel("位置"))
        note_header.addWidget(self.position)
        note_header.addWidget(self.step_size)
        editor_layout.addLayout(note_header)

        self.note_buttons = []
        keyboard = QGridLayout()
        keyboard.setHorizontalSpacing(7)
        keyboard.setVerticalSpacing(7)
        for index, (key, label, physical) in enumerate(zip(SKY_KEYS, KEY_LABELS, KEYBOARD_LABELS)):
            button = QPushButton(f"{label}\n{physical}")
            button.setObjectName("SkyNoteButton")
            button.setCheckable(True)
            button.setAccessibleName(f"{label}，键盘 {physical}")
            button.setToolTip(f"{label} · {key} · 键盘 {physical}")
            button.setFixedSize(66, 66)
            button.clicked.connect(lambda _checked, note=index: self.on_note_clicked(note))
            keyboard.addWidget(button, index // 5, index % 5, Qt.AlignCenter)
            self.note_buttons.append(button)
        for column in range(5):
            keyboard.setColumnStretch(column, 1)
        editor_layout.addLayout(keyboard)

        midi_row = QHBoxLayout()
        self.midi_devices = QComboBox()
        self.midi_devices.setAccessibleName("制谱 MIDI 输入设备")
        self.midi_devices.setMinimumWidth(100)
        self.midi_refresh_button = QPushButton("刷新")
        self.midi_refresh_button.clicked.connect(self.refresh_midi_devices)
        self.midi_connect_button = QPushButton("连接 MIDI")
        self.midi_connect_button.clicked.connect(self.toggle_midi_connection)
        self.midi_base = QComboBox()
        self.midi_base.setAccessibleName("制谱 MIDI 音域")
        for base in (36, 48, 60, 72, 84):
            self.midi_base.addItem(f"{note_name(base)}–{note_name(base + 24)}", base)
        self.midi_base.setCurrentIndex(2)
        midi_row.addWidget(self.midi_devices, 1)
        midi_row.addWidget(self.midi_refresh_button)
        midi_row.addWidget(self.midi_connect_button)
        midi_row.addWidget(self.midi_base)
        self.midi_status = QLabel("电脑键盘与 MIDI 可直接录入音符组")
        self.midi_status.setObjectName("MutedText")

        controls = QHBoxLayout()
        self.add_button = QPushButton("添加音符组")
        self.add_button.setObjectName("PrimaryButton")
        self.add_button.clicked.connect(self.add_event)
        self.apply_button = QPushButton("更新选中组")
        self.apply_button.clicked.connect(self.update_event)
        self.delete_button = QPushButton("删除")
        self.delete_button.clicked.connect(self.delete_event)
        self.clear_selection = QPushButton("清空音高")
        self.clear_selection.clicked.connect(self.clear_note_selection)
        controls.addWidget(self.add_button)
        controls.addWidget(self.apply_button)
        controls.addWidget(self.delete_button)
        controls.addStretch()
        controls.addWidget(self.clear_selection)
        editor_layout.addLayout(controls)
        editor_layout.addStretch(1)
        body.addWidget(editor, 1)

        score = QFrame()
        score.setObjectName("Card")
        score_layout = QVBoxLayout(score)
        score_layout.setContentsMargins(12, 12, 12, 12)
        score_layout.setSpacing(8)
        score_title_row = QHBoxLayout()
        score_title = QLabel("时间轴")
        score_title.setObjectName("CardTitle")
        self.count_label = QLabel("0 组 · 0 音")
        self.count_label.setObjectName("MutedText")
        score_title_row.addWidget(score_title)
        score_title_row.addStretch()
        score_title_row.addWidget(self.count_label)
        score_layout.addLayout(score_title_row)
        score_layout.addLayout(midi_row)
        score_layout.addWidget(self.midi_status)
        self.event_table = QTableWidget(0, 3)
        self.event_table.setHorizontalHeaderLabels(["#", "位置", "音符组"])
        self.event_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.event_table.setSelectionMode(QTableWidget.SingleSelection)
        self.event_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.event_table.verticalHeader().setVisible(False)
        self.event_table.horizontalHeader().setStretchLastSection(True)
        self.event_table.setColumnWidth(0, 42)
        self.event_table.setColumnWidth(1, 92)
        self.event_table.itemSelectionChanged.connect(self._load_selected_event)
        score_layout.addWidget(self.event_table, 1)
        body.addWidget(score, 1)
        body_scroll = QScrollArea()
        body_scroll.setObjectName("ScoreEditorScroll")
        body_scroll.setWidgetResizable(True)
        body_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body_scroll.setFrameShape(QFrame.NoFrame)
        body_scroll.setWidget(body_content)
        root.addWidget(body_scroll, 1)

        footer = QHBoxLayout()
        self.open_button = QPushButton("打开 JSON")
        self.open_button.clicked.connect(self.open_score)
        self.preview_button = QPushButton("试听")
        self.preview_button.clicked.connect(self.toggle_preview)
        self.save_button = QPushButton("保存到曲库")
        self.save_button.setObjectName("PrimaryButton")
        self.save_button.clicked.connect(self.save_score)
        self.export_button = QPushButton("另存为…")
        self.export_button.clicked.connect(self.export_score)
        footer.addWidget(self.open_button)
        footer.addWidget(self.preview_button)
        footer.addStretch()
        footer.addWidget(self.export_button)
        footer.addWidget(self.save_button)
        root.addLayout(footer)
        self.input_mode.currentIndexChanged.connect(self.on_input_mode_changed)

    def separate_recording(self):
        return self.input_mode.currentData() == "separate"

    def reset_live_input(self):
        self.held_notes.clear()
        self.input_chord.clear()
        for button in self.note_buttons:
            button.setDown(False)

    def on_input_mode_changed(self, _index=None):
        self.reset_live_input()
        self.clear_note_selection()
        separate = self.separate_recording()
        self.commit_button.setVisible(separate)
        self.add_button.setText("写入本组" if separate else "添加音符组")
        self.mode_hint.setText(
            "逐个选音，空格写入；再按同一音可取消。" if separate
            else "按住和弦，全部松开后自动写入。"
        )
        self.status_label.setText("分离录制" if separate else "琴键模式")
        if self.isVisible():
            self.note_buttons[0].setFocus(Qt.OtherFocusReason)

    def on_note_clicked(self, index):
        if not self.separate_recording() or self.note_buttons[index].isChecked():
            self.preview_note([SKY_KEYS[index]])
        if self.separate_recording():
            self.show_pending_chord()

    def show_pending_chord(self):
        labels = [KEY_LABELS[i] for i, button in enumerate(self.note_buttons) if button.isChecked()]
        self.status_label.setText(f"本组：{len(labels)} 个音" if labels else "本组尚未选音")
        self.status_label.setToolTip(" + ".join(labels))

    def selected_keys(self):
        return [key for key, button in zip(SKY_KEYS, self.note_buttons) if button.isChecked()]

    def set_selected_keys(self, keys):
        selected = set(keys)
        for key, button in zip(SKY_KEYS, self.note_buttons):
            button.setChecked(key in selected)

    def clear_note_selection(self):
        self.set_selected_keys([])
        self.input_chord.clear()

    def _step_ms(self):
        return max(1, round(60000 / self.tempo.value() * float(self.step_size.currentData())))

    def add_event(self):
        self.append_event(self.selected_keys())

    def append_event(self, keys):
        if not keys:
            self.status_label.setText("先选择一个或多个音高")
            return
        keys = [key for key in SKY_KEYS if key in keys]
        self.events.append({"time": self.position.value(), "keys": keys})
        self.events.sort(key=lambda event: event["time"])
        self.position.setValue(min(600000, self.position.value() + self._step_ms()))
        self.set_selected_keys([])
        self._render_events()
        self.status_label.setText("已添加音符组")

    def eventFilter(self, watched, event):
        if not self.isVisible() or event.type() not in (QEvent.KeyPress, QEvent.KeyRelease):
            return False
        if QApplication.activeModalWidget():
            return False
        focus = QApplication.focusWidget()
        if isinstance(watched, (QLineEdit, QSpinBox, QComboBox)) or isinstance(focus, (QLineEdit, QSpinBox, QComboBox)):
            return False
        if event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            return False
        if self.separate_recording() and event.key() == Qt.Key_Space:
            if event.type() == QEvent.KeyPress and not event.isAutoRepeat():
                self.add_event()
            return True
        key_text = event.text().upper()
        if key_text not in KEYBOARD_LABELS:
            return False
        if event.isAutoRepeat():
            return True
        index = KEYBOARD_LABELS.index(key_text)
        self.feed_input_note(index, event.type() == QEvent.KeyPress, ("keyboard", index))
        return True

    def feed_input_note(self, index, pressed, identity):
        if pressed:
            if identity in self.held_notes:
                return
            self.held_notes.add(identity)
            if self.separate_recording():
                button = self.note_buttons[index]
                button.setChecked(not button.isChecked())
                button.setDown(True)
                if button.isChecked():
                    self.preview_note([SKY_KEYS[index]])
                self.show_pending_chord()
                return
            self.input_chord.add(index)
            self.note_buttons[index].setDown(True)
            # Start only the newly pressed sample; restarting held voices is a
            # common source of clicks when building a chord.
            self.preview_note([SKY_KEYS[index]])
            return
        if identity not in self.held_notes:
            return
        self.held_notes.remove(identity)
        self.note_buttons[index].setDown(False)
        if self.separate_recording():
            return
        if not self.held_notes and self.input_chord:
            self.append_event([SKY_KEYS[i] for i in sorted(self.input_chord)])
            self.input_chord.clear()

    def refresh_midi_devices(self):
        try:
            devices = self.midi_backend.devices()
        except Exception as exc:
            self.disconnect_midi(f"MIDI 设备不可用：{exc}")
            return
        if self.connected_device and devices != self.device_snapshot:
            self.disconnect_midi("设备已变化，请重新连接")
        if devices != self.device_snapshot:
            selected = self.midi_devices.currentData()
            self.midi_devices.clear()
            for device in devices:
                self.midi_devices.addItem(f"{device[1]} · {device[0] + 1}", device)
            index = self.midi_devices.findData(selected)
            if index >= 0:
                self.midi_devices.setCurrentIndex(index)
            self.device_snapshot = devices
        self.midi_connect_button.setEnabled(bool(devices))
        if not devices:
            self.midi_status.setText("未检测到 MIDI 键盘；仍可用电脑键盘录入")

    def toggle_midi_connection(self):
        if self.connected_device:
            self.disconnect_midi("MIDI 键盘已断开")
            return
        device = self.midi_devices.currentData()
        if device is None:
            return
        self.midi_connecting.emit()
        try:
            self.midi_backend.connect(device[0])
        except Exception as exc:
            self.disconnect_midi(f"连接失败：{exc}")
            return
        self.connected_device = device
        self.midi_devices.setEnabled(False)
        self.midi_connect_button.setText("断开 MIDI")
        self.midi_status.setText(f"已连接：{device[1]}")

    def disconnect_midi(self, message="MIDI 键盘已断开"):
        self.midi_backend.close()
        self.connected_device = None
        self.reset_live_input()
        self.midi_devices.setEnabled(True)
        self.midi_connect_button.setText("连接 MIDI")
        self.midi_status.setText(message)

    def poll_midi(self):
        if not self.connected_device:
            return
        try:
            for status, note, velocity in self.midi_backend.drain():
                kind = status & 0xF0
                if kind not in (0x80, 0x90):
                    continue
                offset = note - self.midi_base.currentData()
                if offset not in MAJOR_STEPS:
                    continue
                index = MAJOR_STEPS.index(offset)
                self.feed_input_note(index, kind == 0x90 and velocity > 0, ("midi", status & 0x0F, note))
        except Exception as exc:
            self.disconnect_midi(f"MIDI 输入中断：{exc}")

    def shutdown(self):
        self.midi_timer.stop()
        self.device_timer.stop()
        QApplication.instance().removeEventFilter(self)
        self.disconnect_midi()

    def hideEvent(self, event):
        if self.connected_device:
            self.disconnect_midi("已离开制谱页，MIDI 已断开")
        else:
            self.reset_live_input()
        super().hideEvent(event)

    def update_event(self):
        row = self.event_table.currentRow()
        keys = self.selected_keys()
        if row < 0 or not keys:
            self.status_label.setText("选择一组音符并至少选择一个音高")
            return
        self.events[row] = {"time": self.position.value(), "keys": keys}
        self.events.sort(key=lambda event: event["time"])
        self._render_events()
        self.status_label.setText("音符组已更新")

    def delete_event(self):
        row = self.event_table.currentRow()
        if row < 0:
            return
        del self.events[row]
        self._render_events()
        self.status_label.setText("音符组已删除")

    def _load_selected_event(self):
        row = self.event_table.currentRow()
        if row < 0 or row >= len(self.events):
            return
        event = self.events[row]
        self.position.setValue(event["time"])
        self.set_selected_keys(event["keys"])

    def _render_events(self):
        self.event_table.setRowCount(len(self.events))
        for row, event in enumerate(self.events):
            labels = [KEY_LABELS[SKY_KEYS.index(key)] for key in event["keys"] if key in SKY_KEYS]
            for column, text in enumerate((str(row + 1), f"{event['time']} ms", "  +  ".join(labels))):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if column == 0:
                    item.setTextAlignment(Qt.AlignCenter)
                self.event_table.setItem(row, column, item)
        note_count = sum(len(event["keys"]) for event in self.events)
        self.count_label.setText(f"{len(self.events)} 组 · {note_count} 音")

    def _score_data(self):
        title = self.title_input.text().strip()
        if not title:
            raise ValueError("请填写曲谱名称。")
        notes = []
        for event in sorted(self.events, key=lambda item: item["time"]):
            for key in event["keys"]:
                notes.append({"time": int(event["time"]), "key": key})
        if not notes:
            raise ValueError("请至少添加一个音符组。")
        return {
            "songName": title,
            "author": self.author_input.text().strip(),
            "transcribedBy": self.transcriber_input.text().strip(),
            "bpm": self.tempo.value(),
            "songNotes": notes,
        }

    @staticmethod
    def _safe_filename(title):
        safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", title).strip(" .")
        if not safe:
            raise ValueError("曲谱名称不能作为文件名。")
        return safe[:120] + ".json"

    def save_score(self):
        try:
            data = self._score_data()
            target = self.score_dir / self._safe_filename(data["songName"])
            if target.exists():
                answer = QMessageBox.question(self, "覆盖曲谱", f"曲库已有“{target.name}”，要覆盖吗？")
                if answer != QMessageBox.Yes:
                    return
            self.score_dir.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.status_label.setText(f"已保存：{target.name}")
        if self.saved_callback:
            self.saved_callback(target.name)

    def export_score(self):
        try:
            data = self._score_data()
            default_name = self._safe_filename(data["songName"])
        except ValueError as exc:
            QMessageBox.information(self, "无法导出", str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出曲谱", default_name, "JSON 乐谱 (*.json)")
        if not path:
            return
        target = Path(path)
        if target.suffix.lower() != ".json":
            target = target.with_suffix(".json")
        try:
            target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        self.status_label.setText(f"已导出：{target.name}")

    def open_score(self):
        path, _ = QFileDialog.getOpenFileName(self, "打开曲谱", "", "JSON 乐谱 (*.json)")
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            if isinstance(data, list):
                data = data[0]
            raw_notes = data.get("songNotes")
            if not isinstance(raw_notes, list):
                raise ValueError("文件中没有有效的 songNotes。")
            grouped = {}
            for note in raw_notes:
                if not isinstance(note, dict) or note.get("key") not in SKY_KEYS:
                    continue
                try:
                    time_ms = max(0, int(float(note.get("time", 0))))
                except (TypeError, ValueError):
                    continue
                grouped.setdefault(time_ms, []).append(note["key"])
            if not grouped:
                raise ValueError("文件中没有可编辑的光遇 15 音符号。")
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            QMessageBox.warning(self, "无法打开曲谱", str(exc))
            return
        self.title_input.setText(str(data.get("songName") or Path(path).stem))
        self.author_input.setText(str(data.get("author") or ""))
        self.transcriber_input.setText(str(data.get("transcribedBy") or ""))
        self.tempo.setValue(max(20, min(300, int(data.get("bpm", 120) or 120))))
        self.events = [{"time": time_ms, "keys": keys} for time_ms, keys in sorted(grouped.items())]
        self._render_events()
        self.position.setValue(0)
        self.set_selected_keys([])
        self.status_label.setText(f"已打开：{Path(path).name}")

    def toggle_preview(self):
        if self.preview_timer.isActive():
            self.preview_timer.stop()
            self.preview_note([])
            self.preview_button.setText("试听")
            self.status_label.setText("试听已停止")
            return
        if not self.events:
            self.status_label.setText("先添加音符再试听")
            return
        self.preview_events = sorted(self.events, key=lambda event: event["time"])
        self.preview_index = 0
        self.preview_button.setText("停止试听")
        self._preview_tick()

    def _preview_tick(self):
        if self.preview_index >= len(self.preview_events):
            self.preview_button.setText("试听")
            self.status_label.setText("试听完成")
            return
        event = self.preview_events[self.preview_index]
        self.preview_note(event["keys"])
        if self.event_table.rowCount() > self.preview_index:
            self.event_table.selectRow(self.preview_index)
        self.preview_index += 1
        if self.preview_index >= len(self.preview_events):
            self.preview_timer.start(320)
        else:
            gap = self.preview_events[self.preview_index]["time"] - event["time"]
            self.preview_timer.start(max(80, min(5000, gap)))
