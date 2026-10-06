import os
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from floating_score import KeyboardScoreInput, ScoreCursor


class ScoreCursorTests(unittest.TestCase):
    def test_groups_collapse_layers_and_ignore_invalid_keys(self):
        cursor = ScoreCursor()
        cursor.set_score({10: ["1Key0", "2Key0", "1Key99"], 0: ["1Key2"]})
        self.assertEqual(cursor.groups, [(0, frozenset({2})), (10, frozenset({0}))])

    def test_repeat_chord_requires_release_and_fresh_strike(self):
        cursor = ScoreCursor()
        cursor.set_score({0: ["1Key0"], 10: ["1Key0"]})
        self.assertTrue(cursor.feed(["1Key0"]))
        self.assertFalse(cursor.feed(["1Key0"]))
        self.assertFalse(cursor.feed([]))
        self.assertTrue(cursor.feed(["1Key0"]))
        self.assertEqual(cursor.index, 2)

    def test_seek_and_move_are_bounded(self):
        cursor = ScoreCursor()
        cursor.set_score({0: ["1Key0"]})
        cursor.move(-5)
        self.assertEqual(cursor.index, 0)
        cursor.move(5)
        self.assertEqual(cursor.index, 1)

    def test_wrong_notes_and_sequential_chord_notes_do_not_advance(self):
        cursor = ScoreCursor()
        cursor.set_score({0: ["1Key0", "2Key2"], 10: ["1Key1"]})
        for keys in (["1Key1"], [], ["1Key0"], [], ["1Key2"], []):
            self.assertFalse(cursor.feed(keys))
        self.assertFalse(cursor.feed(["1Key0", "1Key1", "1Key2"]))
        self.assertEqual(cursor.index, 0)
        cursor.feed([])
        self.assertFalse(cursor.feed(["1Key0"]))
        self.assertTrue(cursor.feed(["1Key0", "1Key2"]))
        self.assertEqual(cursor.index, 1)

    def test_overlapping_chords_require_a_fresh_strike_of_shared_notes(self):
        cursor = ScoreCursor()
        cursor.set_score({0: ["1Key0", "1Key2"], 10: ["1Key0", "1Key4"]})
        self.assertTrue(cursor.feed(["1Key0", "1Key2"]))
        cursor.feed(["1Key0"])
        self.assertFalse(cursor.feed(["1Key0", "1Key4"]))
        cursor.feed([])
        self.assertTrue(cursor.feed(["1Key0", "1Key4"]))

    def test_starting_with_held_keys_requires_release_before_matching(self):
        cursor = ScoreCursor()
        cursor.set_score({0: ["1Key0"]})
        cursor.reset_input(["1Key0"])
        self.assertFalse(cursor.feed(["1Key0"]))
        cursor.feed([])
        self.assertTrue(cursor.feed(["1Key0"]))


class KeyboardScoreInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.input = KeyboardScoreInput({0x15: 0, 0x17: 2, 0x27: 9, 0x33: 12, 0x34: 13, 0x35: 14})
        self.callbacks = []
        self.removers = []

        def hook(callback, suppress=False):
            self.assertFalse(suppress)
            self.callbacks.append(callback)
            remove = Mock()
            self.removers.append(remove)
            return remove

        self.hook = patch("keyboard.hook", side_effect=hook)
        self.pressed = patch("keyboard.is_pressed", return_value=False)
        self.hook.start()
        self.pressed_mock = self.pressed.start()
        self.addCleanup(self.hook.stop)
        self.addCleanup(self.pressed.stop)
        self.addCleanup(self.input.stop)

    def event(self, scan, down=True):
        return SimpleNamespace(scan_code=scan, event_type="down" if down else "up")

    def test_fast_notes_and_chords_are_delivered_in_order_on_the_qt_thread(self):
        changes, threads = [], []
        self.input.keys_changed.connect(lambda keys, modified: (changes.append(keys), threads.append(QThread.currentThread())))
        self.input.start()
        callback = self.callbacks[-1]
        events = [self.event(0x15), self.event(0x15), self.event(0x17),
                  self.event(0x15, False), self.event(0x17, False)]
        worker = threading.Thread(target=lambda: [callback(event) for event in events])
        worker.start()
        worker.join()
        self.assertEqual(changes, [])
        self.app.processEvents()
        self.assertEqual(changes, [["1Key0"], ["1Key0", "1Key2"], ["1Key2"], []])
        self.assertTrue(all(thread == self.input.thread() for thread in threads))

    def test_stopped_or_old_queued_events_cannot_advance_a_new_session(self):
        changes = []
        self.input.keys_changed.connect(lambda keys, modified: changes.append(keys))
        self.input.start()
        old_callback = self.callbacks[-1]
        old_callback(self.event(0x15))
        self.input.stop()
        self.app.processEvents()
        self.assertEqual(changes, [])
        self.removers[0].assert_called_once()
        self.input.start()
        old_callback(self.event(0x17))
        self.callbacks[-1](self.event(0x15))
        self.app.processEvents()
        self.assertEqual(changes, [["1Key0"]])

    def test_held_keys_at_start_and_punctuation_use_physical_scan_codes(self):
        changes = []
        self.pressed_mock.side_effect = lambda key: key == 0x15
        self.input.keys_changed.connect(lambda keys, modified: changes.append(keys))
        self.input.start()
        self.assertEqual(self.input.keys, ["1Key0"])
        callback = self.callbacks[-1]
        callback(self.event(0x15))
        callback(self.event(0x15, False))
        callback(self.event(0x35))
        callback(self.event(0x27))
        callback(self.event(0x33))
        callback(self.event(0x34))
        callback(self.event(0x01))
        self.app.processEvents()
        self.assertEqual(changes, [[], ["1Key14"], ["1Key9", "1Key14"],
                                   ["1Key9", "1Key12", "1Key14"],
                                   ["1Key9", "1Key12", "1Key13", "1Key14"]])

    def test_fast_control_shortcuts_preserve_modifiers_before_qt_processes_them(self):
        changes = []
        self.input.keys_changed.connect(lambda keys, modified: changes.append((keys, modified)))
        self.input.start()
        callback = self.callbacks[-1]
        for event in (self.event(0x1D), self.event(0x15), self.event(0x15, False),
                      self.event(0x1D, False), self.event(0x15)):
            callback(event)
        self.app.processEvents()
        self.assertEqual(changes, [(["1Key0"], True), ([], True), (["1Key0"], False)])


if __name__ == "__main__":
    unittest.main()
