import json
import os
import threading
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

import play_music_qt as app
from midi_keyboard import PracticeSession


class FeaturePagesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.folder = folder
        (folder / "config.json").write_text('{"theme":"carbon"}', encoding="utf-8")
        for name, key in (("A", "1Key0"), ("B", "1Key2")):
            (folder / f"{name}.json").write_text(json.dumps({"songName": name, "songNotes": [{"time": 0, "key": key}]}), encoding="utf-8")
        sheets = folder / "Sheet Music"
        sheets.mkdir()
        for name in ("A", "B"):
            (folder / f"{name}.json").rename(sheets / f"{name}.json")
        for name, value in (("APP_DIR", folder), ("SHEET_MUSIC_DIR", sheets),
                            ("CONFIG_FILE", folder / "config.json"),
                            ("FAVORITES_FILE", folder / "favorites.json")):
            self.stack.enter_context(patch.object(app, name, value))
        for method in ("bind_escape_stop", "check_app_update", "save_config", "release_all_keys"):
            self.stack.enter_context(patch.object(app.MainWindow, method))
        self.stack.enter_context(patch.object(app.SheetSearchIndex, "start"))
        self.window = app.MainWindow()
        self.addCleanup(self.window.close)

    def test_pages_and_import_controls_are_separate(self):
        window = self.window
        window.nav_transcribe.click()
        self.assertIs(window.pages.currentWidget(), window.transcribe_page)
        self.assertTrue(window.transcribe_page.isAncestorOf(window.import_midi_files_button))
        self.assertFalse(window.play_page.isAncestorOf(window.import_midi_files_button))
        self.assertFalse(window.transcribe_search.isWindow())
        window.nav_practice.click()
        self.assertIs(window.pages.currentWidget(), window.midi_practice)
        window.nav_play.click()
        self.assertIs(window.pages.currentWidget(), window.play_page)

    def test_game_tools_have_one_page_and_all_shortcuts_open_midi_tab(self):
        window = self.window
        window.show()
        window.nav_game.click()
        self.assertIs(window.pages.currentWidget(), window.game_page)
        self.assertEqual([window.game_tabs.tabText(i) for i in range(3)],
                         ["悬浮琴谱", "MIDI 接入", "手机同步"])
        self.assertTrue(window.game_page.isAncestorOf(window.midi_game))
        self.assertTrue(window.game_page.isAncestorOf(window.mobile_toggle_button))
        self.assertFalse(window.help_page.isAncestorOf(window.midi_game))
        for button in (window.game_midi_button, window.overlay.midi_button):
            window.pages.setCurrentWidget(window.play_page)
            button.click()
            self.assertIs(window.pages.currentWidget(), window.game_page)
            self.assertEqual(window.game_tabs.currentIndex(), 1)
            self.assertTrue(window.nav_game.isChecked())
            self.assertFalse(window.nav_update.isChecked())

    def test_score_entry_opens_score_and_select_song_returns_to_playback(self):
        window = self.window
        window.nav_game.click()
        window.game_open_score.click()
        self.assertTrue(window.overlay.isVisible())
        self.assertTrue(window.overlay.score_button.isChecked())
        window.game_open_score.click()
        self.assertTrue(window.overlay.isVisible())
        window.select_file("B.json")
        self.assertEqual(window.game_song_label.text(), "B")
        window.game_choose_song.click()
        self.assertTrue(window.nav_play.isChecked())
        self.assertFalse(window.nav_game.isChecked())
        window.overlay_btn.click()
        self.assertFalse(window.overlay.isVisible())
        window.overlay_btn.click()
        self.assertTrue(window.overlay.isVisible())
        window.clear_selected_song()
        self.assertEqual(window.game_song_label.text(), "未选择曲谱")

    def test_f7_in_game_page_starts_selected_game_mode(self):
        window = self.window
        window.show()
        window.show_game_midi()
        with patch.object(window.midi_game, "start") as midi, patch.object(window, "start_play") as play:
            window.signals.start_requested.emit()
            midi.assert_called_once()
            play.assert_not_called()
            window.game_tabs.setCurrentIndex(0)
            window.signals.start_requested.emit()
            self.assertTrue(window.overlay.score_timer.isActive())
            play.assert_not_called()
            window.game_tabs.setCurrentIndex(2)
            window.signals.start_requested.emit()
            play.assert_not_called()

    def configure_game_midi(self):
        panel = self.window.midi_game
        backend = Mock()
        backend.devices.return_value = [(0, "测试 MIDI")]
        backend.drain.return_value = []
        windows = Mock()
        windows.devices.return_value = [((123, 456), "测试光遇")]
        windows.exists.return_value = windows.focused.return_value = True
        panel.backend, panel.windows = backend, windows
        panel.refresh_devices()
        panel.refresh_targets()
        panel.toggle_connection()
        panel.start()
        return panel, backend

    def test_overlay_tracks_playback_selection_and_exact_note_group(self):
        window = self.window
        cursor = window.overlay.score_view.cursor
        self.assertEqual(cursor.target, {0})
        window.midi_practice.song_selector.setCurrentIndex(window.midi_practice.song_selector.findData("B.json"))
        window.midi_practice.choose_score()
        self.assertEqual(cursor.target, {0})
        window.select_file("B.json")
        self.assertEqual(cursor.target, {2})
        notes = {i: [f"1Key{i % 15}"] for i in range(200)}
        window.sorted_times = list(notes)
        window.overlay.set_score(notes)
        window.stop_event.clear()
        window.on_worker_progress(0, "0:00", "2/200", ["1Key1"])
        self.assertEqual(cursor.index, 1)
        window.clear_selected_song()
        self.assertEqual(cursor.groups, [])

    def test_guide_scroll_never_sends_game_keys_and_escape_stops_it(self):
        window = self.window
        overlay = window.overlay
        overlay.score_button.setChecked(True)
        overlay.toggle_score()
        overlay.set_score({0: ["1Key0"], 500: ["1Key1"]})
        overlay.show()
        with patch.object(app, "send_scan_key") as send:
            window.signals.score_scroll_requested.emit()
            self.assertTrue(overlay.score_timer.isActive())
            overlay.score_scroll_tick()
            self.assertEqual(overlay.score_view.cursor.index, 1)
            send.assert_not_called()
            window.signals.stop_requested.emit()
        self.assertFalse(overlay.score_timer.isActive())
        self.assertFalse(overlay.auto_score_button.isChecked())

    def test_clickthrough_keeps_scrolling_and_f4_unlocks(self):
        window = self.window
        window.global_hotkeys_registered = True
        overlay = window.overlay
        overlay.score_button.setChecked(True)
        overlay.toggle_score()
        overlay.show()
        overlay.auto_score_button.click()
        window.signals.overlay_lock_requested.emit()
        self.assertTrue(overlay.windowFlags() & Qt.WindowTransparentForInput)
        self.assertTrue(overlay.score_timer.isActive())
        window.signals.overlay_lock_requested.emit()
        self.assertFalse(overlay.windowFlags() & Qt.WindowTransparentForInput)
        self.assertTrue(overlay.score_timer.isActive())

    def test_game_midi_follows_fast_notes_and_escape_releases_keys(self):
        window = self.window
        overlay = window.overlay
        overlay.score_button.setChecked(True)
        overlay.midi_follow.setChecked(True)
        overlay.set_score({0: ["1Key0"], 100: ["1Key1"]})
        with patch.object(app, "send_scan_key", return_value=True) as send:
            panel, backend = self.configure_game_midi()
            backend.drain.return_value = [(0x90, 60, 100), (0x80, 60, 0), (0x90, 62, 100)]
            panel.poll()
            self.assertEqual(overlay.score_view.cursor.index, 2)
            window.signals.stop_requested.emit()
            self.assertFalse(panel.router.held)
            self.assertFalse(panel.router.enabled)
            send.assert_any_call("U", True)

    def test_connecting_editor_releases_game_midi_before_opening_device(self):
        window = self.window
        with patch.object(app, "send_scan_key", return_value=True) as send:
            panel, backend = self.configure_game_midi()
            backend.drain.return_value = [(0x90, 60, 100)]
            panel.poll()
            editor_backend = Mock()
            editor_backend.devices.return_value = [(0, "测试 MIDI")]
            window.score_editor.midi_backend = editor_backend
            window.score_editor.refresh_midi_devices()
            editor_backend.connect.side_effect = lambda _: self.assertIsNone(panel.connected_device)
            window.score_editor.toggle_midi_connection()
            self.assertFalse(panel.router.enabled)
            self.assertFalse(panel.router.held)
            backend.close.assert_called()
            editor_backend.connect.assert_called_once()
            send.assert_any_call("Y", True)

    def test_closing_main_closes_overlay_and_game_midi(self):
        window = self.window
        with patch.object(app, "send_scan_key", return_value=True) as send:
            panel, backend = self.configure_game_midi()
            backend.drain.return_value = [(0x90, 60, 100)]
            panel.poll()
            window.overlay.show()
            window.close()
            self.assertFalse(panel.router.enabled)
            self.assertFalse(panel.poll_timer.isActive())
            self.assertFalse(window.overlay.isVisible())
            backend.close.assert_called()
            send.assert_any_call("Y", True)

    def test_realtime_result_opens_score_and_stops_listening(self):
        window = self.window
        page = window.realtime_recognition
        self.assertEqual(window.transcribe_tabs.tabText(3), "实时识曲")
        self.assertFalse(page.recognizer.running)
        page.on_results([{"filename": "B.json", "title": "B", "score": 0.95}])
        with patch.object(page, "stop_listening") as stop:
            page.open_button.click()
        stop.assert_called_once()
        self.assertEqual(window.selected_file, "B.json")
        self.assertIs(window.pages.currentWidget(), window.play_page)
        self.assertTrue(window.nav_play.isChecked())

    def test_realtime_no_match_clears_old_results_and_escape_stops_listening(self):
        window = self.window
        page = window.realtime_recognition
        page.on_results([{"filename": "B.json", "title": "B", "score": 0.95}])
        page.on_results([])
        self.assertEqual(page.results_list.count(), 0)
        self.assertFalse(page.open_button.isEnabled())
        with patch.object(page, "stop_listening") as stop:
            window.force_stop()
        stop.assert_called_once()

    def test_practice_selection_does_not_change_playback(self):
        window = self.window
        page = window.midi_practice
        page.song_selector.setCurrentIndex(page.song_selector.findData("B.json"))
        page.choose_score()
        self.assertEqual(page.song_title, "B")
        self.assertEqual(window.selected_file, "A.json")
        session = PracticeSession([{64}])
        page.session = session
        window.select_file("B.json")
        window.select_file("A.json")
        self.assertEqual(page.selected_file, "B.json")
        self.assertIs(page.session, session)
        window.refresh_files()
        self.assertIs(page.session, session)

    def test_removing_practice_score_clears_old_session(self):
        page = self.window.midi_practice
        page.session = PracticeSession([{60}])
        (self.folder / "Sheet Music" / "A.json").unlink()
        self.window.refresh_files()
        self.assertEqual(page.selected_file, "B.json")
        self.assertIsNone(page.session)

    def test_import_completion_keeps_selection_and_status_on_import_page(self):
        window = self.window
        window.show_transcribe()
        window.transcribe_tabs.setCurrentIndex(1)
        window.on_midi_convert_done({"song_name": "B", "filename": "B.json"}, "")
        self.assertEqual(window.selected_file, "A.json")
        self.assertEqual(window.import_progress.value(), 100)
        self.assertEqual(window.import_status.text(), "已导入：B")
        self.assertIs(window.pages.currentWidget(), window.transcribe_page)

    def test_f7_uses_practice_action_on_practice_page(self):
        window = self.window
        with patch.object(window, "start_play") as play, patch.object(window.midi_practice, "start_practice") as practice:
            window.nav_practice.click()
            window.signals.start_requested.emit()
            practice.assert_called_once()
            play.assert_not_called()
            window.nav_play.click()
            window.signals.start_requested.emit()
            play.assert_called_once()

    def test_piastudy_result_keeps_embedded_search_open(self):
        window = self.window
        window.open_piastudy_search("test query")
        window.transcribe_search._on_convert_done({"song_name": "测试"}, "")
        self.assertIs(window.pages.currentWidget(), window.transcribe_page)
        self.assertEqual(window.transcribe_search.status_label.text(), "已添加：测试")
        self.assertEqual(window.transcribe_search.search_box.text(), "test query")

    def test_piastudy_detail_url_opens_link_import(self):
        window = self.window
        page_url = "https://piastudy.com/Intermediate/1LoVrqGKT"
        window.open_piastudy_search(page_url)
        self.assertEqual(window.transcribe_search.url_box.text(), page_url)
        self.assertEqual(window.transcribe_search.search_box.text(), "")

    def test_sky_sound_selection_syncs_compose_and_settings(self):
        window = self.window
        editor = window.score_editor
        harp = editor.sound_combo.findData("specy:Harp")
        editor.sound_combo.setCurrentIndex(harp)
        self.assertEqual(window.sky_sound_instrument_combo.currentData(), "specy:Harp")
        self.assertEqual(window.play_sound_combo.currentData(), "specy:Harp")
        self.assertFalse(editor.sound_combo.itemIcon(harp).isNull())
        self.assertFalse(editor.sound_download_button.isVisible())
        self.assertEqual(len(window.sky_sound_files), 15)

        folder = self.folder / "Sky Sounds" / "Specy" / "Harp"
        folder.mkdir(parents=True)
        for index in range(15):
            (folder / f"{index}.mp3").write_bytes(b"ID3" + b"x" * 128)
        window.refresh_sky_sound_choices()
        self.assertEqual(len(window.sky_sound_files), 15)
        self.assertFalse(editor.sound_download_button.isEnabled())

    def test_score_editor_keyboard_records_chord_on_release(self):
        editor = self.window.score_editor
        self.window.show()
        self.window.nav_compose.click()
        editor.note_buttons[0].setFocus()
        QTest.keyPress(editor.note_buttons[0], Qt.Key_Y)
        QTest.keyPress(editor.note_buttons[0], Qt.Key_I)
        self.assertEqual(editor.events, [])
        QTest.keyRelease(editor.note_buttons[0], Qt.Key_Y)
        QTest.keyRelease(editor.note_buttons[0], Qt.Key_I)
        self.assertEqual(editor.events[0]["keys"], ["1Key0", "1Key2"])

    def test_separate_recording_latches_sequential_keyboard_notes_until_space(self):
        editor = self.window.score_editor
        self.window.show()
        self.window.nav_compose.click()
        editor.input_mode.setCurrentIndex(editor.input_mode.findData("separate"))
        target = editor.note_buttons[0]
        with patch.object(editor, "preview_note") as preview:
            QTest.keyClick(target, Qt.Key_Y)
            QTest.keyClick(target, Qt.Key_I)
            self.assertEqual(editor.selected_keys(), ["1Key0", "1Key2"])
            self.assertEqual(editor.events, [])
            self.assertEqual(editor.position.value(), 0)
            self.assertFalse(editor.held_notes)
            self.assertEqual(preview.call_count, 2)
            QTest.keyClick(target, Qt.Key_Space)
        self.assertEqual(editor.events, [{"time": 0, "keys": ["1Key0", "1Key2"]}])
        self.assertEqual(editor.position.value(), 500)
        self.assertEqual(editor.selected_keys(), [])
        QTest.keyClick(target, Qt.Key_O)
        editor.commit_button.click()
        self.assertEqual(editor.events[1], {"time": 500, "keys": ["1Key3"]})

    def test_separate_recording_can_cancel_notes_and_clear_without_writing(self):
        editor = self.window.score_editor
        editor.input_mode.setCurrentIndex(editor.input_mode.findData("separate"))
        with patch.object(editor, "preview_note") as preview:
            editor.feed_input_note(0, True, ("keyboard", 0))
            editor.feed_input_note(0, True, ("keyboard", 0))
            editor.feed_input_note(0, False, ("keyboard", 0))
            self.assertEqual(editor.selected_keys(), ["1Key0"])
            editor.feed_input_note(0, True, ("keyboard", 0))
            editor.feed_input_note(0, False, ("keyboard", 0))
            self.assertEqual(editor.selected_keys(), [])
            self.assertEqual(preview.call_count, 1)
        editor.note_buttons[2].click()
        self.assertEqual(editor.selected_keys(), ["1Key2"])
        editor.clear_selection.click()
        editor.commit_button.click()
        self.assertEqual(editor.events, [])
        self.assertEqual(editor.position.value(), 0)

    def test_separate_recording_accumulates_midi_and_mouse_notes(self):
        editor = self.window.score_editor
        editor.input_mode.setCurrentIndex(editor.input_mode.findData("separate"))
        editor.connected_device = (0, "Test MIDI")
        messages = [(0x90, 60, 90), (0x80, 60, 0), (0x90, 64, 90), (0x90, 64, 0)]
        with patch.object(editor.midi_backend, "drain", return_value=messages):
            editor.poll_midi()
        editor.note_buttons[4].click()
        self.assertEqual(editor.selected_keys(), ["1Key0", "1Key2", "1Key4"])
        self.assertEqual(editor.events, [])
        editor.add_button.click()
        self.assertEqual(editor.events[0]["keys"], ["1Key0", "1Key2", "1Key4"])
        self.assertEqual(editor.selected_keys(), [])

    def test_switching_record_modes_clears_uncommitted_notes_and_old_releases(self):
        editor = self.window.score_editor
        editor.feed_input_note(0, True, ("keyboard", 0))
        editor.input_mode.setCurrentIndex(editor.input_mode.findData("separate"))
        editor.feed_input_note(0, False, ("keyboard", 0))
        self.assertEqual(editor.events, [])
        self.assertFalse(editor.input_chord)
        editor.feed_input_note(2, True, ("keyboard", 2))
        editor.input_mode.setCurrentIndex(editor.input_mode.findData("piano"))
        editor.feed_input_note(2, False, ("keyboard", 2))
        self.assertEqual(editor.selected_keys(), [])
        self.assertEqual(editor.events, [])
        editor.feed_input_note(4, True, ("keyboard", 4))
        editor.feed_input_note(4, False, ("keyboard", 4))
        self.assertEqual(editor.events[0]["keys"], ["1Key4"])

    def test_separate_recording_shortcuts_do_not_interfere_with_title_input(self):
        editor = self.window.score_editor
        self.window.show()
        self.window.nav_compose.click()
        editor.input_mode.setCurrentIndex(editor.input_mode.findData("separate"))
        editor.title_input.setFocus()
        QTest.keyClicks(editor.title_input, "Y I")
        self.assertEqual(editor.title_input.text(), "Y I")
        self.assertEqual(editor.events, [])
        self.assertEqual(editor.selected_keys(), [])

    def test_score_editor_midi_maps_major_scale_to_sky_key(self):
        editor = self.window.score_editor
        class FakeMidi:
            def __init__(self):
                self.messages = []

            def devices(self):
                return [(0, "Test MIDI")]

            def connect(self, _device_id):
                pass

            def drain(self):
                messages, self.messages = self.messages, []
                return messages

            def close(self):
                pass

        backend = FakeMidi()
        editor.midi_backend = backend
        editor.refresh_midi_devices()
        editor.toggle_midi_connection()
        backend.messages = [(0x90, 67, 90), (0x90, 61, 90), (0x80, 67, 0)]
        editor.poll_midi()
        self.assertEqual(editor.events[0]["keys"], ["1Key4"])
        self.assertEqual(editor.position.value(), 500)

    def test_location_selects_pitch_and_remembers_manual_correction(self):
        window = self.window
        location = window.play_location_combo
        location.setCurrentIndex(location.findData("云野 · 八人图"))
        self.assertEqual(window.play_pitch_combo.currentText(), "Db")
        window.play_pitch_combo.setCurrentText("F")
        location.setCurrentIndex(location.findData("遇境"))
        self.assertEqual(window.play_pitch_combo.currentText(), "C")
        location.setCurrentIndex(location.findData("云野 · 八人图"))
        self.assertEqual(window.play_pitch_combo.currentText(), "F")
        self.assertEqual(window.sky_location_pitches["云野 · 八人图"], "F")

    def test_changing_location_stops_current_preview(self):
        window = self.window
        with patch.object(window, "stop_synth_preview_audio") as stop_synth:
            location = window.play_location_combo
            location.setCurrentIndex(location.findData("云野 · 八人图"))
        stop_synth.assert_called_once()

    def test_sky_sound_chord_uses_separate_media_players(self):
        window = self.window
        folder = self.folder / "Harp"
        folder.mkdir()
        for index in range(15):
            (folder / f"{index}.mp3").write_bytes(b"ID3" + b"x" * 128)
        window.sky_sound_files = app.sample_files(folder)
        with patch.object(app, "QAudioOutput") as audio, patch.object(app, "QMediaPlayer") as media:
            window._play_preview_note(["1Key0", "1Key2"])
        self.assertEqual(media.call_count, 2)
        self.assertEqual(audio.call_count, 2)

    def test_bundled_samples_use_cached_sound_effects(self):
        window = self.window
        window.select_sky_sound("specy:Harp", save=False)
        with patch.object(app, "QSoundEffect") as effects, patch.object(app, "QMediaPlayer") as media:
            effects.return_value.isPlaying.return_value = False
            window._play_preview_note(["1Key0", "1Key2"])
            window._play_preview_note(["1Key0"])
        self.assertEqual(effects.call_count, 2)
        media.assert_not_called()

    def test_chord_preview_does_not_restart_held_sample(self):
        window = self.window
        folder = self.folder / "Harp"
        folder.mkdir()
        for index in range(15):
            (folder / f"{index}.mp3").write_bytes(b"ID3" + b"x" * 128)
        window.sky_sound_files = app.sample_files(folder)
        with patch.object(app, "QAudioOutput"), patch.object(app, "QMediaPlayer") as media:
            window._play_preview_note(["1Key0"])
            window._play_preview_note(["1Key2"])
        self.assertEqual(media.return_value.stop.call_count, 0)

    def test_location_pitch_changes_sample_playback_rate(self):
        window = self.window
        folder = self.folder / "Harp"
        folder.mkdir()
        for index in range(15):
            (folder / f"{index}.mp3").write_bytes(b"ID3" + b"x" * 128)
        window.sky_sound_files = app.sample_files(folder)
        window.play_pitch_combo.setCurrentText("D")
        with patch.object(app, "QAudioOutput"), patch.object(app, "QMediaPlayer") as media:
            window._play_preview_note(["1Key0"])
        media.return_value.setPlaybackRate.assert_called_once()
        rate = media.return_value.setPlaybackRate.call_args.args[0]
        self.assertAlmostEqual(rate, 2 ** (2 / 12))

    def test_sky_sound_preview_from_worker_runs_on_qt_thread(self):
        window = self.window
        folder = self.folder / "Harp"
        folder.mkdir()
        for index in range(15):
            (folder / f"{index}.mp3").write_bytes(b"ID3" + b"x" * 128)
        window.sky_sound_files = app.sample_files(folder)
        with patch.object(app, "QAudioOutput"), patch.object(app, "QMediaPlayer") as media:
            worker = threading.Thread(target=window.preview_note, args=(["1Key0"],))
            worker.start()
            worker.join()
            self.app.processEvents()
        self.assertEqual(media.call_count, 1)


if __name__ == "__main__":
    unittest.main()
