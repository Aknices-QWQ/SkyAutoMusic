import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import album_art


class AlbumArtTests(unittest.TestCase):
    def test_search_ranks_title_and_artist_then_caches_artwork(self):
        with tempfile.TemporaryDirectory() as directory:
            candidates = {
                "results": [
                    {
                        "trackName": "Blue Bird",
                        "artistName": "Aqua",
                        "collectionName": "Wrong Album",
                        "artworkUrl100": "https://art.example/wrong/100x100bb.jpg",
                    },
                    {
                        "trackName": "Blue Bird",
                        "artistName": "Ikimono-gakari",
                        "collectionName": "My Song Your Song",
                        "artworkUrl100": "https://art.example/right/100x100bb.jpg",
                    },
                ],
            }
            with patch.object(album_art, "_read_json", return_value=candidates) as search, \
                    patch.object(album_art, "_open_bytes", return_value=b"\xff\xd8\xffjpeg") as download:
                result = album_art.find_album_art("Blue Bird", "Ikimono-gakari", directory)
                cached = album_art.find_album_art("Blue Bird", "Ikimono-gakari", directory)

            self.assertEqual(result["artist"], "Ikimono-gakari")
            self.assertEqual(result["album"], "My Song Your Song")
            self.assertGreaterEqual(result["score"], 0.99)
            self.assertEqual(cached["image_path"], result["image_path"])
            self.assertEqual(Path(result["image_path"]).read_bytes(), b"\xff\xd8\xffjpeg")
            search.assert_called_once()
            download.assert_called_once_with("https://art.example/right/600x600bb.jpg")

    def test_no_match_is_cached_without_repeating_the_search(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(album_art, "_read_json", return_value={"results": []}) as search:
                self.assertIsNone(album_art.find_album_art("No Such Track", cache_dir=directory))
                self.assertIsNone(album_art.find_album_art("No Such Track", cache_dir=directory))
            search.assert_called_once()

    def test_non_image_response_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(album_art, "_read_json", return_value={
                "results": [{
                    "trackName": "Song", "artistName": "Singer",
                    "artworkUrl100": "https://art.example/100x100bb.jpg",
                }],
            }), patch.object(album_art, "_open_bytes", return_value=b"not an image"):
                with self.assertRaisesRegex(RuntimeError, "封面格式"):
                    album_art.find_album_art("Song", "Singer", directory)


if __name__ == "__main__":
    unittest.main()
