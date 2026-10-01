"""Windows MIDI I/O and score-following logic, independent of Qt."""

import ctypes
import os
import queue
import re
from ctypes import wintypes

MAJOR_STEPS = (0, 2, 4, 5, 7, 9, 11, 12, 14, 16, 17, 19, 21, 23, 24)


def note_name(note):
    return f"{('C', 'C♯', 'D', 'D♯', 'E', 'F', 'F♯', 'G', 'G♯', 'A', 'A♯', 'B')[note % 12]}{note // 12 - 1}"


def score_groups(notes_by_time, base=60):
    groups = []
    for timestamp in sorted(notes_by_time):
        notes = set()
        for key in notes_by_time[timestamp]:
            match = re.fullmatch(r"[12]Key(\d+)", str(key))
            if match and 0 <= int(match[1]) < len(MAJOR_STEPS):
                notes.add(base + MAJOR_STEPS[int(match[1])])
        if notes:
            groups.append(frozenset(notes))
    return groups


class PracticeSession:
    """Require the exact chord and a fresh strike for every target note."""

    def __init__(self, groups, held=()):
        self.groups = groups
        self.index = 0
        self.mistakes = 0
        self.held = set(held)  # (channel, note), preserving cross-channel releases
        self.fresh = set()

    @property
    def target(self):
        return self.groups[self.index] if self.index < len(self.groups) else frozenset()

    def feed(self, status, note, velocity):
        kind, channel = status & 0xF0, status & 0x0F
        key = (channel, note)
        if kind == 0x80 or (kind == 0x90 and velocity == 0):
            self.held.discard(key)
            if not any(n == note for _, n in self.held):
                self.fresh.discard(note)
            return self.check()
        if kind != 0x90 or key in self.held:
            return False
        self.held.add(key)
        if not self.target:
            return False
        self.fresh.add(note)
        if note not in self.target:
            self.mistakes += 1
        return self.check()

    def check(self):
        if self.target and {n for _, n in self.held} == self.target and self.target <= self.fresh:
            self.index += 1
            self.fresh.clear()
            return True
        return False


class _InputCaps(ctypes.Structure):
    _fields_ = [("mid", wintypes.WORD), ("pid", wintypes.WORD),
                ("version", wintypes.DWORD), ("name", ctypes.c_wchar * 32),
                ("support", wintypes.DWORD)]


class WindowsMidi:
    """WinMM callback only enqueues messages; the GUI drains them on its thread."""

    def __init__(self):
        self.events = queue.SimpleQueue()
        self.input_handle = None
        self.output_handle = None
        self._callback = None
        self._generation = 0
        self.api = None
        if os.name == "nt":
            self.api = ctypes.WinDLL("winmm")
            pointer = ctypes.c_size_t
            signatures = {
                "midiInGetNumDevs": ([], wintypes.UINT),
                "midiInGetDevCapsW": ([pointer, ctypes.POINTER(_InputCaps), wintypes.UINT], wintypes.UINT),
                "midiInOpen": ([ctypes.POINTER(ctypes.c_void_p), wintypes.UINT, pointer, pointer, wintypes.DWORD], wintypes.UINT),
                "midiOutOpen": ([ctypes.POINTER(ctypes.c_void_p), wintypes.UINT, pointer, pointer, wintypes.DWORD], wintypes.UINT),
                "midiOutShortMsg": ([ctypes.c_void_p, wintypes.DWORD], wintypes.UINT),
                "midiInGetErrorTextW": ([wintypes.UINT, wintypes.LPWSTR, wintypes.UINT], wintypes.UINT),
            }
            for name in ("midiInStart", "midiInStop", "midiInReset", "midiInClose", "midiOutReset", "midiOutClose"):
                signatures[name] = ([ctypes.c_void_p], wintypes.UINT)
            for name, (args, result) in signatures.items():
                function = getattr(self.api, name)
                function.argtypes, function.restype = args, result

    def _check(self, result):
        if result:
            message = ctypes.create_unicode_buffer(256)
            self.api.midiInGetErrorTextW(result, message, len(message))
            raise RuntimeError(message.value or f"MIDI 错误 {result}")

    def devices(self):
        if self.api is None:
            return []
        result = []
        for index in range(self.api.midiInGetNumDevs()):
            caps = _InputCaps()
            if not self.api.midiInGetDevCapsW(index, ctypes.byref(caps), ctypes.sizeof(caps)):
                result.append((index, caps.name))
        return result

    def connect(self, device_id):
        self.close()
        if self.api is None:
            raise RuntimeError("MIDI 键盘目前仅支持 Windows")
        generation = self._generation
        callback_type = ctypes.WINFUNCTYPE(None, ctypes.c_void_p, wintypes.UINT,
                                          ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t)

        def callback(handle, message, instance, data, timestamp):
            if message == 0x3C3:  # MIM_DATA; ignore SysEx, timing and driver notifications
                self.events.put((generation, data & 0xFF, (data >> 8) & 0x7F, (data >> 16) & 0x7F))

        self._callback = callback_type(callback)
        handle = ctypes.c_void_p()
        self._check(self.api.midiInOpen(ctypes.byref(handle), device_id,
                                      ctypes.cast(self._callback, ctypes.c_void_p).value, 0, 0x30000))
        self.input_handle = handle
        try:
            self._check(self.api.midiInStart(handle))
        except Exception:
            self.close()
            raise

    def drain(self):
        # Bound work per GUI tick, even if a device floods the MIDI port.
        for _ in range(1024):
            try:
                generation, status, note, velocity = self.events.get_nowait()
            except queue.Empty:
                break
            if generation == self._generation and self.input_handle:
                yield status, note, velocity

    def enable_sound(self, enabled):
        if self.output_handle:
            self.api.midiOutReset(self.output_handle)
            self.api.midiOutClose(self.output_handle)
            self.output_handle = None
        if enabled:
            handle = ctypes.c_void_p()
            self._check(self.api.midiOutOpen(ctypes.byref(handle), 0xFFFFFFFF, 0, 0, 0))
            self.output_handle = handle

    def send(self, status, note, velocity):
        if self.output_handle:
            # Keep piano timbre; forward notes and sustain only, not device program changes.
            if status & 0xF0 in (0x80, 0x90) or (status & 0xF0 == 0xB0 and note == 64):
                self._check(self.api.midiOutShortMsg(self.output_handle, status | note << 8 | velocity << 16))

    def silence(self):
        if self.output_handle:
            self.api.midiOutReset(self.output_handle)

    def close(self):
        self._generation += 1
        if self.input_handle:
            self.api.midiInStop(self.input_handle)
            self.api.midiInReset(self.input_handle)
            self.api.midiInClose(self.input_handle)
            self.input_handle = None
        self._callback = None
        self.enable_sound(False)
        while not self.events.empty():
            self.events.get_nowait()
