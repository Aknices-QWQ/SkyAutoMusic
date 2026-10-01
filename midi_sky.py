"""MIDI-to-Sky keyboard routing with a foreground-window gate."""

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from midi_keyboard import MAJOR_STEPS, WindowsMidi, note_name

SKY_PHYSICAL_KEYS = ("Y", "U", "I", "O", "P", "H", "J", "K", "L", ";", "N", "M", ",", ".", "/")


class SkyWindows:
    """Identify the real game process, preserving both window and process IDs."""

    def __init__(self):
        self.user32 = self.kernel32 = None
        if os.name == "nt":
            self.user32 = ctypes.WinDLL("user32", use_last_error=True)
            self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            for name, args, result in (
                ("GetForegroundWindow", [], wintypes.HWND),
                ("GetAncestor", [wintypes.HWND, wintypes.UINT], wintypes.HWND),
                ("IsWindow", [wintypes.HWND], wintypes.BOOL),
                ("IsWindowVisible", [wintypes.HWND], wintypes.BOOL),
                ("GetWindowTextW", [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int),
                ("GetWindowThreadProcessId", [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            ):
                fn = getattr(self.user32, name)
                fn.argtypes, fn.restype = args, result
            self.kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            self.kernel32.OpenProcess.restype = wintypes.HANDLE
            self.kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
            self.kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
            self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            self.kernel32.CloseHandle.restype = wintypes.BOOL

    def pid(self, hwnd):
        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def devices(self):
        if self.user32 is None:
            return []
        result = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def visit(hwnd, _):
            if not self.user32.IsWindowVisible(hwnd):
                return True
            pid = self.pid(hwnd)
            handle = self.kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return True
            try:
                path, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
                if not self.kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(size)):
                    return True
                if Path(path.value).name.casefold() not in {"sky.exe", "skychildrenofthelight.exe"}:
                    return True
                title = ctypes.create_unicode_buffer(512)
                self.user32.GetWindowTextW(hwnd, title, len(title))
                result.append(((int(hwnd), pid), f"{title.value or '光遇'} · PID {pid}"))
            finally:
                self.kernel32.CloseHandle(handle)
            return True

        self.user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
        self.user32.EnumWindows.restype = wintypes.BOOL
        self.user32.EnumWindows(callback_type(visit), 0)
        return result

    def exists(self, target):
        return bool(self.user32 and target and self.user32.IsWindow(target[0]) and self.pid(target[0]) == target[1])

    def focused(self, target):
        if not self.exists(target):
            return False
        foreground = self.user32.GetForegroundWindow()
        return bool(foreground and self.user32.GetAncestor(foreground, 2) == target[0])


class MidiSkyRouter:
    """One physical down per Sky key; release only when all channels let go."""

    def __init__(self, send_key, can_send, base=60):
        self.send_key, self.can_send, self.base = send_key, can_send, base
        self.held = {}
        self.blocked = set()
        self.enabled = False

    @property
    def indices(self):
        return set(self.held.values())

    def release_all(self):
        self.blocked.update(self.held)
        for index in self.indices:
            self.send_key(SKY_PHYSICAL_KEYS[index], True)
        self.held.clear()

    def stop(self):
        self.enabled = False
        self.release_all()

    def check_focus(self):
        if not self.enabled or not self.can_send():
            self.release_all()
            return False
        return True

    def feed(self, status, note, velocity):
        allowed = self.check_focus()
        kind, channel = status & 0xf0, status & 0x0f
        identity = (channel, note)
        if kind == 0xb0 and note in (120, 123):
            for ch, pitch in list(self.held):
                if ch == channel:
                    self.feed(0x80 | ch, pitch, 0)
            self.blocked = {key for key in self.blocked if key[0] != channel}
            return False
        if kind == 0x80 or (kind == 0x90 and velocity == 0):
            self.blocked.discard(identity)
            index = self.held.pop(identity, None)
            if index is not None and index not in self.indices:
                self.send_key(SKY_PHYSICAL_KEYS[index], True)
            return False
        if kind != 0x90 or identity in self.held or identity in self.blocked:
            return False
        if not allowed:
            self.blocked.add(identity)
            return False
        offset = note - self.base
        if offset not in MAJOR_STEPS:
            return False
        index = MAJOR_STEPS.index(offset)
        if index not in self.indices and not self.send_key(SKY_PHYSICAL_KEYS[index], False):
            self.stop()
            raise RuntimeError("光遇按键发送失败；请检查游戏与程序的管理员权限。")
        self.held[identity] = index
        return True


class MidiSkyPanel(QFrame):
    connecting = Signal()
    starting = Signal()
    keys_changed = Signal(list)

    def __init__(self, send_key, config, parent=None, backend=None, windows=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.backend = backend if backend is not None else WindowsMidi()
        self.windows = windows if windows is not None else SkyWindows()
        self.connected_device = self.target = None
        self.snapshot = None
        self.router = MidiSkyRouter(send_key, lambda: self.windows.focused(self.target), config.get("sky_midi_base", 60))
        self._last_state = None
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        title = QLabel("MIDI 接入光遇")
        title.setObjectName("CardTitle")
        root.addWidget(title)
        copy = QLabel("连接键盘并开始接入后，切回光遇打开乐器即可弹奏。F7 开始接入，Esc / F8 停止。")
        copy.setObjectName("MutedText")
        copy.setWordWrap(True)
        root.addWidget(copy)
        device_row = QHBoxLayout()
        self.devices = QComboBox()
        self.devices.setAccessibleName("光遇 MIDI 输入设备")
        self.devices.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.refresh_button = QPushButton("刷新设备")
        self.connect_button = QPushButton("连接 MIDI")
        self.octave = QComboBox()
        self.octave.setAccessibleName("光遇 MIDI 音域")
        for base in (36, 48, 60, 72, 84):
            self.octave.addItem(f"{note_name(base)}–{note_name(base + 24)}", base)
        self.octave.setCurrentIndex(max(0, self.octave.findData(self.router.base)))
        self.router.base = self.octave.currentData()
        for widget, stretch in ((self.devices, 1), (self.refresh_button, 0), (self.connect_button, 0), (self.octave, 0)):
            device_row.addWidget(widget, stretch)
        root.addLayout(device_row)
        target_row = QHBoxLayout()
        target_row.addWidget(QLabel("光遇窗口"))
        self.targets = QComboBox()
        self.targets.setAccessibleName("MIDI 目标光遇窗口")
        self.targets.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.target_refresh = QPushButton("刷新游戏窗口")
        target_row.addWidget(self.targets, 1)
        target_row.addWidget(self.target_refresh)
        root.addLayout(target_row)
        actions = QHBoxLayout()
        self.start_button = QPushButton("开始接入")
        self.start_button.setToolTip("F7 开始接入；只向选中的前台光遇窗口输入")
        self.start_button.setObjectName("PrimaryButton")
        self.stop_button = QPushButton("停止接入")
        self.stop_button.setEnabled(False)
        actions.addWidget(self.start_button)
        actions.addWidget(self.stop_button)
        actions.addStretch()
        root.addLayout(actions)
        self.status = QLabel("尚未连接 MIDI")
        self.status.setObjectName("MutedText")
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        hint = QLabel("15 个自然音映射 Y U I O P / H J K L ; / N M , . /；黑键、音域外和延音踏板忽略。游戏音高由当前地点决定。")
        hint.setObjectName("MutedText")
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.refresh_button.clicked.connect(self.refresh_devices)
        self.target_refresh.clicked.connect(self.refresh_targets)
        self.connect_button.clicked.connect(self.toggle_connection)
        self.start_button.clicked.connect(self.start)
        self.stop_button.clicked.connect(self.stop)
        self.octave.currentIndexChanged.connect(self.change_octave)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(10)
        self.poll_timer.timeout.connect(self.poll)
        self.poll_timer.start()
        self.scan_timer = QTimer(self)
        self.scan_timer.setInterval(2000)
        self.scan_timer.timeout.connect(self.refresh_devices)
        self.scan_timer.start()
        self.refresh_devices()
        self.refresh_targets()

    def refresh_targets(self):
        if self.router.enabled:
            return
        current = self.targets.currentData()
        self.targets.clear()
        try:
            targets = self.windows.devices()
        except Exception as exc:
            self.status.setText(f"读取光遇窗口失败：{exc}")
            return
        for target, title in targets:
            self.targets.addItem(title, target)
        self.targets.setCurrentIndex(max(0, self.targets.findData(current)))
        if self.targets.count() == 0:
            self.targets.setPlaceholderText("请先打开光遇，再刷新")

    def refresh_devices(self):
        try:
            devices = self.backend.devices()
        except Exception as exc:
            self.disconnect(f"读取 MIDI 设备失败：{exc}")
            return
        if self.connected_device and devices != self.snapshot:
            self.disconnect("MIDI 设备已变化，请重新连接。")
        if devices != self.snapshot:
            selected = self.devices.currentData()
            self.devices.clear()
            for device in devices:
                self.devices.addItem(device[1], device)
            self.devices.setCurrentIndex(max(0, self.devices.findData(selected)))
            self.snapshot = list(devices)
        self.connect_button.setEnabled(bool(devices))

    def toggle_connection(self):
        if self.connected_device:
            self.disconnect()
            return
        device = self.devices.currentData()
        if device is None:
            self.status.setText("未检测到 MIDI 键盘，请接入后刷新。")
            return
        self.connecting.emit()
        try:
            self.backend.connect(device[0])
            self.connected_device = device
            self.router.blocked.clear()
            self.devices.setEnabled(False)
            self.connect_button.setText("断开 MIDI")
            self.status.setText(f"已连接：{device[1]}；选择光遇窗口后开始接入。")
        except Exception as exc:
            self.disconnect(f"连接失败：{exc}；请关闭占用键盘的其他软件。")

    def start(self):
        if self.router.enabled:
            return
        target = self.targets.currentData()
        if not self.connected_device:
            self.status.setText("请先连接 MIDI 键盘。")
            return
        if not self.windows.exists(target):
            self.status.setText("请先打开光遇并刷新游戏窗口。")
            return
        self.starting.emit()
        self.target = target
        # Drain already-held keys while disabled, so enabling needs a fresh strike.
        self.poll()
        self.router.enabled = True
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.targets.setEnabled(False)
        self.target_refresh.setEnabled(False)
        self.octave.setEnabled(False)
        self._last_state = None
        self.status.setText("接入已开启，请切回光遇打开乐器。")

    def stop(self):
        was_enabled = self.router.enabled
        self.router.stop()
        self.keys_changed.emit([])
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.targets.setEnabled(True)
        self.target_refresh.setEnabled(True)
        self.octave.setEnabled(True)
        self._last_state = None
        if was_enabled:
            self.status.setText("接入已停止，MIDI 连接保留。")

    def disconnect(self, message="MIDI 已断开"):
        self.stop()
        self.backend.close()
        self.connected_device = None
        self.router.blocked.clear()
        self.devices.setEnabled(True)
        self.connect_button.setText("连接 MIDI")
        self.status.setText(message)

    def change_octave(self):
        self.stop()
        self.router.base = self.octave.currentData()

    def poll(self):
        if not self.connected_device:
            return
        try:
            if self.router.enabled and not self.windows.exists(self.target):
                self.stop()
                self.status.setText("光遇窗口已关闭；请刷新后重新开始接入。")
            previous = self.router.indices
            self.router.check_focus()
            if previous != self.router.indices:
                self.keys_changed.emit([f"1Key{i}" for i in sorted(self.router.indices)])
            for status, note, velocity in self.backend.drain():
                previous = self.router.indices
                self.router.feed(status, note, velocity)
                if previous != self.router.indices:
                    self.keys_changed.emit([f"1Key{i}" for i in sorted(self.router.indices)])
            state = (self.router.enabled, self.windows.focused(self.target), tuple(sorted(self.router.indices)))
            if state != self._last_state:
                self._last_state = state
                self.keys_changed.emit([f"1Key{i}" for i in state[2]])
                if state[0]:
                    self.status.setText("接入中 · " + ("  ".join(SKY_PHYSICAL_KEYS[i] for i in state[2]) or "等待弹奏")
                                        if state[1] else "接入中 · 等待切回光遇；请松开琴键再弹。")
        except Exception as exc:
            self.disconnect(f"MIDI 接入中断：{exc}")

    def settings(self):
        return {"sky_midi_base": self.octave.currentData()}

    def shutdown(self):
        self.poll_timer.stop()
        self.scan_timer.stop()
        self.disconnect()
