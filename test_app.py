from pathlib import Path
import unittest
from unittest.mock import patch

from app import normalize_time, sound_title


class NormalizeTimeTests(unittest.TestCase):
    def test_start_fills_missing_values_with_zero(self):
        self.assertEqual(normalize_time("7", ceil=False), "07:00:00")
        self.assertEqual(normalize_time("7:20", ceil=False), "07:20:00")

    def test_end_fills_missing_values_with_fifty_nine(self):
        self.assertEqual(normalize_time("7", ceil=True), "07:59:59")
        self.assertEqual(normalize_time("7:20", ceil=True), "07:20:59")

    def test_explicit_seconds_are_preserved(self):
        self.assertEqual(normalize_time("7:20:12", ceil=False), "07:20:12")
        self.assertEqual(normalize_time("7:20:12", ceil=True), "07:20:12")

    def test_compact_time_is_expanded(self):
        self.assertEqual(normalize_time("0700", ceil=False), "07:00:00")
        self.assertEqual(normalize_time("0700", ceil=True), "07:00:59")
        self.assertEqual(normalize_time("720", ceil=False), "07:20:00")
        self.assertEqual(normalize_time("720", ceil=True), "07:20:59")
        self.assertEqual(normalize_time("072015", ceil=False), "07:20:15")
        self.assertEqual(normalize_time("072015", ceil=True), "07:20:15")

    def test_invalid_values_are_rejected(self):
        for value in ("", "24", "1260", "12:60", "123060", "12:30:60", "abc"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_time(value, ceil=False)


class SoundTitleTests(unittest.TestCase):
    @patch("app.EasyID3", return_value={"title": ["Despertar tranquilo"]})
    def test_mp3_title_metadata_is_preferred(self, _easy_id3):
        self.assertEqual(sound_title(Path("audio-01.mp3")), "Despertar tranquilo")

    def test_file_stem_is_used_when_metadata_is_unavailable(self):
        self.assertEqual(sound_title(Path("alarma-suave.mp3")), "Alarma Suave")


if __name__ == "__main__":
    unittest.main()
