import unittest

from app import normalize_time


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

    def test_invalid_values_are_rejected(self):
        for value in ("", "24", "12:60", "12:30:60", "abc"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_time(value, ceil=False)


if __name__ == "__main__":
    unittest.main()
