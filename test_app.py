from pathlib import Path
import unittest
from unittest.mock import patch

from app import Alarm, alarm_issues, alarm_names_using_sound, normalize_time, overlapping_alarm_indices, sound_title


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

    def test_alarm_names_using_sound_matches_by_file_name(self):
        alarms = [
            Alarm("Mañana", ["Mon"], "08:00:00", "08:05:00", "/old/path/alarm.mp3"),
            Alarm("Tarde", ["Mon"], "18:00:00", "18:05:00", "/sounds/other.mp3"),
        ]
        self.assertEqual(alarm_names_using_sound(alarms, Path("/new/path/alarm.mp3")), ["Mañana"])


class AlarmOverlapTests(unittest.TestCase):
    def test_shared_day_overlaps_mark_both_alarms(self):
        alarms = [
            Alarm("A", ["Mon"], "08:00:00", "09:00:00", "a.mp3"),
            Alarm("B", ["Mon", "Tue"], "08:30:00", "10:00:00", "b.mp3"),
            Alarm("C", ["Tue"], "08:30:00", "10:00:00", "c.mp3"),
        ]
        self.assertEqual(overlapping_alarm_indices(alarms), {0, 1, 2})

    def test_touching_intervals_do_not_overlap(self):
        alarms = [
            Alarm("A", ["Mon"], "08:00:00", "09:00:00", "a.mp3"),
            Alarm("B", ["Mon"], "09:00:00", "10:00:00", "b.mp3"),
        ]
        self.assertEqual(overlapping_alarm_indices(alarms), set())

    def test_issues_describe_overlap_and_missing_sound(self):
        alarms = [
            Alarm("Trabajo", ["Mon"], "08:00:00", "09:00:00", "missing-a.mp3"),
            Alarm("Reunión", ["Mon"], "08:30:00", "10:00:00", "missing-b.mp3"),
        ]
        issues = alarm_issues(alarms)
        self.assertIn("Se solapa con «Reunión».", issues[0])
        self.assertIn("No se encuentra el fichero de sonido «missing-a.mp3».", issues[0])
        self.assertIn("Se solapa con «Trabajo».", issues[1])


if __name__ == "__main__":
    unittest.main()
