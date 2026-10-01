import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication

from beginner_lessons import BeginnerCourse, LESSONS
from midi_practice import MidiPracticePage
from test_midi_practice import FakeMidi


class CourseTests(unittest.TestCase):
    def test_chunking_clean_rounds_and_explicit_advance(self):
        course = BeginnerCourse(LESSONS[1], 60)
        self.assertEqual([len(c) for c in course.chunks], [4, 4, 1])
        self.assertFalse(course.advance())
        course.finish_round(0)
        course.finish_round(1)
        self.assertEqual(course.clean_rounds, 0)
        course.finish_round(0)
        course.finish_round(0)
        self.assertTrue(course.ready)
        self.assertFalse(course.complete)
        self.assertTrue(course.advance())
        self.assertEqual(course.chunk_index, 1)
        self.assertEqual(course.clean_rounds, 0)
        self.assertEqual(course.errors, 1)

    def test_fingers_transpose_and_hands(self):
        self.assertEqual(LESSONS[1].fingers(48)[48], 1)
        self.assertEqual(LESSONS[2].fingers(48)[48], 5)
        self.assertEqual(LESSONS[2].fingers(48)[55], 1)
        for lesson in LESSONS:
            self.assertTrue(all(0 <= offset <= 7 for offset in lesson.offsets))


class BeginnerPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.backend = FakeMidi()
        self.page = MidiPracticePage({"beginner_mode": True}, backend=self.backend)
        self.page.toggle_connection()
        self.addCleanup(self.page.shutdown)

    def send(self, *messages):
        self.backend.messages.extend(messages)
        self.page.poll()

    def play_round(self):
        for group in list(self.page.session.groups):
            note = next(iter(group))
            self.send((0x90, note, 80), (0x80, note, 0))

    def test_beginner_without_library_completes_and_persists(self):
        self.page.start_practice()
        self.assertEqual(self.page.notes, {})
        self.play_round()
        self.assertFalse(self.page.course.complete)
        self.assertEqual(self.page.course.attempts, 1)
        self.assertFalse(self.page.next_button.isEnabled())
        self.page.start_practice()
        self.play_round()
        self.assertTrue(self.page.course.complete)
        self.assertEqual(self.page.course.attempts, 2)
        self.assertIn("find_c", self.page.settings()["beginner_progress"])
        # Extra releases after completion must not add more rounds or records.
        self.send((0x80, 60, 0))
        self.assertEqual(self.page.course.attempts, 2)
        self.page.advance_course()
        self.assertEqual(self.page.lesson_selector.currentIndex(), 1)
        self.assertIsNone(self.page.course)

    def test_wrong_notes_reset_clean_streak(self):
        self.page.start_practice()
        self.play_round()
        self.page.start_practice()
        self.send((0x90, 61, 80), (0x80, 61, 0))
        self.play_round()
        self.assertEqual(self.page.course.clean_rounds, 0)
        self.assertFalse(self.page.next_button.isEnabled())

    def test_stop_cancels_demo_and_metronome(self):
        self.page.start_demo()
        self.assertTrue(self.page.demo_timer.isActive())
        self.assertIsNotNone(self.page.demo_note)
        self.page.metronome.setChecked(True)
        self.page.stop_practice()
        self.assertFalse(self.page.demo_timer.isActive())
        self.assertFalse(self.page.beat_timer.isActive())
        self.assertIsNone(self.page.demo_note)
        self.assertTrue(self.page.start_button.isEnabled())
        self.assertTrue(self.backend.silenced)

    def test_demo_never_advances_progress(self):
        self.page.start_demo()
        self.send((0x90, 60, 80), (0x80, 60, 0))
        for _ in range(8):
            self.page.demo_tick()
        self.assertFalse(self.page.demo_timer.isActive())
        self.assertEqual(self.page.course.attempts, 0)
        self.assertEqual(self.page.lesson_progress, {})

    def test_lesson_mode_octave_changes_stop_activity(self):
        self.page.start_practice()
        self.page.set_score("background library update", {0: ["1Key7"]})
        self.assertIsNotNone(self.page.session)
        self.page.octave.setCurrentIndex(1)
        self.assertIsNone(self.page.session)
        self.assertIsNone(self.page.course)
        self.page.start_practice()
        self.assertEqual(self.page.session.target, {48})
        self.page.practice_mode.setCurrentIndex(0)
        self.assertIsNone(self.page.session)
        self.assertFalse(self.page.keyboard.beginner)
        self.page.start_practice()
        self.assertEqual(self.page.session.target, {60})

    def test_visual_metronome_skips_late_ticks_and_resets(self):
        self.page.sound.setChecked(False)
        with patch("midi_practice.time.monotonic", return_value=10):
            self.page.metronome.setChecked(True)
            self.page.beat_tick()
        self.assertEqual(self.page.beat_label.text(), "● ○ ○ ○")
        with patch("midi_practice.time.monotonic", return_value=20):
            self.page.beat_tick()
        self.assertEqual(self.page.beat_label.text(), "○ ● ○ ○")
        self.assertEqual(self.page.next_beat_time, 21)


if __name__ == "__main__":
    unittest.main()
