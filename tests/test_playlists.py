import tempfile
import unittest
from pathlib import Path

from playlists import (
    create_playlist,
    load_playlists,
    normalize_playlists,
    rename_playlist,
    save_playlists,
    set_song_membership,
)


class PlaylistTests(unittest.TestCase):
    def test_create_add_remove_rename_and_persist(self):
        data = create_playlist({}, "通勤")
        data = set_song_membership(data, "通勤", "Song.json", True)
        data = set_song_membership(data, "通勤", "Song.json", True)
        self.assertEqual(data["通勤"], ["Song.json"])

        data = rename_playlist(data, "通勤", "晨间")
        data = set_song_membership(data, "晨间", "Song.json", False)
        self.assertEqual(data, {"晨间": []})

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "playlists.json"
            save_playlists(path, {"晨间": ["Song.json"]})
            self.assertEqual(load_playlists(path), {"晨间": ["Song.json"]})

    def test_duplicate_names_and_path_values_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "同名"):
            create_playlist({"Favorites": []}, "favorites")
        self.assertEqual(
            normalize_playlists({"bad": ["../outside.json", "Song.json", "song.JSON"]}),
            {"bad": ["Song.json"]},
        )

    def test_song_membership_requires_an_existing_playlist(self):
        with self.assertRaisesRegex(ValueError, "找不到"):
            set_song_membership({}, "missing", "Song.json", True)


if __name__ == "__main__":
    unittest.main()
