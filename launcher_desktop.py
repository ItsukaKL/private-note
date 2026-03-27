from __future__ import annotations

import json
import os
import queue
import socket
import sys
import threading
import traceback
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk

import launcher_core
from note_service import (
    ask_question,
    create_note_item,
    delete_note_item,
    get_note_item,
    init_storage,
    list_note_items,
    update_note_item_content,
)
from ollama_client import get_llm_model, init_runtime_settings, list_chat_models, set_llm_model


CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 18000 + (zlib.crc32(str(launcher_core.ROOT_DIR).lower().encode("utf-8")) % 1000)
STATE_PATH = Path("data") / "desktop_state.json"
MAX_CHAT_ITEMS = 200
APP_ICON_ICO = launcher_core.ROOT_DIR / "packaging" / "assets" / "app.ico"
APP_ICON_PNG = launcher_core.ROOT_DIR / "icon.png"


@dataclass(frozen=True)
class Palette:
    root_bg: str
    panel_bg: str
    panel_alt: str
    border: str
    text: str
    muted: str
    accent: str
    accent_hover: str
    danger: str
    danger_hover: str
    bubble_assistant: str
    bubble_user: str
    input_bg: str
    input_fg: str
    chip_bg: str
    chip_fg: str
    chrome_bg: str
    chrome_hover: str


PALETTES = {
    "light": Palette(
        root_bg="#edf3fb",
        panel_bg="#ffffff",
        panel_alt="#f6f9fc",
        border="#d8e1ec",
        text="#0f172a",
        muted="#64748b",
        accent="#1d76ff",
        accent_hover="#0f5fd5",
        danger="#dc2626",
        danger_hover="#b91c1c",
        bubble_assistant="#ffffff",
        bubble_user="#dcedff",
        input_bg="#ffffff",
        input_fg="#0f172a",
        chip_bg="#e8f0ff",
        chip_fg="#1d4ed8",
        chrome_bg="#eef3f8",
        chrome_hover="#dfe7f0",
    ),
    "dark": Palette(
        root_bg="#09111f",
        panel_bg="#0f172a",
        panel_alt="#14203a",
        border="#22314b",
        text="#e2e8f0",
        muted="#94a3b8",
        accent="#3b82f6",
        accent_hover="#2563eb",
        danger="#ef4444",
        danger_hover="#dc2626",
        bubble_assistant="#172338",
        bubble_user="#0f766e",
        input_bg="#0b1323",
        input_fg="#e2e8f0",
        chip_bg="#162743",
        chip_fg="#dbeafe",
        chrome_bg="#162133",
        chrome_hover="#21304a",
    ),
}


def install_excepthook() -> None:
    def handle_exception(exc_type, exc_value, exc_traceback) -> None:
        launcher_core.ensure_runtime_dirs()
        with launcher_core.DESKTOP_STDERR_LOG.open("a", encoding="utf-8") as file:
            file.write("".join(traceback.format_exception(exc_type, exc_value, exc_traceback)))
            file.write("\n")
        try:
            messagebox.showerror("Private Note 客户端", str(exc_value))
        except Exception:
            pass

    sys.excepthook = handle_exception


def notify_existing_instance() -> bool:
    try:
        with socket.create_connection((CONTROL_HOST, CONTROL_PORT), timeout=0.4) as connection:
            connection.sendall(b"raise")
        return True
    except OSError:
        return False


def format_time(iso_text: str | None) -> str:
    if not iso_text:
        return ""
    try:
        timestamp = datetime.fromisoformat(iso_text.replace("Z", "+00:00"))
    except ValueError:
        return iso_text
    return timestamp.astimezone().strftime("%Y-%m-%d %H:%M")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def summarize_note(content: str, limit: int = 110) -> str:
    text = " ".join((content or "").split())
    if not text:
        return "空白笔记"
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def first_line(content: str, fallback: str) -> str:
    for line in (content or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:42]
    return fallback


class ControlSocketServer(threading.Thread):
    def __init__(self, callback):
        super().__init__(daemon=True)
        self.callback = callback
        self.stop_event = threading.Event()
        self.server_socket: socket.socket | None = None

    def run(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            self.server_socket = server
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((CONTROL_HOST, CONTROL_PORT))
            server.listen()
            server.settimeout(0.5)
            while not self.stop_event.is_set():
                try:
                    connection, _address = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                with connection:
                    try:
                        connection.recv(1024)
                    except OSError:
                        pass
                self.callback()

    def shutdown(self) -> None:
        self.stop_event.set()
        if self.server_socket:
            try:
                self.server_socket.close()
            except OSError:
                pass


class ScrollArea(tk.Frame):
    def __init__(self, parent, bg: str, style_name: str = "App.Vertical.TScrollbar"):
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview, style=style_name)
        self.content = tk.Frame(self.canvas, bg=bg, highlightthickness=0, bd=0)
        self.window_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")
        self.content.bind("<Configure>", self._on_content_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self._mousewheel_bound = False
        for widget in (self, self.canvas, self.content):
            widget.bind("<Enter>", self._bind_mousewheel, add="+")
            widget.bind("<Leave>", self._unbind_mousewheel, add="+")

    def _on_content_configure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event) -> None:
        self.canvas.itemconfigure(self.window_id, width=event.width)

    def _bind_mousewheel(self, _event=None) -> None:
        if self._mousewheel_bound:
            return
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self._mousewheel_bound = True

    def _unbind_mousewheel(self, _event=None) -> None:
        if not self._mousewheel_bound:
            return
        self.canvas.unbind_all("<MouseWheel>")
        self._mousewheel_bound = False

    def _on_mousewheel(self, event) -> None:
        try:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        except tk.TclError:
            pass

    def clear(self) -> None:
        for child in self.content.winfo_children():
            child.destroy()

    def scroll_to_end(self) -> None:
        self.update_idletasks()
        self.canvas.yview_moveto(1.0)

    def set_colors(self, bg: str) -> None:
        self.configure(bg=bg)
        self.canvas.configure(bg=bg)
        self.content.configure(bg=bg)


def _rounded_polygon_points(x1: float, y1: float, x2: float, y2: float, radius: float) -> list[float]:
    radius = max(0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    return [
        x1 + radius,
        y1,
        x1 + radius,
        y1,
        x2 - radius,
        y1,
        x2 - radius,
        y1,
        x2,
        y1,
        x2,
        y1 + radius,
        x2,
        y1 + radius,
        x2,
        y2 - radius,
        x2,
        y2 - radius,
        x2,
        y2,
        x2 - radius,
        y2,
        x2 - radius,
        y2,
        x1 + radius,
        y2,
        x1 + radius,
        y2,
        x1,
        y2,
        x1,
        y2 - radius,
        x1,
        y2 - radius,
        x1,
        y1 + radius,
        x1,
        y1 + radius,
        x1,
        y1,
    ]


class RoundedButton(tk.Canvas):
    def __init__(self, parent, text: str, command, style_name: str, palette: Palette):
        super().__init__(parent, highlightthickness=0, bd=0, relief="flat", cursor="hand2")
        self.command = command
        self.style_name = style_name
        self.palette = palette
        self._text = text
        self._disabled = False
        self._hovered = False
        self._pressed = False
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._redraw()

    def _metrics(self) -> tuple[tuple[str, int, str], int, int, int]:
        if self.style_name == "Icon.TButton" and self._text in {"__moon__", "__sun__", "__trash__"}:
            return (("Segoe UI Symbol", 16, "normal"), 10, 10, 20)
        if self.style_name == "Accent.TButton":
            return (("Microsoft YaHei UI", 10, "bold"), 18, 12, 22)
        if self.style_name == "Danger.TButton":
            return (("Microsoft YaHei UI", 10, "bold"), 18, 12, 22)
        if self.style_name == "PrimarySmall.TButton":
            return (("Microsoft YaHei UI", 9, "bold"), 12, 8, 18)
        if self.style_name == "GhostSmall.TButton":
            return (("Microsoft YaHei UI", 9, "bold"), 12, 8, 18)
        if self.style_name == "DangerSmall.TButton":
            return (("Microsoft YaHei UI", 9, "bold"), 12, 8, 18)
        if self.style_name == "Icon.TButton":
            return (("Segoe UI Symbol", 16, "normal"), 12, 10, 20)
        if self.style_name == "FluentIcon.TButton":
            return (("Segoe MDL2 Assets", 16, "normal"), 12, 10, 20)
        return (("Microsoft YaHei UI", 10, "bold"), 18, 12, 22)

    def _colors(self) -> tuple[str, str, str]:
        if self._disabled:
            return self.palette.chrome_bg, self.palette.border, self.palette.muted

        if self.style_name in {"Accent.TButton"}:
            fill = self.palette.accent_hover if self._hovered or self._pressed else self.palette.accent
            return fill, fill, "#ffffff"
        if self.style_name in {"PrimarySmall.TButton"}:
            fill = self.palette.accent_hover if self._hovered or self._pressed else self.palette.accent
            return fill, fill, "#ffffff"
        if self.style_name in {"Danger.TButton", "DangerSmall.TButton"}:
            fill = self.palette.danger_hover if self._hovered or self._pressed else self.palette.danger
            return fill, fill, "#ffffff"

        fill = self.palette.chrome_hover if self._hovered or self._pressed else self.palette.chrome_bg
        return fill, self.palette.border, self.palette.text

    def _redraw(self) -> None:
        font, pad_x, pad_y, radius = self._metrics()
        if self.style_name == "Icon.TButton" and self._text in {"__moon__", "__sun__", "__trash__"}:
            width = 48
            height = 42
        else:
            measure = tkfont.Font(font=font)
            text_width = measure.measure(self._text)
            text_height = measure.metrics("linespace")
            width = text_width + pad_x * 2
            height = text_height + pad_y * 2
        if self.style_name in {"Icon.TButton", "FluentIcon.TButton"}:
            width = max(width, height)

        fill, outline, fg = self._colors()
        self.configure(width=width, height=height, bg=self.master.cget("bg"))
        self.delete("all")
        points = _rounded_polygon_points(1, 1, width - 1, height - 1, radius)
        self.create_polygon(points, smooth=True, splinesteps=32, fill=fill, outline=outline, width=1)
        if self.style_name == "Icon.TButton" and self._text in {"__moon__", "__sun__", "__trash__"}:
            if self._text == "__moon__":
                self._draw_moon_icon(width, height, fg, fill)
            elif self._text == "__trash__":
                self._draw_trash_icon(width, height, fg)
            else:
                self._draw_sun_icon(width, height, fg)
            return
        self.create_text(width / 2, height / 2, text=self._text, fill=fg, font=font)

    def _draw_moon_icon(self, width: int, height: int, fg: str, fill: str) -> None:
        self.create_text(
            width / 2 - 1,
            height / 2 + 1,
            text="☾",
            fill=fg,
            font=("Segoe UI Symbol", 18, "normal"),
        )

    def _draw_sun_icon(self, width: int, height: int, fg: str) -> None:
        cx = width / 2
        cy = height / 2
        radius = min(width, height) * 0.14
        ray_outer = min(width, height) * 0.28
        self.create_oval(
            cx - radius,
            cy - radius,
            cx + radius,
            cy + radius,
            outline=fg,
            width=2,
        )
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0), (-0.7, -0.7), (0.7, -0.7), (-0.7, 0.7), (0.7, 0.7)):
            self.create_line(
                cx + dx * (radius + 2),
                cy + dy * (radius + 2),
                cx + dx * ray_outer,
                cy + dy * ray_outer,
                fill=fg,
                width=2,
                capstyle=tk.ROUND,
            )

    def _draw_trash_icon(self, width: int, height: int, fg: str) -> None:
        cx = width / 2
        lid_y = height * 0.3
        body_top = height * 0.38
        body_bottom = height * 0.73
        left_x = cx - width * 0.115
        right_x = cx + width * 0.115
        self.create_line(
            cx - width * 0.16,
            lid_y,
            cx + width * 0.16,
            lid_y,
            fill=fg,
            width=2,
            capstyle=tk.ROUND,
        )
        self.create_line(
            cx - width * 0.07,
            lid_y - height * 0.05,
            cx + width * 0.07,
            lid_y - height * 0.05,
            fill=fg,
            width=2,
            capstyle=tk.ROUND,
        )
        self.create_arc(
            cx - width * 0.055,
            lid_y - height * 0.1,
            cx + width * 0.055,
            lid_y - height * 0.01,
            start=0,
            extent=180,
            style=tk.ARC,
            outline=fg,
            width=2,
        )
        body_points = [
            left_x,
            body_top,
            right_x,
            body_top,
            right_x - width * 0.02,
            body_bottom,
            left_x + width * 0.02,
            body_bottom,
        ]
        self.create_polygon(body_points, outline=fg, fill="", width=2, joinstyle=tk.ROUND)
        for offset in (-width * 0.045, width * 0.045):
            self.create_line(
                cx + offset,
                body_top + height * 0.09,
                cx + offset,
                body_bottom - height * 0.08,
                fill=fg,
                width=2,
                capstyle=tk.ROUND,
            )

    def set_palette(self, palette: Palette) -> None:
        self.palette = palette
        self._redraw()

    def configure(self, cnf=None, **kwargs):
        text_changed = False
        if cnf:
            kwargs.update(cnf)
        if "text" in kwargs:
            self._text = kwargs.pop("text")
            text_changed = True
        if "command" in kwargs:
            self.command = kwargs.pop("command")
        super().configure(**kwargs)
        if text_changed:
            self._redraw()

    config = configure

    def cget(self, key: str):
        if key == "text":
            return self._text
        return super().cget(key)

    def state(self, statespec: list[str]):
        if "disabled" in statespec:
            self._disabled = True
        if "!disabled" in statespec:
            self._disabled = False
        self._redraw()

    def _on_enter(self, _event=None) -> None:
        if self._disabled:
            return
        self._hovered = True
        self._redraw()

    def _on_leave(self, _event=None) -> None:
        self._hovered = False
        self._pressed = False
        self._redraw()

    def _on_press(self, _event=None) -> None:
        if self._disabled:
            return
        self._pressed = True
        self._redraw()

    def _on_release(self, event=None) -> None:
        if self._disabled:
            return
        was_pressed = self._pressed
        self._pressed = False
        self._redraw()
        if not was_pressed:
            return
        if event is None:
            return
        if 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height():
            if callable(self.command):
                self.command()


class RoundedBubble(tk.Canvas):
    def __init__(
        self,
        parent,
        *,
        text: str,
        fill: str,
        fg: str,
        outline: str,
        wraplength: int,
        font: tuple[str, int] | tuple[str, int, str],
        outer_bg: str,
        radius: int = 20,
        pad_x: int = 16,
        pad_y: int = 14,
    ):
        super().__init__(parent, highlightthickness=0, bd=0, relief="flat", bg=outer_bg)
        self.text_value = text
        self.fill = fill
        self.fg = fg
        self.outline = outline
        self.wraplength = wraplength
        self.font = font
        self.radius = radius
        self.pad_x = pad_x
        self.pad_y = pad_y
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        text_id = self.create_text(
            self.pad_x,
            self.pad_y,
            text=self.text_value,
            fill=self.fg,
            font=self.font,
            anchor="nw",
            justify="left",
            width=self.wraplength,
        )
        bbox = self.bbox(text_id)
        if bbox is None:
            width = self.pad_x * 2 + 40
            height = self.pad_y * 2 + 20
        else:
            width = bbox[2] + self.pad_x
            height = bbox[3] + self.pad_y
        self.configure(width=width, height=height)
        self.delete("all")
        points = _rounded_polygon_points(1, 1, width - 1, height - 1, self.radius)
        self.create_polygon(points, smooth=True, splinesteps=36, fill=self.fill, outline=self.outline, width=1)
        self.create_text(
            self.pad_x,
            self.pad_y,
            text=self.text_value,
            fill=self.fg,
            font=self.font,
            anchor="nw",
            justify="left",
            width=self.wraplength,
        )


class DesktopClient:
    def __init__(self) -> None:
        self.theme_name = self._load_theme_name()
        self.palette = PALETTES[self.theme_name]
        self.root = tk.Tk()
        self.root.title("Private Note 客户端")
        self.root.geometry("1440x920")
        self.root.minsize(1200, 760)
        self.root.configure(bg=self.palette.root_bg)
        self._window_icon_image: tk.PhotoImage | None = None
        self._apply_window_icon()

        self.style = ttk.Style(self.root)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

        self.queue: queue.Queue[tuple[str, object, object]] = queue.Queue()
        self.notes_cache: list[dict] = []
        self.chat_items = self._load_chat_items()
        self.settings_payload: dict | None = None
        self.current_page = "main"
        self.current_note_id: int | None = None
        self.editing_note_id: int | None = None
        self.refresh_in_flight = False
        self.chat_busy = False
        self.note_busy = False
        self.model_busy = False
        self.runtime_busy = False
        self.model_event_suppressed = False
        self.status_clear_after_id: str | None = None
        self.shutdown_in_progress = False

        self.surface_roles: list[tuple[tk.Widget, str]] = []
        self.label_roles: list[tuple[tk.Label, str]] = []
        self.text_widgets: list[tk.Text] = []
        self.scroll_areas: list[ScrollArea] = []
        self.rounded_buttons: list[RoundedButton] = []

        launcher_core.ensure_runtime_dirs()
        launcher_core.register_launcher_pid(os.getpid())
        launcher_core.append_event("桌面客户端已启动。")
        init_storage()

        self.server = ControlSocketServer(self.request_raise)
        self.server.start()

        self.root.option_add("*tearOff", False)
        self.root.bind("<Control-Return>", self._submit_shortcut)

        self._build_ui()
        self.apply_theme()
        self._schedule_queue_poll()
        self._schedule_auto_refresh()
        self._set_model_chip(self._safe_runtime_model())
        self.load_notes(show_feedback=False)
        self.refresh_settings_status(silent=True)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _apply_window_icon(self) -> None:
        if APP_ICON_ICO.exists():
            try:
                self.root.iconbitmap(default=str(APP_ICON_ICO))
            except tk.TclError:
                pass
        if APP_ICON_PNG.exists():
            try:
                self._window_icon_image = tk.PhotoImage(file=str(APP_ICON_PNG))
                self.root.iconphoto(True, self._window_icon_image)
            except tk.TclError:
                self._window_icon_image = None

    def _build_ui(self) -> None:
        self.shell = tk.Frame(self.root, bg=self.palette.root_bg, padx=18, pady=18)
        self.shell.pack(fill="both", expand=True)
        self.surface_roles.append((self.shell, "root"))

        self.page_container = tk.Frame(self.shell, bg=self.palette.root_bg)
        self.page_container.pack(fill="both", expand=True)
        self.surface_roles.append((self.page_container, "root"))

        self.pages: dict[str, tk.Frame] = {}
        self._build_main_page()
        self._build_editor_page()
        self._build_settings_page()
        self.show_page("main")

        self.status_line = tk.Label(
            self.shell,
            text="就绪",
            anchor="w",
            padx=2,
            pady=10,
            font=("Microsoft YaHei UI", 9),
        )
        self.status_line.pack(fill="x")
        self.label_roles.append((self.status_line, "muted"))

    def _build_main_page(self) -> None:
        page = tk.Frame(self.page_container, bg=self.palette.root_bg)
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        page.grid_columnconfigure(1, weight=1)
        page.grid_rowconfigure(0, weight=1)
        self.pages["main"] = page
        self.surface_roles.append((page, "root"))

        self.sidebar_shell = tk.Frame(page, bg=self.palette.root_bg, width=350)
        self.sidebar_shell.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        self.sidebar_shell.grid_propagate(False)
        self.surface_roles.append((self.sidebar_shell, "root"))

        self.sidebar_card = tk.Frame(
            self.sidebar_shell,
            bg=self.palette.panel_bg,
            highlightthickness=1,
            highlightbackground=self.palette.border,
            bd=0,
            padx=18,
            pady=18,
        )
        self.sidebar_card.pack(fill="both", expand=True)
        self.surface_roles.append((self.sidebar_card, "panel"))

        sidebar_header = tk.Frame(self.sidebar_card, bg=self.palette.panel_bg)
        sidebar_header.pack(fill="x", pady=(0, 14))
        self.surface_roles.append((sidebar_header, "panel"))

        sidebar_title_wrap = tk.Frame(sidebar_header, bg=self.palette.panel_bg)
        sidebar_title_wrap.pack(side="left", fill="x", expand=True)
        self.surface_roles.append((sidebar_title_wrap, "panel"))

        self.notes_title = tk.Label(sidebar_title_wrap, text="我的笔记", font=("Microsoft YaHei UI", 16, "bold"), anchor="w")
        self.notes_title.pack(anchor="w")
        self.label_roles.append((self.notes_title, "text"))

        self.notes_subtitle = tk.Label(sidebar_title_wrap, text="本地知识库与问答上下文", font=("Microsoft YaHei UI", 10), anchor="w")
        self.notes_subtitle.pack(anchor="w", pady=(4, 0))
        self.label_roles.append((self.notes_subtitle, "muted"))

        self.add_note_button = self._make_button(sidebar_header, "+ 新建", self.open_new_note, style="Accent.TButton")
        self.add_note_button.pack(side="right")

        self.note_count_label = tk.Label(self.sidebar_card, text="0 条笔记", font=("Microsoft YaHei UI", 9), anchor="w")
        self.note_count_label.pack(fill="x", pady=(0, 10))
        self.label_roles.append((self.note_count_label, "muted"))

        self.notes_area = ScrollArea(self.sidebar_card, self.palette.panel_bg)
        self.notes_area.pack(fill="both", expand=True)
        self.scroll_areas.append(self.notes_area)

        self.main_panel = tk.Frame(
            page,
            bg=self.palette.panel_bg,
            highlightthickness=1,
            highlightbackground=self.palette.border,
            bd=0,
        )
        self.main_panel.grid(row=0, column=1, sticky="nsew")
        self.main_panel.grid_rowconfigure(1, weight=1)
        self.main_panel.grid_columnconfigure(0, weight=1)
        self.surface_roles.append((self.main_panel, "panel"))

        chat_header = tk.Frame(self.main_panel, bg=self.palette.panel_bg, padx=24, pady=20)
        chat_header.grid(row=0, column=0, sticky="ew")
        chat_header.grid_columnconfigure(0, weight=1)
        self.surface_roles.append((chat_header, "panel"))

        chat_title_wrap = tk.Frame(chat_header, bg=self.palette.panel_bg)
        chat_title_wrap.grid(row=0, column=0, sticky="w")
        self.surface_roles.append((chat_title_wrap, "panel"))

        self.chat_title = tk.Label(chat_title_wrap, text="基于笔记的问答", font=("Microsoft YaHei UI", 16, "bold"), anchor="w")
        self.chat_title.pack(anchor="w")
        self.label_roles.append((self.chat_title, "text"))

        self.chat_subtitle = tk.Label(chat_title_wrap, text="回答仅基于当前本地笔记内容", font=("Microsoft YaHei UI", 10), anchor="w")
        self.chat_subtitle.pack(anchor="w", pady=(4, 0))
        self.label_roles.append((self.chat_subtitle, "muted"))

        header_actions = tk.Frame(chat_header, bg=self.palette.panel_bg)
        header_actions.grid(row=0, column=1, sticky="e")
        self.surface_roles.append((header_actions, "panel"))

        self.model_chip = tk.Label(header_actions, text="模型: 加载中", font=("Microsoft YaHei UI", 9, "bold"), padx=12, pady=7)
        self.model_chip.pack(side="left", padx=(0, 10))

        self.chat_clear_button = self._make_button(header_actions, "__trash__", self.confirm_clear_chat, style="Icon.TButton")
        self.chat_clear_button.pack(side="left", padx=(0, 8))

        self.theme_button = self._make_button(header_actions, "__moon__", self.toggle_theme, style="Icon.TButton")
        self.theme_button.pack(side="left", padx=(0, 8))

        self.settings_button = self._make_button(header_actions, "\ue713", self.open_settings_page, style="FluentIcon.TButton")
        self.settings_button.pack(side="left")

        self.chat_area = ScrollArea(self.main_panel, self.palette.panel_alt)
        self.chat_area.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 10))
        self.scroll_areas.append(self.chat_area)

        self.composer = tk.Frame(self.main_panel, bg=self.palette.panel_bg, padx=18, pady=18)
        self.composer.grid(row=2, column=0, sticky="ew")
        self.composer.grid_columnconfigure(0, weight=1)
        self.surface_roles.append((self.composer, "panel"))

        self.input_frame = tk.Frame(
            self.composer,
            bg=self.palette.input_bg,
            highlightthickness=1,
            highlightbackground=self.palette.border,
            bd=0,
        )
        self.input_frame.grid(row=0, column=0, sticky="ew", padx=(0, 12))
        self.surface_roles.append((self.input_frame, "input"))

        self.question_input = tk.Text(
            self.input_frame,
            wrap="word",
            height=4,
            relief="flat",
            bd=0,
            padx=14,
            pady=12,
            font=("Microsoft YaHei UI", 11),
            undo=True,
        )
        self.question_input.pack(fill="both", expand=True)
        self.text_widgets.append(self.question_input)

        self.send_button = self._make_button(self.composer, "发送", self.send_question, style="Accent.TButton")
        self.send_button.grid(row=0, column=1, sticky="ns")

    def _build_editor_page(self) -> None:
        page = tk.Frame(self.page_container, bg=self.palette.root_bg)
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        page.grid_rowconfigure(0, weight=1)
        page.grid_columnconfigure(0, weight=1)
        self.pages["editor"] = page
        self.surface_roles.append((page, "root"))

        self.editor_surface = tk.Frame(
            page,
            bg=self.palette.panel_bg,
            highlightthickness=1,
            highlightbackground=self.palette.border,
            bd=0,
            padx=22,
            pady=22,
        )
        self.editor_surface.pack(fill="both", expand=True)
        self.editor_surface.grid_rowconfigure(1, weight=1)
        self.editor_surface.grid_columnconfigure(0, weight=1)
        self.surface_roles.append((self.editor_surface, "panel"))

        editor_header = tk.Frame(self.editor_surface, bg=self.palette.panel_bg)
        editor_header.grid(row=0, column=0, sticky="ew", pady=(0, 18))
        editor_header.grid_columnconfigure(0, weight=1)
        self.surface_roles.append((editor_header, "panel"))

        editor_title_wrap = tk.Frame(editor_header, bg=self.palette.panel_bg)
        editor_title_wrap.grid(row=0, column=0, sticky="w")
        self.surface_roles.append((editor_title_wrap, "panel"))

        self.editor_title_label = tk.Label(editor_title_wrap, text="新建笔记", font=("Microsoft YaHei UI", 16, "bold"), anchor="w")
        self.editor_title_label.pack(anchor="w")
        self.label_roles.append((self.editor_title_label, "text"))

        self.editor_copy_label = tk.Label(editor_title_wrap, text="保存后会重新向量化并更新检索索引", font=("Microsoft YaHei UI", 10), anchor="w")
        self.editor_copy_label.pack(anchor="w", pady=(4, 0))
        self.label_roles.append((self.editor_copy_label, "muted"))

        editor_actions = tk.Frame(editor_header, bg=self.palette.panel_bg)
        editor_actions.grid(row=0, column=1, sticky="e")
        self.surface_roles.append((editor_actions, "panel"))

        self.editor_clear_button = self._make_button(editor_actions, "清空", self.clear_editor, style="Danger.TButton")
        self.editor_clear_button.pack(side="left", padx=(0, 10))

        self.editor_save_button = self._make_button(editor_actions, "保存", self.save_note, style="Accent.TButton")
        self.editor_save_button.pack(side="left", padx=(0, 10))

        self.editor_back_button = self._make_button(editor_actions, "返回", self.back_to_main, style="Ghost.TButton")
        self.editor_back_button.pack(side="left")

        self.editor_text = tk.Text(
            self.editor_surface,
            wrap="word",
            relief="flat",
            bd=0,
            padx=18,
            pady=18,
            font=("Microsoft YaHei UI", 12),
            undo=True,
        )
        self.editor_text.grid(row=1, column=0, sticky="nsew")
        self.text_widgets.append(self.editor_text)

    def _build_settings_page(self) -> None:
        page = tk.Frame(self.page_container, bg=self.palette.root_bg)
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.pages["settings"] = page
        self.surface_roles.append((page, "root"))

        self.settings_surface = tk.Frame(page, bg=self.palette.root_bg)
        self.settings_surface.pack(fill="both", expand=True)
        self.surface_roles.append((self.settings_surface, "root"))

        settings_header = tk.Frame(self.settings_surface, bg=self.palette.root_bg, pady=4)
        settings_header.pack(fill="x", pady=(0, 16))
        settings_header.grid_columnconfigure(0, weight=1)
        self.surface_roles.append((settings_header, "root"))

        settings_title_wrap = tk.Frame(settings_header, bg=self.palette.root_bg)
        settings_title_wrap.grid(row=0, column=0, sticky="w")
        self.surface_roles.append((settings_title_wrap, "root"))

        self.settings_title_label = tk.Label(settings_title_wrap, text="设置与运行控制台", font=("Microsoft YaHei UI", 16, "bold"), anchor="w")
        self.settings_title_label.pack(anchor="w")
        self.label_roles.append((self.settings_title_label, "text"))

        self.settings_copy_label = tk.Label(
            settings_title_wrap,
            text="这里承担模型切换、Ollama 状态检查、环境诊断与日志查看",
            font=("Microsoft YaHei UI", 10),
            anchor="w",
        )
        self.settings_copy_label.pack(anchor="w", pady=(4, 0))
        self.label_roles.append((self.settings_copy_label, "muted"))

        self.settings_back_button = self._make_button(settings_header, "返回", self.back_to_main, style="Ghost.TButton")
        self.settings_back_button.grid(row=0, column=1, sticky="e")

        self.settings_body = tk.Frame(self.settings_surface, bg=self.palette.root_bg)
        self.settings_body.pack(fill="both", expand=True)
        self.settings_body.grid_columnconfigure(0, weight=3)
        self.settings_body.grid_columnconfigure(1, weight=2)
        self.settings_body.grid_rowconfigure(0, weight=1)
        self.settings_body.grid_rowconfigure(1, weight=1)
        self.surface_roles.append((self.settings_body, "root"))

        self.runtime_card = self._make_card(self.settings_body)
        self.runtime_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=(0, 10))

        self.runtime_title = self._make_card_title(self.runtime_card, "运行状态")
        self.runtime_title.pack(anchor="w")

        self.runtime_copy = self._make_card_copy(self.runtime_card, "这里开始接管项目私有 Ollama 运行时和项目私有模型目录。")
        self.runtime_copy.pack(anchor="w", pady=(4, 18))

        self.runtime_grid = tk.Frame(self.runtime_card, bg=self.palette.panel_bg)
        self.runtime_grid.pack(fill="x")
        self.runtime_grid.grid_columnconfigure(1, weight=1)
        self.surface_roles.append((self.runtime_grid, "panel"))

        self.runtime_desktop_value = self._add_runtime_row(self.runtime_grid, 0, "桌面客户端")
        self.runtime_ollama_value = self._add_runtime_row(self.runtime_grid, 1, "Ollama")
        self.runtime_source_value = self._add_runtime_row(self.runtime_grid, 2, "运行时来源")
        self.runtime_model_value = self._add_runtime_row(self.runtime_grid, 3, "当前模型")
        self.runtime_models_value = self._add_runtime_row(self.runtime_grid, 4, "已安装模型")

        self.model_card = self._make_card(self.settings_body)
        self.model_card.grid(row=1, column=0, sticky="nsew", padx=(0, 10))

        self.model_title = self._make_card_title(self.model_card, "模型设置")
        self.model_title.pack(anchor="w")

        self.model_copy = self._make_card_copy(self.model_card, "切换后立即热生效，下一次提问直接使用新的对话模型。")
        self.model_copy.pack(anchor="w", pady=(4, 18))

        self.model_var = tk.StringVar()
        self.model_combo = ttk.Combobox(
            self.model_card,
            textvariable=self.model_var,
            state="readonly",
            style="App.TCombobox",
        )
        self.model_combo.pack(fill="x")
        self.model_combo.bind("<<ComboboxSelected>>", self.on_model_selected)

        self.model_hint = tk.Label(self.model_card, text="等待检测已安装模型...", font=("Microsoft YaHei UI", 9), justify="left", anchor="w")
        self.model_hint.pack(fill="x", pady=(10, 0))
        self.label_roles.append((self.model_hint, "muted"))

        self.diagnostics_card = self._make_card(self.settings_body)
        self.diagnostics_card.grid(row=0, column=1, sticky="nsew", pady=(0, 10))
        self.diagnostics_card.grid_rowconfigure(0, weight=1)
        self.diagnostics_card.grid_columnconfigure(0, weight=1)

        self.diagnostics_area = ScrollArea(self.diagnostics_card, self.palette.panel_bg)
        self.diagnostics_area.grid(row=0, column=0, sticky="nsew")
        self.scroll_areas.append(self.diagnostics_area)

        self.diagnostics_title = self._make_card_title(self.diagnostics_area.content, "环境诊断")
        self.diagnostics_title.pack(anchor="w")

        self.diagnostics_copy = self._make_card_copy(self.diagnostics_area.content, "启动依赖会在这里汇总，包括 Python、uv 和项目根目录。")
        self.diagnostics_copy.pack(anchor="w", pady=(4, 18))

        self.environment_label = tk.Label(self.diagnostics_area.content, text="", justify="left", anchor="nw", font=("Consolas", 10))
        self.environment_label.pack(fill="x")
        self.label_roles.append((self.environment_label, "text"))

        self.dependency_title = self._make_card_title(self.diagnostics_area.content, "依赖管理")
        self.dependency_title.pack(anchor="w", pady=(18, 0))

        self.dependency_copy = self._make_card_copy(self.diagnostics_area.content, "项目私有运行时和模型目录都在这里管理。")
        self.dependency_copy.pack(anchor="w", pady=(4, 14))

        runtime_action_row = tk.Frame(self.diagnostics_area.content, bg=self.palette.panel_bg)
        runtime_action_row.pack(fill="x", pady=(0, 10))
        self.surface_roles.append((runtime_action_row, "panel"))

        self.install_runtime_button = self._make_button(runtime_action_row, "安装私有 Ollama", self.install_private_ollama, style="Accent.TButton")
        self.install_runtime_button.pack(side="left", padx=(0, 10))

        self.start_runtime_button = self._make_button(runtime_action_row, "启动 Ollama", self.start_ollama_runtime, style="Ghost.TButton")
        self.start_runtime_button.pack(side="left", padx=(0, 10))

        self.stop_runtime_button = self._make_button(runtime_action_row, "停止 Ollama", self.stop_ollama_runtime, style="Ghost.TButton")
        self.stop_runtime_button.pack(side="left", padx=(0, 10))

        self.uninstall_runtime_button = self._make_button(runtime_action_row, "卸载私有运行时", self.uninstall_private_ollama, style="Danger.TButton")
        self.uninstall_runtime_button.pack(side="left")

        model_action_row = tk.Frame(self.diagnostics_area.content, bg=self.palette.panel_bg)
        model_action_row.pack(fill="x", pady=(0, 10))
        self.surface_roles.append((model_action_row, "panel"))

        self.install_models_button = self._make_button(model_action_row, "安装推荐模型", self.install_recommended_models, style="Accent.TButton")
        self.install_models_button.pack(side="left", padx=(0, 10))

        self.delete_model_button = self._make_button(model_action_row, "删除当前模型", self.delete_selected_model, style="Ghost.TButton")
        self.delete_model_button.pack(side="left", padx=(0, 10))

        self.refresh_status_button = self._make_button(model_action_row, "刷新状态", self.refresh_settings_status, style="Ghost.TButton")
        self.refresh_status_button.pack(side="left")

        self.logs_card = self._make_card(self.settings_body)
        self.logs_card.grid(row=1, column=1, sticky="nsew")
        self.logs_card.grid_rowconfigure(0, weight=1)
        self.logs_card.grid_columnconfigure(0, weight=1)

        self.logs_area = ScrollArea(self.logs_card, self.palette.panel_bg)
        self.logs_area.grid(row=0, column=0, sticky="nsew")
        self.scroll_areas.append(self.logs_area)

        self.logs_title = self._make_card_title(self.logs_area.content, "运行日志")
        self.logs_title.pack(anchor="w")

        self.logs_copy = self._make_card_copy(self.logs_area.content, "展示最近的启动器事件与客户端错误输出，方便排查运行问题。")
        self.logs_copy.pack(anchor="w", pady=(4, 14))

        self.logs_label = tk.Label(
            self.logs_area.content,
            text="",
            justify="left",
            anchor="nw",
            font=("Consolas", 10),
        )
        self.logs_label.pack(fill="x")
        self.label_roles.append((self.logs_label, "text"))

    def _make_card(self, parent: tk.Widget) -> tk.Frame:
        card = tk.Frame(
            parent,
            bg=self.palette.panel_bg,
            highlightthickness=1,
            highlightbackground=self.palette.border,
            bd=0,
            padx=20,
            pady=20,
        )
        self.surface_roles.append((card, "panel"))
        return card

    def _make_card_title(self, parent: tk.Widget, text: str) -> tk.Label:
        label = tk.Label(parent, text=text, font=("Microsoft YaHei UI", 14, "bold"), anchor="w")
        self.label_roles.append((label, "text"))
        return label

    def _make_card_copy(self, parent: tk.Widget, text: str) -> tk.Label:
        label = tk.Label(parent, text=text, font=("Microsoft YaHei UI", 10), anchor="w", justify="left")
        self.label_roles.append((label, "muted"))
        return label

    def _add_runtime_row(self, parent: tk.Frame, row: int, title: str) -> tk.Label:
        name = tk.Label(parent, text=title, anchor="w", font=("Microsoft YaHei UI", 10))
        name.grid(row=row, column=0, sticky="w", pady=6, padx=(0, 16))
        self.label_roles.append((name, "muted"))

        value = tk.Label(parent, text="检测中", anchor="w", font=("Microsoft YaHei UI", 10, "bold"), padx=10, pady=4)
        value.grid(row=row, column=1, sticky="ew", pady=6)
        return value

    def _make_button(self, parent: tk.Widget, text: str, command, style: str = "Ghost.TButton") -> RoundedButton:
        button = RoundedButton(parent, text=text, command=command, style_name=style, palette=self.palette)
        self.rounded_buttons.append(button)
        return button

    def _safe_runtime_model(self) -> str:
        try:
            if launcher_core.ollama_ready():
                init_runtime_settings()
                return get_llm_model()
        except Exception:
            pass
        return launcher_core.get_saved_llm_model()

    def _load_chat_items(self) -> list[dict[str, str]]:
        payload = self._load_desktop_state()
        items = payload.get("chat_items")
        if not isinstance(items, list):
            return []
        result: list[dict[str, str]] = []
        for item in items[-MAX_CHAT_ITEMS:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "")
            text = str(item.get("text") or "")
            time_text = str(item.get("time") or "")
            model = str(item.get("model") or "")
            if role and text:
                result.append({"role": role, "text": text, "time": time_text, "model": model})
        return result

    def _load_desktop_state(self) -> dict:
        path = launcher_core.ROOT_DIR / STATE_PATH
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as file:
                payload = json.load(file)
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _save_desktop_state(self) -> None:
        path = launcher_core.ROOT_DIR / STATE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._load_desktop_state()
        payload["chat_items"] = self.chat_items[-MAX_CHAT_ITEMS:]
        payload["theme_name"] = self.theme_name
        with path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)

    def _load_theme_name(self) -> str:
        payload = self._load_desktop_state()
        theme_name = payload.get("theme_name")
        if theme_name in PALETTES:
            return str(theme_name)
        return "dark"

    def _save_theme_name(self) -> None:
        self._save_desktop_state()

    def _save_chat_items(self) -> None:
        self._save_desktop_state()

    def _append_chat_item(self, role: str, text: str, model: str = "") -> None:
        self.chat_items.append({"role": role, "text": text, "time": now_iso(), "model": model})
        self.chat_items = self.chat_items[-MAX_CHAT_ITEMS:]
        self._save_chat_items()
        self.render_chat_history()

    def _set_model_chip(self, model_name: str) -> None:
        short_name = model_name if len(model_name) <= 28 else model_name[:25] + "..."
        self.model_chip.configure(text=f"模型: {short_name}")

    def _submit_shortcut(self, _event=None) -> str:
        self.send_question()
        return "break"

    def request_raise(self) -> None:
        self.root.after(0, self._raise_window)

    def _raise_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _run_worker(self, worker, on_success, on_error=None) -> None:
        def target() -> None:
            try:
                result = worker()
            except Exception as exc:
                self.queue.put(("error", on_error, exc))
            else:
                self.queue.put(("success", on_success, result))

        threading.Thread(target=target, daemon=True).start()

    def _schedule_queue_poll(self) -> None:
        if self.shutdown_in_progress:
            return
        try:
            self._poll_queue()
            self.root.after(120, self._schedule_queue_poll)
        except tk.TclError:
            return

    def _poll_queue(self) -> None:
        while True:
            try:
                kind, callback, payload = self.queue.get_nowait()
            except queue.Empty:
                break
            if callable(callback):
                callback(payload)
            elif kind == "error":
                self._handle_exception(payload)

    def _schedule_auto_refresh(self) -> None:
        if self.shutdown_in_progress:
            return
        if not self.refresh_in_flight and not self.model_busy:
            self.refresh_settings_status(silent=True)
        try:
            self.root.after(7000, self._schedule_auto_refresh)
        except tk.TclError:
            return

    def _handle_exception(self, exc: Exception) -> None:
        self.set_status(str(exc), tone="error")
        messagebox.showerror("Private Note 客户端", str(exc))

    def show_page(self, name: str) -> None:
        self.pages[name].tkraise()
        self.current_page = name
        if name == "settings":
            self.refresh_settings_status(silent=True)

    def back_to_main(self) -> None:
        self.show_page("main")

    def open_settings_page(self) -> None:
        self.show_page("settings")

    def open_new_note(self) -> None:
        self.editing_note_id = None
        self.editor_title_label.configure(text="新建笔记")
        self.editor_text.delete("1.0", "end")
        self.show_page("editor")
        self.editor_text.focus_set()
        self.set_status("准备写入新笔记。")

    def open_edit_note(self, note_id: int) -> None:
        note = next((item for item in self.notes_cache if item["id"] == note_id), None)
        if note is None:
            note = get_note_item(note_id)
        if note is None:
            self.set_status(f"未找到笔记 #{note_id}", tone="error")
            return
        self.editing_note_id = note_id
        self.current_note_id = note_id
        self.editor_title_label.configure(text=f"编辑笔记 #{note_id}")
        self.editor_text.delete("1.0", "end")
        self.editor_text.insert("1.0", note["content"])
        self.show_page("editor")
        self.editor_text.focus_set()
        self.render_notes()
        self.set_status(f"正在编辑笔记 #{note_id}。")

    def clear_editor(self) -> None:
        if not self.editor_text.get("1.0", "end").strip():
            return
        if messagebox.askyesno("清空内容", "确定要清空当前笔记内容吗？"):
            self.editor_text.delete("1.0", "end")
            self.set_status("已清空编辑内容。")

    def save_note(self) -> None:
        if self.note_busy:
            return
        content = self.editor_text.get("1.0", "end").strip()
        if not content:
            self.set_status("请输入笔记内容。", tone="error")
            return

        self.note_busy = True
        self.editor_save_button.state(["disabled"])
        start_text = "更新笔记并重建索引..." if self.editing_note_id is not None else "保存笔记并建立索引..."
        self.set_status(start_text)
        note_id = self.editing_note_id

        def worker():
            if note_id is None:
                return {"mode": "create", "note_id": create_note_item(content)}
            updated = update_note_item_content(note_id, content)
            return {"mode": "update", "note_id": updated["id"]}

        def on_success(payload: dict) -> None:
            self.note_busy = False
            self.editor_save_button.state(["!disabled"])
            self.current_note_id = int(payload["note_id"])
            self.editing_note_id = None
            self.load_notes(show_feedback=False)
            self.show_page("main")
            verb = "已创建" if payload["mode"] == "create" else "已更新"
            self.set_status(f"{verb}笔记 #{payload['note_id']}。", tone="success")

        def on_error(exc: Exception) -> None:
            self.note_busy = False
            self.editor_save_button.state(["!disabled"])
            self.set_status(str(exc), tone="error")
            messagebox.showerror("保存失败", str(exc))

        self._run_worker(worker, on_success, on_error)

    def confirm_delete_note(self, note_id: int) -> None:
        note = next((item for item in self.notes_cache if item["id"] == note_id), None)
        preview = summarize_note(note["content"], 64) if note else f"#{note_id}"
        if messagebox.askyesno("删除笔记", f"确定删除这条笔记吗？\n\n{preview}"):
            self.delete_note(note_id)

    def delete_note(self, note_id: int) -> None:
        if self.note_busy:
            return
        self.note_busy = True
        self.set_status(f"正在删除笔记 #{note_id}...")

        def worker():
            return delete_note_item(note_id)

        def on_success(deleted_id: int) -> None:
            self.note_busy = False
            if self.current_note_id == deleted_id:
                self.current_note_id = None
            if self.editing_note_id == deleted_id:
                self.editing_note_id = None
                self.show_page("main")
            self.load_notes(show_feedback=False)
            self.set_status(f"已删除笔记 #{deleted_id}。", tone="success")

        def on_error(exc: Exception) -> None:
            self.note_busy = False
            self.set_status(str(exc), tone="error")
            messagebox.showerror("删除失败", str(exc))

        self._run_worker(worker, on_success, on_error)

    def load_notes(self, show_feedback: bool = True) -> None:
        self.notes_cache = list_note_items()
        if self.current_note_id is None and self.notes_cache:
            self.current_note_id = int(self.notes_cache[0]["id"])
        self.note_count_label.configure(text=f"{len(self.notes_cache)} 条笔记")
        self.render_notes()
        if show_feedback:
            self.set_status("笔记列表已刷新。")

    def render_notes(self) -> None:
        self.notes_area.clear()
        if not self.notes_cache:
            empty = tk.Frame(
                self.notes_area.content,
                bg=self.palette.panel_alt,
                highlightthickness=1,
                highlightbackground=self.palette.border,
                bd=0,
                padx=16,
                pady=18,
            )
            empty.pack(fill="x", pady=(0, 10))
            title = tk.Label(empty, text="还没有笔记", font=("Microsoft YaHei UI", 12, "bold"), anchor="w")
            title.pack(anchor="w")
            copy = tk.Label(empty, text="先在左上角新建一条笔记，之后就可以直接提问。", font=("Microsoft YaHei UI", 10), anchor="w", justify="left")
            copy.pack(anchor="w", pady=(6, 0))
            title.configure(bg=self.palette.panel_alt, fg=self.palette.text)
            copy.configure(bg=self.palette.panel_alt, fg=self.palette.muted)
            return

        for note in self.notes_cache:
            active = note["id"] == self.current_note_id
            bg = self.palette.chip_bg if active else self.palette.panel_alt
            border = self.palette.accent if active else self.palette.border

            card = tk.Frame(
                self.notes_area.content,
                bg=bg,
                highlightthickness=1,
                highlightbackground=border,
                bd=0,
                padx=14,
                pady=14,
                cursor="hand2",
            )
            card.pack(fill="x", pady=(0, 10))

            title = tk.Label(
                card,
                text=first_line(note["content"], f"笔记 #{note['id']}"),
                bg=bg,
                fg=self.palette.text,
                font=("Microsoft YaHei UI", 11, "bold"),
                anchor="w",
                justify="left",
            )
            title.pack(fill="x", anchor="w")

            preview = tk.Label(
                card,
                text=summarize_note(note["content"]),
                bg=bg,
                fg=self.palette.muted,
                font=("Microsoft YaHei UI", 10),
                anchor="w",
                justify="left",
                wraplength=280,
            )
            preview.pack(fill="x", anchor="w", pady=(8, 10))

            meta = tk.Label(
                card,
                text=f"更新于 {format_time(note['created_at']) or '未知'}",
                bg=bg,
                fg=self.palette.muted,
                font=("Microsoft YaHei UI", 9),
                anchor="w",
            )
            meta.pack(fill="x", anchor="w")

            actions = tk.Frame(card, bg=bg)
            actions.pack(fill="x", pady=(12, 0))

            edit_button = self._make_button(actions, "编辑", lambda item_id=note["id"]: self.open_edit_note(item_id), style="PrimarySmall.TButton")
            edit_button.pack(side="left", padx=(0, 8))

            delete_button = self._make_button(actions, "删除", lambda item_id=note["id"]: self.confirm_delete_note(item_id), style="DangerSmall.TButton")
            delete_button.pack(side="left")

            for widget in (card, title, preview, meta):
                widget.bind("<Button-1>", lambda _event, item_id=note["id"]: self.select_note(item_id))

    def select_note(self, note_id: int) -> None:
        self.current_note_id = note_id
        self.render_notes()
        self.set_status(f"已选中笔记 #{note_id}。")

    def render_chat_history(self) -> None:
        self.chat_area.clear()
        wrap_width = max(320, min(760, self.root.winfo_width() - 620))

        if not self.chat_items:
            empty = tk.Frame(
                self.chat_area.content,
                bg=self.palette.panel_bg,
                highlightthickness=1,
                highlightbackground=self.palette.border,
                bd=0,
                padx=24,
                pady=26,
            )
            empty.pack(pady=48)
            title = tk.Label(empty, text="开始提问", bg=self.palette.panel_bg, fg=self.palette.text, font=("Microsoft YaHei UI", 16, "bold"))
            title.pack()
            copy = tk.Label(
                empty,
                text="系统会先检索本地笔记，再把结果整理成回答。",
                bg=self.palette.panel_bg,
                fg=self.palette.muted,
                font=("Microsoft YaHei UI", 10),
            )
            copy.pack(pady=(8, 0))
            return

        for item in self.chat_items:
            is_user = item["role"] == "user"
            row = tk.Frame(self.chat_area.content, bg=self.palette.panel_alt)
            row.pack(fill="x", pady=(0, 14))

            bubble_bg = self.palette.bubble_user if is_user else self.palette.bubble_assistant
            bubble = RoundedBubble(
                row,
                text=item["text"],
                fill=bubble_bg,
                fg=self.palette.text,
                outline=self.palette.border if not is_user else bubble_bg,
                wraplength=wrap_width,
                font=("Microsoft YaHei UI", 11),
                outer_bg=self.palette.panel_alt,
                radius=20,
                pad_x=16,
                pad_y=14,
            )
            bubble.pack(side="right" if is_user else "left", padx=(80, 0) if is_user else (0, 80))

            meta_text = format_time(item.get("time"))
            if not is_user:
                meta_text = f"{meta_text}  {item.get('model') or self._safe_runtime_model()}".strip()
            meta = tk.Label(
                row,
                text=meta_text,
                bg=self.palette.panel_alt,
                fg=self.palette.muted,
                font=("Microsoft YaHei UI", 9),
                anchor="e" if is_user else "w",
                justify="right" if is_user else "left",
            )
            meta.pack(fill="x", pady=(6, 0))

        self.chat_area.scroll_to_end()

    def confirm_clear_chat(self) -> None:
        if not self.chat_items:
            self.set_status("当前没有聊天记录可清空。")
            return
        if messagebox.askyesno("清空聊天", "确定要清空当前聊天记录吗？"):
            self.chat_items = []
            self._save_chat_items()
            self.render_chat_history()
            self.set_status("聊天记录已清空。", tone="success")

    def send_question(self) -> None:
        if self.chat_busy:
            return
        question = self.question_input.get("1.0", "end").strip()
        if not question:
            self.set_status("请输入问题。", tone="error")
            return

        self.chat_busy = True
        self.send_button.state(["disabled"])
        self._append_chat_item("user", question)
        self.question_input.delete("1.0", "end")
        self.set_status("正在检索笔记并生成回答...")

        def worker():
            return ask_question(question)

        def on_success(payload: dict[str, str]) -> None:
            self.chat_busy = False
            self.send_button.state(["!disabled"])
            answer = payload.get("answer") or "未返回内容"
            model = payload.get("model") or self._safe_runtime_model()
            self._set_model_chip(model)
            self._append_chat_item("assistant", answer, model=model)
            self.set_status("回答完成。", tone="success")

        def on_error(exc: Exception) -> None:
            self.chat_busy = False
            self.send_button.state(["!disabled"])
            self.set_status(str(exc), tone="error")
            messagebox.showerror("问答失败", str(exc))

        self._run_worker(worker, on_success, on_error)

    def refresh_settings_status(self, silent: bool = False) -> None:
        if self.refresh_in_flight:
            return
        self.refresh_in_flight = True
        if not silent:
            self.set_status("正在刷新运行状态...")

        def worker():
            payload = launcher_core.get_status(os.getpid())
            runtime_model = launcher_core.get_saved_llm_model()
            available_models: list[str] = []
            model_hint = "等待可用的 Ollama 运行时后读取模型列表。"
            try:
                if payload["ollama"]["running"]:
                    init_runtime_settings()
                    runtime_model = get_llm_model()
                    available_models = list_chat_models()
                    model_hint = "切换后立即热生效。"
                elif payload["ollama"]["private_installed"]:
                    model_hint = "项目私有 Ollama 已安装，但当前未运行。"
                elif payload["ollama"]["installed"]:
                    model_hint = "当前仍在兼容使用系统 Ollama，可安装私有运行时后切换。"
                else:
                    model_hint = "未检测到可用的 Ollama 运行时。"
            except Exception as exc:
                model_hint = f"模型读取失败: {exc}"
            payload["runtime_model"] = runtime_model
            payload["available_models"] = available_models
            payload["model_hint"] = model_hint
            return payload

        def on_success(payload: dict) -> None:
            self.refresh_in_flight = False
            self.settings_payload = payload
            self.apply_settings_payload(payload)
            if not silent:
                self.set_status("运行状态已更新。", tone="success")

        def on_error(exc: Exception) -> None:
            self.refresh_in_flight = False
            self.set_status(str(exc), tone="error")
            if not silent:
                messagebox.showerror("刷新失败", str(exc))

        self._run_worker(worker, on_success, on_error)

    def apply_settings_payload(self, payload: dict) -> None:
        environment = payload.get("environment", {})
        ollama_state = payload.get("ollama", {})

        self._set_model_chip(str(payload.get("runtime_model") or launcher_core.get_saved_llm_model()))
        self._set_badge(self.runtime_desktop_value, "运行中", "good")
        self._set_badge(self.runtime_ollama_value, self._describe_ollama_status(ollama_state), self._ollama_tone(ollama_state))
        self._set_badge(self.runtime_source_value, self._describe_ollama_source(ollama_state), "neutral")
        self._set_badge(self.runtime_model_value, str(payload.get("runtime_model") or "未知"), "accent")
        installed_models = list(ollama_state.get("models") or [])
        self._set_badge(self.runtime_models_value, f"{len(installed_models)} 个", "neutral")

        lines = [
            f"project_root : {environment.get('project_root') or '-'}",
            f"runtime_dir  : {environment.get('runtime_dir') or '-'}",
            f"data_dir     : {environment.get('data_dir') or '-'}",
            f"log_dir      : {environment.get('log_dir') or '-'}",
            f"python_ready : {'yes' if environment.get('python_ready') else 'no'}",
            f"python_path  : {environment.get('python_path') or '-'}",
            f"uv_available : {'yes' if environment.get('uv_available') else 'no'}",
            f"uv_path      : {environment.get('uv_path') or '-'}",
            f"ollama_path  : {ollama_state.get('path') or '-'}",
            f"models_dir   : {ollama_state.get('private_models_dir') or '-'}",
            f"ollama_url   : {ollama_state.get('base_url') or '-'}",
        ]
        self.environment_label.configure(text="\n".join(lines))

        available_models = list(payload.get("available_models") or [])
        runtime_model = str(payload.get("runtime_model") or launcher_core.get_saved_llm_model())
        model_values = available_models[:] if available_models else [runtime_model]
        if runtime_model and runtime_model not in model_values:
            model_values.insert(0, runtime_model)

        self.model_event_suppressed = True
        self.model_combo.configure(values=model_values)
        self.model_var.set(runtime_model)
        self.model_event_suppressed = False

        if available_models:
            self.model_combo.state(["!disabled", "readonly"])
        else:
            self.model_combo.state(["disabled"])
        self.model_hint.configure(text=str(payload.get("model_hint") or ""))

        self._update_runtime_buttons(ollama_state, runtime_model)
        self._render_logs(payload)

    def _render_logs(self, payload: dict) -> None:
        logs = payload.get("logs", {})
        sections = [
            ("Launcher Events", logs.get("launcher_events") or []),
            ("Ollama Stderr", logs.get("ollama_stderr") or []),
            ("Desktop Stderr", logs.get("launcher_stderr") or []),
        ]
        blocks: list[str] = []
        for title, lines in sections:
            blocks.append(f"[{title}]")
            if lines:
                blocks.extend(lines[-12:])
            else:
                blocks.append("(empty)")
            blocks.append("")
        self.logs_label.configure(text="\n".join(blocks).strip())

    def _describe_ollama_status(self, ollama_state: dict) -> str:
        if not ollama_state.get("installed"):
            return "未安装"
        return "运行中" if ollama_state.get("running") else "未运行"

    def _describe_ollama_source(self, ollama_state: dict) -> str:
        mode = str(ollama_state.get("mode") or "missing")
        if mode == "private":
            return "项目私有"
        if mode == "system":
            return "系统共享"
        return "未配置"

    def _ollama_tone(self, ollama_state: dict) -> str:
        if not ollama_state.get("installed"):
            return "danger"
        return "good" if ollama_state.get("running") else "neutral"

    def _update_runtime_buttons(self, ollama_state: dict, runtime_model: str) -> None:
        private_installed = bool(ollama_state.get("private_installed"))
        running = bool(ollama_state.get("running"))
        self._set_button_enabled(self.install_runtime_button, not private_installed and not self.runtime_busy)
        self._set_button_enabled(self.start_runtime_button, private_installed and not running and not self.runtime_busy)
        self._set_button_enabled(self.stop_runtime_button, private_installed and running and not self.runtime_busy)
        self._set_button_enabled(self.uninstall_runtime_button, private_installed and not self.runtime_busy)
        self._set_button_enabled(self.refresh_status_button, not self.runtime_busy)
        self._set_button_enabled(self.install_models_button, private_installed and not self.runtime_busy)
        self._set_button_enabled(self.delete_model_button, private_installed and running and bool(runtime_model) and not self.runtime_busy)

    def _set_button_enabled(self, button, enabled: bool) -> None:
        if enabled:
            button.state(["!disabled"])
        else:
            button.state(["disabled"])

    def _set_badge(self, label: tk.Label, text: str, tone: str) -> None:
        bg, fg = self._badge_colors(tone)
        label.configure(text=text, bg=bg, fg=fg)

    def _badge_colors(self, tone: str) -> tuple[str, str]:
        if tone == "good":
            return ("#dcfce7", "#166534") if self.theme_name == "light" else ("#052e16", "#86efac")
        if tone == "danger":
            return ("#fee2e2", "#991b1b") if self.theme_name == "light" else ("#3f0a0a", "#fecaca")
        if tone == "accent":
            return self.palette.chip_bg, self.palette.chip_fg
        return self.palette.chrome_bg, self.palette.text

    def on_model_selected(self, _event=None) -> None:
        if self.model_event_suppressed or self.model_busy:
            return
        selected = self.model_var.get().strip()
        if not selected:
            return

        self.model_busy = True
        self.model_combo.state(["disabled"])
        self.set_status(f"正在切换模型到 {selected}...")

        def worker():
            launcher_core.ensure_ollama_running()
            init_runtime_settings()
            current = set_llm_model(selected)
            return {"current_model": current, "available_models": list_chat_models()}

        def on_success(payload: dict) -> None:
            self.model_busy = False
            current_model = str(payload.get("current_model") or selected)
            self._set_model_chip(current_model)
            self.model_hint.configure(text=f"当前生效模型: {current_model}")
            self.model_event_suppressed = True
            self.model_combo.configure(values=payload.get("available_models") or [current_model])
            self.model_var.set(current_model)
            self.model_event_suppressed = False
            self.model_combo.state(["!disabled", "readonly"])
            self.set_status(f"模型已切换为 {current_model}。", tone="success")
            self.refresh_settings_status(silent=True)

        def on_error(exc: Exception) -> None:
            self.model_busy = False
            self.set_status(str(exc), tone="error")
            self.refresh_settings_status(silent=True)
            messagebox.showerror("模型切换失败", str(exc))

        self._run_worker(worker, on_success, on_error)

    def _runtime_action(self, status_text: str, worker, success_text: str) -> None:
        if self.runtime_busy:
            return
        self.runtime_busy = True
        if self.settings_payload is not None:
            self._update_runtime_buttons(self.settings_payload.get("ollama", {}), self.model_var.get().strip())
        self.set_status(status_text)

        def on_success(_payload) -> None:
            self.runtime_busy = False
            self.refresh_settings_status(silent=True)
            self.set_status(success_text, tone="success")

        def on_error(exc: Exception) -> None:
            self.runtime_busy = False
            self.refresh_settings_status(silent=True)
            self.set_status(str(exc), tone="error")
            messagebox.showerror("运行时操作失败", str(exc))

        self._run_worker(worker, on_success, on_error)

    def install_private_ollama(self) -> None:
        if not messagebox.askyesno("安装私有 Ollama", "将下载并安装项目私有 Ollama 运行时，继续吗？"):
            return
        self._runtime_action(
            "正在安装项目私有 Ollama 运行时...",
            launcher_core.install_private_ollama_runtime,
            "项目私有 Ollama 已安装。",
        )

    def start_ollama_runtime(self) -> None:
        self._runtime_action(
            "正在启动项目私有 Ollama...",
            launcher_core.start_private_ollama,
            "项目私有 Ollama 已启动。",
        )

    def stop_ollama_runtime(self) -> None:
        self._runtime_action(
            "正在停止项目私有 Ollama...",
            launcher_core.stop_private_ollama,
            "项目私有 Ollama 已停止。",
        )

    def uninstall_private_ollama(self) -> None:
        if not messagebox.askyesno("卸载私有运行时", "这会删除项目内的 Ollama 运行时文件，但不会自动删除模型目录，继续吗？"):
            return
        self._runtime_action(
            "正在卸载项目私有 Ollama 运行时...",
            launcher_core.uninstall_private_ollama_runtime,
            "项目私有 Ollama 运行时已卸载。",
        )

    def install_recommended_models(self) -> None:
        def worker():
            launcher_core.pull_model(launcher_core.DEFAULT_LLM_MODEL)
            launcher_core.pull_model(launcher_core.DEFAULT_EMBED_MODEL)
            return True

        self._runtime_action(
            "正在安装推荐模型，这可能需要一些时间...",
            worker,
            "推荐模型已安装完成。",
        )

    def delete_selected_model(self) -> None:
        model_name = self.model_var.get().strip()
        if not model_name:
            self.set_status("当前没有可删除的模型。", tone="error")
            return
        if not messagebox.askyesno("删除模型", f"确定删除当前模型 {model_name} 吗？"):
            return

        self._runtime_action(
            f"正在删除模型 {model_name}...",
            lambda: launcher_core.remove_model(model_name),
            f"模型 {model_name} 已删除。",
        )

    def toggle_theme(self) -> None:
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self.palette = PALETTES[self.theme_name]
        self._save_theme_name()
        self.apply_theme()
        self.set_status(f"已切换到{'浅色' if self.theme_name == 'light' else '深色'}模式。")

    def apply_theme(self) -> None:
        self.root.configure(bg=self.palette.root_bg)
        self._configure_styles()

        role_map = {
            "root": self.palette.root_bg,
            "panel": self.palette.panel_bg,
            "alt": self.palette.panel_alt,
            "input": self.palette.input_bg,
        }
        for widget, role in self.surface_roles:
            kwargs = {"bg": role_map.get(role, self.palette.panel_bg)}
            if isinstance(widget, tk.Frame):
                kwargs["highlightbackground"] = self.palette.border
            widget.configure(**kwargs)

        for label, role in self.label_roles:
            label.configure(bg=label.master.cget("bg"), fg=self.palette.text if role == "text" else self.palette.muted)

        for text in self.text_widgets:
            text.configure(
                bg=self.palette.input_bg,
                fg=self.palette.input_fg,
                insertbackground=self.palette.input_fg,
                highlightbackground=self.palette.border,
                highlightcolor=self.palette.border,
            )

        self.model_chip.configure(bg=self.palette.chip_bg, fg=self.palette.chip_fg)
        self.status_line.configure(bg=self.palette.root_bg)

        for scroll_area in self.scroll_areas:
            scroll_area.set_colors(self.palette.panel_alt if scroll_area is self.chat_area else self.palette.panel_bg)

        self.theme_button.configure(text="__moon__" if self.theme_name == "light" else "__sun__")
        alive_buttons: list[RoundedButton] = []
        for button in self.rounded_buttons:
            try:
                if not button.winfo_exists():
                    continue
                button.set_palette(self.palette)
                alive_buttons.append(button)
            except tk.TclError:
                continue
        self.rounded_buttons = alive_buttons
        self.load_notes(show_feedback=False)
        self.render_chat_history()
        if self.settings_payload is not None:
            self.apply_settings_payload(self.settings_payload)
        self.root.update_idletasks()
        try:
            self.root.update()
        except tk.TclError:
            pass

    def _configure_styles(self) -> None:
        self.style.configure(
            "Ghost.TButton",
            background=self.palette.chrome_bg,
            foreground=self.palette.text,
            borderwidth=0,
            focusthickness=0,
            focuscolor=self.palette.chrome_bg,
            padding=(16, 10),
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.style.map(
            "Ghost.TButton",
            background=[("active", self.palette.chrome_hover), ("disabled", self.palette.chrome_bg)],
            foreground=[("disabled", self.palette.muted)],
        )
        self.style.configure(
            "Accent.TButton",
            background=self.palette.accent,
            foreground="#ffffff",
            borderwidth=0,
            focusthickness=0,
            focuscolor=self.palette.accent,
            padding=(16, 10),
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.style.map(
            "Accent.TButton",
            background=[("active", self.palette.accent_hover), ("disabled", self.palette.chrome_bg)],
            foreground=[("disabled", self.palette.muted)],
        )
        self.style.configure(
            "Danger.TButton",
            background=self.palette.danger,
            foreground="#ffffff",
            borderwidth=0,
            focusthickness=0,
            focuscolor=self.palette.danger,
            padding=(16, 10),
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.style.map(
            "Danger.TButton",
            background=[("active", self.palette.danger_hover), ("disabled", self.palette.chrome_bg)],
            foreground=[("disabled", self.palette.muted)],
        )
        self.style.configure(
            "GhostSmall.TButton",
            background=self.palette.chrome_bg,
            foreground=self.palette.text,
            borderwidth=0,
            focusthickness=0,
            focuscolor=self.palette.chrome_bg,
            padding=(10, 6),
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.style.map(
            "GhostSmall.TButton",
            background=[("active", self.palette.chrome_hover), ("disabled", self.palette.chrome_bg)],
            foreground=[("disabled", self.palette.muted)],
        )
        self.style.configure(
            "DangerSmall.TButton",
            background=self.palette.danger,
            foreground="#ffffff",
            borderwidth=0,
            focusthickness=0,
            focuscolor=self.palette.danger,
            padding=(10, 6),
            font=("Microsoft YaHei UI", 9, "bold"),
        )
        self.style.map(
            "DangerSmall.TButton",
            background=[("active", self.palette.danger_hover), ("disabled", self.palette.chrome_bg)],
            foreground=[("disabled", self.palette.muted)],
        )
        self.style.configure(
            "Icon.TButton",
            background=self.palette.chrome_bg,
            foreground=self.palette.text,
            borderwidth=0,
            focusthickness=0,
            focuscolor=self.palette.chrome_bg,
            padding=(11, 8),
            font=("Segoe UI Symbol", 11),
        )
        self.style.map(
            "Icon.TButton",
            background=[("active", self.palette.chrome_hover), ("disabled", self.palette.chrome_bg)],
            foreground=[("disabled", self.palette.muted)],
        )
        self.style.configure(
            "FluentIcon.TButton",
            background=self.palette.chrome_bg,
            foreground=self.palette.text,
            borderwidth=0,
            focusthickness=0,
            focuscolor=self.palette.chrome_bg,
            padding=(11, 8),
            font=("Segoe MDL2 Assets", 12),
        )
        self.style.map(
            "FluentIcon.TButton",
            background=[("active", self.palette.chrome_hover), ("disabled", self.palette.chrome_bg)],
            foreground=[("disabled", self.palette.muted)],
        )
        self.style.configure(
            "App.TCombobox",
            fieldbackground=self.palette.input_bg,
            background=self.palette.input_bg,
            foreground=self.palette.input_fg,
            bordercolor=self.palette.border,
            lightcolor=self.palette.border,
            darkcolor=self.palette.border,
            arrowcolor=self.palette.text,
            padding=8,
        )
        self.style.map(
            "App.TCombobox",
            fieldbackground=[("readonly", self.palette.input_bg), ("disabled", self.palette.chrome_bg)],
            foreground=[("readonly", self.palette.input_fg), ("disabled", self.palette.muted)],
        )
        self.style.configure(
            "App.Vertical.TScrollbar",
            background=self.palette.chrome_bg,
            troughcolor=self.palette.panel_bg,
            bordercolor=self.palette.panel_bg,
            arrowcolor=self.palette.muted,
            darkcolor=self.palette.chrome_bg,
            lightcolor=self.palette.chrome_bg,
        )

    def set_status(self, text: str, tone: str = "info") -> None:
        fg = self.palette.muted
        if tone == "success":
            fg = self.palette.accent
        elif tone == "error":
            fg = self.palette.danger
        elif tone == "text":
            fg = self.palette.text
        self.status_line.configure(text=text, fg=fg)
        if self.status_clear_after_id:
            self.root.after_cancel(self.status_clear_after_id)
            self.status_clear_after_id = None
        if tone in {"success", "info"}:
            self.status_clear_after_id = self.root.after(6000, self._clear_status_if_same, text)

    def _clear_status_if_same(self, text: str) -> None:
        if self.status_line.cget("text") == text:
            self.status_line.configure(text="就绪", fg=self.palette.muted)
        self.status_clear_after_id = None

    def on_close(self) -> None:
        if self.shutdown_in_progress:
            return
        self.shutdown_in_progress = True
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)
        self.root.withdraw()
        self.server.shutdown()
        launcher_core.append_event("桌面客户端正在关闭，并清理旧版后台残留进程。")

        def worker() -> None:
            try:
                launcher_core.shutdown_project_background()
            except Exception:
                launcher_core.ensure_runtime_dirs()
                with launcher_core.DESKTOP_STDERR_LOG.open("a", encoding="utf-8") as file:
                    file.write(traceback.format_exc())
                    file.write("\n")
            finally:
                self.queue.put(("success", self._complete_close, None))

        threading.Thread(target=worker, daemon=True).start()

    def _complete_close(self, _payload=None) -> None:
        self._finalize_close()

    def _finalize_close(self) -> None:
        launcher_core.clear_launcher_pid()
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def run(self) -> None:
        self.render_chat_history()
        self.root.mainloop()


def main() -> int:
    install_excepthook()
    if notify_existing_instance():
        return 0
    client = DesktopClient()
    client.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
