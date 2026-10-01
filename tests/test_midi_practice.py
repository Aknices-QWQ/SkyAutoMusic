import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication
from midi_keyboard import PracticeSession, WindowsMidi, note_name, score_groups
from midi_practice import MidiPracticePage


class MatchingTests(unittest.TestCase):
    def test_score_mapping_chords_and_invalid_keys(self):
        self.assertEqual(score_groups({50: ["1Key14"], 0: ["1Key0", "2Key0", "2Key4", "1Key99", "bad"]}, 48),
                         [frozenset({48, 55}), frozenset({72})])
        self.assertEqual(note_name(60), "C4")

    def test_chord_wrong_note_and_release(self):
        session = PracticeSession([{60, 64}, {67}])
        self.assertFalse(session.feed(0x90, 61, 90))
        self.assertFalse(session.feed(0x90, 60, 90))
        self.assertFalse(session.feed(0x90, 64, 90))
        self.assertEqual(session.mistakes, 1)
        self.assertTrue(session.feed(0x90, 61, 0))
        self.assertEqual(session.index, 1)

    def test_repeat_requires_new_press_and_all_channels_released(self):
        session = PracticeSession([{60}, {60}])
        self.assertTrue(session.feed(0x90, 60, 80))
        self.assertFalse(session.feed(0x90, 60, 80))
        session.feed(0x80, 60, 0)
        self.assertTrue(session.feed(0x90, 60, 80))
        session = PracticeSession([{60}], {(0, 60), (1, 60)})
        session.feed(0x80, 60, 0)
        self.assertEqual(session.held, {(1, 60)})
        self.assertEqual(session.index, 0)

    def test_notes_held_before_start_do_not_count(self):
        session = PracticeSession([{60, 64}], {(0, 60)})
        session.feed(0x90, 64, 100)
        self.assertEqual(session.index, 0)
        session.feed(0x80, 60, 0)
        self.assertTrue(session.feed(0x90, 60, 100))


class FakeMidi:
    def __init__(self):
        self.available = [(0, "测试 MIDI 键盘")]
        self.messages = []
        self.closed = False
        self.silenced = False
        self.open_error = False
        self.sound_error = False

    def devices(self):
        return list(self.available)

    def connect(self, device_id):
        if self.open_error:
            raise RuntimeError("device busy")
        self.closed = False

    def enable_sound(self, enabled):
        if enabled and self.sound_error:
            raise RuntimeError("no synth")

    def drain(self):
        messages, self.messages = self.messages, []
        return iter(messages)

    def send(self, *message):
        pass

    def silence(self):
        self.silenced = True

    def close(self):
        self.closed = True


class PageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.backend = FakeMidi()
        self.page = MidiPracticePage({}, backend=self.backend)
        self.page.toggle_connection()

    def tearDown(self):
        self.page.shutdown()
        self.page.deleteLater()

    def send(self, *messages):
        self.backend.messages.extend(messages)
        self.page.poll()

    def test_practice_complete_and_hot_unplug(self):
        self.page.set_score("测试曲", {0: ["1Key0"], 10: ["1Key1", "1Key2"]})
        self.page.start_practice()
        self.send((0x90, 60, 100), (0x80, 60, 0), (0x90, 62, 80), (0x90, 64, 80))
        self.assertEqual(self.page.progress.value(), 2)
        self.assertEqual(self.page.target_label.text(), "练习完成！")
        self.backend.available = []
        self.page.refresh_devices()
        self.assertIsNone(self.page.connected_device)
        self.assertIsNone(self.page.session)
        self.assertFalse(self.page.held)
        self.assertTrue(self.backend.closed)
        self.assertTrue(self.backend.silenced)

    def test_pedal_and_channel_panic(self):
        self.send((0x90, 60, 100), (0x91, 64, 80), (0xB0, 64, 127), (0xB0, 123, 0))
        self.assertEqual(self.page.held, {(1, 64)})
        self.send((0x91, 64, 0))
        self.assertFalse(self.page.keyboard.held)

    def test_score_and_octave_changes_cancel_session(self):
        self.page.set_score("测试", {0: ["1Key0"]})
        self.page.start_practice()
        self.page.octave.setCurrentIndex(1)
        self.assertIsNone(self.page.session)
        self.page.start_practice()
        self.assertEqual(self.page.session.target, {48})
        self.page.set_score("新曲", {0: ["1Key1"]})
        self.assertIsNone(self.page.session)

    def test_connection_failure_and_optional_sound_failure(self):
        self.page.toggle_connection()
        self.backend.open_error = True
        self.page.toggle_connection()
        self.assertIsNone(self.page.connected_device)
        self.assertIn("连接失败", self.page.connection_label.text())
        self.backend.open_error = False
        self.backend.sound_error = True
        self.page.toggle_connection()
        self.assertIsNotNone(self.page.connected_device)
        self.assertFalse(self.page.sound.isChecked())

    def test_stale_driver_messages_are_ignored(self):
        midi = WindowsMidi()
        midi.input_handle = 1  # exercise queue filtering without opening hardware
        midi.events.put((-1, 0x90, 60, 100))
        midi.events.put((0, 0x90, 62, 100))
        self.assertEqual(list(midi.drain()), [(0x90, 62, 100)])
        midi.input_handle = None

    def test_main_window_stop_and_close_release_midi(self):
        import play_music_qt as app

        # Exercise real window wiring without hotkeys, network checks or config writes.
        with patch.object(app.MainWindow, "bind_escape_stop"), \
                patch.object(app.MainWindow, "check_app_update"), \
                patch.object(app.MainWindow, "save_config"), \
                patch.object(app.MainWindow, "release_all_keys"), \
                patch.object(app.SheetSearchIndex, "start"):
            window = app.MainWindow()
            try:
                page = window.midi_practice
                backend = FakeMidi()
                page.backend = backend
                page.refresh_devices()
                page.toggle_connection()
                page.set_score("测试曲", {0: ["1Key0"]})
                window.nav_practice.click()
                self.assertIs(window.pages.currentWidget(), page)
                page.start_practice()
                self.assertIsNotNone(page.session)
                self.assertTrue(window.stop_event.is_set())
                window.force_stop()
                self.assertIsNone(page.session)
                self.assertIsNotNone(page.connected_device)
                self.assertTrue(backend.silenced)
            finally:
                window.close()
            self.assertTrue(backend.closed)
            self.assertFalse(page.poll_timer.isActive())
            self.assertFalse(page.scan_timer.isActive())


if __name__ == "__main__":
    unittest.main()
