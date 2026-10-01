"""MIDI keyboard connection and wait-for-the-correct-notes practice page."""

from datetime import date
import math
from pathlib import Path
import struct
import tempfile
import time
import wave

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QCompleter, QDialog, QFrame, QHBoxLayout, QLabel, QProgressBar,
    QPushButton, QScrollArea, QSpinBox, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from midi_keyboard import PracticeSession, WindowsMidi, note_name, score_groups
from beginner_lessons import BeginnerCourse, GUIDE_HTML, LESSONS, SOLFEGE, SOURCES_HTML
from practice_coach import PracticeCoach


class PianoKeyboard(QWidget):
    def __init__(self):
        super().__init__()
        self.base = 60
        self.held = set()
        self.target = set()
        self.beginner = False
        self.fingers = {}
        self.show_target = True
        self.setMinimumHeight(135)
        self.setAccessibleName("练琴键盘：蓝色为待弹音，绿色为正确按键，橙色为其他按键")

    def paintEvent(self, event):
        painter = QPainter(self)
        white_width = self.width() / 15
        whites = 0
        black_keys = []
        for note in range(self.base, self.base + 25):
            if note % 12 in (1, 3, 6, 8, 10):
                black_keys.append((note, QRectF(whites * white_width - white_width * .3, 0,
                                               white_width * .6, self.height() * (.43 if self.beginner else .62))))
                continue
            rect = QRectF(whites * white_width, 0, white_width, self.height() - 1)
            self.draw_key(painter, note, rect, False)
            whites += 1
        for note, rect in black_keys:
            self.draw_key(painter, note, rect, True)

    def draw_key(self, painter, note, rect, black):
        color = "#24282C" if black else "#F4F3F0"
        if self.show_target and note in self.target:
            color = "#95BDF0"
        if note in self.held:
            color = "#78CFA0" if not self.target or note in self.target else "#EDB16F"
        painter.setPen(QColor("#555B63"))
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 3, 3)
        if not black:
            painter.setPen(QColor("#202327"))
            label = note_name(note)
            if self.beginner:
                label = f"{SOLFEGE.get(note % 12, '')}\n{label}"
                if note in self.fingers:
                    label += f"\n{self.fingers[note]} 指"
            painter.drawText(rect.adjusted(0, 0, 0, -7), Qt.AlignBottom | Qt.AlignHCenter, label)


class MidiPracticePage(QWidget):
    connecting = Signal()
    starting = Signal()
    score_selected = Signal(str)
    progress_saved = Signal()

    def __init__(self, config, parent=None, backend=None):
        super().__init__(parent)
        self.setObjectName("PracticePage")
        self.backend = backend if backend is not None else WindowsMidi()
        self.connected_device = None
        self.held = set()
        self.session = None
        self.notes = {}
        self.song_title = "未选择曲谱"
        self.selected_file = None
        self.library_files = []
        self.device_snapshot = None
        self.course = None
        self.round_recorded = False
        self.attempt_started_at = None
        self.coach = PracticeCoach(config.get("practice_coach", {}))
        self.coach_recommendation = None
        self.demo_groups = []
        self.demo_index = 0
        self.demo_note = None
        self.beat_number = 0
        self.next_beat_time = 0
        saved = config.get("beginner_progress", {})
        self.lesson_progress = dict(saved) if isinstance(saved, dict) else {}
        self.demo_timer = QTimer(self)
        self.demo_timer.timeout.connect(self.demo_tick)
        self.beat_timer = QTimer(self)
        self.beat_timer.setInterval(15)
        self.beat_timer.timeout.connect(self.beat_tick)
        self.click_path = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("PracticeScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        content = QWidget()
        content.setObjectName("PracticeContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(24, 12, 24, 12)
        layout.setSpacing(6)
        scroll.setWidget(content)
        root.addWidget(scroll)

        title = QLabel("练习")
        title.setObjectName("BrandTitle")
        header = QHBoxLayout()
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)
        row = QHBoxLayout()
        self.devices = QComboBox()
        self.devices.setMinimumWidth(180)
        self.devices.setAccessibleName("MIDI 输入设备")
        self.refresh_button = QPushButton("刷新设备")
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.connect_button = QPushButton("连接键盘")
        self.connect_button.clicked.connect(self.toggle_connection)
        row.addWidget(self.devices, 1)
        row.addWidget(self.refresh_button)
        row.addWidget(self.connect_button)
        layout.addLayout(row)
        self.connection_label = QLabel("未连接")
        self.connection_label.setObjectName("MutedText")
        self.connection_label.setMaximumWidth(420)
        self.connection_label.setWordWrap(True)
        header.addWidget(self.connection_label)

        lesson_row = QHBoxLayout()
        self.practice_mode = QComboBox()
        self.practice_mode.setAccessibleName("练习模式")
        self.practice_mode.addItems(["曲谱跟弹", "初学者"])
        self.lesson_selector = QComboBox()
        self.lesson_selector.setAccessibleName("入门课程")
        for lesson in LESSONS:
            self.lesson_selector.addItem(lesson.title, lesson.id)
        self.guide_button = QPushButton("入门指南")
        self.guide_button.clicked.connect(self.show_guide)
        lesson_row.addWidget(self.practice_mode)
        lesson_row.addWidget(self.lesson_selector, 1)
        lesson_row.addWidget(self.guide_button)
        layout.addLayout(lesson_row)

        options = QHBoxLayout()
        self.sound = QCheckBox("本机钢琴试听")
        self.sound.setChecked(bool(config.get("midi_sound", True)))
        self.sound.toggled.connect(self.change_sound)
        self.octave = QComboBox()
        self.octave.setAccessibleName("练习音域")
        for base in (36, 48, 60, 72, 84):
            self.octave.addItem(f"{note_name(base)} – {note_name(base + 24)}", base)
        index = self.octave.findData(config.get("midi_base", 60))
        self.octave.setCurrentIndex(index if index >= 0 else 2)
        self.octave.currentIndexChanged.connect(self.change_octave)
        options.addWidget(self.sound)
        options.addStretch()
        options.addWidget(QLabel("练习音域"))
        options.addWidget(self.octave)
        layout.addLayout(options)
        self.song_selector = QComboBox()
        self.song_selector.setAccessibleName("练习曲目")
        self.song_selector.setEditable(True)
        self.song_selector.setInsertPolicy(QComboBox.NoInsert)
        self.song_selector.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.song_selector.setMinimumContentsLength(16)
        self.song_selector.lineEdit().setPlaceholderText("搜索练习曲目")
        self.song_selector.completer().setCaseSensitivity(Qt.CaseInsensitive)
        self.song_selector.completer().setFilterMode(Qt.MatchContains)
        self.song_selector.completer().setCompletionMode(QCompleter.PopupCompletion)
        self.song_selector.activated.connect(self.choose_score)
        self.song_selector.lineEdit().returnPressed.connect(self.choose_typed_score)
        layout.addWidget(self.song_selector)
        self.lesson_tools = QWidget()
        lesson_tools = QHBoxLayout(self.lesson_tools)
        lesson_tools.setContentsMargins(0, 0, 0, 0)
        self.demo_button = QPushButton("听示范")
        self.demo_button.clicked.connect(self.start_demo)
        self.next_button = QPushButton("下一段")
        self.next_button.clicked.connect(self.advance_course)
        self.tempo = QSpinBox()
        self.tempo.setRange(40, 100)
        self.tempo.setValue(60)
        self.tempo.setSuffix(" BPM")
        self.tempo.setAccessibleName("入门节拍速度")
        self.tempo.valueChanged.connect(self.reset_beat)
        self.metronome = QCheckBox("节拍")
        self.metronome.setToolTip("听四拍，再试一拍一音；节奏不计分。")
        self.metronome.toggled.connect(self.toggle_beat)
        self.beat_label = QLabel("○ ○ ○ ○")
        self.beat_label.setMinimumWidth(78)
        self.key_hints = QCheckBox("亮键提示")
        self.key_hints.setChecked(True)
        self.key_hints.toggled.connect(self.change_key_hints)
        for control in (self.demo_button, self.next_button, self.tempo, self.metronome, self.beat_label, self.key_hints):
            lesson_tools.addWidget(control)
        layout.addWidget(self.lesson_tools)
        self.lesson_hint = QLabel("")
        self.lesson_hint.setObjectName("MutedText")
        self.lesson_hint.setWordWrap(True)
        layout.addWidget(self.lesson_hint)
        self.target_label = QLabel("选择曲目，连接键盘")
        self.target_label.setWordWrap(True)
        self.target_label.setObjectName("CardTitle")
        layout.addWidget(self.target_label)
        self.keyboard = PianoKeyboard()
        self.keyboard.base = self.octave.currentData()
        layout.addWidget(self.keyboard)
        self.legend = QLabel("蓝：待弹　绿：正确　橙：其他音　·　15 音 C 大调")
        self.legend.setWordWrap(True)
        self.legend.setObjectName("MutedText")
        layout.addWidget(self.legend)
        self.keyboard.setToolTip("中央 C = C4（MIDI 60）。和弦需同时按住，重复音需松开再按。\n使用光遇 15 音曲谱，不评判节奏；延音踏板只影响试听。")
        self.input_label = QLabel("当前按键：—")
        self.input_label.setWordWrap(True)
        layout.addWidget(self.input_label)
        self.progress = QProgressBar()
        self.progress.setFixedHeight(6)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.feedback = QLabel("弹对后继续")
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        self.coach_label = QLabel("教练建议：完成一轮后更新")
        self.coach_label.setObjectName("MutedText")
        self.coach_label.setWordWrap(True)
        self.coach_label.setToolTip("根据最近的准确率、错音和复习间隔给出下一步建议。")
        layout.addWidget(self.coach_label)
        transport = QFrame()
        transport.setObjectName("TransportBar")
        controls = QHBoxLayout(transport)
        controls.setContentsMargins(24, 12, 24, 12)
        self.start_button = QPushButton("开始练习")
        self.start_button.setToolTip("从头跟弹（F7）")
        self.start_button.setObjectName("PrimaryButton")
        self.start_button.clicked.connect(self.start_practice)
        self.stop_button = QPushButton("停止")
        self.stop_button.setToolTip("停止并消音（F8 / Esc）")
        self.start_button.setMinimumHeight(38)
        self.stop_button.setMinimumHeight(38)
        self.stop_button.clicked.connect(self.stop_practice)
        controls.addWidget(self.start_button)
        controls.addWidget(self.stop_button)
        root.addWidget(transport)
        layout.addStretch()
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll)
        self.poll_timer.start(10)
        self.scan_timer = QTimer(self)
        self.scan_timer.timeout.connect(self.refresh_devices)
        self.scan_timer.start(2000)
        self.practice_mode.currentIndexChanged.connect(self.change_mode)
        self.lesson_selector.currentIndexChanged.connect(self.change_lesson)
        self.practice_mode.setCurrentIndex(1 if config.get("beginner_mode", False) else 0)
        self.change_mode()
        self.refresh_devices()

    def settings(self):
        return {"midi_base": self.octave.currentData(), "midi_sound": self.sound.isChecked(),
                "beginner_mode": self.is_beginner, "beginner_progress": self.lesson_progress,
                "practice_coach": self.coach.to_dict()}

    @property
    def is_beginner(self):
        return self.practice_mode.currentIndex() == 1

    def show_guide(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("初学者指南")
        dialog.resize(650, 520)
        layout = QVBoxLayout(dialog)
        tabs = QTabWidget()
        for title, html in (("怎么练", GUIDE_HTML), ("参考资料", SOURCES_HTML)):
            text = QTextBrowser()
            text.setOpenExternalLinks(True)
            text.setHtml(html)
            tabs.addTab(text, title)
        layout.addWidget(tabs)
        close = QPushButton("关闭")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()

    def change_mode(self):
        self.stop_practice()
        self.course = None
        for control in (self.lesson_selector, self.lesson_tools, self.lesson_hint):
            control.setVisible(self.is_beginner)
        self.song_selector.setVisible(not self.is_beginner)
        self.keyboard.beginner = self.is_beginner
        self.keyboard.setMinimumHeight(118 if self.is_beginner else 135)
        self.legend.setVisible(not self.is_beginner)
        self.change_lesson()

    def change_lesson(self):
        self.stop_practice()
        self.course = None
        self.progress.setValue(0)
        self.next_button.setEnabled(False)
        self.next_button.setText("下一段")
        self.start_button.setText("开始练习")
        self.keyboard.fingers = {}
        if self.is_beginner:
            lesson = LESSONS[self.lesson_selector.currentIndex()]
            self.keyboard.fingers = lesson.fingers(self.octave.currentData())
            self.lesson_hint.setText(lesson.hint)
            record = self.lesson_progress.get(lesson.id, {})
            completed_date = record.get("date") if isinstance(record, dict) else None
            if completed_date:
                self.feedback.setText(f"上次完成 {completed_date} · 今天可复习")
            else:
                self.feedback.setText("每段连续 2 轮无错，再进入下一段")
            self.update_coach_hint()
        else:
            self.update_coach_hint()
        self.change_key_hints()

    def change_key_hints(self):
        self.keyboard.show_target = not self.is_beginner or self.key_hints.isChecked()
        self.keyboard.update()

    def ensure_course(self):
        if self.course is None:
            self.course = BeginnerCourse(LESSONS[self.lesson_selector.currentIndex()], self.octave.currentData())

    def advance_course(self):
        if not self.course or not self.course.ready:
            return
        if self.course.complete:
            index = self.lesson_selector.currentIndex()
            if index < len(LESSONS) - 1:
                self.lesson_selector.setCurrentIndex(index + 1)
            return
        self.stop_practice()
        self.course.advance()
        self.next_button.setEnabled(False)
        self.start_button.setText("开始本段")
        self.target_label.setText(f"第 {self.course.chunk_index + 1} / {len(self.course.chunks)} 段")
        self.update_coach_hint()

    def start_demo(self):
        if not self.connected_device:
            self.feedback.setText("请先连接 MIDI 键盘")
            return
        if not self.sound.isChecked():
            self.feedback.setText("打开本机钢琴试听后可听示范")
            return
        self.starting.emit()
        self.stop_practice()
        self.ensure_course()
        self.demo_groups = self.course.groups
        self.demo_index = 0
        self.demo_timer.setInterval(round(30000 / self.tempo.value()))
        self.demo_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.next_button.setEnabled(False)
        self.demo_tick()
        if self.demo_groups:
            self.demo_timer.start()

    def demo_tick(self):
        try:
            if self.demo_note is not None:
                self.backend.send(0x80, self.demo_note, 0)
                self.demo_note = None
                self.keyboard.target = set()
            elif self.demo_index < len(self.demo_groups):
                self.demo_note = next(iter(self.demo_groups[self.demo_index]))
                self.demo_index += 1
                self.backend.send(0x90, self.demo_note, 80)
                self.keyboard.target = {self.demo_note}
                self.target_label.setText("示范 · " + self.course.target_text(self.demo_note))
            else:
                self.stop_practice()
                self.feedback.setText("轮到你了，点击开始练习")
            self.keyboard.update()
        except Exception as exc:
            self.stop_practice()
            self.feedback.setText(f"示范不可用：{exc}")

    def toggle_beat(self, enabled):
        self.beat_timer.stop()
        self.beat_label.setText("○ ○ ○ ○")
        if enabled:
            self.reset_beat()
            self.beat_timer.start()

    def reset_beat(self):
        self.beat_number = 0
        self.next_beat_time = time.monotonic()

    def beat_tick(self):
        now = time.monotonic()
        if now < self.next_beat_time:
            return
        self.beat_label.setText(" ".join("●" if i == self.beat_number else "○" for i in range(4)))
        self.beat_number = (self.beat_number + 1) % 4
        # Skip delayed beats instead of playing a burst after a busy GUI frame.
        self.next_beat_time = now + 60 / self.tempo.value()
        if self.sound.isChecked():
            try:
                import winsound
                if self.click_path is None:
                    path = Path(tempfile.gettempdir()) / "sky_beginner_metronome.wav"
                    rate = 22050
                    frames = b"".join(struct.pack("<h", int(6000 * math.sin(2 * math.pi * 1200 * i / rate)
                                                          * math.exp(-i / 120))) for i in range(660))
                    with wave.open(str(path), "wb") as wav:
                        wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
                        wav.writeframes(frames)
                    self.click_path = path
                winsound.PlaySound(str(self.click_path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            except Exception:
                self.beat_label.setToolTip("节拍声音不可用，仍可跟随亮点数拍")

    def set_library(self, files, display_name):
        if files == self.library_files:
            return
        self.library_files = list(files)
        self.song_selector.blockSignals(True)
        self.song_selector.clear()
        for filename in files:
            self.song_selector.addItem(display_name(filename), filename)
        index = self.song_selector.findData(self.selected_file)
        self.song_selector.setCurrentIndex(index if index >= 0 else (0 if files else -1))
        self.song_selector.blockSignals(False)
        self.choose_score()

    def choose_score(self, index=None):
        filename = self.song_selector.currentData()
        if filename != self.selected_file:
            self.selected_file = filename
            self.score_selected.emit(filename or "")

    def choose_typed_score(self):
        query = self.song_selector.currentText().strip().casefold()
        for index in range(self.song_selector.count()):
            if self.song_selector.itemText(index).casefold() == query:
                self.song_selector.setCurrentIndex(index)
                self.choose_score()
                return
        self.feedback.setText("请从搜索结果选择曲目")

    def refresh_devices(self):
        try:
            devices = self.backend.devices()
        except Exception as exc:
            self.disconnect(f"无法读取 MIDI 设备：{exc}")
            return
        if self.connected_device and devices != self.device_snapshot:
            # Device indices can change on hot-plug; require an explicit reconnect.
            self.disconnect("设备已变化，请重新连接")
        if devices != self.device_snapshot:
            selected = self.devices.currentData()
            self.devices.clear()
            for device in devices:
                self.devices.addItem(f"{device[1]} · {device[0] + 1}", device)
            index = self.devices.findData(selected)
            if index >= 0:
                self.devices.setCurrentIndex(index)
            self.device_snapshot = devices
        self.connect_button.setEnabled(bool(devices))
        if not devices:
            self.connection_label.setText("未检测到键盘")

    def toggle_connection(self):
        if self.connected_device:
            self.disconnect("键盘已断开")
            return
        device = self.devices.currentData()
        if device is None:
            return
        self.connecting.emit()
        try:
            self.backend.connect(device[0])
            self.connected_device = device
            self.devices.setEnabled(False)
            self.connect_button.setText("断开连接")
            self.connection_label.setText(f"已连接：{device[1]}")
            self.change_sound(self.sound.isChecked())
        except Exception as exc:
            self.disconnect(f"连接失败：{exc}。请检查驱动，或关闭占用键盘的其他软件。")

    def disconnect(self, message):
        self.stop_practice()
        self.backend.close()
        self.connected_device = None
        self.held.clear()
        self.devices.setEnabled(True)
        self.connect_button.setText("连接键盘")
        self.connection_label.setText(message)
        self.update_input()

    def change_sound(self, enabled):
        if not enabled and self.demo_timer.isActive():
            self.stop_practice()
        if self.connected_device:
            try:
                self.backend.enable_sound(enabled)
            except Exception as exc:
                self.sound.blockSignals(True)
                self.sound.setChecked(False)
                self.sound.blockSignals(False)
                self.connection_label.setText(f"键盘已连接，钢琴试听不可用：{exc}。仍可无声跟弹。")

    def set_score(self, title, notes):
        if not self.is_beginner:
            self.stop_practice()
        self.song_title = title
        self.notes = {t: list(keys) for t, keys in notes.items()}
        self.song_selector.setToolTip(title)
        if not self.is_beginner:
            self.progress.setValue(0)
            self.target_label.setText("待开始" if notes else "请选择曲目")
            self.update_coach_hint()

    def change_octave(self):
        self.change_lesson()
        self.keyboard.base = self.octave.currentData()
        self.keyboard.update()

    def start_practice(self):
        if not self.connected_device:
            self.feedback.setText("请先连接 MIDI 键盘。")
            return
        if self.is_beginner:
            if self.course and self.course.complete:
                self.course = None
            self.ensure_course()
            groups = self.course.groups
        else:
            groups = score_groups(self.notes, self.octave.currentData())
        if not groups:
            self.feedback.setText("无可练习音符，请换曲")
            return
        use_beat = self.metronome.isChecked() or (self.is_beginner and self.course.lesson.rhythm)
        self.starting.emit()
        self.stop_practice()
        self.session = PracticeSession(groups, self.held)
        self.round_recorded = False
        self.attempt_started_at = time.monotonic()
        self.next_button.setEnabled(False)
        if self.is_beginner and use_beat:
            self.metronome.setChecked(True)
        self.progress.setRange(0, len(groups))
        self.progress.setValue(0)
        self.show_target()

    def stop_practice(self):
        self.session = None
        self.demo_timer.stop()
        self.demo_groups = []
        self.demo_note = None
        self.demo_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self.metronome.setChecked(False)
        self.beat_timer.stop()
        self.backend.silence()
        self.keyboard.target = set()
        self.keyboard.update()
        self.target_label.setText("待开始")
        self.feedback.setText("弹对后继续")
        self.attempt_started_at = None
        self.next_button.setEnabled(bool(self.course and self.course.ready and not
                                        (self.course.complete and self.lesson_selector.currentIndex() == len(LESSONS) - 1)))

    def show_target(self):
        session = self.session
        self.progress.setValue(session.index)
        self.keyboard.target = session.target
        if session.target:
            if self.is_beginner:
                names = self.course.target_text(next(iter(session.target)))
                self.target_label.setText(f"第 {self.course.chunk_index + 1} 段 · {names}")
            else:
                names = " + ".join(note_name(n) for n in sorted(session.target))
                self.target_label.setText(f"第 {session.index + 1} / {len(session.groups)} 组 · 请弹：{names}")
            self.feedback.setText(f"已完成 {session.index} 组 · 错音 {session.mistakes} 次")
        else:
            self.target_label.setText("练习完成！")
            self.feedback.setText(f"共完成 {len(session.groups)} 组 · 错音 {session.mistakes} 次")
            if self.is_beginner:
                self.finish_beginner_round()
            else:
                self.finish_score_round()
        self.keyboard.update()

    def coach_context(self, *, mistakes=0, accuracy=0.0, streak=0, segment=0,
                      duration=0.0, hint_used=False):
        if self.is_beginner and self.course:
            piece = f"lesson:{self.course.lesson.id}"
            segment = self.course.chunk_index
        else:
            piece = f"score:{self.selected_file or self.song_title or 'practice'}"
        return {
            "piece": piece,
            "segment": segment,
            "accuracy": accuracy,
            "mistakes": mistakes,
            "streak": streak,
            "tempo": self.tempo.value(),
            "hint_used": hint_used,
            "duration": duration,
            "is_beginner": self.is_beginner,
        }

    def update_coach_hint(self, context=None):
        if context is None:
            context = self.coach_context(
                segment=self.course.chunk_index if self.course else 0,
                hint_used=self.is_beginner and self.key_hints.isChecked(),
            )
        self.coach_recommendation = self.coach.recommend(context)
        self.coach_label.setText(self.coach_recommendation["message"])
        self.coach_label.setToolTip(self.coach_recommendation["reason"])

    def apply_coach_recommendation(self, recommendation):
        if not recommendation:
            return
        if self.is_beginner and recommendation["action"] == "slow_down":
            target = recommendation.get("tempo", self.tempo.value())
            if target < self.tempo.value():
                self.tempo.setValue(target)
        if self.is_beginner and recommendation.get("show_hints") and not self.key_hints.isChecked():
            self.key_hints.setChecked(True)
        self.coach_recommendation = recommendation
        self.coach_label.setText(recommendation["message"])
        self.coach_label.setToolTip(recommendation["reason"])

    def finish_score_round(self):
        if self.round_recorded or not self.session:
            return
        groups = max(1, len(self.session.groups))
        accuracy = max(0.0, 1.0 - self.session.mistakes / groups)
        duration = 0.0 if self.attempt_started_at is None else max(0.0, time.monotonic() - self.attempt_started_at)
        recommendation = self.coach.record_attempt(self.coach_context(
            mistakes=self.session.mistakes,
            accuracy=accuracy,
            streak=1 if self.session.mistakes == 0 else 0,
            duration=duration,
        ))
        self.round_recorded = True
        self.apply_coach_recommendation(recommendation)
        self.progress_saved.emit()

    def finish_beginner_round(self):
        if not self.round_recorded:
            mistakes = self.session.mistakes
            groups = max(1, len(self.session.groups))
            accuracy = max(0.0, 1.0 - mistakes / groups)
            duration = 0.0 if self.attempt_started_at is None else max(0.0, time.monotonic() - self.attempt_started_at)
            self.course.finish_round(mistakes)
            recommendation = self.coach.record_attempt(self.coach_context(
                mistakes=mistakes,
                accuracy=accuracy,
                streak=self.course.clean_rounds,
                segment=self.course.chunk_index,
                duration=duration,
                hint_used=self.key_hints.isChecked(),
            ))
            self.round_recorded = True
            self.apply_coach_recommendation(recommendation)
            if self.course.complete:
                self.lesson_progress[self.course.lesson.id] = {
                    "date": date.today().isoformat(), "attempts": self.course.attempts,
                    "errors": self.course.errors,
                }
            self.progress_saved.emit()
        self.metronome.setChecked(False)
        self.start_button.setText("再练一轮")
        self.next_button.setText("下一课" if self.course.complete else "下一段")
        self.next_button.setEnabled(self.course.ready and not
                                    (self.course.complete and self.lesson_selector.currentIndex() == len(LESSONS) - 1))
        if self.course.complete:
            self.target_label.setText("本课完成 · 明天再复习")
            self.feedback.setText(f"练了 {self.course.attempts} 轮 · 错音 {self.course.errors} 次")
        else:
            self.target_label.setText(f"第 {self.course.chunk_index + 1} / {len(self.course.chunks)} 段 · 无错 {self.course.clean_rounds} / 2 轮")
            self.feedback.setText("可以进入下一段" if self.course.ready else "找到错音后再试一次" if self.session.mistakes else "很好，再巩固一轮")
            if self.coach_recommendation:
                self.coach_label.setText(self.coach_recommendation["message"])

    def update_input(self, velocity=None):
        notes = {n for _, n in self.held}
        self.keyboard.held = notes
        self.keyboard.update()
        names = "  ".join(note_name(n) for n in sorted(notes)) or "—"
        suffix = f" · 力度 {velocity}" if velocity else ""
        self.input_label.setText(f"当前按键：{names}{suffix}")

    def poll(self):
        try:
            for status, note, velocity in self.backend.drain():
                kind, channel = status & 0xF0, status & 15
                if kind == 0xB0 and note in (120, 123):
                    self.held = {key for key in self.held if key[0] != channel}
                    self.backend.silence()
                    if self.session:
                        self.session.held = set(self.held)
                        self.session.fresh.clear()
                    self.update_input()
                    continue
                if kind not in (0x80, 0x90, 0xB0):
                    continue
                if not self.demo_timer.isActive():
                    self.backend.send(status, note, velocity)
                if kind == 0xB0:
                    continue
                if kind == 0x90 and velocity:
                    self.held.add((channel, note))
                else:
                    self.held.discard((channel, note))
                if self.session:
                    self.session.feed(status, note, velocity)
                    self.show_target()
                    wrong = {n for _, n in self.held} - self.session.target
                    if self.session.target and wrong:
                        self.feedback.setText("请松开其他音：" + "、".join(note_name(n) for n in sorted(wrong))
                                              + f" · 错音 {self.session.mistakes} 次")
                self.update_input(velocity if kind == 0x90 else None)
        except Exception as exc:
            self.disconnect(f"MIDI 连接中断：{exc}，请重新连接。")

    def shutdown(self):
        self.poll_timer.stop()
        self.scan_timer.stop()
        self.disconnect("键盘已断开")
