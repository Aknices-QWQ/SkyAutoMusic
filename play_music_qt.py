import ctypes
import json
import math
import os
import random
import struct
import sys
import tempfile
import threading
import time
import wave
from bisect import bisect_left
from collections import defaultdict
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QCloseEvent, QShortcut, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from sheet_updater import GITHUB_MIRRORS, install_sheet_update, read_local_state


APP_VERSION = "1.0.0"
if "__compiled__" in globals() or getattr(sys, "frozen", False):
    APP_DIR = Path(sys.executable).resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
SHEET_MUSIC_DIR = APP_DIR / "Sheet Music"
CONFIG_FILE = APP_DIR / "config.json"
FAVORITES_FILE = APP_DIR / "favorites.json"
SHEET_MUSIC_DIR.mkdir(exist_ok=True)

NOTE_TO_KEY = {
    "1Key0": "Y", "1Key1": "U", "1Key2": "I", "1Key3": "O", "1Key4": "P",
    "1Key5": "H", "1Key6": "J", "1Key7": "K", "1Key8": "L", "1Key9": ";",
    "1Key10": "N", "1Key11": "M", "1Key12": ",", "1Key13": ".", "1Key14": "/",
    "2Key0": "Y", "2Key1": "U", "2Key2": "I", "2Key3": "O", "2Key4": "P",
    "2Key5": "H", "2Key6": "J", "2Key7": "K", "2Key8": "L", "2Key9": ";",
    "2Key10": "N", "2Key11": "M", "2Key12": ",", "2Key13": ".", "2Key14": "/",
}
KEY_TO_SCANCODE = {
    "Y": 0x15, "U": 0x16, "I": 0x17, "O": 0x18, "P": 0x19,
    "H": 0x23, "J": 0x24, "K": 0x25, "L": 0x26, ";": 0x27,
    "N": 0x31, "M": 0x32, ",": 0x33, ".": 0x34, "/": 0x35,
}
SKY_MAJOR = [0, 2, 4, 5, 7, 9, 11, 12, 14, 16, 17, 19, 21, 23, 24]

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
INPUT_KEYBOARD = 1
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_ushort),
        ("wScan", ctypes.c_ushort),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.c_ulong),
        ("dwFlags", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_ulong),
        ("wParamL", ctypes.c_short),
        ("wParamH", ctypes.c_ushort),
    ]


class INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("union", INPUTUNION)]


def send_scan_key(key: str, key_up: bool = False) -> bool:
    scan_code = KEY_TO_SCANCODE.get(key.upper() if len(key) == 1 and key.isalpha() else key)
    if scan_code is None:
        return False
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if key_up else 0)
    event = INPUT(type=INPUT_KEYBOARD, union=INPUTUNION(ki=KEYBDINPUT(0, scan_code, flags, 0, 0)))
    user32 = ctypes.windll.user32
    user32.SendInput.argtypes = (ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = ctypes.c_uint
    return user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) == 1


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, data):
    try:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def parse_music_file(filename: str):
    path = SHEET_MUSIC_DIR / filename
    last_err = None
    for enc in ("utf-8", "utf-8-sig", "gbk", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            with path.open("r", encoding=enc) as f:
                data = json.load(f)
            break
        except Exception as exc:
            last_err = exc
    else:
        raise RuntimeError(f"乐谱解析失败: {last_err}")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        meta = data[0]
    elif isinstance(data, dict):
        meta = data
    else:
        raise RuntimeError("乐谱格式不正确")
    notes = meta.get("songNotes")
    if not isinstance(notes, list):
        raise RuntimeError("乐谱缺少 songNotes")
    return meta, notes


class PlayerSignals(QObject):
    progress = Signal(int, str, str, list)
    finished = Signal(bool)
    status = Signal(str)


class UpdateSignals(QObject):
    progress = Signal(int, str)
    finished = Signal(dict)
    failed = Signal(str)


class FloatingControl(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.drag_pos = None
        self.setWindowTitle("悬浮控制")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setObjectName("FloatingControl")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 16)

        surface = QFrame()
        surface.setObjectName("OverlaySurface")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 170))
        surface.setGraphicsEffect(shadow)
        outer.addWidget(surface)

        root = QVBoxLayout(surface)
        root.setContentsMargins(14, 12, 14, 13)
        root.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("悬浮控制")
        title.setObjectName("OverlayTitle")
        pinned = QLabel("始终置顶")
        pinned.setObjectName("OverlayBadge")
        close = QPushButton("关闭")
        close.setObjectName("OverlayClose")
        close.setFixedWidth(58)
        close.clicked.connect(self.hide)
        header.addWidget(title)
        header.addWidget(pinned)
        header.addStretch()
        header.addWidget(close)
        root.addLayout(header)

        now_playing = QFrame()
        now_playing.setObjectName("OverlayNowPlaying")
        now_layout = QVBoxLayout(now_playing)
        now_layout.setContentsMargins(12, 10, 12, 10)
        now_layout.setSpacing(6)

        song_row = QHBoxLayout()
        self.song_label = QLabel("未选择曲谱")
        self.song_label.setObjectName("OverlaySong")
        self.song_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.song_label.setMinimumWidth(0)
        self.percent_label = QLabel("0%")
        self.percent_label.setObjectName("OverlayPercent")
        song_row.addWidget(self.song_label, 1)
        song_row.addWidget(self.percent_label)
        now_layout.addLayout(song_row)

        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setObjectName("OverlayTime")
        now_layout.addWidget(self.time_label)
        self.progress = QSlider(Qt.Horizontal)
        self.progress.setRange(0, 100)
        self.progress.setAccessibleName("悬浮窗演奏进度")
        self.progress.sliderMoved.connect(self.preview_seek_position)
        self.progress.sliderReleased.connect(self.seek_from_slider)
        now_layout.addWidget(self.progress)
        self.keys_label = QLabel("当前按键：—")
        self.keys_label.setObjectName("OverlayKeys")
        now_layout.addWidget(self.keys_label)
        root.addWidget(now_playing)

        row = QHBoxLayout()
        row.setSpacing(8)
        range_label = QLabel("曲库")
        range_label.setObjectName("OverlayFieldLabel")
        self.mode = QComboBox()
        self.mode.addItems(["全部", "收藏"])
        self.mode.setFixedWidth(82)
        self.mode.setAccessibleName("曲库范围")
        self.song = QComboBox()
        self.song.setAccessibleName("选择乐谱")
        self.song.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.song.setMinimumContentsLength(14)
        self.song.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.mode.currentTextChanged.connect(self.on_mode_changed)
        self.song.currentIndexChanged.connect(self.on_song_changed)
        row.addWidget(range_label)
        row.addWidget(self.mode)
        row.addWidget(self.song, 1)
        root.addLayout(row)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.start_btn = QPushButton("开始")
        self.start_btn.setObjectName("OverlayPrimary")
        self.preview_btn = QPushButton("预览")
        self.preview_btn.setObjectName("OverlayPreview")
        self.preview_btn.setFixedWidth(62)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("OverlayDanger")
        self.main_btn = QPushButton("主窗")
        for button in (self.start_btn, self.preview_btn, self.stop_btn, self.main_btn):
            button.setMinimumHeight(34)
        self.start_btn.clicked.connect(main.start_play)
        self.preview_btn.clicked.connect(main.toggle_preview)
        self.stop_btn.clicked.connect(main.stop_play)
        self.main_btn.clicked.connect(main.toggle_main_window)
        controls.addWidget(self.start_btn, 2)
        controls.addWidget(self.preview_btn)
        controls.addWidget(self.stop_btn)
        controls.addWidget(self.main_btn)
        root.addLayout(controls)

        self.status_label = QLabel("待机")
        self.status_label.setObjectName("OverlayStatus")
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        root.addWidget(self.status_label)
        self.resize(450, 278)
        self.refresh()

    def refresh(self):
        self.mode.blockSignals(True)
        self.mode.setCurrentText(self.main.overlay_mode)
        self.mode.blockSignals(False)
        files = self.main.overlay_song_files()
        current = self.main.selected_file
        self.song.blockSignals(True)
        self.song.clear()
        for filename in files:
            self.song.addItem(self.main.display_song_name(filename), filename)
        current_index = self.song.findData(current)
        if current_index >= 0:
            self.song.setCurrentIndex(current_index)
        self.song.blockSignals(False)

    def on_mode_changed(self, text):
        self.main.overlay_mode = text
        self.main.save_config()
        self.refresh()

    def on_song_changed(self):
        filename = self.song.currentData()
        if filename:
            self.main.select_file(filename, sync_list=True)

    def preview_seek_position(self, percent):
        self.main.progress.blockSignals(True)
        self.main.progress.setValue(percent)
        self.main.progress.blockSignals(False)
        self.main.preview_seek_position(percent)

    def seek_from_slider(self):
        self.main.progress.blockSignals(True)
        self.main.progress.setValue(self.progress.value())
        self.main.progress.blockSignals(False)
        self.main.seek_from_slider()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_pos and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_pos)
            event.accept()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = load_json(CONFIG_FILE, {})
        self.favorites = set(load_json(FAVORITES_FILE, []))
        self.speed_logic_version = int(self.config.get("speed_logic_version", 1) or 1)
        if self.speed_logic_version >= 2 and isinstance(self.config.get("speed_presets"), dict):
            self.speed_presets = self.config.get("speed_presets")
            self.initial_speed = float(self.config.get("speed", 1.0))
        else:
            self.speed_presets = {}
            self.initial_speed = 1.0
        self.speed_logic_version = 2
        self.overlay_mode = self.config.get("overlay_music_mode", "全部")
        self.all_files = []
        self.display_files = []
        self.selected_file = None
        self.meta = {}
        self.notes_by_time = defaultdict(list)
        self.sorted_times = []
        self.bpm = 120
        self.player_thread = None
        self.stop_event = threading.Event()
        self.seek_lock = threading.Lock()
        self.seek_request_index = None
        self.global_escape_registered = False
        self.previewing = False
        self.preview_cache = {}
        self.preview_dir = Path(tempfile.gettempdir()) / "sky_auto_music_qt_preview"
        self.preview_dir.mkdir(exist_ok=True)
        self.pressed_keys = set()
        self.signals = PlayerSignals()
        self.signals.progress.connect(self.on_worker_progress)
        self.signals.finished.connect(self.on_worker_finished)
        self.signals.status.connect(self.set_status)
        self.update_signals = UpdateSignals()
        self.update_signals.progress.connect(self.on_update_progress)
        self.update_signals.finished.connect(self.on_update_finished)
        self.update_signals.failed.connect(self.on_update_failed)
        self.update_running = False

        self.setWindowTitle("SkyAutoMusic")
        self.resize(int(self.config.get("width", 1080)), int(self.config.get("height", 720)))
        self.setMinimumSize(860, 620)
        self.build_ui()
        self.apply_style()
        self.bind_escape_stop()
        self.refresh_files()
        self.watch_timer = QTimer(self)
        self.watch_timer.timeout.connect(self.refresh_files_if_changed)
        self.watch_timer.start(1500)

    def bind_escape_stop(self):
        self.escape_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self.escape_shortcut.setContext(Qt.ApplicationShortcut)
        self.escape_shortcut.activated.connect(self.force_stop)
        try:
            import keyboard
            keyboard.add_hotkey("esc", self.force_stop, suppress=False)
            self.global_escape_registered = True
        except Exception:
            self.global_escape_registered = False

    def build_ui(self):
        tabs = QTabWidget()
        tabs.setObjectName("MainTabs")
        tabs.setDocumentMode(True)
        self.setCentralWidget(tabs)
        play_page = QWidget()
        play_page.setObjectName("PlayPage")
        help_page = QWidget()
        help_page.setObjectName("HelpPage")
        tabs.addTab(play_page, "播放")
        tabs.addTab(help_page, "说明")

        page_layout = QVBoxLayout(play_page)
        page_layout.setContentsMargins(20, 16, 20, 20)
        page_layout.setSpacing(14)

        top_bar = QFrame()
        top_bar.setObjectName("TopBar")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(4, 2, 4, 6)
        brand_stack = QVBoxLayout()
        brand_stack.setSpacing(1)
        brand = QLabel("SkyAutoMusic")
        brand.setObjectName("BrandTitle")
        tagline = QLabel("专注、稳定的 Sky 自动演奏控制台")
        tagline.setObjectName("MutedText")
        brand_stack.addWidget(brand)
        brand_stack.addWidget(tagline)
        top_layout.addLayout(brand_stack)
        top_layout.addStretch()
        shortcut_hint = QLabel("ESC 随时停止")
        shortcut_hint.setObjectName("ShortcutHint")
        top_layout.addWidget(shortcut_hint)
        page_layout.addWidget(top_bar)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("WorkspaceSplitter")
        splitter.setChildrenCollapsible(False)
        page_layout.addWidget(splitter, 1)

        left = QFrame()
        left.setObjectName("Panel")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(14, 16, 14, 14)
        left_layout.setSpacing(10)
        library_header = QHBoxLayout()
        library_title = QLabel("曲库")
        library_title.setObjectName("SectionTitle")
        self.song_count_label = QLabel("0 首")
        self.song_count_label.setObjectName("CountBadge")
        library_header.addWidget(library_title)
        library_header.addStretch()
        library_header.addWidget(self.song_count_label)
        left_layout.addLayout(library_header)
        self.mode_tabs = QComboBox()
        self.mode_tabs.addItems(["全部曲谱", "收藏曲谱"])
        self.mode_tabs.currentTextChanged.connect(self.refresh_list)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索歌名或文件名")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh_list)
        self.list_widget = QListWidget()
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setSpacing(2)
        self.list_widget.itemSelectionChanged.connect(self.on_list_selection)
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self.show_song_menu)
        left_layout.addWidget(self.mode_tabs)
        left_layout.addWidget(self.search)
        left_layout.addWidget(self.list_widget, 1)
        left.setMinimumWidth(250)
        left.setMaximumWidth(430)
        splitter.addWidget(left)

        right_scroll = QScrollArea()
        right_scroll.setObjectName("RightScroll")
        right_scroll.setWidgetResizable(True)
        right_scroll.setFrameShape(QFrame.NoFrame)
        right_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right_content = QWidget()
        right_content.setObjectName("RightContent")
        right = QVBoxLayout(right_content)
        right.setContentsMargins(4, 0, 4, 4)
        right.setSpacing(12)
        right_scroll.setWidget(right_content)
        splitter.addWidget(right_scroll)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 780])

        info = QFrame()
        info.setObjectName("NowPlayingCard")
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(20, 18, 20, 18)
        info_layout.setSpacing(8)
        eyebrow = QLabel("当前曲目")
        eyebrow.setObjectName("Eyebrow")
        self.name_label = QLabel("请选择一首乐谱")
        self.name_label.setObjectName("SongTitle")
        self.name_label.setWordWrap(True)
        self.song_summary_label = QLabel("从左侧曲库选择后即可开始")
        self.song_summary_label.setObjectName("MutedText")
        info_layout.addWidget(eyebrow)
        info_layout.addWidget(self.name_label)
        info_layout.addWidget(self.song_summary_label)

        info_grid = QGridLayout()
        info_grid.setHorizontalSpacing(20)
        info_grid.setVerticalSpacing(5)
        self.author_label = QLabel("—")
        self.transcribed_label = QLabel("—")
        self.filename_label = QLabel("—")
        self.filename_label.setObjectName("Filename")
        self.filename_label.setWordWrap(True)
        for row, (label, widget) in enumerate([
            ("作者", self.author_label), ("制谱", self.transcribed_label),
            ("文件", self.filename_label),
        ]):
            field = QLabel(label)
            field.setObjectName("FieldLabel")
            info_grid.addWidget(field, row, 0)
            info_grid.addWidget(widget, row, 1)
        info_grid.setColumnStretch(1, 1)
        info_layout.addLayout(info_grid)
        right.addWidget(info)

        time_box = QFrame()
        time_box.setObjectName("Card")
        time_layout = QVBoxLayout(time_box)
        time_layout.setContentsMargins(18, 14, 18, 16)
        time_layout.setSpacing(8)
        top_time = QHBoxLayout()
        progress_title = QLabel("演奏进度")
        progress_title.setObjectName("CardTitle")
        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setObjectName("ValueLabel")
        self.percent_label = QLabel("0%")
        self.percent_label.setObjectName("ValueLabel")
        top_time.addWidget(progress_title)
        top_time.addSpacing(10)
        top_time.addWidget(self.time_label)
        top_time.addStretch()
        top_time.addWidget(self.percent_label)
        self.progress = QSlider(Qt.Horizontal)
        self.progress.setRange(0, 100)
        self.progress.setAccessibleName("演奏进度")
        self.progress.sliderMoved.connect(self.preview_seek_position)
        self.progress.sliderReleased.connect(self.seek_from_slider)
        time_layout.addLayout(top_time)
        time_layout.addWidget(self.progress)
        right.addWidget(time_box)

        settings_row = QHBoxLayout()
        settings_row.setSpacing(12)

        speed_box = QFrame()
        speed_box.setObjectName("Card")
        speed_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        speed_layout = QVBoxLayout(speed_box)
        speed_layout.setContentsMargins(18, 14, 18, 16)
        speed_layout.setSpacing(10)
        speed_title_row = QHBoxLayout()
        speed_title = QLabel("相对原曲速率")
        speed_title.setObjectName("CardTitle")
        speed_title_row.addWidget(speed_title)
        speed_title_row.addStretch()
        self.speed_label = QLabel("")
        self.speed_label.setObjectName("ValueBadge")
        speed_title_row.addWidget(self.speed_label)
        speed_layout.addLayout(speed_title_row)
        speed_row = QHBoxLayout()
        self.speed_slider = QSlider(Qt.Horizontal)
        self.speed_slider.setRange(25, 200)
        self.speed_slider.setValue(int(self.initial_speed * 100))
        self.speed_slider.setAccessibleName("相对原曲速度")
        self.speed_slider.valueChanged.connect(self.on_speed_changed)
        speed_row.addWidget(self.speed_slider)
        speed_layout.addLayout(speed_row)
        quick = QHBoxLayout()
        for value in (0.75, 1.0, 1.25):
            btn = QPushButton(f"{value:g}x")
            btn.clicked.connect(lambda _, v=value: self.set_speed(v))
            quick.addWidget(btn)
        save = QPushButton("保存速度")
        clear = QPushButton("清除")
        save.clicked.connect(self.save_speed_preset)
        clear.clicked.connect(self.clear_speed_preset)
        quick.addWidget(save)
        quick.addWidget(clear)
        speed_layout.addLayout(quick)
        settings_row.addWidget(speed_box, 1)
        self.on_speed_changed()

        effects = QFrame()
        effects.setObjectName("Card")
        effects.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        effects_grid = QGridLayout(effects)
        effects_grid.setContentsMargins(18, 14, 18, 16)
        effects_grid.setHorizontalSpacing(10)
        effects_grid.setVerticalSpacing(10)
        effects_title = QLabel("演奏效果")
        effects_title.setObjectName("CardTitle")
        effects_grid.addWidget(effects_title, 0, 0, 1, 3)
        self.delay_slider = QSlider(Qt.Horizontal)
        self.delay_slider.setRange(0, 50)
        self.delay_slider.setValue(int(float(self.config.get("random_delay_percent", 0))))
        self.delay_label = QLabel("")
        self.wrong_slider = QSlider(Qt.Horizontal)
        self.wrong_slider.setRange(0, 30)
        self.wrong_slider.setValue(int(float(self.config.get("wrong_key_percent", 0))))
        self.wrong_label = QLabel("")
        self.delay_slider.valueChanged.connect(self.update_effect_labels)
        self.wrong_slider.valueChanged.connect(self.update_effect_labels)
        self.delay_label.setObjectName("ValueBadge")
        self.wrong_label.setObjectName("ValueBadge")
        effects_grid.addWidget(QLabel("随机延迟"), 1, 0)
        effects_grid.addWidget(self.delay_slider, 1, 1)
        effects_grid.addWidget(self.delay_label, 1, 2)
        effects_grid.addWidget(QLabel("错键概率"), 2, 0)
        effects_grid.addWidget(self.wrong_slider, 2, 1)
        effects_grid.addWidget(self.wrong_label, 2, 2)
        settings_row.addWidget(effects, 1)
        right.addLayout(settings_row)
        self.update_effect_labels()

        controls = QFrame()
        controls.setObjectName("ControlCard")
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(18, 14, 18, 16)
        controls_layout.setSpacing(10)
        control_title = QLabel("播放控制")
        control_title.setObjectName("CardTitle")
        controls_layout.addWidget(control_title)
        grid = QGridLayout()
        grid.setSpacing(8)
        self.start_btn = QPushButton("开始演奏")
        self.start_btn.setObjectName("PrimaryButton")
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("DangerButton")
        self.preview_btn = QPushButton("预览")
        self.overlay_btn = QPushButton("显示悬浮窗")
        for button in (self.start_btn, self.stop_btn, self.preview_btn, self.overlay_btn):
            button.setMinimumHeight(42)
        self.stop_btn.setEnabled(False)
        self.start_btn.clicked.connect(self.start_play)
        self.stop_btn.clicked.connect(self.stop_play)
        self.preview_btn.clicked.connect(self.toggle_preview)
        self.overlay_btn.clicked.connect(self.toggle_overlay)
        grid.addWidget(self.start_btn, 0, 0, 1, 2)
        grid.addWidget(self.stop_btn, 0, 2)
        grid.addWidget(self.preview_btn, 1, 0)
        grid.addWidget(self.overlay_btn, 1, 1, 1, 2)
        controls_layout.addLayout(grid)
        right.addWidget(controls)

        self.status = QLabel("请选择乐谱")
        self.status.setObjectName("Status")
        self.status.setWordWrap(True)
        right.addWidget(self.status)
        right.addStretch(1)

        help_layout = QVBoxLayout(help_page)
        help_layout.setContentsMargins(24, 24, 24, 24)
        help_layout.setSpacing(12)
        help_title = QLabel("使用说明")
        help_title.setObjectName("BrandTitle")
        help_intro = QLabel("从曲库选择乐谱，调整速度与演奏效果，然后点击“开始演奏”。")
        help_intro.setObjectName("MutedText")
        help_intro.setWordWrap(True)
        help_card = QFrame()
        help_card.setObjectName("Card")
        help_card_layout = QVBoxLayout(help_card)
        help_card_layout.setContentsMargins(20, 18, 20, 18)
        for text in (
            "曲库：输入关键词搜索；右键乐谱可收藏或取消收藏。",
            "预览：先在本机试听键位与节奏，不会向游戏发送按键。",
            "进度：主窗口与悬浮窗都可拖动进度，松开后立即跳转。",
            "速度：1.00x 为原曲 BPM，其余倍率按原曲速度加速或减速。",
            "悬浮窗：游戏中快速切歌、开始或停止演奏。",
            "紧急停止：任何时候按 ESC 都会立即释放全部按键。",
        ):
            line = QLabel(text)
            line.setWordWrap(True)
            help_card_layout.addWidget(line)
        help_layout.addWidget(help_title)
        help_layout.addWidget(help_intro)
        help_layout.addWidget(help_card)

        update_card = QFrame()
        update_card.setObjectName("Card")
        update_layout = QVBoxLayout(update_card)
        update_layout.setContentsMargins(20, 18, 20, 18)
        update_layout.setSpacing(10)
        update_header = QHBoxLayout()
        update_title = QLabel("在线曲库更新")
        update_title.setObjectName("CardTitle")
        local_state = read_local_state(APP_DIR)
        local_version = local_state.get("version", "内置版本")
        self.update_version_label = QLabel(f"当前：{local_version}")
        self.update_version_label.setObjectName("ValueBadge")
        update_header.addWidget(update_title)
        update_header.addStretch()
        update_header.addWidget(self.update_version_label)
        update_layout.addLayout(update_header)

        update_copy = QLabel("从 GitHub Release 下载完整曲库；国内网络可优先使用预设镜像。下载完成后会自动校验并刷新列表。")
        update_copy.setObjectName("MutedText")
        update_copy.setWordWrap(True)
        update_layout.addWidget(update_copy)

        update_controls = QHBoxLayout()
        self.update_mirror_combo = QComboBox()
        self.update_mirror_combo.setAccessibleName("曲库下载线路")
        saved_mirror = self.config.get("update_mirror", GITHUB_MIRRORS[0][1])
        selected_mirror_index = 0
        for index, (label, prefix) in enumerate(GITHUB_MIRRORS):
            self.update_mirror_combo.addItem(label, prefix)
            if prefix == saved_mirror:
                selected_mirror_index = index
        self.update_mirror_combo.setCurrentIndex(selected_mirror_index)
        self.update_mirror_combo.currentIndexChanged.connect(self.save_config)
        self.update_button = QPushButton("检查并更新")
        self.update_button.setObjectName("PrimaryButton")
        self.update_button.clicked.connect(self.start_sheet_update)
        update_controls.addWidget(self.update_mirror_combo, 1)
        update_controls.addWidget(self.update_button)
        update_layout.addLayout(update_controls)

        self.update_progress = QProgressBar()
        self.update_progress.setRange(0, 100)
        self.update_progress.setValue(0)
        self.update_progress.setTextVisible(False)
        self.update_status_label = QLabel("尚未检查更新")
        self.update_status_label.setObjectName("MutedText")
        self.update_status_label.setWordWrap(True)
        update_layout.addWidget(self.update_progress)
        update_layout.addWidget(self.update_status_label)
        help_layout.addWidget(update_card)
        help_layout.addStretch()
        self.overlay = FloatingControl(self)

    def apply_style(self):
        self.setStyleSheet("""
            QMainWindow, #PlayPage, #HelpPage { background: #090F1B; }
            QWidget { color: #E7EEF9; font-family: "Microsoft YaHei UI", "Microsoft YaHei"; font-size: 10pt; }
            QTabWidget::pane { border: 0; background: #090F1B; }
            QTabBar::tab { background: transparent; color: #8290A8; padding: 10px 22px; border-bottom: 2px solid transparent; font-weight: 700; }
            QTabBar::tab:hover { color: #C8D5E8; }
            QTabBar::tab:selected { color: #78A5FF; border-bottom-color: #5B8CFF; }
            #TopBar { background: transparent; border: 0; }
            #BrandTitle { color: #F5F8FD; font-size: 18pt; font-weight: 800; }
            #MutedText { color: #8796AF; }
            #ShortcutHint, #CountBadge, #ValueBadge { background: #17243A; color: #9CB9F1; border: 1px solid #263A59; border-radius: 11px; padding: 3px 9px; font-weight: 700; }
            #Panel, #Card, #ControlCard { background: #111A2A; border: 1px solid #202E45; border-radius: 12px; }
            #NowPlayingCard { background: #132039; border: 1px solid #2B4774; border-radius: 14px; }
            #SectionTitle, #CardTitle { color: #F0F5FC; font-size: 11pt; font-weight: 800; }
            #Eyebrow { color: #73A0FA; font-size: 9pt; font-weight: 800; }
            #SongTitle { color: #FFFFFF; font-size: 20pt; font-weight: 800; }
            #FieldLabel { color: #7F8DA5; font-weight: 700; min-width: 38px; }
            #Filename { color: #9AA9BF; }
            #ValueLabel { color: #B9C8DE; font-weight: 700; }
            #RightScroll, #RightContent { background: transparent; border: 0; }
            QSplitter::handle { background: transparent; width: 8px; }
            QLineEdit, QComboBox { background: #0C1422; border: 1px solid #26354D; border-radius: 8px; padding: 8px 10px; selection-background-color: #527FE0; }
            QLineEdit:focus, QComboBox:focus { border-color: #5B8CFF; }
            QComboBox::drop-down { border: 0; width: 24px; }
            QComboBox QAbstractItemView { background: #111A2A; color: #E7EEF9; border: 1px solid #2A3B56; selection-background-color: #294A7F; outline: 0; }
            QListWidget { background: #0C1422; border: 1px solid #202E45; border-radius: 9px; padding: 5px; outline: 0; }
            QListWidget::item { color: #B7C4D8; border-radius: 7px; padding: 9px 10px; }
            QListWidget::item:hover { background: #16243A; color: #EAF1FB; }
            QListWidget::item:selected { background: #274F8D; color: #FFFFFF; }
            QPushButton { background: #17243A; color: #D7E2F2; border: 1px solid #2A3B56; border-radius: 8px; padding: 8px 12px; font-weight: 700; }
            QPushButton:hover { background: #21334F; border-color: #3B5680; }
            QPushButton:pressed { background: #122038; }
            QPushButton:disabled { color: #58667B; background: #101827; border-color: #1B273A; }
            QPushButton#PrimaryButton, QPushButton#OverlayPrimary { background: #5B8CFF; color: #07101E; border-color: #6D99FF; }
            QPushButton#PrimaryButton:hover, QPushButton#OverlayPrimary:hover { background: #74A0FF; }
            QPushButton#DangerButton { background: #2B1A24; color: #FF9BA9; border-color: #5A2A38; }
            QPushButton#DangerButton:hover { background: #40202D; }
            QSlider::groove:horizontal { height: 6px; background: #26344B; border-radius: 3px; }
            QSlider::sub-page:horizontal { background: #5B8CFF; border-radius: 3px; }
            QSlider::handle:horizontal { background: #DDE8FF; border: 3px solid #5B8CFF; width: 14px; margin: -7px 0; border-radius: 10px; }
            QProgressBar { border: 0; background: #26344B; border-radius: 5px; height: 9px; text-align: center; color: transparent; }
            QProgressBar::chunk { background: #5B8CFF; border-radius: 5px; }
            #Status { background: #0D1727; color: #8FADE2; border: 1px solid #1E304B; border-radius: 8px; padding: 9px 12px; font-weight: 700; }
            QScrollBar:vertical { background: transparent; width: 10px; margin: 3px; }
            QScrollBar::handle:vertical { background: #31415A; min-height: 30px; border-radius: 4px; }
            QScrollBar::handle:vertical:hover { background: #435879; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QMenu { background: #111A2A; color: #E7EEF9; border: 1px solid #2A3B56; padding: 5px; }
            QMenu::item { padding: 7px 20px; border-radius: 5px; }
            QMenu::item:selected { background: #294A7F; }
            QToolTip { background: #17243A; color: #F5F8FD; border: 1px solid #334868; padding: 5px; }
            #FloatingControl { background: transparent; }
            #OverlaySurface { background: #0D1625; color: #E7EEF9; border: 1px solid #314463; border-radius: 14px; }
            #OverlaySurface QLabel { background: transparent; color: #DCE6F5; }
            #OverlayNowPlaying { background: #111E32; border: 1px solid #263A58; border-radius: 10px; }
            #OverlaySurface QPushButton { background: #1A2A43; color: white; border: 1px solid #304667; padding: 6px 10px; }
            #OverlaySurface QPushButton:hover { background: #243958; border-color: #44618D; }
            #OverlaySurface QComboBox { background: #111D30; color: white; border: 1px solid #304667; padding: 6px 8px; }
            #OverlaySurface QSlider::groove:horizontal { height: 5px; background: #26344B; border-radius: 2px; }
            #OverlaySurface QSlider::sub-page:horizontal { background: #5B8CFF; border-radius: 2px; }
            #OverlaySurface QSlider::handle:horizontal { background: #E4ECFF; border: 2px solid #5B8CFF; width: 11px; margin: -5px 0; border-radius: 7px; }
            #OverlayTitle { color: #FFFFFF; font-size: 11pt; font-weight: 800; }
            #OverlayBadge, #OverlayPercent { background: #1A2B46; color: #9CB9F1; border: 1px solid #304767; border-radius: 9px; padding: 2px 7px; font-size: 9pt; font-weight: 700; }
            #OverlaySong { color: #F4F7FC; font-size: 12pt; font-weight: 800; }
            #OverlayTime, #OverlayKeys, #OverlayStatus, #OverlayFieldLabel { color: #91A2BB; font-size: 9pt; }
            #OverlayKeys { color: #B9C9DF; }
            #OverlayStatus { padding-left: 2px; }
            #OverlayClose { color: #AAB8CD; background: transparent; }
            QPushButton#OverlayPrimary { background: #5B8CFF; color: #07101E; border-color: #6D99FF; }
            QPushButton#OverlayDanger { background: #2B1A24; color: #FF9BA9; border-color: #5A2A38; }
            QPushButton#OverlayDanger:hover { background: #40202D; }
        """)
        if hasattr(self, "overlay"):
            self.overlay.setStyleSheet(self.styleSheet())

    def refresh_files(self):
        self.all_files = sorted([p.name for p in SHEET_MUSIC_DIR.glob("*.json")], key=str.lower)
        self.refresh_list()
        if self.all_files and not self.selected_file:
            self.select_file(self.all_files[0], sync_list=True)

    def refresh_files_if_changed(self):
        files = sorted([p.name for p in SHEET_MUSIC_DIR.glob("*.json")], key=str.lower)
        if files != self.all_files:
            current = self.selected_file
            self.all_files = files
            self.refresh_list()
            if current in self.all_files:
                self.select_file(current, sync_list=True)
            elif self.all_files:
                self.select_file(self.all_files[0], sync_list=True)
            self.overlay.refresh()

    @staticmethod
    def display_song_name(filename):
        name = Path(filename).stem.replace("_", " ")
        return " ".join(name.split()) or Path(filename).stem

    def filtered_files(self):
        keyword = self.search.text().strip().lower()
        files = [
            filename for filename in self.all_files
            if not keyword
            or keyword in filename.lower()
            or keyword in self.display_song_name(filename).lower()
        ]
        if self.mode_tabs.currentText() == "收藏曲谱":
            files = [f for f in files if f in self.favorites]
        return files

    def refresh_list(self):
        current = self.selected_file
        self.display_files = self.filtered_files()
        if hasattr(self, "song_count_label"):
            self.song_count_label.setText(f"{len(self.display_files)} / {len(self.all_files)} 首")
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for filename in self.display_files:
            title = self.display_song_name(filename)
            item = QListWidgetItem(title)
            if filename in self.favorites:
                item.setText("已收藏  " + title)
            item.setToolTip(filename)
            item.setData(Qt.UserRole, filename)
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)
        if current in self.display_files:
            self.select_file(current, sync_list=True)
        self.overlay.refresh() if hasattr(self, "overlay") else None

    def show_song_menu(self, pos):
        item = self.list_widget.itemAt(pos)
        if not item:
            return
        filename = item.data(Qt.UserRole)
        menu = QMenu(self)
        label = "取消收藏" if filename in self.favorites else "收藏"
        action = QAction(label, self)
        action.triggered.connect(lambda: self.toggle_favorite(filename))
        menu.addAction(action)
        menu.exec(self.list_widget.mapToGlobal(pos))

    def toggle_favorite(self, filename):
        if filename in self.favorites:
            self.favorites.remove(filename)
        else:
            self.favorites.add(filename)
        write_json(FAVORITES_FILE, sorted(self.favorites))
        self.refresh_list()
        self.overlay.refresh()

    def on_list_selection(self):
        items = self.list_widget.selectedItems()
        if items:
            self.select_file(items[0].data(Qt.UserRole), sync_list=False)

    def select_file(self, filename, sync_list=True):
        if filename not in self.all_files:
            return
        self.selected_file = filename
        try:
            self.meta, notes = parse_music_file(filename)
        except Exception as exc:
            QMessageBox.critical(self, "错误", str(exc))
            return
        self.bpm = int(self.meta.get("bpm", 120) or 120)
        self.notes_by_time = defaultdict(list)
        for note in notes:
            try:
                self.notes_by_time[int(note["time"])].append(note["key"])
            except Exception:
                pass
        self.sorted_times = sorted(self.notes_by_time.keys())
        self.name_label.setText(self.meta.get("songName") or self.meta.get("name") or self.display_song_name(filename))
        self.author_label.setText(self.meta.get("author") or "未知作者")
        self.transcribed_label.setText(self.meta.get("transcribedBy") or self.meta.get("transcriber") or "未署名")
        self.filename_label.setText(filename)
        self.song_summary_label.setText(f"{len(notes):,} 个音符 · {len(self.sorted_times):,} 个演奏时点")
        self.overlay.song_label.setText(self.name_label.text() or Path(filename).stem)
        self.apply_speed_preset(filename)
        self.on_speed_changed()
        self.set_progress(0)
        self.update_total_time()
        self.set_status(f"已选择乐谱: {filename}")
        if sync_list:
            for i in range(self.list_widget.count()):
                if self.list_widget.item(i).data(Qt.UserRole) == filename:
                    self.list_widget.setCurrentRow(i)
                    break
        self.overlay.refresh()

    def overlay_song_files(self):
        if self.overlay_mode == "收藏":
            return [f for f in self.all_files if f in self.favorites]
        return list(self.all_files)

    def load_music(self):
        if not self.selected_file:
            QMessageBox.warning(self, "提示", "请先选择乐谱")
            return False
        if not self.sorted_times:
            self.select_file(self.selected_file, sync_list=False)
        return bool(self.sorted_times)

    def set_status(self, text):
        self.status.setText(text)
        self.overlay.status_label.setText(text)

    def speed(self):
        return max(0.25, self.speed_slider.value() / 100)

    def set_speed(self, value):
        self.speed_slider.setValue(int(value * 100))

    def on_speed_changed(self):
        effective_bpm = self.bpm * self.speed() if self.bpm else 0
        bpm_text = f" · {effective_bpm:g} BPM" if effective_bpm else ""
        self.speed_label.setText(f"{self.speed():.2f}x{bpm_text}")
        self.update_total_time()

    def update_effect_labels(self):
        self.delay_label.setText(f"{self.delay_slider.value()}%")
        self.wrong_label.setText(f"{self.wrong_slider.value()}%")

    def effective_factor(self):
        return 1.0 / self.speed()

    def total_seconds(self):
        if not self.sorted_times:
            return 0
        return max(0, (self.sorted_times[-1] - self.sorted_times[0]) / 1000 * self.effective_factor())

    def format_seconds(self, seconds):
        seconds = max(0, int(seconds))
        m, s = divmod(seconds, 60)
        return f"{m}:{s:02d}"

    def update_total_time(self):
        elapsed = 0
        if self.sorted_times:
            idx = self.progress_to_index(self.progress.value())
            elapsed = (self.sorted_times[idx] - self.sorted_times[0]) / 1000 * self.effective_factor()
        self.time_label.setText(
            f"{self.format_seconds(elapsed)} / {self.format_seconds(self.total_seconds())}"
        )
        if hasattr(self, "overlay"):
            self.overlay.time_label.setText(self.time_label.text())

    def set_progress(self, percent, elapsed=None):
        percent = max(0, min(100, int(percent)))
        if not self.progress.isSliderDown():
            self.progress.blockSignals(True)
            self.progress.setValue(percent)
            self.progress.blockSignals(False)
        self.percent_label.setText(f"{percent}%")
        if hasattr(self, "overlay"):
            if not self.overlay.progress.isSliderDown():
                self.overlay.progress.setValue(percent)
            self.overlay.percent_label.setText(f"{percent}%")
        if elapsed is not None:
            text = f"{self.format_seconds(elapsed)} / {self.format_seconds(self.total_seconds())}"
            self.time_label.setText(text)
            if hasattr(self, "overlay"):
                self.overlay.time_label.setText(text)

    def progress_to_index(self, percent):
        if not self.sorted_times:
            return 0
        if percent >= 100:
            return len(self.sorted_times) - 1
        first, last = self.sorted_times[0], self.sorted_times[-1]
        target_time = first + (last - first) * max(0, percent) / 100
        return max(0, min(len(self.sorted_times) - 1, bisect_left(self.sorted_times, target_time)))

    def preview_seek_position(self, percent):
        if not self.sorted_times:
            return
        idx = self.progress_to_index(percent)
        elapsed = (self.sorted_times[idx] - self.sorted_times[0]) / 1000 * self.effective_factor()
        self.percent_label.setText(f"{percent}%")
        self.overlay.percent_label.setText(f"{percent}%")
        text = f"{self.format_seconds(elapsed)} / {self.format_seconds(self.total_seconds())}"
        self.time_label.setText(text)
        self.overlay.time_label.setText(text)

    def seek_from_slider(self):
        if not self.sorted_times:
            return
        percent = self.progress.value()
        idx = self.progress_to_index(percent)
        elapsed = (self.sorted_times[idx] - self.sorted_times[0]) / 1000 * self.effective_factor()
        is_active = self.previewing or bool(self.player_thread and self.player_thread.is_alive())
        if is_active:
            with self.seek_lock:
                self.seek_request_index = idx
            self.release_all_keys()
            self.set_status(f"已跳转到 {self.format_seconds(elapsed)}")
        else:
            self.set_status(f"已定位到 {self.format_seconds(elapsed)}")
        self.set_progress(percent, elapsed)

    def apply_speed_preset(self, filename):
        try:
            if filename in self.speed_presets:
                self.set_speed(float(self.speed_presets[filename]))
        except Exception:
            pass

    def save_speed_preset(self):
        if not self.selected_file:
            return
        self.speed_presets[self.selected_file] = round(self.speed(), 3)
        self.save_config()
        self.set_status(f"已保存本曲速度：{self.speed_label.text()}")

    def clear_speed_preset(self):
        if self.selected_file in self.speed_presets:
            del self.speed_presets[self.selected_file]
            self.save_config()
        self.set_status("已清除本曲速度预设")

    def apply_wrong_key(self, note_key):
        if self.wrong_slider.value() <= 0 or random.random() * 100 >= self.wrong_slider.value():
            return note_key
        try:
            idx = int(note_key.split("Key", 1)[1])
        except Exception:
            return note_key
        candidates = []
        if idx > 0:
            candidates.append(idx - 1)
        if idx < 14:
            candidates.append(idx + 1)
        if not candidates:
            return note_key
        return note_key.split("Key", 1)[0] + "Key" + str(random.choice(candidates))

    def start_play(self):
        if self.player_thread and self.player_thread.is_alive():
            return
        if self.previewing:
            self.stop_preview()
        if not self.load_music():
            return
        start_idx = self.progress_to_index(self.progress.value())
        self.stop_event.clear()
        with self.seek_lock:
            self.seek_request_index = None
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.player_thread = threading.Thread(target=self.play_loop, args=(start_idx, False), daemon=True)
        self.player_thread.start()

    def stop_play(self):
        self.stop_event.set()
        with self.seek_lock:
            self.seek_request_index = None
        self.previewing = False
        self.release_all_keys()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.preview_btn.setText("预览")
        self.overlay.preview_btn.setText("预览")
        self.set_status("已停止")

    def force_stop(self):
        self.stop_play()
        self.set_progress(0, 0)
        self.overlay.keys_label.setText("当前按键：—")
        self.set_status("ESC 已停止")

    def toggle_preview(self):
        if self.previewing:
            self.stop_preview()
        else:
            self.start_preview()

    def start_preview(self):
        if self.player_thread and self.player_thread.is_alive():
            QMessageBox.warning(self, "提示", "演奏中不能预览")
            return
        if not self.load_music():
            return
        self.previewing = True
        self.stop_event.clear()
        self.stop_btn.setEnabled(True)
        self.preview_btn.setText("停止预览")
        self.overlay.preview_btn.setText("停止")
        start_idx = self.progress_to_index(self.progress.value())
        with self.seek_lock:
            self.seek_request_index = None
        threading.Thread(target=self.play_loop, args=(start_idx, True), daemon=True).start()

    def stop_preview(self):
        self.stop_event.set()
        with self.seek_lock:
            self.seek_request_index = None
        self.previewing = False
        self.preview_btn.setText("预览")
        self.overlay.preview_btn.setText("预览")
        self.stop_btn.setEnabled(False)
        self.set_status("预览已停止")

    def play_loop(self, start_idx, preview):
        total = len(self.sorted_times)
        t0 = self.sorted_times[0]
        if not preview:
            for remaining in (3, 2, 1):
                if self.stop_event.is_set():
                    self.signals.finished.emit(True)
                    return
                self.signals.status.emit(f"{remaining} 秒后开始，请切到目标窗口")
                time.sleep(1)
        idx = max(0, min(total - 1, start_idx))
        while idx < total and not self.stop_event.is_set():
            with self.seek_lock:
                if self.seek_request_index is not None:
                    idx = max(0, min(total - 1, self.seek_request_index))
                    self.seek_request_index = None
            t = self.sorted_times[idx]
            note_keys = [self.apply_wrong_key(k) for k in self.notes_by_time[t]]
            if preview:
                self.preview_note(note_keys)
            else:
                self.press_notes(note_keys)
                time.sleep(0.05)
                self.release_notes(note_keys)
            elapsed = max(0, (t - t0) / 1000 * self.effective_factor())
            duration_ms = max(1, self.sorted_times[-1] - t0)
            percent = int((t - t0) / duration_ms * 100)
            self.signals.progress.emit(percent, self.format_seconds(elapsed), f"{idx+1}/{total}", note_keys)
            if idx < total - 1:
                interval = (self.sorted_times[idx + 1] - t) / 1000 * self.effective_factor()
                if self.delay_slider.value() > 0:
                    interval *= 1 + random.uniform(-self.delay_slider.value(), self.delay_slider.value()) / 100
                end = time.time() + max(0, interval)
                while time.time() < end:
                    if self.stop_event.is_set():
                        break
                    with self.seek_lock:
                        if self.seek_request_index is not None:
                            break
                    time.sleep(min(0.03, end - time.time()))
            idx += 1
        self.signals.finished.emit(self.stop_event.is_set())

    def on_worker_progress(self, percent, elapsed, count_text, note_keys):
        self.set_progress(percent)
        text = f"{elapsed} / {self.format_seconds(self.total_seconds())}"
        self.time_label.setText(text)
        self.overlay.time_label.setText(text)
        mapped = [NOTE_TO_KEY.get(k, "") for k in note_keys]
        self.overlay.keys_label.setText("当前按键：" + ("  ".join(k for k in mapped if k) or "—"))
        self.set_status(("预览进度: " if self.previewing else "演奏进度: ") + count_text)

    def on_worker_finished(self, stopped):
        self.release_all_keys()
        self.previewing = False
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.preview_btn.setText("预览")
        self.overlay.preview_btn.setText("预览")
        self.overlay.keys_label.setText("当前按键：—")
        self.set_status("已停止" if stopped else "完成")
        if not stopped:
            self.set_progress(100, self.total_seconds())

    def press_notes(self, note_keys):
        for note in note_keys:
            key = NOTE_TO_KEY.get(note)
            if key and send_scan_key(key, False):
                self.pressed_keys.add(key)

    def release_notes(self, note_keys):
        for note in note_keys:
            key = NOTE_TO_KEY.get(note)
            if key:
                send_scan_key(key, True)
                self.pressed_keys.discard(key)

    def release_all_keys(self):
        for key in set(self.pressed_keys) | set(KEY_TO_SCANCODE):
            send_scan_key(key, True)
        self.pressed_keys.clear()

    def preview_note_frequency(self, note_key):
        try:
            idx = int(note_key.split("Key", 1)[1])
            return 261.63 * (2 ** (SKY_MAJOR[idx] / 12))
        except Exception:
            return None

    def preview_wave_path(self, note_keys):
        keys = tuple(sorted(set(k for k in note_keys if self.preview_note_frequency(k))))
        if not keys:
            return None
        if keys in self.preview_cache and self.preview_cache[keys].exists():
            return self.preview_cache[keys]
        sample_rate = 44100
        duration = 0.32
        attack = 0.012
        frames = []
        freqs = [self.preview_note_frequency(k) for k in keys]
        for i in range(int(sample_rate * duration)):
            t = i / sample_rate
            envelope = (t / attack) if t < attack else math.exp(-7.2 * (t - attack))
            value = 0.0
            for freq in freqs:
                for mult, amp in ((1, 1.0), (2, 0.34), (3, 0.16), (4, 0.07)):
                    value += amp * math.sin(2 * math.pi * freq * mult * t)
            value = value / max(1, len(freqs)) * envelope * 0.42
            frames.append(struct.pack("<h", int(max(-1, min(1, value)) * 32767)))
        path = self.preview_dir / ("preview_" + "_".join(k.replace("Key", "k") for k in keys) + ".wav")
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(b"".join(frames))
        self.preview_cache[keys] = path
        return path

    def preview_note(self, note_keys):
        try:
            import winsound
            path = self.preview_wave_path(note_keys)
            if path:
                winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
        except Exception:
            QApplication.beep()

    def toggle_overlay(self):
        if self.overlay.isVisible():
            self.overlay.hide()
            self.overlay_btn.setText("显示悬浮窗")
        else:
            self.overlay.refresh()
            self.overlay.show()
            self.overlay.raise_()
            self.overlay_btn.setText("隐藏悬浮窗")

    def toggle_main_window(self):
        self.setVisible(not self.isVisible())

    def start_sheet_update(self):
        if self.update_running:
            return
        self.update_running = True
        self.update_button.setEnabled(False)
        self.update_mirror_combo.setEnabled(False)
        self.update_progress.setRange(0, 0)
        self.update_status_label.setText("正在获取曲库版本信息…")
        mirror_prefix = self.update_mirror_combo.currentData() or ""

        def progress(percent, downloaded, total):
            if total:
                downloaded_mb = downloaded / (1024 * 1024)
                total_mb = total / (1024 * 1024)
                message = f"正在下载 {downloaded_mb:.1f} / {total_mb:.1f} MB"
            else:
                message = f"正在下载 {downloaded / (1024 * 1024):.1f} MB"
            self.update_signals.progress.emit(percent, message)

        def worker():
            try:
                result = install_sheet_update(APP_DIR, mirror_prefix, progress)
                self.update_signals.finished.emit(result)
            except Exception as exc:
                self.update_signals.failed.emit(str(exc))

        self.update_thread = threading.Thread(target=worker, daemon=True)
        self.update_thread.start()

    def on_update_progress(self, percent, message):
        if percent > 0:
            self.update_progress.setRange(0, 100)
            self.update_progress.setValue(percent)
        self.update_status_label.setText(message)

    def on_update_finished(self, result):
        self.update_running = False
        self.update_button.setEnabled(True)
        self.update_mirror_combo.setEnabled(True)
        self.update_progress.setRange(0, 100)
        self.update_progress.setValue(100)
        version = result.get("version", "未知")
        count = int(result.get("count", 0))
        self.update_version_label.setText(f"当前：{version}")
        if result.get("updated"):
            self.update_status_label.setText(f"更新完成：已安装 {count} 首曲谱。")
            self.refresh_files()
        else:
            self.update_status_label.setText(f"已经是最新曲库，共 {count} 首曲谱。")

    def on_update_failed(self, message):
        self.update_running = False
        self.update_button.setEnabled(True)
        self.update_mirror_combo.setEnabled(True)
        self.update_progress.setRange(0, 100)
        self.update_progress.setValue(0)
        self.update_status_label.setText(f"更新失败：{message}")
        QMessageBox.warning(self, "曲库更新失败", f"{message}\n\n请切换下载线路后重试。")

    def save_config(self):
        geo = self.geometry()
        data = {
            "width": geo.width(),
            "height": geo.height(),
            "x": geo.x(),
            "y": geo.y(),
            "speed": self.speed(),
            "speed_logic_version": self.speed_logic_version,
            "random_delay_percent": self.delay_slider.value(),
            "wrong_key_percent": self.wrong_slider.value(),
            "speed_presets": self.speed_presets,
            "overlay_music_mode": self.overlay_mode,
            "update_mirror": self.update_mirror_combo.currentData() if hasattr(self, "update_mirror_combo") else GITHUB_MIRRORS[0][1],
        }
        if self.overlay:
            og = self.overlay.geometry()
            data["progress_geometry"] = f"{og.width()}x{og.height()}+{og.x()}+{og.y()}"
        write_json(CONFIG_FILE, data)

    def closeEvent(self, event: QCloseEvent):
        self.stop_play()
        if self.global_escape_registered:
            try:
                import keyboard
                keyboard.unhook_all_hotkeys()
            except Exception:
                pass
        self.save_config()
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    x = window.config.get("x")
    y = window.config.get("y")
    if isinstance(x, int) and isinstance(y, int):
        window.move(x, y)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
