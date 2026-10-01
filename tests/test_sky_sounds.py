import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import sky_sounds


class SkySoundTests(unittest.TestCase):
    def test_bundled_instruments_have_all_upstream_samples(self):
        root = Path(sky_sounds.__file__).resolve().parent / "assets" / "sky_sounds" / "Specy"
        for name in sky_sounds.SPECY_INSTRUMENTS:
            count = sky_sounds.SAMPLE_COUNTS.get(name, 15)
            with self.subTest(instrument=name):
                files = sky_sounds.sample_files(root / name, count)
                self.assertEqual(len(files), count)
                self.assertTrue(all(path.suffix == ".wav" for path in files.values()))

    def test_pitched_wave_preserves_pcm_and_changes_sample_rate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "sample.wav"
            with wave.open(str(source), "wb") as writer:
                writer.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
                writer.writeframes(b"\x00\x00\x40\x00" * 100)
            target = sky_sounds.pitched_wave(source, "D", root / "cache")
            with wave.open(str(source), "rb") as original, wave.open(str(target), "rb") as shifted:
                self.assertEqual(shifted.readframes(100), original.readframes(100))
                self.assertEqual(shifted.getframerate(), round(24000 * 2 ** (2 / 12)))
            self.assertEqual(sky_sounds.pitched_wave(source, "D", root / "cache"), target)

    def test_requires_all_fifteen_numbered_samples(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "Harp"
            folder.mkdir()
            for index in range(14):
                (folder / f"{index}.mp3").write_bytes(b"ID3" + b"x" * 128)
            self.assertEqual(sky_sounds.sample_files(folder), {})
            (folder / "14.mp3").write_bytes(b"ID3" + b"x" * 128)
            self.assertEqual(len(sky_sounds.sample_files(folder)), 15)
            self.assertEqual(sky_sounds.instrument_folders(directory)[0][0], "Harp")

    def test_download_fetches_only_missing_samples(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, _):
                return b"ID3" + b"x" * 128

        class Opener:
            def __init__(self):
                self.urls = []

            def open(self, url, timeout):
                self.urls.append(url)
                return Response()

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "Harp"
            folder.mkdir()
            (folder / "0.mp3").write_bytes(b"ID3" + b"x" * 128)
            opener = Opener()
            progress = []
            with patch.object(sky_sounds.urllib.request, "build_opener", return_value=opener):
                sky_sounds.download_specy_instrument("Harp", folder, lambda done, total: progress.append((done, total)))
            self.assertEqual(len(opener.urls), 14)
            self.assertTrue(opener.urls[0].endswith("/Harp/1.mp3"))
            self.assertEqual(progress[-1], (15, 15))
            self.assertEqual(len(sky_sounds.sample_files(folder)), 15)

    def test_download_retries_with_system_proxy_after_direct_failure(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, _):
                return b"ID3" + b"x" * 128

        class FailingOpener:
            def open(self, _url, timeout):
                raise OSError("direct connection reset")

        class WorkingOpener:
            def open(self, _url, timeout):
                return Response()

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "Harp"
            folder.mkdir()
            openers = iter((FailingOpener(), WorkingOpener()))
            with patch.object(sky_sounds.urllib.request, "build_opener", side_effect=lambda *args: next(openers)):
                sky_sounds.download_specy_instrument("Harp", folder)
            self.assertEqual(len(sky_sounds.sample_files(folder)), 15)

    def test_location_presets_have_unique_names_and_valid_pitches(self):
        names = [name for name, _pitch, _condition in sky_sounds.LOCATION_PRESETS]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn(("云野 · 八人图", "Db", ""), sky_sounds.LOCATION_PRESETS)
        self.assertTrue(all(pitch in sky_sounds.PITCHES for _, pitch, _ in sky_sounds.LOCATION_PRESETS))


if __name__ == "__main__":
    unittest.main()
