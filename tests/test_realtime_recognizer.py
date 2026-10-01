import json
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np

import realtime_recognizer as recognition


MELODY = [0, 2, 4, 7, 5, 11, 9, 2, 5, 0, 7, 4, 11, 5, 9, 4, 2, 7, 11, 0, 5, 9, 7, 2]


def score(pitches, name="Test", spacing=500):
    return {"songName": name, "songNotes": [
        {"time": index * spacing, "key": f"1Key{recognition.SKY_MAJOR.index(pitch)}"}
        for index, pitch in enumerate(pitches)
    ]}


class MelodyMatchingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.write("A.json", score(MELODY, "A"))
        self.write("B.json", score([9, 7, 11, 2, 9, 0, 5, 4, 7, 2, 11, 5, 0, 2, 4, 9], "B"))
        self.matcher = recognition.SheetMatcher(self.folder, self.folder / "index.json.gz")
        self.matcher.build()

    def write(self, filename, data):
        (self.folder / filename).write_text(json.dumps(data), encoding="utf-8")

    def test_finds_middle_of_song_in_another_key_and_octave(self):
        detected = [pitch + 75 for pitch in MELODY[4:22]]
        result = self.matcher.match(detected)
        self.assertEqual(result[0]["filename"], "A.json")
        self.assertEqual(result[0]["score"], 1.0)

    def test_tolerates_one_missing_or_wrong_detected_note(self):
        detected = [pitch + 60 for pitch in MELODY]
        del detected[10]
        detected[16] += 1
        result = self.matcher.match(detected)
        self.assertEqual(result[0]["filename"], "A.json")
        self.assertGreater(result[0]["score"], 0.85)

    def test_rejects_short_repetitive_and_unrelated_melodies(self):
        self.assertEqual(self.matcher.match(MELODY[:8]), [])
        self.assertEqual(self.matcher.match([60, 62, 64] * 10), [])
        self.assertEqual(self.matcher.match([60, 61, 66, 63, 68, 65, 70, 67, 72, 69, 74, 71]), [])

    def test_index_refreshes_same_filename_after_edit(self):
        self.write("A.json", score([pitch for pitch in reversed(MELODY)], "Edited"))
        self.matcher.build()
        loaded = recognition.SheetMatcher(self.folder, self.folder / "index.json.gz")
        loaded.build()
        self.assertEqual(loaded.entries, self.matcher.entries)
        self.assertTrue(any(entry[1] == "Edited" for entry in loaded.entries))

    def test_corrupt_cache_is_rebuilt_and_invalid_scores_are_skipped(self):
        (self.folder / "index.json.gz").write_bytes(b"invalid gzip")
        self.write("Bad.json", {"songNotes": [{"time": 0, "key": "1Key99"}]})
        rebuilt = recognition.SheetMatcher(self.folder, self.folder / "index.json.gz")
        self.assertEqual(rebuilt.build(), 2)

    def test_build_can_be_cancelled_before_listening(self):
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaises(recognition.RecognitionCancelled):
            self.matcher.build(cancelled)

    def test_layered_accompaniment_does_not_interrupt_lead_melody(self):
        notes = score(MELODY)["songNotes"]
        notes.extend({"time": index * 500 + 250, "key": "1Key0", "l": 2}
                     for index in range(len(MELODY)))
        melodies = recognition.score_melodies(notes)
        self.assertIn(bytes(MELODY), melodies)


class PitchDetectionTests(unittest.TestCase):
    def test_callback_keeps_audio_while_matching_is_busy(self):
        if recognition.pyaudio is None:
            self.skipTest("Windows capture dependency unavailable")
        queries = []
        stop_writer = threading.Event()
        writer_threads = []
        source_rate = 48000
        audio = []
        for pitch in MELODY:
            timeline = np.arange(source_rate // 4) / source_rate
            frequency = 440 * 2 ** ((pitch + 63 - 69) / 12)
            audio.extend(0.15 * np.sin(2 * np.pi * frequency * timeline))
        stereo = np.repeat(np.asarray(audio)[:, None], 2, axis=1).astype("<f4")

        class Stream:
            def is_active(self):
                return not stop_writer.is_set()

            def close(self):
                stop_writer.set()
                for writer in writer_threads:
                    writer.join(timeout=1)

        class Audio:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def get_default_wasapi_loopback(self):
                return {"isLoopbackDevice": True, "index": 1, "name": "Test",
                        "defaultSampleRate": source_rate, "maxInputChannels": 2}

            def open(self, **kwargs):
                callback = kwargs["stream_callback"]

                def write():
                    for start in range(0, len(stereo), source_rate // 20):
                        if stop_writer.is_set():
                            break
                        data = stereo[start:start + source_rate // 20]
                        callback(data.tobytes(), len(data), {}, 0)
                        stop_writer.wait(0.015)

                writer = threading.Thread(target=write)
                writer_threads.append(writer)
                writer.start()
                return Stream()

        def slow_match(notes, **kwargs):
            queries.append(list(notes))
            if len(queries) == 1:
                time.sleep(0.35)
            return []

        with TemporaryDirectory() as folder:
            recognizer = recognition.RealtimeRecognizer(folder)
            with patch.object(recognizer.matcher, "build", return_value=1), \
                    patch.object(recognizer.matcher, "match", side_effect=slow_match), \
                    patch.object(recognition.pyaudio, "PyAudio", return_value=Audio()):
                self.assertTrue(recognizer.start())
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline and not any(len(q) >= len(MELODY) for q in queries):
                    time.sleep(0.01)
                recognizer.shutdown()
                self.assertFalse(recognizer.running)
        self.assertTrue(any([pitch % 12 for pitch in q] == [(p + 3) % 12 for p in MELODY]
                            for q in queries), queries)

    def test_detects_sine_and_rich_tones_and_rejects_noise(self):
        timeline = np.arange(recognition.FRAME_SIZE) / recognition.SAMPLE_RATE
        for midi in (48, 60, 62, 65, 69, 74, 81, 84, 91):
            frequency = 440 * 2 ** ((midi - 69) / 12)
            tone = 0.10 * np.sin(2 * np.pi * frequency * timeline)
            tone += 0.08 * np.sin(4 * np.pi * frequency * timeline)
            with self.subTest(midi=midi):
                found, _ = recognition.estimate_pitch(tone)
                self.assertEqual(recognition.nearest_midi(found), midi)
        self.assertIsNone(recognition.estimate_pitch(np.zeros(recognition.FRAME_SIZE))[0])
        noise = np.random.default_rng(123).normal(0, 0.03, recognition.FRAME_SIZE)
        self.assertIsNone(recognition.estimate_pitch(noise)[0])

    def test_streaming_rate_conversion_and_audio_to_score_match(self):
        source_rate = 44100
        audio = []
        for pitch in MELODY:
            timeline = np.arange(round(source_rate * 0.25)) / source_rate
            frequency = 440 * 2 ** ((pitch + 63 - 69) / 12)
            audio.extend(0.15 * np.sin(2 * np.pi * frequency * timeline))
        audio = np.asarray(audio)
        tracker = recognition.PitchTracker()
        converter = recognition.StreamingResampler(source_rate)
        events = []
        for start in range(0, len(audio), 997):
            events.extend(tracker.feed(converter.feed(audio[start:start + 997])))
        detected = [midi for _, midi in events]
        # Overlapping tones can briefly shift an octave. Matching treats those
        # as the same pitch class and collapses the duplicate observation.
        pitch_classes = []
        for midi in detected:
            if not pitch_classes or pitch_classes[-1] != midi % 12:
                pitch_classes.append(midi % 12)
        self.assertEqual(pitch_classes, [(pitch + 3) % 12 for pitch in MELODY])
        with TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "A.json").write_text(json.dumps(score(MELODY)), encoding="utf-8")
            matcher = recognition.SheetMatcher(path)
            matcher.build()
            self.assertEqual(matcher.match(detected)[0]["filename"], "A.json")

    def test_silent_output_can_stop_without_blocking_read(self):
        opened = threading.Event()
        closed = threading.Event()

        class SilentStream:
            def get_read_available(self):
                return 0

            def close(self):
                closed.set()

        class FakeAudio:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def get_default_wasapi_loopback(self):
                return {"isLoopbackDevice": True, "index": 1, "name": "Test",
                        "defaultSampleRate": 48000, "maxInputChannels": 2}

            def open(self, **kwargs):
                opened.set()
                return SilentStream()

        if recognition.pyaudio is None:
            self.skipTest("Windows capture dependency unavailable")
        with TemporaryDirectory() as folder:
            recognizer = recognition.RealtimeRecognizer(folder)
            with patch.object(recognizer.matcher, "build", return_value=1), \
                    patch.object(recognition.pyaudio, "PyAudio", return_value=FakeAudio()):
                self.assertTrue(recognizer.start())
                self.assertTrue(opened.wait(1))
                recognizer.shutdown()
                self.assertFalse(recognizer.running)
                self.assertTrue(closed.is_set())


if __name__ == "__main__":
    unittest.main()
