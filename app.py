import json
import queue
import shutil
import socket
import struct
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import pygame
except ImportError:  # handled in UI startup
    pygame = None

try:
    import pystray
    from PIL import Image
except ImportError:  # handled in UI startup
    pystray = Image = None

try:
    from mutagen.easyid3 import EasyID3
    from mutagen.mp3 import MP3
except ImportError:  # handled by falling back to the file name
    EasyID3 = None
    MP3 = None

try:
    import objc
    from AppKit import (
        NSApplication,
        NSApplicationActivationPolicyAccessory,
        NSImage,
        NSMenu,
        NSMenuItem,
        NSStatusBar,
        NSVariableStatusItemLength,
    )
    from Foundation import NSObject
except ImportError:  # optional outside macOS
    objc = None
    NSApplication = NSApplicationActivationPolicyAccessory = NSImage = None
    NSMenu = NSMenuItem = NSStatusBar = NSVariableStatusItemLength = NSObject = None

APP_DIR = Path.home() / ".multi_alarm_clock"
SOURCE_DIR = Path(__file__).resolve().parent
if getattr(sys, "frozen", False):
    RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", SOURCE_DIR))
    SOUNDS_DIR = APP_DIR / "sonidos"
else:
    RESOURCE_DIR = SOURCE_DIR
    SOUNDS_DIR = SOURCE_DIR / "sonidos"
BUNDLED_SOUNDS_DIR = RESOURCE_DIR / "sonidos"
ICON_FILE = RESOURCE_DIR / "assets" / "app-icon.png"
LEGACY_SOUNDS_DIR = APP_DIR / "sounds"
ALARMS_FILE = APP_DIR / "alarms.json"
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DAY_LABELS = {
    "Mon": "Lunes",
    "Tue": "Martes",
    "Wed": "Miércoles",
    "Thu": "Jueves",
    "Fri": "Viernes",
    "Sat": "Sábado",
    "Sun": "Domingo",
}
SINGLE_INSTANCE_PORT = 43827
NTP_SERVER = "pool.ntp.org"
NTP_TIMEOUT_SECONDS = 3.0
CLOCK_SKEW_THRESHOLD_SECONDS = 5.0
NTP_EPOCH_DELTA = 2_208_988_800


class AdjustableClock:
    """A process-local clock offset; it never changes the operating-system clock."""

    def __init__(self, offset_seconds: float = 0.0):
        self.offset_seconds = float(offset_seconds)

    def now(self, system_now: datetime | None = None) -> datetime:
        return (system_now or datetime.now()) + timedelta(seconds=self.offset_seconds)

    def set_from_network(
        self, network_now: datetime, system_now: datetime | None = None
    ) -> None:
        self.offset_seconds = (network_now - (system_now or datetime.now())).total_seconds()

    def set_adjusted_time(
        self, value: str, system_now: datetime | None = None
    ) -> None:
        try:
            adjusted_time = datetime.strptime(value.strip(), "%H:%M:%S").time()
        except (TypeError, ValueError) as exc:
            raise ValueError("Introduce la hora en formato HH:MM:SS.") from exc
        if adjusted_time.tzinfo is not None:
            raise ValueError("La hora no debe incluir zona horaria.")
        current = system_now or datetime.now()
        target = current.replace(
            hour=adjusted_time.hour,
            minute=adjusted_time.minute,
            second=adjusted_time.second,
            microsecond=adjusted_time.microsecond,
        )
        self.offset_seconds = (target - current).total_seconds()


def parse_ntp_response(packet: bytes) -> datetime:
    """Validate an NTP server reply and return its transmit time in local time."""
    if len(packet) < 48:
        raise ValueError("La respuesta NTP está incompleta.")
    leap_indicator = packet[0] >> 6
    mode = packet[0] & 0b111
    stratum = packet[1]
    if leap_indicator == 3 or stratum == 0 or stratum > 15:
        raise ValueError("El servidor NTP no está sincronizado.")
    if mode != 4:
        raise ValueError("La respuesta recibida no procede de un servidor NTP.")
    seconds, fraction = struct.unpack("!II", packet[40:48])
    if seconds == 0:
        raise ValueError("La respuesta NTP no contiene una hora válida.")
    unix_timestamp = seconds - NTP_EPOCH_DELTA + fraction / 2**32
    return datetime.fromtimestamp(unix_timestamp)


def _ntp_timestamp_seconds(packet: bytes, offset: int) -> float:
    seconds, fraction = struct.unpack("!II", packet[offset : offset + 8])
    if seconds == 0 and fraction == 0:
        raise ValueError("La respuesta NTP no contiene una hora válida.")
    return seconds - NTP_EPOCH_DELTA + fraction / 2**32


def _encode_ntp_timestamp(unix_timestamp: float) -> bytes:
    ntp_timestamp = unix_timestamp + NTP_EPOCH_DELTA
    seconds = int(ntp_timestamp)
    fraction = int((ntp_timestamp - seconds) * 2**32)
    return struct.pack("!II", seconds, fraction)


def query_ntp_time(
    server: str = NTP_SERVER, timeout: float = NTP_TIMEOUT_SECONDS
) -> datetime:
    """Fetch verified time over UDP without changing the system clock."""
    request = bytearray(48)
    request[0] = 0b00_011_011  # LI=0, VN=3, client mode
    addresses = socket.getaddrinfo(server, 123, type=socket.SOCK_DGRAM)
    last_error: OSError | ValueError | None = None
    deadline = time.monotonic() + timeout
    for family, socktype, protocol, _canonical_name, address in addresses:
        try:
            with socket.socket(family, socktype, protocol) as client:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise socket.timeout("Se agotó el tiempo de espera de NTP.")
                client.settimeout(remaining)
                t1 = time.time()
                request[40:48] = _encode_ntp_timestamp(t1)
                request_origin = bytes(request[40:48])
                client.sendto(request, address)
                response, _ = client.recvfrom(512)
            t4 = time.time()
            parse_ntp_response(response)
            if response[24:32] != request_origin:
                raise ValueError("La respuesta NTP no coincide con la solicitud enviada.")
            t2 = _ntp_timestamp_seconds(response, 32)
            t3 = _ntp_timestamp_seconds(response, 40)
            offset = ((t2 - t1) + (t3 - t4)) / 2
            return datetime.fromtimestamp(t4 + offset)
        except (OSError, ValueError) as exc:
            last_error = exc
    if last_error:
        raise last_error
    raise OSError("No se han encontrado direcciones para el servidor NTP.")


if NSObject is not None:
    class MacTrayIcon(NSObject):
        """Native macOS menu-bar icon backed directly by NSStatusBar."""

        @objc.python_method
        def configure(self, icon_path: Path, title: str, commands: queue.SimpleQueue[str]):
            self.commands = commands
            self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(
                NSVariableStatusItemLength
            )
            image = NSImage.alloc().initWithContentsOfFile_(str(icon_path))
            image.setSize_((18, 18))
            image.setTemplate_(False)
            button = self.status_item.button()
            button.setImage_(image)
            button.setToolTip_(title)

            menu = NSMenu.alloc().init()
            show_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Mostrar ventana", "showWindow:", ""
            )
            show_item.setTarget_(self)
            menu.addItem_(show_item)
            settings_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Biblioteca de sonidos", "openSettings:", ""
            )
            settings_item.setTarget_(self)
            menu.addItem_(settings_item)
            clock_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Ajustar hora", "adjustClock:", ""
            )
            clock_item.setTarget_(self)
            menu.addItem_(clock_item)
            menu.addItem_(NSMenuItem.separatorItem())
            exit_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                "Salir", "quitApplication:", ""
            )
            exit_item.setTarget_(self)
            menu.addItem_(exit_item)
            self.status_item.setMenu_(menu)
            self.menu = menu
            return self

        def showWindow_(self, _sender):
            self.commands.put("show")

        def openSettings_(self, _sender):
            self.commands.put("settings")

        def adjustClock_(self, _sender):
            self.commands.put("clock")

        def quitApplication_(self, _sender):
            self.commands.put("exit")

        @property
        @objc.python_method
        def title(self):
            return self.status_item.button().toolTip()

        @title.setter
        @objc.python_method
        def title(self, value):
            self.status_item.button().setToolTip_(value)

        @objc.python_method
        def stop(self):
            if getattr(self, "status_item", None) is not None:
                NSStatusBar.systemStatusBar().removeStatusItem_(self.status_item)
                self.status_item = None
else:
    MacTrayIcon = None


class SingleInstanceGuard:
    """Keep one application instance and notify it when another is launched."""

    def __init__(self, port: int = SINGLE_INSTANCE_PORT):
        self.port = port
        self.server: socket.socket | None = None

    def acquire(self) -> bool:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            server.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            server.bind(("127.0.0.1", self.port))
            server.listen(4)
            server.setblocking(False)
        except OSError:
            server.close()
            return False
        self.server = server
        self.port = server.getsockname()[1]
        return True

    def notify_existing(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=0.5) as connection:
                connection.sendall(b"ACTIVATE")
            return True
        except OSError:
            return False

    def activation_requested(self) -> bool:
        if self.server is None:
            return False
        try:
            connection, _address = self.server.accept()
        except BlockingIOError:
            return False
        with connection:
            connection.recv(64)
        return True

    def close(self):
        if self.server is not None:
            self.server.close()
            self.server = None


def normalize_time(value: str, *, ceil: bool) -> str:
    """Normalize colon-separated or compact time input."""
    raw = value.strip()
    if raw.isdigit() and ":" not in raw and 3 <= len(raw) <= 6:
        if len(raw) <= 4:
            parts = [raw[:-2], raw[-2:]]
        else:
            parts = [raw[:-4], raw[-4:-2], raw[-2:]]
    else:
        parts = [part.strip() for part in raw.split(":")]
    if not 1 <= len(parts) <= 3 or any(not part.isdigit() for part in parts):
        raise ValueError("Usa una hora válida: HH, HHMM, HHMMSS, HH:MM o HH:MM:SS.")

    numbers = [int(part) for part in parts]
    hour = numbers[0]
    minute = numbers[1] if len(numbers) >= 2 else (59 if ceil else 0)
    second = numbers[2] if len(numbers) == 3 else (59 if ceil else 0)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59 or not 0 <= second <= 59:
        raise ValueError("La hora debe estar comprendida entre 00:00:00 y 23:59:59.")
    return f"{hour:02d}:{minute:02d}:{second:02d}"


def sound_title(path: Path) -> str:
    """Return the MP3 title tag, falling back to a readable file stem."""
    if EasyID3:
        try:
            titles = EasyID3(path).get("title", [])
            if titles and titles[0].strip():
                return titles[0].strip()
        except Exception:
            pass
    return path.stem.replace("_", " ").replace("-", " ").strip().title()


def sound_label(path: Path) -> str:
    return f"{sound_title(path)} ({path.name})"


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "--:--"
    total_seconds = max(0, round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def sound_duration(path: Path) -> float | None:
    if MP3 is None:
        return None
    try:
        return MP3(path).info.length
    except Exception:
        return None


def alarm_names_using_sound(alarms: list["Alarm"], sound_path: Path) -> list[str]:
    """Return alarm names that reference a sound file."""
    return [alarm.name for alarm in alarms if Path(alarm.sound).name == sound_path.name]


@dataclass
class Alarm:
    name: str
    days: list[str]
    start: str
    end: str
    sound: str
    volume: int = 100
    enabled: bool = True
    last_started_key: str | None = None

    @property
    def end_time(self) -> dtime:
        return dtime.fromisoformat(self.end)

    @property
    def start_time(self) -> dtime:
        return dtime.fromisoformat(self.start)


def next_alarm_occurrence(alarms: list[Alarm], now: datetime) -> tuple[datetime, Alarm] | None:
    """Return the next enabled alarm start in the coming seven days."""
    candidates: list[tuple[datetime, Alarm]] = []
    for days_ahead in range(8):
        date = (now + timedelta(days=days_ahead)).date()
        day = DAYS[date.weekday()]
        for alarm in alarms:
            if not alarm.enabled or day not in alarm.days:
                continue
            occurrence = datetime.combine(date, alarm.start_time)
            if occurrence > now:
                candidates.append((occurrence, alarm))
    return min(candidates, key=lambda item: item[0]) if candidates else None


def tray_status_text(alarms: list[Alarm], now: datetime) -> str:
    next_alarm = next_alarm_occurrence(alarms, now)
    if next_alarm is None:
        return "Programador de alarmas — No hay alarmas programadas"
    occurrence, alarm = next_alarm
    day_label = DAY_LABELS[DAYS[occurrence.weekday()]].lower()
    return (
        f"Próxima alarma: {day_label} {occurrence:%d/%m} "
        f"a las {occurrence:%H:%M:%S} — {alarm.name}"
    )


def overlapping_alarm_indices(alarms: list[Alarm]) -> set[int]:
    """Return enabled alarms whose intervals overlap on a shared weekday."""
    overlapping: set[int] = set()
    for index, alarm in enumerate(alarms):
        if not alarm.enabled:
            continue
        for other_index in range(index + 1, len(alarms)):
            other = alarms[other_index]
            if not other.enabled or not set(alarm.days).intersection(other.days):
                continue
            if alarm.start_time < other.end_time and other.start_time < alarm.end_time:
                overlapping.update((index, other_index))
    return overlapping


def alarm_issues(alarms: list[Alarm]) -> dict[int, list[str]]:
    """Return human-readable issues for each alarm row."""
    issues = {index: [] for index in range(len(alarms))}
    for index, alarm in enumerate(alarms):
        if not alarm.enabled:
            continue
        for other_index in range(index + 1, len(alarms)):
            other = alarms[other_index]
            if not other.enabled or not set(alarm.days).intersection(other.days):
                continue
            if alarm.start_time < other.end_time and other.start_time < alarm.end_time:
                issues[index].append(f"Se solapa con «{other.name}».")
                issues[other_index].append(f"Se solapa con «{alarm.name}».")
    for index, alarm in enumerate(alarms):
        sound_path = Path(alarm.sound)
        if not sound_path.exists():
            issues[index].append(f"No se encuentra el fichero de sonido «{sound_path.name}».")
    return {index: messages for index, messages in issues.items() if messages}


class AlarmStore:
    def __init__(self):
        APP_DIR.mkdir(exist_ok=True)
        SOUNDS_DIR.mkdir(exist_ok=True)
        self._migrate_legacy_sounds()
        self._install_bundled_sounds()
        self._clean_empty_id3_comments()
        self.alarms: list[Alarm] = []
        self.load()

    def _migrate_legacy_sounds(self):
        if not LEGACY_SOUNDS_DIR.exists():
            return
        for source in LEGACY_SOUNDS_DIR.glob("*.mp3"):
            target = SOUNDS_DIR / source.name
            if not target.exists():
                shutil.move(str(source), str(target))

    def _install_bundled_sounds(self):
        if BUNDLED_SOUNDS_DIR.resolve() == SOUNDS_DIR.resolve() or not BUNDLED_SOUNDS_DIR.exists():
            return
        for source in BUNDLED_SOUNDS_DIR.glob("*.mp3"):
            target = SOUNDS_DIR / source.name
            if not target.exists():
                shutil.copy2(source, target)

    def _clean_empty_id3_comments(self):
        """Remove empty ID3 comments that make mpg123 print a warning."""
        if EasyID3 is None:
            return
        for sound_path in SOUNDS_DIR.glob("*.mp3"):
            try:
                tags = EasyID3(sound_path)
                comments = tags.get("comment", [])
                if comments and not any(comment.strip() for comment in comments):
                    del tags["comment"]
                    tags.save()
            except Exception:
                # A malformed or unsupported tag must not prevent the app from starting.
                continue

    def load(self):
        if not ALARMS_FILE.exists():
            self.alarms = []
            return
        data = json.loads(ALARMS_FILE.read_text(encoding="utf-8"))
        self.alarms = []
        for item in data:
            item["start"] = normalize_time(item["start"], ceil=False)
            item["end"] = normalize_time(item["end"], ceil=True)
            item["volume"] = int(item.get("volume", 100))
            catalog_sound = SOUNDS_DIR / Path(item["sound"]).name
            if catalog_sound.exists():
                item["sound"] = str(catalog_sound)
            self.alarms.append(Alarm(**item))

    def save(self):
        ALARMS_FILE.write_text(
            json.dumps([asdict(alarm) for alarm in self.alarms], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def sounds(self) -> list[Path]:
        return sorted(SOUNDS_DIR.glob("*.mp3"))

    def add_sound(self, source: Path) -> Path:
        if source.parent.resolve() == SOUNDS_DIR.resolve():
            return source
        target = SOUNDS_DIR / source.name
        if target.exists():
            stem, suffix = source.stem, source.suffix
            i = 2
            while target.exists():
                target = SOUNDS_DIR / f"{stem}-{i}{suffix}"
                i += 1
        shutil.move(str(source), str(target))
        return target


class AudioPlayer:
    def __init__(self):
        self.current_alarms: dict[int, Alarm] = {}
        self.channels: dict[int, object] = {}
        self.preview_channel = None
        self.preview_sound = None
        if pygame:
            pygame.mixer.init()
            pygame.mixer.set_num_channels(max(8, pygame.mixer.get_num_channels()))
            pygame.mixer.set_reserved(1)

    def play_loops(self, alarms: list[Alarm]):
        if not pygame:
            return
        requested = {id(alarm): alarm for alarm in alarms}
        for alarm_id in set(self.current_alarms) - set(requested):
            self.channels[alarm_id].stop()
            del self.channels[alarm_id]
        for alarm_id, alarm in requested.items():
            if alarm_id in self.current_alarms:
                self.channels[alarm_id].set_volume(alarm.volume / 100)
                continue
            sound = pygame.mixer.Sound(alarm.sound)
            channel = pygame.mixer.find_channel(True)
            channel.set_volume(alarm.volume / 100)
            channel.play(sound, loops=-1)
            self.channels[alarm_id] = channel
        self.current_alarms = requested

    @property
    def is_playing(self) -> bool:
        return bool(self.current_alarms)

    @property
    def is_previewing(self) -> bool:
        return self.preview_channel is not None and self.preview_channel.get_busy()

    def preview(self, sound_path: Path):
        if not pygame:
            return
        self.stop_preview()
        self.preview_sound = pygame.mixer.Sound(str(sound_path))
        self.preview_channel = pygame.mixer.Channel(0)
        self.preview_channel.set_volume(1.0)
        self.preview_channel.play(self.preview_sound)

    def stop_preview(self):
        if self.preview_channel is not None:
            self.preview_channel.stop()
        self.preview_channel = None
        self.preview_sound = None

    def stop(self):
        if not pygame:
            return
        self.stop_preview()
        for channel in self.channels.values():
            channel.stop()
        self.channels.clear()
        self.current_alarms.clear()


class TooltipManager:
    """Own one delayed, square-corner tooltip inside its parent window."""

    DELAY_MS = 1000

    def __init__(self, root, can_show):
        self.root = root
        self.can_show = can_show
        self.widget = None
        self.owner = None
        self.key = None
        self._check_id = None
        self._show_id = None
        self.pending = None

    def bind_owner(self, owner):
        def owner_unavailable(event):
            if event.widget is owner:
                self.hide(owner)

        owner.bind("<Unmap>", owner_unavailable, add="+")
        owner.bind("<Destroy>", owner_unavailable, add="+")
        owner.bind("<FocusOut>", lambda _event: self.hide(owner), add="+")

    def show(self, owner, key, text, x, y):
        if not text or not self.can_show(owner):
            self.hide(owner)
            return
        if self.widget is not None and self.owner is owner and self.key == key:
            return
        if self.pending is not None:
            pending_owner, pending_key, _, _, _ = self.pending
            if pending_owner is owner and pending_key == key:
                self.pending = (owner, key, text, x, y)
                return
        self.hide()
        # A synchronous Destroy callback may already have scheduled a newer popup.
        if self.widget is not None or self.pending is not None or not self.can_show(owner):
            return
        self.pending = (owner, key, text, x, y)
        self._show_id = self.root.after(self.DELAY_MS, self._show_pending)

    def _show_pending(self):
        self._show_id = None
        pending, self.pending = self.pending, None
        if pending is None:
            return
        owner, key, text, x, y = pending
        if not text or not self.can_show(owner):
            return
        # A native Toplevel keeps macOS's rounded window corners even when its
        # child label has a square border. Use an ordinary child widget instead.
        tooltip = tk.Frame(owner, background="#000000", borderwidth=0, highlightthickness=0)
        # Publish ownership BEFORE any operation that might dispatch Tk events.
        self.widget, self.owner, self.key = tooltip, owner, key

        def destroyed(event):
            if event.widget is tooltip and self.widget is tooltip:
                self._forget()

        try:
            tooltip.bind("<Destroy>", destroyed, add="+")
            label = tk.Label(
                tooltip, text=text, justify="left", background="#fff8c6",
                foreground="#000000", relief="flat", borderwidth=0,
                highlightthickness=1, highlightbackground="#000000",
                highlightcolor="#000000",
                padx=10, pady=3, wraplength=max(200, owner.winfo_screenwidth() - 40),
            )
            label.pack()
            # Requested sizes are synchronous; update_idletasks here can
            # re-enter a hover callback and orphan the tooltip being constructed.
            width, height = label.winfo_reqwidth(), label.winfo_reqheight()
            owner_x, owner_y = owner.winfo_rootx(), owner.winfo_rooty()
            owner_width, owner_height = owner.winfo_width(), owner.winfo_height()
            edge_margin = 10

            max_x = max(edge_margin, owner_width - width - edge_margin)
            max_y = max(edge_margin, owner_height - height - edge_margin)

            x = max(edge_margin, min(x - owner_x + 12, max_x))
            y = max(edge_margin, min(y - owner_y + 12, max_y))
            tooltip.place(x=x, y=y)

            tooltip.lift()
            if self.widget is not tooltip or not self.can_show(owner):
                if self.widget is tooltip:
                    self.hide()
                return
            if self.widget is tooltip:
                self._check_id = self.root.after(100, self._check_owner)
        except tk.TclError:
            # The owner may have been destroyed during a Tk callback.
            if self.widget is tooltip:
                self.hide()

    def _check_owner(self):
        self._check_id = None
        if self.widget is None:
            return
        if not self.can_show(self.owner):
            self.hide()
        else:
            self._check_id = self.root.after(100, self._check_owner)

    def _forget(self):
        self.widget = self.owner = self.key = None
        check_id, self._check_id = self._check_id, None
        show_id, self._show_id = self._show_id, None
        self.pending = None
        if check_id is not None:
            self.root.after_cancel(check_id)
        if show_id is not None:
            self.root.after_cancel(show_id)

    def hide(self, owner=None):
        if owner is not None:
            pending_owner = self.pending[0] if self.pending is not None else None
            if self.owner is not owner and pending_owner is not owner:
                return
        tooltip = self.widget
        # Destroy/Unmap callbacks must see an empty state, never a stale cell.
        self._forget()
        if tooltip is not None:
            try:
                tooltip.destroy()
            except tk.TclError:
                pass


class AlarmApp(tk.Tk):
    def __init__(self, single_instance_guard: SingleInstanceGuard | None = None):
        super().__init__()
        self.single_instance_guard = single_instance_guard
        self.title("Programador de alarmas")
        if ICON_FILE.exists():
            self.app_icon = tk.PhotoImage(file=ICON_FILE)
            self.iconphoto(True, self.app_icon)
            if NSApplication and NSImage:
                dock_icon = NSImage.alloc().initWithContentsOfFile_(str(ICON_FILE))
                if dock_icon:
                    NSApplication.sharedApplication().setApplicationIconImage_(dock_icon)
        if sys.platform == "darwin" and NSApplication:
            NSApplication.sharedApplication().setActivationPolicy_(
                NSApplicationActivationPolicyAccessory
            )
        self.store = AlarmStore()
        self.player = AudioPlayer() if pygame else None
        self.selected_index: int | None = None
        self.form_mode = "idle"
        self.updating_form = False
        self.running = True
        self.window_hidden = False
        self.tray_commands: queue.SimpleQueue[str] = queue.SimpleQueue()
        self.tray_icon = None
        self.last_tray_text = ""
        self.silenced_occurrences: set[str] = set()
        self.clock = AdjustableClock()
        self.clock_manually_set = False
        self.ntp_results: queue.SimpleQueue[tuple[bool, datetime | str]] = queue.SimpleQueue()
        self.ntp_sync_finished = False
        self.clock_dialog: tk.Toplevel | None = None
        self.day_vars = {day: tk.BooleanVar() for day in DAYS}
        self.enabled_var = tk.BooleanVar(value=True)
        self.name_var = tk.StringVar()
        self.start_var = tk.StringVar(value="08:00:00")
        self.end_var = tk.StringVar(value="08:05:00")
        self.sound_var = tk.StringVar()
        self.volume_var = tk.IntVar(value=100)
        self.status_var = tk.StringVar(value="Listo")
        self.sound_library_window: tk.Toplevel | None = None
        self.tooltips = TooltipManager(self, self.can_show_tooltip)
        self.tooltips.bind_owner(self)

        self.build_ui()
        self.bind("<Control-t>", self.create_test_alarm)
        self.build_menu()
        self.refresh_sounds()
        self.refresh_alarms()
        self.setup_form_tracking()
        self.fit_window_to_content()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.bind("<Unmap>", self.on_window_unmap, add="+")
        self.start_tray_icon()
        self.after(200, self.poll_tray_commands)
        self.after(200, self.poll_ntp_result)
        self.after(200, self.scheduler_tick)
        if self.single_instance_guard:
            self.after(200, self.poll_single_instance)

        if pygame is None:
            messagebox.showwarning(
                "Falta dependencia",
                "Instala pygame-ce para reproducir MP3:\n\npython -m pip install -r requirements.txt",
            )

        self.start_ntp_sync()

        if sys.platform != "darwin" and (pystray is None or Image is None):
            messagebox.showwarning(
                "Falta dependencia",
                "No se puede crear el icono de la barra de tareas.\n\n"
                "Instala las dependencias con:\npython -m pip install -r requirements.txt",
            )

    def start_tray_icon(self):
        if not ICON_FILE.exists():
            return

        self.last_tray_text = tray_status_text(self.store.alarms, self.clock.now())
        if sys.platform == "darwin" and MacTrayIcon is not None:
            self.tray_icon = MacTrayIcon.alloc().init().configure(
                ICON_FILE, self.last_tray_text, self.tray_commands
            )
            return
        if pystray is None or Image is None:
            return

        def request_show(_icon=None, _item=None):
            self.tray_commands.put("show")

        def request_settings(_icon=None, _item=None):
            self.tray_commands.put("settings")

        def request_clock(_icon=None, _item=None):
            self.tray_commands.put("clock")

        def request_exit(_icon=None, _item=None):
            self.tray_commands.put("exit")

        show_options = {"default": True} if pystray.Icon.HAS_DEFAULT_ACTION else {}
        menu = pystray.Menu(
            pystray.MenuItem("Mostrar ventana", request_show, **show_options),
            pystray.MenuItem("Biblioteca de sonidos", request_settings),
            pystray.MenuItem("Ajustar hora", request_clock),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Salir", request_exit),
        )
        self.tray_icon = pystray.Icon(
            "programador-de-alarmas",
            Image.open(ICON_FILE),
            self.last_tray_text,
            menu,
        )
        self.tray_icon.run_detached()

    def poll_tray_commands(self):
        if not self.running:
            return
        while True:
            try:
                command = self.tray_commands.get_nowait()
            except queue.Empty:
                break
            if command == "show":
                self.restore_window()
            elif command == "settings":
                self.restore_window()
                self.after_idle(self.open_sound_library)
            elif command == "clock":
                self.restore_window()
                self.after_idle(self.open_clock_settings)
            elif command == "exit":
                self.quit_application()
                return
        self.after(200, self.poll_tray_commands)

    def update_tray_status(self, now: datetime):
        if self.tray_icon is None:
            return
        text = tray_status_text(self.store.alarms, now)
        if text != self.last_tray_text:
            self.tray_icon.title = text
            self.last_tray_text = text

    def build_menu(self):
        menu_bar = tk.Menu(self)
        if sys.platform == "darwin":
            app_menu = tk.Menu(menu_bar, name="apple", tearoff=False)
            app_menu.add_command(label="Acerca de Programador de alarmas", command=self.show_about)
            menu_bar.add_cascade(menu=app_menu)
        menu_bar.add_command(label="Biblioteca de sonidos", command=self.open_sound_library)
        menu_bar.add_command(label="Ajustar hora", command=self.open_clock_settings)
        help_menu = tk.Menu(menu_bar, tearoff=False)
        help_menu.add_command(label="Cómo usar la aplicación", command=self.show_help)
        if sys.platform != "darwin":
            help_menu.add_separator()
            help_menu.add_command(label="Acerca de", command=self.show_about)
        menu_bar.add_cascade(label="Ayuda", menu=help_menu)
        self.config(menu=menu_bar)
        if sys.platform == "darwin":
            self.tk.createcommand("tkAboutDialog", self.show_about)

    def start_ntp_sync(self):
        def synchronize():
            try:
                self.ntp_results.put((True, query_ntp_time()))
            except Exception as exc:
                self.ntp_results.put((False, str(exc)))

        threading.Thread(target=synchronize, name="ntp-clock-sync", daemon=True).start()

    def poll_ntp_result(self):
        if not self.running:
            return
        try:
            succeeded, result = self.ntp_results.get_nowait()
        except queue.Empty:
            self.after(200, self.poll_ntp_result)
            return
        if self.ntp_sync_finished:
            return
        self.ntp_sync_finished = True
        if not succeeded:
            if not self.clock_manually_set:
                self.clock.offset_seconds = 0.0
            fallback_message = (
                "Se mantendrá la hora ajustada manualmente."
                if self.clock_manually_set
                else "Las alarmas seguirán la hora del sistema. Puedes ajustar la hora "
                "manualmente desde el menú «Ajustar hora»."
            )
            messagebox.showwarning(
                "No se pudo comprobar la hora real",
                "No se ha podido conectar con el servidor horario NTP. "
                f"{fallback_message}\n\n"
                f"Detalle: {result}",
                parent=self,
            )
            return

        if self.clock_manually_set:
            return

        network_now = result
        system_now = datetime.now()
        difference = (network_now - system_now).total_seconds()
        if abs(difference) >= CLOCK_SKEW_THRESHOLD_SECONDS:
            use_network_time = messagebox.askyesno(
                "Diferencia entre las horas",
                "La hora del sistema y la hora real de la red no coinciden.\n\n"
                f"Hora del sistema: {system_now:%H:%M:%S}\n"
                f"Hora real (NTP): {network_now:%H:%M:%S}\n\n"
                "¿Quieres que las alarmas sigan la hora real de la red?\n"
                "Si eliges No, seguirán la hora del sistema.",
                parent=self,
            )
            if not use_network_time:
                self.clock.offset_seconds = 0.0
        else:
            self.clock.set_from_network(network_now, system_now)
        if abs(difference) >= CLOCK_SKEW_THRESHOLD_SECONDS and use_network_time:
            self.clock.set_from_network(network_now, system_now)
        self.update_tray_status(self.clock.now())
        self.status_var.set("Hora real de red aplicada" if self.clock.offset_seconds else "Hora del sistema aplicada")

    def open_clock_settings(self):
        if self.clock_dialog is not None:
            try:
                if self.clock_dialog.winfo_exists():
                    self.clock_dialog.deiconify()
                    self.clock_dialog.lift()
                    self.clock_dialog.focus_force()
                    return
            except tk.TclError:
                pass

        window = tk.Toplevel(self)
        self.clock_dialog = window
        window.title("Ajustar hora")
        window.transient(self)
        window.resizable(False, False)
        content = ttk.Frame(window, padding=14)
        content.pack(fill="both", expand=True)

        system_time_var = tk.StringVar()
        adjusted_time_var = tk.StringVar()
        ttk.Label(content, text="Hora del sistema").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Label(content, textvariable=system_time_var).grid(row=0, column=1, sticky="w", padx=(12, 0))
        ttk.Label(content, text="Hora ajustada").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Label(content, textvariable=adjusted_time_var).grid(row=1, column=1, sticky="w", padx=(12, 0))
        ttk.Label(content, text="Nueva hora ajustada (HH:MM:SS)").grid(
            row=2, column=0, sticky="w", pady=(12, 4)
        )
        adjusted_entry_var = tk.StringVar(value=self.clock.now().strftime("%H:%M:%S"))
        entry = ttk.Entry(content, textvariable=adjusted_entry_var, width=12)
        entry.grid(row=2, column=1, sticky="w", padx=(12, 0), pady=(12, 4))

        def close_dialog():
            if self.clock_dialog is window:
                self.clock_dialog = None
            window.destroy()

        def save_adjusted_time():
            try:
                self.clock.set_adjusted_time(adjusted_entry_var.get())
            except ValueError as exc:
                messagebox.showerror("Formato de hora no válido", str(exc), parent=window)
                entry.focus_set()
                entry.selection_range(0, tk.END)
                return
            self.clock_manually_set = True
            self.update_tray_status(self.clock.now())
            self.status_var.set("Hora ajustada manualmente")
            close_dialog()

        buttons = ttk.Frame(content)
        buttons.grid(row=3, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="Guardar", command=save_adjusted_time).pack(side="left")
        ttk.Button(buttons, text="Cancelar", command=close_dialog).pack(side="left", padx=(6, 0))

        def update_clock_labels():
            if not window.winfo_exists():
                return
            system_now = datetime.now()
            system_time_var.set(system_now.strftime("%H:%M:%S"))
            adjusted_time_var.set(self.clock.now(system_now).strftime("%H:%M:%S"))
            window.after(250, update_clock_labels)

        window.protocol("WM_DELETE_WINDOW", close_dialog)
        window.bind("<Destroy>", lambda event: setattr(self, "clock_dialog", None)
                    if event.widget is window and self.clock_dialog is window else None, add="+")
        update_clock_labels()
        window.grab_set()
        entry.focus_set()

    def show_help(self):
        messagebox.showinfo(
            "Ayuda de Programador de alarmas",
            "1. Pulsa Nueva y escribe el nombre, los días y las horas de inicio y fin.\n\n"
            "2. Selecciona un sonido y su volumen, y pulsa Guardar.\n\n"
            "3. Gestiona los MP3 desde Biblioteca de sonidos.\n\n"
            "4. Las filas naranjas indican horarios solapados y las rojas, sonidos ausentes. "
            "Sitúa el puntero sobre ⚠ para ver el problema.\n\n"
            "5. Detener sonido silencia las alarmas que estén sonando. Ctrl-T crea una alarma de prueba.",
        )

    def show_about(self):
        messagebox.showinfo(
            "Acerca de Programador de alarmas",
            "Programador de alarmas\n\n"
            "Autor: Franz Jimeno\n"
            "Copyright © 2026 Franz Jimeno\n\n"
            "Licencia Creative Commons Atribución 4.0 Internacional "
            "(CC BY 4.0)\n"
            "https://creativecommons.org/licenses/by/4.0/",
        )

    def fit_window_to_content(self):
        self.update_idletasks()
        margin = 60
        width = min(self.winfo_reqwidth(), self.winfo_screenwidth() - margin)
        height = min(self.winfo_reqheight(), self.winfo_screenheight() - margin)
        x = max((self.winfo_screenwidth() - width) // 2, 0)
        y = max((self.winfo_screenheight() - height) // 2, 0)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(width, height)

    def setup_form_tracking(self):
        variables = [
            self.enabled_var,
            self.name_var,
            self.start_var,
            self.end_var,
            self.sound_var,
            self.volume_var,
            *self.day_vars.values(),
        ]
        for variable in variables:
            variable.trace_add("write", self.on_form_changed)

    def on_form_changed(self, *_args):
        if self.updating_form:
            return
        if self.form_mode == "idle":
            self.form_mode = "new"
            self.new_button.state(["disabled"])
            self.save_button.state(["!disabled"])
            self.delete_button.state(["disabled"])
            self.cancel_button.state(["!disabled"])
            self.status_var.set("Nueva alarma")
        elif self.form_mode == "edit":
            self.form_mode = "edit_dirty"
            self.save_button.state(["!disabled"])
            self.cancel_button.state(["!disabled"])
            self.status_var.set("Alarma modificada")

    def build_ui(self):
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)

        left = ttk.LabelFrame(root, text="Alarmas", padding=8)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))

        columns = ("enabled", "name", "days", "start", "end", "volume", "sound", "alerts")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", height=18)
        for col, title, width in [
            ("enabled", "Activa", 60),
            ("name", "Nombre", 150),
            ("days", "Días", 190),
            ("start", "Inicio", 70),
            ("end", "Fin", 70),
            ("volume", "Vol.", 50),
            ("sound", "Sonido", 180),
            ("alerts", "Avisos", 55),
        ]:
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.load_selected_alarm)
        self.tree.bind("<Motion>", self.show_sound_tooltip)
        self.tree.bind("<Leave>", lambda _event: self.tooltips.hide(self))
        self.tree.bind("<ButtonPress>", lambda _event: self.tooltips.hide(self), add="+")
        self.tree.bind("<MouseWheel>", lambda _event: self.tooltips.hide(self), add="+")
        self.tree.tag_configure("missing_sound", foreground="red")
        self.tree.tag_configure("overlapping", foreground="#d97706")

        buttons = ttk.Frame(left)
        buttons.pack(fill="x", pady=(8, 0))
        self.new_button = ttk.Button(buttons, text="Nueva", command=self.start_new_alarm)
        self.new_button.pack(side="left")
        self.delete_button = ttk.Button(buttons, text="Eliminar", command=self.delete_alarm, state="disabled")
        self.delete_button.pack(side="left", padx=4)
        ttk.Button(buttons, text="↑ Subir", command=lambda: self.move_alarm(-1)).pack(side="left", padx=(12, 4))
        ttk.Button(buttons, text="↓ Bajar", command=lambda: self.move_alarm(1)).pack(side="left")
        ttk.Button(buttons, text="Detener sonido", command=self.stop_sound).pack(side="right")

        form = ttk.LabelFrame(root, text="Programación", padding=8)
        form.pack(side="right", fill="y")

        ttk.Checkbutton(form, text="Activa", variable=self.enabled_var).grid(row=0, column=0, sticky="w", columnspan=2)
        ttk.Label(form, text="Nombre").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.name_entry = ttk.Entry(form, textvariable=self.name_var, width=28)
        self.name_entry.grid(row=1, column=1, sticky="ew", pady=(8, 0))

        ttk.Label(form, text="Días").grid(row=2, column=0, sticky="nw", pady=(8, 0))
        days_box = ttk.Frame(form)
        days_box.grid(row=2, column=1, sticky="w", pady=(8, 0))
        for i, day in enumerate(DAYS):
            ttk.Checkbutton(days_box, text=DAY_LABELS[day], variable=self.day_vars[day]).grid(row=i, column=0, sticky="w")

        ttk.Label(form, text="Inicio HH:MM:SS").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.start_entry = ttk.Entry(form, textvariable=self.start_var, width=10)
        self.start_entry.grid(row=3, column=1, sticky="w", pady=(8, 0))
        self.start_entry.bind(
            "<FocusOut>",
            lambda _event: self.normalize_time_field(
                self.start_var, ceil=False, field_name="inicio", field_widget=self.start_entry
            ),
        )
        ttk.Label(form, text="Fin HH:MM:SS").grid(row=4, column=0, sticky="w", pady=(8, 0))
        self.end_entry = ttk.Entry(form, textvariable=self.end_var, width=10)
        self.end_entry.grid(row=4, column=1, sticky="w", pady=(8, 0))
        self.end_entry.bind(
            "<FocusOut>",
            lambda _event: self.normalize_time_field(
                self.end_var, ceil=True, field_name="fin", field_widget=self.end_entry
            ),
        )
        ttk.Label(form, text="Admite 0700. Inicio completa con 00; fin con 59.", foreground="#555").grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(2, 8)
        )

        ttk.Label(form, text="Sonido MP3").grid(row=6, column=0, sticky="w")
        self.sound_combo = ttk.Combobox(form, textvariable=self.sound_var, state="readonly", width=25)
        self.sound_combo.grid(row=6, column=1, sticky="ew")
        ttk.Label(form, text="Volumen").grid(row=7, column=0, sticky="w", pady=(8, 0))
        tk.Scale(
            form,
            from_=0,
            to=100,
            resolution=1,
            variable=self.volume_var,
            orient="horizontal",
            showvalue=True,
            length=180,
        ).grid(row=7, column=1, sticky="ew", pady=(8, 0))
        form_buttons = ttk.Frame(form)
        form_buttons.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        self.save_button = ttk.Button(form_buttons, text="Guardar", command=self.save_alarm, state="disabled")
        self.save_button.pack(side="left")
        self.cancel_button = ttk.Button(
            form_buttons, text="Cancelar", command=self.cancel_configuration, state="disabled"
        )
        self.cancel_button.pack(side="left", padx=(6, 0))
        ttk.Label(form, textvariable=self.status_var, foreground="#075").grid(
            row=9, column=0, columnspan=2, sticky="w", pady=(12, 0)
        )

    def normalize_time_field(self, variable: tk.StringVar, *, ceil: bool, field_name: str, field_widget):
        value = variable.get().strip()
        if not value:
            return
        try:
            variable.set(normalize_time(value, ceil=ceil))
        except ValueError as exc:
            messagebox.showerror(
                "Formato de hora no reconocido",
                f"No se reconoce el formato de la hora de {field_name}.\n\n{exc}",
            )
            field_widget.focus_set()
            field_widget.selection_range(0, tk.END)

    def refresh_sounds(self):
        self.sound_paths = self.store.sounds()
        self.sound_labels = {sound_label(path): path for path in self.sound_paths}
        self.sound_combo["values"] = list(self.sound_labels)
        if self.sound_var.get() not in self.sound_labels:
            self.sound_var.set(sound_label(self.sound_paths[0]) if self.sound_paths else "")

    def refresh_alarms(self):
        self.hide_all_tooltips()
        self.tree.delete(*self.tree.get_children())
        overlapping = overlapping_alarm_indices(self.store.alarms)
        issues = alarm_issues(self.store.alarms)
        for idx, alarm in enumerate(self.store.alarms):
            sound_path = Path(alarm.sound)
            tag = "missing_sound" if not sound_path.exists() else "overlapping" if idx in overlapping else ""
            self.tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(
                    "Sí" if alarm.enabled else "No",
                    alarm.name,
                    ", ".join(DAY_LABELS[d] for d in alarm.days),
                    alarm.start,
                    alarm.end,
                    f"{alarm.volume}%",
                    sound_title(sound_path),
                    "⚠" if idx in issues else "",
                ),
                tags=(tag,) if tag else (),
            )

    def can_show_tooltip(self, owner):
        if not self.running or self.window_hidden:
            return False
        try:
            if not self.winfo_viewable() or not owner.winfo_exists() or not owner.winfo_viewable():
                return False
            if self.state() in ("withdrawn", "iconic") or owner.state() in ("withdrawn", "iconic"):
                return False
            focused = owner.focus_displayof()
            if focused is None or focused.winfo_toplevel() is not owner:
                return False
            grab = self.grab_current()
            return grab is None or grab.winfo_toplevel() is owner
        except tk.TclError:
            return False

    def show_sound_tooltip(self, event):
        if not self.can_show_tooltip(self):
            self.tooltips.hide(self)
            return
        row_id = self.tree.identify_row(event.y)
        column_id = self.tree.identify_column(event.x)
        if not row_id or not self.tree.exists(row_id):
            self.tooltips.hide(self)
            return
        if column_id == "#8":
            self.show_problem_tooltip(event, row_id)
        elif column_id == "#7":
            alarm = self.store.alarms[int(row_id)]
            self.tooltips.show(self, (row_id, column_id), Path(alarm.sound).name,
                               event.x_root, event.y_root)
        else:
            self.tooltips.hide(self)

    def show_problem_tooltip(self, event, row_id: str):
        issues = alarm_issues(self.store.alarms).get(int(row_id), [])
        self.tooltips.show(self, (row_id, "#8"), "\n".join(issues),
                           event.x_root, event.y_root)

    def hide_all_tooltips(self, _event=None):
        self.tooltips.hide()

    def on_window_unmap(self, event):
        if event.widget is self:
            self.hide_all_tooltips()

    def selected_sound_path(self) -> str:
        selected = self.sound_var.get()
        path = self.sound_labels.get(selected)
        if path:
            return str(path)
        raise ValueError("Selecciona un sonido MP3 del catálogo.")

    def save_alarm(self):
        try:
            name = self.name_var.get().strip() or "Alarma"
            days = [day for day in DAYS if self.day_vars[day].get()]
            if not days:
                raise ValueError("Selecciona al menos un día.")
            start = normalize_time(self.start_var.get(), ceil=False)
            end = normalize_time(self.end_var.get(), ceil=True)
            alarm = Alarm(
                name=name,
                days=days,
                start=start,
                end=end,
                sound=self.selected_sound_path(),
                volume=int(self.volume_var.get()),
                enabled=self.enabled_var.get(),
            )
            if alarm.end_time <= alarm.start_time:
                raise ValueError("La hora de fin debe ser posterior a la de inicio en el mismo día.")
            if self.selected_index is None:
                self.store.alarms.append(alarm)
            else:
                self.store.alarms[self.selected_index] = alarm
            self.store.save()
            self.refresh_alarms()
            self.clear_form()
            self.status_var.set("Alarma guardada")
        except Exception as exc:
            messagebox.showerror("No se pudo guardar", str(exc))

    def load_selected_alarm(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            return
        self.updating_form = True
        self.form_mode = "edit"
        try:
            self.selected_index = int(selected[0])
            alarm = self.store.alarms[self.selected_index]
            self.enabled_var.set(alarm.enabled)
            self.name_var.set(alarm.name)
            self.start_var.set(alarm.start)
            self.end_var.set(alarm.end)
            self.sound_var.set(sound_label(Path(alarm.sound)))
            self.volume_var.set(alarm.volume)
            for day in DAYS:
                self.day_vars[day].set(day in alarm.days)
        finally:
            self.updating_form = False
        self.save_button.state(["disabled"])
        self.delete_button.state(["!disabled"])
        self.cancel_button.state(["disabled"])
        self.new_button.state(["!disabled"])

    def start_new_alarm(self):
        self.clear_form()
        self.form_mode = "new"
        self.new_button.state(["disabled"])
        self.save_button.state(["!disabled"])
        self.cancel_button.state(["!disabled"])
        self.after_idle(self.name_entry.focus_set)

    def cancel_configuration(self):
        if self.form_mode == "edit_dirty" and self.selected_index is not None:
            self.load_selected_alarm()
            self.status_var.set("Cambios cancelados")
            return
        self.clear_form()
        self.status_var.set("Configuración cancelada")

    def clear_form(self):
        self.updating_form = True
        self.form_mode = "idle"
        try:
            self.selected_index = None
            self.tree.selection_remove(self.tree.selection())
            self.enabled_var.set(True)
            self.name_var.set("")
            self.start_var.set("08:00:00")
            self.end_var.set("08:05:00")
            self.volume_var.set(100)
            for var in self.day_vars.values():
                var.set(False)
        finally:
            self.updating_form = False
        self.save_button.state(["disabled"])
        self.delete_button.state(["disabled"])
        self.cancel_button.state(["disabled"])
        self.new_button.state(["!disabled"])

    def delete_alarm(self):
        if self.selected_index is None:
            return
        del self.store.alarms[self.selected_index]
        self.store.save()
        self.refresh_alarms()
        self.clear_form()

    def open_sound_library(self):
        self.hide_all_tooltips()
        if self.sound_library_window is not None:
            try:
                if self.sound_library_window.winfo_exists():
                    self.sound_library_window.deiconify()
                    self.sound_library_window.lift()
                    self.sound_library_window.focus_force()
                    return
            except tk.TclError:
                pass
            self.sound_library_window = None
        window = tk.Toplevel(self)
        self.sound_library_window = window
        self.tooltips.bind_owner(window)
        window.title("Biblioteca de sonidos")
        window.transient(self)
        window.resizable(True, True)

        content = ttk.Frame(window, padding=12)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="Sonidos disponibles").pack(anchor="w")
        table_frame = ttk.Frame(content)
        table_frame.pack(fill="both", expand=True, pady=(6, 10))
        sound_table = ttk.Treeview(
            table_frame,
            columns=("name", "file", "duration"),
            show="headings",
            height=14,
            selectmode="browse",
        )
        sound_table.heading("name", text="Nombre")
        sound_table.heading("file", text="Fichero")
        sound_table.heading("duration", text="Duración")
        sound_table.column("name", width=220, anchor="w")
        sound_table.column("file", width=270, anchor="w")
        sound_table.column("duration", width=80, anchor="center", stretch=False)
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=sound_table.yview)
        sound_table.configure(yscrollcommand=scrollbar.set)
        sound_table.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        library_paths: list[Path] = []
        library_closing = False

        def refresh_library(selected_path: Path | None = None):
            nonlocal library_paths
            self.tooltips.hide(window)
            library_paths = self.store.sounds()
            sound_table.delete(*sound_table.get_children())
            for index, path in enumerate(library_paths):
                sound_table.insert(
                    "",
                    "end",
                    iid=str(index),
                    values=(sound_title(path), path.name, format_duration(sound_duration(path))),
                )
            if selected_path:
                for index, path in enumerate(library_paths):
                    if path == selected_path:
                        sound_table.selection_set(str(index))
                        sound_table.focus(str(index))
                        sound_table.see(str(index))
                        break
            update_library_buttons()

        def selected_library_sound() -> Path | None:
            selection = sound_table.selection()
            if not selection:
                return None
            return library_paths[int(selection[0])]

        def update_library_buttons(_event=None):
            if sound_table.selection() or (self.player and self.player.is_previewing):
                preview_button.state(["!disabled"])
            else:
                preview_button.state(["disabled"])

        def hide_table_tooltip(_event=None):
            self.tooltips.hide(window)

        def show_table_tooltip(event):
            if library_closing or not self.can_show_tooltip(window):
                hide_table_tooltip()
                return
            row_id = sound_table.identify_row(event.y)
            column_id = sound_table.identify_column(event.x)
            column_name = {"#1": "name", "#2": "file"}.get(column_id)
            if not row_id or column_name is None or not sound_table.exists(row_id):
                hide_table_tooltip()
                return
            text = sound_table.set(row_id, column_name)
            self.tooltips.show(window, (row_id, column_name), text,
                               event.x_root, event.y_root)

        def add_library_sound():
            hide_table_tooltip()
            filename = filedialog.askopenfilename(parent=window, filetypes=[("Ficheros MP3", "*.mp3")])
            if not filename:
                return
            added = self.store.add_sound(Path(filename))
            self.refresh_sounds()
            self.sound_var.set(sound_label(added))
            refresh_library(added)
            self.status_var.set(f"Movido a sonidos/{added.name}")

        def remove_library_sound():
            hide_table_tooltip()
            sound_path = selected_library_sound()
            if sound_path is None:
                messagebox.showwarning("Selecciona un sonido", "Selecciona el MP3 que quieres eliminar.", parent=window)
                return
            alarm_names = alarm_names_using_sound(self.store.alarms, sound_path)
            if alarm_names:
                alarm_list = "\n".join(f"• {name}" for name in alarm_names)
                warning = (
                    f"El sonido «{sound_path.name}» se utiliza en estas alarmas:\n\n"
                    f"{alarm_list}\n\nSi lo eliminas, esas alarmas quedarán sin sonido."
                )
            else:
                warning = f"¿Quieres eliminar «{sound_path.name}» de la biblioteca?"
            if not messagebox.askyesno("Eliminar sonido", warning, parent=window):
                return
            try:
                if self.player:
                    self.player.stop_preview()
                    preview_button.config(text="Reproducir")
                sound_path.unlink()
            except OSError as exc:
                messagebox.showerror("No se pudo eliminar", str(exc), parent=window)
                return
            self.refresh_sounds()
            self.refresh_alarms()
            refresh_library()
            self.status_var.set(f"Sonido eliminado: {sound_path.name}")

        def play_library_sound():
            hide_table_tooltip()
            if self.player and self.player.is_previewing:
                self.player.stop_preview()
                preview_button.config(text="Reproducir")
                return
            sound_path = selected_library_sound()
            if sound_path is None:
                messagebox.showwarning("Selecciona un sonido", "Selecciona el MP3 que quieres reproducir.", parent=window)
                return
            if not self.player:
                messagebox.showwarning("Audio no disponible", "No se puede reproducir el sonido.", parent=window)
                return
            try:
                self.player.preview(sound_path)
                preview_button.config(text="Parar")
                monitor_preview()
            except Exception as exc:
                messagebox.showerror("No se pudo reproducir", str(exc), parent=window)

        def monitor_preview():
            if not window.winfo_exists():
                return
            if self.player and self.player.is_previewing:
                window.after(200, monitor_preview)
            else:
                preview_button.config(text="Reproducir")

        def close_library():
            nonlocal library_closing
            library_closing = True
            if self.player:
                self.player.stop_preview()
            hide_table_tooltip()
            self.sound_library_window = None
            window.destroy()

        def library_destroyed(event):
            nonlocal library_closing
            if event.widget is window:
                library_closing = True
                hide_table_tooltip()
                if self.sound_library_window is window:
                    self.sound_library_window = None
                if self.player:
                    self.player.stop_preview()

        buttons = ttk.Frame(content)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Añadir MP3", command=add_library_sound).pack(side="left")
        preview_button = ttk.Button(buttons, text="Reproducir", command=play_library_sound, state="disabled")
        preview_button.pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text="Eliminar", command=remove_library_sound).pack(side="left", padx=6)
        ttk.Button(buttons, text="Cerrar", command=close_library).pack(side="right")

        sound_table.bind("<<TreeviewSelect>>", update_library_buttons)
        sound_table.bind("<Motion>", show_table_tooltip)
        sound_table.bind("<Leave>", hide_table_tooltip)
        sound_table.bind("<ButtonPress>", hide_table_tooltip, add="+")
        sound_table.bind("<MouseWheel>", hide_table_tooltip, add="+")
        window.bind("<Destroy>", library_destroyed, add="+")
        refresh_library()
        window.protocol("WM_DELETE_WINDOW", close_library)
        window.grab_set()
        window.focus_set()

    def create_test_alarm(self, _event=None):
        now = self.clock.now().replace(microsecond=0)
        start_at = now + timedelta(seconds=20)
        end_at = start_at + timedelta(seconds=30)
        sound_path = next(
            (
                path
                for path in self.store.sounds()
                if sound_title(path).casefold() == "smoothing alarm ringtone"
                or path.stem.casefold().replace("-", " ") == "soothing alarm ringtone"
            ),
            None,
        )
        if sound_path is None:
            messagebox.showerror(
                "Sonido no encontrado",
                "No se encuentra el sonido «Smoothing Alarm Ringtone» en la carpeta sonidos.",
            )
            return "break"

        alarm = Alarm(
            name="Alarma de prueba",
            days=[DAYS[start_at.weekday()]],
            start=start_at.strftime("%H:%M:%S"),
            end=end_at.strftime("%H:%M:%S"),
            sound=str(sound_path),
            volume=100,
            enabled=True,
        )
        self.store.alarms.append(alarm)
        self.store.save()
        self.refresh_alarms()
        self.status_var.set(f"Alarma de prueba programada para las {alarm.start}")
        return "break"

    def move_alarm(self, direction: int):
        if self.selected_index is None:
            return
        target = self.selected_index + direction
        if not 0 <= target < len(self.store.alarms):
            return
        self.store.alarms[self.selected_index], self.store.alarms[target] = (
            self.store.alarms[target],
            self.store.alarms[self.selected_index],
        )
        self.store.save()
        self.selected_index = target
        self.refresh_alarms()
        self.tree.selection_set(str(target))
        self.tree.focus(str(target))
        self.tree.see(str(target))

    def stop_sound(self):
        if self.player:
            now = self.clock.now()
            for alarm in self.player.current_alarms.values():
                self.silenced_occurrences.add(self.occurrence_key(alarm, now))
            self.player.stop()
            self.status_var.set("Sonido detenido manualmente")

    def occurrence_key(self, alarm: Alarm, now: datetime) -> str:
        return f"{now.date()}|{id(alarm)}|{alarm.start}|{alarm.end}"

    def poll_single_instance(self):
        if not self.running or self.single_instance_guard is None:
            return
        if self.single_instance_guard.activation_requested():
            self.restore_window()
        self.after(200, self.poll_single_instance)

    def restore_window(self):
        self.window_hidden = False
        self.deiconify()
        self.lift()
        self.focus_force()
        if sys.platform == "darwin" and NSApplication:
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        else:
            self.attributes("-topmost", True)
            self.after(200, lambda: self.attributes("-topmost", False))

    def scheduler_tick(self):
        if not self.running:
            return
        now = self.clock.now()
        self.update_tray_status(now)
        day = DAYS[now.weekday()]
        current = now.time().replace(microsecond=0)
        active_alarms: list[Alarm] = []
        active_keys: set[str] = set()
        for alarm in self.store.alarms:
            if not alarm.enabled or day not in alarm.days:
                continue
            key = self.occurrence_key(alarm, now)
            if alarm.start_time <= current < alarm.end_time:
                active_keys.add(key)
                if key not in self.silenced_occurrences:
                    active_alarms.append(alarm)

        self.silenced_occurrences.intersection_update(active_keys)
        if active_alarms and self.player:
            self.player.play_loops(active_alarms)
            names = ", ".join(alarm.name for alarm in active_alarms)
            self.status_var.set(f"Sonando: {names}")
        elif self.player and self.player.is_playing:
            self.player.stop()
            self.status_var.set("Listo")
        self.after(200, self.scheduler_tick)

    def on_close(self):
        if self.tray_icon is None:
            self.quit_application()
            return
        self.window_hidden = True
        self.hide_all_tooltips()
        self.withdraw()
        self.status_var.set("La aplicación continúa ejecutándose en la barra de tareas")

    def quit_application(self):
        self.running = False
        self.hide_all_tooltips()
        self.stop_sound()
        if self.tray_icon is not None:
            self.tray_icon.stop()
            self.tray_icon = None
        if self.single_instance_guard:
            self.single_instance_guard.close()
        self.destroy()


if __name__ == "__main__":
    instance_guard = SingleInstanceGuard()
    if not instance_guard.acquire():
        instance_guard.notify_existing()
        raise SystemExit(0)
    try:
        AlarmApp(instance_guard).mainloop()
    finally:
        instance_guard.close()
