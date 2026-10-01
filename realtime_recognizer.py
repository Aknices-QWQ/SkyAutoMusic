"""Local melody recognition from Windows WASAPI computer audio.

Audio stays in memory. This matches clear instrumental melodies against local
Sky JSON scores; it is not a recording-fingerprint or general music service.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import queue
import re
import threading
import time
from array import array
from collections import Counter, defaultdict, deque
from pathlib import Path

from PySide6.QtCore import QObject, Signal

try:
    import numpy as np
except ImportError:
    np = None
try:
    import pyaudiowpatch as pyaudio
except (ImportError, OSError):
    pyaudio = None


SKY_MAJOR = (0, 2, 4, 5, 7, 9, 11, 12, 14, 16, 17, 19, 21, 23, 24)
SAMPLE_RATE = 16000
FRAME_SIZE = 2048
HOP_SIZE = 320
MIN_NOTES = 12
GRAM_SIZE = 4
INDEX_VERSION = 2


class RecognitionCancelled(Exception):
    pass


def _cancelled(stop_event):
    if stop_event is not None and stop_event.is_set():
        raise RecognitionCancelled()


def _read_score(path):
    for encoding in ("utf-8-sig", "gbk", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            data = json.loads(path.read_text(encoding=encoding))
            break
        except (UnicodeError, ValueError):
            continue
    else:
        raise ValueError("无法读取曲谱")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        data = data[0]
    if not isinstance(data, dict) or not isinstance(data.get("songNotes"), list):
        raise ValueError("曲谱缺少音符")
    return data


def score_melodies(notes):
    """Chord top notes and, when marked, the separate melody layer.

    Pitch classes make matching independent of the game key and octave. Runs
    of the same note are collapsed because reattacks are unreliable in reverb.
    """
    all_groups, lead_groups = defaultdict(list), defaultdict(list)
    has_accompaniment = False
    for note in notes:
        if not isinstance(note, dict):
            continue
        key = re.fullmatch(r"[12]Key(\d+)", str(note.get("key", "")))
        if not key or not 0 <= int(key[1]) < len(SKY_MAJOR):
            continue
        try:
            when = float(note["time"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(when) or when < 0:
            continue
        pitch = SKY_MAJOR[int(key[1])]
        all_groups[when].append(pitch)
        if note.get("l") in (2, "2"):
            has_accompaniment = True
        else:
            lead_groups[when].append(pitch)
    melodies = []
    for groups in (lead_groups, all_groups) if has_accompaniment else (all_groups,):
        melody = []
        for when in sorted(groups):
            pitch = max(groups[when]) % 12
            if not melody or melody[-1] != pitch:
                melody.append(pitch)
        packed = bytes(melody)
        if len(packed) >= MIN_NOTES and packed not in melodies:
            melodies.append(packed)
    return melodies


def _contour(notes):
    return bytes((b - a) % 12 for a, b in zip(notes, notes[1:]))


class SheetMatcher:
    """Cached score melodies with compact interval-ngram postings."""

    def __init__(self, sheet_dir, cache_file=None):
        self.sheet_dir = Path(sheet_dir)
        self.cache_file = Path(cache_file) if cache_file else None
        self.entries = []
        self.postings = {}
        self.signature = None

    @property
    def song_count(self):
        return len({entry[0] for entry in self.entries})

    def build(self, stop_event=None, progress=None):
        paths = sorted(self.sheet_dir.glob("*.json"))
        digest = hashlib.sha256(str(INDEX_VERSION).encode())
        for path in paths:
            _cancelled(stop_event)
            try:
                stat = path.stat()
                digest.update(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}\n".encode())
            except OSError:
                continue
        signature = digest.hexdigest()
        if signature == self.signature:
            return self.song_count
        entries = None
        if self.cache_file:
            try:
                with gzip.open(self.cache_file, "rt", encoding="utf-8") as handle:
                    cached = json.load(handle)
                _cancelled(stop_event)
                if cached.get("signature") == signature:
                    entries = []
                    for filename, title, pitches in cached["entries"]:
                        melody = bytes.fromhex(pitches)
                        if (Path(filename).name != filename or not isinstance(title, str)
                                or len(melody) < MIN_NOTES or any(p > 11 for p in melody)):
                            raise ValueError("识曲缓存无效")
                        entries.append((filename, title, melody))
            except (OSError, ValueError, KeyError, TypeError, EOFError):
                entries = None
        if entries is None:
            entries = []
            for index, path in enumerate(paths):
                _cancelled(stop_event)
                if progress and index % 256 == 0:
                    progress(f"正在准备旋律曲库：{index:,} / {len(paths):,}")
                try:
                    meta = _read_score(path)
                    title = str(meta.get("songName") or meta.get("name") or path.stem)
                    title = re.sub(r"(?i)^sky\d+\s*[-–—]\s*", "", title).replace("_", " ").strip()
                    for melody in score_melodies(meta["songNotes"]):
                        entries.append((path.name, title or path.stem, melody))
                except (OSError, ValueError):
                    continue
            if self.cache_file:
                temp = self.cache_file.with_suffix(self.cache_file.suffix + ".tmp")
                try:
                    _cancelled(stop_event)
                    with gzip.open(temp, "wt", encoding="utf-8") as handle:
                        json.dump({"signature": signature, "entries": [
                            (filename, title, melody.hex()) for filename, title, melody in entries
                        ]}, handle, ensure_ascii=False)
                    _cancelled(stop_event)
                    os.replace(temp, self.cache_file)
                except OSError:
                    pass  # read-only installs can still recognize without caching
                finally:
                    try:
                        temp.unlink(missing_ok=True)
                    except OSError:
                        pass
        postings = {}
        for entry_id, (_, _, melody) in enumerate(entries):
            _cancelled(stop_event)
            contour = _contour(melody)
            for offset in range(len(contour) - GRAM_SIZE + 1):
                gram = contour[offset:offset + GRAM_SIZE]
                postings.setdefault(gram, array("Q")).append((entry_id << 32) | offset)
        self.entries, self.postings, self.signature = entries, postings, signature
        return self.song_count

    @staticmethod
    def _similarity(query, target):
        """Subsequence edit similarity, permitting a few missing/extra notes."""
        previous = [0] * (len(target) + 1)
        for row, pitch in enumerate(query, 1):
            current = [row]
            for col, other in enumerate(target, 1):
                current.append(min(previous[col] + 1, current[-1] + 1,
                                   previous[col - 1] + (pitch != other)))
            previous = current
        return 1 - min(previous) / len(query)

    def match(self, notes, limit=5, stop_event=None):
        melody = []
        for note in notes:
            pitch = int(note) % 12
            if not melody or pitch != melody[-1]:
                melody.append(pitch)
        query = melody[-24:]
        if len(query) < MIN_NOTES or len(set(query)) < 4:
            return []
        contour = _contour(query)
        votes = Counter()
        for qi in range(len(contour) - GRAM_SIZE + 1):
            _cancelled(stop_event)
            hits = self.postings.get(contour[qi:qi + GRAM_SIZE], ())
            # Common scale fragments carry little evidence and can be enormous.
            if len(hits) > 3000:
                continue
            for packed in hits:
                entry_id, pos = packed >> 32, packed & 0xffffffff
                shift = (query[qi] - self.entries[entry_id][2][pos]) % 12
                votes[(entry_id, pos - qi, shift)] += 1
        best = {}
        for (entry_id, start, shift), support in votes.most_common(80):
            _cancelled(stop_event)
            if support < 2:
                continue
            filename, title, pitches = self.entries[entry_id]
            target = [(pitch + shift) % 12 for pitch in pitches[max(0, start - 3):start + len(query) + 4]]
            score = self._similarity(query, target)
            if score < 0.80:
                continue
            if filename not in best or score > best[filename]["score"]:
                best[filename] = {"filename": filename, "title": title, "score": score,
                                  "notes": len(query)}
        ranked = sorted(best.values(), key=lambda entry: (-entry["score"], entry["title"].casefold()))
        return ranked[:limit]


def estimate_pitch(samples, sample_rate=SAMPLE_RATE):
    """Normalized FFT autocorrelation with a periodicity gate and peak refinement."""
    if np is None:
        raise RuntimeError("缺少 NumPy，请安装 requirements.txt 中的依赖")
    signal = np.asarray(samples, dtype=np.float64)
    if len(signal) < 256 or not np.isfinite(signal).all():
        return None, 0.0
    signal = signal - signal.mean()
    power = signal * signal
    rms = float(np.sqrt(power.mean()))
    level = max(0.0, min(1.0, (20 * math.log10(max(rms, 1e-12)) + 72) / 72))
    if rms < 0.0005:
        return None, level
    size = 1 << (2 * len(signal) - 1).bit_length()
    spectrum = np.fft.rfft(signal, n=size)
    correlation = np.fft.irfft(spectrum.real ** 2 + spectrum.imag ** 2, n=size)[:len(signal)]
    sums = np.concatenate(([0.0], np.cumsum(power)))
    low = max(2, int(sample_rate / 2200))
    high = min(len(signal) // 2, int(sample_rate / 80))
    lags = np.arange(high + 1)
    denominator = sums[len(signal) - lags] + sums[-1] - sums[lags]
    nsdf = 2 * correlation[:high + 1] / np.maximum(denominator, 1e-12)
    peaks = np.flatnonzero((nsdf[1:-1] >= nsdf[:-2]) & (nsdf[1:-1] > nsdf[2:])) + 1
    peaks = peaks[(peaks >= low) & (peaks < high)]
    if not len(peaks):
        return None, level
    strongest = float(nsdf[peaks].max())
    if strongest < 0.80:
        return None, level
    lag = int(peaks[np.flatnonzero(nsdf[peaks] >= max(0.80, strongest * 0.93))[0]])
    a, b, c = nsdf[lag - 1:lag + 2]
    curvature = a - 2 * b + c
    offset = 0.5 * (a - c) / curvature if abs(curvature) > 1e-12 else 0
    frequency = sample_rate / (lag + float(np.clip(offset, -0.5, 0.5)))
    midi = 69 + 12 * math.log2(frequency / 440)
    if abs(midi - round(midi)) > 0.45:
        return None, level
    return frequency, level


def nearest_midi(frequency):
    return None if frequency is None or frequency <= 0 else round(69 + 12 * math.log2(frequency / 440))


def note_name(midi):
    return ("C", "C♯", "D", "E♭", "E", "F", "F♯", "G", "A♭", "A", "B♭", "B")[midi % 12] + str(midi // 12 - 1)


class StreamingResampler:
    """Preserve sample position across arbitrary native-rate capture chunks."""

    def __init__(self, source_rate, target_rate=SAMPLE_RATE):
        self.step = source_rate / target_rate
        self.tail = np.empty(0)
        self.position = 0.0

    def feed(self, samples):
        data = np.concatenate((self.tail, np.asarray(samples)))
        positions = np.arange(self.position, len(data) - 1, self.step)
        result = np.interp(positions, np.arange(len(data)), data) if len(positions) else np.empty(0)
        next_position = positions[-1] + self.step if len(positions) else self.position
        self.position = next_position - max(0, len(data) - 1)
        self.tail = data[-1:]
        return result


class PitchTracker:
    """Overlap short frames and accept a pitch only after three stable frames."""

    def __init__(self):
        self.buffer = np.empty(0)
        self.position = 0
        self.candidate = self.current = None
        self.stable = self.silent = 0
        self.level = 0.0

    @property
    def seconds(self):
        return self.position / SAMPLE_RATE

    def feed(self, samples):
        self.buffer = np.concatenate((self.buffer, samples))
        events = []
        while len(self.buffer) >= FRAME_SIZE:
            frame = self.buffer[:FRAME_SIZE]
            frequency, self.level = estimate_pitch(frame)
            midi = nearest_midi(frequency)
            # A frame spanning two notes can suggest a false subharmonic or a
            # pitch between them. Require its shorter trailing frame to agree.
            recent, _ = estimate_pitch(frame[-1024:])
            if midi != nearest_midi(recent):
                midi = None
            if midi is None:
                self.candidate, self.stable = None, 0
                self.silent += 1
                if self.silent >= 6:
                    self.current = None
            else:
                self.silent = 0
                if midi == self.candidate:
                    self.stable += 1
                else:
                    self.candidate, self.stable = midi, 1
                if self.stable >= 3 and midi != self.current:
                    self.current = midi
                    events.append((self.seconds, midi))
            self.buffer = self.buffer[HOP_SIZE:]
            self.position += HOP_SIZE
        return events


def list_loopback_devices():
    if pyaudio is None:
        return [], "电脑声音监听需要 Windows 和 PyAudioWPatch"
    try:
        with pyaudio.PyAudio() as audio:
            return [(int(info["index"]), str(info["name"]))
                    for info in audio.get_loopback_device_info_generator()], ""
    except Exception as exc:
        return [], str(exc)


class RealtimeRecognizer(QObject):
    status = Signal(str)
    level = Signal(float)
    notes_changed = Signal(str)
    results = Signal(list)
    finished = Signal()

    def __init__(self, sheet_dir, parent=None, cache_file=None):
        super().__init__(parent)
        self.matcher = SheetMatcher(sheet_dir, cache_file)
        self._stop = threading.Event()
        self._clear = threading.Event()
        self._thread = None

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive())

    def start(self, device_index=None, device_name=None):
        if self.running:
            return False
        if pyaudio is None or np is None:
            self.status.emit("缺少电脑声音监听依赖，请安装 requirements.txt 后再试。")
            return False
        self._stop.clear()
        self._clear.clear()
        self._thread = threading.Thread(target=self._run, args=(device_index, device_name),
                                        name="SkyRealtimeRecognizer", daemon=True)
        self._thread.start()
        return True

    def clear(self):
        self._clear.set()

    def stop(self):
        self._stop.set()

    def shutdown(self):
        self.stop()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)

    def _run(self, device_index, device_name):
        try:
            self.status.emit("正在准备旋律曲库…")
            count = self.matcher.build(self._stop, self.status.emit)
            _cancelled(self._stop)
            if not count:
                self.status.emit("曲库中没有足够长的旋律曲谱，请先导入或更新曲库。")
                return
            with pyaudio.PyAudio() as audio:
                info = (audio.get_device_info_by_index(device_index) if device_index is not None
                        else audio.get_default_wasapi_loopback())
                if not info.get("isLoopbackDevice") or (device_name and info["name"] != device_name):
                    raise RuntimeError("声音设备已变化，请刷新设备后重试")
                rate, channels = int(info["defaultSampleRate"]), int(info["maxInputChannels"])
                audio_queue = queue.Queue(maxsize=128)
                overflow = threading.Event()

                def capture(data, frame_count, timing, flags):
                    if self._stop.is_set():
                        return None, pyaudio.paComplete
                    try:
                        audio_queue.put_nowait(data)
                    except queue.Full:
                        overflow.set()
                    return None, pyaudio.paContinue

                stream = audio.open(format=pyaudio.paFloat32, channels=channels, rate=rate,
                                    input=True, input_device_index=int(info["index"]),
                                    frames_per_buffer=max(256, rate // 20), stream_callback=capture)
                try:
                    self.status.emit(f"正在监听：{info['name']} · 曲库 {count:,} 首")
                    self._capture(stream, rate, channels, audio_queue, overflow)
                finally:
                    stream.close()
        except RecognitionCancelled:
            pass
        except Exception as exc:
            if not self._stop.is_set():
                self.status.emit(f"无法监听电脑声音：{exc}")
        finally:
            self.level.emit(0.0)
            self.finished.emit()

    def _capture(self, stream, rate, channels, audio_queue, overflow):
        resampler, tracker = StreamingResampler(rate), PitchTracker()
        history = deque(maxlen=64)
        revision = matched_revision = 0
        last_note_wall = last_level = last_match = time.monotonic()
        last_audio_wall = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if self._clear.is_set() or overflow.is_set() or (history and now - last_note_wall > 4):
                self._clear.clear()
                while True:
                    try:
                        audio_queue.get_nowait()
                    except queue.Empty:
                        break
                resampler = StreamingResampler(rate)
                if overflow.is_set():
                    overflow.clear()
                    self.status.emit("音频处理出现间断，正在重新收集旋律…")
                history.clear()
                tracker = PitchTracker()
                revision += 1
                self.notes_changed.emit("—")
                self.results.emit([])
            try:
                data = audio_queue.get(timeout=0.03)
            except queue.Empty:
                data = None
                if hasattr(stream, "is_active") and not stream.is_active() and not self._stop.is_set():
                    raise RuntimeError("声音设备已停止，请刷新设备后重试")
            if data:
                last_audio_wall = now
                samples = np.frombuffer(data, dtype="<f4").reshape(-1, channels)
                # Prefer the stronger channel, avoiding cancellation of opposite stereo phases.
                mono = samples[:, int(np.argmax(np.mean(samples ** 2, axis=0)))]
                for when, midi in tracker.feed(resampler.feed(mono)):
                    last_note_wall = now
                    if not history or history[-1][1] % 12 != midi % 12:
                        history.append((when, midi))
                        revision += 1
                while history and tracker.seconds - history[0][0] > 30:
                    history.popleft()
                    revision += 1
            if now - last_level >= 0.2:
                last_level = now
                self.level.emit(tracker.level if now - last_audio_wall < 0.3 else 0)
                self.notes_changed.emit(" · ".join(note_name(note) for _, note in list(history)[-16:]) or "—")
            if revision != matched_revision and now - last_match >= 1.2:
                last_match, matched_revision = now, revision
                matches = self.matcher.match([note for _, note in history], stop_event=self._stop)
                if not self._clear.is_set():
                    self.results.emit(matches)
