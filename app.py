import json
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, time as dtime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    import pygame
except ImportError:  # handled in UI startup
    pygame = None

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
    """Normalize H, H:M or H:M:S using floor/ceil values for omitted units."""
    parts = [part.strip() for part in value.strip().split(":")]
    if not 1 <= len(parts) <= 3 or any(not part.isdigit() for part in parts):
        raise ValueError("Usa una hora válida con formato HH, HH:MM o HH:MM:SS.")

    numbers = [int(part) for part in parts]
    hour = numbers[0]
    minute = numbers[1] if len(numbers) >= 2 else (59 if ceil else 0)
    second = numbers[2] if len(numbers) == 3 else (59 if ceil else 0)
    if not 0 <= hour <= 23 or not 0 <= minute <= 59 or not 0 <= second <= 59:
        raise ValueError("La hora debe estar comprendida entre 00:00:00 y 23:59:59.")
    return f"{hour:02d}:{minute:02d}:{second:02d}"


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


class AlarmStore:
    def __init__(self):
        APP_DIR.mkdir(exist_ok=True)
        SOUNDS_DIR.mkdir(exist_ok=True)
        self._migrate_legacy_sounds()
        self._install_bundled_sounds()
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
        self.current_alarm: Alarm | None = None
        if pygame:
            pygame.mixer.init()

    def play_loop(self, alarm: Alarm):
        if not pygame:
            return
        if self.current_alarm is alarm:
            return
        pygame.mixer.music.stop()
        pygame.mixer.music.load(alarm.sound)
        pygame.mixer.music.set_volume(alarm.volume / 100)
        pygame.mixer.music.play(loops=-1)
        self.current_alarm = alarm

    def stop(self):
        if not pygame:
            return
        pygame.mixer.music.stop()
        self.current_alarm = None


class AlarmApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Programador de alarmas")
        if ICON_FILE.exists():
            self.app_icon = tk.PhotoImage(file=ICON_FILE)
            self.iconphoto(True, self.app_icon)
        self.store = AlarmStore()
        self.player = AudioPlayer() if pygame else None
        self.selected_index: int | None = None
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

        self.build_ui()
        self.build_menu()
        self.refresh_sounds()
        self.refresh_alarms()
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
        help_menu = tk.Menu(menu_bar, tearoff=False)
        help_menu.add_command(label="Acerca de", command=self.show_about)
        menu_bar.add_cascade(label="Ayuda", menu=help_menu)
        self.config(menu=menu_bar)

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

    def build_ui(self):
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)

        left = ttk.LabelFrame(root, text="Alarmas", padding=8)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))

        columns = ("enabled", "name", "days", "start", "end", "volume", "sound")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", height=18)
        for col, title, width in [
            ("enabled", "Activa", 60),
            ("name", "Nombre", 150),
            ("days", "Días", 190),
            ("start", "Inicio", 70),
            ("end", "Fin", 70),
            ("volume", "Vol.", 50),
            ("sound", "Sonido", 180),
        ]:
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self.load_selected_alarm)

        buttons = ttk.Frame(left)
        buttons.pack(fill="x", pady=(8, 0))
        ttk.Button(buttons, text="Nueva", command=self.start_new_alarm).pack(side="left")
        self.save_button = ttk.Button(buttons, text="Guardar", command=self.save_alarm, state="disabled")
        self.save_button.pack(side="left", padx=4)
        self.delete_button = ttk.Button(buttons, text="Eliminar", command=self.delete_alarm, state="disabled")
        self.delete_button.pack(side="left", padx=4)
        self.cancel_button = ttk.Button(buttons, text="Cancelar", command=self.cancel_configuration, state="disabled")
        self.cancel_button.pack(side="left", padx=4)
        ttk.Button(buttons, text="↑ Subir", command=lambda: self.move_alarm(-1)).pack(side="left", padx=(12, 4))
        ttk.Button(buttons, text="↓ Bajar", command=lambda: self.move_alarm(1)).pack(side="left")
        ttk.Button(buttons, text="Detener sonido", command=self.stop_sound).pack(side="right")

        form = ttk.LabelFrame(root, text="Programación", padding=8)
        form.pack(side="right", fill="y")

        ttk.Checkbutton(form, text="Activa", variable=self.enabled_var).grid(row=0, column=0, sticky="w", columnspan=2)
        ttk.Label(form, text="Nombre").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(form, textvariable=self.name_var, width=28).grid(row=1, column=1, sticky="ew", pady=(8, 0))

        ttk.Label(form, text="Días").grid(row=2, column=0, sticky="nw", pady=(8, 0))
        days_box = ttk.Frame(form)
        days_box.grid(row=2, column=1, sticky="w", pady=(8, 0))
        for i, day in enumerate(DAYS):
            ttk.Checkbutton(days_box, text=DAY_LABELS[day], variable=self.day_vars[day]).grid(row=i, column=0, sticky="w")

        ttk.Label(form, text="Inicio HH:MM:SS").grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.start_entry = ttk.Entry(form, textvariable=self.start_var, width=10)
        self.start_entry.grid(row=3, column=1, sticky="w", pady=(8, 0))
        self.start_entry.bind("<FocusOut>", lambda _event: self.normalize_time_field(self.start_var, ceil=False))
        ttk.Label(form, text="Fin HH:MM:SS").grid(row=4, column=0, sticky="w", pady=(8, 0))
        self.end_entry = ttk.Entry(form, textvariable=self.end_var, width=10)
        self.end_entry.grid(row=4, column=1, sticky="w", pady=(8, 0))
        self.end_entry.bind("<FocusOut>", lambda _event: self.normalize_time_field(self.end_var, ceil=True))
        ttk.Label(form, text="Inicio completa con 00; fin completa con 59.", foreground="#555").grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(2, 8)
        )

        ttk.Label(form, text="Sonido MP3").grid(row=6, column=0, sticky="w")
        self.sound_combo = ttk.Combobox(form, textvariable=self.sound_var, state="readonly", width=25)
        self.sound_combo.grid(row=6, column=1, sticky="ew")
        ttk.Button(form, text="Añadir MP3 al catálogo", command=self.add_sound).grid(row=7, column=0, columnspan=2, sticky="ew", pady=8)
        ttk.Label(form, text="Volumen").grid(row=8, column=0, sticky="w")
        tk.Scale(
            form,
            from_=0,
            to=100,
            resolution=1,
            variable=self.volume_var,
            orient="horizontal",
            showvalue=True,
            length=180,
        ).grid(row=8, column=1, sticky="ew")
        ttk.Label(form, textvariable=self.status_var, foreground="#075").grid(row=9, column=0, columnspan=2, sticky="w", pady=(20, 0))

    def normalize_time_field(self, variable: tk.StringVar, *, ceil: bool):
        value = variable.get().strip()
        if not value:
            return
        try:
            variable.set(normalize_time(value, ceil=ceil))
        except ValueError:
            # Keep invalid input available for correction; Save will show the error.
            pass

    def refresh_sounds(self):
        self.sound_paths = self.store.sounds()
        self.sound_combo["values"] = [path.name for path in self.sound_paths]
        if self.sound_paths and not self.sound_var.get():
            self.sound_var.set(self.sound_paths[0].name)

    def refresh_alarms(self):
        self.tree.delete(*self.tree.get_children())
        for idx, alarm in enumerate(self.store.alarms):
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
                    Path(alarm.sound).name,
                ),
            )

    def selected_sound_path(self) -> str:
        selected = self.sound_var.get()
        for path in self.sound_paths:
            if path.name == selected:
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
        self.selected_index = int(selected[0])
        alarm = self.store.alarms[self.selected_index]
        self.enabled_var.set(alarm.enabled)
        self.name_var.set(alarm.name)
        self.start_var.set(alarm.start)
        self.end_var.set(alarm.end)
        self.sound_var.set(Path(alarm.sound).name)
        self.volume_var.set(alarm.volume)
        for day in DAYS:
            self.day_vars[day].set(day in alarm.days)
        self.save_button.state(["!disabled"])
        self.delete_button.state(["!disabled"])
        self.cancel_button.state(["!disabled"])

    def start_new_alarm(self):
        self.clear_form()
        self.save_button.state(["!disabled"])
        self.cancel_button.state(["!disabled"])

    def cancel_configuration(self):
        self.clear_form()
        self.status_var.set("Configuración cancelada")

    def clear_form(self):
        self.selected_index = None
        self.tree.selection_remove(self.tree.selection())
        self.enabled_var.set(True)
        self.name_var.set("")
        self.start_var.set("08:00:00")
        self.end_var.set("08:05:00")
        self.volume_var.set(100)
        for var in self.day_vars.values():
            var.set(False)
        self.save_button.state(["disabled"])
        self.delete_button.state(["disabled"])
        self.cancel_button.state(["disabled"])

    def delete_alarm(self):
        if self.selected_index is None:
            return
        del self.store.alarms[self.selected_index]
        self.store.save()
        self.refresh_alarms()
        self.clear_form()

    def add_sound(self):
        filename = filedialog.askopenfilename(filetypes=[("MP3", "*.mp3")])
        if not filename:
            return
        added = self.store.add_sound(Path(filename))
        self.refresh_sounds()
        self.sound_var.set(added.name)
        self.status_var.set(f"Movido a sonidos/{added.name}")

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
            if self.player.current_alarm:
                self.silenced_occurrences.add(self.occurrence_key(self.player.current_alarm, now))
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
        active_alarm = None
        active_keys: set[str] = set()
        for alarm in self.store.alarms:
            if not alarm.enabled or day not in alarm.days:
                continue
            key = self.occurrence_key(alarm, now)
            if alarm.start_time <= current < alarm.end_time:
                active_keys.add(key)
                if key not in self.silenced_occurrences and active_alarm is None:
                    active_alarm = alarm

        self.silenced_occurrences.intersection_update(active_keys)
        if active_alarm and self.player:
            self.player.play_loop(active_alarm)
            self.status_var.set(f"Sonando: {active_alarm.name}")
        elif self.player and self.player.current_alarm:
            self.player.stop()
            self.status_var.set("Listo")
        self.after(200, self.scheduler_tick)

    def on_close(self):
        self.running = False
        self.stop_sound()
        self.destroy()


if __name__ == "__main__":
    AlarmApp().mainloop()
