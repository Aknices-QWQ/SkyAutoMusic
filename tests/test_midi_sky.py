import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from PySide6.QtWidgets import QApplication

from midi_sky import MidiSkyPanel, MidiSkyRouter, SKY_PHYSICAL_KEYS, SkyWindows


class FakeWindows:
    def __init__(self):
        self.ok = True
        self.focus = True

    def exists(self, target):
        return self.ok

    def devices(self):
        return [((123, 456), "光遇测试窗口")]

    def focused(self, target):
        return self.focus and self.ok


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.windows = FakeWindows()
        self.router = MidiSkyRouter(
            lambda key, up: self.sent.append((key, up)) or True,
            lambda: self.windows.focus,
            base=60,
        )
        self.router.enabled = True

    def test_notes_map_and_last_channel_release(self):
        self.assertTrue(self.router.feed(0x90, 60, 100))
        self.assertTrue(self.router.feed(0x91, 60, 100))
        self.assertEqual(self.sent, [(SKY_PHYSICAL_KEYS[0], False)])
        self.router.feed(0x80, 60, 0)
        self.assertEqual(len(self.sent), 1)
        self.router.feed(0x81, 60, 0)
        self.assertEqual(self.sent[-1], (SKY_PHYSICAL_KEYS[0], True))

    def test_black_keys_and_velocity_zero_are_safe(self):
        self.assertFalse(self.router.feed(0x90, 61, 100))
        self.assertFalse(self.router.feed(0x90, 60, 0))
        self.assertEqual(self.sent, [])
        self.router.feed(0x90, 59, 100)
        self.router.feed(0x90, 85, 100)
        self.assertEqual(self.sent, [])
        self.router.feed(0x90, 84, 100)
        self.router.feed(0x90, 84, 0)
        self.assertEqual(self.sent, [("/", False), ("/", True)])

    def test_disabled_messages_cannot_be_replayed_on_start(self):
        self.router.stop()
        self.router.feed(0x90, 60, 100)
        self.router.enabled = True
        self.assertFalse(self.router.feed(0x90, 60, 100))
        self.assertEqual(self.sent, [])
        self.router.feed(0x80, 60, 0)
        self.assertTrue(self.router.feed(0x90, 60, 100))

    def test_duplicate_press_and_stop_release_each_key_once(self):
        self.router.feed(0x90, 60, 100)
        self.router.feed(0x90, 60, 100)
        self.router.feed(0x90, 64, 100)
        self.router.stop()
        self.router.stop()
        self.assertCountEqual(self.sent, [("Y", False), ("I", False), ("Y", True), ("I", True)])
        self.assertFalse(self.router.held)

    def test_sustain_does_not_keep_game_keys_held(self):
        self.router.feed(0x90, 60, 100)
        self.router.feed(0xB0, 64, 127)
        self.router.feed(0x80, 60, 0)
        self.assertFalse(self.router.held)

    def test_failed_send_stops_and_releases_previous_keys(self):
        self.router.feed(0x90, 60, 100)
        original = self.router.send_key
        self.router.send_key = lambda key, up: original(key, up) if up else False
        with self.assertRaisesRegex(RuntimeError, "按键发送失败"):
            self.router.feed(0x90, 62, 100)
        self.assertFalse(self.router.enabled)
        self.assertFalse(self.router.held)
        self.assertEqual(self.sent[-1], ("Y", True))

    def test_focus_loss_releases_and_requires_fresh_press(self):
        self.router.feed(0x90, 60, 100)
        self.windows.focus = False
        self.assertFalse(self.router.check_focus())
        self.assertEqual(self.sent[-1], (SKY_PHYSICAL_KEYS[0], True))
        self.windows.focus = True
        self.assertFalse(self.router.feed(0x90, 60, 100))
        self.router.feed(0x80, 60, 0)
        self.assertTrue(self.router.feed(0x90, 60, 100))

    def test_channel_panic_releases_only_that_channel(self):
        self.router.feed(0x90, 60, 100)
        self.router.feed(0x91, 62, 100)
        self.router.feed(0xB0, 123, 0)
        self.assertEqual(self.router.held, {(1, 62): 1})
        self.assertEqual(self.sent[-1], (SKY_PHYSICAL_KEYS[0], True))


class FakeMidi:
    def __init__(self):
        self.available = [(0, "测试键盘")]
        self.messages = []
        self.closed = True

    def devices(self):
        return list(self.available)

    def connect(self, device):
        self.closed = False

    def close(self):
        self.closed = True

    def drain(self):
        messages, self.messages = self.messages, []
        return messages


class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.sent = []
        self.backend, self.windows = FakeMidi(), FakeWindows()
        self.panel = MidiSkyPanel(lambda key, up: self.sent.append((key, up)) or True,
                                  {}, backend=self.backend, windows=self.windows)
        self.addCleanup(self.panel.deleteLater)
        self.addCleanup(self.panel.shutdown)

    def connect_and_play(self):
        self.panel.toggle_connection()
        self.panel.start()
        self.backend.messages = [(0x90, 60, 100)]
        self.panel.poll()

    def test_start_is_explicit_and_requires_keyboard_and_game(self):
        self.assertFalse(self.panel.router.enabled)
        self.panel.start()
        self.assertFalse(self.panel.router.enabled)
        self.panel.toggle_connection()
        self.windows.ok = False
        self.panel.start()
        self.assertFalse(self.panel.router.enabled)
        self.assertEqual(self.sent, [])

    def test_unplug_or_game_closed_releases_held_keys(self):
        self.connect_and_play()
        self.windows.ok = False
        self.panel.poll()
        self.assertFalse(self.panel.router.enabled)
        self.assertEqual(self.sent[-1], ("Y", True))
        self.windows.ok = True
        self.backend.messages = [(0x80, 60, 0)]
        self.panel.poll()
        self.panel.start()
        self.backend.messages = [(0x90, 60, 100)]
        self.panel.poll()
        self.backend.available = []
        self.panel.refresh_devices()
        self.assertTrue(self.backend.closed)
        self.assertIsNone(self.panel.connected_device)
        self.assertFalse(self.panel.router.held)
        self.assertEqual(self.sent[-1], ("Y", True))

    def test_poll_preserves_fast_presses_for_score_following(self):
        self.panel.toggle_connection()
        self.panel.start()
        changes = []
        self.panel.keys_changed.connect(lambda keys: changes.append(keys))
        self.backend.messages = [(0x90, 60, 100), (0x80, 60, 0), (0x90, 62, 100), (0x80, 62, 0)]
        self.panel.poll()
        self.assertIn(["1Key0"], changes)
        self.assertIn(["1Key1"], changes)
        self.assertEqual(changes[-1], [])

    def test_native_windows_enumeration_smoke(self):
        self.assertIsInstance(SkyWindows().devices(), list)

if __name__ == "__main__":
    unittest.main()
