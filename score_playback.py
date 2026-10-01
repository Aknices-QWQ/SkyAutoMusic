"""Cancellable note playback, independent of Qt and Windows key delivery."""

from dataclasses import dataclass, field
import random
import threading
import time


@dataclass
class PlaybackSession:
    times: tuple
    notes: dict
    start_index: int
    end_index: int
    end_ms: int
    preview: bool = False
    countdown: int = 3
    loop: bool = False
    compensate: bool = False
    stop: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    seek_index: int | None = None
    position_index: int | None = None
    next_index: int | None = None

    def seek(self, index):
        with self.lock:
            self.seek_index = max(self.start_index, min(self.end_index - 1, index))

    def _wait(self, seconds, seekable=True):
        deadline = time.monotonic() + max(0, seconds)
        while not self.stop.is_set():
            if seekable:
                with self.lock:
                    if self.seek_index is not None:
                        return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self.stop.wait(min(.03, remaining))

    def run(self, emit, play, release, transform, factor, delay, resume_index=None):
        """Callbacks receive snapshots; no widgets are accessed by this worker."""
        stopped = True
        try:
            if not self.times or self.start_index >= self.end_index:
                return
            for remaining in range(self.countdown if not self.preview else 0, 0, -1):
                if self.stop.is_set():
                    return
                emit("status", f"{remaining} 秒后开始，请切到目标窗口")
                self.stop.wait(1)
            index = max(self.start_index, min(self.end_index - 1,
                                             resume_index if resume_index is not None else self.start_index))
            while not self.stop.is_set():
                with self.lock:
                    if self.seek_index is not None:
                        index, self.seek_index = self.seek_index, None
                if index >= self.end_index:
                    if not self.loop:
                        stopped = False
                        break
                    index = self.start_index
                keys = [transform(key) for key in self.notes[self.times[index]]]
                if self.preview:
                    emit("notes", keys)
                else:
                    try:
                        play(keys)
                        self.stop.wait(.05)
                    finally:
                        release(keys)
                with self.lock:
                    self.position_index = index
                    self.next_index = index + 1
                emit("progress", {"index": index, "keys": keys})
                next_time = self.times[index+1] if index+1 < self.end_index else self.end_ms
                interval = max(0, next_time - self.times[index]) / 1000 * factor()
                if not self.preview and not self.compensate:
                    interval = max(0, interval - .05)
                jitter = delay()
                if jitter:
                    interval *= 1 + random.uniform(-jitter, jitter) / 100
                # A one-note loop must always wait instead of spinning.
                self._wait(max(.05 if self.loop else 0, interval))
                index += 1
        except Exception as exc:
            emit("error", str(exc))
        finally:
            emit("finished", stopped or self.stop.is_set())
