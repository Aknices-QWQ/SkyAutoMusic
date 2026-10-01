import unittest

from floating_score import ScoreCursor


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


if __name__ == "__main__":
    unittest.main()
