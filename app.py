import json
import shutil
import sys
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
    from mutagen.easyid3 import EasyID3
except ImportError:  # handled by falling back to the file name
    EasyID3 = None

try:
    from AppKit import NSApplication, NSImage
except ImportError:  # optional outside macOS
    NSApplication = NSImage = None

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
        if pygame:
            pygame.mixer.init()

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

    def stop(self):
        if not pygame:
            return
        for channel in self.channels.values():
            channel.stop()
        self.channels.clear()
        self.current_alarms.clear()


class AlarmApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Programador de alarmas")
        if ICON_FILE.exists():
            self.app_icon = tk.PhotoImage(file=ICON_FILE)
            self.iconphoto(True, self.app_icon)
            if NSApplication and NSImage:
                dock_icon = NSImage.alloc().initWithContentsOfFile_(str(ICON_FILE))
                if dock_icon:
                    NSApplication.sharedApplication().setApplicationIconImage_(dock_icon)
        self.store = AlarmStore()
        self.player = AudioPlayer() if pygame else None
        self.selected_index: int | None = None
        self.form_mode = "idle"
        self.updating_form = False
        self.running = True
        self.silenced_occurrences: set[str] = set()
        self.day_vars = {day: tk.BooleanVar() for day in DAYS}
        self.enabled_var = tk.BooleanVar(value=True)
        self.name_var = tk.StringVar()
        self.start_var = tk.StringVar(value="08:00:00")
        self.end_var = tk.StringVar(value="08:05:00")
        self.sound_var = tk.StringVar()
        self.volume_var = tk.IntVar(value=100)
        self.status_var = tk.StringVar(value="Listo")
        self.sound_tooltip: tk.Toplevel | None = None
        self.sound_tooltip_row: str | None = None
        self.problem_tooltip: tk.Toplevel | None = None
        self.problem_tooltip_row: str | None = None

        self.build_ui()
        self.bind("<Control-t>", self.create_test_alarm)
        self.build_menu()
        self.refresh_sounds()
        self.refresh_alarms()
        self.setup_form_tracking()
        self.fit_window_to_content()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(200, self.scheduler_tick)

        if pygame is None:
            messagebox.showwarning(
                "Falta dependencia",
                "Instala pygame-ce para reproducir MP3:\n\npython -m pip install -r requirements.txt",
            )

    def build_menu(self):
        menu_bar = tk.Menu(self)
        if sys.platform == "darwin":
            app_menu = tk.Menu(menu_bar, name="apple", tearoff=False)
            app_menu.add_command(label="Acerca de Programador de alarmas", command=self.show_about)
            menu_bar.add_cascade(menu=app_menu)
        settings_menu = tk.Menu(menu_bar, tearoff=False)
        settings_menu.add_command(label="Biblioteca de sonidos", command=self.open_sound_library)
        menu_bar.add_cascade(label="Configuración", menu=settings_menu)
        help_menu = tk.Menu(menu_bar, tearoff=False)
        help_menu.add_command(label="Cómo usar la aplicación", command=self.show_help)
        if sys.platform != "darwin":
            help_menu.add_separator()
            help_menu.add_command(label="Acerca de", command=self.show_about)
        menu_bar.add_cascade(label="Ayuda", menu=help_menu)
        self.config(menu=menu_bar)
        if sys.platform == "darwin":
            self.tk.createcommand("tkAboutDialog", self.show_about)

    def show_help(self):
        messagebox.showinfo(
            "Ayuda de Programador de alarmas",
            "1. Pulsa Nueva y escribe el nombre, los días y las horas de inicio y fin.\n\n"
            "2. Selecciona un sonido y su volumen, y pulsa Guardar.\n\n"
            "3. Gestiona los MP3 desde Configuración → Biblioteca de sonidos.\n\n"
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
        self.tree.bind("<Leave>", lambda _event: self.hide_sound_tooltip())
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

    def show_sound_tooltip(self, event):
        row_id = self.tree.identify_row(event.y)
        column_id = self.tree.identify_column(event.x)
        if column_id == "#8" and row_id:
            self.hide_sound_tooltip()
            self.show_problem_tooltip(event, row_id)
            return
        self.hide_problem_tooltip()
        if not row_id or column_id != "#7":
            self.hide_sound_tooltip()
            return
        if self.sound_tooltip is not None and self.sound_tooltip_row == row_id:
            return

        alarm = self.store.alarms[int(row_id)]
        self.hide_sound_tooltip()
        tooltip = tk.Toplevel(self)
        tooltip.withdraw()
        tooltip.wm_overrideredirect(True)
        tooltip.attributes("-topmost", True)
        label = tk.Label(
            tooltip,
            text=Path(alarm.sound).name,
            background="#fff8c6",
            foreground="#000000",
            relief="solid",
            borderwidth=1,
            padx=5,
            pady=3,
        )
        label.pack()
        tooltip.update_idletasks()
        tooltip.geometry(f"+{event.x_root + 12}+{event.y_root + 12}")
        tooltip.deiconify()
        self.sound_tooltip = tooltip
        self.sound_tooltip_row = row_id

    def show_problem_tooltip(self, event, row_id: str):
        if self.problem_tooltip is not None and self.problem_tooltip_row == row_id:
            return
        issues = alarm_issues(self.store.alarms).get(int(row_id), [])
        if not issues:
            return
        self.hide_problem_tooltip()
        tooltip = tk.Toplevel(self)
        tooltip.withdraw()
        tooltip.wm_overrideredirect(True)
        tooltip.attributes("-topmost", True)
        label = tk.Label(
            tooltip,
            text="\n".join(issues),
            justify="left",
            background="#fff8c6",
            foreground="#000000",
            relief="solid",
            borderwidth=1,
            padx=5,
            pady=3,
        )
        label.pack()
        tooltip.update_idletasks()
        tooltip.geometry(f"+{event.x_root + 12}+{event.y_root + 12}")
        tooltip.deiconify()
        self.problem_tooltip = tooltip
        self.problem_tooltip_row = row_id

    def hide_sound_tooltip(self):
        if self.sound_tooltip is not None:
            self.sound_tooltip.destroy()
            self.sound_tooltip = None
            self.sound_tooltip_row = None

    def hide_problem_tooltip(self):
        if self.problem_tooltip is not None:
            self.problem_tooltip.destroy()
            self.problem_tooltip = None
            self.problem_tooltip_row = None

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
        window = tk.Toplevel(self)
        window.title("Biblioteca de sonidos")
        window.transient(self)
        window.resizable(True, True)

        content = ttk.Frame(window, padding=12)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="Sonidos disponibles").pack(anchor="w")
        sound_list = tk.Listbox(content, width=62, height=14, exportselection=False)
        sound_list.pack(fill="both", expand=True, pady=(6, 10))
        library_paths: list[Path] = []

        def refresh_library(selected_path: Path | None = None):
            nonlocal library_paths
            library_paths = self.store.sounds()
            sound_list.delete(0, tk.END)
            for path in library_paths:
                sound_list.insert(tk.END, sound_label(path))
            if selected_path:
                for index, path in enumerate(library_paths):
                    if path == selected_path:
                        sound_list.selection_set(index)
                        sound_list.see(index)
                        break

        def add_library_sound():
            filename = filedialog.askopenfilename(parent=window, filetypes=[("Ficheros MP3", "*.mp3")])
            if not filename:
                return
            added = self.store.add_sound(Path(filename))
            self.refresh_sounds()
            self.sound_var.set(sound_label(added))
            refresh_library(added)
            self.status_var.set(f"Movido a sonidos/{added.name}")

        def remove_library_sound():
            selection = sound_list.curselection()
            if not selection:
                messagebox.showwarning("Selecciona un sonido", "Selecciona el MP3 que quieres eliminar.", parent=window)
                return
            sound_path = library_paths[selection[0]]
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
                sound_path.unlink()
            except OSError as exc:
                messagebox.showerror("No se pudo eliminar", str(exc), parent=window)
                return
            self.refresh_sounds()
            self.refresh_alarms()
            refresh_library()
            self.status_var.set(f"Sonido eliminado: {sound_path.name}")

        buttons = ttk.Frame(content)
        buttons.pack(fill="x")
        ttk.Button(buttons, text="Añadir MP3", command=add_library_sound).pack(side="left")
        ttk.Button(buttons, text="Eliminar", command=remove_library_sound).pack(side="left", padx=6)
        ttk.Button(buttons, text="Cerrar", command=window.destroy).pack(side="right")

        refresh_library()
        window.protocol("WM_DELETE_WINDOW", window.destroy)
        window.grab_set()
        window.focus_set()

    def create_test_alarm(self, _event=None):
        now = datetime.now().replace(microsecond=0)
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
            now = datetime.now()
            for alarm in self.player.current_alarms.values():
                self.silenced_occurrences.add(self.occurrence_key(alarm, now))
            self.player.stop()
            self.status_var.set("Sonido detenido manualmente")

    def occurrence_key(self, alarm: Alarm, now: datetime) -> str:
        return f"{now.date()}|{id(alarm)}|{alarm.start}|{alarm.end}"

    def scheduler_tick(self):
        if not self.running:
            return
        now = datetime.now()
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
        self.running = False
        self.stop_sound()
        self.destroy()


if __name__ == "__main__":
    AlarmApp().mainloop()
