"""Archive staging regressions that need neither Qt nor a compiler."""

import tempfile
import unittest
import zipfile
from pathlib import Path

from tools.prepare_desktop_release import extract_library


class ExtractLibraryTests(unittest.TestCase):
    def extract(self, entries, expected_count):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        archive = root / 'library.zip'
        with zipfile.ZipFile(archive, 'w') as stream:
            for name, payload in entries:
                stream.writestr(name, payload)
        destination = root / 'sheets'
        destination.mkdir()
        count = extract_library(archive, destination, expected_count)
        return count, {path.name: path.read_bytes() for path in destination.iterdir()}

    def test_illegal_characters_and_collisions_keep_every_payload(self):
        count, files = self.extract([
            ('folder/a|b.json', b'first'),
            ('a?b.json', b'second'),
            ('A_B.json', b'third'),
            ('曲谱.json', '中文内容'.encode()),
        ], 4)
        self.assertEqual(count, 4)
        self.assertEqual(files, {
            'a_b.json': b'first', 'a_b (1).json': b'second',
            'A_B (2).json': b'third', '曲谱.json': '中文内容'.encode(),
        })

    def test_windows_device_names_can_be_written(self):
        count, files = self.extract([
            ('CON.json', b'console'),
            ('nul.extra.json', b'null'),
            ('LPT9.json', b'printer'),
        ], 3)
        self.assertEqual(count, 3)
        self.assertEqual(set(files), {'_CON.json', '_nul.extra.json', '_LPT9.json'})

    def test_all_windows_illegal_characters_are_replaced(self):
        _, files = self.extract([('a<>:"|?*\x01.json', b'notes')], 1)
        self.assertEqual(files, {'a________.json': b'notes'})

    def test_case_collisions_keep_both_sheets(self):
        _, files = self.extract([('folder/Score.json', b'one'), ('score.JSON', b'two')], 2)
        self.assertEqual(files, {'Score.json': b'one', 'score (1).JSON': b'two'})

    def test_backup_and_non_sheet_files_do_not_change_count(self):
        count, files = self.extract([
            ('score.json', b'notes'), ('score.bak.json', b'backup'),
            ('score.wrong-base-test.json', b'backup'),
            ('score.space-rebuild.test.json', b'backup'), ('readme.txt', b'text'),
        ], 1)
        self.assertEqual(count, 1)
        self.assertEqual(files, {'score.json': b'notes'})

    def test_path_traversal_and_absolute_paths_are_rejected(self):
        for name in ('../escape.json', 'folder/../../escape.json',
                     'folder\\..\\escape.json', '/absolute.json'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'Unsafe path'):
                self.extract([(name, b'notes')], 1)

    def test_count_mismatch_is_still_rejected(self):
        with self.assertRaisesRegex(ValueError, 'expected 2, got 1'):
            self.extract([('score.json', b'notes')], 2)


if __name__ == '__main__':
    unittest.main()
