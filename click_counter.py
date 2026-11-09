# -*- coding: utf-8 -*-
"""
Счётчик кликов мыши — Windows.

Считает ВСЕ клики мыши по системе с момента запуска.
Кнопка «Стоп» показывает отчёт. Закрытие окна сворачивает приложение в трей.

Запуск:  run.bat   (сам поставит зависимости)
   или:  python click_counter.py
"""

import os
import sys
import time
import queue
import threading
import traceback

APP_NAME = "Счётчик кликов"
APP_ID = "click_counter"

# В .exe (PyInstaller) файлы распаковываются во временную папку — журнал
# и настройки должны лежать рядом с самим .exe, а не в этой временной папке.
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- зависимости ------------------------------------------------------------
IMPORT_ERRORS = []

try:
    from pynput import mouse
except Exception as exc:                                  # noqa: BLE001
    mouse = None
    IMPORT_ERRORS.append(("pynput", exc))

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
    TRAY_AVAILABLE = True
except Exception as exc:                                  # noqa: BLE001
    pystray = None
    TRAY_AVAILABLE = False
    TRAY_ERROR = exc
else:
    TRAY_ERROR = None

import tkinter as tk
from tkinter import messagebox


def resource_path(name):
    """Путь к ресурсу — работает и в исходниках, и внутри собранного .exe."""
    base = getattr(sys, "_MEIPASS", BASE_DIR)
    return os.path.join(base, name)


# --- журнал и аварийные сообщения ------------------------------------------
def log_path():
    for folder in (BASE_DIR, os.path.join(os.environ.get("LOCALAPPDATA", ""), "ClickCounter")):
        if not folder:
            continue
        try:
            os.makedirs(folder, exist_ok=True)
            probe = os.path.join(folder, ".w")
            with open(probe, "w", encoding="utf-8"):
                pass
            os.remove(probe)
            return os.path.join(folder, "click_counter.log")
        except Exception:                                 # noqa: BLE001
            continue
    return None


LOG_FILE = log_path()


def write_log(text):
    if not LOG_FILE:
        return
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(time.strftime("[%Y-%m-%d %H:%M:%S] "))
            f.write(text)
            f.write("\n")
    except Exception:                                     # noqa: BLE001
        pass


def show_error(title, message):
    """Показать ошибку так, чтобы её увидели даже без консоли."""
    write_log(f"{title}: {message}")
    try:
        root_tmp = tk.Tk()
        root_tmp.withdraw()
        root_tmp.attributes("-topmost", True)
        messagebox.showerror(title, message, parent=root_tmp)
        root_tmp.destroy()
    except Exception:                                     # noqa: BLE001
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)
        except Exception:                                 # noqa: BLE001
            print(f"{title}: {message}", file=sys.stderr)


# --- оформление -------------------------------------------------------------
BG        = "#0d1117"
BG_PANEL  = "#161b22"
BG_ROW    = "#1c2128"
FG        = "#e6edf3"
FG_DIM    = "#8b949e"
FG_DIMMER = "#6e7681"
ACCENT    = "#58a6ff"
GREEN     = "#3fb950"
RED       = "#f85149"

BUTTON_NAMES = {
    mouse.Button.left:   "Левая",
    mouse.Button.right:  "Правая",
    mouse.Button.middle: "Средняя",
} if mouse else {}
BUTTON_ORDER = ["Левая", "Правая", "Средняя"]


def fmt_num(n):
    return f"{n:,}".replace(",", " ")


def fmt_time(sec):
    if sec is None:
        return "—"
    h = int(sec // 3600)
    m = int(sec // 60) % 60
    s = int(sec) % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def fmt_ms(sec):
    if sec is None:
        return "—"
    return f"{int(sec * 1000)} мс" if sec < 1 else f"{sec:.2f} с"


# --- счётчик ----------------------------------------------------------------
class Stats:
    """Потокобезопасный счётчик: хранит только агрегаты, память не растёт."""

    DEBOUNCE = 0.002

    def __init__(self):
        self.lock = threading.Lock()
        self.start = time.perf_counter()
        self.reset()

    def reset(self):
        with self.lock:
            self.running = True
            self.start = time.perf_counter()
            self.stop_time = None
            self.total = 0
            self.buttons = {name: 0 for name in BUTTON_ORDER}
            self.other_buttons = 0
            self.first_click = None
            self.last_ts = None
            self.gaps_count = 0
            self.sum_gap = 0.0
            self.min_gap = None
            self.max_gap = None
            self.per_second = {}
            self._last_press = (None, None, 0.0)

    def on_click(self, x, y, button, pressed):
        if not pressed:
            return
        now = time.perf_counter()
        with self.lock:
            if not self.running:
                return

            name = BUTTON_NAMES.get(button, str(button).replace("Button.", ""))

            last_name, last_pos, last_t = self._last_press
            if (name == last_name and (x, y) == last_pos
                    and (now - last_t) < self.DEBOUNCE):
                return
            self._last_press = (name, (x, y), now)

            ts = now - self.start
            self.total += 1
            if name in self.buttons:
                self.buttons[name] += 1
            else:
                self.other_buttons += 1

            if self.first_click is None:
                self.first_click = ts

            if self.last_ts is not None:
                gap = ts - self.last_ts
                self.gaps_count += 1
                self.sum_gap += gap
                if self.min_gap is None or gap < self.min_gap:
                    self.min_gap = gap
                if self.max_gap is None or gap > self.max_gap:
                    self.max_gap = gap
            self.last_ts = ts

            key = int(ts)
            self.per_second[key] = self.per_second.get(key, 0) + 1

    def stop(self):
        with self.lock:
            if self.running:
                self.stop_time = time.perf_counter()
                self.running = False
            return self.stop_time

    def live(self):
        with self.lock:
            end = self.stop_time or time.perf_counter()
            return self.total, self.running, end - self.start

    def snapshot(self):
        with self.lock:
            end = self.stop_time or time.perf_counter()
            busiest_sec, busiest_n = 0, 0
            if self.per_second:
                busiest_sec = max(self.per_second, key=self.per_second.get)
                busiest_n = self.per_second[busiest_sec]
            return {
                "total": self.total,
                "buttons": dict(self.buttons),
                "other": self.other_buttons,
                "duration": end - self.start,
                "first_click": self.first_click,
                "gaps_count": self.gaps_count,
                "avg_gap": (self.sum_gap / self.gaps_count) if self.gaps_count else None,
                "min_gap": self.min_gap,
                "max_gap": self.max_gap,
                "busiest_sec": busiest_sec,
                "busiest_n": busiest_n,
            }


def build_report_rows(s):
    cps = (s["total"] / s["duration"]) if s["duration"] > 0 else 0.0
    rows = [
        ("Всего кликов",            fmt_num(s["total"])),
        ("Время работы",            fmt_time(s["duration"])),
        ("Средняя скорость",        f"{cps:.2f} клика/сек  ·  {cps * 60:.0f} в минуту"),
        ("Средний интервал",        fmt_ms(s["avg_gap"])),
        ("Самый быстрый интервал",  fmt_ms(s["min_gap"])),
        ("Самая долгая пауза",      fmt_ms(s["max_gap"])),
        ("Первый клик",             f"через {fmt_ms(s['first_click'])}"
                                    if s["first_click"] else "кликов не было"),
        ("Самая активная секунда",  f"{s['busiest_n']} клик(ов) на {s['busiest_sec']}-й секунде"
                                    if s["busiest_n"] else "—"),
    ]
    breakdown = []
    for name in BUTTON_ORDER:
        n = s["buttons"].get(name, 0)
        if n:
            share = (n / s["total"] * 100) if s["total"] else 0
            breakdown.append(f"{name}: {fmt_num(n)} ({share:.0f}%)")
    if s["other"]:
        breakdown.append(f"Прочие: {fmt_num(s['other'])}")
    return rows, breakdown


# --- иконка трея ------------------------------------------------------------
def _load_font(size):
    for name in ("seguisb.ttf", "segoeuib.ttf", "arialbd.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:                                 # noqa: BLE001
            continue
    try:
        return ImageFont.load_default()
    except Exception:                                     # noqa: BLE001
        return None


def make_icon(count=0, running=True, size=64):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bg = (63, 185, 80, 255) if running else (248, 81, 73, 255)
    d.ellipse([2, 2, size - 3, size - 3], fill=bg, outline=(13, 17, 23, 255), width=3)

    text = str(count) if count < 10000 else f"{count // 1000}k"
    font = _load_font(36 if len(text) <= 2 else (28 if len(text) == 3 else 20))
    if font is not None:
        try:
            d.text((size / 2, size / 2 + 1), text, font=font,
                   fill=(6, 18, 31, 255), anchor="mm")
        except Exception:                                 # noqa: BLE001
            d.text((size / 2 - 8, size / 2 - 12), text,
                   font=font, fill=(6, 18, 31, 255))
    return img


# --- приложение -------------------------------------------------------------
class App:
    def __init__(self, root, stats):
        self.root = root
        self.stats = stats
        self.ui_queue = queue.Queue()
        self.tray = None
        self.tray_hint_shown = False
        self._last_shown = -1
        self._last_icon_total = -1
        self._last_icon_time = 0.0
        self.hidden = False

        root.title(APP_NAME)
        root.configure(bg=BG)
        root.geometry("430x640")
        root.minsize(400, 580)
        root.attributes("-topmost", True)

        # иконка окна — рисуем сами, файл не нужен
        try:
            from PIL import ImageTk
            self._win_icon = ImageTk.PhotoImage(make_icon(0, True, 64))
            root.iconphoto(True, self._win_icon)
        except Exception:                                 # noqa: BLE001
            pass

        wrap = tk.Frame(root, bg=BG, padx=24, pady=18)
        wrap.pack(fill="both", expand=True)

        head = tk.Frame(wrap, bg=BG)
        head.pack(fill="x")
        self.dot = tk.Label(head, text="●", bg=BG, fg=GREEN, font=("Segoe UI", 12))
        self.dot.pack(side="left")
        self.status = tk.Label(head, text="Идёт подсчёт", bg=BG, fg=FG_DIM,
                               font=("Segoe UI", 9))
        self.status.pack(side="left", padx=(6, 0))

        self.counter = tk.Label(wrap, text="0", bg=BG, fg=FG,
                                font=("Segoe UI Semibold", 58))
        self.counter.pack(pady=(12, 0))
        tk.Label(wrap, text="кликов мыши с момента запуска", bg=BG, fg=FG_DIMMER,
                 font=("Segoe UI", 9)).pack()

        self.timer = tk.Label(wrap, text="00:00", bg=BG, fg=ACCENT, font=("Consolas", 14))
        self.timer.pack(pady=(8, 0))

        tk.Label(wrap, text="Считаются все клики по системе — в любом окне",
                 bg=BG, fg=FG_DIMMER, font=("Segoe UI", 9)).pack(pady=(4, 14))

        self.stop_btn = tk.Button(wrap, text="СТОП  —  показать отчёт",
                                  command=self.on_stop, relief="flat", bd=0,
                                  bg="#2d1b1b", fg="#ff7b72", activebackground="#3d2323",
                                  activeforeground="#ff7b72", cursor="hand2",
                                  font=("Segoe UI Semibold", 10), pady=11)
        self.stop_btn.pack(fill="x")

        self.reset_btn = tk.Button(wrap, text="Сбросить и начать заново",
                                   command=self.on_reset, relief="flat", bd=0,
                                   bg=BG_PANEL, fg=ACCENT, activebackground="#1f2733",
                                   activeforeground=ACCENT, cursor="hand2",
                                   font=("Segoe UI Semibold", 10), pady=11)
        self.reset_btn.pack(fill="x", pady=(8, 0))

        self.report = tk.Frame(wrap, bg=BG_PANEL, highlightthickness=1,
                               highlightbackground="#232a33")
        self.report_body = tk.Frame(self.report, bg=BG_PANEL)
        self.report_body.pack(fill="both", expand=True, padx=16, pady=12)
        self.rows_frame = tk.Frame(self.report_body, bg=BG_PANEL)
        self.rows_frame.pack(fill="x")
        self.extra = tk.Label(self.report_body, text="", bg=BG_PANEL, fg=FG_DIM,
                              font=("Segoe UI", 9), justify="left", anchor="w")
        self.extra.pack(fill="x", pady=(8, 0))

        hint = ("Закрытие окна сворачивает приложение в трей — подсчёт продолжится."
                if TRAY_AVAILABLE else
                "pystray не установлен — сворачивание в трей недоступно.")
        tk.Label(wrap, text=hint, bg=BG, fg="#484f58", font=("Segoe UI", 8),
                 wraplength=360, justify="center").pack(side="bottom", pady=(12, 0))

        self.listener = mouse.Listener(on_click=self.stats.on_click)
        self.listener.daemon = True
        self.listener.start()

        root.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        if TRAY_AVAILABLE:
            self.start_tray()

        self.tick()

    # --- трей ---------------------------------------------------------------
    def start_tray(self):
        try:
            menu = pystray.Menu(
                pystray.MenuItem("Открыть окно", self._q(self.show_window), default=True),
                pystray.MenuItem("Стоп и показать отчёт", self._q(self.on_stop)),
                pystray.MenuItem("Сбросить", self._q(self.on_reset)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Выход", self._q(self.quit_app)),
            )
            self.tray = pystray.Icon(APP_ID, make_icon(0, True),
                                     f"{APP_NAME} — 0", menu)
            threading.Thread(target=self.tray.run, daemon=True).start()
            write_log("Трей запущен")
        except Exception as exc:                          # noqa: BLE001
            self.tray = None
            write_log("Ошибка трея: " + traceback.format_exc())
            print("Трей недоступен:", exc, file=sys.stderr)

    def _q(self, fn):
        """Оборачивает действие трея — выполнится в потоке Tk."""
        def wrapper(icon=None, item=None):
            self.ui_queue.put(fn)
        return wrapper

    def update_tray_icon(self, force=False):
        if not self.tray:
            return
        total, running, _ = self.stats.live()
        now = time.perf_counter()
        if not force and total == self._last_icon_total and (now - self._last_icon_time) < 2:
            return
        if not force and (now - self._last_icon_time) < 0.4:
            return
        self._last_icon_total = total
        self._last_icon_time = now
        try:
            self.tray.icon = make_icon(total, running)
            self.tray.title = f"{APP_NAME} — {fmt_num(total)}"
            self.tray.update_menu()
        except Exception:                                 # noqa: BLE001
            pass

    def hide_to_tray(self):
        if not TRAY_AVAILABLE:
            self.quit_app()
            return
        self.hidden = True
        self.root.withdraw()
        self.update_tray_icon(force=True)
        if not self.tray_hint_shown and self.tray:
            self.tray_hint_shown = True
            try:
                self.tray.notify("Свёрнуто в трей. Подсчёт продолжается.",
                                 APP_NAME)
            except Exception:                             # noqa: BLE001
                pass

    def show_window(self):
        self.hidden = False
        self.root.deiconify()
        self.root.attributes("-topmost", True)
        self.root.lift()
        self.root.focus_force()

    def quit_app(self):
        try:
            if self.listener:
                self.listener.stop()
        except Exception:                                 # noqa: BLE001
            pass
        try:
            if self.tray:
                self.tray.stop()
        except Exception:                                 # noqa: BLE001
            pass
        write_log("Выход")
        try:
            self.root.quit()
            self.root.destroy()
        except Exception:                                 # noqa: BLE001
            pass

    # --- цикл отрисовки -----------------------------------------------------
    def tick(self):
        # команды из трея
        try:
            while True:
                fn = self.ui_queue.get_nowait()
                try:
                    fn()
                except Exception:                         # noqa: BLE001
                    write_log("Ошибка команды трея: " + traceback.format_exc())
        except queue.Empty:
            pass

        total, running, elapsed = self.stats.live()

        if total != self._last_shown:
            self.counter.config(text=fmt_num(total))
            self._last_shown = total

        if running:
            self.timer.config(text=fmt_time(elapsed))

        self.update_tray_icon()

        self.root.after(120, self.tick)   # цикл живёт всегда, даже после «Стоп»

    # --- действия -----------------------------------------------------------
    def on_stop(self):
        self.stats.stop()
        self.dot.config(fg=RED)
        self.status.config(text="Остановлено")

        rows, breakdown = build_report_rows(self.stats.snapshot())
        for w in self.rows_frame.winfo_children():
            w.destroy()

        for i, (label, value) in enumerate(rows):
            r = tk.Frame(self.rows_frame, bg=BG_ROW if i % 2 == 0 else BG_PANEL)
            r.pack(fill="x")
            tk.Label(r, text=label, bg=r["bg"], fg=FG_DIM, font=("Segoe UI", 9),
                     anchor="w").pack(side="left", padx=10, pady=5)
            tk.Label(r, text=value, bg=r["bg"], fg=FG, font=("Consolas", 9, "bold"),
                     anchor="e").pack(side="right", padx=10, pady=5)

        self.extra.config(text="ПО КНОПКАМ\n" + ("\n".join(breakdown)
                                                 if breakdown else "кликов не было"))
        self.report.pack(fill="x", pady=(12, 0))

        self.stop_btn.config(state="disabled", bg=BG_PANEL, fg="#484f58",
                             activebackground=BG_PANEL, cursor="arrow")
        self.reset_btn.config(bg="#122a1c", fg="#7ee787")

        self.update_tray_icon(force=True)
        self.show_window()

    def on_reset(self):
        self.stats.reset()
        self.dot.config(fg=GREEN)
        self.status.config(text="Идёт подсчёт")
        self.counter.config(text="0")
        self._last_shown = 0
        self.report.pack_forget()
        self.stop_btn.config(state="normal", bg="#2d1b1b", fg="#ff7b72",
                             activebackground="#3d2323", cursor="hand2")
        self.reset_btn.config(bg=BG_PANEL, fg=ACCENT)
        self.update_tray_icon(force=True)


# --- запуск -----------------------------------------------------------------
def main():
    if sys.platform != "win32":
        show_error(APP_NAME, "Эта версия приложения рассчитана только на Windows.")
        return

    if mouse is None:
        details = "".join(f"\n• {name}: {err}" for name, err in IMPORT_ERRORS)
        show_error(
            APP_NAME,
            "Не удалось загрузить библиотеку для перехвата мыши."
            f"{details}\n\n"
            "Откройте командную строку в папке с приложением и выполните:\n\n"
            "    pip install pynput\n\n"
            f"Журнал: {LOG_FILE}"
        )
        return

    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:                                     # noqa: BLE001
        pass

    if not TRAY_AVAILABLE:
        write_log(f"Трей недоступен: {TRAY_ERROR}")

    write_log("Запуск приложения")
    try:
        root = tk.Tk()
        App(root, Stats())
        root.mainloop()
    except Exception:                                     # noqa: BLE001
        tb = traceback.format_exc()
        write_log(tb)
        show_error(APP_NAME, "Приложение упало при запуске.\n\n"
                             f"{tb.strip().splitlines()[-1]}\n\n"
                             f"Полный журнал: {LOG_FILE}")


if __name__ == "__main__":
    try:
        main()
    except Exception:                                     # noqa: BLE001
        show_error(APP_NAME, "Критическая ошибка:\n\n" + traceback.format_exc())
