from pathlib import Path
from datetime import datetime, timedelta, timezone
import struct
import unittest
from unittest.mock import MagicMock, patch

import app
from app import (
    Alarm,
    SingleInstanceGuard,
    alarm_issues,
    alarm_names_using_sound,
    format_duration,
    next_alarm_occurrence,
    normalize_time,
    overlapping_alarm_indices,
    sound_title,
    tray_status_text,
)


def require_clock_api(name):
    """Fail with a focused message while clock production code is pending."""
    try:
        return getattr(app, name)
    except AttributeError as exc:
        raise AssertionError(f"app.{name} has not been implemented") from exc


class AdjustableClockTests(unittest.TestCase):
    def test_unavailable_ntp_keeps_alarms_on_system_clock_and_warns(self):
        app_instance = object.__new__(app.AlarmApp)
        app_instance.running = True
        app_instance.clock = app.AdjustableClock(offset_seconds=42)
        app_instance.clock_manually_set = False
        app_instance.ntp_results = app.queue.SimpleQueue()
        app_instance.ntp_results.put((False, "network unavailable"))
        app_instance.ntp_sync_finished = False

        with patch("app.messagebox.showwarning") as warning:
            app.AlarmApp.poll_ntp_result(app_instance)

        self.assertEqual(app_instance.clock.offset_seconds, 0)
        warning.assert_called_once()
        self.assertIn("servidor horario NTP", warning.call_args.args[1])

    def test_unavailable_ntp_does_not_clear_a_manual_adjustment(self):
        app_instance = object.__new__(app.AlarmApp)
        app_instance.running = True
        app_instance.clock = app.AdjustableClock(offset_seconds=42)
        app_instance.clock_manually_set = True
        app_instance.ntp_results = app.queue.SimpleQueue()
        app_instance.ntp_results.put((False, "network unavailable"))
        app_instance.ntp_sync_finished = False

        with patch("app.messagebox.showwarning") as warning:
            app.AlarmApp.poll_ntp_result(app_instance)

        self.assertEqual(app_instance.clock.offset_seconds, 42)
        self.assertIn("hora ajustada manualmente", warning.call_args.args[1])

    def test_offset_is_applied_to_system_time(self):
        clock_type = require_clock_api("AdjustableClock")
        system_now = datetime(2026, 10, 12, 10, 20, 30)
        clock = clock_type(offset_seconds=90)

        self.assertEqual(clock.now(system_now), system_now + timedelta(seconds=90))

    def test_network_time_sets_offset_without_changing_system_clock(self):
        clock_type = require_clock_api("AdjustableClock")
        system_now = datetime(2026, 10, 12, 10, 20, 30)
        network_now = datetime(2026, 10, 12, 10, 18, 0)
        clock = clock_type()

        clock.set_from_network(network_now, system_now=system_now)

        self.assertEqual(clock.now(system_now), network_now)

    def test_manual_time_adjustment_changes_virtual_time_of_day(self):
        clock_type = require_clock_api("AdjustableClock")
        system_now = datetime(2026, 10, 12, 10, 20, 30)
        clock = clock_type()

        clock.set_adjusted_time("09:15:45", system_now=system_now)

        self.assertEqual(clock.now(system_now), datetime(2026, 10, 12, 9, 15, 45))

    def test_manual_time_adjustment_rejects_invalid_time(self):
        clock_type = require_clock_api("AdjustableClock")
        clock = clock_type()

        with self.assertRaises(ValueError):
            clock.set_adjusted_time("25:61:00", system_now=datetime(2026, 10, 12))


class NtpResponseTests(unittest.TestCase):
    @staticmethod
    def response_packet(network_utc):
        packet = bytearray(48)
        packet[0] = 0x24  # LI=0, NTP version 4, server response mode.
        packet[1] = 1  # Primary reference.
        unix_seconds = network_utc.timestamp()
        ntp_seconds = int(unix_seconds) + 2_208_988_800
        fraction = round((unix_seconds % 1) * (1 << 32))
        struct.pack_into("!II", packet, 40, ntp_seconds, fraction)
        return bytes(packet)

    def test_parses_transmit_timestamp_to_local_naive_datetime(self):
        parse_ntp_response = require_clock_api("parse_ntp_response")
        network_utc = datetime(2026, 10, 12, 8, 20, 30, 250000, tzinfo=timezone.utc)

        parsed = parse_ntp_response(self.response_packet(network_utc))

        expected = datetime.fromtimestamp(network_utc.timestamp())
        self.assertIsNone(parsed.tzinfo)
        self.assertLessEqual(abs((parsed - expected).total_seconds()), 0.00001)

    def test_rejects_packet_with_invalid_length(self):
        parse_ntp_response = require_clock_api("parse_ntp_response")

        with self.assertRaises(ValueError):
            parse_ntp_response(bytes(47))

    def test_rejects_non_server_response(self):
        parse_ntp_response = require_clock_api("parse_ntp_response")
        packet = bytearray(self.response_packet(datetime(2026, 10, 12, tzinfo=timezone.utc)))
        packet[0] = 0x23  # NTP client mode, not a server response.

        with self.assertRaises(ValueError):
            parse_ntp_response(bytes(packet))

    def test_query_uses_four_timestamps_and_validates_origin(self):
        t1 = 1_800_000_000.0
        t2 = t1 + 2.0
        t3 = t1 + 2.1
        t4 = t1 + 0.2
        packet = bytearray(48)
        packet[0] = 0x24
        packet[1] = 1
        packet[24:32] = app._encode_ntp_timestamp(t1)
        packet[32:40] = app._encode_ntp_timestamp(t2)
        packet[40:48] = app._encode_ntp_timestamp(t3)

        client = MagicMock()
        client.__enter__.return_value = client
        client.recvfrom.return_value = (bytes(packet), ("127.0.0.1", 123))

        with (
            patch("app.socket.getaddrinfo", return_value=[(2, 2, 17, "", ("127.0.0.1", 123))]),
            patch("app.socket.socket", return_value=client),
            patch("app.time.time", side_effect=[t1, t4]),
            patch("app.time.monotonic", side_effect=[10.0, 10.1]),
        ):
            received = app.query_ntp_time("time.example")

        self.assertAlmostEqual(received.timestamp(), t1 + 2.15, places=3)
        self.assertEqual(client.sendto.call_args.args[0][40:48], packet[24:32])

    def test_query_rejects_response_for_another_request(self):
        packet = bytearray(self.response_packet(datetime(2026, 10, 12, tzinfo=timezone.utc)))
        packet[24:32] = app._encode_ntp_timestamp(1_800_000_000)
        client = MagicMock()
        client.__enter__.return_value = client
        client.recvfrom.return_value = (bytes(packet), ("127.0.0.1", 123))

        with (
            patch("app.socket.getaddrinfo", return_value=[(2, 2, 17, "", ("127.0.0.1", 123))]),
            patch("app.socket.socket", return_value=client),
            patch("app.time.time", side_effect=[1_800_000_000, 1_800_000_000.1]),
            patch("app.time.monotonic", side_effect=[10.0, 10.1]),
        ):
            with self.assertRaises(ValueError):
                app.query_ntp_time("time.example")


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

    def test_duration_is_formatted_for_minutes_and_hours(self):
        self.assertEqual(format_duration(65.4), "01:05")
        self.assertEqual(format_duration(3661), "1:01:01")
        self.assertEqual(format_duration(None), "--:--")


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


class NextAlarmTests(unittest.TestCase):
    def test_returns_next_alarm_today(self):
        alarms = [Alarm("Desayuno", ["Mon"], "08:30:00", "08:35:00", "alarm.mp3")]
        result = next_alarm_occurrence(alarms, datetime(2026, 10, 12, 8, 0, 0))
        self.assertIsNotNone(result)
        occurrence, alarm = result
        self.assertEqual(occurrence, datetime(2026, 10, 12, 8, 30, 0))
        self.assertEqual(alarm.name, "Desayuno")

    def test_skips_disabled_and_past_alarms(self):
        alarms = [
            Alarm("Pasada", ["Mon"], "07:00:00", "07:05:00", "a.mp3"),
            Alarm("Desactivada", ["Mon"], "09:00:00", "09:05:00", "b.mp3", enabled=False),
            Alarm("Mañana", ["Tue"], "06:00:00", "06:05:00", "c.mp3"),
        ]
        result = next_alarm_occurrence(alarms, datetime(2026, 10, 12, 8, 0, 0))
        self.assertEqual(result[0], datetime(2026, 10, 13, 6, 0, 0))
        self.assertEqual(result[1].name, "Mañana")

    def test_tray_text_contains_time_and_description(self):
        alarms = [Alarm("Reunión", ["Mon"], "09:15:00", "09:20:00", "a.mp3")]
        text = tray_status_text(alarms, datetime(2026, 10, 12, 8, 0, 0))
        self.assertIn("09:15:00", text)
        self.assertIn("Reunión", text)


class SingleInstanceTests(unittest.TestCase):
    @patch("app.socket.socket")
    def test_first_instance_acquires_listener(self, socket_factory):
        server = socket_factory.return_value
        server.getsockname.return_value = ("127.0.0.1", 43827)
        guard = SingleInstanceGuard()
        self.assertTrue(guard.acquire())
        server.bind.assert_called_once_with(("127.0.0.1", 43827))
        server.listen.assert_called_once_with(4)

    @patch("app.socket.socket")
    def test_second_instance_cannot_acquire_listener(self, socket_factory):
        socket_factory.return_value.bind.side_effect = OSError("already in use")
        self.assertFalse(SingleInstanceGuard().acquire())

    def test_activation_request_is_received(self):
        connection = MagicMock()
        guard = SingleInstanceGuard()
        guard.server = MagicMock()
        guard.server.accept.return_value = (connection, ("127.0.0.1", 12345))
        self.assertTrue(guard.activation_requested())
        connection.recv.assert_called_once_with(64)


if __name__ == "__main__":
    unittest.main()
