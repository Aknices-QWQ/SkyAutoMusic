import ctypes
import json
import math
import os
import random
import re
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from bisect import bisect_left
from collections import defaultdict
from ctypes import wintypes
from pathlib import Path
from urllib.parse import urlencode

from PySide6.QtCore import QFile, QObject, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QCloseEvent, QCursor, QDesktopServices, QIcon, QPainter, QPixmap, QShortcut, QKeySequence
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer, QSoundEffect
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
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
    QStackedWidget,
    QStyle,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sheet_updater import (
    GITHUB_MIRRORS,
    import_sheet_files,
    install_external_source,
    install_sheet_update,
    read_local_state,
)

import piastudy
from app_updater import APP_VERSION, compare_versions, fetch_latest_release
from midi_practice import MidiPracticePage
from midi_sky import MidiSkyPanel
from floating_score import FloatingScoreView
from mobile_remote import MobileRemoteServer, create_pairing_token
from score_editor import ScoreEditorPage
from score_playback import PlaybackSession
from section_editor import SectionEditor
from song_sections import estimate_sections, note_groups, score_end_ms, score_fingerprint, validate_sections
from sky_sounds import (
    INSTRUMENT_CUSTOM_ICONS, INSTRUMENT_ICON_CODES, INSTRUMENT_LABELS, LOCATION_PRESETS, PITCHES,
    SAMPLE_COUNTS, SPECY_INSTRUMENTS, SOURCE_URL, download_specy_instrument,
    instrument_folders, pitch_semitones, pitched_wave, sample_files,
)
from realtime_recognizer import RealtimeRecognizer, list_loopback_devices


def executable_path():
    """Absolute path of the running program.

    In a Nuitka standalone build ``sys.executable`` points at a ``python.exe``
    that does not exist on disk, so asking Windows for our own module file name
    is the only reliable way to locate the real binary. The administrative
    relaunch depends on this path, otherwise ShellExecuteW fails with
    ERROR_FILE_NOT_FOUND and elevation silently never happens.
    """
    if os.name == "nt":
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            if ctypes.windll.kernel32.GetModuleFileNameW(None, buffer, 32768):
                candidate = Path(buffer.value)
                if candidate.exists():
                    return candidate
        except Exception:
            pass
    candidate = Path(sys.executable)
    if candidate.exists():
        return candidate
    if sys.argv and sys.argv[0]:
        candidate = Path(os.path.abspath(sys.argv[0]))
        if candidate.exists():
            return candidate
    return Path(sys.executable)


if "__compiled__" in globals() or getattr(sys, "frozen", False):
    APP_DIR = executable_path().resolve().parent
else:
    APP_DIR = Path(__file__).resolve().parent
SHEET_MUSIC_DIR = APP_DIR / "Sheet Music"
ASSET_DIR = APP_DIR / "assets"
CONFIG_FILE = APP_DIR / "config.json"
FAVORITES_FILE = APP_DIR / "favorites.json"
SHEET_MUSIC_DIR.mkdir(exist_ok=True)

PROJECT_SITE = "https://sky.xxlab.dev/"


def clean_song_name(text):
    """Remove catalog prefixes from user-facing song names without renaming files."""
    value = str(text or "").replace("_", " ").strip()
    value = re.sub(r"(?i)^sky\d+\s*[-–—]\s*", "", value)
    return " ".join(value.split())


_SEARCH_STRIP = re.compile(r"[\s\-_·、。，,.()（）\[\]【】'\"!！?？~～:：;；/\\|]+")


def normalize_search_text(text):
    """Lower-case and drop spacing/punctuation so names match loosely."""
    return _SEARCH_STRIP.sub("", str(text or "").lower())


def is_admin():
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def request_admin_restart():
    """Request a single elevated restart. A cancelled UAC prompt keeps this process alive."""
    if os.name != "nt" or is_admin():
        return False
    if "__compiled__" in globals() or getattr(sys, "frozen", False):
        executable = str(executable_path().resolve())
        parameters = ""
    else:
        # Running from source: sys.executable is the interpreter that owns the
        # script (and its virtualenv), so keep it.
        executable = str(Path(sys.executable).resolve())
        parameters = subprocess.list2cmdline([str(Path(__file__).resolve()), *sys.argv[1:]])
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", executable, parameters, str(APP_DIR), 1
        )
        return int(result) > 32
    except Exception:
        return False

THEMES = {
    "carbon": {
        "label": "中性黑灰",
        "description": "克制的无彩色深色主题",
        "replacements": {
            "#090C12": "#0A0B0C", "#0C1118": "#101214", "#0D121A": "#0D0F11",
            "#0E141D": "#101214", "#0F151E": "#111315", "#111722": "#151719",
            "#151C27": "#1B1E21", "#172131": "#25282C", "#1B2330": "#202327",
            "#1C2532": "#24282C", "#243250": "#2A2E33", "#252E3C": "#2B2F34",
            "#2B3445": "#34383D", "#344054": "#3C4147", "#354054": "#41464C",
            "#3A4659": "#454A50", "#3D485C": "#4B5057", "#3F68C7": "#70757B",
            "#48556C": "#585E65", "#49566C": "#5A6067", "#4A5870": "#60666D",
            "#5B86FF": "#E3E5E7", "#6B92FF": "#F4F5F6", "#6D94FF": "#FFFFFF",
            "#776BFF": "#FFFFFF", "#9DB8FF": "#D3D6D9", "#ABC0FF": "#D8DBDE",
            "#F8FAFF": "#111315",
        },
    },
    "warm": {
        "label": "暖石墨",
        "description": "低饱和暖灰与柔和琥珀",
        "replacements": {
            "#090C12": "#121110", "#0C1118": "#181614", "#0D121A": "#171513",
            "#0E141D": "#1A1815", "#0F151E": "#1C1917", "#10151D": "#1C1917",
            "#101620": "#201C19", "#111722": "#211E1B", "#151C27": "#2B2723",
            "#172131": "#3A3026", "#1B2330": "#302A25", "#1C2532": "#352F29",
            "#202837": "#332D28", "#243250": "#42372C", "#252E3C": "#3A332D",
            "#2B3445": "#4A433D", "#344054": "#5A5149", "#354054": "#62584F",
            "#3A4659": "#675D54", "#3D485C": "#74695E", "#3F68C7": "#A8794F",
            "#48556C": "#85786C", "#49566C": "#817468", "#4A5870": "#8B7D70",
            "#596475": "#746B63", "#5B86FF": "#C99763", "#6B92FF": "#D8A870",
            "#6D94FF": "#E1B37D", "#776BFF": "#E2B983", "#98A3B4": "#B0A79C",
            "#9DB8FF": "#D4B48F", "#ABC0FF": "#DEC09A", "#B5BECC": "#C7BFB5",
            "#C2CAD7": "#D6CEC4", "#C7CFDC": "#DED6CC", "#C8D0DC": "#E0D8CE",
            "#DCE3EE": "#EEE7DE", "#DCE4F2": "#F0E9DF", "#E2E7EF": "#F1EBE3",
            "#F5F7FB": "#F4EFE7", "#F8FAFF": "#171310",
        },
    },
    "editorial": {
        "label": "黑白浅色",
        "description": "明亮、清晰的编辑式黑白主题",
        "replacements": {
            "#090C12": "#F4F3F0", "#0C1118": "#FAFAF8", "#0D121A": "#FCFBF9",
            "#0E141D": "#F8F7F4", "#0F151E": "#F7F6F3", "#10151D": "#ECEAE6",
            "#101620": "#E7E5E0", "#111722": "#FFFFFF", "#151C27": "#F0EFEC",
            "#172131": "#1E1E1E", "#1B2330": "#E7E5E0", "#1C2532": "#E5E3DE",
            "#202837": "#D8D6D1", "#243250": "#D9D7D2", "#252E3C": "#DCD9D3",
            "#2B3445": "#C9C7C2", "#344054": "#B7B4AE", "#354054": "#A8A59F",
            "#3A4659": "#A39F98", "#3D485C": "#AAA7A0", "#3F68C7": "#4C4C4C",
            "#48556C": "#99968E", "#49566C": "#8F8C85", "#4A5870": "#87847D",
            "#596475": "#9A9892", "#5B86FF": "#171717", "#6B92FF": "#292929",
            "#6D94FF": "#303030", "#776BFF": "#4B4B4B", "#98A3B4": "#6A6863",
            "#9DB8FF": "#3D3D3D", "#ABC0FF": "#424242", "#B5BECC": "#55534F",
            "#C2CAD7": "#494743", "#C7CFDC": "#3B3A37", "#C8D0DC": "#383734",
            "#DCE3EE": "#2F2E2B", "#DCE4F2": "#292825", "#E2E7EF": "#252421",
            "#F5F7FB": "#171717", "#F8FAFF": "#FFFFFF", "#78CFA0": "#222222",
        },
    },
}

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
    start_requested = Signal()
    play_stop_requested = Signal()
    stop_requested = Signal()
    toggle_overlay_requested = Signal()
    mobile_action = Signal(dict)
    preview_requested = Signal(list)
    score_step_requested = Signal(int)
    overlay_lock_requested = Signal()
    score_scroll_requested = Signal()
    playback_event = Signal(int, str, object)


class SkySoundSignals(QObject):
    progress = Signal(int, int)
    finished = Signal(str)
    failed = Signal(str)


class UpdateSignals(QObject):
    progress = Signal(int, str)
    finished = Signal(dict)
    failed = Signal(str)


class AppUpdateSignals(QObject):
    finished = Signal(dict)
    failed = Signal(str)


class WindowTitleBar(QFrame):
    """Compact custom title bar that keeps native move/maximize behavior."""

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.drag_origin = None
        self.setObjectName("WindowTitleBar")
        self.setFixedHeight(44)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 6, 0)
        layout.setSpacing(4)

        brand = QLabel("SkyAutoMusic")
        brand.setObjectName("TitleBarBrand")
        layout.addWidget(brand)
        layout.addStretch()

        style = self.style()
        self.minimize_btn = QToolButton()
        self.maximize_btn = QToolButton()
        self.close_btn = QToolButton()
        self.minimize_btn.setIcon(style.standardIcon(QStyle.SP_TitleBarMinButton))
        self.maximize_btn.setIcon(style.standardIcon(QStyle.SP_TitleBarMaxButton))
        self.close_btn.setIcon(style.standardIcon(QStyle.SP_TitleBarCloseButton))
        self.close_btn.setObjectName("TitleBarClose")
        for button, label in (
            (self.minimize_btn, "最小化"),
            (self.maximize_btn, "最大化"),
            (self.close_btn, "关闭"),
        ):
            button.setAccessibleName(label)
            button.setToolTip(label)
            button.setFixedSize(42, 34)
            button.setIconSize(QSize(13, 13))
            layout.addWidget(button)

        self.minimize_btn.clicked.connect(window.showMinimized)
        self.maximize_btn.clicked.connect(self.toggle_maximized)
        self.close_btn.clicked.connect(window.close)

    def toggle_maximized(self):
        if self.window.isMaximized():
            self.window.showNormal()
        else:
            self.window.showMaximized()
        icon = QStyle.SP_TitleBarNormalButton if self.window.isMaximized() else QStyle.SP_TitleBarMaxButton
        self.maximize_btn.setIcon(self.style().standardIcon(icon))

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_origin = event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()
            if self.window.windowHandle() and self.window.windowHandle().startSystemMove():
                self.drag_origin = None
            event.accept()

    def mouseMoveEvent(self, event):
        if self.drag_origin is not None and event.buttons() & Qt.LeftButton:
            if self.window.isMaximized():
                self.window.showNormal()
            self.window.move(event.globalPosition().toPoint() - self.drag_origin)
            event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.toggle_maximized()
            event.accept()


class FloatingControl(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.drag_pos = None
        self._song_files_cache = []
        self.setWindowTitle("悬浮控制")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setObjectName("FloatingControl")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(7, 7, 7, 10)

        surface = QFrame()
        surface.setObjectName("OverlaySurface")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(14)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(0, 0, 0, 130))
        surface.setGraphicsEffect(shadow)
        outer.addWidget(surface)

        root = QVBoxLayout(surface)
        root.setContentsMargins(10, 9, 10, 9)
        root.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(6)
        self.song_label = QLabel("未选择曲谱")
        self.song_label.setObjectName("OverlaySong")
        self.song_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.song_label.setMinimumWidth(0)
        self.percent_label = QLabel("0%")
        self.percent_label.setObjectName("OverlayPercent")
        self.close_btn = QToolButton()
        self.close_btn.setObjectName("OverlayClose")
        self.close_btn.setIcon(self.style().standardIcon(QStyle.SP_TitleBarCloseButton))
        self.close_btn.setAccessibleName("关闭悬浮窗")
        self.close_btn.setToolTip("关闭悬浮窗 (F3)")
        self.close_btn.setFixedSize(26, 26)
        self.close_btn.setIconSize(QSize(12, 12))
        self.close_btn.clicked.connect(main.toggle_overlay)
        header.addWidget(self.song_label, 1)
        header.addWidget(self.percent_label)
        header.addWidget(self.close_btn)
        root.addLayout(header)

        now_playing = QFrame()
        now_playing.setObjectName("OverlayNowPlaying")
        now_layout = QVBoxLayout(now_playing)
        now_layout.setContentsMargins(0, 0, 0, 0)
        now_layout.setSpacing(3)

        meta_row = QHBoxLayout()
        self.time_label = QLabel("0:00 / 0:00")
        self.time_label.setObjectName("OverlayTime")
        self.keys_label = QLabel("按键：—")
        self.keys_label.setObjectName("OverlayKeys")
        self.keys_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.keys_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        meta_row.addWidget(self.time_label)
        meta_row.addStretch()
        meta_row.addWidget(self.keys_label, 1)
        now_layout.addLayout(meta_row)
        self.progress = QSlider(Qt.Horizontal)
        self.progress.setRange(0, 100)
        self.progress.setAccessibleName("悬浮窗演奏进度")
        self.progress.sliderMoved.connect(self.preview_seek_position)
        self.progress.sliderReleased.connect(self.seek_from_slider)
        now_layout.addWidget(self.progress)
        root.addWidget(now_playing)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.mode = QComboBox()
        self.mode.addItems(["全部", "收藏"])
        self.mode.setFixedWidth(70)
        self.mode.setAccessibleName("曲库范围")
        self.song = QComboBox()
        self.song.setAccessibleName("选择乐谱")
        self.song.setToolTip("输入歌名或文件名，按 Enter 查找")
        self.song.setEditable(True)
        self.song.setInsertPolicy(QComboBox.NoInsert)
        self.song.lineEdit().setPlaceholderText("输入歌名查找")
        self.song.completer().setCaseSensitivity(Qt.CaseInsensitive)
        self.song.completer().setFilterMode(Qt.MatchContains)
        self.song.completer().setCompletionMode(QCompleter.PopupCompletion)
        self.song.lineEdit().returnPressed.connect(self.select_typed_song)
        self.song.setMinimumContentsLength(10)
        self.song.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.mode.currentTextChanged.connect(self.on_mode_changed)
        self.song.currentIndexChanged.connect(self.on_song_changed)
        row.addWidget(self.mode)
        row.addWidget(self.song, 1)
        root.addLayout(row)

        controls = QHBoxLayout()
        controls.setSpacing(6)
        self.start_btn = QPushButton("开始")
        self.start_btn.setObjectName("OverlayPrimary")
        self.preview_btn = QPushButton("预览")
        self.preview_btn.setObjectName("OverlayPreview")
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("OverlayDanger")
        self.main_btn = QPushButton("主窗")
        self.immediate_btn = QPushButton("立即")
        self.immediate_btn.setToolTip("立即演奏，跳过 3 秒倒计时")
        for button in (self.start_btn, self.preview_btn, self.stop_btn, self.main_btn, self.immediate_btn):
            button.setMinimumHeight(30)
        self.start_btn.clicked.connect(main.start_play)
        self.immediate_btn.clicked.connect(lambda: main.start_play(immediate=True))
        self.preview_btn.clicked.connect(main.toggle_preview)
        self.stop_btn.clicked.connect(main.stop_play)
        self.main_btn.clicked.connect(main.toggle_main_window)
        controls.addWidget(self.preview_btn, 1)
        controls.addWidget(self.start_btn, 2)
        controls.addWidget(self.immediate_btn, 1)
        controls.addWidget(self.stop_btn, 1)
        controls.addWidget(self.main_btn, 1)
        root.addLayout(controls)

        guide_controls = QHBoxLayout()
        self.score_button = QPushButton("琴谱")
        self.score_button.setCheckable(True)
        self.score_button.setChecked(bool(main.config.get("overlay_show_score", False)))
        self.score_button.setToolTip("显示光遇 15 键琴谱；F5 上一组，F6 下一组，F9 自动翻谱")
        self.score_button.clicked.connect(self.toggle_score)
        self.lock_button = QPushButton("穿透")
        self.lock_button.setCheckable(True)
        self.lock_button.setToolTip("鼠标穿透到游戏；F4 解除穿透，F3 隐藏悬浮窗")
        self.lock_button.clicked.connect(self.set_input_locked)
        self.midi_button = QPushButton("MIDI 接入")
        self.midi_button.clicked.connect(main.show_game_midi)
        self.opacity = QComboBox()
        self.opacity.setAccessibleName("悬浮窗不透明度")
        for value in (100, 85, 70):
            self.opacity.addItem(f"{value}%", value)
        self.opacity.setCurrentIndex(max(0, self.opacity.findData(main.config.get("overlay_opacity", 100))))
        self.opacity.currentIndexChanged.connect(lambda _: self.setWindowOpacity(self.opacity.currentData() / 100))
        self.setWindowOpacity(self.opacity.currentData() / 100)
        for widget in (self.score_button, self.lock_button, self.midi_button, self.opacity):
            guide_controls.addWidget(widget)
        root.addLayout(guide_controls)

        self.score_panel = QWidget()
        score_layout = QVBoxLayout(self.score_panel)
        score_layout.setContentsMargins(0, 0, 0, 0)
        score_layout.setSpacing(5)
        self.score_view = FloatingScoreView()
        score_layout.addWidget(self.score_view)
        score_actions = QHBoxLayout()
        self.previous_button = QPushButton("上一组")
        self.next_button = QPushButton("下一组")
        self.auto_score_button = QPushButton("自动翻谱")
        self.auto_score_button.setCheckable(True)
        self.midi_follow = QCheckBox("MIDI 跟谱")
        self.midi_follow.setToolTip("接入光遇后，弹对当前音符组再显示下一组；重复音需松开再弹。")
        self.previous_button.clicked.connect(lambda: self.step_score(-1))
        self.next_button.clicked.connect(lambda: self.step_score(1))
        self.auto_score_button.clicked.connect(self.toggle_score_scroll)
        self.midi_follow.toggled.connect(lambda enabled: enabled and self.stop_score_scroll())
        for widget in (self.previous_button, self.next_button, self.auto_score_button, self.midi_follow):
            score_actions.addWidget(widget)
        score_layout.addLayout(score_actions)
        root.addWidget(self.score_panel)
        self.score_panel.setVisible(self.score_button.isChecked())
        self.score_timer = QTimer(self)
        self.score_timer.setSingleShot(True)
        self.score_timer.timeout.connect(self.score_scroll_tick)

        status_row = QHBoxLayout()
        status_row.setSpacing(5)
        self.status_label = QLabel("待机")
        self.status_label.setObjectName("OverlayStatus")
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.status_label.setMaximumHeight(18)
        self.request_btn = QPushButton("求谱")
        self.request_btn.setObjectName("CompactButton")
        self.request_btn.setToolTip("提交给项目维护者扒谱")
        self.request_btn.setAccessibleName("提交扒谱请求")
        self.request_btn.setFixedSize(46, 24)
        self.request_btn.hide()
        self.request_btn.clicked.connect(self.request_current_song)
        status_row.addWidget(self.status_label, 1)
        status_row.addWidget(self.request_btn)
        root.addLayout(status_row)
        self.setMinimumSize(340, 188)
        self.resize(400, 540 if self.score_button.isChecked() else 240)
        self.refresh()

    def toggle_score(self):
        visible = self.score_button.isChecked()
        self.score_panel.setVisible(visible)
        if not visible:
            self.stop_score_scroll()
        self.resize(max(400, self.width()), 540 if visible else 240)
        self.main.save_config()

    def set_input_locked(self, locked):
        if locked and not getattr(self.main, "global_hotkeys_registered", False):
            self.lock_button.setChecked(False)
            self.status_label.setText("全局 F4 不可用，暂时无法开启穿透")
            return
        visible = self.isVisible()
        remaining = self.score_timer.remainingTime()
        self.lock_button.setChecked(bool(locked))
        self.setWindowFlag(Qt.WindowTransparentForInput, bool(locked))
        self.setWindowFlag(Qt.WindowDoesNotAcceptFocus, bool(locked))
        if visible:
            self.show()
            if remaining >= 0:
                self.auto_score_button.setChecked(True)
                self.score_timer.start(max(1, remaining))

    def set_score(self, notes_by_time):
        self.stop_score_scroll()
        self.score_view.cursor.set_score(notes_by_time)
        self.score_view.cursor.held.clear()
        self.score_view.update()

    def step_score(self, step):
        if not self.isVisible() or not self.score_button.isChecked():
            return
        self.stop_score_scroll()
        self.score_view.cursor.move(step)
        self.score_view.update()

    def stop_score_scroll(self):
        self.score_timer.stop()
        self.auto_score_button.setChecked(False)

    def toggle_score_scroll(self):
        if not self.isVisible() or not self.score_button.isChecked():
            self.stop_score_scroll()
            return
        if not self.auto_score_button.isChecked():
            self.stop_score_scroll()
            return
        if self.main.previewing or (self.main.player_thread and self.main.player_thread.is_alive()):
            self.main.stop_play()
        self.auto_score_button.setChecked(True)
        self.midi_follow.setChecked(False)
        cursor = self.score_view.cursor
        if not cursor.groups:
            self.stop_score_scroll()
            return
        if cursor.index >= len(cursor.groups):
            cursor.seek(0)
        self.score_view.update()
        self.schedule_score_tick()

    def schedule_score_tick(self):
        cursor = self.score_view.cursor
        index = cursor.index
        if index >= len(cursor.groups):
            self.stop_score_scroll()
            return
        gap = (cursor.groups[index + 1][0] - cursor.groups[index][0]
               if index + 1 < len(cursor.groups) else 60000 / max(1, self.main.bpm))
        self.score_timer.start(max(1, round(gap / self.main.speed())))

    def score_scroll_tick(self):
        self.score_view.cursor.move(1)
        self.score_view.update()
        self.schedule_score_tick()

    def follow_playback(self, time_ms):
        cursor = self.score_view.cursor
        if cursor.groups:
            cursor.seek(bisect_left([group[0] for group in cursor.groups], time_ms))
            self.score_view.update()

    def feed_game_midi(self, keys):
        if self.midi_follow.isChecked() and self.score_button.isChecked():
            self.score_view.cursor.feed(keys)
            self.score_view.update()

    def hideEvent(self, event):
        self.stop_score_scroll()
        super().hideEvent(event)

    def refresh(self):
        self.mode.blockSignals(True)
        self.mode.setCurrentText(self.main.overlay_mode)
        self.mode.blockSignals(False)
        files = self.main.overlay_song_files()
        current = self.main.selected_file
        if current:
            files = [current, *(filename for filename in files if filename != current)]
        files = files[:300]
        self.song.blockSignals(True)
        if files != self._song_files_cache:
            self.song.clear()
            for filename in files:
                self.song.addItem(self.main.display_song_name(filename), filename)
            self._song_files_cache = list(files)
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

    def select_typed_song(self):
        raw_query = self.song.currentText().strip()
        query = raw_query.lower()
        if not query:
            self.request_btn.hide()
            return
        for index in range(self.song.count()):
            if query in self.song.itemText(index).lower():
                self.song.setCurrentIndex(index)
                self.on_song_changed()
                self.request_btn.hide()
                return
        for filename in self.main.all_files:
            if query in self.main.display_song_name(filename).lower() or query in filename.lower():
                self.main.select_file(filename, sync_list=True)
                self.refresh()
                self.request_btn.hide()
                return
        self.status_label.setText("未找到歌曲")
        self.request_btn.setProperty("song_query", raw_query)
        self.request_btn.show()

    def request_current_song(self):
        query = self.request_btn.property("song_query") or self.song.currentText().strip()
        self.main.open_sheet_request(query)

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


class SheetSearchIndex(QObject):
    """filename -> normalised song/author text, built in the background and cached.

    Lets the library search match on artist or transcriber too, not just the
    file name. Reading ~18k JSON files takes tens of seconds, so the result is
    cached on disk and reused until the library changes.
    """

    ready_changed = Signal()

    def __init__(self, sheet_dir, cache_file, parent=None):
        super().__init__(parent)
        self.sheet_dir = Path(sheet_dir)
        self.cache_file = Path(cache_file)
        self.entries = {}
        self.ready = False
        self._lock = threading.Lock()

    def signature(self):
        """Cheap fingerprint of the library, so a shipped index stays valid."""
        count = 0
        total = 0
        for path in self.sheet_dir.glob("*.json"):
            count += 1
            try:
                total += path.stat().st_size
            except OSError:
                continue
        return f"{count}:{total}"

    def start(self, delay_ms=2500):
        QTimer.singleShot(
            delay_ms, lambda: threading.Thread(target=self._build, daemon=True).start()
        )

    def _build(self):
        try:
            signature = self.signature()
        except Exception:
            signature = ""
        if signature:
            try:
                cached = json.loads(self.cache_file.read_text(encoding="utf-8"))
                if cached.get("signature") == signature and isinstance(cached.get("entries"), dict):
                    with self._lock:
                        self.entries = cached["entries"]
                        self.ready = True
                    self.ready_changed.emit()
                    return
            except Exception:
                pass

        entries = {path.name: self._read_text(path) for path in self.sheet_dir.glob("*.json")}
        with self._lock:
            self.entries = entries
            self.ready = True
        if signature:
            try:
                self.cache_file.write_text(
                    json.dumps({"signature": signature, "entries": entries}, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception:
                pass
        self.ready_changed.emit()

    @staticmethod
    def _read_text(path):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return ""
        if isinstance(data, list) and data and isinstance(data[0], dict):
            meta = data[0]
        elif isinstance(data, dict):
            meta = data
        else:
            return ""
        parts = [meta.get("songName"), meta.get("name"),
                 meta.get("author"), meta.get("transcribedBy")]
        return normalize_search_text(" ".join(str(part) for part in parts if part))

    def text_for(self, filename):
        with self._lock:
            return self.entries.get(filename, "")


class LoopbackScanSignals(QObject):
    finished = Signal(list, str)


class RealtimeRecognizerPage(QWidget):
    """Local computer-audio melody recognition controls."""

    def __init__(self, host, sheet_dir, parent=None):
        super().__init__(parent)
        self.host = host
        self.recognizer = RealtimeRecognizer(sheet_dir, self, APP_DIR / "recognition-index.json.gz")
        self._stopping = self._closing = self._scanned = False
        self._scan_thread = None
        self.scan_signals = LoopbackScanSignals(self)
        self.scan_signals.finished.connect(self.on_devices)
        self._build_ui()
        self.recognizer.status.connect(self.on_status)
        self.recognizer.level.connect(self.on_level)
        self.recognizer.notes_changed.connect(self.notes_label.setText)
        self.recognizer.results.connect(self.on_results)
        self.recognizer.finished.connect(self.on_finished)
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.start_button.clicked.connect(self.start_listening)
        self.stop_button.clicked.connect(self.stop_listening)
        self.results_list.itemDoubleClicked.connect(self.open_result)
        self.open_button.clicked.connect(self.open_selected)
        self.clear_button.clicked.connect(self.clear_listening)
        self.results_list.currentItemChanged.connect(lambda item, _: self.open_button.setEnabled(item is not None))

    def showEvent(self, event):
        super().showEvent(event)
        if not self._scanned:
            self.refresh_devices()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("TranscribePanel")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        body.setObjectName("TranscribePanel")
        body.setAttribute(Qt.WA_StyledBackground, True)
        root = QVBoxLayout(body)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        title = QLabel("实时识曲")
        title.setObjectName("CardTitle")
        root.addWidget(title)
        intro = QLabel(
            "听电脑里正在演奏的旋律，匹配本地曲库。全程离线，音频不会保存。"
        )
        intro.setObjectName("MutedText")
        intro.setWordWrap(True)
        root.addWidget(intro)

        device_row = QHBoxLayout()
        device_row.addWidget(QLabel("声音来源"))
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(220)
        self.device_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.device_combo.addItem("默认输出设备", None)
        self.device_combo.setAccessibleName("电脑声音来源")
        device_row.addWidget(self.device_combo, 1)
        self.refresh_button = QPushButton("刷新设备")
        device_row.addWidget(self.refresh_button)
        root.addLayout(device_row)

        action_row = QHBoxLayout()
        self.start_button = QPushButton("开始监听")
        self.start_button.setObjectName("PrimaryButton")
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        self.clear_button = QPushButton("重新听一段")
        self.clear_button.setEnabled(False)
        action_row.addWidget(self.start_button)
        action_row.addWidget(self.stop_button)
        action_row.addWidget(self.clear_button)
        action_row.addStretch()
        root.addLayout(action_row)

        self.level_bar = QProgressBar()
        self.level_bar.setRange(0, 100)
        self.level_bar.setValue(0)
        self.level_bar.setTextVisible(False)
        self.level_bar.setMaximumHeight(10)
        self.level_bar.setAccessibleName("声音电平")
        root.addWidget(self.level_bar)
        self.status_label = QLabel("请选择声音来源后开始监听")
        self.status_label.setObjectName("MutedText")
        self.status_label.setWordWrap(True)
        root.addWidget(self.status_label)

        notes_row = QHBoxLayout()
        notes_row.addWidget(QLabel("听到的音符"))
        self.notes_label = QLabel("—")
        self.notes_label.setObjectName("MutedText")
        self.notes_label.setWordWrap(True)
        self.notes_label.setMaximumHeight(44)
        notes_row.addWidget(self.notes_label, 1)
        root.addLayout(notes_row)

        result_title = QLabel("匹配曲谱")
        result_title.setObjectName("CardTitle")
        result_row = QHBoxLayout()
        result_row.addWidget(result_title)
        result_row.addStretch()
        self.open_button = QPushButton("打开选中曲谱")
        self.open_button.setEnabled(False)
        result_row.addWidget(self.open_button)
        root.addLayout(result_row)
        self.results_list = QListWidget()
        self.results_list.setMinimumHeight(120)
        self.results_list.setAccessibleName("识曲结果")
        root.addWidget(self.results_list, 1)
        hint = QLabel("试验功能：建议独奏并减小背景音乐，连续听至少 12 次音高变化。复杂和弦或多人合奏会影响结果，曲库外的歌曲无法识别。")
        hint.setObjectName("MutedText")
        hint.setWordWrap(True)
        root.addWidget(hint)

    def refresh_devices(self):
        if self.recognizer.running or (self._scan_thread and self._scan_thread.is_alive()):
            return
        self._scanned = True
        self.start_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.on_status("正在查找电脑声音来源…")

        def scan():
            devices, error = list_loopback_devices()
            if not self._closing:
                try:
                    self.scan_signals.finished.emit(devices, error)
                except RuntimeError:
                    pass

        self._scan_thread = threading.Thread(target=scan, name="SkyAudioDevices", daemon=True)
        self._scan_thread.start()

    def on_devices(self, devices, error):
        if self._closing:
            return
        selected_name = self.device_combo.currentText()
        self.device_combo.blockSignals(True)
        self.device_combo.clear()
        self.device_combo.addItem("默认输出设备", None)
        for index, name in devices:
            self.device_combo.addItem(name, index)
        self.device_combo.setCurrentIndex(max(0, self.device_combo.findText(selected_name)))
        self.device_combo.blockSignals(False)
        self.start_button.setEnabled(bool(devices) and not error)
        self.refresh_button.setEnabled(True)
        if error:
            self.on_status(f"无法读取电脑声音设备：{error}")
        elif devices:
            self.on_status(f"找到 {len(devices)} 个电脑声音来源")
        else:
            self.on_status("没有找到 WASAPI 回环设备；请确认 Windows 音频输出可用")

    def start_listening(self):
        device_index = self.device_combo.currentData()
        device_name = self.device_combo.currentText() if device_index is not None else None
        self._stopping = False
        self.results_list.clear()
        self.open_button.setEnabled(False)
        self.notes_label.setText("—")
        self.level_bar.setValue(0)
        if not self.recognizer.start(device_index, device_name):
            return
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.clear_button.setEnabled(True)
        self.device_combo.setEnabled(False)
        self.refresh_button.setEnabled(False)

    def stop_listening(self):
        if not self.recognizer.running:
            return
        self._stopping = True
        self.recognizer.stop()
        self.stop_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        self.status_label.setText("正在停止监听…")

    def clear_listening(self):
        self.recognizer.clear()
        self.results_list.clear()
        self.open_button.setEnabled(False)
        self.notes_label.setText("—")
        self.status_label.setText("监听中 · 重新收集旋律…")

    def on_status(self, message):
        if not self._stopping:
            self.status_label.setText(str(message))

    def on_level(self, value):
        self.level_bar.setValue(round(value * 100))

    def on_results(self, matches):
        if self._stopping or self._closing:
            return
        selected = self.results_list.currentItem()
        filename = selected.data(Qt.UserRole) if selected else None
        self.results_list.clear()
        for match in matches:
            item = QListWidgetItem(
                f"{match['title']}    ·    音符吻合 {match['score'] * 100:.0f}%"
            )
            item.setData(Qt.UserRole, match["filename"])
            item.setToolTip(f"{match['filename']}\n音符吻合度用于比较候选，不代表识别正确概率。")
            self.results_list.addItem(item)
            if match['filename'] == filename:
                self.results_list.setCurrentItem(item)
        if matches and self.results_list.currentRow() < 0:
            self.results_list.setCurrentRow(0)
        self.open_button.setEnabled(self.results_list.currentItem() is not None)
        if matches:
            self.status_label.setText(f"监听中 · 最接近《{matches[0]['title']}》；继续演奏可比较候选")
        else:
            self.status_label.setText("监听中 · 暂未匹配，继续听一段清楚的旋律…")

    def on_finished(self):
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.clear_button.setEnabled(False)
        self.device_combo.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.level_bar.setValue(0)
        if self._stopping:
            self.status_label.setText("监听已停止")
        self._stopping = False

    def open_selected(self):
        item = self.results_list.currentItem()
        if item:
            self.open_result(item)

    def open_result(self, item):
        filename = item.data(Qt.UserRole)
        if not filename:
            return
        if filename not in self.host.all_files:
            self.status_label.setText("该曲谱已从曲库移除，请重新识别。")
            return
        self.stop_listening()
        self.host.select_file(filename)
        self.host.pages.setCurrentWidget(self.host.play_page)
        self.host.nav_play.setChecked(True)
        self.host.set_status(f"已打开识曲结果：{self.host.display_song_name(filename)}")

    def shutdown(self):
        self._closing = True
        self.recognizer.shutdown()
        if self._scan_thread and self._scan_thread.is_alive():
            self._scan_thread.join(timeout=1.0)


class PiastudySignals(QObject):
    search_done = Signal(list, str)
    convert_done = Signal(dict, str)
    browser_done = Signal(bool, str)
    browser_progress = Signal(str)


class PiastudySearchPanel(QWidget):
    """在 piastudy 上搜索曲谱并一键转换到本地曲库。"""

    def __init__(self, parent, query=""):
        super().__init__(parent)
        self.host = parent
        self.results = []
        self._thread = None
        self.signals = PiastudySignals()
        self.signals.search_done.connect(self._on_search_done)
        self.signals.convert_done.connect(self._on_convert_done)
        self.signals.browser_done.connect(self._on_browser_done)
        self.signals.browser_progress.connect(self._on_browser_progress)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        search_row = QHBoxLayout()
        self.search_box = QLineEdit(query)
        self.search_box.setPlaceholderText("输入歌名")
        self.search_box.returnPressed.connect(self.do_search)
        self.search_btn = QPushButton("搜索")
        self.search_btn.clicked.connect(self.do_search)
        search_row.addWidget(self.search_box, 1)
        search_row.addWidget(self.search_btn)
        layout.addLayout(search_row)

        link_row = QHBoxLayout()
        self.url_box = QLineEdit()
        self.url_box.setPlaceholderText("粘贴 PiaStudy 歌曲详情页链接")
        self.url_box.returnPressed.connect(self.import_from_url)
        self.url_btn = QPushButton("按链接导入")
        self.url_btn.setToolTip("直接抓取已打开的 PiaStudy 歌曲详情页")
        self.url_btn.clicked.connect(self.import_from_url)
        link_row.addWidget(self.url_box, 1)
        link_row.addWidget(self.url_btn)
        layout.addLayout(link_row)

        browser_row = QHBoxLayout()
        self.browser_check = QCheckBox("使用无头浏览器")
        self.browser_check.setToolTip("执行页面 JavaScript，应对 PiaStudy 反爬；首次使用前请下载 Chromium")
        self.browser_download_btn = QPushButton("下载无头浏览器")
        self.browser_download_btn.setObjectName("CompactButton")
        self.browser_download_btn.setToolTip("仅在用户目录下载 Chromium，约数百 MB")
        self.browser_download_btn.clicked.connect(self.download_browser)
        browser_row.addWidget(self.browser_check)
        browser_row.addStretch()
        browser_row.addWidget(self.browser_download_btn)
        layout.addLayout(browser_row)
        self._refresh_browser_status()

        self.hands_combo = QComboBox()
        self.hands_combo.addItem("纯旋律", True)
        self.hands_combo.addItem("双手版", False)
        self.hands_combo.setToolTip("转换可能与原曲有差异，可尝试同曲其他版本。")
        layout.addWidget(self.hands_combo)

        self.results_list = QListWidget()
        self.results_list.itemDoubleClicked.connect(lambda _: self.convert_selected())
        layout.addWidget(self.results_list, 1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("MutedText")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        buttons = QHBoxLayout()
        self.convert_btn = QPushButton("转换到曲库")
        self.convert_btn.clicked.connect(self.convert_selected)
        self.convert_btn.setEnabled(False)
        self.request_btn = QPushButton("求谱")
        self.request_btn.setObjectName("CompactButton")
        self.request_btn.setToolTip("结果较少时，可提交给项目维护者处理")
        self.request_btn.clicked.connect(self.request_sheet)
        self.request_btn.hide()
        buttons.addStretch()
        buttons.addWidget(self.request_btn)
        buttons.addWidget(self.convert_btn)
        layout.addLayout(buttons)

        if query:
            QTimer.singleShot(0, self.do_search)

    def do_search(self):
        query = self.search_box.text().strip()
        if not query:
            return
        if self._thread and self._thread.is_alive():
            return
        self.status_label.setText("搜索中…")
        self.search_btn.setEnabled(False)
        self.convert_btn.setEnabled(False)
        self.results_list.clear()
        self._thread = threading.Thread(target=self._search_worker, args=(query,), daemon=True)
        self._thread.start()

    def _search_worker(self, query):
        try:
            results = piastudy.search(query)
            self.signals.search_done.emit(results, "")
        except Exception as exc:
            self.signals.search_done.emit([], str(exc))

    def _on_search_done(self, results, error):
        self.search_btn.setEnabled(True)
        if error:
            self.status_label.setText(f"搜索失败：{error}")
            return
        self.results = results
        self.results_list.clear()
        self.request_btn.setVisible(len(results) < 10)
        for item in results:
            label = item["title"]
            meta = " · ".join(x for x in (item["keynote"], item["difficulty"]) if x)
            if meta:
                label += f"（{meta}）"
            list_item = QListWidgetItem(label)
            list_item.setToolTip(item["page_url"])
            list_item.setData(Qt.UserRole, item)
            self.results_list.addItem(list_item)
        if results:
            message = f"{len(results)} 个结果"
            self.status_label.setText(message)
            self.results_list.setCurrentRow(0)
            self.convert_btn.setEnabled(True)
        else:
            self.status_label.setText("未找到曲谱")

    def request_sheet(self):
        query = self.search_box.text().strip()
        if query:
            self.host.open_sheet_request(query)

    def convert_selected(self):
        if self._thread and self._thread.is_alive():
            return
        item = self.results_list.currentItem()
        if not item:
            return
        info = item.data(Qt.UserRole)
        melody = bool(self.hands_combo.currentData())
        self._start_convert(info["page_url"], melody)

    def import_from_url(self):
        """直接抓取用户粘贴的 PiaStudy 歌曲详情页链接。"""
        if self._thread and self._thread.is_alive():
            return
        page_url = self.url_box.text().strip()
        if not page_url:
            self.status_label.setText("请先粘贴 PiaStudy 歌曲详情页链接")
            self.url_box.setFocus()
            return
        self._start_convert(page_url, bool(self.hands_combo.currentData()))

    def _refresh_browser_status(self):
        status = piastudy.browser_status()
        self.browser_check.setEnabled(bool(status.get("installed")))
        self.browser_download_btn.setEnabled(bool(status.get("available")))
        if status.get("installed"):
            self.browser_download_btn.setText("无头浏览器已下载")
            self.browser_download_btn.setToolTip(status.get("path", ""))
        elif status.get("available"):
            self.browser_download_btn.setText("下载无头浏览器")
        else:
            self.browser_download_btn.setText("无头浏览器不可用")
            self.browser_download_btn.setToolTip(status.get("error", "当前版本没有包含无头浏览器组件"))

    def download_browser(self):
        if self._thread and self._thread.is_alive():
            return
        if not piastudy.browser_status().get("available"):
            self.status_label.setText("当前版本没有包含无头浏览器组件")
            return
        self.browser_download_btn.setEnabled(False)
        self.url_btn.setEnabled(False)
        self.search_btn.setEnabled(False)
        self.status_label.setText("正在下载 Chromium…")
        self._thread = threading.Thread(target=self._browser_download_worker, daemon=True)
        self._thread.start()

    def _browser_download_worker(self):
        try:
            piastudy.install_browser(progress=self.signals.browser_progress.emit)
            self.signals.browser_done.emit(True, "")
        except Exception as exc:
            self.signals.browser_done.emit(False, str(exc))

    def _on_browser_progress(self, text):
        self.status_label.setText(f"正在下载 Chromium：{text}")

    def _on_browser_done(self, success, error):
        self.search_btn.setEnabled(True)
        self.url_btn.setEnabled(True)
        self._refresh_browser_status()
        if success:
            self.status_label.setText("无头浏览器已下载，可以勾选后扒谱")
        else:
            self.status_label.setText(f"无头浏览器下载失败：{error}")

    def _start_convert(self, page_url, melody):
        self.status_label.setText("正在抓取并转换…")
        self.convert_btn.setEnabled(False)
        self.url_btn.setEnabled(False)
        self.search_btn.setEnabled(False)
        self.browser_download_btn.setEnabled(False)
        use_browser = bool(self.browser_check.isChecked())
        self._thread = threading.Thread(
            target=self._convert_worker, args=(page_url, melody, use_browser), daemon=True
        )
        self._thread.start()

    def _convert_worker(self, page_url, melody, use_browser=False):
        try:
            result = piastudy.install_from_page(
                page_url, SHEET_MUSIC_DIR, melody=melody, use_browser=use_browser,
            )
            self.signals.convert_done.emit(result, "")
        except Exception as exc:
            self.signals.convert_done.emit({}, str(exc))

    def _on_convert_done(self, result, error):
        self.search_btn.setEnabled(True)
        self.url_btn.setEnabled(True)
        self._refresh_browser_status()
        if error:
            self.status_label.setText(f"转换失败：{error}")
            self.convert_btn.setEnabled(bool(self.results_list.currentItem()))
            return
        self.status_label.setText(f"已添加：{result.get('song_name')}")
        self.host.refresh_files()
        self.convert_btn.setEnabled(bool(self.results_list.currentItem()))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.config = load_json(CONFIG_FILE, {})
        self.favorites = set(load_json(FAVORITES_FILE, []))
        configured_theme = self.config.get("theme")
        self.theme_confirmed = configured_theme in THEMES
        self.theme_name = configured_theme if self.theme_confirmed else "carbon"
        self.speed_logic_version = int(self.config.get("speed_logic_version", 1) or 1)
        if self.speed_logic_version >= 2 and isinstance(self.config.get("speed_presets"), dict):
            self.speed_presets = self.config.get("speed_presets")
            self.initial_speed = float(self.config.get("speed", 1.0))
        else:
            self.speed_presets = {}
            self.initial_speed = 1.0
        self.speed_logic_version = 2
        self.overlay_mode = self.config.get("overlay_music_mode", "全部")
        self.admin_mode = bool(self.config.get("admin_mode", False))
        self.admin_tip_count = max(0, int(self.config.get("admin_tip_count", 0) or 0))
        self.all_files = []
        self.display_files = []
        self.selected_file = None
        self.meta = {}
        self.current_score_notes = []
        self.notes_by_time = defaultdict(list)
        self.sorted_times = []
        self.bpm = 120
        self.player_thread = None
        self.stop_event = threading.Event()
        self.playback_session = None
        self.playback_generation = 0
        self.position_index = 0
        self.resume_index = 0
        self.playback_complete = False
        positions = self.config.get("playback_positions", {})
        self.playback_positions = dict(positions) if isinstance(positions, dict) else {}
        saved_sections = self.config.get("song_sections", {})
        self.saved_song_sections = dict(saved_sections) if isinstance(saved_sections, dict) else {}
        self.song_sections = []
        self.sections_ready = False
        self.sections_dialog = None
        self.score_signature = ""
        self.global_hotkey_handles = []
        self.previewing = False
        self._mobile_note_keys = []
        self.preview_cache = {}
        self.preview_dir = Path(tempfile.gettempdir()) / "sky_auto_music_qt_preview"
        self.preview_dir.mkdir(exist_ok=True)
        self.sky_sound_root = APP_DIR / "Sky Sounds"
        self.sky_sound_id = str(self.config.get("sky_sound_id") or "specy:Harp")
        self.sky_location = str(self.config.get("sky_location") or "遇境")
        saved_location_pitches = self.config.get("sky_location_pitches")
        self.sky_location_pitches = dict(saved_location_pitches) if isinstance(saved_location_pitches, dict) else {}
        self.sky_pitch = "C"
        self.sky_sound_files = {}
        self.sky_sound_players = {}
        self.sky_sound_effects = {}
        self.sky_sound_downloading = False
        self.pressed_keys = set()
        self.signals = PlayerSignals()
        self.signals.progress.connect(self.on_worker_progress)
        self.signals.finished.connect(self.on_worker_finished)
        self.signals.status.connect(self.set_status)
        self.signals.playback_event.connect(self.on_playback_event, Qt.QueuedConnection)
        self.signals.start_requested.connect(self.start_from_shortcut)
        self.signals.play_stop_requested.connect(self.stop_play)
        self.signals.stop_requested.connect(self.force_stop)
        self.signals.toggle_overlay_requested.connect(self.toggle_overlay)
        self.signals.mobile_action.connect(self.handle_mobile_action, Qt.QueuedConnection)
        self.signals.preview_requested.connect(self._play_preview_note, Qt.QueuedConnection)
        self.sky_sound_signals = SkySoundSignals()
        self.sky_sound_signals.progress.connect(self.on_sky_sound_progress)
        self.sky_sound_signals.finished.connect(self.on_sky_sound_finished)
        self.sky_sound_signals.failed.connect(self.on_sky_sound_failed)
        mobile_token = str(self.config.get("mobile_remote_token") or create_pairing_token())
        self.mobile_remote = MobileRemoteServer(
            APP_DIR / "mobile" / "web",
            token=mobile_token,
            on_action=self.request_mobile_action,
        )
        self.update_signals = UpdateSignals()
        self.update_signals.progress.connect(self.on_update_progress)
        self.update_signals.finished.connect(self.on_update_finished)
        self.update_signals.failed.connect(self.on_update_failed)
        self.app_update_signals = AppUpdateSignals()
        self.app_update_signals.finished.connect(self.on_app_update_finished)
        self.app_update_signals.failed.connect(self.on_app_update_failed)
        self.midi_signals = PiastudySignals()
        self.midi_signals.convert_done.connect(self.on_midi_convert_done)
        self.search_index = SheetSearchIndex(SHEET_MUSIC_DIR, APP_DIR / "search-index.json", self)
        self.search_index.ready_changed.connect(self.refresh_list, Qt.QueuedConnection)
        self.update_running = False
        self.midi_game = None

        self.setWindowTitle("SkyAutoMusic")
        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setAcceptDrops(True)
        self.resize(int(self.config.get("width", 1080)), int(self.config.get("height", 720)))
        self.setMinimumSize(1000, 580)
        self.build_ui()
        self.signals.score_step_requested.connect(self.overlay.step_score)
        self.signals.overlay_lock_requested.connect(
            lambda: self.overlay.set_input_locked(not self.overlay.lock_button.isChecked()))
        self.signals.score_scroll_requested.connect(self.overlay.auto_score_button.click)
        self.apply_style()
        self.bind_escape_stop()
        self.refresh_files()
        self.search_index.start()
        self.watch_timer = QTimer(self)
        self.watch_timer.timeout.connect(self.refresh_files_if_changed)
        self.watch_timer.start(1500)
        if not self.theme_confirmed:
            QTimer.singleShot(0, lambda: not self.theme_confirmed and self.show_theme_chooser())
        QTimer.singleShot(1200, self.check_app_update)

    def bind_escape_stop(self):
        self.escape_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self.escape_shortcut.setContext(Qt.ApplicationShortcut)
        self.escape_shortcut.activated.connect(self.force_stop)
        global_hotkeys_registered = False
        try:
            import keyboard

            self.global_hotkey_handles.append(
                keyboard.add_hotkey("esc", self.signals.stop_requested.emit, suppress=False)
            )
            self.global_hotkey_handles.append(
                keyboard.add_hotkey("f7", self.signals.start_requested.emit, suppress=False)
            )
            self.global_hotkey_handles.append(
                keyboard.add_hotkey("f8", self.signals.play_stop_requested.emit, suppress=False)
            )
            self.global_hotkey_handles.append(
                keyboard.add_hotkey("f3", self.signals.toggle_overlay_requested.emit, suppress=False)
            )
            for key, callback in (
                ("f4", self.signals.overlay_lock_requested.emit),
                ("f5", lambda: self.signals.score_step_requested.emit(-1)),
                ("f6", lambda: self.signals.score_step_requested.emit(1)),
                ("f9", self.signals.score_scroll_requested.emit),
            ):
                self.global_hotkey_handles.append(keyboard.add_hotkey(key, callback, suppress=False))
            global_hotkeys_registered = True
        except Exception:
            for handle in self.global_hotkey_handles:
                try:
                    keyboard.remove_hotkey(handle)
                except Exception:
                    pass
            self.global_hotkey_handles.clear()
        self.global_hotkeys_registered = global_hotkeys_registered
        if not global_hotkeys_registered:
            self.overlay_shortcut = QShortcut(QKeySequence(Qt.Key_F3), self)
            self.overlay_shortcut.setContext(Qt.ApplicationShortcut)
            self.overlay_shortcut.activated.connect(self.toggle_overlay)
            self.start_shortcut = QShortcut(QKeySequence(Qt.Key_F7), self)
            self.start_shortcut.setContext(Qt.ApplicationShortcut)
            self.start_shortcut.activated.connect(self.start_from_shortcut)
            self.stop_shortcut = QShortcut(QKeySequence(Qt.Key_F8), self)
            self.stop_shortcut.setContext(Qt.ApplicationShortcut)
            self.stop_shortcut.activated.connect(self.stop_play)
            self.guide_shortcuts = []
            for key, callback in (
                (Qt.Key_F4, lambda: self.overlay.set_input_locked(False)),
                (Qt.Key_F5, lambda: self.overlay.step_score(-1)),
                (Qt.Key_F6, lambda: self.overlay.step_score(1)),
                (Qt.Key_F9, self.overlay.auto_score_button.click),
            ):
                shortcut = QShortcut(QKeySequence(key), self)
                shortcut.setContext(Qt.ApplicationShortcut)
                shortcut.activated.connect(callback)
                self.guide_shortcuts.append(shortcut)

    def build_ui(self):
        shell = QWidget()
        shell.setObjectName("AppShell")
        self.setCentralWidget(shell)
        shell_layout = QVBoxLayout(shell)
        shell_layout.setContentsMargins(1, 1, 1, 1)
        shell_layout.setSpacing(0)

        self.title_bar = WindowTitleBar(self)
        shell_layout.addWidget(self.title_bar)

        content = QFrame()
        content.setObjectName("AppContent")
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        shell_layout.addWidget(content, 1)

        navigation = QFrame()
        navigation.setObjectName("NavigationRail")
        navigation.setFixedWidth(88)
        nav_layout = QVBoxLayout(navigation)
        nav_layout.setContentsMargins(7, 9, 7, 9)
        nav_layout.setSpacing(5)
        nav_group = QButtonGroup(self)
        nav_group.setExclusive(True)
        self.nav_icon_specs = []

        def add_nav(text, icon, checked=False, hint=""):
            button = QToolButton()
            button.setObjectName("NavButton")
            button.setText(text)
            button.setAccessibleName(text)
            button.setToolTip(hint or text)
            button.setIcon(self.style().standardIcon(icon))
            button.setIconSize(QSize(20, 20))
            button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            button.setCheckable(True)
            button.setChecked(checked)
            button.setFixedHeight(58)
            nav_group.addButton(button)
            self.nav_icon_specs.append((button, icon))
            nav_layout.addWidget(button)
            return button

        self.nav_play = add_nav("播放", QStyle.SP_MediaPlay, True, "曲库、试听与自动演奏")
        self.nav_compose = add_nav("制谱", QStyle.SP_FileDialogDetailedView, hint="键盘 / MIDI 制谱与分离录制")
        self.nav_transcribe = add_nav("找谱", QStyle.SP_FileDialogListView, hint="在线搜索、本地导入、曲库更新与实时识曲")
        self.nav_practice = add_nav("练习", QStyle.SP_MediaVolume, hint="曲谱跟弹、初学者课程与练习教练")
        self.nav_game = add_nav("游戏", QStyle.SP_DesktopIcon, hint="悬浮琴谱、MIDI 接入光遇与手机同步")
        nav_layout.addStretch()
        self.nav_update = add_nav("设置", QStyle.SP_ComputerIcon, hint="主题、音色管理与管理员模式")
        self.nav_about = add_nav("关于", QStyle.SP_MessageBoxInformation, hint="软件更新、交流与项目资料")
        nav_status = QLabel("就绪")
        nav_status.setObjectName("NavStatus")
        nav_status.setAlignment(Qt.AlignCenter)
        nav_layout.addWidget(nav_status)
        content_layout.addWidget(navigation)

        self.pages = QStackedWidget()
        self.pages.setObjectName("Pages")
        content_layout.addWidget(self.pages, 1)
        play_page = QWidget()
        self.play_page = play_page
        play_page.setObjectName("PlayPage")
        help_page = QWidget()
        self.help_page = help_page
        help_page.setObjectName("HelpPage")
        about_page = QWidget()
        about_page.setObjectName("AboutPage")
        self.pages.addWidget(play_page)
        self.pages.addWidget(help_page)
        self.pages.addWidget(about_page)
        self.game_page = QWidget()
        self.game_page.setObjectName("GamePage")
        self.pages.addWidget(self.game_page)
        self.nav_play.clicked.connect(lambda: self.pages.setCurrentWidget(play_page))
        self.nav_update.clicked.connect(lambda: self.pages.setCurrentWidget(help_page))
        self.nav_about.clicked.connect(lambda: self.pages.setCurrentWidget(about_page))
        self.nav_game.clicked.connect(self.show_game_page)
        self.transcribe_page = QWidget()
        self.transcribe_page.setObjectName("TranscribePage")
        self.pages.addWidget(self.transcribe_page)
        self.nav_transcribe.clicked.connect(self.show_transcribe)
        self.score_editor = ScoreEditorPage(SHEET_MUSIC_DIR, self.preview_note, self.on_score_saved, self)
        self.score_editor.input_mode.setCurrentIndex(max(0, self.score_editor.input_mode.findData(
            self.config.get("score_input_mode", "piano"))))
        self.pages.addWidget(self.score_editor)
        self.nav_compose.clicked.connect(self.show_score_editor)
        self.midi_practice = MidiPracticePage(self.config, self)
        self.pages.addWidget(self.midi_practice)
        self.midi_practice.starting.connect(self.stop_play)
        self.midi_practice.score_selected.connect(self.select_practice_file)
        self.midi_practice.progress_saved.connect(self.save_config)
        self.nav_practice.clicked.connect(self.show_midi_practice)
        self.page_navigation = {
            play_page: self.nav_play, self.score_editor: self.nav_compose,
            self.transcribe_page: self.nav_transcribe, self.midi_practice: self.nav_practice,
            self.game_page: self.nav_game, help_page: self.nav_update, about_page: self.nav_about,
        }
        self.pages.currentChanged.connect(self.sync_navigation)

        page_layout = QVBoxLayout(play_page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("WorkspaceSplitter")
        splitter.setChildrenCollapsible(False)
        page_layout.addWidget(splitter, 1)

        left = QFrame()
        left.setObjectName("LibraryPanel")
        left_layout = QVBoxLayout(left)
        self.library_layout = left_layout
        left_layout.setContentsMargins(12, 17, 12, 12)
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

        self.not_found_panel = QFrame()
        self.not_found_panel.setObjectName("HintBox")
        nf_layout = QVBoxLayout(self.not_found_panel)
        nf_layout.setContentsMargins(10, 8, 10, 8)
        nf_layout.setSpacing(5)
        nf_hint = QLabel("曲库中没找到")
        nf_hint.setObjectName("MutedText")
        nf_layout.addWidget(nf_hint)
        self.piastudy_search_btn = QPushButton("去找谱")
        self.piastudy_search_btn.setObjectName("CompactButton")
        self.piastudy_search_btn.clicked.connect(lambda: self.open_piastudy_search(self.search.text().strip()))
        nf_layout.addWidget(self.piastudy_search_btn)
        self.not_found_panel.hide()

        self.list_widget = QListWidget()
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.setSpacing(2)
        self.list_widget.itemSelectionChanged.connect(self.on_list_selection)
        self.list_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self.show_song_menu)
        left_layout.addWidget(self.mode_tabs)
        left_layout.addWidget(self.search)
        left_layout.addWidget(self.list_widget, 1)
        left_layout.addWidget(self.not_found_panel)
        left.setMinimumWidth(210)
        left.setMaximumWidth(330)
        splitter.addWidget(left)

        center = QFrame()
        center.setObjectName("CenterWorkspace")
        center.setMinimumWidth(400)
        right = QVBoxLayout(center)
        self.center_layout = right
        right.setContentsMargins(16, 18, 16, 13)
        right.setSpacing(0)
        splitter.addWidget(center)

        info = QFrame()
        info.setObjectName("NowPlayingSection")
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(0, 0, 0, 6)
        info_layout.setSpacing(4)
        info_layout.setAlignment(Qt.AlignTop)
        eyebrow = QLabel("播放")
        eyebrow.setObjectName("Eyebrow")
        self.name_label = QLabel("请选择一首乐谱")
        self.name_label.setObjectName("SongTitle")
        self.name_label.setWordWrap(True)
        self.song_summary_label = QLabel("—")
        self.song_summary_label.setObjectName("MutedText")
        info_layout.addWidget(eyebrow)
        info_layout.addWidget(self.name_label)

        info_grid = QGridLayout()
        info_grid.setHorizontalSpacing(18)
        info_grid.setVerticalSpacing(5)
        self.author_label = QLabel("—")
        self.transcribed_label = QLabel("—")
        self.filename_label = QLabel("—")
        self.duration_summary_label = QLabel("0:00")
        self.filename_label.setObjectName("Filename")
        self.filename_label.setWordWrap(True)
        for row, (label, widget) in enumerate([
            ("音符", self.song_summary_label),
            ("时长", self.duration_summary_label),
            ("作者", self.author_label), ("制谱", self.transcribed_label),
            ("文件", self.filename_label),
        ]):
            field = QLabel(label)
            field.setObjectName("FieldLabel")
            info_grid.addWidget(field, row, 0)
            info_grid.addWidget(widget, row, 1)
        info_grid.setColumnStretch(1, 1)
        info_layout.addLayout(info_grid)
        right.addWidget(info, 0)

        sound_section = QFrame()
        sound_section.setObjectName("PlaybackSoundSection")
        sound_layout = QVBoxLayout(sound_section)
        sound_layout.setContentsMargins(0, 5, 0, 7)
        sound_layout.setSpacing(5)
        instrument_row = QHBoxLayout()
        instrument_row.setSpacing(7)
        instrument_row.addWidget(QLabel("音色"))
        self.play_sound_combo = QComboBox()
        self.play_sound_combo.setAccessibleName("播放预览音色")
        self.play_sound_combo.setIconSize(QSize(22, 22))
        self.play_sound_combo.setToolTip("选择播放页预览和制谱试听使用的乐器")
        instrument_row.addWidget(self.play_sound_combo, 1)
        self.play_sound_download_button = QToolButton()
        self.play_sound_download_button.setIcon(self.style().standardIcon(QStyle.SP_ArrowDown))
        self.play_sound_download_button.setFixedSize(32, 32)
        self.play_sound_download_button.setToolTip("下载所选音色")
        self.play_sound_download_button.setAccessibleName("下载所选音色")
        instrument_row.addWidget(self.play_sound_download_button)
        sound_layout.addLayout(instrument_row)
        location_row = QHBoxLayout()
        location_row.setSpacing(7)
        location_row.addWidget(QLabel("演奏地点"))
        self.play_location_combo = QComboBox()
        self.play_location_combo.setAccessibleName("演奏地点")
        self.play_location_combo.setToolTip("选择游戏中的具体地点，为本机试听匹配调性")
        location_row.addWidget(self.play_location_combo, 1)
        location_row.addWidget(QLabel("调性"))
        self.play_pitch_combo = QComboBox()
        self.play_pitch_combo.setAccessibleName("演奏调性")
        self.play_pitch_combo.setToolTip("当前地点的试听调性；手动修改会记住该地点的选择")
        self.play_pitch_combo.setFixedWidth(78)
        location_row.addWidget(self.play_pitch_combo)
        sound_layout.addLayout(location_row)
        right.addWidget(sound_section)

        time_box = QFrame()
        time_box.setObjectName("ProgressSection")
        time_layout = QVBoxLayout(time_box)
        time_layout.setContentsMargins(0, 7, 0, 8)
        time_layout.setSpacing(5)
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

        section_row = QHBoxLayout()
        self.section_combo = QComboBox()
        self.section_combo.setAccessibleName("播放歌曲段落")
        self.section_combo.addItem("全曲", None)
        self.section_combo.setMinimumWidth(0)
        self.section_combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.section_combo.currentIndexChanged.connect(self.on_section_selected)
        self.section_button = QPushButton("歌曲段落…")
        self.section_button.setToolTip("识别与编辑 A 段、B 段、高潮候选；可单独播放和循环")
        self.section_button.clicked.connect(self.show_song_sections)
        self.section_loop = QCheckBox("循环")
        self.section_loop.setToolTip("循环选中的段落；选择全曲时循环全曲")
        section_row.addWidget(self.section_combo, 1)
        section_row.addWidget(self.section_loop)
        section_row.addWidget(self.section_button)
        right.addLayout(section_row)

        settings = QFrame()
        settings.setObjectName("SettingsSection")
        settings_row = QHBoxLayout(settings)
        settings_row.setContentsMargins(0, 8, 0, 0)
        settings_row.setSpacing(9)

        speed_box = QFrame()
        speed_box.setObjectName("SpeedSection")
        speed_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        speed_layout = QVBoxLayout(speed_box)
        speed_layout.setContentsMargins(0, 0, 12, 0)
        speed_layout.setSpacing(5)
        speed_layout.setAlignment(Qt.AlignTop)
        speed_title_row = QHBoxLayout()
        speed_title = QLabel("原速")
        speed_title.setObjectName("CardTitle")
        speed_title_row.addWidget(speed_title)
        speed_title_row.addStretch()
        self.speed_label = QLabel("")
        self.speed_label.setObjectName("ValueBadge")
        self.speed_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
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
        quick.setSpacing(4)
        for value in (0.75, 1.0, 1.25):
            btn = QPushButton(f"{value:g}x")
            btn.setObjectName("CompactButton")
            btn.clicked.connect(lambda _, v=value: self.set_speed(v))
            quick.addWidget(btn)
        save = QPushButton("保存")
        clear = QPushButton("清除")
        save.setObjectName("CompactButton")
        clear.setObjectName("CompactButton")
        save.setToolTip("保存当前歌曲速度")
        save.clicked.connect(self.save_speed_preset)
        clear.clicked.connect(self.clear_speed_preset)
        quick.addWidget(save)
        quick.addWidget(clear)
        speed_layout.addLayout(quick)
        settings_row.addWidget(speed_box, 1)
        self.on_speed_changed()

        effects = QFrame()
        effects.setObjectName("EffectsSection")
        effects.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        effects_grid = QGridLayout(effects)
        effects_grid.setContentsMargins(12, 0, 0, 0)
        effects_grid.setHorizontalSpacing(9)
        effects_grid.setVerticalSpacing(5)
        effects_grid.setAlignment(Qt.AlignTop)
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
        self.delay_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.wrong_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        effects_grid.addWidget(QLabel("随机延迟"), 1, 0)
        effects_grid.addWidget(self.delay_slider, 1, 1)
        effects_grid.addWidget(self.delay_label, 1, 2)
        effects_grid.addWidget(QLabel("错键概率"), 2, 0)
        effects_grid.addWidget(self.wrong_slider, 2, 1)
        effects_grid.addWidget(self.wrong_label, 2, 2)
        settings_row.addWidget(effects, 1)
        right.addWidget(settings, 0)
        right.addStretch(1)
        self.update_effect_labels()

        inspector = QFrame()
        inspector.setObjectName("InspectorPanel")
        inspector.setMinimumWidth(200)
        inspector.setMaximumWidth(275)
        inspector_layout = QVBoxLayout(inspector)
        self.inspector_layout = inspector_layout
        inspector_layout.setContentsMargins(14, 18, 14, 13)
        inspector_layout.setSpacing(12)
        inspector_title = QLabel("游戏工具与快捷键")
        inspector_title.setObjectName("InspectorTitle")
        inspector_layout.addWidget(inspector_title)

        self.game_midi_button = QPushButton("MIDI 接入光遇")
        self.game_midi_button.setToolTip("打开游戏页的 MIDI 接入设置")
        self.game_midi_button.clicked.connect(self.show_game_midi)
        inspector_layout.addWidget(self.game_midi_button)

        inspector_divider = QFrame()
        inspector_divider.setFrameShape(QFrame.HLine)
        inspector_divider.setObjectName("Divider")
        inspector_layout.addWidget(inspector_divider)

        shortcuts_title = QLabel("快捷键")
        shortcuts_title.setObjectName("CardTitle")
        inspector_layout.addWidget(shortcuts_title)
        for label_text, key_text in (
            ("开始演奏", "F7"),
            ("停止演奏", "F8"),
            ("显示 / 隐藏悬浮窗", "F3"),
            ("强制停止并释放按键", "ESC"),
        ):
            shortcut_row = QHBoxLayout()
            shortcut_label = QLabel(label_text)
            shortcut_label.setObjectName("InspectorLabel")
            key = QLabel(key_text)
            key.setObjectName("KeyCap")
            key.setAlignment(Qt.AlignCenter)
            shortcut_row.addWidget(shortcut_label, 1)
            shortcut_row.addWidget(key)
            inspector_layout.addLayout(shortcut_row)

        inspector_layout.addStretch()
        splitter.addWidget(inspector)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([285, 600, 235])

        transport = QFrame()
        transport.setObjectName("TransportBar")
        transport.setFixedHeight(86)
        transport_layout = QHBoxLayout(transport)
        transport_layout.setContentsMargins(12, 13, 12, 13)
        transport_layout.setSpacing(10)
        self.start_btn = QPushButton("开始演奏")
        self.start_btn.setObjectName("PrimaryButton")
        self.start_btn.setToolTip("从保存的位置继续演奏，3 秒倒计时后开始")
        self.immediate_btn = QPushButton("立即播放")
        self.immediate_btn.setToolTip("立即演奏到目标窗口，跳过 3 秒倒计时；本机试听请点预览")
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setObjectName("DangerButton")
        self.preview_btn = QPushButton("预览")
        self.overlay_btn = QPushButton("悬浮琴谱")
        self.overlay_btn.setToolTip("打开光遇 15 键琴谱，在游戏里看谱弹奏；F3 显示 / 隐藏")
        for button in (self.start_btn, self.immediate_btn, self.stop_btn, self.preview_btn, self.overlay_btn):
            button.setMinimumHeight(52)
        self.stop_btn.setEnabled(False)
        self.start_btn.clicked.connect(self.start_play)
        self.immediate_btn.clicked.connect(lambda: self.start_play(immediate=True))
        self.stop_btn.clicked.connect(self.stop_play)
        self.preview_btn.clicked.connect(self.toggle_preview)
        self.overlay_btn.clicked.connect(self.toggle_score_overlay)
        transport_layout.addWidget(self.preview_btn, 2)
        transport_layout.addWidget(self.start_btn, 3)
        transport_layout.addWidget(self.immediate_btn, 2)
        transport_layout.addWidget(self.stop_btn, 3)
        transport_layout.addWidget(self.overlay_btn, 2)
        page_layout.addWidget(transport)

        self.status = QLabel("请选择乐谱")
        self.status.setObjectName("Status")
        self.status.setWordWrap(True)
        inspector_layout.insertWidget(inspector_layout.count() - 1, self.status)

        help_page_layout = QVBoxLayout(help_page)
        help_page_layout.setContentsMargins(0, 0, 0, 0)
        help_scroll = QScrollArea()
        self.help_scroll = help_scroll
        help_scroll.setObjectName("HelpScroll")
        help_scroll.setWidgetResizable(True)
        help_scroll.setFrameShape(QFrame.NoFrame)
        help_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        help_content = QWidget()
        help_content.setObjectName("HelpContent")
        help_layout = QVBoxLayout(help_content)
        help_layout.setContentsMargins(30, 24, 30, 24)
        help_layout.setSpacing(16)
        help_scroll.setWidget(help_content)
        help_page_layout.addWidget(help_scroll)
        help_title = QLabel("设置")
        help_title.setObjectName("BrandTitle")
        theme_card = QFrame()
        theme_card.setObjectName("Card")
        theme_layout = QVBoxLayout(theme_card)
        theme_layout.setContentsMargins(20, 18, 20, 18)
        theme_layout.setSpacing(10)
        theme_title = QLabel("外观主题")
        theme_title.setObjectName("CardTitle")
        self.theme_combo = QComboBox()
        self.theme_combo.setAccessibleName("外观主题")
        for key, theme in THEMES.items():
            self.theme_combo.addItem(f"{theme['label']} · {theme['description']}", key)
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(self.theme_name)))
        self.theme_combo.currentIndexChanged.connect(self.on_theme_combo_changed)
        theme_layout.addWidget(theme_title)
        theme_layout.addWidget(self.theme_combo)
        help_layout.addWidget(help_title)
        help_layout.addWidget(theme_card)

        admin_card = QFrame()
        admin_card.setObjectName("Card")
        admin_layout = QVBoxLayout(admin_card)
        admin_layout.setContentsMargins(20, 18, 20, 18)
        admin_layout.setSpacing(10)
        admin_title = QLabel("管理员模式")
        admin_title.setObjectName("CardTitle")
        admin_copy = QLabel("仅在游戏无法接收按键时开启。")
        admin_copy.setObjectName("MutedText")
        admin_copy.setWordWrap(True)
        self.admin_mode_check = QCheckBox("以后每次启动都使用管理员模式")
        self.admin_mode_check.setChecked(self.admin_mode)
        self.admin_mode_check.clicked.connect(self.on_admin_mode_changed)
        admin_layout.addWidget(admin_title)
        admin_layout.addWidget(admin_copy)
        admin_layout.addWidget(self.admin_mode_check)
        help_layout.addWidget(admin_card)

        self.build_game_page()
        self.midi_game = MidiSkyPanel(self.send_game_key, self.config, self.game_page)
        self.midi_game.starting.connect(self.stop_play)
        self.game_midi_layout.addWidget(self.midi_game)
        self.game_midi_layout.addStretch()

        sound_card = QFrame()
        sound_card.setObjectName("Card")
        sound_layout = QVBoxLayout(sound_card)
        sound_layout.setContentsMargins(20, 18, 20, 18)
        sound_layout.setSpacing(9)
        sound_title = QLabel("光遇音色")
        sound_title.setObjectName("CardTitle")
        sound_copy = QLabel("内置光遇乐器音色，可在制谱和曲谱预览中直接使用；也可导入本地采样。")
        sound_copy.setObjectName("MutedText")
        sound_copy.setWordWrap(True)
        sound_layout.addWidget(sound_title)
        sound_layout.addWidget(sound_copy)
        sound_controls = QHBoxLayout()
        self.sky_sound_instrument_combo = QComboBox()
        self.sky_sound_instrument_combo.setAccessibleName("光遇音色")
        self.sky_sound_download_button = QPushButton("下载所选音色")
        self.sky_sound_download_button.setObjectName("CompactButton")
        self.sky_sound_import_button = QPushButton("选择本地采样目录")
        self.sky_sound_import_button.setObjectName("CompactButton")
        self.sky_sound_source_button = QPushButton("打开采样来源")
        self.sky_sound_source_button.setObjectName("CompactButton")
        sound_controls.addWidget(self.sky_sound_instrument_combo, 1)
        sound_controls.addWidget(self.sky_sound_download_button)
        sound_controls.addWidget(self.sky_sound_import_button)
        sound_controls.addWidget(self.sky_sound_source_button)
        sound_layout.addLayout(sound_controls)
        self.sky_sound_status_label = QLabel("当前使用：合成音")
        self.sky_sound_status_label.setObjectName("MutedText")
        self.sky_sound_status_label.setWordWrap(True)
        sound_layout.addWidget(self.sky_sound_status_label)
        help_layout.addWidget(sound_card)

        mobile_card = QFrame()
        mobile_card.setObjectName("Card")
        mobile_layout = QVBoxLayout(mobile_card)
        mobile_layout.setContentsMargins(20, 18, 20, 18)
        mobile_layout.setSpacing(9)
        mobile_title = QLabel("手机同步")
        mobile_title.setObjectName("CardTitle")
        mobile_copy = QLabel(
            "手机端可以独立播放；连接同一局域网后，可从这里同步曲库和当前曲谱。"
        )
        mobile_copy.setObjectName("MutedText")
        mobile_copy.setWordWrap(True)
        mobile_layout.addWidget(mobile_title)
        mobile_layout.addWidget(mobile_copy)
        mobile_url_row = QHBoxLayout()
        mobile_url_label = QLabel("地址")
        mobile_url_label.setObjectName("FieldLabel")
        self.mobile_url_input = QLineEdit()
        self.mobile_url_input.setReadOnly(True)
        self.mobile_url_input.setAccessibleName("手机同步地址")
        self.mobile_url_input.setPlaceholderText("启动后显示局域网地址")
        mobile_url_row.addWidget(mobile_url_label)
        mobile_url_row.addWidget(self.mobile_url_input, 1)
        mobile_layout.addLayout(mobile_url_row)
        mobile_token_row = QHBoxLayout()
        mobile_token_label = QLabel("令牌")
        mobile_token_label.setObjectName("FieldLabel")
        self.mobile_token_input = QLineEdit(self.mobile_remote.token)
        self.mobile_token_input.setReadOnly(True)
        self.mobile_token_input.setAccessibleName("手机同步令牌")
        mobile_copy_button = QPushButton("复制")
        mobile_copy_button.setObjectName("CompactButton")
        mobile_copy_button.setAccessibleName("复制手机同步令牌")
        mobile_copy_button.clicked.connect(self.copy_mobile_token)
        mobile_token_row.addWidget(mobile_token_label)
        mobile_token_row.addWidget(self.mobile_token_input, 1)
        mobile_token_row.addWidget(mobile_copy_button)
        mobile_layout.addLayout(mobile_token_row)
        mobile_action_row = QHBoxLayout()
        self.mobile_status_label = QLabel("尚未启动")
        self.mobile_status_label.setObjectName("MutedText")
        self.mobile_status_label.setWordWrap(True)
        self.mobile_toggle_button = QPushButton("启动手机同步")
        self.mobile_toggle_button.setObjectName("CompactButton")
        self.mobile_toggle_button.clicked.connect(self.toggle_mobile_remote)
        mobile_action_row.addWidget(self.mobile_status_label, 1)
        mobile_action_row.addWidget(self.mobile_toggle_button)
        mobile_layout.addLayout(mobile_action_row)
        self.game_mobile_layout.addWidget(mobile_card)
        self.game_mobile_layout.addStretch()

        self.build_transcribe_page()

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

        external_title = QLabel("其他来源")
        external_title.setObjectName("CardTitle")
        self.transcribe_import_layout.addWidget(external_title)
        external_controls = QHBoxLayout()
        self.source_url_input = QLineEdit()
        self.source_url_input.setPlaceholderText("粘贴 .zip 或 .json 的 HTTP/HTTPS 直链")
        self.source_url_input.setAccessibleName("其他曲库来源下载地址")
        self.source_download_button = QPushButton("下载并导入")
        self.source_download_button.clicked.connect(self.start_external_source_import)
        external_controls.addWidget(self.source_url_input, 1)
        external_controls.addWidget(self.source_download_button)
        self.transcribe_import_layout.addLayout(external_controls)

        local_controls = QHBoxLayout()
        self.import_files_button = QPushButton("导入 JSON / ZIP")
        self.import_files_button.clicked.connect(self.choose_sheet_files)
        self.import_midi_files_button = QPushButton("导入 MIDI")
        self.import_midi_files_button.clicked.connect(self.choose_midi_import)
        local_controls.addWidget(self.import_midi_files_button)
        local_controls.addWidget(self.import_files_button)
        self.transcribe_import_layout.insertLayout(0, local_controls)
        self.transcribe_import_layout.addStretch()

        self.update_progress = QProgressBar()
        self.update_progress.setRange(0, 100)
        self.update_progress.setValue(0)
        self.update_progress.setTextVisible(False)
        self.import_progress = QProgressBar()
        self.import_progress.setRange(0, 100)
        self.import_progress.setValue(0)
        self.update_progress.valueChanged.connect(self.import_progress.setValue)
        self.import_progress.setTextVisible(False)
        self.import_status = QLabel("支持 MIDI、JSON、ZIP")
        self.import_status.setObjectName("MutedText")
        self.import_status.setWordWrap(True)
        self.transcribe_import_layout.insertWidget(3, self.import_progress)
        self.transcribe_import_layout.insertWidget(4, self.import_status)
        self.update_status_label = QLabel("尚未检查更新")
        self.update_status_label.setObjectName("MutedText")
        self.update_status_label.setWordWrap(True)
        update_layout.addWidget(self.update_progress)
        update_layout.addWidget(self.update_status_label)
        self.transcribe_update_layout.addWidget(update_card)
        self.transcribe_update_layout.addStretch()
        help_layout.addStretch()
        self.build_about_page(about_page)
        self.overlay = FloatingControl(self)
        self.midi_game.keys_changed.connect(self.overlay.feed_game_midi)
        self.midi_game.connecting.connect(self.prepare_game_midi)
        self.score_editor.midi_connecting.connect(lambda: self.release_midi_ownership("editor"))
        self.midi_practice.connecting.connect(lambda: self.release_midi_ownership("practice"))
        self.setup_sky_sound_controls()
        self.setup_sky_location_controls()

    def build_game_page(self):
        root = QVBoxLayout(self.game_page)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)
        title = QLabel("游戏")
        title.setObjectName("BrandTitle")
        root.addWidget(title)
        self.game_tabs = QTabWidget()
        self.game_tabs.setAccessibleName("游戏弹奏方式")
        root.addWidget(self.game_tabs, 1)
        for label, attribute in (
            ("悬浮琴谱", "game_score_layout"),
            ("MIDI 接入", "game_midi_layout"),
            ("手机同步", "game_mobile_layout"),
        ):
            scroll = QScrollArea()
            scroll.setObjectName("GameScroll")
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            content = QWidget()
            content.setObjectName("GameContent")
            layout = QVBoxLayout(content)
            layout.setContentsMargins(18, 18, 18, 18)
            layout.setSpacing(14)
            setattr(self, attribute, layout)
            scroll.setWidget(content)
            self.game_tabs.addTab(scroll, label)

        card = QFrame()
        card.setObjectName("Card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        heading = QLabel("在光遇里看谱弹奏")
        heading.setObjectName("CardTitle")
        layout.addWidget(heading)
        self.game_song_label = QLabel("未选择曲谱")
        self.game_song_label.setObjectName("SongTitle")
        self.game_song_label.setWordWrap(True)
        layout.addWidget(self.game_song_label)
        self.game_score_details = QLabel("先在播放页选择一首曲谱。")
        self.game_score_details.setObjectName("MutedText")
        self.game_score_details.setWordWrap(True)
        layout.addWidget(self.game_score_details)
        actions = QHBoxLayout()
        self.game_choose_song = QPushButton("选择曲谱")
        self.game_choose_song.clicked.connect(lambda: self.pages.setCurrentWidget(self.play_page))
        self.game_open_score = QPushButton("打开悬浮琴谱")
        self.game_open_score.setObjectName("PrimaryButton")
        self.game_open_score.clicked.connect(self.show_score_overlay)
        self.game_hide_score = QPushButton("隐藏悬浮窗")
        self.game_hide_score.clicked.connect(lambda: self.overlay.isVisible() and self.toggle_overlay())
        for button in (self.game_choose_song, self.game_open_score, self.game_hide_score):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        copy = QLabel("切回光遇后，按亮起的 15 键提示弹奏。悬浮窗可拖动，也可调整透明度。")
        copy.setObjectName("MutedText")
        copy.setWordWrap(True)
        layout.addWidget(copy)
        shortcuts = QLabel("F3 显示 / 隐藏　　F4 鼠标穿透\nF5 / F6 翻谱　　F7 开始翻谱　　F9 自动翻谱\nF8 / Esc 停止")
        shortcuts.setObjectName("MutedText")
        shortcuts.setWordWrap(True)
        layout.addWidget(shortcuts)
        window_hint = QLabel("游戏使用窗口化或无边框模式，悬浮窗才能覆盖游戏画面。")
        window_hint.setObjectName("MutedText")
        window_hint.setWordWrap(True)
        layout.addWidget(window_hint)
        self.game_score_layout.addWidget(card)
        midi_link = QPushButton("用 MIDI 键盘在光遇弹奏 →")
        midi_link.clicked.connect(self.show_game_midi)
        self.game_score_layout.addWidget(midi_link, 0, Qt.AlignLeft)
        self.game_score_layout.addStretch()

    def sync_navigation(self):
        button = self.page_navigation.get(self.pages.currentWidget())
        if button:
            button.setChecked(True)

    def show_game_page(self):
        self.pages.setCurrentWidget(self.game_page)

    def show_score_overlay(self):
        if not self.sorted_times:
            self.pages.setCurrentWidget(self.play_page)
            self.set_status("请先选择曲谱，再打开悬浮琴谱")
            return
        if not self.overlay.score_button.isChecked():
            self.overlay.score_button.setChecked(True)
            self.overlay.toggle_score()
        if self.overlay.lock_button.isChecked():
            self.overlay.set_input_locked(False)
        if not self.overlay.isVisible():
            self.toggle_overlay()

    def toggle_score_overlay(self):
        if self.overlay.isVisible() and self.overlay.score_button.isChecked():
            self.toggle_overlay()
        else:
            self.show_score_overlay()

    def update_game_score(self):
        self.game_song_label.setText(self.name_label.text() if self.selected_file else "未选择曲谱")
        self.game_score_details.setText(
            f"{len(self.sorted_times):,} 组音符 · {self.bpm} BPM · 与播放页共用选曲"
            if self.sorted_times else "先在播放页选择一首曲谱。")

    def build_transcribe_page(self):
        layout = QVBoxLayout(self.transcribe_page)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        title = QLabel("找谱")
        title.setObjectName("BrandTitle")
        layout.addWidget(title)
        self.transcribe_tabs = QTabWidget()
        layout.addWidget(self.transcribe_tabs, 1)
        self.transcribe_search = PiastudySearchPanel(self)
        self.transcribe_search.setObjectName("TranscribePanel")
        self.transcribe_tabs.addTab(self.transcribe_search, "在线搜索")
        for label, attribute in (("本地导入", "transcribe_import_layout"),
                                 ("曲库更新", "transcribe_update_layout")):
            page = QWidget()
            page.setObjectName("TranscribePanel")
            content = QVBoxLayout(page)
            content.setContentsMargins(18, 18, 18, 18)
            content.setSpacing(14)
            setattr(self, attribute, content)
            self.transcribe_tabs.addTab(page, label)
        self.realtime_recognition = RealtimeRecognizerPage(self, SHEET_MUSIC_DIR)
        self.realtime_recognition.setObjectName("TranscribePanel")
        self.transcribe_tabs.addTab(self.realtime_recognition, "实时识曲")

    def show_transcribe(self):
        self.pages.setCurrentWidget(self.transcribe_page)
        self.nav_transcribe.setChecked(True)

    def show_score_editor(self):
        if self.midi_practice.connected_device:
            self.midi_practice.disconnect("已切换到制谱页")
        self.pages.setCurrentWidget(self.score_editor)
        self.nav_compose.setChecked(True)

    def show_midi_practice(self):
        if self.score_editor.connected_device:
            self.score_editor.disconnect_midi("已切换到练习页")
        self.pages.setCurrentWidget(self.midi_practice)

    def show_game_midi(self):
        self.show_game_page()
        self.game_tabs.setCurrentIndex(1)
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def release_midi_ownership(self, owner):
        if owner != "game":
            self.midi_game.disconnect("已切换到制谱或练习输入")
        if owner != "editor":
            self.score_editor.disconnect_midi("已切换 MIDI 输入用途")
        if owner != "practice":
            self.midi_practice.disconnect("已切换 MIDI 输入用途")

    def prepare_game_midi(self):
        self.stop_play()
        self.release_midi_ownership("game")

    def send_game_key(self, key, key_up):
        return send_scan_key(key, key_up)

    def setup_sky_sound_controls(self):
        editor = self.score_editor
        for combo in (editor.sound_combo, self.sky_sound_instrument_combo, self.play_sound_combo):
            combo.setIconSize(QSize(22, 22))
            combo.currentIndexChanged.connect(lambda _, source=combo: self.select_sky_sound(source.currentData()))
        for button in (editor.sound_download_button, self.sky_sound_download_button, self.play_sound_download_button):
            button.clicked.connect(self.download_selected_sky_sound)
        for button in (editor.sound_import_button, self.sky_sound_import_button):
            button.clicked.connect(self.import_sky_sound_folder)
        for button in (editor.sound_source_button, self.sky_sound_source_button):
            button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(SOURCE_URL)))
        self.refresh_sky_sound_choices()

    @staticmethod
    def sky_instrument_icon(name):
        custom = INSTRUMENT_CUSTOM_ICONS.get(name)
        if custom:
            path = ASSET_DIR / "instruments" / custom
            return QIcon(str(path)) if path.is_file() else QIcon()
        code = INSTRUMENT_ICON_CODES.get(name)
        path = ASSET_DIR / "instruments" / f"{code}.png" if code else None
        return QIcon(str(path)) if path and path.is_file() else QIcon()

    def refresh_sky_sound_choices(self):
        choices = [("合成音", "", QIcon())]
        for name in SPECY_INSTRUMENTS:
            choices.append((f"{INSTRUMENT_LABELS[name]} ({name})", f"specy:{name}", self.sky_instrument_icon(name)))
        local_root = self.config.get("sky_sound_local_root")
        if local_root:
            for name, path in instrument_folders(local_root):
                choices.append((f"{INSTRUMENT_LABELS.get(name, name)} · 本地", f"local:{path}", self.sky_instrument_icon(name)))
        if self.sky_sound_id not in {value for _, value, _ in choices}:
            self.sky_sound_id = ""
        for combo in (self.score_editor.sound_combo, self.sky_sound_instrument_combo, self.play_sound_combo):
            combo.blockSignals(True)
            combo.clear()
            for label, value, icon in choices:
                combo.addItem(icon, label, value)
            combo.setCurrentIndex(combo.findData(self.sky_sound_id))
            combo.blockSignals(False)
        self.select_sky_sound(self.sky_sound_id, save=False)

    def select_sky_sound(self, sound_id, save=True):
        sound_id = str(sound_id or "")
        if sound_id != self.sky_sound_id:
            self.stop_sky_sound_players()
        self.sky_sound_id = sound_id
        if sound_id.startswith("specy:"):
            name = sound_id.partition(":")[2]
            required_count = SAMPLE_COUNTS.get(name, 15)
            downloaded = self.sky_sound_root / "Specy" / name
            bundled = ASSET_DIR / "sky_sounds" / "Specy" / name
            downloaded_files = sample_files(downloaded, required_count)
            bundled_files = sample_files(bundled, required_count)
            self.sky_sound_files = downloaded_files or bundled_files
            label = INSTRUMENT_LABELS.get(name, name)
        elif sound_id.startswith("local:"):
            folder = Path(sound_id.partition(":")[2])
            label = folder.name
            self.sky_sound_files = sample_files(folder, SAMPLE_COUNTS.get(folder.name, 15))
        else:
            label = "合成音"
            self.sky_sound_files = {}
        if self.sky_sound_files:
            status = f"当前使用：{label}"
        elif sound_id.startswith("specy:"):
            status = f"{label} 的内置采样缺失；试听暂用合成音。"
        elif sound_id.startswith("local:"):
            status = "本地采样目录无效；试听暂用合成音。"
        else:
            status = "当前使用：合成音"
        for combo in (self.score_editor.sound_combo, self.sky_sound_instrument_combo, self.play_sound_combo):
            index = combo.findData(sound_id)
            if index >= 0 and combo.currentIndex() != index:
                combo.blockSignals(True)
                combo.setCurrentIndex(index)
                combo.blockSignals(False)
        self.score_editor.sound_status.setText(status)
        self.sky_sound_status_label.setText(status)
        downloadable = sound_id.startswith("specy:") and not self.sky_sound_files
        for button in (self.score_editor.sound_download_button, self.sky_sound_download_button):
            button.setEnabled(downloadable and not self.sky_sound_downloading)
            button.setHidden(not downloadable)
        self.play_sound_download_button.setEnabled(downloadable and not self.sky_sound_downloading)
        self.play_sound_download_button.setHidden(not downloadable)
        self.play_sound_download_button.setToolTip("下载所选音色" if downloadable else status)
        if save:
            self.save_config()

    def setup_sky_location_controls(self):
        self.location_presets = {name: (pitch, condition) for name, pitch, condition in LOCATION_PRESETS}
        self.location_presets["其他地点"] = ("C", "")
        for name, (pitch, condition) in self.location_presets.items():
            self.play_location_combo.addItem(name, name)
            index = self.play_location_combo.count() - 1
            tip = f"攻略参考调性：{pitch}"
            if condition:
                tip += f"；{condition}"
            self.play_location_combo.setItemData(index, tip, Qt.ToolTipRole)
        self.play_pitch_combo.addItems(PITCHES)
        if self.sky_location not in self.location_presets:
            self.sky_location = "遇境"
        self.play_location_combo.setCurrentIndex(self.play_location_combo.findData(self.sky_location))
        self.play_location_combo.currentIndexChanged.connect(self.on_sky_location_changed)
        self.play_pitch_combo.currentTextChanged.connect(self.on_sky_pitch_changed)
        self.on_sky_location_changed(save=False)

    def on_sky_location_changed(self, _index=None, save=True):
        location = self.play_location_combo.currentData() or "遇境"
        self.sky_location = location
        default_pitch, condition = self.location_presets[location]
        pitch = self.sky_location_pitches.get(location, default_pitch)
        if pitch not in PITCHES:
            pitch = default_pitch
        self.sky_pitch = pitch
        self.play_pitch_combo.blockSignals(True)
        self.play_pitch_combo.setCurrentText(pitch)
        self.play_pitch_combo.blockSignals(False)
        self.play_location_combo.setToolTip(
            f"{location}：{pitch} 调" + (f"；{condition}" if condition else "")
            + "。实际游戏调性仍以当前背景音乐为准。"
        )
        self.stop_sky_sound_players()
        if save:
            self.save_config()

    def on_sky_pitch_changed(self, pitch):
        if pitch not in PITCHES:
            return
        self.sky_pitch = pitch
        self.sky_location_pitches[self.sky_location] = pitch
        self.stop_sky_sound_players()
        self.save_config()

    def import_sky_sound_folder(self):
        selected = QFileDialog.getExistingDirectory(self, "选择光遇音色采样目录")
        if not selected:
            return
        folders = instrument_folders(selected)
        if not folders:
            QMessageBox.warning(self, "无法导入音色", "请选择包含连续编号采样的乐器目录，或其上一级目录。")
            return
        if len(folders) > 1:
            names = [name for name, _ in folders]
            name, accepted = QInputDialog.getItem(self, "选择乐器", "乐器", names, 0, False)
            if not accepted:
                return
            folder = folders[names.index(name)][1]
        else:
            folder = folders[0][1]
        self.config["sky_sound_local_root"] = selected
        self.stop_sky_sound_players()
        self.sky_sound_id = f"local:{folder}"
        self.refresh_sky_sound_choices()
        self.save_config()

    def download_selected_sky_sound(self):
        if self.sky_sound_downloading or not self.sky_sound_id.startswith("specy:"):
            return
        name = self.sky_sound_id.partition(":")[2]
        self.sky_sound_downloading = True
        self.sky_sound_download_name = name
        self.select_sky_sound(self.sky_sound_id, save=False)
        total = SAMPLE_COUNTS.get(name, 15)
        self.score_editor.sound_status.setText(f"正在下载 {name}：0 / {total}")
        self.sky_sound_status_label.setText(f"正在下载 {name}：0 / {total}")
        self.play_sound_download_button.setToolTip(f"正在下载 {name}：0 / {total}")

        def worker():
            try:
                folder = self.sky_sound_root / "Specy" / name
                download_specy_instrument(name, folder, self.sky_sound_signals.progress.emit)
                self.sky_sound_signals.finished.emit(name)
            except Exception as exc:
                self.sky_sound_signals.failed.emit(str(exc))

        self.sky_sound_thread = threading.Thread(target=worker, daemon=True)
        self.sky_sound_thread.start()

    def on_sky_sound_progress(self, current, total):
        if self.sky_sound_id != f"specy:{self.sky_sound_download_name}":
            return
        status = f"正在下载：{current} / {total}"
        self.score_editor.sound_status.setText(status)
        self.sky_sound_status_label.setText(status)
        self.play_sound_download_button.setToolTip(status)

    def on_sky_sound_finished(self, name):
        self.sky_sound_downloading = False
        self.refresh_sky_sound_choices()
        self.set_status(f"光遇音色 {name} 已下载")

    def on_sky_sound_failed(self, error):
        self.sky_sound_downloading = False
        self.select_sky_sound(self.sky_sound_id, save=False)
        QMessageBox.warning(self, "音色下载失败", f"下载未完成：{error}\n可重试，或选择本地采样目录。")

    def on_score_saved(self, filename):
        self.refresh_files()
        self.start_mobile_remote(silent=True)
        self.publish_mobile_state(include_library=True)
        self.select_file(filename)
        self.set_status(f"已保存制谱：{filename}")

    def select_practice_file(self, filename):
        if not filename:
            self.midi_practice.set_score("未选择曲谱", {})
            return
        try:
            meta, notes = parse_music_file(filename)
            grouped = defaultdict(list)
            for note in notes:
                try:
                    grouped[int(note["time"])].append(note["key"])
                except (KeyError, ValueError, TypeError):
                    continue
            title = clean_song_name(meta.get("songName") or meta.get("name")) or self.display_song_name(filename)
            self.midi_practice.set_score(title, grouped)
        except Exception as exc:
            self.midi_practice.set_score("无法读取曲谱", {})
            self.midi_practice.feedback.setText(str(exc))

    def start_from_shortcut(self):
        if self.pages.currentWidget() is self.midi_practice:
            self.midi_practice.start_practice()
        elif self.pages.currentWidget() is self.game_page:
            if self.game_tabs.currentIndex() == 1:
                self.midi_game.start()
            elif self.game_tabs.currentIndex() == 0:
                self.show_score_overlay()
                if self.overlay.isVisible() and not self.overlay.auto_score_button.isChecked():
                    self.overlay.auto_score_button.click()
            else:
                self.set_status("请在手机端选择曲谱并开始演奏")
        else:
            self.start_play()

    def request_mobile_action(self, action):
        """Queue a phone command on the Qt thread; HTTP worker threads never touch widgets."""
        if not isinstance(action, dict):
            return False
        self.signals.mobile_action.emit(dict(action))
        return True

    def handle_mobile_action(self, action):
        name = action.get("action")
        try:
            if name == "select":
                filename = str(action.get("filename") or action.get("file") or "")
                if filename not in self.all_files:
                    self.set_status("手机请求的曲谱不存在")
                    return
                self.select_file(filename, sync_list=True)
            elif name == "start":
                self.start_play()
            elif name == "stop":
                self.stop_play()
            elif name == "preview":
                self.toggle_preview()
            elif name == "seek":
                value = max(0, min(100, int(float(action.get("value", action.get("percent", 0))))))
                self.progress.setValue(value)
                self.seek_from_slider()
            elif name == "set_speed":
                self.set_speed(max(0.25, min(2.0, float(action.get("value", 1.0)))))
                self.save_config()
            elif name == "set_delay":
                self.delay_slider.setValue(max(0, min(50, int(float(action.get("value", 0))))))
                self.save_config()
            elif name == "set_wrong":
                self.wrong_slider.setValue(max(0, min(30, int(float(action.get("value", 0))))))
                self.save_config()
            else:
                self.set_status("手机请求的操作不受支持")
                return
            self.set_status(f"手机已请求：{name}")
        except (TypeError, ValueError):
            self.set_status("手机请求参数无效")

    def copy_mobile_token(self):
        QApplication.clipboard().setText(self.mobile_remote.token)
        self.mobile_status_label.setText("令牌已复制")
        QTimer.singleShot(1500, self.refresh_mobile_remote_ui)

    def toggle_mobile_remote(self):
        if self.mobile_remote.running:
            self.stop_mobile_remote()
        else:
            self.start_mobile_remote()

    def start_mobile_remote(self, silent=False):
        try:
            port = self.mobile_remote.start()
        except OSError as exc:
            if not silent:
                QMessageBox.warning(self, "手机同步", f"无法启动手机同步服务：{exc}")
            self.refresh_mobile_remote_ui()
            return False
        self.config["mobile_remote_token"] = self.mobile_remote.token
        self.refresh_mobile_remote_ui()
        if not silent:
            urls = self.mobile_remote.pairing_urls()
            address = urls[0] if urls else f"http://127.0.0.1:{port}/"
            self.set_status(f"手机同步已启动：{address}")
        return True

    def stop_mobile_remote(self):
        self.mobile_remote.stop()
        self.refresh_mobile_remote_ui()
        self.set_status("手机同步已停止")

    def refresh_mobile_remote_ui(self):
        if not hasattr(self, "mobile_status_label"):
            return
        if self.mobile_remote.running:
            urls = self.mobile_remote.pairing_urls()
            address = urls[0] if urls else f"http://127.0.0.1:{self.mobile_remote.bound_port}/"
            self.mobile_url_input.setText(address)
            self.mobile_status_label.setText("运行中；手机和电脑需连接同一 Wi-Fi")
            self.mobile_toggle_button.setText("停止手机同步")
        else:
            self.mobile_url_input.clear()
            self.mobile_status_label.setText("尚未启动")
            self.mobile_toggle_button.setText("启动手机同步")

    def publish_mobile_state(self, include_library=False, include_score=False):
        """Publish a JSON-safe snapshot used by both the native app and web fallback."""
        remote = getattr(self, "mobile_remote", None)
        if remote is None:
            return
        snapshot = {
            "app_version": APP_VERSION,
            "status": self.status.text() if hasattr(self, "status") else "",
            "selected_file": self.selected_file,
            "song_title": self.name_label.text() if hasattr(self, "name_label") else "",
            "progress": self.progress.value() if hasattr(self, "progress") else 0,
            "elapsed": self.time_label.text().split(" / ", 1)[0] if hasattr(self, "time_label") else "0:00",
            "duration": self.format_seconds(self.total_seconds()),
            "playing": bool(self.player_thread and self.player_thread.is_alive() and not self.previewing),
            "previewing": bool(self.previewing),
            "speed": round(self.speed(), 3) if hasattr(self, "speed_slider") else 1.0,
            "delay": self.delay_slider.value() if hasattr(self, "delay_slider") else 0,
            "wrong": self.wrong_slider.value() if hasattr(self, "wrong_slider") else 0,
            "current_keys": [NOTE_TO_KEY.get(key, "") for key in getattr(self, "_mobile_note_keys", [])],
        }
        if include_library:
            snapshot["library"] = [
                {
                    "filename": filename,
                    "title": self.display_song_name(filename),
                    "favorite": filename in self.favorites,
                }
                for filename in self.all_files
            ]
        if include_score:
            snapshot["score"] = {
                "filename": self.selected_file,
                "title": snapshot["song_title"],
                "bpm": self.bpm,
                "notes": self.current_score_notes,
            } if self.selected_file else None
        try:
            remote.set_state(snapshot)
        except Exception:
            pass

    def build_about_page(self, about_page):
        page_layout = QVBoxLayout(about_page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("AboutScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("AboutContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)
        scroll.setWidget(content)
        page_layout.addWidget(scroll)

        title = QLabel("关于 SkyAutoMusic")
        title.setObjectName("BrandTitle")
        intro = QLabel("感谢每一位使用、反馈和分享 SkyAutoMusic 的朋友。")
        intro.setObjectName("MutedText")
        intro.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(intro)

        project_card = QFrame()
        project_card.setObjectName("Card")
        project_layout = QVBoxLayout(project_card)
        project_layout.setContentsMargins(14, 12, 14, 12)
        project_layout.setSpacing(5)
        project_title = QLabel("项目信息")
        project_title.setObjectName("CardTitle")
        project_copy = QLabel(
            f"SkyAutoMusic {APP_VERSION} · Windows 自动演奏工具\n"
            "定制曲谱署名：Aknices&&BA4KQS"
        )
        project_copy.setObjectName("MutedText")
        project_copy.setWordWrap(True)
        project_layout.addWidget(project_title)
        project_layout.addWidget(project_copy)
        app_update_row = QHBoxLayout()
        self.app_update_status = QLabel("启动后自动检查软件更新")
        self.app_update_status.setObjectName("MutedText")
        self.app_update_status.setWordWrap(True)
        self.app_update_download_button = QPushButton("下载新版本")
        self.app_update_download_button.setObjectName("PrimaryButton")
        self.app_update_download_button.hide()
        self.app_update_download_button.clicked.connect(self.open_app_update)
        self.app_update_check_button = QPushButton("检查软件更新")
        self.app_update_check_button.setObjectName("CompactButton")
        self.app_update_check_button.clicked.connect(self.check_app_update)
        app_update_row.addWidget(self.app_update_status, 1)
        app_update_row.addWidget(self.app_update_download_button)
        app_update_row.addWidget(self.app_update_check_button)
        project_layout.addLayout(app_update_row)
        layout.addWidget(project_card)

        request_card = QFrame()
        request_card.setObjectName("Card")
        request_layout = QVBoxLayout(request_card)
        request_layout.setContentsMargins(14, 12, 14, 12)
        request_layout.setSpacing(6)
        request_title = QLabel("扒谱请求")
        request_title.setObjectName("CardTitle")
        request_copy = QLabel("交流与求谱")
        request_copy.setObjectName("MutedText")
        request_copy.setWordWrap(True)
        qq_row = QHBoxLayout()
        qq_label = QLabel("作者 QQ：2912173424")
        qq_label.setObjectName("ValueLabel")
        qq_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.copy_qq_btn = QPushButton("复制 QQ")
        self.copy_qq_btn.setObjectName("CompactButton")
        self.copy_qq_btn.setAccessibleName("复制作者 QQ")
        self.copy_qq_btn.clicked.connect(self.copy_author_qq)
        qq_row.addWidget(qq_label)
        qq_row.addStretch()
        qq_row.addWidget(self.copy_qq_btn)
        request_layout.addWidget(request_title)
        request_layout.addWidget(request_copy)
        group_row = QHBoxLayout()
        group_label = QLabel("交流 / 求谱 QQ 群：675830191")
        group_label.setObjectName("ValueLabel")
        group_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.copy_group_btn = QPushButton("复制群号")
        self.copy_group_btn.setObjectName("CompactButton")
        self.copy_group_btn.setAccessibleName("复制 QQ 群号 675830191")
        self.copy_group_btn.clicked.connect(self.copy_group_number)
        group_row.addWidget(group_label)
        group_row.addStretch()
        group_row.addWidget(self.copy_group_btn)
        request_layout.addLayout(group_row)
        request_layout.addLayout(qq_row)
        layout.addWidget(request_card)

        sponsor_card = QFrame()
        sponsor_card.setObjectName("Card")
        sponsor_layout = QHBoxLayout(sponsor_card)
        sponsor_layout.setContentsMargins(14, 12, 14, 12)
        sponsor_layout.setSpacing(12)
        sponsor_text = QVBoxLayout()
        sponsor_title = QLabel("支持开发")
        sponsor_title.setObjectName("CardTitle")
        sponsor_copy = QLabel("如果 SkyAutoMusic 对你有帮助，可以请作者喝杯饮料。")
        sponsor_copy.setObjectName("MutedText")
        sponsor_copy.setWordWrap(True)
        sponsor_text.addWidget(sponsor_title)
        sponsor_text.addWidget(sponsor_copy)
        sponsor_button = QPushButton("赞助我")
        sponsor_button.setObjectName("PrimaryButton")
        sponsor_button.setAccessibleName("打开赞助二维码")
        sponsor_button.setMinimumSize(130, 42)
        sponsor_button.clicked.connect(self.show_sponsor_dialog)
        sponsor_layout.addLayout(sponsor_text, 1)
        sponsor_layout.addWidget(sponsor_button)
        layout.addWidget(sponsor_card)
        layout.addStretch()

    def copy_author_qq(self):
        QApplication.clipboard().setText("2912173424")
        self.copy_qq_btn.setText("已复制")
        QTimer.singleShot(1500, lambda: self.copy_qq_btn.setText("复制 QQ"))

    def copy_group_number(self):
        QApplication.clipboard().setText("675830191")
        self.copy_group_btn.setText("已复制")
        self.set_status("已复制 QQ 群号：675830191")
        QTimer.singleShot(1500, lambda: self.copy_group_btn.setText("复制群号"))

    def show_sponsor_dialog(self):
        dialog = QDialog(self)
        dialog.setObjectName("SponsorDialog")
        dialog.setWindowTitle("赞助 SkyAutoMusic")
        dialog.setModal(True)
        dialog.resize(660, 570)
        dialog.setMinimumSize(600, 520)
        dialog.setStyleSheet(self.styleSheet())

        root = QVBoxLayout(dialog)
        root.setContentsMargins(22, 20, 22, 18)
        root.setSpacing(12)
        title = QLabel("感谢支持")
        title.setObjectName("ThemeDialogTitle")
        copy = QLabel("开发不易，感谢你的每一份支持。请选择支付宝或微信扫码赞助。")
        copy.setObjectName("MutedText")
        copy.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(copy)

        codes = QHBoxLayout()
        codes.setSpacing(18)
        for label_text, filename in (
            ("支付宝", "sponsor-alipay.jpg"),
            ("微信", "sponsor-wechat.png"),
        ):
            column = QVBoxLayout()
            heading = QLabel(label_text)
            heading.setObjectName("CardTitle")
            heading.setAlignment(Qt.AlignCenter)
            image_label = QLabel()
            image_label.setObjectName("SponsorQr")
            image_label.setAlignment(Qt.AlignCenter)
            image_label.setMinimumSize(240, 360)
            image_label.setAccessibleName(f"{label_text}赞助二维码")
            pixmap = QPixmap(str(ASSET_DIR / filename))
            if pixmap.isNull():
                image_label.setText("二维码资源缺失")
            else:
                image_label.setPixmap(
                    pixmap.scaled(QSize(250, 380), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                )
            column.addWidget(heading)
            column.addWidget(image_label, 1)
            codes.addLayout(column, 1)
        root.addLayout(codes, 1)

        close_button = QPushButton("完成")
        close_button.setMinimumHeight(38)
        close_button.clicked.connect(dialog.accept)
        root.addWidget(close_button)
        dialog.exec()

    def apply_theme(self, theme_name):
        if theme_name not in THEMES:
            return
        self.theme_name = theme_name
        self.apply_style()
        if hasattr(self, "theme_combo"):
            self.theme_combo.blockSignals(True)
            self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(theme_name)))
            self.theme_combo.blockSignals(False)

    @staticmethod
    def tinted_icon_pixmap(icon, color, size=23):
        pixmap = icon.pixmap(size, size)
        painter = QPainter(pixmap)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), QColor(color))
        painter.end()
        return pixmap

    def refresh_nav_icons(self):
        replacements = THEMES[self.theme_name]["replacements"]
        normal_color = replacements.get("#98A3B4", "#98A3B4")
        active_color = replacements.get("#5B86FF", "#5B86FF")
        for button, standard_icon in self.nav_icon_specs:
            source = self.style().standardIcon(standard_icon)
            icon = QIcon()
            icon.addPixmap(self.tinted_icon_pixmap(source, normal_color), QIcon.Normal, QIcon.Off)
            icon.addPixmap(self.tinted_icon_pixmap(source, active_color), QIcon.Normal, QIcon.On)
            button.setIcon(icon)
        if hasattr(self, "overlay") and hasattr(self.overlay, "close_btn"):
            close_source = self.style().standardIcon(QStyle.SP_TitleBarCloseButton)
            self.overlay.close_btn.setIcon(QIcon(self.tinted_icon_pixmap(close_source, normal_color, 12)))

    def on_theme_combo_changed(self):
        theme_name = self.theme_combo.currentData()
        if theme_name in THEMES:
            self.theme_confirmed = True
            self.apply_theme(theme_name)
            self.save_config()
            self.set_status(f"已切换主题：{THEMES[theme_name]['label']}")

    def show_theme_chooser(self):
        previous_theme = self.theme_name
        dialog = QDialog(self)
        dialog.setObjectName("ThemeDialog")
        dialog.setWindowTitle("选择你的界面主题")
        dialog.setModal(True)
        dialog.setMinimumWidth(660)
        dialog.setWindowFlag(Qt.WindowContextHelpButtonHint, False)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(16)
        title = QLabel("第一次使用，先选择界面主题")
        title.setObjectName("ThemeDialogTitle")
        subtitle = QLabel("可以立即预览；之后也能在“设置”里随时更改。")
        subtitle.setObjectName("MutedText")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        choices = QHBoxLayout()
        choices.setSpacing(10)
        group = QButtonGroup(dialog)
        group.setExclusive(True)
        selected = {"theme": self.theme_name}
        for key, theme in THEMES.items():
            button = QPushButton(f"{theme['label']}\n{theme['description']}")
            button.setObjectName("ThemeChoice")
            button.setCheckable(True)
            button.setChecked(key == self.theme_name)
            button.setMinimumHeight(92)
            button.clicked.connect(lambda checked=False, value=key: (
                selected.__setitem__("theme", value), self.apply_theme(value)
            ))
            group.addButton(button)
            choices.addWidget(button, 1)
        layout.addLayout(choices)

        confirm = QPushButton("使用这个主题")
        confirm.setObjectName("PrimaryButton")
        confirm.setMinimumHeight(44)
        confirm.clicked.connect(dialog.accept)
        layout.addWidget(confirm)

        accepted = dialog.exec() == QDialog.Accepted
        if accepted:
            self.theme_confirmed = True
            self.config["theme"] = selected["theme"]
            self.apply_theme(selected["theme"])
            self.save_config()
        else:
            self.apply_theme(previous_theme)
        return accepted

    def apply_style(self):
        stylesheet = """
            QMainWindow { background: #090C12; }
            QWidget { color: #F5F7FB; font-family: "Microsoft YaHei UI", "Microsoft YaHei"; font-size: 14px; }
            #AppShell { background: #090C12; border: 1px solid #2B3445; border-radius: 6px; }
            #AppContent, #Pages, #PlayPage, #HelpPage, #HelpScroll, #HelpContent, #AboutPage, #AboutScroll, #AboutContent, #PracticePage, #PracticeScroll, #PracticeContent, #TranscribePage, #TranscribePanel, #ScoreEditorPage, #ScoreEditorBody, #ScoreEditorScroll, #GamePage, #GameScroll, #GameContent { background: #090C12; border: 0; }
            #WindowTitleBar { background: #0C1118; border-bottom: 1px solid #2B3445; }
            #TitleBarBrand { color: #F5F7FB; font-size: 15px; font-weight: 700; }
            #WindowTitleBar QToolButton { background: transparent; border: 0; border-radius: 3px; }
            #WindowTitleBar QToolButton:hover { background: #1B2330; }
            #WindowTitleBar QToolButton:pressed { background: #252E3C; }
            #WindowTitleBar QToolButton#TitleBarClose:hover { background: #B64654; }
            #NavigationRail { background: #0C1118; border-right: 1px solid #2B3445; }
            QToolButton#NavButton { background: transparent; color: #98A3B4; border: 1px solid transparent; border-radius: 4px; padding: 8px 4px; font-weight: 600; }
            QToolButton#NavButton:hover { background: #151C27; color: #F5F7FB; }
            QToolButton#NavButton:checked { background: #151C27; color: #F5F7FB; border-left: 4px solid #5B86FF; }
            #NavStatus { color: #78CFA0; font-size: 12px; padding: 6px 2px; }
            #LibraryPanel { background: #0F151E; border-right: 1px solid #2B3445; }
            #CenterWorkspace { background: #0D121A; }
            #InspectorPanel { background: #0F151E; border-left: 1px solid #2B3445; }
            #TransportBar { background: #0E141D; border-top: 1px solid #2B3445; }
            #BrandTitle { color: #F5F7FB; font-size: 27px; font-weight: 750; }
            #SectionTitle { color: #F5F7FB; font-size: 23px; font-weight: 750; }
            #CardTitle, #InspectorTitle { color: #F5F7FB; font-size: 15px; font-weight: 700; }
            #InspectorTitle { font-size: 16px; }
            #MutedText, #InspectorLabel { color: #98A3B4; }
            #CountBadge, #ValueBadge, #KeyCap { background: #151C27; color: #DCE4F2; border: 1px solid #2B3445; border-radius: 3px; padding: 4px 9px; font-weight: 650; }
            #KeyCap { min-width: 34px; padding: 6px 8px; }
            #Card { background: #111722; border: 1px solid #2B3445; border-radius: 6px; }
            #ScoreEditorPage QTableWidget { background: #0C1118; color: #DCE4F2; border: 1px solid #2B3445; border-radius: 4px; gridline-color: #252E3C; }
            #ScoreEditorPage QHeaderView::section { background: #151C27; color: #98A3B4; border: 0; border-bottom: 1px solid #2B3445; padding: 7px; font-weight: 650; }
            QPushButton#SkyNoteButton { background: #24343B; color: #F3F2E8; border: 2px solid #7F989B; border-radius: 32px; padding: 4px; font-size: 12px; font-weight: 700; }
            QPushButton#SkyNoteButton:hover { background: #35535B; border-color: #B7D5D3; }
            QPushButton#SkyNoteButton:checked, QPushButton#SkyNoteButton:pressed { background: #9B784C; color: #FFFFFF; border-color: #F0D4A1; }
            QTabWidget::pane { border: 1px solid #2B3445; border-radius: 4px; }
            QTabBar::tab { background: #0C1118; color: #98A3B4; padding: 10px 22px; }
            QTabBar::tab:selected { background: #151C27; color: #F5F7FB; border-bottom: 2px solid #5B86FF; }
            #NowPlayingSection { border: 0; }
            #ProgressSection { border-top: 1px solid #2B3445; border-bottom: 1px solid #2B3445; }
            #SettingsSection { border: 0; }
            #EffectsSection { border-left: 1px solid #2B3445; }
            #Divider { color: #2B3445; background: #2B3445; border: 0; max-height: 1px; }
            #HintBox { background: #111722; border: 1px solid #2B3445; border-radius: 4px; }
            #ImportHint { background: #111722; color: #C7CFDC; border: 1px solid #3A4659; border-radius: 4px; padding: 9px 11px; }
            #Eyebrow { color: #5B86FF; font-size: 12px; font-weight: 750; }
            #CurrentCaption { color: #C8D0DC; font-size: 13px; font-weight: 650; margin-top: 5px; }
            #SongTitle { color: #F5F7FB; font-size: 32px; font-weight: 750; }
            #FieldLabel { color: #98A3B4; font-weight: 650; min-width: 52px; }
            #Filename { color: #B5BECC; }
            #ValueLabel { color: #C7CFDC; font-weight: 650; }
            QSplitter::handle { background: transparent; width: 1px; }
            QLineEdit, QComboBox, QSpinBox { background: #0C1118; border: 1px solid #2B3445; border-radius: 4px; padding: 7px 9px; selection-background-color: #5B86FF; min-height: 17px; }
            QTextBrowser { background: #111722; color: #F5F7FB; border: 1px solid #2B3445; padding: 12px; }
            QLineEdit:hover, QComboBox:hover { border-color: #3D485C; }
            QLineEdit:focus, QComboBox:focus { border-color: #776BFF; }
            QComboBox::drop-down { border: 0; width: 25px; }
            QComboBox QAbstractItemView { background: #111722; color: #F5F7FB; border: 1px solid #2B3445; selection-background-color: #243250; outline: 0; }
            QListWidget { background: #0C1118; border: 1px solid #2B3445; border-radius: 4px; padding: 5px; outline: 0; }
            QListWidget::item { color: #C2CAD7; border-radius: 4px; padding: 9px 10px; }
            QListWidget::item:hover { background: #151C27; color: #F5F7FB; }
            QListWidget::item:selected { background: #172131; color: #F5F7FB; border: 1px solid #3F68C7; border-left: 4px solid #5B86FF; }
            QPushButton { background: #151C27; color: #E2E7EF; border: 1px solid #2B3445; border-radius: 4px; padding: 6px 10px; font-weight: 650; }
            QPushButton:hover { background: #1C2532; border-color: #48556C; }
            QPushButton:pressed { background: #101620; border-color: #5B86FF; }
            QPushButton:focus { border-color: #776BFF; }
            QPushButton:disabled { color: #596475; background: #10151D; border-color: #202837; }
            QPushButton#CompactButton { padding: 4px 4px; font-size: 12px; }
            QPushButton#PrimaryButton, QPushButton#OverlayPrimary { background: #5B86FF; color: #F8FAFF; border-color: #6D94FF; }
            QPushButton#PrimaryButton:hover, QPushButton#OverlayPrimary:hover { background: #6B92FF; }
            QPushButton#DangerButton, QPushButton#OverlayDanger { background: #2B171D; color: #FF8795; border-color: #873846; }
            QPushButton#DangerButton:hover, QPushButton#OverlayDanger:hover { background: #3A1D25; border-color: #D35B68; }
            QSlider::groove:horizontal { height: 6px; background: #2B3445; border-radius: 2px; }
            QSlider::sub-page:horizontal { background: #5B86FF; border-radius: 2px; }
            QSlider::handle:horizontal { background: #F5F7FB; border: 3px solid #5B86FF; width: 12px; margin: -6px 0; border-radius: 3px; }
            QProgressBar { border: 0; background: #2B3445; border-radius: 3px; height: 8px; text-align: center; color: transparent; }
            QProgressBar::chunk { background: #5B86FF; border-radius: 3px; }
            #Status { background: #111722; color: #9DB8FF; border: 1px solid #2B3445; border-radius: 4px; padding: 8px 9px; font-size: 12px; }
            QCheckBox#OverlaySwitch { spacing: 0; }
            QCheckBox#OverlaySwitch::indicator { width: 38px; height: 22px; border: 1px solid #3A4659; border-radius: 4px; background: #151C27; }
            QCheckBox#OverlaySwitch::indicator:checked { background: #5B86FF; border-color: #6D94FF; }
            QScrollBar:vertical { background: transparent; width: 9px; margin: 3px; }
            QScrollBar::handle:vertical { background: #354054; min-height: 30px; border-radius: 3px; }
            QScrollBar::handle:vertical:hover { background: #49566C; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QMenu { background: #111722; color: #F5F7FB; border: 1px solid #2B3445; padding: 5px; }
            QMenu::item { padding: 7px 20px; border-radius: 3px; }
            QMenu::item:selected { background: #243250; }
            QToolTip { background: #151C27; color: #F5F7FB; border: 1px solid #3D485C; padding: 5px; }
            #FloatingControl { background: transparent; }
            #OverlaySurface { background: #111722; color: #F5F7FB; border: 1px solid #3A4659; border-radius: 8px; }
            #OverlaySurface QLabel { background: transparent; color: #DCE3EE; }
            #OverlayNowPlaying { background: #0C1118; border: 1px solid #2B3445; border-radius: 6px; }
            #OverlaySurface QPushButton { background: #151C27; color: #F5F7FB; border: 1px solid #344054; border-radius: 4px; padding: 4px 8px; }
            #OverlaySurface QPushButton:hover { background: #1C2532; border-color: #4A5870; }
            #OverlaySurface QComboBox { background: #0C1118; color: #F5F7FB; border: 1px solid #344054; border-radius: 4px; padding: 4px 7px; min-height: 16px; }
            #OverlaySurface QSlider::groove:horizontal { height: 5px; background: #2B3445; border-radius: 2px; }
            #OverlaySurface QSlider::sub-page:horizontal { background: #5B86FF; border-radius: 2px; }
            #OverlaySurface QSlider::handle:horizontal { background: #F5F7FB; border: 2px solid #5B86FF; width: 11px; margin: -5px 0; border-radius: 3px; }
            #OverlayTitle { color: #F5F7FB; font-size: 14px; font-weight: 750; }
            #OverlayBadge, #OverlayPercent { background: #151C27; color: #ABC0FF; border: 1px solid #344054; border-radius: 3px; padding: 1px 6px; font-size: 11px; font-weight: 650; }
            #OverlaySong { color: #F5F7FB; font-size: 15px; font-weight: 750; }
            #OverlayTime, #OverlayKeys, #OverlayStatus, #OverlayFieldLabel { color: #98A3B4; font-size: 11px; }
            #OverlayKeys { color: #C8D0DC; }
            #OverlayStatus { padding-left: 2px; }
            #OverlayClose { color: #B5BECC; background: transparent; }
            #ThemeDialog { background: #111722; }
            #ThemeDialogTitle { color: #F5F7FB; font-size: 22px; font-weight: 750; }
            #SponsorDialog { background: #111722; }
            #SponsorQr { background: #0C1118; border: 1px solid #2B3445; border-radius: 5px; padding: 6px; }
            QPushButton#ThemeChoice { text-align: left; padding: 12px 14px; }
            QPushButton#ThemeChoice:checked { background: #172131; border: 2px solid #5B86FF; }
        """
        for source, target in THEMES[self.theme_name]["replacements"].items():
            stylesheet = stylesheet.replace(source, target)
        transport_overrides = {
            "carbon": """
                #TransportBar QPushButton#PrimaryButton { background: #151719; color: #F5F7FB; border-color: #555B62; }
                #TransportBar QPushButton#DangerButton { background: #4A1D22; color: #FF8795; border-color: #B84752; }
            """,
            "warm": """
                #TransportBar QPushButton#DangerButton { background: #7A2B30; color: #FFF4EF; border-color: #B64A50; }
            """,
            "editorial": """
                QListWidget::item:selected { color: #FFFFFF; }
                #TransportBar QPushButton#PrimaryButton { background: #FFFFFF; color: #171717; border-color: #171717; }
                #TransportBar QPushButton#DangerButton { background: #FFFFFF; color: #9E2828; border-color: #AF3A36; }
            """,
        }
        stylesheet += transport_overrides[self.theme_name]
        self.setStyleSheet(stylesheet)
        self.refresh_nav_icons()
        if hasattr(self, "overlay"):
            self.overlay.setStyleSheet(self.styleSheet())
            self.overlay.score_view.set_theme(THEMES[self.theme_name]["replacements"])

    def refresh_files(self):
        self.all_files = sorted([p.name for p in SHEET_MUSIC_DIR.glob("*.json")], key=str.lower)
        self.refresh_list()
        if self.all_files and not self.selected_file:
            saved = self.config.get("last_selected_file")
            self.select_file(saved if saved in self.all_files else self.all_files[0], sync_list=True)
        self.publish_mobile_state(include_library=True)

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
            if self.overlay.isVisible():
                self.overlay.refresh()
            self.publish_mobile_state(include_library=True)

    @staticmethod
    def display_song_name(filename):
        name = clean_song_name(Path(filename).stem)
        return name or Path(filename).stem

    def filtered_files(self):
        keyword = self.search.text().strip().lower()
        files = list(self.all_files)
        if keyword:
            # Searching always looks at the whole library: the favourites tab
            # must not make an existing song look missing.
            tokens = [part for part in re.split(r"\s+", keyword) if part]
            normalized = normalize_search_text(keyword)
            indexed = self.search_index.ready
            matched = []
            for filename in files:
                haystack = f"{filename.lower()} {self.display_song_name(filename).lower()}"
                if normalized and normalized in normalize_search_text(haystack):
                    matched.append(filename)
                    continue
                if indexed:
                    haystack += " " + self.search_index.text_for(filename)
                if all(token in haystack for token in tokens):
                    matched.append(filename)
            return matched
        if self.mode_tabs.currentText() == "收藏曲谱":
            files = [f for f in files if f in self.favorites]
        return files

    def refresh_list(self):
        self.midi_practice.set_library(self.all_files, self.display_song_name)
        current = self.selected_file
        self.display_files = self.filtered_files()
        if hasattr(self, "song_count_label"):
            visible = len(self.display_files)
            total = len(self.all_files)
            total_text = f"{total / 1000:.1f}k" if total >= 10000 else f"{total:,}"
            visible_text = f"{visible / 1000:.1f}k" if visible >= 10000 else f"{visible:,}"
            count = f"{total_text} 首" if visible == total else f"{visible_text} / {total_text} 首"
            self.song_count_label.setText(count)
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
            if filename == current:
                self.list_widget.setCurrentItem(item)
        self.list_widget.blockSignals(False)
        query = self.search.text().strip()
        self.not_found_panel.setVisible(bool(query and not self.display_files))
        if query and not self.display_files:
            self.piastudy_search_btn.setToolTip(f"搜索：{query}")
        if hasattr(self, "overlay") and self.overlay.isVisible():
            self.overlay.refresh()

    def show_song_menu(self, pos):
        item = self.list_widget.itemAt(pos)
        if not item:
            return
        filename = item.data(Qt.UserRole)
        menu = QMenu(self)
        label = "取消收藏" if filename in self.favorites else "收藏"
        action = QAction(label, menu)
        action.triggered.connect(lambda: self.toggle_favorite(filename))
        menu.addAction(action)
        menu.addSeparator()
        delete_action = menu.addAction("删除曲谱…")
        delete_action.setEnabled(not self.update_running)
        delete_action.triggered.connect(lambda: self.delete_song(filename))
        menu.exec(self.list_widget.viewport().mapToGlobal(pos))
        menu.deleteLater()

    def delete_song(self, filename):
        if self.update_running:
            QMessageBox.information(self, "请稍候", "曲库正在更新或导入，请完成后再删除曲谱。")
            return
        path = SHEET_MUSIC_DIR / filename
        if (filename not in self.all_files or Path(filename).name != filename
                or path.resolve().parent != SHEET_MUSIC_DIR.resolve()):
            return
        answer = QMessageBox.question(
            self, "删除曲谱",
            f"将《{self.display_song_name(filename)}》移到回收站？\n\n"
            f"文件：{filename}\n可从 Windows 回收站恢复。收藏和本曲速度预设也会移除。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        # The confirmation runs a nested event loop: an import may have started.
        if self.update_running:
            QMessageBox.information(self, "请稍候", "曲库正在更新或导入，请完成后再删除曲谱。")
            return
        file = QFile(str(path))
        if not file.moveToTrash():
            QMessageBox.warning(self, "删除失败", f"无法将曲谱移到回收站：\n{file.errorString()}")
            return
        if self.selected_file == filename:
            self.clear_selected_song()
        self.favorites.discard(filename)
        self.speed_presets.pop(filename, None)
        self.playback_positions.pop(filename, None)
        self.saved_song_sections.pop(filename, None)
        write_json(FAVORITES_FILE, sorted(self.favorites))
        self.save_config()
        self.all_files = sorted([p.name for p in SHEET_MUSIC_DIR.glob("*.json")], key=str.lower)
        self.refresh_list()
        self.overlay.refresh()
        self.set_status(f"已移到回收站：{self.display_song_name(filename)}")

    def clear_selected_song(self):
        self.stop_play()
        self.overlay.set_score({})
        self.selected_file = None
        self.meta = {}
        self.notes_by_time = defaultdict(list)
        self.sorted_times = []
        self.current_score_notes = []
        self.position_index = self.resume_index = 0
        self.playback_complete = False
        self.song_sections = []
        self.sections_ready = False
        self.score_signature = ""
        self.refresh_section_choices()
        if self.sections_dialog:
            self.sections_dialog.hide()
        self.bpm = 120
        self.name_label.setText("请选择一首乐谱")
        self.song_summary_label.setText("—")
        for label in (self.author_label, self.transcribed_label, self.filename_label):
            label.setText("—")
        self.overlay.song_label.setText("请选择一首乐谱")
        self.update_game_score()
        self.overlay.keys_label.setText("当前按键：—")
        self.set_progress(0, 0)
        self.on_speed_changed()

    def toggle_favorite(self, filename):
        if filename in self.favorites:
            self.favorites.remove(filename)
        else:
            self.favorites.add(filename)
        write_json(FAVORITES_FILE, sorted(self.favorites))
        self.refresh_list()
        if self.overlay.isVisible():
            self.overlay.refresh()

    def on_list_selection(self):
        items = self.list_widget.selectedItems()
        if items:
            self.select_file(items[0].data(Qt.UserRole), sync_list=False)

    def select_file(self, filename, sync_list=True):
        if filename not in self.all_files:
            return
        if filename == self.selected_file and self.playback_session:
            return
        if filename != self.selected_file and (self.previewing or self.playback_session or
                                                (self.player_thread and self.player_thread.is_alive())):
            self.stop_play()
        try:
            meta, notes = parse_music_file(filename)
        except Exception as exc:
            QMessageBox.critical(self, "错误", str(exc))
            return
        self.remember_position()
        self.selected_file = filename
        self.meta = meta
        groups = note_groups(notes)
        self.current_score_notes = [{"time": time, "key": f"1Key{pitch}"}
                                    for time, pitches in groups for pitch in pitches]
        try:
            self.bpm = int(self.meta.get("bpm", 120) or 120)
        except (TypeError, ValueError, OverflowError):
            self.bpm = 120
        self.notes_by_time = defaultdict(list)
        for note in self.current_score_notes:
            self.notes_by_time[note["time"]].append(note["key"])
        self.sorted_times = sorted(self.notes_by_time.keys())
        self.score_signature = score_fingerprint(self.current_score_notes)
        self.restore_position()
        self.song_sections = []
        self.sections_ready = False
        stored = self.saved_song_sections.get(filename)
        if isinstance(stored, dict) and stored.get("signature") == self.score_signature:
            try:
                self.song_sections = validate_sections(stored.get("sections"), groups)
                self.sections_ready = True
            except ValueError:
                pass
        self.refresh_section_choices()
        if self.sections_dialog:
            self.sections_dialog.hide()
        self.overlay.set_score(self.notes_by_time)
        raw_title = self.meta.get("songName") or self.meta.get("name") or self.display_song_name(filename)
        self.name_label.setText(clean_song_name(raw_title) or self.display_song_name(filename))
        self.author_label.setText(self.meta.get("author") or "未知作者")
        self.transcribed_label.setText(self.meta.get("transcribedBy") or self.meta.get("transcriber") or "未署名")
        self.filename_label.setText(self.display_song_name(filename))
        self.song_summary_label.setText(f"{len(notes):,} 个音符")
        self.overlay.song_label.setText(self.name_label.text() or Path(filename).stem)
        self.update_game_score()
        self.apply_speed_preset(filename)
        self.on_speed_changed()
        self.show_position()
        self.update_total_time()
        self.set_status(f"已选择乐谱: {filename}")
        if sync_list:
            self.list_widget.blockSignals(True)
            for i in range(self.list_widget.count()):
                if self.list_widget.item(i).data(Qt.UserRole) == filename:
                    self.list_widget.setCurrentRow(i)
                    break
            self.list_widget.blockSignals(False)
        if self.overlay.isVisible():
            self.overlay.refresh()
        self.publish_mobile_state(include_score=True)
        self.remember_position()
        self.save_config()

    def overlay_song_files(self):
        if self.overlay_mode == "收藏":
            return [f for f in self.all_files if f in self.favorites]
        return list(self.all_files)

    def open_piastudy_search(self, query=""):
        self.show_transcribe()
        self.transcribe_tabs.setCurrentIndex(0)
        query = (query or "").strip()
        if query.lower().startswith(("https://piastudy.com/", "http://piastudy.com/", "https://www.piastudy.com/", "http://www.piastudy.com/")):
            self.transcribe_search.url_box.setText(query)
            self.transcribe_search.url_box.setFocus()
        else:
            self.transcribe_search.search_box.setText(query)
            self.transcribe_search.search_box.setFocus()

    def choose_midi_import(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 MIDI 文件", "", "MIDI 文件 (*.mid *.midi)"
        )
        if path:
            self.start_midi_convert(Path(path))

    def start_midi_convert(self, midi_path):
        if self.update_running:
            return
        self.update_running = True
        self.update_task_kind = "midi"
        self.set_update_controls_enabled(False)
        self.show_transcribe()
        self.transcribe_tabs.setCurrentIndex(1)
        self.set_task_progress_range(0, 0)
        self.set_task_status(f"正在转换：{midi_path.name}")
        threading.Thread(
            target=self._midi_worker, args=(midi_path,), daemon=True
        ).start()

    def _midi_worker(self, midi_path):
        try:
            result = piastudy.install_from_midi(midi_path, SHEET_MUSIC_DIR)
            self.midi_signals.convert_done.emit(result, "")
        except Exception as exc:
            self.midi_signals.convert_done.emit({}, str(exc))

    def on_midi_convert_done(self, result, error):
        self.update_running = False
        self.set_update_controls_enabled(True)
        self.set_task_progress_range(0, 100)
        if error:
            QMessageBox.warning(self, "MIDI 导入失败", str(error))
            self.update_progress.setValue(0)
            self.set_task_status("MIDI 导入失败")
            return
        self.refresh_files()
        self.update_progress.setValue(100)
        self.set_task_status(f"已导入：{result.get('song_name')}")

    def contact_author_qq(self):
        QApplication.clipboard().setText("2912173424")
        QMessageBox.information(self, "联系作者", "作者 QQ 已复制：2912173424")

    def open_sheet_request(self, song_name=""):
        query = clean_song_name(song_name) or "未填写歌名"
        params = urlencode({"request": "1", "title": query})
        QDesktopServices.openUrl(QUrl(f"{PROJECT_SITE}?{params}#request"))
        self.set_status(f"已打开扒谱请求：{query}")

    def on_admin_mode_changed(self, checked):
        self.admin_mode = bool(checked)
        self.save_config()
        if not checked:
            self.set_status("管理员模式已关闭，下次启动恢复普通权限")
            return
        if is_admin():
            self.set_status("管理员模式已开启")
            return
        answer = QMessageBox.question(
            self,
            "开启管理员模式",
            "设置已保存。现在重启并请求管理员权限吗？\n\n以后每次启动都会显示 Windows 权限确认。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        if answer == QMessageBox.Yes:
            if request_admin_restart():
                QApplication.quit()
            else:
                QMessageBox.information(self, "未切换权限", "管理员启动已取消；本次继续使用普通权限。")

    def confirm_admin_tip_before_play(self):
        if os.name != "nt" or is_admin() or self.admin_tip_count >= 3:
            return True
        self.admin_tip_count += 1
        self.save_config()
        box = QMessageBox(self)
        box.setWindowTitle("游戏无法响应按键？")
        box.setIcon(QMessageBox.Information)
        box.setText("如果游戏没有响应按键，请在“设置”中开启管理员模式。")
        box.setInformativeText("开启后，SkyAutoMusic 每次启动都会请求管理员权限。此提示只在前三次演奏时出现。")
        continue_button = box.addButton("继续普通模式", QMessageBox.AcceptRole)
        admin_button = box.addButton("开启管理员模式", QMessageBox.ActionRole)
        box.setDefaultButton(continue_button)
        box.exec()
        if box.clickedButton() != admin_button:
            return True
        self.admin_mode = True
        self.admin_mode_check.setChecked(True)
        self.save_config()
        if request_admin_restart():
            QApplication.quit()
        else:
            QMessageBox.information(self, "未切换权限", "管理员启动已取消；你可以稍后在设置中重试。")
        return False

    def load_music(self):
        if not self.selected_file:
            QMessageBox.warning(self, "提示", "请先选择乐谱")
            return False
        if not self.sorted_times:
            self.select_file(self.selected_file, sync_list=False)
        return bool(self.sorted_times)

    def refresh_section_choices(self):
        self.section_combo.blockSignals(True)
        self.section_combo.clear()
        self.section_combo.addItem("全曲", None)
        for section in self.song_sections:
            suffix = " · 估计" if section.estimated else ""
            self.section_combo.addItem(section.name + suffix, section)
        self.section_combo.blockSignals(False)
        self.section_button.setEnabled(bool(self.sorted_times))

    def show_song_sections(self):
        if not self.load_music():
            return
        if not self.sections_ready:
            self.store_song_sections(estimate_sections(self.current_score_notes))
        if not self.sections_dialog:
            self.sections_dialog = SectionEditor(self)
        if not self.sections_dialog.isVisible():
            self.sections_dialog.set_song()
        self.sections_dialog.show()
        self.sections_dialog.raise_()

    def store_song_sections(self, sections):
        selected = self.section_combo.currentData()
        if self.playback_session or (self.player_thread and self.player_thread.is_alive()):
            self.stop_play()
        self.song_sections = sections
        self.sections_ready = True
        self.saved_song_sections[self.selected_file] = {
            "signature": self.score_signature,
            "sections": [section.to_dict() for section in sections],
        }
        self.refresh_section_choices()
        if selected in sections:
            self.section_combo.blockSignals(True)
            self.section_combo.setCurrentIndex(sections.index(selected) + 1)
            self.section_combo.blockSignals(False)
        self.save_config()

    def on_section_selected(self, *_):
        if not self.sorted_times:
            return
        if self.playback_session or (self.player_thread and self.player_thread.is_alive()):
            self.stop_play()
        section = self.section_combo.currentData()
        if section:
            self.position_index = self.resume_index = bisect_left(self.sorted_times, section.start_ms)
            self.playback_complete = False
            self.show_position()
            self.remember_position()
            self.save_config()
            self.set_status(f"已选择 {section.name}，播放至本段结束")

    def play_section(self, section, preview=False, loop=False, immediate=False):
        self.stop_play()
        self.section_combo.blockSignals(True)
        self.section_combo.setCurrentIndex(self.song_sections.index(section) + 1)
        self.section_combo.blockSignals(False)
        self.section_loop.setChecked(loop)
        self.position_index = self.resume_index = bisect_left(self.sorted_times, section.start_ms)
        self.playback_complete = False
        self.show_position()
        if preview:
            self.start_preview()
        else:
            self.start_play(immediate=immediate)

    def remember_position(self):
        if not self.selected_file or not self.sorted_times:
            return
        current = min(len(self.sorted_times) - 1, max(0, self.position_index))
        next_time = self.sorted_times[self.resume_index] if 0 <= self.resume_index < len(self.sorted_times) else None
        self.playback_positions[self.selected_file] = {
            "signature": self.score_signature,
            "time_ms": self.sorted_times[current], "next_ms": next_time,
            "complete": self.playback_complete,
        }

    def restore_position(self):
        self.position_index = self.resume_index = 0
        self.playback_complete = False
        stored = self.playback_positions.get(self.selected_file)
        if not self.sorted_times or not isinstance(stored, dict) or stored.get("signature") != self.score_signature:
            return
        try:
            self.position_index = min(len(self.sorted_times)-1, bisect_left(self.sorted_times, int(stored["time_ms"])))
            self.resume_index = (bisect_left(self.sorted_times, int(stored["next_ms"]))
                                 if stored.get("next_ms") is not None else len(self.sorted_times))
            self.playback_complete = bool(stored.get("complete"))
        except (KeyError, TypeError, ValueError, OverflowError):
            self.position_index = self.resume_index = 0

    def show_position(self):
        if not self.sorted_times:
            self.set_progress(0, 0)
            return
        index = min(len(self.sorted_times)-1, max(0, self.position_index))
        duration = max(1, self.sorted_times[-1] - self.sorted_times[0])
        elapsed = (self.sorted_times[index] - self.sorted_times[0]) / 1000 * self.effective_factor()
        percent = 100 if self.playback_complete else int((self.sorted_times[index] - self.sorted_times[0]) / duration * 100)
        self.set_progress(percent, elapsed)
        self.overlay.follow_playback(self.sorted_times[index])

    def set_status(self, text):
        self.status.setText(text)
        self.overlay.status_label.setText(text)
        self.publish_mobile_state()

    def speed(self):
        return max(0.25, self.speed_slider.value() / 100)

    def set_speed(self, value):
        self.speed_slider.setValue(int(value * 100))

    def on_speed_changed(self):
        self._playback_factor = self.effective_factor()
        effective_bpm = self.bpm * self.speed() if self.bpm else 0
        bpm_text = f" · {effective_bpm:g} BPM" if effective_bpm else ""
        self.speed_label.setText(f"{self.speed():.2f}x{bpm_text}")
        self.update_total_time()
        self.publish_mobile_state()

    def update_effect_labels(self):
        self._playback_delay = self.delay_slider.value()
        self._playback_wrong = self.wrong_slider.value()
        self.delay_label.setText(f"{self.delay_slider.value()}%")
        self.wrong_label.setText(f"{self.wrong_slider.value()}%")
        self.publish_mobile_state()

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
            idx = min(len(self.sorted_times)-1, max(0, self.position_index))
            elapsed = (self.sorted_times[idx] - self.sorted_times[0]) / 1000 * self.effective_factor()
        self.time_label.setText(
            f"{self.format_seconds(elapsed)} / {self.format_seconds(self.total_seconds())}"
        )
        if hasattr(self, "duration_summary_label"):
            self.duration_summary_label.setText(self.format_seconds(self.total_seconds()))
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
        self.publish_mobile_state()

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
        section = self.section_combo.currentData()
        if section:
            start = bisect_left(self.sorted_times, section.start_ms)
            end = bisect_left(self.sorted_times, section.end_ms)
            idx = max(start, min(end - 1, idx))
        self.position_index = self.resume_index = idx
        self.playback_complete = False
        elapsed = (self.sorted_times[idx] - self.sorted_times[0]) / 1000 * self.effective_factor()
        is_active = self.playback_session is not None
        if is_active:
            self.playback_session.seek(idx)
            self.release_all_keys()
            self.set_status(f"已跳转到 {self.format_seconds(elapsed)}")
        else:
            self.set_status(f"已定位到 {self.format_seconds(elapsed)}")
        self.show_position()
        self.overlay.stop_score_scroll()
        self.overlay.follow_playback(self.sorted_times[idx])
        self.remember_position()
        self.save_config()

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
        wrong = getattr(self, "_playback_wrong", 0)
        if wrong <= 0 or random.random() * 100 >= wrong:
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

    def start_play(self, immediate=False):
        if self.player_thread and self.player_thread.is_alive():
            return
        if self.previewing:
            self.stop_preview()
        if not self.load_music() or not self.confirm_admin_tip_before_play():
            return
        self.start_playback(preview=False, immediate=immediate)

    def start_playback(self, preview=False, immediate=False):
        if self.player_thread and self.player_thread.is_alive():
            return
        self.midi_game.stop()
        self.overlay.stop_score_scroll()
        self.midi_practice.stop_practice()
        section = self.section_combo.currentData()
        start = bisect_left(self.sorted_times, section.start_ms) if section else 0
        end = bisect_left(self.sorted_times, section.end_ms) if section else len(self.sorted_times)
        resume = self.resume_index if start <= self.resume_index < end else start
        if self.playback_complete:
            resume = start
            self.playback_complete = False
        self.stop_event.clear()
        self.playback_generation += 1
        generation = self.playback_generation
        self.previewing = preview
        self.start_btn.setEnabled(False)
        self.immediate_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.preview_btn.setText("停止预览" if preview else "预览")
        self.overlay.preview_btn.setText("停止" if preview else "预览")
        notes = {time: list(keys) for time, keys in self.notes_by_time.items()}
        session = PlaybackSession(tuple(self.sorted_times), notes, start, end,
                                  section.end_ms if section else score_end_ms(note_groups(self.current_score_notes)),
                                  preview=preview, countdown=0 if immediate else 3,
                                  loop=self.section_loop.isChecked(), compensate=bool(self.meta.get("playerPressCompensationMs")))
        self.playback_session = session

        def emit(kind, payload):
            self.signals.playback_event.emit(generation, kind, payload)

        def worker():
            session.run(emit, self.press_notes, self.release_notes, self.apply_wrong_key,
                        lambda: self._playback_factor, lambda: self._playback_delay,
                        resume_index=resume)

        self.player_thread = threading.Thread(target=worker, daemon=True)
        self.player_thread.start()

    def stop_play(self):
        self.midi_practice.stop_practice()
        if self.midi_game:
            self.midi_game.stop()
        self.overlay.stop_score_scroll()
        self.playback_generation += 1
        self.stop_event.set()
        if self.playback_session:
            self.playback_session.stop.set()
        if self.player_thread and self.player_thread.is_alive():
            self.player_thread.join(timeout=0.2)
        self.remember_session_position()
        self.previewing = False
        self.playback_session = None
        self.silence_preview_audio()
        try:
            import winsound
            winsound.PlaySound(None, 0)
        except Exception:
            pass
        self.release_all_keys()
        self.start_btn.setEnabled(True)
        self.immediate_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.preview_btn.setText("预览")
        self.overlay.preview_btn.setText("预览")
        self.overlay.keys_label.setText("当前按键：—")
        self._mobile_note_keys = []
        self.show_position()
        self.remember_position()
        self.save_config()
        self.set_status("已停止")

    def force_stop(self):
        self.stop_play()
        self.realtime_recognition.stop_listening()
        self.remember_position()
        self.save_config()
        self.show_position()
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
        self.start_playback(preview=True)

    def stop_preview(self):
        self.stop_play()
        self.set_status("预览已停止")

    def remember_session_position(self):
        session = self.playback_session
        if not session:
            return
        with session.lock:
            if session.seek_index is not None:
                self.position_index = self.resume_index = session.seek_index
            elif session.position_index is not None:
                self.position_index = session.position_index
            if session.seek_index is None and session.next_index is not None:
                self.resume_index = session.next_index

    def on_playback_event(self, generation, kind, payload):
        if generation != self.playback_generation:
            return
        if self.stop_event.is_set() or self.playback_session is None:
            return
        if kind == "status":
            self.set_status(str(payload))
        elif kind == "notes":
            self.preview_note(payload)
        elif kind == "progress":
            index, keys = payload["index"], payload["keys"]
            self.position_index, self.resume_index = index, index + 1
            self.playback_complete = False
            self.on_worker_progress(
                int((self.sorted_times[index] - self.sorted_times[0]) / max(1, self.sorted_times[-1] - self.sorted_times[0]) * 100),
                self.format_seconds((self.sorted_times[index] - self.sorted_times[0]) / 1000 * self.effective_factor()),
                f"{index + 1}/{len(self.sorted_times)}", keys,
            )
        elif kind == "error":
            self.set_status(f"播放失败：{payload}")
        elif kind == "finished":
            stopped = bool(payload)
            session = self.playback_session
            if session:
                with session.lock:
                    last = session.end_index - 1
                    if not stopped:
                        self.position_index = last
                        self.resume_index = session.start_index
                        self.playback_complete = self.section_combo.currentData() is None
            self.on_worker_finished(stopped)
            self.remember_position()
            self.save_config()

    def on_worker_progress(self, percent, elapsed, count_text, note_keys):
        if self.stop_event.is_set():
            return
        self.set_progress(percent)
        index = int(count_text.split("/", 1)[0]) - 1
        if 0 <= index < len(self.sorted_times):
            self.position_index, self.resume_index = index, index + 1
            self.remember_position()
            self.overlay.follow_playback(self.sorted_times[index])
        text = f"{elapsed} / {self.format_seconds(self.total_seconds())}"
        self.time_label.setText(text)
        self.overlay.time_label.setText(text)
        mapped = [NOTE_TO_KEY.get(k, "") for k in note_keys]
        self._mobile_note_keys = list(note_keys)
        self.overlay.keys_label.setText("当前按键：" + ("  ".join(k for k in mapped if k) or "—"))
        self.set_status(("预览进度: " if self.previewing else "演奏进度: ") + count_text)

    def on_worker_finished(self, stopped):
        self.release_all_keys()
        self.previewing = False
        self.start_btn.setEnabled(True)
        self.immediate_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.preview_btn.setText("预览")
        self.overlay.preview_btn.setText("预览")
        self.overlay.keys_label.setText("当前按键：—")
        self._mobile_note_keys = []
        self.publish_mobile_state()
        if not stopped:
            section = self.section_combo.currentData()
            self.set_status(f"{section.name} 播放完成" if section else "完成")
            self.show_position()
        self.playback_session = None

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
        keys = set(self.pressed_keys)
        if not self.midi_game or not self.midi_game.router.enabled:
            keys.update(KEY_TO_SCANCODE)
        for key in keys:
            send_scan_key(key, True)
        self.pressed_keys.clear()

    def preview_note_frequency(self, note_key):
        try:
            idx = int(note_key.split("Key", 1)[1])
            return 261.63 * (2 ** ((SKY_MAJOR[idx] + pitch_semitones(self.sky_pitch)) / 12))
        except Exception:
            return None

    def preview_wave_path(self, note_keys):
        keys = tuple(sorted(set(k for k in note_keys if self.preview_note_frequency(k))))
        if not keys:
            return None
        cache_key = (self.sky_pitch, keys)
        if cache_key in self.preview_cache and self.preview_cache[cache_key].exists():
            return self.preview_cache[cache_key]
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
        path = self.preview_dir / ("preview_" + self.sky_pitch + "_" + "_".join(k.replace("Key", "k") for k in keys) + ".wav")
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(b"".join(frames))
        self.preview_cache[cache_key] = path
        return path

    def preview_note(self, note_keys):
        if QThread.currentThread() != self.thread():
            self.signals.preview_requested.emit(list(note_keys))
            return
        self._play_preview_note(note_keys)

    def stop_sky_sound_players(self):
        for voices in self.sky_sound_players.values():
            for player, output in voices:
                player.stop()
                player.deleteLater()
                output.deleteLater()
        self.sky_sound_players.clear()
        for voices in self.sky_sound_effects.values():
            for effect in voices:
                effect.stop()
                effect.deleteLater()
        self.sky_sound_effects.clear()
        self.stop_synth_preview_audio()

    @staticmethod
    def stop_synth_preview_audio():
        try:
            import winsound
            winsound.PlaySound(None, 0)
        except Exception:
            pass

    def silence_preview_audio(self):
        for voices in self.sky_sound_players.values():
            for player, _output in voices:
                player.stop()
        for voices in self.sky_sound_effects.values():
            for effect in voices:
                effect.stop()
        self.stop_synth_preview_audio()

    def play_sky_wave(self, index, path):
        try:
            source = pitched_wave(path, self.sky_pitch, self.preview_dir)
        except (OSError, ValueError, wave.Error):
            self.play_sky_media(index, path)
            return
        voices = self.sky_sound_effects.setdefault(index, [])
        effect = next((voice for voice in voices if not voice.isPlaying()), None)
        if effect is None and len(voices) < 4:
            effect = QSoundEffect(self)
            effect.setVolume(0.45)
            effect.setSource(QUrl.fromLocalFile(str(source)))
            voices.append(effect)
        elif effect is None:
            effect = voices.pop(0)
            effect.stop()
            voices.append(effect)
        effect.play()

    def play_sky_media(self, index, path):
        voices = self.sky_sound_players.setdefault(index, [])
        pair = next((voice for voice in voices if voice[0].playbackState() != QMediaPlayer.PlayingState), None)
        if pair is None and len(voices) < 4:
            output = QAudioOutput(self)
            output.setVolume(0.34)
            player = QMediaPlayer(self)
            player.setAudioOutput(output)
            player.setSource(QUrl.fromLocalFile(str(path)))
            player.setPlaybackRate(2 ** (pitch_semitones(self.sky_pitch) / 12))
            if hasattr(player, "setPitchCompensation"):
                player.setPitchCompensation(False)
            pair = (player, output)
            voices.append(pair)
        elif pair is None:
            pair = voices.pop(0)
            pair[0].stop()
            voices.append(pair)
        pair[0].setPosition(0)
        pair[0].play()

    def _play_preview_note(self, note_keys):
        if not note_keys:
            self.silence_preview_audio()
            return
        if self.sky_sound_files:
            missing = []
            for note_key in set(note_keys):
                try:
                    index = int(note_key.split("Key", 1)[1])
                    path = self.sky_sound_files[index]
                except KeyError:
                    missing.append(note_key)
                    continue
                except (IndexError, TypeError, ValueError):
                    continue
                if path.suffix.lower() == ".wav":
                    self.play_sky_wave(index, path)
                else:
                    self.play_sky_media(index, path)
            if missing:
                self.play_synth_preview(missing)
            return
        self.play_synth_preview(note_keys)

    def play_synth_preview(self, note_keys):
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
            self.overlay_btn.setText("悬浮琴谱")
            visible = False
        else:
            self.overlay.refresh()
            self.overlay.show()
            self.overlay.raise_()
            self.overlay_btn.setText("隐藏悬浮窗")
            visible = True
        if hasattr(self, "overlay_switch"):
            self.overlay_switch.blockSignals(True)
            self.overlay_switch.setChecked(visible)
            self.overlay_switch.blockSignals(False)

    def toggle_main_window(self):
        self.setVisible(not self.isVisible())

    @staticmethod
    def dropped_sheet_paths(event):
        if not event.mimeData().hasUrls():
            return []
        paths = []
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.is_file() and path.suffix.lower() in (".json", ".zip"):
                paths.append(path)
        return paths

    def dragEnterEvent(self, event):
        if self.dropped_sheet_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = self.dropped_sheet_paths(event)
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self.show_transcribe()
        self.transcribe_tabs.setCurrentIndex(1)
        self.start_manual_import(paths)

    def resizeEvent(self, event):
        """Keep compact 720p spacing while restoring air on taller screens."""
        if hasattr(self, "center_layout"):
            tall_extra = max(0, min(50, int((self.height() - 720) * 0.17)))
            library_extra = max(0, min(24, int((self.height() - 720) * 0.08)))
            self.center_layout.setContentsMargins(16, 18 + tall_extra, 16, 13)
            self.inspector_layout.setContentsMargins(14, 18 + tall_extra, 14, 13)
            self.library_layout.setContentsMargins(12, 17 + library_extra, 12, 12)
        super().resizeEvent(event)

    def resize_hit_test(self, global_position, border=7):
        """Return a Windows resize hit code from a Qt logical screen point."""
        local = self.mapFromGlobal(global_position)
        if not self.rect().contains(local):
            return None
        left = local.x() < border
        right = local.x() >= self.width() - border
        top = local.y() < border
        bottom = local.y() >= self.height() - border
        hit = {
            (True, False, True, False): 13,   # HTTOPLEFT
            (False, True, True, False): 14,   # HTTOPRIGHT
            (True, False, False, True): 16,   # HTBOTTOMLEFT
            (False, True, False, True): 17,   # HTBOTTOMRIGHT
        }.get((left, right, top, bottom))
        if hit is not None:
            return hit
        return 10 if left else 11 if right else 12 if top else 15 if bottom else None

    def nativeEvent(self, event_type, message):
        """Restore edge resizing for the frameless Windows window."""
        if os.name == "nt" and not self.isMaximized():
            try:
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == 0x0084:  # WM_NCHITTEST
                    # WM_NCHITTEST supplies physical screen pixels, while
                    # mapFromGlobal expects Qt logical coordinates. Using the
                    # native point directly makes most of the lower/right side
                    # look like a resize border at 125–200% Windows scaling.
                    cursor = QCursor.pos()
                    hit = self.resize_hit_test(cursor)
                    if hit is not None:
                        return True, hit
            except Exception:
                pass
        return super().nativeEvent(event_type, message)

    def start_sheet_update(self):
        if self.update_running:
            return
        self.update_running = True
        self.update_task_kind = "official"
        self.set_update_controls_enabled(False)
        self.set_task_progress_range(0, 0)
        self.set_task_status("正在获取曲库版本信息…")
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

    def set_task_status(self, message):
        self.update_status_label.setText(message)
        self.import_status.setText(message)

    def set_task_progress_range(self, minimum, maximum):
        self.update_progress.setRange(minimum, maximum)
        self.import_progress.setRange(minimum, maximum)

    def set_update_controls_enabled(self, enabled):
        for control_name in (
            "update_button",
            "update_mirror_combo",
            "source_url_input",
            "source_download_button",
            "import_files_button",
            "import_midi_files_button",
        ):
            control = getattr(self, control_name, None)
            if control is not None:
                control.setEnabled(enabled)

    def start_external_source_import(self):
        if self.update_running:
            return
        url = self.source_url_input.text().strip()
        if not url:
            QMessageBox.information(self, "其他曲库来源", "请先粘贴 ZIP 或 JSON 文件的直链。")
            self.source_url_input.setFocus()
            return
        self.update_running = True
        self.update_task_kind = "external"
        self.set_update_controls_enabled(False)
        self.set_task_progress_range(0, 0)
        self.set_task_status("正在从其他来源下载…")

        def progress(percent, downloaded, total):
            if total:
                message = f"正在下载 {downloaded / 1048576:.1f} / {total / 1048576:.1f} MB"
            else:
                message = f"正在下载 {downloaded / 1048576:.1f} MB"
            self.update_signals.progress.emit(percent, message)

        def worker():
            try:
                result = install_external_source(APP_DIR, url, progress)
                self.update_signals.finished.emit(result)
            except Exception as exc:
                self.update_signals.failed.emit(str(exc))

        self.update_thread = threading.Thread(target=worker, daemon=True)
        self.update_thread.start()

    def choose_sheet_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "选择曲谱或曲库压缩包",
            "",
            "曲谱文件 (*.json *.zip);;JSON 乐谱 (*.json);;ZIP 压缩包 (*.zip)",
        )
        if paths:
            self.start_manual_import([Path(path) for path in paths])

    def start_manual_import(self, paths):
        if self.update_running:
            QMessageBox.information(self, "正在处理", "请等待当前曲库任务完成后再导入。")
            return
        self.update_running = True
        self.update_task_kind = "manual"
        self.set_update_controls_enabled(False)
        self.set_task_progress_range(0, 0)
        self.set_task_status(f"正在导入 {len(paths)} 个文件…")

        def worker():
            try:
                result = import_sheet_files(APP_DIR, paths)
                self.update_signals.finished.emit(result)
            except Exception as exc:
                self.update_signals.failed.emit(str(exc))

        self.update_thread = threading.Thread(target=worker, daemon=True)
        self.update_thread.start()

    def on_update_progress(self, percent, message):
        if percent > 0:
            self.set_task_progress_range(0, 100)
            self.update_progress.setValue(percent)
        self.set_task_status(message)

    def on_update_finished(self, result):
        self.update_running = False
        self.set_update_controls_enabled(True)
        self.set_task_progress_range(0, 100)
        self.update_progress.setValue(100)
        version = result.get("version", "未知")
        count = int(result.get("count", 0))
        mode = result.get("mode")
        changed = int(result.get("changed", 0))
        renamed = int(result.get("renamed", 0))
        skipped = int(result.get("skipped", 0))
        if mode in ("manual", "external"):
            if changed:
                message = f"导入完成：新增 {changed} 首"
                if renamed:
                    message += f"，重名自动改名 {renamed} 首"
                if skipped:
                    message += f"，跳过 {skipped} 个无效或重复文件"
                self.set_task_status(f"{message}；曲库共 {count} 首。")
                self.refresh_files()
            else:
                self.set_task_status(f"没有导入新曲谱；已跳过 {skipped} 个无效或重复文件。")
            return
        self.update_version_label.setText(f"当前：{version}")
        if result.get("updated"):
            changed = int(result.get("changed", count))
            removed = int(result.get("removed", 0))
            if result.get("mode") == "incremental":
                self.set_task_status(
                    f"增量更新完成：新增或修改 {changed} 首，移除 {removed} 首；曲库共 {count} 首。"
                )
            else:
                detail = f"曲库安装完成：共 {count} 首曲谱"
                if renamed:
                    detail += f"；重名自动改名 {renamed} 首"
                if skipped:
                    detail += f"；跳过无效文件 {skipped} 个"
                self.set_task_status(detail + "。")
            self.refresh_files()
        else:
            self.set_task_status(f"已经是最新曲库，共 {count} 首曲谱。")

    def on_update_failed(self, message):
        self.update_running = False
        self.set_update_controls_enabled(True)
        self.set_task_progress_range(0, 100)
        self.update_progress.setValue(0)
        self.set_task_status(f"处理失败：{message}")
        if getattr(self, "update_task_kind", "official") == "external":
            suggestion = "请检查下载地址是否为可直接访问的 ZIP 或 JSON 文件。"
        elif getattr(self, "update_task_kind", "official") == "manual":
            suggestion = "请确认文件为有效的 SkyAutoMusic JSON 乐谱或 ZIP 曲库。"
        else:
            suggestion = "请切换下载线路后重试。"
        QMessageBox.warning(self, "曲库处理失败", f"{message}\n\n{suggestion}")

    def check_app_update(self):
        """Check the public GitHub Release without blocking the UI."""
        if getattr(self, "app_update_checking", False):
            return
        self.app_update_checking = True
        self.app_update_check_button.setEnabled(False)
        self.app_update_download_button.hide()
        self.app_update_status.setText("正在检查软件更新…")

        def worker():
            try:
                self.app_update_signals.finished.emit(fetch_latest_release())
            except Exception as exc:
                self.app_update_signals.failed.emit(str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def on_app_update_finished(self, result):
        self.app_update_checking = False
        self.app_update_check_button.setEnabled(True)
        latest = result["version"]
        if compare_versions(latest, APP_VERSION) > 0:
            self.app_update_status.setText(f"发现新版本：v{latest}")
            self.app_update_url = result["url"]
            self.app_update_download_button.show()
            self.set_status(f"发现软件新版本 v{latest}，可在“关于”页下载。")
        else:
            self.app_update_status.setText(f"当前已是最新版本 v{APP_VERSION}")

    def open_app_update(self):
        url = getattr(self, "app_update_url", None)
        if url and not QDesktopServices.openUrl(QUrl(url)):
            self.app_update_status.setText("无法打开浏览器，请访问 sky.xxlab.dev 下载。")

    def on_app_update_failed(self, message):
        self.app_update_checking = False
        self.app_update_check_button.setEnabled(True)
        self.app_update_status.setText(message)

    def save_config(self):
        geo = self.geometry()
        data = dict(self.config)
        data.update(self.midi_practice.settings())
        data.update(self.midi_game.settings())
        data.update({
            "width": geo.width(),
            "height": geo.height(),
            "x": geo.x(),
            "y": geo.y(),
            "speed": self.speed(),
            "speed_logic_version": self.speed_logic_version,
            "random_delay_percent": self.delay_slider.value(),
            "wrong_key_percent": self.wrong_slider.value(),
            "speed_presets": self.speed_presets,
            "playback_positions": self.playback_positions,
            "last_selected_file": self.selected_file,
            "song_sections": self.saved_song_sections,
            "overlay_music_mode": self.overlay_mode,
            "admin_mode": self.admin_mode,
            "admin_tip_count": self.admin_tip_count,
            "sky_sound_id": self.sky_sound_id,
            "score_input_mode": self.score_editor.input_mode.currentData(),
            "sky_location": self.sky_location,
            "sky_location_pitches": self.sky_location_pitches,
            "mobile_remote_token": self.mobile_remote.token if hasattr(self, "mobile_remote") else self.config.get("mobile_remote_token"),
            "update_mirror": self.update_mirror_combo.currentData() if hasattr(self, "update_mirror_combo") else GITHUB_MIRRORS[0][1],
        })
        if self.theme_confirmed:
            data["theme"] = self.theme_name
        if self.overlay:
            og = self.overlay.geometry()
            data["progress_geometry"] = f"{og.width()}x{og.height()}+{og.x()}+{og.y()}"
            data["overlay_show_score"] = self.overlay.score_button.isChecked()
            data["overlay_opacity"] = self.overlay.opacity.currentData()
        write_json(CONFIG_FILE, data)
        self.config = data

    def closeEvent(self, event: QCloseEvent):
        self.realtime_recognition.shutdown()
        self.stop_play()
        self.stop_sky_sound_players()
        self.score_editor.shutdown()
        if hasattr(self, "mobile_remote"):
            self.mobile_remote.stop()
        self.midi_practice.shutdown()
        self.midi_game.shutdown()
        if self.global_hotkey_handles:
            try:
                import keyboard
                for handle in self.global_hotkey_handles:
                    keyboard.remove_hotkey(handle)
                self.global_hotkey_handles.clear()
            except Exception:
                pass
        self.save_config()
        event.accept()
        self.overlay.close()


def configure_high_dpi():
    """Use per-monitor DPI instead of Windows compatibility bitmap scaling."""
    if os.name == "nt":
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                pass
    try:
        QApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass


def fit_window_to_screen(window, screen):
    """Clamp saved geometry to the logical work area at 100–200% scaling."""
    available = screen.availableGeometry()
    max_width = max(window.minimumWidth(), int(available.width() * 0.94))
    max_height = max(window.minimumHeight(), int(available.height() * 0.92))
    window.resize(min(window.width(), max_width), min(window.height(), max_height))

    saved_x = window.config.get("x")
    saved_y = window.config.get("y")
    if isinstance(saved_x, int) and isinstance(saved_y, int):
        right_limit = available.right() - window.width() + 1
        bottom_limit = available.bottom() - window.height() + 1
        window.move(
            max(available.left(), min(saved_x, right_limit)),
            max(available.top(), min(saved_y, bottom_limit)),
        )
    else:
        window.move(
            available.left() + max(0, (available.width() - window.width()) // 2),
            available.top() + max(0, (available.height() - window.height()) // 2),
        )


def main():
    configure_high_dpi()
    startup_config = load_json(CONFIG_FILE, {})
    if startup_config.get("admin_mode") and os.name == "nt" and not is_admin():
        if request_admin_restart():
            return
    app = QApplication(sys.argv)
    window = MainWindow()
    fit_window_to_screen(window, window.screen() or app.primaryScreen())
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
