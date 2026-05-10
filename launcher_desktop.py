from __future__ import annotations

import json
import os
import queue
import re
import socket
import sys
import threading
import time
import traceback
import zlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import font as tkfont
from tkinter import filedialog, messagebox, ttk

import launcher_core
from project_paths import DESKTOP_STATE_PATH
from note_service import (
    authenticate_account,
    ask_question,
    create_account_item,
    create_note_item,
    delete_note_item,
    export_note_item,
    get_note_item,
    import_note_item,
    init_storage,
    list_account_items,
    list_note_items,
    update_note_item_content,
)
from ollama_client import (
    get_embed_model,
    get_llm_model,
    init_runtime_settings,
    list_chat_models,
    list_embedding_models,
    set_embed_model,
    set_llm_model,
)


CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 18000 + (zlib.crc32(str(launcher_core.ROOT_DIR).lower().encode("utf-8")) % 1000)
STATE_PATH = DESKTOP_STATE_PATH
MAX_CHAT_ITEMS = 200
APP_ICON_ICO = launcher_core.ROOT_DIR / "packaging" / "assets" / "app.ico"
APP_ICON_PNG_PATHS = (
    launcher_core.ROOT_DIR / "icon.png",
    launcher_core.ROOT_DIR / "_internal" / "icon.png",
)
THEME_ICON_DIR_PATHS = (
    launcher_core.ROOT_DIR / "static" / "theme-icons",
    launcher_core.ROOT_DIR / "_internal" / "static" / "theme-icons",
    Path(__file__).resolve().parent / "static" / "theme-icons",
)
THEME_ICON_FILES = {
    ("__moon__", "light"): "moon-light.png",
    ("__moon__", "dark"): "moon-dark.png",
    ("__sun__", "light"): "sun-light.png",
    ("__sun__", "dark"): "sun-dark.png",
}
ACCOUNT_ROLE_LABELS = {"admin": "管理员", "member": "成员"}
ACCOUNT_ROLE_OPTIONS = (("admin", "管理员"), ("member", "成员"))


def _read_app_version() -> str:
    candidate_paths = (
        launcher_core.ROOT_DIR / "pyproject.toml",
        Path(__file__).resolve().parent / "pyproject.toml",
    )
    for path in candidate_paths:
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("version ="):
                    parts = stripped.split('"')
                    if len(parts) >= 2 and parts[1]:
                        return parts[1]
        except OSError:
            continue
    return "build"


APP_VERSION = _read_app_version()
APP_VERSION_DISPLAY = f"v{APP_VERSION}" if APP_VERSION != "build" else "LOCAL BUILD"
APP_WINDOW_GEOMETRY = "1440x920"
APP_WINDOW_MIN_SIZE = (1200, 760)
LOGIN_CARD_WIDTH = 470
LOGIN_CARD_HEIGHT = 540
LOGIN_WINDOW_GEOMETRY = f"{LOGIN_CARD_WIDTH}x{LOGIN_CARD_HEIGHT}"
LOGIN_WINDOW_MIN_SIZE = (LOGIN_CARD_WIDTH, LOGIN_CARD_HEIGHT)
LOGIN_FORM_WIDTH = LOGIN_CARD_WIDTH - 58


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


def compose_note_document(title: str, body: str) -> str:
    title_text = str(title or "").strip()
    body_text = str(body or "").strip()
    if title_text and body_text:
        return f"{title_text}\n{body_text}"
    return title_text or body_text


def note_title_text(note: dict, fallback: str = "未命名笔记") -> str:
    title = str(note.get("title") or "").strip()
    if title:
        return title
    for line in str(note.get("content") or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return fallback


def note_body_text(note: dict) -> str:
    body = str(note.get("body") or "")
    if body:
        return body
    content = str(note.get("content") or "")
    lines = content.splitlines()
    found_title = False
    body_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not found_title and stripped:
            found_title = True
            continue
        body_lines.append(line)
    return "\n".join(body_lines).strip("\n")


def note_preview_text(note: dict, limit: int = 110) -> str:
    return summarize_note(note_body_text(note), limit=limit)


def note_export_filename(note: dict) -> str:
    title = note_title_text(note, "note")
    safe_title = re.sub(r'[\\/:*?"<>|]+', "_", title).strip(" ._")
    safe_title = safe_title or "note"
    return f"{safe_title}.pnote.json"


def search_terms(query: str) -> list[str]:
    raw_terms = re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{1,}", str(query or "").lower())
    seen: set[str] = set()
    terms: list[str] = []
    for term in sorted(raw_terms, key=len, reverse=True):
        if not term or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def excerpt_with_query(text: str, query: str, limit: int = 120) -> str:
    content = " ".join(str(text or "").split())
    if not content:
        return ""
    lowered = content.lower()
    for term in search_terms(query):
        index = lowered.find(term)
        if index < 0:
            continue
        start = max(0, index - limit // 2)
        end = min(len(content), index + len(term) + limit // 2)
        snippet = content[start:end].strip()
        if start > 0:
            snippet = "..." + snippet
        if end < len(content):
            snippet = snippet + "..."
        return snippet
    return summarize_note(content, limit=limit)


def account_role_label(role: str | None) -> str:
    return ACCOUNT_ROLE_LABELS.get(str(role or "").strip().lower(), "成员")


def account_role_value(label: str | None) -> str:
    normalized = str(label or "").strip()
    for value, display in ACCOUNT_ROLE_OPTIONS:
        if normalized in {value, display}:
            return value
    return "member"


def theme_name_from_palette(palette: Palette) -> str:
    for theme_name, known_palette in PALETTES.items():
        if known_palette == palette:
            return theme_name
    return "light"


def resolve_theme_icon_path(icon_name: str, theme_name: str) -> Path | None:
    file_name = THEME_ICON_FILES.get((icon_name, theme_name))
    if not file_name:
        return None
    for base_dir in THEME_ICON_DIR_PATHS:
        candidate = base_dir / file_name
        if candidate.exists():
            return candidate
    return None


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    text = value.lstrip("#")
    if len(text) != 6:
        raise ValueError(f"Unsupported color: {value}")
    return tuple(int(text[index : index + 2], 16) for index in (0, 2, 4))


def _rgb_to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def blend_hex(color_a: str, color_b: str, ratio: float) -> str:
    ratio = max(0.0, min(1.0, ratio))
    a = _hex_to_rgb(color_a)
    b = _hex_to_rgb(color_b)
    mixed = tuple(int(round(a[idx] + (b[idx] - a[idx]) * ratio)) for idx in range(3))
    return _rgb_to_hex(mixed)


def is_embedding_model_entry(model_info: dict[str, object]) -> bool:
    name = str(model_info.get("name", "")).lower()
    details = model_info.get("details") or {}
    if not isinstance(details, dict):
        details = {}
    family = str(details.get("family", "")).lower()
    families = details.get("families") or []
    family_text = " ".join(str(item).lower() for item in families)
    haystack = f"{name} {family} {family_text}"
    return "embed" in haystack or "bert" in haystack


def format_model_size(size_value: object) -> str:
    try:
        size = float(size_value)
    except (TypeError, ValueError):
        return "-"
    if size <= 0:
        return "-"
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    return f"{size:.1f}{units[unit_index]}"


def estimate_model_requirements(model_info: dict[str, object]) -> dict[str, str]:
    size_gb = 0.0
    try:
        size_gb = float(model_info.get("size") or 0.0) / (1024 ** 3)
    except (TypeError, ValueError):
        size_gb = 0.0
    embedding = is_embedding_model_entry(model_info)

    if embedding:
        ram_text = "建议 4-8GB 内存"
        gpu_text = "CPU 即可，GPU 非必需"
        perf_text = "索引/检索较轻，适合常驻"
        formula_text = "规则：若识别为向量/Embedding 模型，则直接归入轻量档，不强制依赖 GPU。"
    elif size_gb <= 1.0:
        ram_text = "建议 4-8GB 内存"
        gpu_text = "CPU 可用，入门 GPU 更顺"
        perf_text = "轻量模型，响应较快"
        formula_text = "规则：模型大小 <= 1GB，归入轻量档。"
    elif size_gb <= 6.0:
        ram_text = "建议 8-16GB 内存"
        gpu_text = "CPU 可跑，8GB+ 显存更合适"
        perf_text = "中等模型，质量和速度平衡"
        formula_text = "规则：1GB < 模型大小 <= 6GB，归入中等档。"
    elif size_gb <= 12.0:
        ram_text = "建议 16-24GB 内存"
        gpu_text = "建议 12GB+ 显存"
        perf_text = "偏重模型，CPU 速度通常明显变慢"
        formula_text = "规则：6GB < 模型大小 <= 12GB，归入偏重档。"
    elif size_gb <= 24.0:
        ram_text = "建议 24-32GB 内存"
        gpu_text = "建议 16GB+ 显存"
        perf_text = "重型模型，优先 GPU"
        formula_text = "规则：12GB < 模型大小 <= 24GB，归入重型档。"
    else:
        ram_text = "建议 32GB+ 内存"
        gpu_text = "建议 24GB+ 显存"
        perf_text = "超大模型，硬件要求较高"
        formula_text = "规则：模型大小 > 24GB，归入超大档。"

    return {
        "ram": ram_text,
        "gpu": gpu_text,
        "performance": perf_text,
        "formula": formula_text,
        "note": "估算基于模型体积、类型和常见本地部署经验，不代表官方硬性要求。",
    }


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


class VerticalScrollbar(tk.Canvas):
    def __init__(self, parent, command, bg: str, palette: Palette, width: int = 18):
        super().__init__(
            parent,
            width=width,
            highlightthickness=0,
            bd=0,
            relief="flat",
            bg=bg,
            cursor="hand2",
        )
        self.command = command
        self.arrow_size = 16
        self.min_thumb_height = 40
        self.first = 0.0
        self.last = 1.0
        self.drag_offset = 0.0
        self.dragging = False
        self.hover_part: str | None = None
        self.pressed_part: str | None = None
        self._set_palette(bg, palette)
        self.bind("<Configure>", self._redraw, add="+")
        self.bind("<Motion>", self._on_motion, add="+")
        self.bind("<Leave>", self._on_leave, add="+")
        self.bind("<ButtonPress-1>", self._on_press, add="+")
        self.bind("<ButtonRelease-1>", self._on_release, add="+")
        self.bind("<B1-Motion>", self._on_drag, add="+")

    def _set_palette(self, bg: str, palette: Palette) -> None:
        self.base_bg = bg
        self.thumb_color = "#8da4bf" if palette.root_bg == PALETTES["light"].root_bg else "#768ca7"
        self.thumb_active = "#6f8fb6" if palette.root_bg == PALETTES["light"].root_bg else "#9bb3cf"
        self.arrow_color = "#657a92" if palette.root_bg == PALETTES["light"].root_bg else "#90a8c4"
        self.arrow_active = palette.text
        self.configure(bg=bg)

    def configure_colors(self, bg: str, palette: Palette) -> None:
        self._set_palette(bg, palette)
        self._redraw()

    def set(self, first, last) -> None:
        try:
            self.first = max(0.0, min(1.0, float(first)))
            self.last = max(0.0, min(1.0, float(last)))
        except (TypeError, ValueError):
            self.first = 0.0
            self.last = 1.0
        self._redraw()

    def _track_bounds(self) -> tuple[float, float]:
        top = self.arrow_size + 8
        bottom = max(top + self.min_thumb_height, self.winfo_height() - self.arrow_size - 8)
        return top, bottom

    def _thumb_bounds(self) -> tuple[float, float, float, float]:
        width = max(self.winfo_width(), 1)
        top, bottom = self._track_bounds()
        travel = max(1.0, bottom - top)
        visible = max(0.0, min(1.0, self.last - self.first))
        if visible >= 0.999:
            return width * 0.25, top, width * 0.75, top
        thumb_height = max(self.min_thumb_height, travel * visible)
        max_first = max(1e-9, 1.0 - visible)
        position = (self.first / max_first) * max(0.0, travel - thumb_height)
        y1 = top + position
        y2 = y1 + thumb_height
        return width * 0.22, y1, width * 0.78, y2

    def _hit_test(self, x: float, y: float) -> str | None:
        width = self.winfo_width()
        height = self.winfo_height()
        if y <= self.arrow_size + 6:
            return "up"
        if y >= height - self.arrow_size - 6:
            return "down"
        x1, y1, x2, y2 = self._thumb_bounds()
        if y2 > y1 and x1 <= x <= x2 and y1 <= y <= y2:
            return "thumb"
        return None

    def _on_motion(self, event) -> None:
        self.hover_part = self._hit_test(event.x, event.y)
        if not self.dragging:
            self._redraw()

    def _on_leave(self, _event=None) -> None:
        self.hover_part = None
        if not self.dragging:
            self._redraw()

    def _on_press(self, event) -> None:
        part = self._hit_test(event.x, event.y)
        self.pressed_part = part
        if part == "up":
            self.command("scroll", -1, "units")
        elif part == "down":
            self.command("scroll", 1, "units")
        elif part == "thumb":
            _x1, y1, _x2, _y2 = self._thumb_bounds()
            self.drag_offset = event.y - y1
            self.dragging = True
        self._redraw()

    def _on_release(self, _event=None) -> None:
        self.dragging = False
        self.pressed_part = None
        self._redraw()

    def _on_drag(self, event) -> None:
        if not self.dragging:
            return
        _x1, y1, _x2, y2 = self._thumb_bounds()
        thumb_height = max(1.0, y2 - y1)
        top, bottom = self._track_bounds()
        travel = max(1.0, bottom - top)
        visible = max(0.0, min(1.0, self.last - self.first))
        max_first = max(1e-9, 1.0 - visible)
        max_offset = max(1.0, travel - thumb_height)
        thumb_top = min(max(top, event.y - self.drag_offset), top + max_offset)
        position_ratio = (thumb_top - top) / max_offset
        self.command("moveto", position_ratio * max_first)

    def _draw_arrow(self, center_x: float, center_y: float, direction: str, color: str) -> None:
        span = 5
        if direction == "up":
            points = [
                center_x,
                center_y - span,
                center_x - span,
                center_y + span - 1,
                center_x + span,
                center_y + span - 1,
            ]
        else:
            points = [
                center_x,
                center_y + span,
                center_x - span,
                center_y - span + 1,
                center_x + span,
                center_y - span + 1,
            ]
        self.create_polygon(points, fill=color, outline=color)

    def _redraw(self, _event=None) -> None:
        self.delete("all")
        width = max(self.winfo_width(), 1)
        height = max(self.winfo_height(), 1)
        self.configure(bg=self.base_bg)

        up_color = self.arrow_active if self.hover_part == "up" or self.pressed_part == "up" else self.arrow_color
        down_color = self.arrow_active if self.hover_part == "down" or self.pressed_part == "down" else self.arrow_color
        self._draw_arrow(width / 2, self.arrow_size / 2 + 2, "up", up_color)
        self._draw_arrow(width / 2, height - self.arrow_size / 2 - 2, "down", down_color)

        x1, y1, x2, y2 = self._thumb_bounds()
        if y2 <= y1:
            return
        thumb_color = self.thumb_active if self.hover_part == "thumb" or self.dragging else self.thumb_color
        self.create_rectangle(x1, y1, x2, y2, fill=thumb_color, outline=thumb_color, width=0)


class ScrollArea(tk.Frame):
    def __init__(self, parent, bg: str, palette: Palette):
        super().__init__(parent, bg=bg, highlightthickness=0, bd=0)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.scrollbar = VerticalScrollbar(self, command=self.canvas.yview, bg=bg, palette=palette)
        self.content = tk.Frame(self.canvas, bg=bg, highlightthickness=0, bd=0)
        self.window_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y", padx=(8, 0))
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
            if self.canvas.yview() == (0.0, 1.0):
                return
            steps = max(1, int(abs(event.delta) / 120))
            direction = -1 if event.delta > 0 else 1
            self.canvas.yview_scroll(direction * steps, "units")
        except tk.TclError:
            pass

    def clear(self) -> None:
        for child in self.content.winfo_children():
            child.destroy()

    def scroll_to_end(self) -> None:
        self.update_idletasks()
        self.canvas.yview_moveto(1.0)

    def set_colors(self, bg: str, palette: Palette) -> None:
        self.configure(bg=bg)
        self.canvas.configure(bg=bg)
        self.content.configure(bg=bg)
        self.scrollbar.configure_colors(bg, palette)


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
        self._icon_image: tk.PhotoImage | None = None
        self._icon_asset_key: tuple[str, str, str] | None = None
        self._disabled = False
        self._hovered = False
        self._pressed = False
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._redraw()

    def _metrics(self) -> tuple[tuple[str, int, str], int, int, int]:
        if self.style_name == "Icon.TButton" and self._text in {"__moon__", "__sun__", "__trash__", "__send__"}:
            return (("Segoe UI Symbol", 16, "normal"), 10, 10, 20)
        if self.style_name == "ModeIcon.TButton":
            return (("Microsoft YaHei UI", 10, "bold"), 10, 10, 20)
        if self.style_name == "LoginAccent.TButton":
            return (("Microsoft YaHei UI", 11, "bold"), 26, 15, 24)
        if self.style_name == "LoginGhost.TButton":
            return (("Microsoft YaHei UI", 11, "bold"), 26, 15, 24)
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

        if self.style_name == "Icon.TButton" and self._text == "__send__":
            fill = self.palette.accent_hover if self._hovered or self._pressed else self.palette.accent
            return fill, fill, "#ffffff"
        if self.style_name in {"Accent.TButton", "LoginAccent.TButton"}:
            fill = self.palette.accent_hover if self._hovered or self._pressed else self.palette.accent
            return fill, fill, "#ffffff"
        if self.style_name in {"PrimarySmall.TButton"}:
            fill = self.palette.accent_hover if self._hovered or self._pressed else self.palette.accent
            return fill, fill, "#ffffff"
        if self.style_name in {"Danger.TButton", "DangerSmall.TButton"}:
            fill = self.palette.danger_hover if self._hovered or self._pressed else self.palette.danger
            return fill, fill, "#ffffff"
        if self.style_name == "ModeIcon.TButton":
            if self._text == "GPU":
                fill = self.palette.chip_bg
                return fill, fill, self.palette.chip_fg
            fill = self.palette.chrome_hover if self._hovered or self._pressed else self.palette.chrome_bg
            return fill, self.palette.border, self.palette.text

        fill = self.palette.chrome_hover if self._hovered or self._pressed else self.palette.chrome_bg
        return fill, self.palette.border, self.palette.text

    def _redraw(self) -> None:
        font, pad_x, pad_y, radius = self._metrics()
        if self.style_name == "Icon.TButton" and self._text in {"__moon__", "__sun__", "__trash__"}:
            width = 48
            height = 42
        elif self.style_name == "ModeIcon.TButton":
            width = 48
            height = 42
        elif self.style_name == "Icon.TButton" and self._text == "__send__":
            width = 44
            height = 44
            radius = 22
        else:
            measure = tkfont.Font(font=font)
            text_width = measure.measure(self._text)
            text_height = measure.metrics("linespace")
            width = text_width + pad_x * 2
            height = text_height + pad_y * 2
        if self.style_name == "LoginAccent.TButton":
            width = max(width, LOGIN_FORM_WIDTH)
            height = max(height, 58)
        elif self.style_name == "LoginGhost.TButton":
            width = max(width, 148)
            height = max(height, 58)
        if self.style_name in {"Icon.TButton", "FluentIcon.TButton", "ModeIcon.TButton"}:
            width = max(width, height)

        fill, outline, fg = self._colors()
        self.configure(width=width, height=height, bg=self.master.cget("bg"))
        self.delete("all")
        points = _rounded_polygon_points(1, 1, width - 1, height - 1, radius)
        self.create_polygon(points, smooth=True, splinesteps=32, fill=fill, outline=outline, width=1)
        if self.style_name == "Icon.TButton" and self._text in {"__moon__", "__sun__", "__trash__", "__send__"}:
            if self._text == "__moon__":
                if self._draw_theme_icon_asset(width, height):
                    return
                self._draw_moon_icon(width, height, fg, fill)
            elif self._text == "__trash__":
                self._draw_trash_icon(width, height, fg)
            elif self._text == "__send__":
                self._draw_send_icon(width, height, fg)
            else:
                if self._draw_theme_icon_asset(width, height):
                    return
                self._draw_sun_icon(width, height, fg)
            return
        self.create_text(width / 2, height / 2, text=self._text, fill=fg, font=font)

    def _draw_theme_icon_asset(self, width: int, height: int) -> bool:
        theme_name = theme_name_from_palette(self.palette)
        asset_path = resolve_theme_icon_path(self._text, theme_name)
        if asset_path is None:
            self._icon_image = None
            self._icon_asset_key = None
            return False

        cache_key = (self._text, theme_name, str(asset_path))
        if self._icon_asset_key != cache_key or self._icon_image is None:
            try:
                self._icon_image = tk.PhotoImage(master=self, file=str(asset_path))
                self._icon_asset_key = cache_key
            except (OSError, tk.TclError):
                self._icon_image = None
                self._icon_asset_key = None
                return False

        self.create_image(width / 2, height / 2, image=self._icon_image)
        return True

    def _draw_moon_icon(self, width: int, height: int, fg: str, fill: str) -> None:
        points = [
            width * 0.60,
            height * 0.08,
            width * 0.46,
            height * 0.05,
            width * 0.27,
            height * 0.14,
            width * 0.13,
            height * 0.35,
            width * 0.10,
            height * 0.64,
            width * 0.22,
            height * 0.87,
            width * 0.50,
            height * 0.96,
            width * 0.86,
            height * 0.82,
            width * 0.76,
            height * 0.73,
            width * 0.52,
            height * 0.82,
            width * 0.31,
            height * 0.69,
            width * 0.24,
            height * 0.41,
            width * 0.32,
            height * 0.17,
            width * 0.49,
            height * 0.09,
        ]
        self.create_polygon(
            points,
            fill=fg,
            outline="",
            smooth=True,
            splinesteps=48,
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

    def _draw_send_icon(self, width: int, height: int, fg: str) -> None:
        cx = width / 2
        top_y = height * 0.24
        head_y = height * 0.39
        stem_bottom = height * 0.69
        self.create_line(
            cx,
            stem_bottom,
            cx,
            top_y + 2,
            fill=fg,
            width=2.6,
            capstyle=tk.ROUND,
            joinstyle=tk.ROUND,
        )
        self.create_line(
            cx,
            top_y,
            cx - width * 0.14,
            head_y,
            fill=fg,
            width=2.6,
            capstyle=tk.ROUND,
            joinstyle=tk.ROUND,
        )
        self.create_line(
            cx,
            top_y,
            cx + width * 0.14,
            head_y,
            fill=fg,
            width=2.6,
            capstyle=tk.ROUND,
            joinstyle=tk.ROUND,
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

    def set_colors(self, *, fill: str, fg: str, outline: str, outer_bg: str) -> None:
        self.fill = fill
        self.fg = fg
        self.outline = outline
        self.configure(bg=outer_bg)
        self._redraw()


class ChatAvatar(tk.Canvas):
    def __init__(self, parent, *, kind: str, palette: Palette, outer_bg: str, size: int = 42):
        super().__init__(
            parent,
            width=size,
            height=size,
            highlightthickness=0,
            bd=0,
            relief="flat",
            bg=outer_bg,
        )
        self.kind = kind
        self.palette = palette
        self.outer_bg = outer_bg
        self.size = size
        self._redraw()

    def set_palette(self, palette: Palette, outer_bg: str) -> None:
        self.palette = palette
        self.outer_bg = outer_bg
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        self.configure(bg=self.outer_bg)
        if self.kind == "user":
            self._draw_user()
            return
        self._draw_assistant()

    def _draw_user(self) -> None:
        stroke = self.palette.accent
        cx = self.size / 2
        head_bottom = 24
        self.create_oval(
            cx - 9,
            6,
            cx + 9,
            head_bottom,
            outline=stroke,
            width=2.2,
        )
        self.create_line(cx - 6, head_bottom - 1, cx - 17, self.size - 5, fill=stroke, width=2.2, capstyle=tk.ROUND)
        self.create_line(cx + 6, head_bottom - 1, cx + 17, self.size - 5, fill=stroke, width=2.2, capstyle=tk.ROUND)

    def _draw_assistant(self) -> None:
        page_fill = self.palette.panel_bg
        outline = self.palette.accent
        face = self.palette.text
        left = 7
        top = 5
        right = self.size - 7
        bottom = self.size - 5
        fold = 9
        self.create_polygon(
            left,
            top,
            right - fold,
            top,
            right,
            top + fold,
            right,
            bottom,
            left,
            bottom,
            fill=page_fill,
            outline=outline,
            width=2,
            joinstyle=tk.ROUND,
        )
        self.create_line(right - fold, top, right - fold, top + fold, fill=outline, width=2)
        self.create_line(right - fold, top + fold, right, top + fold, fill=outline, width=2)
        self.create_oval(left + 10, top + 11, left + 13, top + 14, fill=face, outline=face)
        self.create_oval(left + 21, top + 11, left + 24, top + 14, fill=face, outline=face)
        self.create_arc(
            left + 10,
            top + 14,
            left + 25,
            top + 25,
            start=200,
            extent=140,
            style=tk.ARC,
            outline=face,
            width=2,
        )


class RoundedPanel(tk.Canvas):
    def __init__(
        self,
        parent,
        *,
        fill: str,
        outline: str,
        outer_bg: str,
        radius: int = 22,
        pad_x: int = 16,
        pad_y: int = 16,
        cursor: str = "",
    ):
        super().__init__(parent, highlightthickness=0, bd=0, relief="flat", bg=outer_bg, cursor=cursor)
        self.fill = fill
        self.outline = outline
        self.radius = radius
        self.pad_x = pad_x
        self.pad_y = pad_y
        self.content = tk.Frame(self, bg=fill, highlightthickness=0, bd=0, cursor=cursor)
        self.window_id = self.create_window((pad_x, pad_y), window=self.content, anchor="nw")
        self.bind("<Configure>", self._sync_layout, add="+")
        self.content.bind("<Configure>", self._sync_layout, add="+")
        self._sync_layout()

    def _sync_layout(self, _event=None) -> None:
        width = self.winfo_width()
        if width <= 1:
            width = self.content.winfo_reqwidth() + self.pad_x * 2
        inner_width = max(1, width - self.pad_x * 2)
        height = max(self.content.winfo_reqheight() + self.pad_y * 2, self.pad_y * 2 + 2)
        self.coords(self.window_id, self.pad_x, self.pad_y)
        self.itemconfigure(self.window_id, width=inner_width)
        self.configure(height=height)
        self._redraw(width, height)

    def _redraw(self, width: int, height: int) -> None:
        self.delete("panel_shape")
        points = _rounded_polygon_points(1, 1, width - 1, height - 1, self.radius)
        shape_id = self.create_polygon(
            points,
            smooth=True,
            splinesteps=36,
            fill=self.fill,
            outline=self.outline,
            width=1,
            tags="panel_shape",
        )
        self.tag_lower(shape_id, self.window_id)

    def set_colors(self, *, fill: str, outline: str, outer_bg: str) -> None:
        self.fill = fill
        self.outline = outline
        self.configure(bg=outer_bg)
        self.content.configure(bg=fill)
        self._sync_layout()


class DesktopClient:
    def __init__(self) -> None:
        self.theme_name = self._load_theme_name()
        self.palette = PALETTES[self.theme_name]
        self.root = tk.Tk()
        self.root.title("Private Note 客户端")
        self.root.geometry(LOGIN_WINDOW_GEOMETRY)
        self.root.minsize(*LOGIN_WINDOW_MIN_SIZE)
        self.root.configure(bg=self.palette.root_bg)
        self.root.withdraw()
        self._window_icon_image: tk.PhotoImage | None = None
        self._apply_window_icon()

        self.style = ttk.Style(self.root)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

        self.queue: queue.Queue[tuple[str, object, object]] = queue.Queue()
        self.notes_cache: list[dict] = []
        self.chat_items: list[dict[str, object]] = []
        self.note_visuals: dict[int, dict] = {}
        self.chat_visuals: list[dict] = []
        self.current_account: dict | None = None
        self.accounts_cache: list[dict] = []
        self.last_username = self._load_last_username()
        self.settings_payload: dict | None = None
        self.badge_states: dict[tk.Label, tuple[str, str]] = {}
        self.current_page = "login"
        self.current_note_id: int | None = None
        self.editing_note_id: int | None = None
        self.editor_mode = "preview"
        self.editor_dirty = False
        self.editor_loaded_signature = ("", "")
        self.editor_pending_action = None
        self.editor_search_window: tk.Toplevel | None = None
        self.editor_search_var = tk.StringVar()
        self.editor_search_results_area: ScrollArea | None = None
        self.editor_highlight_query = ""
        self.refresh_in_flight = False
        self.chat_busy = False
        self.note_busy = False
        self.account_busy = False
        self.model_busy = False
        self.runtime_busy = False
        self.model_event_suppressed = False
        self.embed_model_event_suppressed = False
        self.hovered_note_id: int | None = None
        self.status_clear_after_id: str | None = None
        self.shutdown_in_progress = False
        self.window_mode = ""
        self.members_window: tk.Toplevel | None = None
        self.members_list_area: ScrollArea | None = None
        self.members_window_title_label: tk.Label | None = None
        self.members_window_copy_label: tk.Label | None = None
        self.model_manager_window: tk.Toplevel | None = None
        self.model_manager_area: ScrollArea | None = None
        self.model_manager_status_label: tk.Label | None = None
        self.model_manager_feedback_label: tk.Label | None = None
        self.model_manager_source_label: tk.Label | None = None
        self.model_catalog_entries: list[dict[str, object]] = []
        self.model_catalog_source = ""
        self.model_catalog_refreshing = False
        self.model_operations: dict[str, dict[str, object]] = {}
        self.model_task_items = self._load_model_tasks()
        self.model_detail_window: tk.Toplevel | None = None
        self.manage_models_button = None
        self.dependency_manager_window: tk.Toplevel | None = None
        self.dependency_manager_list_area: ScrollArea | None = None
        self.dependency_manager_title_label: tk.Label | None = None
        self.dependency_manager_copy_label: tk.Label | None = None
        self.dependency_manager_status_label: tk.Label | None = None
        self.dependency_refresh_button = None
        self.dependency_entries: list[dict[str, object]] = []
        self.dependency_progress_widgets: dict[str, dict[str, tk.Widget]] = {}
        self.dependency_last_status_text = ""

        self.surface_roles: list[tuple[tk.Widget, str]] = []
        self.label_roles: list[tuple[tk.Label, str]] = []
        self.text_widgets: list[tk.Text] = []
        self.entry_widgets: list[tk.Entry] = []
        self.scroll_areas: list[ScrollArea] = []
        self.rounded_buttons: list[RoundedButton] = []
        self.skeleton_roles: list[tuple[tk.Widget, str]] = []

        launcher_core.ensure_runtime_dirs()
        launcher_core.register_launcher_pid(os.getpid())
        launcher_core.append_event("桌面客户端已启动。")
        init_storage()
        self.auth_mode = "login"
        self.password_peek_active = False

        self.server = ControlSocketServer(self.request_raise)
        self.server.start()

        self.root.option_add("*tearOff", False)
        self.root.bind("<Control-Return>", self._submit_shortcut)
        self.root.bind("<Control-s>", self._save_shortcut)
        self.root.bind("<Control-S>", self._save_shortcut)
        self.root.bind("<Control-f>", self._open_search_shortcut)
        self.root.bind("<Control-F>", self._open_search_shortcut)

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
        for candidate in APP_ICON_PNG_PATHS:
            if not candidate.exists():
                continue
            try:
                self._window_icon_image = tk.PhotoImage(file=str(candidate))
                self.root.iconphoto(True, self._window_icon_image)
                break
            except tk.TclError:
                self._window_icon_image = None

    def _build_ui(self) -> None:
        self.shell_padding = 18
        self.shell = tk.Frame(
            self.root,
            bg=self.palette.root_bg,
            padx=self.shell_padding,
            pady=self.shell_padding,
        )
        self.shell.pack(fill="both", expand=True)
        self.surface_roles.append((self.shell, "root"))

        self.page_container = tk.Frame(self.shell, bg=self.palette.root_bg)
        self.page_container.pack(fill="both", expand=True)
        self.surface_roles.append((self.page_container, "root"))

        self.pages: dict[str, tk.Frame] = {}
        self._build_login_page()
        self._build_main_page()
        self._build_editor_page()
        self._build_settings_page()
        self._refresh_auth_view()
        self._update_account_context()
        self.show_page("login")

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
        if self.current_page == "login":
            self.status_line.pack_forget()

    def _build_login_page(self) -> None:
        page = tk.Frame(self.page_container, bg=self.palette.panel_bg)
        page.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.pages["login"] = page
        self.surface_roles.append((page, "panel"))

        shell = tk.Frame(page, bg=self.palette.panel_bg, padx=0, pady=0)
        shell.pack(fill="both", expand=True)
        self.surface_roles.append((shell, "panel"))

        card = tk.Frame(
            shell,
            bg=self.palette.panel_bg,
            highlightthickness=1,
            highlightbackground=self.palette.border,
            bd=0,
            padx=28,
            pady=28,
        )
        card.pack(fill="both", expand=True)
        self.surface_roles.append((card, "panel"))
        self.login_card = card

        self.launcher_badge_label = tk.Label(
            card,
            text="PRIVATE NOTE",
            font=("Bahnschrift SemiBold", 30, "bold"),
            anchor="w",
        )
        self.launcher_badge_label.pack(anchor="w")
        self.label_roles.append((self.launcher_badge_label, "text"))

        self.auth_title_label = tk.Label(card, text="登录账号", font=("Microsoft YaHei UI", 26, "bold"), anchor="w")
        self.auth_title_label.pack(anchor="w", pady=(20, 0))
        self.label_roles.append((self.auth_title_label, "text"))

        self.auth_form = tk.Frame(card, bg=self.palette.panel_bg)
        self.auth_form.pack(fill="x", pady=(24, 0))
        self.surface_roles.append((self.auth_form, "panel"))

        self.auth_username_var = tk.StringVar()
        self.auth_password_var = tk.StringVar()

        self.auth_username_row = tk.Frame(self.auth_form, bg=self.palette.panel_bg)
        self.auth_username_row.pack(fill="x", pady=(0, 12))
        self.surface_roles.append((self.auth_username_row, "panel"))
        self.auth_username_label = tk.Label(self.auth_username_row, text="用户名", font=("Microsoft YaHei UI", 10), anchor="w")
        self.auth_username_label.pack(anchor="w", pady=(0, 6))
        self.label_roles.append((self.auth_username_label, "muted"))
        self.auth_username_entry = tk.Entry(
            self.auth_username_row,
            textvariable=self.auth_username_var,
            relief="flat",
            bd=0,
            highlightthickness=1,
            font=("Microsoft YaHei UI", 11),
        )
        self.auth_username_entry.pack(fill="x", ipady=10)
        self.entry_widgets.append(self.auth_username_entry)

        self.auth_password_row = tk.Frame(self.auth_form, bg=self.palette.panel_bg)
        self.auth_password_row.pack(fill="x", pady=(0, 16))
        self.surface_roles.append((self.auth_password_row, "panel"))
        self.auth_password_label = tk.Label(self.auth_password_row, text="密码", font=("Microsoft YaHei UI", 10), anchor="w")
        self.auth_password_label.pack(anchor="w", pady=(0, 6))
        self.label_roles.append((self.auth_password_label, "muted"))
        self.auth_password_input_row = tk.Frame(self.auth_password_row, bg=self.palette.panel_bg)
        self.auth_password_input_row.pack(fill="x")
        self.surface_roles.append((self.auth_password_input_row, "panel"))
        self.auth_password_shell = tk.Frame(
            self.auth_password_input_row,
            bg=self.palette.input_bg,
            highlightthickness=1,
            highlightbackground=self.palette.border,
            bd=0,
        )
        self.auth_password_shell.pack(fill="x")
        self.surface_roles.append((self.auth_password_shell, "input"))
        self.auth_password_entry = tk.Entry(
            self.auth_password_shell,
            textvariable=self.auth_password_var,
            relief="flat",
            bd=0,
            highlightthickness=0,
            font=("Microsoft YaHei UI", 11),
            show="*",
        )
        self.auth_password_entry.pack(side="left", fill="x", expand=True, padx=(12, 0), ipady=10)
        self.entry_widgets.append(self.auth_password_entry)
        self.auth_password_peek_canvas = tk.Canvas(
            self.auth_password_shell,
            width=36,
            height=34,
            highlightthickness=0,
            bd=0,
            relief="flat",
            cursor="hand2",
        )
        self.auth_password_peek_canvas.pack(side="left", padx=(0, 10), pady=4)
        self.auth_password_peek_canvas.bind("<ButtonPress-1>", self._on_password_peek_press, add="+")
        self.auth_password_peek_canvas.bind("<ButtonRelease-1>", self._on_password_peek_release, add="+")
        self.auth_password_peek_canvas.bind("<Leave>", self._on_password_peek_release, add="+")
        self._render_password_peek_icon()

        for entry in (self.auth_username_entry, self.auth_password_entry):
            entry.bind("<Return>", lambda _event: self.submit_auth_by_mode(), add="+")

        self.auth_action_row = tk.Frame(card, bg=self.palette.panel_bg)
        self.auth_action_row.pack(fill="x", pady=(2, 0))
        self.auth_action_row.grid_columnconfigure(0, weight=1)
        self.surface_roles.append((self.auth_action_row, "panel"))

        self.auth_submit_button = self._make_button(
            self.auth_action_row,
            "登录",
            self.submit_auth_by_mode,
            style="LoginAccent.TButton",
        )
        self.auth_submit_button.grid(row=0, column=0, sticky="ew")

        self.auth_toggle_row = tk.Frame(card, bg=self.palette.panel_bg)
        self.auth_toggle_row.pack(anchor="w", pady=(12, 0))
        self.surface_roles.append((self.auth_toggle_row, "panel"))

        self.auth_toggle_prefix_label = tk.Label(
            self.auth_toggle_row,
            text="",
            font=("Microsoft YaHei UI", 10),
            anchor="w",
        )
        self.auth_toggle_prefix_label.pack(side="left")
        self.label_roles.append((self.auth_toggle_prefix_label, "muted"))

        self.auth_toggle_link_label = tk.Label(
            self.auth_toggle_row,
            text="",
            font=("Microsoft YaHei UI", 10, "bold"),
            anchor="w",
            cursor="hand2",
        )
        self.auth_toggle_link_label.pack(side="left", padx=(6, 0))
        self.auth_toggle_link_label.bind("<Button-1>", lambda _event: self.toggle_auth_mode(), add="+")

        self.auth_status_label = tk.Label(card, text="", font=("Microsoft YaHei UI", 9), anchor="w", justify="left")
        self.auth_status_label.pack(anchor="w", pady=(14, 0))
        self.label_roles.append((self.auth_status_label, "muted"))
        self._build_login_loading_overlay()

    def _render_login_backdrop(self, _event=None) -> None:
        if not hasattr(self, "login_backdrop"):
            return
        try:
            width = max(self.login_backdrop.winfo_width(), 1)
            height = max(self.login_backdrop.winfo_height(), 1)
        except tk.TclError:
            return

        if self.theme_name == "light":
            glow_primary = "#d9e7ff"
            glow_secondary = "#c7dcff"
            band_fill = "#eef4fc"
            line_color = "#d3dfef"
        else:
            glow_primary = "#102741"
            glow_secondary = "#0b2038"
            band_fill = "#0b1628"
            line_color = "#17304f"

        self.login_backdrop.delete("all")
        self.login_backdrop.create_rectangle(0, 0, width, height, fill=self.palette.root_bg, outline="")
        self.login_backdrop.create_oval(
            -width * 0.1,
            -height * 0.08,
            width * 0.42,
            height * 0.48,
            fill=glow_primary,
            outline="",
        )
        self.login_backdrop.create_oval(
            width * 0.58,
            -height * 0.22,
            width * 1.04,
            height * 0.34,
            fill=glow_secondary,
            outline="",
        )
        self.login_backdrop.create_polygon(
            width * 0.42,
            0,
            width,
            0,
            width,
            height * 0.72,
            width * 0.66,
            height,
            width * 0.28,
            height,
            width * 0.56,
            height * 0.36,
            fill=band_fill,
            outline="",
        )
        self.login_backdrop.create_line(
            width * 0.12,
            height * 0.8,
            width * 0.56,
            height * 0.34,
            fill=line_color,
            width=2,
        )
        self.login_backdrop.create_line(
            width * 0.18,
            height * 0.88,
            width * 0.64,
            height * 0.4,
            fill=line_color,
            width=2,
        )
        self.login_backdrop.create_line(
            width * 0.68,
            height * 0.1,
            width * 0.94,
            height * 0.1,
            fill=self.palette.accent,
            width=3,
        )

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

        sidebar_actions = tk.Frame(sidebar_header, bg=self.palette.panel_bg)
        sidebar_actions.pack(side="right")
        self.surface_roles.append((sidebar_actions, "panel"))

        self.add_note_button = self._make_button(sidebar_actions, "+ 新建", self.open_new_note, style="Accent.TButton")
        self.add_note_button.pack(side="left", padx=(0, 8))

        self.import_note_button = self._make_button(sidebar_actions, "导入", self.import_note_from_file, style="Ghost.TButton")
        self.import_note_button.pack(side="left", padx=(0, 8))

        self.search_note_button = self._make_button(sidebar_actions, "搜索", self.open_note_search_window, style="Ghost.TButton")
        self.search_note_button.pack(side="left")

        self.note_count_label = tk.Label(self.sidebar_card, text="0 条笔记", font=("Microsoft YaHei UI", 9), anchor="w")
        self.note_count_label.pack(fill="x", pady=(0, 10))
        self.label_roles.append((self.note_count_label, "muted"))

        self.notes_area = ScrollArea(self.sidebar_card, self.palette.panel_alt, self.palette)
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

        self.chat_toolbar_shell = tk.Frame(
            header_actions,
            bg=self.palette.chrome_bg,
            highlightthickness=1,
            highlightbackground=self.palette.border,
            bd=0,
            padx=6,
            pady=6,
        )
        self.chat_toolbar_shell.pack(side="right")
        self.surface_roles.append((self.chat_toolbar_shell, "chrome"))

        self.model_chip = tk.Label(self.chat_toolbar_shell, text="模型: 加载中", font=("Microsoft YaHei UI", 9, "bold"), padx=12, pady=7)
        self.model_chip.pack(side="left", padx=(0, 8))

        self.chat_clear_button = self._make_button(self.chat_toolbar_shell, "__trash__", self.confirm_clear_chat, style="Icon.TButton")
        self.chat_clear_button.pack(side="left", padx=(0, 6))

        self.runtime_mode_button = self._make_button(
            self.chat_toolbar_shell,
            launcher_core.RUNTIME_PROFILE_SHORT_LABELS[launcher_core.get_saved_runtime_profile()],
            self.toggle_runtime_profile,
            style="ModeIcon.TButton",
        )
        self.runtime_mode_button.pack(side="left", padx=(0, 6))

        self.theme_button = self._make_button(self.chat_toolbar_shell, "__moon__", self.toggle_theme, style="Icon.TButton")
        self.theme_button.pack(side="left", padx=(0, 6))

        self.settings_button = self._make_button(self.chat_toolbar_shell, "\ue713", self.open_settings_page, style="FluentIcon.TButton")
        self.settings_button.pack(side="left")

        self.chat_area = ScrollArea(self.main_panel, self.palette.panel_alt, self.palette)
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
        self.input_frame.grid(row=0, column=0, sticky="ew")
        self.input_frame.grid_columnconfigure(0, weight=1)
        self.input_frame.grid_rowconfigure(0, weight=1)
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
        self.question_input.grid(row=0, column=0, sticky="nsew")
        self.text_widgets.append(self.question_input)
        self.question_input.bind("<Return>", self._chat_return_shortcut, add="+")
        self.question_input.bind("<KP_Enter>", self._chat_return_shortcut, add="+")
        self.question_input.bind("<Button-1>", lambda _event: self._hide_question_placeholder(), add="+")
        self.question_input.bind("<FocusIn>", self._on_question_focus_in, add="+")
        self.question_input.bind("<FocusOut>", self._on_question_focus_out, add="+")
        self.question_input.bind("<KeyRelease>", self._on_question_key_release, add="+")

        self.question_placeholder = tk.Label(
            self.input_frame,
            text="输入问题，回车发送，Shift+回车换行",
            font=("Microsoft YaHei UI", 10),
            anchor="nw",
            justify="left",
            cursor="xterm",
        )
        self.question_placeholder.place(x=14, y=12)
        self.question_placeholder.bind(
            "<Button-1>",
            lambda _event: (self._hide_question_placeholder(), self.question_input.focus_set()),
            add="+",
        )
        self.label_roles.append((self.question_placeholder, "muted"))

        self.send_button = self._make_button(self.input_frame, "__send__", self.send_question, style="Icon.TButton")
        self.send_button.grid(row=0, column=1, sticky="se", padx=(0, 10), pady=(0, 10))
        self._sync_send_button_state()

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

        self.editor_edit_button = self._make_button(editor_actions, "编辑", self.toggle_editor_mode, style="Ghost.TButton")
        self.editor_edit_button.pack(side="left", padx=(0, 10))

        self.editor_save_button = self._make_button(editor_actions, "保存", self.save_note, style="Accent.TButton")
        self.editor_save_button.pack(side="left", padx=(0, 10))

        self.editor_export_button = self._make_button(editor_actions, "导出", self.export_current_note, style="Ghost.TButton")
        self.editor_export_button.pack(side="left", padx=(0, 10))

        self.editor_clear_button = self._make_button(editor_actions, "清空", self.clear_editor, style="Danger.TButton")
        self.editor_clear_button.pack(side="left", padx=(0, 10))

        self.editor_back_button = self._make_button(editor_actions, "返回", self.back_to_main, style="Ghost.TButton")
        self.editor_back_button.pack(side="left")

        self.editor_meta_row = tk.Frame(self.editor_surface, bg=self.palette.panel_bg)
        self.editor_meta_row.grid(row=1, column=0, sticky="ew", pady=(0, 16))
        self.editor_meta_row.grid_columnconfigure(1, weight=1)
        self.surface_roles.append((self.editor_meta_row, "panel"))

        self.editor_status_badge = tk.Label(self.editor_meta_row, text="预览模式", anchor="w", font=("Microsoft YaHei UI", 9, "bold"), padx=10, pady=4)
        self.editor_status_badge.grid(row=0, column=0, sticky="w", padx=(0, 14))
        self._set_badge(self.editor_status_badge, "预览模式", "neutral")

        self.editor_meta_label = tk.Label(
            self.editor_meta_row,
            text="创建时间：-    最后修改：-    最后修改人：-",
            font=("Microsoft YaHei UI", 9),
            anchor="w",
            justify="left",
        )
        self.editor_meta_label.grid(row=0, column=1, sticky="w")
        self.label_roles.append((self.editor_meta_label, "muted"))

        self.editor_title_var = tk.StringVar()
        self.editor_title_entry = tk.Entry(
            self.editor_surface,
            textvariable=self.editor_title_var,
            relief="flat",
            bd=0,
            highlightthickness=1,
            font=("Microsoft YaHei UI", 16, "bold"),
        )
        self.editor_title_entry.grid(row=2, column=0, sticky="ew", pady=(0, 12), ipady=10)
        self.entry_widgets.append(self.editor_title_entry)
        self.editor_title_entry.bind("<KeyRelease>", self._on_editor_text_changed, add="+")

        self.editor_text = tk.Text(
            self.editor_surface,
            wrap="word",
            relief="flat",
            bd=0,
            padx=24,
            pady=22,
            font=("Microsoft YaHei UI", 12),
            undo=True,
            spacing1=4,
            spacing2=2,
            spacing3=8,
        )
        self.editor_text.grid(row=3, column=0, sticky="nsew")
        self.editor_text.bind("<KeyRelease>", self._on_editor_text_changed, add="+")
        self.editor_text.bind("<ButtonRelease-1>", self._update_current_line_highlight, add="+")
        self.editor_text.bind("<FocusIn>", self._update_current_line_highlight, add="+")
        self.editor_text.tag_configure("current_line", background=blend_hex(self.palette.chip_bg, self.palette.panel_bg, 0.35))
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

        self.settings_action_row = tk.Frame(settings_header, bg=self.palette.root_bg)
        self.settings_action_row.grid(row=0, column=1, sticky="e")
        self.surface_roles.append((self.settings_action_row, "root"))

        self.members_button = self._make_button(self.settings_action_row, "所有成员", self.open_members_window, style="Accent.TButton")
        self.members_button.pack(side="left", padx=(0, 12))

        self.logout_button = self._make_button(self.settings_action_row, "退出登录", self.logout_account, style="Danger.TButton")
        self.logout_button.pack(side="left", padx=(0, 10))

        self.refresh_status_button = self._make_button(self.settings_action_row, "刷新状态", self.refresh_settings_status, style="Ghost.TButton")
        self.refresh_status_button.pack(side="left", padx=(0, 10))

        self.settings_back_button = self._make_button(self.settings_action_row, "返回", self.back_to_main, style="Ghost.TButton")
        self.settings_back_button.pack(side="left")

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

        self.runtime_copy = self._make_card_copy(self.runtime_card, "这里统一管理项目内置固定版 Ollama 运行时，以及项目私有模型目录。")
        self.runtime_copy.pack(anchor="w", pady=(4, 18))

        self.runtime_grid = tk.Frame(self.runtime_card, bg=self.palette.panel_bg)
        self.runtime_grid.pack(fill="x")
        self.runtime_grid.grid_columnconfigure(1, weight=1)
        self.surface_roles.append((self.runtime_grid, "panel"))

        self.runtime_desktop_value = self._add_runtime_row(self.runtime_grid, 0, "桌面客户端")
        self.runtime_ollama_value = self._add_runtime_row(self.runtime_grid, 1, "Ollama")
        self.runtime_gpu_bundle_value = self._add_runtime_row(self.runtime_grid, 2, "GPU 包状态")
        self.runtime_profile_value = self._add_runtime_row(self.runtime_grid, 3, "运行模式")
        self.runtime_model_value = self._add_runtime_row(self.runtime_grid, 4, "对话模型")
        self.runtime_embed_model_value = self._add_runtime_row(self.runtime_grid, 5, "向量模型")

        self.model_card = self._make_card(self.settings_body)
        self.model_card.grid(row=1, column=0, sticky="nsew", padx=(0, 10))

        self.model_title = self._make_card_title(self.model_card, "模型设置")
        self.model_title.pack(anchor="w")

        self.chat_model_label = tk.Label(self.model_card, text="对话模型", font=("Microsoft YaHei UI", 10, "bold"), anchor="w")
        self.chat_model_label.pack(anchor="w", pady=(16, 6))
        self.label_roles.append((self.chat_model_label, "text"))

        self.model_var = tk.StringVar()
        self.model_combo = ttk.Combobox(
            self.model_card,
            textvariable=self.model_var,
            state="readonly",
            style="App.TCombobox",
        )
        self.model_combo.pack(fill="x")
        self.model_combo.bind("<<ComboboxSelected>>", self.on_model_selected)
        self.model_combo.bind("<ButtonPress-1>", self._open_combobox_dropdown, add="+")

        self.embed_model_title = tk.Label(self.model_card, text="向量模型", font=("Microsoft YaHei UI", 10, "bold"), anchor="w")
        self.embed_model_title.pack(anchor="w", pady=(16, 6))
        self.label_roles.append((self.embed_model_title, "text"))

        self.embed_model_var = tk.StringVar()
        self.embed_model_combo = ttk.Combobox(
            self.model_card,
            textvariable=self.embed_model_var,
            state="readonly",
            style="App.TCombobox",
        )
        self.embed_model_combo.pack(fill="x")
        self.embed_model_combo.bind("<<ComboboxSelected>>", self.on_embed_model_selected)
        self.embed_model_combo.bind("<ButtonPress-1>", self._open_combobox_dropdown, add="+")

        self.diagnostics_card = self._make_card(self.settings_body)
        self.diagnostics_card.grid(row=0, column=1, sticky="nsew", pady=(0, 10))
        self.diagnostics_card.grid_rowconfigure(0, weight=1)
        self.diagnostics_card.grid_columnconfigure(0, weight=1)

        self.diagnostics_area = ScrollArea(self.diagnostics_card, self.palette.panel_bg, self.palette)
        self.diagnostics_area.grid(row=0, column=0, sticky="nsew")
        self.scroll_areas.append(self.diagnostics_area)

        self.dependency_title = self._make_card_title(self.diagnostics_area.content, "依赖管理")
        self.dependency_title.pack(anchor="w", pady=(18, 0))

        self.dependency_copy = self._make_card_copy(self.diagnostics_area.content, "在应用内可视化管理运行时、GPU 加速库和模型依赖。")
        self.dependency_copy.pack(anchor="w", pady=(4, 14))

        runtime_action_row = tk.Frame(self.diagnostics_area.content, bg=self.palette.panel_bg)
        runtime_action_row.pack(fill="x", pady=(0, 10))
        self.surface_roles.append((runtime_action_row, "panel"))

        self.install_runtime_button = self._make_button(runtime_action_row, "管理依赖", self.open_dependency_manager_window, style="Accent.TButton")
        self.install_runtime_button.pack(side="left", padx=(0, 10))

        self.start_runtime_button = self._make_button(runtime_action_row, "启动 Ollama", self.start_ollama_runtime, style="Ghost.TButton")
        self.start_runtime_button.pack(side="left", padx=(0, 10))

        self.stop_runtime_button = self._make_button(runtime_action_row, "停止 Ollama", self.stop_ollama_runtime, style="Ghost.TButton")
        self.stop_runtime_button.pack(side="left", padx=(0, 10))

        self.dependency_feedback_label = tk.Label(
            self.diagnostics_area.content,
            text="",
            font=("Microsoft YaHei UI", 9),
            justify="left",
            anchor="w",
            wraplength=420,
        )
        self.dependency_feedback_label.pack(fill="x", pady=(0, 14))
        self.label_roles.append((self.dependency_feedback_label, "muted"))

        self.diagnostics_title = self._make_card_title(self.diagnostics_area.content, "环境诊断")
        self.diagnostics_title.pack(anchor="w", pady=(12, 0))

        self.diagnostics_copy = self._make_card_copy(self.diagnostics_area.content, "这里汇总当前本地固定环境，包括 Python、CPU/GPU 硬件信息和项目根目录。")
        self.diagnostics_copy.pack(anchor="w", pady=(4, 18))

        self.environment_label = tk.Label(self.diagnostics_area.content, text="", justify="left", anchor="nw", font=("Consolas", 10))
        self.environment_label.pack(fill="x")
        self.label_roles.append((self.environment_label, "text"))

        self.logs_card = self._make_card(self.settings_body)
        self.logs_card.grid(row=1, column=1, sticky="nsew")
        self.logs_card.grid_rowconfigure(0, weight=1)
        self.logs_card.grid_columnconfigure(0, weight=1)

        self.logs_area = ScrollArea(self.logs_card, self.palette.panel_bg, self.palette)
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
        self._build_settings_loading_overlay()

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

    def _skeleton_palette_colors(self) -> dict[str, str]:
        if self.theme_name == "light":
            overlay_root = blend_hex(self.palette.root_bg, self.palette.panel_bg, 0.22)
            overlay_panel = blend_hex(self.palette.panel_bg, self.palette.root_bg, 0.08)
            block = blend_hex(self.palette.border, self.palette.panel_alt, 0.45)
            block_strong = blend_hex(self.palette.border, self.palette.chrome_bg, 0.6)
        else:
            overlay_root = blend_hex(self.palette.root_bg, self.palette.panel_bg, 0.18)
            overlay_panel = blend_hex(self.palette.panel_bg, self.palette.root_bg, 0.12)
            block = blend_hex(self.palette.border, self.palette.panel_alt, 0.35)
            block_strong = blend_hex(self.palette.border, self.palette.chrome_bg, 0.55)
        return {
            "overlay_root": overlay_root,
            "overlay_panel": overlay_panel,
            "card": self.palette.panel_bg,
            "section": self.palette.panel_bg,
            "block": block,
            "block_strong": block_strong,
        }

    def _register_skeleton_widget(self, widget: tk.Widget, role: str) -> None:
        self.skeleton_roles.append((widget, role))

    def _make_skeleton_block(
        self,
        parent: tk.Widget,
        *,
        height: int,
        width: int | None = None,
        strong: bool = False,
    ) -> tk.Frame:
        block = tk.Frame(parent, height=height, width=width or 0, highlightthickness=0, bd=0)
        block.pack_propagate(False)
        self._register_skeleton_widget(block, "block_strong" if strong else "block")
        return block

    def _apply_skeleton_theme(self) -> None:
        colors = self._skeleton_palette_colors()
        for widget, role in self.skeleton_roles:
            try:
                if not widget.winfo_exists():
                    continue
                if role in {"overlay_root", "overlay_panel", "section"}:
                    widget.configure(bg=colors[role])
                elif role == "card":
                    widget.configure(bg=colors["card"], highlightbackground=self.palette.border)
                elif role in {"block", "block_strong"}:
                    widget.configure(bg=colors[role])
            except tk.TclError:
                continue

    def _build_login_loading_overlay(self) -> None:
        overlay = tk.Frame(self.login_card, bd=0, highlightthickness=0)
        self._register_skeleton_widget(overlay, "overlay_panel")

        shell = tk.Frame(overlay, bd=0, highlightthickness=0, padx=0, pady=0)
        shell.pack(fill="both", expand=True)
        self._register_skeleton_widget(shell, "section")

        header_block = self._make_skeleton_block(shell, height=34, width=240, strong=True)
        header_block.pack(anchor="w", pady=(6, 0))
        title_block = self._make_skeleton_block(shell, height=30, width=176, strong=True)
        title_block.pack(anchor="w", pady=(22, 0))

        form = tk.Frame(shell, bd=0, highlightthickness=0)
        form.pack(fill="x", pady=(26, 0))
        self._register_skeleton_widget(form, "section")

        for _label_width, field_height in ((68, 46), (52, 46)):
            label_block = self._make_skeleton_block(form, height=12, width=_label_width)
            label_block.pack(anchor="w", pady=(0, 8))
            field_block = self._make_skeleton_block(form, height=field_height, strong=True)
            field_block.pack(fill="x", pady=(0, 14))

        action_block = self._make_skeleton_block(shell, height=58, strong=True)
        action_block.pack(fill="x", pady=(8, 0))
        link_block = self._make_skeleton_block(shell, height=12, width=164)
        link_block.pack(anchor="w", pady=(16, 0))

        self.login_loading_overlay = overlay
        self._set_login_loading(True)

    def _make_settings_skeleton_card(self, parent: tk.Widget) -> tk.Frame:
        card = tk.Frame(parent, padx=20, pady=20, bd=0, highlightthickness=1)
        self._register_skeleton_widget(card, "card")
        return card

    def _build_settings_loading_overlay(self) -> None:
        overlay = tk.Frame(self.settings_body, bd=0, highlightthickness=0)
        self._register_skeleton_widget(overlay, "overlay_root")
        overlay.grid_columnconfigure(0, weight=3)
        overlay.grid_columnconfigure(1, weight=2)
        overlay.grid_rowconfigure(0, weight=1)
        overlay.grid_rowconfigure(1, weight=1)

        runtime_card = self._make_settings_skeleton_card(overlay)
        runtime_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10), pady=(0, 10))
        runtime_inner = tk.Frame(runtime_card, bd=0, highlightthickness=0)
        runtime_inner.pack(fill="both", expand=True)
        self._register_skeleton_widget(runtime_inner, "section")
        self._make_skeleton_block(runtime_inner, height=24, width=120, strong=True).pack(anchor="w")
        self._make_skeleton_block(runtime_inner, height=12, width=300).pack(anchor="w", pady=(10, 20))
        for width in (420, 360, 390, 400, 320, 340):
            self._make_skeleton_block(runtime_inner, height=30, width=width, strong=True).pack(fill="x", pady=(0, 12))

        diagnostics_card = self._make_settings_skeleton_card(overlay)
        diagnostics_card.grid(row=0, column=1, sticky="nsew", pady=(0, 10))
        diagnostics_inner = tk.Frame(diagnostics_card, bd=0, highlightthickness=0)
        diagnostics_inner.pack(fill="both", expand=True)
        self._register_skeleton_widget(diagnostics_inner, "section")
        self._make_skeleton_block(diagnostics_inner, height=24, width=110, strong=True).pack(anchor="w")
        self._make_skeleton_block(diagnostics_inner, height=12, width=260).pack(anchor="w", pady=(10, 18))
        for width in (220, 240, 200):
            self._make_skeleton_block(diagnostics_inner, height=40, width=width, strong=True).pack(fill="x", pady=(0, 12))
        for width in (230, 280, 250, 210, 260, 240):
            self._make_skeleton_block(diagnostics_inner, height=12, width=width).pack(anchor="w", pady=(0, 10))

        model_card = self._make_settings_skeleton_card(overlay)
        model_card.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        model_inner = tk.Frame(model_card, bd=0, highlightthickness=0)
        model_inner.pack(fill="both", expand=True)
        self._register_skeleton_widget(model_inner, "section")
        self._make_skeleton_block(model_inner, height=24, width=100, strong=True).pack(anchor="w")
        for label_width in (76, 68):
            self._make_skeleton_block(model_inner, height=12, width=label_width).pack(anchor="w", pady=(20, 8))
            self._make_skeleton_block(model_inner, height=42, strong=True).pack(fill="x")

        logs_card = self._make_settings_skeleton_card(overlay)
        logs_card.grid(row=1, column=1, sticky="nsew")
        logs_inner = tk.Frame(logs_card, bd=0, highlightthickness=0)
        logs_inner.pack(fill="both", expand=True)
        self._register_skeleton_widget(logs_inner, "section")
        self._make_skeleton_block(logs_inner, height=24, width=88, strong=True).pack(anchor="w")
        self._make_skeleton_block(logs_inner, height=12, width=250).pack(anchor="w", pady=(10, 18))
        for width in (260, 290, 245, 275, 230, 300, 250):
            self._make_skeleton_block(logs_inner, height=12, width=width).pack(anchor="w", pady=(0, 10))

        self.settings_loading_overlay = overlay
        self._set_settings_loading(False)

    def _set_login_loading(self, visible: bool) -> None:
        if not hasattr(self, "login_loading_overlay"):
            return
        if visible:
            self.login_loading_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.login_loading_overlay.lift()
        elif self.login_loading_overlay.winfo_manager() == "place":
            self.login_loading_overlay.place_forget()

    def _set_settings_loading(self, visible: bool) -> None:
        if not hasattr(self, "settings_loading_overlay"):
            return
        if visible:
            self.settings_loading_overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
            self.settings_loading_overlay.lift()
        elif self.settings_loading_overlay.winfo_manager() == "place":
            self.settings_loading_overlay.place_forget()

    def _add_runtime_row(self, parent: tk.Frame, row: int, title: str) -> tk.Label:
        name = tk.Label(parent, text=title, anchor="w", font=("Microsoft YaHei UI", 10))
        name.grid(row=row, column=0, sticky="w", pady=6, padx=(0, 16))
        self.label_roles.append((name, "muted"))

        value = tk.Label(parent, text="检测中", anchor="w", font=("Microsoft YaHei UI", 10, "bold"), padx=10, pady=4)
        value.grid(row=row, column=1, sticky="ew", pady=6)
        self._set_badge(value, "检测中", "neutral")
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

    def _safe_runtime_embed_model(self) -> str:
        try:
            if launcher_core.ollama_ready():
                init_runtime_settings()
                return get_embed_model()
        except Exception:
            pass
        return launcher_core.get_saved_embed_model()

    def _runtime_profile_display(self, profile: str | None) -> str:
        normalized = launcher_core.normalize_runtime_profile(profile)
        return launcher_core.RUNTIME_PROFILE_LABELS.get(normalized, normalized.upper())

    def _runtime_profile_from_display(self, text: str | None) -> str:
        normalized = str(text or "").strip()
        for key, label in launcher_core.RUNTIME_PROFILE_OPTIONS:
            if normalized in {key, label}:
                return key
        return launcher_core.normalize_runtime_profile(normalized)

    def _sync_send_button_state(self) -> None:
        if not hasattr(self, "send_button"):
            return
        enabled = self.current_account is not None and not self.chat_busy and not self.runtime_busy and not self.model_busy
        self._set_button_enabled(self.send_button, enabled)

    def _current_editor_signature(self) -> tuple[str, str]:
        title = str(self.editor_title_var.get() if hasattr(self, "editor_title_var") else "").strip()
        body = str(self.editor_text.get("1.0", "end").strip() if hasattr(self, "editor_text") else "")
        return title, body

    def _populate_editor_meta(self, note: dict | None = None) -> None:
        payload = note or {}
        created_at = format_time(payload.get("created_at")) or "-"
        updated_at = format_time(payload.get("updated_at")) or created_at
        updated_by = str(payload.get("updated_by") or payload.get("created_by") or "-")
        self.editor_meta_label.configure(
            text=f"创建时间：{created_at}    最后修改：{updated_at}    最后修改人：{updated_by}"
        )

    def _set_editor_mode(self, mode: str) -> None:
        self.editor_mode = "edit" if mode == "edit" else "preview"
        editing = self.editor_mode == "edit"
        self.editor_title_entry.configure(state="normal" if editing else "readonly")
        self.editor_text.configure(state="normal" if editing else "disabled", cursor="xterm" if editing else "arrow")
        self._set_badge(self.editor_status_badge, "编辑模式" if editing else "预览模式", "accent" if editing else "neutral")
        self.editor_edit_button.configure(text="取消编辑" if editing else "编辑")
        self._set_button_enabled(self.editor_edit_button, True)
        self._set_button_enabled(self.editor_save_button, editing and not self.note_busy)
        self._set_button_enabled(self.editor_export_button, self.current_note_id is not None and not self.note_busy)
        self._set_button_enabled(self.editor_clear_button, editing and not self.note_busy)
        self._update_current_line_highlight()

    def show_editor_edit_mode(self) -> None:
        self._set_editor_mode("edit")
        self.editor_title_entry.focus_set()

    def toggle_editor_mode(self) -> None:
        if self.editor_mode == "edit":
            self._attempt_leave_editor(lambda: self._set_editor_mode("preview"))
            return
        self.show_editor_edit_mode()

    def _on_editor_text_changed(self, _event=None) -> None:
        self._sync_editor_dirty_state()
        self._update_current_line_highlight()

    def _sync_editor_dirty_state(self) -> None:
        if not hasattr(self, "editor_title_var"):
            return
        self.editor_dirty = self._current_editor_signature() != self.editor_loaded_signature
        if self.editor_dirty:
            self.editor_copy_label.configure(text="有未保存修改，保存后会重新向量化并更新检索索引")
        else:
            self.editor_copy_label.configure(text="保存后会重新向量化并更新检索索引")

    def _update_current_line_highlight(self, _event=None) -> None:
        if not hasattr(self, "editor_text"):
            return
        self.editor_text.tag_remove("current_line", "1.0", "end")
        if self.editor_mode != "edit":
            return
        line_start = self.editor_text.index("insert linestart")
        line_end = self.editor_text.index("insert lineend +1c")
        self.editor_text.tag_add("current_line", line_start, line_end)

    def _clear_editor_search_highlight(self) -> None:
        if not hasattr(self, "editor_text"):
            return
        try:
            self.editor_text.tag_remove("search_match", "1.0", "end")
        except tk.TclError:
            return

    def _apply_editor_search_highlight(self, query: str) -> None:
        self.editor_highlight_query = str(query or "").strip()
        self._clear_editor_search_highlight()
        if not self.editor_highlight_query or not hasattr(self, "editor_text"):
            return
        try:
            self.editor_text.tag_configure(
                "search_match",
                background=blend_hex(self.palette.accent, self.palette.panel_bg, 0.72),
                foreground=self.palette.text,
            )
            state = str(self.editor_text.cget("state"))
            if state == "disabled":
                self.editor_text.configure(state="normal")
            match_index: str | None = None
            for term in search_terms(self.editor_highlight_query):
                start = "1.0"
                while True:
                    match_index = self.editor_text.search(term, start, stopindex="end", nocase=True)
                    if not match_index:
                        break
                    end_index = f"{match_index}+{len(term)}c"
                    self.editor_text.tag_add("search_match", match_index, end_index)
                    if match_index is None:
                        match_index = start
                    start = end_index
                if match_index:
                    break
            if match_index:
                self.editor_text.see(match_index)
                self.editor_text.mark_set("insert", match_index)
            if state == "disabled":
                self.editor_text.configure(state="disabled")
        except tk.TclError:
            return

    def _load_editor_note(self, note: dict | None, *, edit_mode: bool) -> None:
        payload = note or {}
        title = note_title_text(payload, "")
        body = note_body_text(payload)
        self.editor_title_var.set(title)
        self.editor_text.configure(state="normal")
        self.editor_text.delete("1.0", "end")
        self.editor_text.insert("1.0", body)
        self.editor_loaded_signature = (title, body)
        self.editor_dirty = False
        self._populate_editor_meta(note)
        self._sync_editor_dirty_state()
        self._set_editor_mode("edit" if edit_mode else "preview")
        self._apply_editor_search_highlight(self.editor_highlight_query)

    def _attempt_leave_editor(self, action) -> bool:
        if self.current_page != "editor" or not self.editor_dirty or self.note_busy:
            if callable(action):
                action()
            return True
        decision = messagebox.askyesnocancel("未保存修改", "当前笔记有未保存修改。是否先保存？")
        if decision is None:
            return False
        if decision:
            self.editor_pending_action = action
            self.save_note()
            return False
        self.editor_pending_action = None
        self.editor_dirty = False
        if callable(action):
            action()
        return True

    def _open_combobox_dropdown(self, event=None) -> None:
        widget = getattr(event, "widget", None)
        if widget is None:
            return
        try:
            if str(widget.cget("state")) == "disabled":
                return
            self.root.after_idle(lambda w=widget: self.root.eval(f"ttk::combobox::Post {w}"))
        except tk.TclError:
            return

    def _account_signature(self, account: dict | None = None) -> str:
        payload = account or self.current_account
        if payload is None:
            return "未登录"
        username = str(payload.get("username") or "").strip()
        return username or "未命名账号"

    def _require_account(self) -> dict:
        if self.current_account is None:
            raise RuntimeError("请先登录账号。")
        return self.current_account

    def _load_chat_items(self, username: str | None = None) -> list[dict[str, object]]:
        payload = self._load_desktop_state()
        items = None
        chat_items_by_user = payload.get("chat_items_by_user")
        if username and isinstance(chat_items_by_user, dict):
            items = chat_items_by_user.get(username)
        elif username and isinstance(payload.get("chat_items"), list):
            items = payload.get("chat_items")
        if not isinstance(items, list):
            return []
        result: list[dict[str, object]] = []
        for item in items[-MAX_CHAT_ITEMS:]:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "")
            text = str(item.get("text") or "")
            time_text = str(item.get("time") or "")
            model = str(item.get("model") or "")
            runtime_profile = str(item.get("runtime_profile") or "")
            elapsed_seconds = item.get("elapsed_seconds")
            try:
                elapsed_value = float(elapsed_seconds) if elapsed_seconds is not None else None
            except (TypeError, ValueError):
                elapsed_value = None
            if role and text:
                chat_item: dict[str, object] = {"role": role, "text": text, "time": time_text, "model": model}
                if runtime_profile:
                    chat_item["runtime_profile"] = runtime_profile
                if elapsed_value is not None:
                    chat_item["elapsed_seconds"] = elapsed_value
                raw_sources = item.get("sources")
                if isinstance(raw_sources, list):
                    sources: list[dict[str, object]] = []
                    for source in raw_sources[:1]:
                        if not isinstance(source, dict):
                            continue
                        note_id = source.get("note_id")
                        try:
                            parsed_note_id = int(note_id)
                        except (TypeError, ValueError):
                            continue
                        sources.append(
                            {
                                "note_id": parsed_note_id,
                                "title": str(source.get("title") or ""),
                                "snippet": str(source.get("snippet") or ""),
                            }
                        )
                    if sources:
                        chat_item["sources"] = sources
                result.append(chat_item)
        return result

    def _load_last_username(self) -> str:
        payload = self._load_desktop_state()
        return str(payload.get("last_username") or "")

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
        chat_items_by_user = payload.get("chat_items_by_user")
        if not isinstance(chat_items_by_user, dict):
            chat_items_by_user = {}
        if self.current_account is not None:
            chat_items_by_user[str(self.current_account.get("username") or "")] = self.chat_items[-MAX_CHAT_ITEMS:]
        payload["chat_items_by_user"] = chat_items_by_user
        payload.pop("chat_items", None)
        payload["theme_name"] = self.theme_name
        payload["last_username"] = self.last_username
        launcher_core.atomic_write_json(path, payload)

    def _load_theme_name(self) -> str:
        payload = self._load_desktop_state()
        theme_name = payload.get("theme_name")
        if theme_name in PALETTES:
            return str(theme_name)
        return "light"

    def _save_theme_name(self) -> None:
        self._save_desktop_state()

    def _save_chat_items(self) -> None:
        self._save_desktop_state()

    def _load_model_tasks(self) -> list[dict[str, object]]:
        payload = self._load_desktop_state()
        raw_tasks = payload.get("model_tasks")
        if not isinstance(raw_tasks, list):
            return []
        tasks: list[dict[str, object]] = []
        changed = False
        for item in raw_tasks[-12:]:
            if not isinstance(item, dict):
                continue
            state = str(item.get("state") or "pending")
            if state == "running":
                state = "interrupted"
                changed = True
            tasks.append(
                {
                    "task_id": str(item.get("task_id") or now_iso()),
                    "model_name": str(item.get("model_name") or ""),
                    "kind": str(item.get("kind") or ""),
                    "state": state,
                    "message": str(item.get("message") or ""),
                    "progress": item.get("progress"),
                    "updated_at": str(item.get("updated_at") or now_iso()),
                }
            )
        if changed:
            self._save_model_tasks(tasks)
        return tasks

    def _save_model_tasks(self, tasks: list[dict[str, object]] | None = None) -> None:
        path = launcher_core.ROOT_DIR / STATE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._load_desktop_state()
        payload["model_tasks"] = list((tasks if tasks is not None else self.model_task_items)[-12:])
        launcher_core.atomic_write_json(path, payload)

    def _create_model_task(self, model_name: str, kind: str, message: str) -> str:
        task_id = f"{kind}:{model_name}:{int(time.time() * 1000)}"
        self.model_task_items.insert(
            0,
            {
                "task_id": task_id,
                "model_name": model_name,
                "kind": kind,
                "state": "running",
                "message": message,
                "progress": 0.0 if kind == "install" else None,
                "updated_at": now_iso(),
            },
        )
        self.model_task_items = self.model_task_items[:12]
        self._save_model_tasks()
        return task_id

    def _update_model_task(
        self,
        task_id: str | None,
        *,
        state: str,
        message: str,
        progress: float | None = None,
    ) -> None:
        if not task_id:
            return
        for item in self.model_task_items:
            if str(item.get("task_id") or "") != task_id:
                continue
            item["state"] = state
            item["message"] = message
            item["progress"] = progress
            item["updated_at"] = now_iso()
            break
        else:
            return
        self._save_model_tasks()

    def _model_task_badge_tone(self, state: str) -> str:
        if state == "success":
            return "good"
        if state == "error":
            return "danger"
        if state == "interrupted":
            return "warning"
        return "accent" if state == "running" else "neutral"

    def _append_chat_item(
        self,
        role: str,
        text: str,
        model: str = "",
        *,
        runtime_profile: str = "",
        elapsed_seconds: float | None = None,
        sources: list[dict[str, object]] | None = None,
    ) -> None:
        item: dict[str, object] = {"role": role, "text": text, "time": now_iso(), "model": model}
        if runtime_profile:
            item["runtime_profile"] = runtime_profile
        if elapsed_seconds is not None:
            item["elapsed_seconds"] = round(float(elapsed_seconds), 1)
        if sources:
            item["sources"] = [
                {
                    "note_id": int(source["note_id"]),
                    "title": str(source.get("title") or ""),
                    "snippet": str(source.get("snippet") or ""),
                }
                for source in sources
                if source.get("note_id") is not None
            ][:1]
        self.chat_items.append(item)
        self.chat_items = self.chat_items[-MAX_CHAT_ITEMS:]
        self._save_chat_items()
        self.render_chat_history()

    def _set_auth_controls_enabled(self, enabled: bool) -> None:
        entry_state = "normal" if enabled else "disabled"
        for entry in (self.auth_username_entry, self.auth_password_entry):
            entry.configure(state=entry_state)
        self._set_button_enabled(self.auth_submit_button, enabled)
        if enabled:
            self.auth_toggle_link_label.configure(cursor="hand2")
            self.auth_password_peek_canvas.configure(cursor="hand2")
        else:
            self.password_peek_active = False
            self.auth_password_entry.configure(show="*")
            self.auth_toggle_link_label.configure(cursor="arrow")
            self.auth_password_peek_canvas.configure(cursor="arrow")

    def _center_toplevel(self, window: tk.Toplevel, width: int, height: int) -> None:
        try:
            self.root.update_idletasks()
            root_x = self.root.winfo_rootx()
            root_y = self.root.winfo_rooty()
            root_w = max(self.root.winfo_width(), width)
            root_h = max(self.root.winfo_height(), height)
            x = root_x + max(0, (root_w - width) // 2)
            y = root_y + max(0, (root_h - height) // 2)
            window.geometry(f"{width}x{height}+{x}+{y}")
        except tk.TclError:
            window.geometry(f"{width}x{height}")
            self._render_password_peek_icon()

    def _auth_error_message(self, exc: Exception) -> str:
        mapping = {
            "Username is required.": "请输入用户名。",
            "Username must be at least 3 characters.": "用户名至少需要 3 个字符。",
            "Password must be at least 4 characters.": "密码至少需要 4 个字符。",
            "Username already exists.": "该用户名已存在，请更换一个。",
            "Unsupported account role.": "账号角色无效。",
        }
        return mapping.get(str(exc), str(exc))

    def _refresh_auth_view(self) -> None:
        self.auth_mode = "login"
        self.auth_username_var.set(self.last_username)
        self.auth_password_var.set("")
        self._sync_auth_mode_labels()
        self.auth_status_label.configure(text="")
        self.password_peek_active = False
        self.auth_password_entry.configure(show="*")
        self._render_password_peek_icon()
        self.root.after(0, self.auth_username_entry.focus_set)

    def _sync_auth_mode_labels(self) -> None:
        if self.auth_mode == "register":
            self.auth_submit_button.configure(text="注册")
            self.auth_title_label.configure(text="注册账号")
            self.auth_toggle_prefix_label.configure(text="已有账号？")
            self.auth_toggle_link_label.configure(text="返回登录")
            return
        self.auth_submit_button.configure(text="登录")
        self.auth_title_label.configure(text="登录账号")
        self.auth_toggle_prefix_label.configure(text="还没有账号？")
        self.auth_toggle_link_label.configure(text="立即注册")

    def toggle_auth_mode(self) -> None:
        if self.account_busy:
            return
        self.auth_mode = "register" if self.auth_mode == "login" else "login"
        self.auth_password_var.set("")
        self.password_peek_active = False
        self.auth_password_entry.configure(show="*")
        self._render_password_peek_icon()
        self._sync_auth_mode_labels()
        self.auth_status_label.configure(text="")
        self.root.after(0, self.auth_username_entry.focus_set)

    def submit_auth_by_mode(self) -> None:
        if self.auth_mode == "register":
            self.register_auth()
            return
        self.submit_auth()

    def _on_password_peek_press(self, _event=None) -> None:
        if self.account_busy:
            return
        if str(self.auth_password_entry.cget("state")) == "disabled":
            return
        self.password_peek_active = True
        self.auth_password_entry.configure(show="")
        self._render_password_peek_icon()

    def _on_password_peek_release(self, _event=None) -> None:
        self.password_peek_active = False
        self.auth_password_entry.configure(show="*")
        self._render_password_peek_icon()

    def _render_password_peek_icon(self) -> None:
        if not hasattr(self, "auth_password_peek_canvas"):
            return
        canvas = self.auth_password_peek_canvas
        width = int(canvas.cget("width"))
        height = int(canvas.cget("height"))
        stroke = self.palette.accent if self.password_peek_active else self.palette.muted
        cx = width / 2
        cy = height / 2
        eye_w = width * 0.62
        eye_h = height * 0.36
        canvas.configure(bg=self.palette.input_bg)
        canvas.delete("all")
        canvas.create_arc(
            cx - eye_w / 2,
            cy - eye_h / 2,
            cx + eye_w / 2,
            cy + eye_h / 2,
            start=0,
            extent=180,
            style=tk.ARC,
            outline=stroke,
            width=2,
        )
        canvas.create_arc(
            cx - eye_w / 2,
            cy - eye_h / 2,
            cx + eye_w / 2,
            cy + eye_h / 2,
            start=180,
            extent=180,
            style=tk.ARC,
            outline=stroke,
            width=2,
        )
        if self.password_peek_active:
            canvas.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill=stroke, outline=stroke)
            return
        canvas.create_line(
            cx - eye_w * 0.5,
            cy + eye_h * 0.55,
            cx + eye_w * 0.5,
            cy - eye_h * 0.55,
            fill=stroke,
            width=2,
            capstyle=tk.ROUND,
        )

    def _update_account_context(self) -> None:
        if self.current_account is None:
            self.editor_copy_label.configure(text="登录账号后，保存笔记会自动写入最后修改署名并刷新索引。")
            self.settings_copy_label.configure(text="这里可查看运行状态、切换模型，并管理本地账号与运行时。")
            self._sync_send_button_state()
            return

        signature = self._account_signature()
        role_text = account_role_label(self.current_account.get("role"))
        self.editor_copy_label.configure(text=f"保存后将以 {signature} 署名，并刷新向量索引。")
        self.settings_copy_label.configure(text=f"当前账号 {signature} · {role_text} 可在这里管理模型、运行时与本地账号。")
        self._sync_send_button_state()

    def submit_auth(self) -> None:
        if self.account_busy:
            return

        username = self.auth_username_var.get().strip()
        password = self.auth_password_var.get()
        if not username:
            self.auth_status_label.configure(text="请输入用户名。")
            self.set_status("请输入用户名。", tone="error")
            return
        if not password:
            self.auth_status_label.configure(text="请输入密码。")
            self.set_status("请输入密码。", tone="error")
            return
        self.account_busy = True
        self._set_auth_controls_enabled(False)
        progress_text = "正在验证账号信息..."
        self.auth_status_label.configure(text=progress_text)
        self.set_status(progress_text)

        def worker():
            account = authenticate_account(username, password)
            if account is None:
                raise ValueError("用户名或密码错误。")
            return account

        def on_success(account: dict) -> None:
            self.account_busy = False
            self._set_auth_controls_enabled(True)
            self._complete_login(account)

        def on_error(exc: Exception) -> None:
            self.account_busy = False
            self._set_auth_controls_enabled(True)
            message = self._auth_error_message(exc)
            self.auth_status_label.configure(text=message)
            self.set_status(message, tone="error")
            messagebox.showerror("登录失败", message)

        self._run_worker(worker, on_success, on_error)

    def register_auth(self) -> None:
        if self.account_busy:
            return

        username = self.auth_username_var.get().strip()
        password = self.auth_password_var.get()
        if not username:
            self.auth_status_label.configure(text="请输入用户名。")
            self.set_status("请输入用户名。", tone="error")
            return
        if not password:
            self.auth_status_label.configure(text="请输入密码。")
            self.set_status("请输入密码。", tone="error")
            return

        role = "member"
        progress_text = "正在注册账号..."

        self.account_busy = True
        self._set_auth_controls_enabled(False)
        self.auth_status_label.configure(text=progress_text)
        self.set_status(progress_text)

        def worker():
            create_account_item(username, password, role=role)
            account = authenticate_account(username, password)
            if account is None:
                raise RuntimeError("注册成功，但自动登录失败。")
            return account

        def on_success(account: dict) -> None:
            self.account_busy = False
            self._set_auth_controls_enabled(True)
            self._complete_login(account)

        def on_error(exc: Exception) -> None:
            self.account_busy = False
            self._set_auth_controls_enabled(True)
            message = self._auth_error_message(exc)
            self.auth_status_label.configure(text=message)
            self.set_status(message, tone="error")
            messagebox.showerror("注册失败", message)

        self._run_worker(worker, on_success, on_error)

    def _complete_login(self, account: dict) -> None:
        self.current_account = account
        self.last_username = str(account.get("username") or "")
        self.chat_items = self._load_chat_items(self.last_username)
        self.auth_password_var.set("")
        self.current_note_id = None
        self._sync_send_button_state()
        self.render_chat_history()
        self.load_notes(show_feedback=False)
        self._update_account_context()
        self.refresh_settings_status(silent=True)
        self.show_page("main")
        self.set_status(f"已登录 {self._account_signature(account)}。", tone="success")

    def _center_window(self) -> None:
        self.root.update_idletasks()
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        self.root.geometry(f"{width}x{height}+{x}+{y}")

    def _set_window_mode(self, mode: str) -> None:
        if mode == self.window_mode:
            return
        if mode == "login":
            self.root.geometry(LOGIN_WINDOW_GEOMETRY)
            self.root.minsize(*LOGIN_WINDOW_MIN_SIZE)
            self.root.resizable(False, False)
            if hasattr(self, "shell"):
                self.shell.configure(padx=0, pady=0)
            self.window_mode = mode
            self._center_window()
            if hasattr(self, "status_line"):
                self.status_line.pack_forget()
            return

        self.root.geometry(APP_WINDOW_GEOMETRY)
        self.root.minsize(*APP_WINDOW_MIN_SIZE)
        self.root.resizable(True, True)
        if hasattr(self, "shell"):
            self.shell.configure(padx=self.shell_padding, pady=self.shell_padding)
        self.window_mode = mode
        self._center_window()
        if hasattr(self, "status_line") and self.status_line.winfo_manager() != "pack":
            self.status_line.pack(fill="x")

    def _set_model_chip(self, model_name: str) -> None:
        short_name = model_name if len(model_name) <= 28 else model_name[:25] + "..."
        self.model_chip.configure(text=f"模型: {short_name}")

    def _chat_return_shortcut(self, event=None) -> str | None:
        if event is not None and (event.state & 0x0001):
            return None
        self.send_question()
        return "break"

    def _hide_question_placeholder(self) -> None:
        if hasattr(self, "question_placeholder") and self.question_placeholder.winfo_manager() == "place":
            self.question_placeholder.place_forget()

    def _show_question_placeholder(self) -> None:
        if hasattr(self, "question_placeholder") and self.question_placeholder.winfo_manager() != "place":
            self.question_placeholder.place(x=14, y=12)

    def _sync_question_placeholder(self) -> None:
        if not hasattr(self, "question_input"):
            return
        has_text = bool(self.question_input.get("1.0", "end").strip())
        focused = self.question_input.focus_get() is self.question_input
        if has_text or focused:
            self._hide_question_placeholder()
            return
        self._show_question_placeholder()

    def _on_question_focus_in(self, _event=None) -> None:
        self._hide_question_placeholder()

    def _on_question_focus_out(self, _event=None) -> None:
        self._sync_question_placeholder()

    def _on_question_key_release(self, _event=None) -> None:
        self._sync_question_placeholder()

    def _submit_shortcut(self, _event=None) -> str:
        self.send_question()
        return "break"

    def _save_shortcut(self, _event=None) -> str:
        if self.current_page == "editor" and self.editor_mode == "edit":
            self.save_note()
            return "break"
        return "break"

    def _open_search_shortcut(self, _event=None) -> str:
        if self.current_account is None:
            return "break"
        self.open_note_search_window()
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
        dependency_window_open = self._dependency_manager_is_open()
        model_window_open = self.model_manager_window is not None and self.model_manager_window.winfo_exists()
        if self.current_page == "settings" and not dependency_window_open and not model_window_open and not self.refresh_in_flight and not self.model_busy:
            self.refresh_settings_status(silent=True)
        try:
            self.root.after(30000, self._schedule_auto_refresh)
        except tk.TclError:
            return

    def _handle_exception(self, exc: Exception) -> None:
        self.set_status(str(exc), tone="error")
        messagebox.showerror("Private Note 客户端", str(exc))

    def show_page(self, name: str) -> None:
        if name != "login" and self.current_account is None:
            name = "login"
        self._set_window_mode("login" if name == "login" else "app")
        if name == "main":
            self.render_notes()
            self.render_chat_history()
            self._sync_question_placeholder()
        elif name == "settings":
            if self.settings_payload is not None:
                self.apply_settings_payload(self.settings_payload)
                self._set_settings_loading(False)
            else:
                self._set_settings_loading(True)
            self.root.after(10, lambda: self.refresh_settings_status(silent=True))
        elif name == "login":
            self._refresh_auth_view()
        self.pages[name].tkraise()
        self.current_page = name

    def back_to_main(self) -> None:
        if self.current_account is None:
            self.show_page("login")
            return
        self._attempt_leave_editor(lambda: self.show_page("main"))

    def open_settings_page(self) -> None:
        if self.current_account is None:
            self.show_page("login")
            self.set_status("请先登录账号。", tone="error")
            return
        self._attempt_leave_editor(lambda: self.show_page("settings"))

    def open_new_note(self) -> None:
        if self.current_account is None:
            self.show_page("login")
            self.set_status("请先登录账号。", tone="error")
            return
        def proceed() -> None:
            self.editor_highlight_query = ""
            self.editing_note_id = None
            self.current_note_id = None
            self.editor_title_label.configure(text="新建笔记")
            self._load_editor_note(
                {
                    "title": "",
                    "body": "",
                    "created_at": "",
                    "updated_at": "",
                    "created_by": self._account_signature(),
                    "updated_by": self._account_signature(),
                },
                edit_mode=True,
            )
            self.show_page("editor")
            self.editor_title_entry.focus_set()
            self.set_status("准备写入新笔记。")

        self._attempt_leave_editor(proceed)

    def open_edit_note(self, note_id: int, highlight_query: str = "") -> None:
        if self.current_account is None:
            self.show_page("login")
            self.set_status("请先登录账号。", tone="error")
            return
        def proceed() -> None:
            self.editor_highlight_query = str(highlight_query or "").strip()
            note = next((item for item in self.notes_cache if item["id"] == note_id), None)
            if note is None:
                note = get_note_item(note_id)
            if note is None:
                self.set_status(f"未找到笔记 #{note_id}", tone="error")
                return
            self.editing_note_id = note_id
            self.current_note_id = note_id
            self.editor_title_label.configure(text=f"笔记 #{note_id}")
            self._load_editor_note(note, edit_mode=False)
            self.show_page("editor")
            self.render_notes()
            self.set_status(f"正在查看笔记 #{note_id}。")

        self._attempt_leave_editor(proceed)

    def clear_editor(self) -> None:
        if not self.editor_title_var.get().strip() and not self.editor_text.get("1.0", "end").strip():
            return
        if messagebox.askyesno("清空内容", "确定要清空当前笔记内容吗？"):
            self.editor_title_var.set("")
            self.editor_text.delete("1.0", "end")
            self._sync_editor_dirty_state()
            self.set_status("已清空编辑内容。")

    def save_note(self) -> None:
        if self.note_busy:
            return
        try:
            account = self._require_account()
        except RuntimeError as exc:
            self.show_page("login")
            self.set_status(str(exc), tone="error")
            return
        title = self.editor_title_var.get().strip()
        body = self.editor_text.get("1.0", "end").strip()
        if not title:
            self.set_status("请输入笔记标题。", tone="error")
            return

        self.note_busy = True
        self._set_editor_mode("edit")
        start_text = "更新笔记并重建索引..." if self.editing_note_id is not None else "保存笔记并建立索引..."
        self.set_status(start_text)
        note_id = self.editing_note_id

        def worker():
            actor = self._account_signature(account)
            payload = {"title": title, "body": body}
            if note_id is None:
                return {"mode": "create", "note_id": create_note_item(payload, actor)}
            updated = update_note_item_content(note_id, payload, actor)
            return {"mode": "update", "note_id": updated["id"]}

        def on_success(payload: dict) -> None:
            self.note_busy = False
            self.current_note_id = int(payload["note_id"])
            note = get_note_item(self.current_note_id)
            if note is not None:
                self.editing_note_id = int(note["id"])
                self.editor_title_label.configure(text=f"笔记 #{note['id']}")
                self._load_editor_note(note, edit_mode=False)
            else:
                self.editor_loaded_signature = (title, body)
                self.editor_dirty = False
            self.load_notes(show_feedback=False)
            verb = "已创建" if payload["mode"] == "create" else "已更新"
            self.set_status(f"{verb}笔记 #{payload['note_id']}。", tone="success")
            pending = self.editor_pending_action
            self.editor_pending_action = None
            if callable(pending):
                pending()
            else:
                self.show_page("main")

        def on_error(exc: Exception) -> None:
            self.note_busy = False
            self._set_editor_mode("edit")
            self.set_status(str(exc), tone="error")
            messagebox.showerror("保存失败", str(exc))

        self._run_worker(worker, on_success, on_error)

    def _prompt_note_export_path(self, note: dict) -> str:
        return filedialog.asksaveasfilename(
            title="导出笔记",
            defaultextension=".pnote.json",
            initialfile=note_export_filename(note),
            filetypes=[
                ("Private Note 单篇笔记", "*.pnote.json"),
                ("JSON 文件", "*.json"),
                ("所有文件", "*.*"),
            ],
        )

    def export_note_to_file(self, note_id: int) -> None:
        note = get_note_item(note_id)
        if note is None:
            self.set_status(f"未找到笔记 #{note_id}", tone="error")
            return
        path = self._prompt_note_export_path(note)
        if not path:
            return
        self.set_status(f"正在导出笔记 #{note_id}...")

        def worker():
            payload = export_note_item(note_id)
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
            return str(target)

        def on_success(saved_path: str) -> None:
            self.set_status(f"已导出到 {saved_path}", tone="success")

        def on_error(exc: Exception) -> None:
            self.set_status(str(exc), tone="error")
            messagebox.showerror("导出笔记失败", str(exc))

        self._run_worker(worker, on_success, on_error)

    def export_current_note(self) -> None:
        if self.note_busy:
            return
        note_id = self.current_note_id
        if note_id is None:
            self.set_status("当前笔记还没有可导出的已保存内容。", tone="error")
            return
        if self.current_page == "editor" and self.editor_dirty:
            decision = messagebox.askyesnocancel("导出笔记", "当前笔记有未保存修改。是否先保存，再导出？")
            if decision is None:
                return
            if decision:
                self.editor_pending_action = lambda item_id=note_id: self.export_note_to_file(item_id)
                self.save_note()
                return
        self.export_note_to_file(note_id)

    def import_note_from_file(self) -> None:
        if self.note_busy:
            return
        try:
            account = self._require_account()
        except RuntimeError as exc:
            self.show_page("login")
            self.set_status(str(exc), tone="error")
            return
        path = filedialog.askopenfilename(
            title="导入笔记",
            filetypes=[
                ("Private Note 单篇笔记", "*.pnote.json"),
                ("JSON 文件", "*.json"),
                ("所有文件", "*.*"),
            ],
        )
        if not path:
            return
        self.note_busy = True
        self.set_status("正在导入笔记并重建索引...")
        self._set_editor_mode("preview")

        def worker():
            with Path(path).open("r", encoding="utf-8") as file:
                payload = json.load(file)
            if not isinstance(payload, dict):
                raise ValueError("导入文件格式不正确")
            actor = self._account_signature(account)
            return import_note_item(payload, actor)

        def on_success(note: dict) -> None:
            self.note_busy = False
            note_id = int(note["id"])
            self.load_notes(show_feedback=False)
            self.open_edit_note(note_id)
            self.set_status(f"已导入笔记 #{note_id}。", tone="success")

        def on_error(exc: Exception) -> None:
            self.note_busy = False
            self._set_editor_mode("edit" if self.editor_mode == "edit" else "preview")
            self.set_status(str(exc), tone="error")
            messagebox.showerror("导入笔记失败", str(exc))

        self._run_worker(worker, on_success, on_error)

    def confirm_delete_note(self, note_id: int) -> None:
        note = next((item for item in self.notes_cache if item["id"] == note_id), None)
        preview = note_preview_text(note, 64) if note else f"#{note_id}"
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
        self.note_visuals = {}
        if not self.notes_cache:
            empty = RoundedPanel(
                self.notes_area.content,
                fill=self.palette.panel_bg,
                outline=self.palette.border,
                outer_bg=self.palette.panel_alt,
                radius=22,
                pad_x=16,
                pad_y=18,
            )
            empty.pack(fill="x", pady=(0, 10))
            title = tk.Label(empty.content, text="还没有笔记", font=("Microsoft YaHei UI", 12, "bold"), anchor="w")
            title.pack(anchor="w")
            copy = tk.Label(
                empty.content,
                text="先在左上角新建一条笔记，之后就可以直接提问。",
                font=("Microsoft YaHei UI", 10),
                anchor="w",
                justify="left",
            )
            copy.pack(anchor="w", pady=(6, 0))
            title.configure(bg=self.palette.panel_alt, fg=self.palette.text)
            copy.configure(bg=self.palette.panel_alt, fg=self.palette.muted)
            return

        for note in self.notes_cache:
            active = note["id"] == self.current_note_id
            bg = self.palette.chip_bg if active else self.palette.panel_bg
            border = self.palette.accent if active else self.palette.border

            card = RoundedPanel(
                self.notes_area.content,
                fill=bg,
                outline=border,
                outer_bg=self.palette.panel_alt,
                radius=22,
                pad_x=16,
                pad_y=16,
                cursor="hand2",
            )
            card.pack(fill="x", pady=(0, 10))

            marker = tk.Frame(card.content, bg=self.palette.accent if active else self.palette.border, height=3)
            marker.pack(fill="x", pady=(0, 12))

            title = tk.Label(
                card.content,
                text=note_title_text(note, f"笔记 #{note['id']}")[:42],
                bg=bg,
                fg=self.palette.text,
                font=("Microsoft YaHei UI", 11, "bold"),
                anchor="w",
                justify="left",
            )
            title.pack(fill="x", anchor="w")

            preview = tk.Label(
                card.content,
                text=note_preview_text(note),
                bg=bg,
                fg=self.palette.muted,
                font=("Microsoft YaHei UI", 10),
                anchor="w",
                justify="left",
                wraplength=280,
            )
            preview.pack(fill="x", anchor="w", pady=(8, 10))

            meta = tk.Label(
                card.content,
                text=(
                    f"最后修改：{note.get('updated_by') or note.get('created_by') or '未署名'}"
                    f" · {format_time(note.get('updated_at') or note.get('created_at')) or '未知'}"
                ),
                bg=bg,
                fg=self.palette.muted,
                font=("Microsoft YaHei UI", 9),
                anchor="w",
            )
            meta.pack(fill="x", anchor="w")

            actions = tk.Frame(card.content, bg=bg)
            actions.pack(fill="x", pady=(12, 0))

            open_button = self._make_button(actions, "查看", lambda item_id=note["id"]: self.open_edit_note(item_id), style="PrimarySmall.TButton")
            open_button.pack(side="left", padx=(0, 8))

            export_button = self._make_button(actions, "导出", lambda item_id=note["id"]: self.export_note_to_file(item_id), style="GhostSmall.TButton")
            export_button.pack(side="left", padx=(0, 8))

            delete_button = self._make_button(actions, "删除", lambda item_id=note["id"]: self.confirm_delete_note(item_id), style="DangerSmall.TButton")
            delete_button.pack(side="left")

            note_visual = {
                "note_id": int(note["id"]),
                "card": card,
                "marker": marker,
                "title": title,
                "preview": preview,
                "meta": meta,
                "actions": actions,
                "edit_button": open_button,
                "export_button": export_button,
                "delete_button": delete_button,
                "fill": bg,
                "outline": border,
                "marker_fill": self.palette.accent if active else self.palette.border,
            }
            self.note_visuals[int(note["id"])] = note_visual

            for widget in (card, card.content, marker, title, preview, meta, actions):
                widget.bind("<Button-1>", lambda _event, item_id=note["id"]: self.select_note(item_id))
                widget.bind("<Enter>", lambda _event, item_id=note["id"]: self._on_note_hover(item_id, True), add="+")
                widget.bind("<Leave>", lambda _event, item_id=note["id"]: self._on_note_hover(item_id, False), add="+")
            self._restyle_note_card(int(note["id"]), animate=False)

    def select_note(self, note_id: int) -> None:
        self.current_note_id = note_id
        for target_id in list(self.note_visuals):
            self._restyle_note_card(target_id, animate=True)
        self.set_status(f"已选中笔记 #{note_id}。")

    def _on_note_hover(self, note_id: int, hovering: bool) -> None:
        if hovering:
            self.hovered_note_id = note_id
        elif self.hovered_note_id == note_id:
            self.hovered_note_id = None
        self._restyle_note_card(note_id, animate=True)

    def _note_target_colors(self, note_id: int) -> tuple[str, str, str]:
        active = note_id == self.current_note_id
        hovered = note_id == self.hovered_note_id
        if active:
            fill = blend_hex(self.palette.chip_bg, self.palette.panel_bg, 0.12) if hovered else self.palette.chip_bg
            outline = self.palette.accent_hover if hovered else self.palette.accent
            marker = self.palette.accent_hover if hovered else self.palette.accent
            return fill, outline, marker
        if hovered:
            return (
                blend_hex(self.palette.panel_bg, self.palette.chip_bg, 0.38),
                blend_hex(self.palette.border, self.palette.accent, 0.55),
                blend_hex(self.palette.border, self.palette.accent, 0.35),
            )
        return self.palette.panel_bg, self.palette.border, self.palette.border

    def _apply_note_visual(self, note_visual: dict, fill: str, outline: str, marker_fill: str) -> None:
        note_visual["fill"] = fill
        note_visual["outline"] = outline
        note_visual["marker_fill"] = marker_fill
        note_visual["card"].set_colors(fill=fill, outline=outline, outer_bg=self.palette.panel_alt)
        note_visual["marker"].configure(bg=marker_fill)
        note_visual["title"].configure(bg=fill, fg=self.palette.text)
        note_visual["preview"].configure(bg=fill, fg=self.palette.muted)
        note_visual["meta"].configure(bg=fill, fg=self.palette.muted)
        note_visual["actions"].configure(bg=fill)
        note_visual["edit_button"].configure(bg=fill)
        note_visual["delete_button"].configure(bg=fill)

    def _restyle_note_card(self, note_id: int, *, animate: bool) -> None:
        note_visual = self.note_visuals.get(note_id)
        if note_visual is None:
            return
        target_fill, target_outline, target_marker = self._note_target_colors(note_id)
        if not animate:
            self._apply_note_visual(note_visual, target_fill, target_outline, target_marker)
            return
        start_fill = note_visual.get("fill", target_fill)
        start_outline = note_visual.get("outline", target_outline)
        start_marker = note_visual.get("marker_fill", target_marker)
        after_id = note_visual.get("after_id")
        if after_id:
            try:
                self.root.after_cancel(after_id)
            except tk.TclError:
                pass

        def step(index: int) -> None:
            ratio = index / 4
            self._apply_note_visual(
                note_visual,
                blend_hex(start_fill, target_fill, ratio),
                blend_hex(start_outline, target_outline, ratio),
                blend_hex(start_marker, target_marker, ratio),
            )
            if index < 4:
                note_visual["after_id"] = self.root.after(24, step, index + 1)
            else:
                note_visual["after_id"] = None

        step(1)

    def _restyle_chat_visuals(self) -> None:
        for chat_visual in self.chat_visuals:
            if chat_visual["row"].winfo_exists():
                is_user = bool(chat_visual["is_user"])
                chat_visual["row"].configure(bg=self.palette.panel_alt)
                chat_visual["message_line"].configure(bg=self.palette.panel_alt)
                chat_visual["bubble_wrap"].configure(bg=self.palette.panel_alt)
                chat_visual["avatar"].set_palette(self.palette, self.palette.panel_alt)
                bubble_fill = self.palette.bubble_user if is_user else self.palette.bubble_assistant
                bubble_outline = bubble_fill if is_user else self.palette.border
                chat_visual["bubble"].set_colors(
                    fill=bubble_fill,
                    fg=self.palette.text,
                    outline=bubble_outline,
                    outer_bg=self.palette.panel_alt,
                )
                chat_visual["meta"].configure(bg=self.palette.panel_alt, fg=self.palette.muted)

    def render_chat_history(self) -> None:
        self.chat_area.clear()
        self.chat_visuals = []
        wrap_width = max(300, min(720, self.root.winfo_width() - 700))

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

            message_line = tk.Frame(row, bg=self.palette.panel_alt)
            message_line.pack(anchor="e" if is_user else "w", padx=18)

            bubble_wrap = tk.Frame(message_line, bg=self.palette.panel_alt)
            avatar = ChatAvatar(
                message_line,
                kind="user" if is_user else "assistant",
                palette=self.palette,
                outer_bg=self.palette.panel_alt,
                size=42,
            )
            if is_user:
                bubble_wrap.pack(side="left")
                avatar.pack(side="left", padx=(10, 0))
            else:
                avatar.pack(side="left", padx=(0, 10))
                bubble_wrap.pack(side="left")

            bubble_bg = self.palette.bubble_user if is_user else self.palette.bubble_assistant
            bubble = RoundedBubble(
                bubble_wrap,
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
            bubble.pack(anchor="e" if is_user else "w")

            meta_text = format_time(item.get("time"))
            if not is_user:
                meta_text = f"{meta_text}  {item.get('model') or self._safe_runtime_model()}".strip()
                elapsed_seconds = item.get("elapsed_seconds")
                if elapsed_seconds is not None:
                    try:
                        meta_text = f"{meta_text}  {float(elapsed_seconds):.1f}s".strip()
                    except (TypeError, ValueError):
                        pass
                runtime_profile = str(item.get("runtime_profile") or "").strip().lower()
                if runtime_profile:
                    mode_short = launcher_core.RUNTIME_PROFILE_SHORT_LABELS.get(runtime_profile, runtime_profile.upper())
                    meta_text = f"{meta_text}  {mode_short}".strip()
            meta = tk.Label(
                bubble_wrap,
                text=meta_text,
                bg=self.palette.panel_alt,
                fg=self.palette.muted,
                font=("Microsoft YaHei UI", 9),
                anchor="e" if is_user else "w",
                justify="right" if is_user else "left",
            )
            meta.pack(anchor="e" if is_user else "w", pady=(6, 0))
            if not is_user:
                sources = item.get("sources") or []
                if isinstance(sources, list) and sources:
                    sources_wrap = tk.Frame(bubble_wrap, bg=self.palette.panel_alt)
                    sources_wrap.pack(anchor="w", fill="x", pady=(8, 0))
                    sources_title = tk.Label(
                        sources_wrap,
                        text="引用来源",
                        bg=self.palette.panel_alt,
                        fg=self.palette.muted,
                        font=("Microsoft YaHei UI", 9, "bold"),
                        anchor="w",
                    )
                    sources_title.pack(anchor="w")
                    for source in sources[:1]:
                        if not isinstance(source, dict):
                            continue
                        source_card = tk.Frame(
                            sources_wrap,
                            bg=self.palette.panel_bg,
                            highlightthickness=1,
                            highlightbackground=self.palette.border,
                            bd=0,
                            padx=10,
                            pady=8,
                        )
                        source_card.pack(fill="x", pady=(6, 0))
                        source_title = tk.Label(
                            source_card,
                            text=str(source.get("title") or f"笔记 #{source.get('note_id') or '-'}"),
                            bg=self.palette.panel_bg,
                            fg=self.palette.text,
                            font=("Microsoft YaHei UI", 9, "bold"),
                            anchor="w",
                        )
                        source_title.pack(anchor="w")
                        snippet = str(source.get("snippet") or "")
                        if snippet:
                            source_snippet = tk.Label(
                                source_card,
                                text=snippet,
                                bg=self.palette.panel_bg,
                                fg=self.palette.muted,
                                font=("Microsoft YaHei UI", 9),
                                anchor="w",
                                justify="left",
                                wraplength=max(220, wrap_width - 60),
                            )
                            source_snippet.pack(anchor="w", fill="x", pady=(4, 0))
                        source_button = self._make_button(
                            source_card,
                            "打开笔记",
                            lambda item_source=dict(source): self._open_note_from_source(item_source),
                            style="GhostSmall.TButton",
                        )
                        source_button.pack(anchor="w", pady=(8, 0))
            self.chat_visuals.append(
                {
                    "is_user": is_user,
                    "row": row,
                    "message_line": message_line,
                    "bubble_wrap": bubble_wrap,
                    "avatar": avatar,
                    "bubble": bubble,
                    "meta": meta,
                }
            )

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
        if self.runtime_busy:
            self.set_status("正在切换运行模式，请稍候。", tone="error")
            return
        if self.model_busy:
            self.set_status("正在切换模型，请稍候。", tone="error")
            return
        if self.current_account is None:
            self.show_page("login")
            self.set_status("请先登录账号。", tone="error")
            return
        question = self.question_input.get("1.0", "end").strip()
        if not question:
            self.set_status("请输入问题。", tone="error")
            return

        self.chat_busy = True
        self._sync_send_button_state()
        self._append_chat_item("user", question)
        self.question_input.delete("1.0", "end")
        self._sync_question_placeholder()
        self.set_status("正在检索笔记并生成回答...")

        def worker():
            started_at = time.perf_counter()
            payload = ask_question(question)
            payload["elapsed_seconds"] = time.perf_counter() - started_at
            return payload

        def on_success(payload: dict[str, object]) -> None:
            self.chat_busy = False
            self._sync_send_button_state()
            answer = payload.get("answer") or "未返回内容"
            model = payload.get("model") or self._safe_runtime_model()
            runtime_profile = str(payload.get("runtime_profile") or "")
            elapsed_seconds = payload.get("elapsed_seconds")
            sources = payload.get("sources") if isinstance(payload.get("sources"), list) else []
            try:
                elapsed_value = float(elapsed_seconds) if elapsed_seconds is not None else None
            except (TypeError, ValueError):
                elapsed_value = None
            self._set_model_chip(model)
            self._append_chat_item(
                "assistant",
                answer,
                model=model,
                runtime_profile=runtime_profile,
                elapsed_seconds=elapsed_value,
                sources=sources,
            )
            self.set_status("回答完成。", tone="success")

        def on_error(exc: Exception) -> None:
            self.chat_busy = False
            self._sync_send_button_state()
            self.set_status(str(exc), tone="error")
            messagebox.showerror("问答失败", str(exc))

        self._run_worker(worker, on_success, on_error)

    def refresh_settings_status(self, silent: bool = False) -> None:
        if self.refresh_in_flight:
            return
        self.refresh_in_flight = True
        if self.current_page == "settings" and self.settings_payload is None:
            self._set_settings_loading(True)
        if not silent:
            self.set_status("正在刷新运行状态...")

        def worker():
            payload = launcher_core.get_status(os.getpid())
            payload["accounts"] = list_account_items()
            runtime_model = launcher_core.get_saved_llm_model()
            runtime_embed_model = launcher_core.get_saved_embed_model()
            available_models: list[str] = []
            available_embed_models: list[str] = []
            selected_profile = str(payload["ollama"].get("selected_profile") or launcher_core.get_saved_runtime_profile())
            selected_label = self._runtime_profile_display(selected_profile)
            profiles = payload["ollama"].get("profiles") or {}
            cpu_status = profiles.get("cpu") or {}
            gpu_status = profiles.get("gpu") or {}
            gpu_bundle_ready = bool(gpu_status.get("bundled_available"))
            try:
                if payload["ollama"]["running"]:
                    init_runtime_settings(force_refresh=True)
                    runtime_model = get_llm_model()
                    runtime_embed_model = get_embed_model()
                    available_models = list_chat_models()
                    available_embed_models = list_embedding_models()
            except Exception as exc:
                payload["runtime_model_error"] = str(exc)
            payload["runtime_model"] = runtime_model
            payload["runtime_embed_model"] = runtime_embed_model
            payload["available_models"] = available_models
            payload["available_embed_models"] = available_embed_models
            payload["dependency_hint"] = (
                f"当前运行模式：{selected_label}。"
                f"CPU 包：{'已内置' if cpu_status.get('bundled_available') else '缺失'}；"
                f"GPU 包：{'已内置' if gpu_bundle_ready else '缺失'}。"
                f"推荐模型：对话 {', '.join(launcher_core.RECOMMENDED_CHAT_MODELS)}，向量 {launcher_core.DEFAULT_EMBED_MODEL}。"
            )
            return payload

        def on_success(payload: dict) -> None:
            self.refresh_in_flight = False
            self.settings_payload = payload
            self.apply_settings_payload(payload)
            self._set_settings_loading(False)
            if not silent:
                self.set_status("运行状态已更新。", tone="success")

        def on_error(exc: Exception) -> None:
            self.refresh_in_flight = False
            self._set_settings_loading(False)
            self.set_status(str(exc), tone="error")
            if not silent:
                messagebox.showerror("刷新失败", str(exc))

        self._run_worker(worker, on_success, on_error)

    def apply_settings_payload(self, payload: dict) -> None:
        environment = payload.get("environment", {})
        ollama_state = payload.get("ollama", {})
        profiles = ollama_state.get("profiles") or {}
        selected_profile = self._runtime_profile_from_display(ollama_state.get("selected_profile"))
        selected_profile_label = self._runtime_profile_display(selected_profile)
        cpu_name = str(environment.get("cpu_name") or "").strip() or "未知 CPU"
        gpu_profile = profiles.get("gpu") or {}
        gpu_bundled = bool(gpu_profile.get("bundled_available"))
        gpu_download_supported = bool(gpu_profile.get("download_supported"))
        gpu_installed = bool(gpu_profile.get("private_installed"))
        gpu_hardware_names = list(gpu_profile.get("hardware_names") or [])
        gpu_hardware_available = bool(gpu_profile.get("hardware_available"))

        self._set_model_chip(str(payload.get("runtime_model") or launcher_core.get_saved_llm_model()))
        self._set_badge(self.runtime_desktop_value, "运行中", "good")
        self._set_badge(self.runtime_ollama_value, self._describe_ollama_status(ollama_state), self._ollama_tone(ollama_state))
        if gpu_installed and gpu_hardware_available:
            gpu_status_text = "已部署 / 可用"
            gpu_status_tone = "good"
        elif gpu_installed:
            gpu_status_text = "已部署 / 未检出"
            gpu_status_tone = "neutral"
        elif gpu_bundled or gpu_download_supported:
            gpu_status_text = "可部署"
            gpu_status_tone = "neutral"
        else:
            gpu_status_text = "缺失"
            gpu_status_tone = "danger"
        self._set_badge(self.runtime_gpu_bundle_value, gpu_status_text, gpu_status_tone)
        self._set_badge(
            self.runtime_profile_value,
            self._describe_runtime_profile_badge(selected_profile, environment, gpu_hardware_names),
            "accent" if selected_profile == "gpu" else "neutral",
        )
        self._set_badge(self.runtime_model_value, str(payload.get("runtime_model") or "未知"), "accent")
        self._set_badge(self.runtime_embed_model_value, str(payload.get("runtime_embed_model") or "未知"), "neutral")

        lines = [
            f"project_root : {environment.get('project_root') or '-'}",
            f"runtime_dir  : {environment.get('runtime_dir') or '-'}",
            f"data_dir     : {environment.get('data_dir') or '-'}",
            f"log_dir      : {environment.get('log_dir') or '-'}",
            f"python_ready : {'yes' if environment.get('python_ready') else 'no'}",
            f"python_mode  : {environment.get('python_mode') or '-'}",
            f"python_ver   : {environment.get('python_version') or '-'}",
            f"python_path  : {environment.get('python_path') or '-'}",
            f"python_home  : {environment.get('python_home') or '-'}",
            f"uv_available : {'yes' if environment.get('uv_available') else 'no'}",
            f"uv_path      : {environment.get('uv_path') or '-'}",
            f"nvidia_smi   : {environment.get('nvidia_smi') or '-'}",
            f"nvidia_gpu   : {', '.join(environment.get('nvidia_gpu_names') or []) or '-'}",
            f"ollama_path  : {ollama_state.get('path') or '-'}",
            f"bundle_ver   : {ollama_state.get('bundled_version') or '-'}",
            f"bundle_path  : {ollama_state.get('bundled_source') or '-'}",
            f"models_dir   : {ollama_state.get('private_models_dir') or '-'}",
            f"ollama_url   : {ollama_state.get('base_url') or '-'}",
        ]
        self.environment_label.configure(text="\n".join(lines))

        self.runtime_mode_button.configure(text=(launcher_core.RUNTIME_PROFILE_SHORT_LABELS.get(selected_profile, selected_profile.upper())))

        available_models = list(payload.get("available_models") or [])
        available_embed_models = list(payload.get("available_embed_models") or [])
        runtime_model = str(payload.get("runtime_model") or launcher_core.get_saved_llm_model())
        runtime_embed_model = str(payload.get("runtime_embed_model") or launcher_core.get_saved_embed_model())
        model_values = available_models[:] if available_models else [runtime_model]
        if runtime_model and runtime_model not in model_values:
            model_values.insert(0, runtime_model)
        embed_model_values = available_embed_models[:] if available_embed_models else [runtime_embed_model]
        if runtime_embed_model and runtime_embed_model not in embed_model_values:
            embed_model_values.insert(0, runtime_embed_model)

        self.model_event_suppressed = True
        self.model_combo.configure(values=model_values)
        self.model_var.set(runtime_model)
        self.model_event_suppressed = False
        self.embed_model_event_suppressed = True
        self.embed_model_combo.configure(values=embed_model_values)
        self.embed_model_var.set(runtime_embed_model)
        self.embed_model_event_suppressed = False

        self.model_combo.configure(state="readonly" if model_values else "disabled")
        self.embed_model_combo.configure(state="readonly" if embed_model_values else "disabled")
        self.dependency_feedback_label.configure(text=str(payload.get("dependency_hint") or ""))

        self._update_runtime_buttons(ollama_state, runtime_model)
        self._apply_accounts_payload(list(payload.get("accounts") or []))
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

    def _apply_accounts_payload(self, accounts: list[dict]) -> None:
        self.accounts_cache = accounts
        if self.current_account is not None:
            refreshed = next((item for item in accounts if int(item["id"]) == int(self.current_account["id"])), None)
            if refreshed is not None:
                self.current_account = refreshed
        self._update_account_context()
        self.members_button.configure(text=f"所有成员 ({len(accounts)})")
        self._refresh_members_window()
        self._set_button_enabled(self.logout_button, self.current_account is not None and not self.account_busy)

    def open_members_window(self) -> None:
        if self.members_window is not None and self.members_window.winfo_exists():
            self.members_window.deiconify()
            self.members_window.lift()
            self.members_window.focus_force()
            self._refresh_members_window()
            return

        window = tk.Toplevel(self.root)
        window.title("所有成员")
        window.geometry("520x430")
        window.minsize(460, 360)
        window.configure(bg=self.palette.root_bg)
        window.transient(self.root)
        window.protocol("WM_DELETE_WINDOW", self._close_members_window)
        self.members_window = window

        header = tk.Frame(window, bg=self.palette.root_bg, padx=16, pady=14)
        header.pack(fill="x")
        self.surface_roles.append((header, "root"))
        title = tk.Label(header, text="已注册成员名单", font=("Microsoft YaHei UI", 14, "bold"), anchor="w")
        title.pack(anchor="w")
        self.members_window_title_label = title

        copy = tk.Label(
            header,
            text="账号信息仅保存在当前设备本地数据库。",
            font=("Microsoft YaHei UI", 10),
            anchor="w",
        )
        copy.pack(anchor="w", pady=(4, 0))
        self.members_window_copy_label = copy

        self.members_list_area = ScrollArea(window, self.palette.panel_bg, self.palette)
        self.members_list_area.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        self._refresh_members_window()

    def _close_members_window(self) -> None:
        if self.members_window is None:
            return
        try:
            self.members_window.destroy()
        except tk.TclError:
            pass
        self.members_window = None
        self.members_list_area = None
        self.members_window_title_label = None
        self.members_window_copy_label = None

    def _refresh_members_window(self) -> None:
        if self.members_window is None or not self.members_window.winfo_exists():
            self.members_window = None
            self.members_list_area = None
            return
        if self.members_list_area is None or not self.members_list_area.winfo_exists():
            return

        try:
            self.members_window.configure(bg=self.palette.root_bg)
        except tk.TclError:
            return
        if self.members_window_title_label is not None and self.members_window_title_label.winfo_exists():
            self.members_window_title_label.configure(bg=self.palette.root_bg, fg=self.palette.text)
        if self.members_window_copy_label is not None and self.members_window_copy_label.winfo_exists():
            self.members_window_copy_label.configure(bg=self.palette.root_bg, fg=self.palette.muted)
        self.members_list_area.set_colors(self.palette.panel_bg, self.palette)
        self.members_list_area.clear()

        accounts = list(self.accounts_cache)
        if not accounts:
            empty = tk.Label(
                self.members_list_area.content,
                text="暂无已注册账号",
                bg=self.palette.panel_bg,
                fg=self.palette.muted,
                font=("Microsoft YaHei UI", 11),
                anchor="w",
            )
            empty.pack(fill="x", padx=12, pady=12)
            return

        for item in accounts:
            card = tk.Frame(
                self.members_list_area.content,
                bg=self.palette.panel_alt,
                highlightthickness=1,
                highlightbackground=self.palette.border,
                bd=0,
                padx=12,
                pady=10,
            )
            card.pack(fill="x", padx=4, pady=(0, 8))

            username = str(item.get("username") or "").strip() or "未命名账号"
            role_text = account_role_label(item.get("role"))
            created_text = format_time(item.get("created_at"))

            title = tk.Label(card, text=f"{username}  ({role_text})", bg=self.palette.panel_alt, fg=self.palette.text, font=("Microsoft YaHei UI", 11, "bold"), anchor="w")
            title.pack(anchor="w")
            meta = tk.Label(
                card,
                text=f"创建时间: {created_text or '未知'}",
                bg=self.palette.panel_alt,
                fg=self.palette.muted,
                font=("Microsoft YaHei UI", 10),
                anchor="w",
            )
            meta.pack(anchor="w", pady=(6, 0))

    def _set_dependency_manager_controls_enabled(self, enabled: bool) -> None:
        if self.dependency_refresh_button is not None:
            self._set_button_enabled(self.dependency_refresh_button, enabled)

    def _dependency_manager_is_open(self) -> bool:
        return self.dependency_manager_window is not None and self.dependency_manager_window.winfo_exists()

    def _dependency_status_tone(self, entry: dict[str, object]) -> str:
        if bool(entry.get("installed")):
            return "good"
        if str(entry.get("kind") or "") == "runtime" and str(entry.get("source_text") or "") == "当前不可用":
            return "danger"
        return "neutral"

    def _format_dependency_progress(self, entry: dict[str, object], event: dict[str, object]) -> str:
        title = str(entry.get("title") or entry.get("id") or "依赖")
        status = str(event.get("status") or "处理中")
        completed = event.get("completed")
        total = event.get("total")
        if isinstance(completed, (int, float)) and isinstance(total, (int, float)) and float(total) > 0:
            percent = max(0.0, min(100.0, float(completed) / float(total) * 100.0))
            status = f"{status} ({percent:.1f}%)"
        return f"{title} · {status}"

    def _dependency_progress_percent(self, event: dict[str, object]) -> float | None:
        completed = event.get("completed")
        total = event.get("total")
        if isinstance(completed, (int, float)) and isinstance(total, (int, float)) and float(total) > 0:
            return max(0.0, min(1.0, float(completed) / float(total)))
        return None

    def _set_dependency_card_progress(self, entry_id: str, text: str, percent: float | None = None, *, tone: str = "active") -> None:
        widgets = self.dependency_progress_widgets.get(entry_id)
        if not widgets:
            return
        label = widgets.get("label")
        fill = widgets.get("fill")
        if isinstance(label, tk.Label) and label.winfo_exists():
            label.configure(text=text)
        if isinstance(fill, tk.Frame) and fill.winfo_exists():
            if percent is None:
                percent = 0.08 if tone == "active" else 0.0
            color = self.palette.accent
            if tone == "done":
                color = "#22c55e"
            elif tone == "error":
                color = self.palette.danger
            fill.configure(bg=color)
            fill.place_configure(relwidth=max(0.0, min(1.0, percent)))

    def _dependency_model_entries(self) -> list[dict[str, object]]:
        entries: list[dict[str, object]] = []
        for item in self.model_catalog_entries:
            model_name = str(item.get("name") or "").strip()
            if not model_name:
                continue
            installed = bool(item.get("installed"))
            model_type = "向量模型" if bool(item.get("is_embedding")) else "对话模型"
            size_text = format_model_size(item.get("size"))
            hint_parts = [f"来源：内置推荐清单", f"类型：{model_type}"]
            if size_text != "-":
                hint_parts.append(f"大小：{size_text}")
            entries.append(
                {
                    "id": f"model:{model_name}",
                    "kind": "model",
                    "title": model_name,
                    "description": f"{model_type}。安装时会自动拉取到项目私有模型目录。",
                    "installed": installed,
                    "status": "installed" if installed else "missing",
                    "status_text": "已安装" if installed else "未安装",
                    "source_text": "在线拉取",
                    "hint": "；".join(hint_parts),
                    "model_name": model_name,
                    "is_embedding": bool(item.get("is_embedding")),
                }
            )
        return entries

    def _handle_dependency_manager_event(self, payload: dict[str, object]) -> None:
        event_type = str(payload.get("type") or "")
        if event_type == "log":
            message = str(payload.get("message") or "").strip()
            if message:
                if self.dependency_manager_status_label is not None and self.dependency_manager_status_label.winfo_exists():
                    self.dependency_manager_status_label.configure(text=message)
                entry = dict(payload.get("entry") or {})
                entry_id = str(entry.get("id") or "")
                if entry_id:
                    tone = "done" if "完成" in message else "active"
                    percent = 1.0 if tone == "done" else 0.08
                    self._set_dependency_card_progress(entry_id, f"状态：{message}", percent, tone=tone)
            return

        if event_type == "progress":
            entry = dict(payload.get("entry") or {})
            event = payload.get("event") or {}
            if not isinstance(event, dict):
                return
            text = self._format_dependency_progress(entry, event)
            if text == self.dependency_last_status_text:
                return
            self.dependency_last_status_text = text
            if self.dependency_manager_status_label is not None and self.dependency_manager_status_label.winfo_exists():
                self.dependency_manager_status_label.configure(text=text)
            self.dependency_feedback_label.configure(text=text)
            entry_id = str(entry.get("id") or "")
            if entry_id:
                self._set_dependency_card_progress(entry_id, text, self._dependency_progress_percent(event), tone="active")

    def open_dependency_manager_window(self) -> None:
        if self.dependency_manager_window is not None and self.dependency_manager_window.winfo_exists():
            self.dependency_manager_window.deiconify()
            self.dependency_manager_window.lift()
            self.dependency_manager_window.focus_force()
            self.refresh_dependency_sources(announce=False)
            return

        window = tk.Toplevel(self.root)
        window.title("依赖管理")
        window.geometry("1080x720")
        window.minsize(960, 640)
        window.configure(bg=self.palette.root_bg)
        window.transient(self.root)
        window.protocol("WM_DELETE_WINDOW", self._close_dependency_manager_window)
        self.dependency_manager_window = window

        header = tk.Frame(window, bg=self.palette.root_bg, padx=18, pady=16)
        header.pack(fill="x")
        self.surface_roles.append((header, "root"))

        title = tk.Label(
            header,
            text="依赖管理",
            font=("Microsoft YaHei UI", 15, "bold"),
            anchor="w",
            bg=self.palette.root_bg,
            fg=self.palette.text,
        )
        title.pack(anchor="w")
        self.dependency_manager_title_label = title

        copy = tk.Label(
            header,
            text="运行时、GPU 加速库和模型都安装到项目私有目录；应用不会写入系统级 Python 环境或全局模型目录。",
            font=("Microsoft YaHei UI", 10),
            anchor="w",
            justify="left",
            bg=self.palette.root_bg,
            fg=self.palette.muted,
        )
        copy.pack(anchor="w", pady=(6, 0))
        self.dependency_manager_copy_label = copy

        content = tk.Frame(window, bg=self.palette.root_bg, padx=18, pady=0)
        content.pack(fill="both", expand=True, pady=(0, 18))
        self.surface_roles.append((content, "root"))
        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(0, weight=1)

        list_card = tk.Frame(
            content,
            bg=self.palette.panel_bg,
            highlightthickness=1,
            highlightbackground=self.palette.border,
            bd=0,
        )
        list_card.grid(row=0, column=0, sticky="nsew")

        list_header = tk.Frame(list_card, bg=self.palette.panel_bg, padx=14, pady=14)
        list_header.pack(fill="x")
        list_header_top = tk.Frame(list_header, bg=self.palette.panel_bg)
        list_header_top.pack(fill="x")
        tk.Label(
            list_header_top,
            text="依赖目录",
            font=("Microsoft YaHei UI", 12, "bold"),
            anchor="w",
            bg=self.palette.panel_bg,
            fg=self.palette.text,
        ).pack(side="left", anchor="w")
        self.dependency_refresh_button = self._make_button(list_header_top, "刷新", self.refresh_dependency_sources, style="Ghost.TButton")
        self.dependency_refresh_button.pack(side="right")
        tk.Label(
            list_header,
            text="点击卡片按钮即可安装或卸载；安装进度会直接显示在对应依赖卡片里。",
            font=("Microsoft YaHei UI", 9),
            anchor="w",
            justify="left",
            bg=self.palette.panel_bg,
            fg=self.palette.muted,
        ).pack(anchor="w", pady=(4, 0))
        status_label = tk.Label(
            list_header,
            text="等待操作。",
            font=("Microsoft YaHei UI", 9),
            anchor="w",
            justify="left",
            bg=self.palette.panel_bg,
            fg=self.palette.muted,
        )
        status_label.pack(anchor="w", pady=(6, 0))
        self.dependency_manager_status_label = status_label

        self.dependency_manager_list_area = ScrollArea(list_card, self.palette.panel_bg, self.palette)
        self.dependency_manager_list_area.pack(fill="both", expand=True, padx=14, pady=(0, 14))
        self.scroll_areas.append(self.dependency_manager_list_area)

        self.refresh_dependency_manager_window(announce=False)
        self.refresh_model_catalog()

    def _install_single_dependency(self, entry: dict[str, object]) -> None:
        if self.runtime_busy or self.model_busy:
            self.set_status("当前已有任务在执行，请稍后再试。", tone="warning")
            return
        self._install_selected_dependencies([dict(entry)])

    def _remove_single_dependency_model(self, entry: dict[str, object]) -> None:
        if self.runtime_busy or self.model_busy:
            self.set_status("当前已有任务在执行，请稍后再试。", tone="warning")
            return
        self._uninstall_dependency_model(dict(entry))

    def _close_dependency_manager_window(self) -> None:
        if self.dependency_manager_window is None:
            return
        try:
            self.dependency_manager_window.destroy()
        except tk.TclError:
            pass
        self.dependency_manager_window = None
        self.dependency_manager_list_area = None
        self.dependency_manager_title_label = None
        self.dependency_manager_copy_label = None
        self.dependency_manager_status_label = None
        self.dependency_refresh_button = None
        self.dependency_progress_widgets = {}

    def refresh_dependency_sources(self, announce: bool = True) -> None:
        self.refresh_model_catalog()

    def refresh_dependency_manager_window(self, announce: bool = True) -> None:
        runtime_entries = launcher_core.list_managed_dependencies()
        model_entries = self._dependency_model_entries()
        self.dependency_entries = [*runtime_entries, *model_entries]
        if self.dependency_manager_window is None or not self.dependency_manager_window.winfo_exists():
            self.dependency_manager_window = None
            self.dependency_manager_list_area = None
            return
        if self.dependency_manager_list_area is None or not self.dependency_manager_list_area.winfo_exists():
            return

        self.dependency_manager_window.configure(bg=self.palette.root_bg)
        if self.dependency_manager_title_label is not None and self.dependency_manager_title_label.winfo_exists():
            self.dependency_manager_title_label.configure(bg=self.palette.root_bg, fg=self.palette.text)
        if self.dependency_manager_copy_label is not None and self.dependency_manager_copy_label.winfo_exists():
            self.dependency_manager_copy_label.configure(bg=self.palette.root_bg, fg=self.palette.muted)
        if self.dependency_manager_status_label is not None and self.dependency_manager_status_label.winfo_exists():
            self.dependency_manager_status_label.configure(bg=self.palette.panel_bg, fg=self.palette.muted)

        self.dependency_manager_list_area.set_colors(self.palette.panel_bg, self.palette)
        self.dependency_manager_list_area.clear()
        self.dependency_progress_widgets = {}

        summary_card = tk.Frame(
            self.dependency_manager_list_area.content,
            bg=self.palette.panel_alt,
            highlightthickness=1,
            highlightbackground=self.palette.border,
            bd=0,
            padx=12,
            pady=12,
        )
        summary_card.pack(fill="x", pady=(0, 10))
        tk.Label(
            summary_card,
            text="打包策略提示",
            bg=self.palette.panel_alt,
            fg=self.palette.text,
            font=("Microsoft YaHei UI", 10, "bold"),
            anchor="w",
        ).pack(anchor="w")
        tk.Label(
            summary_card,
            text="CPU 运行时可随发布包内置；GPU 运行时、GPU 加速库和模型更适合按需安装到当前应用目录。",
            bg=self.palette.panel_alt,
            fg=self.palette.muted,
            font=("Microsoft YaHei UI", 9),
            justify="left",
            wraplength=900,
            anchor="w",
        ).pack(anchor="w", pady=(6, 0))

        sections = [
            ("运行时依赖", runtime_entries),
            ("推荐模型", model_entries),
        ]
        entry_index = 1
        for section_title, section_entries in sections:
            if not section_entries:
                continue
            section_label = tk.Label(
                self.dependency_manager_list_area.content,
                text=section_title,
                bg=self.palette.panel_bg,
                fg=self.palette.text,
                font=("Microsoft YaHei UI", 11, "bold"),
                anchor="w",
            )
            section_label.pack(fill="x", pady=(4, 10))

            for entry in section_entries:
                card = tk.Frame(
                    self.dependency_manager_list_area.content,
                    bg=self.palette.panel_alt,
                    highlightthickness=1,
                    highlightbackground=self.palette.border,
                    bd=0,
                    padx=12,
                    pady=12,
                )
                card.pack(fill="x", pady=(0, 10))

                top = tk.Frame(card, bg=self.palette.panel_alt)
                top.pack(fill="x")

                index_label = tk.Label(
                    top,
                    text=f"{entry_index}.",
                    bg=self.palette.chip_bg,
                    fg=self.palette.chip_fg,
                    font=("Consolas", 10, "bold"),
                    padx=8,
                    pady=4,
                )
                index_label.pack(side="left")

                title_label = tk.Label(
                    top,
                    text=str(entry.get("title") or "未命名依赖"),
                    bg=self.palette.panel_alt,
                    fg=self.palette.text,
                    font=("Microsoft YaHei UI", 10, "bold"),
                    anchor="w",
                )
                title_label.pack(side="left", fill="x", expand=True, padx=(10, 10))

                status_label = tk.Label(top, text="", font=("Microsoft YaHei UI", 9, "bold"), padx=10, pady=4)
                status_label.pack(side="right")
                self._set_badge(status_label, str(entry.get("status_text") or "未知"), self._dependency_status_tone(entry))

                action_row = tk.Frame(card, bg=self.palette.panel_alt)
                action_row.pack(fill="x", pady=(10, 0))
                is_installed = bool(entry.get("installed"))
                if str(entry.get("kind") or "") == "model" and is_installed:
                    action_button = self._make_button(
                        action_row,
                        "卸载",
                        lambda item=dict(entry): self._remove_single_dependency_model(item),
                        style="Ghost.TButton",
                    )
                else:
                    action_button = self._make_button(
                        action_row,
                        "安装" if not is_installed else "已安装",
                        lambda item=dict(entry): self._install_single_dependency(item),
                        style="Accent.TButton" if not is_installed else "Ghost.TButton",
                    )
                action_button.pack(side="right")
                if is_installed and str(entry.get("kind") or "") != "model":
                    self._set_button_enabled(action_button, False)

                desc_label = tk.Label(
                    card,
                    text=str(entry.get("description") or ""),
                    bg=self.palette.panel_alt,
                    fg=self.palette.text,
                    font=("Microsoft YaHei UI", 9),
                    justify="left",
                    anchor="w",
                    wraplength=820,
                )
                desc_label.pack(fill="x", pady=(8, 0))

                progress_label = tk.Label(
                    card,
                    text="状态：已安装" if is_installed else "状态：等待安装",
                    bg=self.palette.panel_alt,
                    fg=self.palette.muted,
                    font=("Microsoft YaHei UI", 9),
                    justify="left",
                    anchor="w",
                )
                progress_label.pack(fill="x", pady=(10, 0))

                progress_shell = tk.Frame(card, bg=self.palette.border, height=8)
                progress_shell.pack(fill="x", pady=(6, 0))
                progress_shell.pack_propagate(False)
                progress_fill = tk.Frame(progress_shell, bg="#22c55e" if is_installed else self.palette.accent, height=8)
                progress_fill.place(relx=0, rely=0, relheight=1, relwidth=1.0 if is_installed else 0.0)
                entry_id = str(entry.get("id") or "")
                if entry_id:
                    self.dependency_progress_widgets[entry_id] = {
                        "label": progress_label,
                        "fill": progress_fill,
                    }

                hint_label = tk.Label(
                    card,
                    text=str(entry.get("hint") or ""),
                    bg=self.palette.panel_alt,
                    fg=self.palette.muted,
                    font=("Microsoft YaHei UI", 9),
                    justify="left",
                    anchor="w",
                    wraplength=820,
                )
                hint_label.pack(fill="x", pady=(6, 0))
                entry_index += 1

        self._set_dependency_manager_controls_enabled(not self.runtime_busy and not self.model_busy)
        if announce:
            message = f"依赖状态已刷新，共 {len(self.dependency_entries)} 项。"
            if self.dependency_manager_status_label is not None and self.dependency_manager_status_label.winfo_exists():
                self.dependency_manager_status_label.configure(text=message)

    def _install_selected_dependencies(self, entries: list[dict[str, object]]) -> None:
        if not entries:
            self.set_status("没有可安装的依赖。", tone="warning")
            return

        task_names = [str(entry.get("title") or entry.get("id") or "依赖") for entry in entries]
        status_text = f"正在处理依赖：{'、'.join(task_names)}"
        self.runtime_busy = True
        self._sync_send_button_state()
        if self.settings_payload is not None:
            self._update_runtime_buttons(self.settings_payload.get("ollama", {}), self.model_var.get().strip())
        self._set_dependency_manager_controls_enabled(False)
        self.dependency_feedback_label.configure(text=status_text)
        self.set_status(status_text)
        if self.dependency_manager_status_label is not None and self.dependency_manager_status_label.winfo_exists():
            self.dependency_manager_status_label.configure(text=status_text)
        for entry in entries:
            entry_id = str(entry.get("id") or "")
            if entry_id:
                self._set_dependency_card_progress(entry_id, "状态：等待安装任务开始", 0.02, tone="active")

        def worker():
            completed: list[dict[str, object]] = []
            for entry in entries:
                title = str(entry.get("title") or entry.get("id") or "依赖")
                if bool(entry.get("installed")):
                    self.queue.put(
                        (
                            "success",
                            self._handle_dependency_manager_event,
                            {"type": "log", "entry": dict(entry), "message": f"已跳过：{title}（当前已安装）"},
                        )
                    )
                    completed.append(entry)
                    continue

                self.queue.put(
                    (
                        "success",
                        self._handle_dependency_manager_event,
                        {"type": "log", "entry": dict(entry), "message": f"开始安装：{title}"},
                    )
                )

                def progress_callback(event: dict[str, object], dependency_entry=dict(entry)) -> None:
                    self.queue.put(
                        (
                            "success",
                            self._handle_dependency_manager_event,
                            {"type": "progress", "entry": dependency_entry, "event": dict(event)},
                        )
                    )

                launcher_core.install_managed_dependency(str(entry.get("id") or ""), progress_callback=progress_callback)
                completed.append(entry)
                self.queue.put(
                    (
                        "success",
                        self._handle_dependency_manager_event,
                        {"type": "log", "entry": dict(entry), "message": f"安装完成：{title}"},
                    )
                )
            return completed

        def on_success(completed: list[dict[str, object]]) -> None:
            self.runtime_busy = False
            self._sync_send_button_state()
            self.refresh_settings_status(silent=True)
            self.refresh_dependency_manager_window(announce=False)
            summary_names = "、".join(str(item.get("title") or item.get("id") or "依赖") for item in completed)
            summary = f"依赖处理完成：{summary_names}"
            self.dependency_feedback_label.configure(text=summary)
            self.set_status(summary, tone="success")
            if self.dependency_manager_status_label is not None and self.dependency_manager_status_label.winfo_exists():
                self.dependency_manager_status_label.configure(text=summary)

        def on_error(exc: Exception) -> None:
            self.runtime_busy = False
            self._sync_send_button_state()
            self.refresh_settings_status(silent=True)
            self.refresh_dependency_manager_window(announce=False)
            message = str(exc)
            self.dependency_feedback_label.configure(text=message)
            self.set_status(message, tone="error")
            if self.dependency_manager_status_label is not None and self.dependency_manager_status_label.winfo_exists():
                self.dependency_manager_status_label.configure(text=message)
            messagebox.showerror("依赖安装失败", message)

        self._run_worker(worker, on_success, on_error)

    def _uninstall_dependency_model(self, entry: dict[str, object]) -> None:
        model_name = str(entry.get("model_name") or entry.get("title") or "").strip()
        if not model_name:
            self.set_status("没有识别到可卸载的模型。", tone="warning")
            return
        if not bool(entry.get("installed")):
            self.set_status(f"已跳过：{model_name}（当前未安装）", tone="warning")
            return
        if not messagebox.askyesno("卸载模型", f"确定卸载模型 {model_name} 吗？"):
            return

        status_text = f"正在卸载模型：{model_name}"
        self.runtime_busy = True
        self._sync_send_button_state()
        if self.settings_payload is not None:
            self._update_runtime_buttons(self.settings_payload.get("ollama", {}), self.model_var.get().strip())
        self._set_dependency_manager_controls_enabled(False)
        self.dependency_feedback_label.configure(text=status_text)
        self.set_status(status_text)
        if self.dependency_manager_status_label is not None and self.dependency_manager_status_label.winfo_exists():
            self.dependency_manager_status_label.configure(text=status_text)
        self._set_dependency_card_progress(str(entry.get("id") or ""), f"状态：{status_text}", 0.18, tone="active")

        def worker():
            return launcher_core.remove_model_with_progress(model_name)

        def on_success(_payload) -> None:
            self.runtime_busy = False
            self._sync_send_button_state()
            self.refresh_settings_status(silent=True)
            self.refresh_model_catalog()
            self.refresh_dependency_manager_window(announce=False)
            message = f"模型已卸载：{model_name}"
            self.dependency_feedback_label.configure(text=message)
            self.set_status(message, tone="success")
            if self.dependency_manager_status_label is not None and self.dependency_manager_status_label.winfo_exists():
                self.dependency_manager_status_label.configure(text=message)

        def on_error(exc: Exception) -> None:
            self.runtime_busy = False
            self._sync_send_button_state()
            self.refresh_settings_status(silent=True)
            self.refresh_dependency_manager_window(announce=False)
            message = str(exc)
            self.dependency_feedback_label.configure(text=message)
            self.set_status(message, tone="error")
            if self.dependency_manager_status_label is not None and self.dependency_manager_status_label.winfo_exists():
                self.dependency_manager_status_label.configure(text=message)
            messagebox.showerror("卸载模型失败", message)

        self._run_worker(worker, on_success, on_error)

    def open_model_manager_window(self) -> None:
        self.open_dependency_manager_window()
        return

        if self.model_manager_window is not None and self.model_manager_window.winfo_exists():
            self.model_manager_window.deiconify()
            self.model_manager_window.lift()
            self.model_manager_window.focus_force()
            self.refresh_model_catalog()
            return

        window = tk.Toplevel(self.root)
        window.title("模型列表")
        window.geometry("760x620")
        window.minsize(680, 520)
        window.configure(bg=self.palette.root_bg)
        window.transient(self.root)
        window.protocol("WM_DELETE_WINDOW", self._close_model_manager_window)
        self.model_manager_window = window

        header = tk.Frame(window, bg=self.palette.root_bg, padx=16, pady=14)
        header.pack(fill="x")

        title = tk.Label(header, text="模型列表", font=("Microsoft YaHei UI", 14, "bold"), anchor="w")
        title.pack(anchor="w")
        self.label_roles.append((title, "text"))

        self.model_manager_source_label = tk.Label(header, text="", font=("Microsoft YaHei UI", 10), anchor="w", justify="left")
        self.model_manager_source_label.pack(anchor="w", pady=(4, 0))
        self.label_roles.append((self.model_manager_source_label, "muted"))

        action_row = tk.Frame(header, bg=self.palette.root_bg)
        action_row.pack(fill="x", pady=(12, 0))
        self.surface_roles.append((action_row, "root"))

        self.model_manager_status_label = tk.Label(action_row, text="", font=("Microsoft YaHei UI", 9), anchor="w", justify="left")
        self.model_manager_status_label.pack(side="left", fill="x", expand=True)
        self.label_roles.append((self.model_manager_status_label, "muted"))

        self.model_manager_refresh_button = self._make_button(action_row, "刷新模型列表", self.refresh_model_catalog, style="Ghost.TButton")
        self.model_manager_refresh_button.pack(side="right")

        self.model_manager_area = ScrollArea(window, self.palette.panel_bg, self.palette)
        self.model_manager_area.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        self.scroll_areas.append(self.model_manager_area)

        self.model_manager_feedback_label = tk.Label(
            window,
            text="",
            font=("Microsoft YaHei UI", 9),
            anchor="w",
            justify="left",
            padx=16,
            pady=10,
        )
        self.model_manager_feedback_label.pack(fill="x")
        self.label_roles.append((self.model_manager_feedback_label, "muted"))

        self.refresh_model_catalog()

    def _close_model_manager_window(self) -> None:
        if self.model_manager_window is None:
            return
        try:
            self.model_manager_window.destroy()
        except tk.TclError:
            pass
        self.model_manager_window = None
        self.model_manager_area = None
        self.model_manager_status_label = None
        self.model_manager_feedback_label = None
        self.model_manager_source_label = None
    def refresh_model_catalog(self) -> None:
        if self.model_catalog_refreshing:
            return
        self.model_catalog_refreshing = True
        if self.model_manager_status_label is not None and self.model_manager_status_label.winfo_exists():
            self.model_manager_status_label.configure(text="正在刷新本地安装状态...")
        if self.dependency_manager_status_label is not None and self.dependency_manager_status_label.winfo_exists():
            self.dependency_manager_status_label.configure(text="正在刷新本地安装状态...")

        def worker():
            recommended_sizes = {
                "qwen2.5:3b": int(1.9 * 1024**3),
                "qwen2.5:7b": int(4.7 * 1024**3),
                "nomic-embed-text": int(274 * 1024**2),
            }
            installed_entries_by_name = {
                str(item.get("name") or "").strip(): dict(item)
                for item in launcher_core.list_private_model_entries()
                if str(item.get("name") or "").strip()
            }
            installed_names = set(launcher_core.list_private_models_from_store())
            try:
                installed_names.update(launcher_core.get_ollama_models())
            except Exception:
                pass

            merged: dict[str, dict[str, object]] = {}
            for name in launcher_core.RECOMMENDED_MODEL_NAMES:
                payload: dict[str, object] = {
                    "name": name,
                    "installed": launcher_core.test_model_installed(name, sorted(installed_names)),
                    "is_embedding": "embed" in name.lower() or "bert" in name.lower(),
                    "details": {},
                    "size": recommended_sizes.get(name, 0),
                }
                if name in installed_entries_by_name:
                    installed_payload = installed_entries_by_name[name]
                    for key in ("size", "details", "modified_at", "digest", "model"):
                        if not payload.get(key) and installed_payload.get(key):
                            payload[key] = installed_payload.get(key)
                payload["is_embedding"] = is_embedding_model_entry(payload)
                merged[name] = payload

            for name in sorted(installed_names):
                if not name:
                    continue
                if name not in launcher_core.RECOMMENDED_MODEL_NAMES:
                    continue
                fallback_entry = {
                    "name": name,
                    "installed": launcher_core.test_model_installed(name, sorted(installed_names)),
                    "is_embedding": "embed" in name.lower() or "bert" in name.lower(),
                    "details": dict((installed_entries_by_name.get(name) or {}).get("details") or {}),
                    "size": (installed_entries_by_name.get(name) or {}).get("size") or 0,
                }
                if name in merged:
                    merged[name]["installed"] = launcher_core.test_model_installed(name, sorted(installed_names))
                    continue
                merged[name] = fallback_entry

            entries = list(merged.values())
            order = {name: index for index, name in enumerate(launcher_core.RECOMMENDED_MODEL_NAMES)}
            entries.sort(key=lambda item: order.get(str(item.get("name") or ""), 999))
            return {"entries": entries, "source_text": "轻量推荐清单：仅显示 Qwen 2.5 3B / 7B 与向量模型。"}

        def on_success(payload: dict) -> None:
            self.model_catalog_refreshing = False
            self.model_catalog_entries = list(payload.get("entries") or [])
            self.model_catalog_source = str(payload.get("source_text") or "")
            if self.model_manager_status_label is not None and self.model_manager_status_label.winfo_exists():
                self.model_manager_status_label.configure(text="模型安装状态已更新。")
            if self.dependency_manager_status_label is not None and self.dependency_manager_status_label.winfo_exists():
                self.dependency_manager_status_label.configure(text="模型安装状态已更新。")
            if not self.runtime_busy and not self.model_busy:
                self.refresh_dependency_manager_window(announce=False)
            self._refresh_model_manager_window()

        def on_error(exc: Exception) -> None:
            self.model_catalog_refreshing = False
            if self.model_manager_status_label is not None and self.model_manager_status_label.winfo_exists():
                self.model_manager_status_label.configure(text=str(exc))
            if self.dependency_manager_status_label is not None and self.dependency_manager_status_label.winfo_exists():
                self.dependency_manager_status_label.configure(text=str(exc))
            if not self.runtime_busy and not self.model_busy:
                self.refresh_dependency_manager_window(announce=False)
            self._refresh_model_manager_window()

        self._run_worker(worker, on_success, on_error)

    def _ensure_catalog_entry(self, model_name: str) -> dict[str, object]:
        for item in self.model_catalog_entries:
            if str(item.get("name") or "") == model_name:
                return item
        entry = {
            "name": model_name,
            "installed": False,
            "is_embedding": "embed" in model_name.lower() or "bert" in model_name.lower(),
            "details": {},
        }
        self.model_catalog_entries.append(entry)
        return entry

    def _set_model_operation_state(
        self,
        model_name: str,
        *,
        kind: str,
        busy: bool,
        text: str,
        progress: float | None = None,
        installed: bool | None = None,
        indeterminate: bool = False,
        task_id: str | None = None,
    ) -> None:
        state = self.model_operations.get(model_name, {})
        if busy and task_id is None and not state.get("task_id"):
            task_id = self._create_model_task(model_name, kind, text)
        state.update(
            {
                "kind": kind,
                "busy": busy,
                "text": text,
                "progress": progress,
                "indeterminate": indeterminate,
            }
        )
        if task_id is not None:
            state["task_id"] = task_id
        if installed is not None:
            state["installed"] = installed
            entry = self._ensure_catalog_entry(model_name)
            entry["installed"] = installed
        self.model_operations[model_name] = state
        active_task_id = str(state.get("task_id") or "")
        task_state = "running" if busy else ("success" if "失败" not in text else "error")
        if active_task_id:
            self._update_model_task(active_task_id, state=task_state, message=text, progress=progress)
        self._refresh_model_manager_window()

    def _handle_model_install_progress(self, payload: dict[str, object]) -> None:
        model_name = str(payload.get("model_name") or "")
        event = payload.get("event") or {}
        if not isinstance(event, dict) or not model_name:
            return
        status_text = str(event.get("status") or "正在安装...")
        total = event.get("total")
        completed = event.get("completed")
        progress_value: float | None = None
        if isinstance(total, (int, float)) and isinstance(completed, (int, float)) and float(total) > 0:
            progress_value = max(0.0, min(100.0, float(completed) / float(total) * 100.0))
            status_text = f"{status_text} {progress_value:.1f}%"
        self._set_model_operation_state(
            model_name,
            kind="install",
            busy=True,
            text=status_text,
            progress=progress_value,
            installed=False,
            indeterminate=progress_value is None,
        )

    def _handle_model_remove_progress(self, payload: dict[str, object]) -> None:
        model_name = str(payload.get("model_name") or "")
        event = payload.get("event") or {}
        if not isinstance(event, dict) or not model_name:
            return
        self._set_model_operation_state(
            model_name,
            kind="remove",
            busy=True,
            text=str(event.get("status") or "正在卸载..."),
            progress=None,
            installed=True,
            indeterminate=True,
        )

    def _legacy_install_model_from_manager_unused(self, model_name: str) -> None:
        if not model_name or self.model_operations.get(model_name, {}).get("busy"):
            return
        task_id = self._create_model_task(model_name, "install", "准备安装...")
        self._set_model_operation_state(model_name, kind="install", busy=True, text="准备安装...", progress=0.0, installed=False, task_id=task_id)
        if self.model_manager_feedback_label is not None and self.model_manager_feedback_label.winfo_exists():
            self.model_manager_feedback_label.configure(text=f"正在安装模型：{model_name}")

        def progress_callback(event: dict[str, object]) -> None:
            self.queue.put(("success", self._handle_model_install_progress, {"model_name": model_name, "event": event}))

        def worker():
            return launcher_core.pull_model_with_progress(model_name, progress_callback)

        def on_success(_payload) -> None:
            self._set_model_operation_state(model_name, kind="install", busy=False, text="已安装", progress=100.0, installed=True)
            if self.model_manager_feedback_label is not None and self.model_manager_feedback_label.winfo_exists():
                self.model_manager_feedback_label.configure(text=f"模型已安装：{model_name}")
            self.refresh_settings_status(silent=True)
            self.refresh_model_catalog()

        def on_error(exc: Exception) -> None:
            self._set_model_operation_state(model_name, kind="install", busy=False, text=f"安装失败：{exc}", progress=None, installed=False)
            if self.model_manager_feedback_label is not None and self.model_manager_feedback_label.winfo_exists():
                self.model_manager_feedback_label.configure(text=f"安装失败：{model_name}")
            messagebox.showerror("安装模型失败", str(exc))

        self._run_worker(worker, on_success, on_error)

    def _legacy_uninstall_model_from_manager_unused(self, model_name: str) -> None:
        if not model_name or self.model_operations.get(model_name, {}).get("busy"):
            return
        entry = self._ensure_catalog_entry(model_name)
        installed = bool(self.model_operations.get(model_name, {}).get("installed")) if "installed" in self.model_operations.get(model_name, {}) else bool(entry.get("installed"))
        if not installed:
            if self.model_manager_feedback_label is not None and self.model_manager_feedback_label.winfo_exists():
                self.model_manager_feedback_label.configure(text=f"模型未安装，无需卸载：{model_name}")
            return
        if not messagebox.askyesno("卸载模型", f"确定卸载模型 {model_name} 吗？"):
            return
        task_id = self._create_model_task(model_name, "remove", "正在卸载...")
        self._set_model_operation_state(model_name, kind="remove", busy=True, text="正在卸载...", progress=None, installed=True, indeterminate=True, task_id=task_id)
        if self.model_manager_feedback_label is not None and self.model_manager_feedback_label.winfo_exists():
            self.model_manager_feedback_label.configure(text=f"正在卸载模型：{model_name}")

        def progress_callback(event: dict[str, object]) -> None:
            self.queue.put(("success", self._handle_model_remove_progress, {"model_name": model_name, "event": event}))

        def worker():
            return launcher_core.remove_model_with_progress(model_name, progress_callback)

        def on_success(_payload) -> None:
            self._set_model_operation_state(model_name, kind="remove", busy=False, text="已卸载", progress=None, installed=False)
            if self.model_manager_feedback_label is not None and self.model_manager_feedback_label.winfo_exists():
                self.model_manager_feedback_label.configure(text=f"模型已卸载：{model_name}")
            self.refresh_settings_status(silent=True)
            self.refresh_model_catalog()

        def on_error(exc: Exception) -> None:
            self._set_model_operation_state(model_name, kind="remove", busy=False, text=f"卸载失败：{exc}", progress=None, installed=True)
            if self.model_manager_feedback_label is not None and self.model_manager_feedback_label.winfo_exists():
                self.model_manager_feedback_label.configure(text=f"卸载失败：{model_name}")
            messagebox.showerror("卸载模型失败", str(exc))

        self._run_worker(worker, on_success, on_error)

    def install_model_from_manager(self, model_name: str) -> None:
        if not model_name or self.model_operations.get(model_name, {}).get("busy"):
            return
        task_id = self._create_model_task(model_name, "install", "准备安装...")
        self._set_model_operation_state(
            model_name,
            kind="install",
            busy=True,
            text="准备安装...",
            progress=0.0,
            installed=False,
            task_id=task_id,
        )
        if self.model_manager_feedback_label is not None and self.model_manager_feedback_label.winfo_exists():
            self.model_manager_feedback_label.configure(text=f"正在安装模型：{model_name}")

        def progress_callback(event: dict[str, object]) -> None:
            self.queue.put(("success", self._handle_model_install_progress, {"model_name": model_name, "event": event}))

        def worker():
            return launcher_core.pull_model_with_progress(model_name, progress_callback)

        def on_success(_payload) -> None:
            self._set_model_operation_state(model_name, kind="install", busy=False, text="已安装", progress=100.0, installed=True)
            if self.model_manager_feedback_label is not None and self.model_manager_feedback_label.winfo_exists():
                self.model_manager_feedback_label.configure(text=f"模型已安装：{model_name}")
            self.refresh_settings_status(silent=True)
            self.refresh_model_catalog()

        def on_error(exc: Exception) -> None:
            self._set_model_operation_state(model_name, kind="install", busy=False, text=f"安装失败：{exc}", progress=None, installed=False)
            if self.model_manager_feedback_label is not None and self.model_manager_feedback_label.winfo_exists():
                self.model_manager_feedback_label.configure(text=f"安装失败：{model_name}")
            messagebox.showerror("安装模型失败", str(exc))

        self._run_worker(worker, on_success, on_error)

    def uninstall_model_from_manager(self, model_name: str) -> None:
        if not model_name or self.model_operations.get(model_name, {}).get("busy"):
            return
        entry = self._ensure_catalog_entry(model_name)
        installed = bool(self.model_operations.get(model_name, {}).get("installed")) if "installed" in self.model_operations.get(model_name, {}) else bool(entry.get("installed"))
        if not installed:
            if self.model_manager_feedback_label is not None and self.model_manager_feedback_label.winfo_exists():
                self.model_manager_feedback_label.configure(text=f"模型未安装，无需卸载：{model_name}")
            return
        if not messagebox.askyesno("卸载模型", f"确定卸载模型 {model_name} 吗？"):
            return
        task_id = self._create_model_task(model_name, "remove", "准备卸载...")
        self._set_model_operation_state(
            model_name,
            kind="remove",
            busy=True,
            text="正在卸载...",
            progress=None,
            installed=True,
            indeterminate=True,
            task_id=task_id,
        )
        if self.model_manager_feedback_label is not None and self.model_manager_feedback_label.winfo_exists():
            self.model_manager_feedback_label.configure(text=f"正在卸载模型：{model_name}")

        def progress_callback(event: dict[str, object]) -> None:
            self.queue.put(("success", self._handle_model_remove_progress, {"model_name": model_name, "event": event}))

        def worker():
            return launcher_core.remove_model_with_progress(model_name, progress_callback)

        def on_success(_payload) -> None:
            self._set_model_operation_state(model_name, kind="remove", busy=False, text="已卸载", progress=None, installed=False)
            if self.model_manager_feedback_label is not None and self.model_manager_feedback_label.winfo_exists():
                self.model_manager_feedback_label.configure(text=f"模型已卸载：{model_name}")
            self.refresh_settings_status(silent=True)
            self.refresh_model_catalog()

        def on_error(exc: Exception) -> None:
            self._set_model_operation_state(model_name, kind="remove", busy=False, text=f"卸载失败：{exc}", progress=None, installed=True)
            if self.model_manager_feedback_label is not None and self.model_manager_feedback_label.winfo_exists():
                self.model_manager_feedback_label.configure(text=f"卸载失败：{model_name}")
            messagebox.showerror("卸载模型失败", str(exc))

        self._run_worker(worker, on_success, on_error)

    def _refresh_model_manager_window(self) -> None:
        if self.model_manager_window is None or not self.model_manager_window.winfo_exists():
            self.model_manager_window = None
            self.model_manager_area = None
            return
        if self.model_manager_area is None or not self.model_manager_area.winfo_exists():
            return

        try:
            self.model_manager_window.configure(bg=self.palette.root_bg)
        except tk.TclError:
            return
        self.model_manager_area.set_colors(self.palette.panel_bg, self.palette)
        self.model_manager_area.clear()
        visible_entries = list(self.model_catalog_entries)

        if self.model_manager_source_label is not None and self.model_manager_source_label.winfo_exists():
            self.model_manager_source_label.configure(
                bg=self.palette.root_bg,
                fg=self.palette.muted,
                text=self.model_catalog_source or "轻量推荐清单：仅显示 Qwen 2.5 3B / 7B 与向量模型。",
            )
        if self.model_manager_status_label is not None and self.model_manager_status_label.winfo_exists():
            self.model_manager_status_label.configure(bg=self.palette.root_bg, fg=self.palette.muted)
        if self.model_manager_feedback_label is not None and self.model_manager_feedback_label.winfo_exists():
            self.model_manager_feedback_label.configure(bg=self.palette.root_bg, fg=self.palette.muted)
        sections = [
            ("对话模型", [item for item in visible_entries if not bool(item.get("is_embedding"))]),
            ("向量模型", [item for item in visible_entries if bool(item.get("is_embedding"))]),
        ]
        if not any(items for _title, items in sections):
            empty = tk.Label(
                self.model_manager_area.content,
                text="正在读取模型列表..." if self.model_catalog_refreshing else "暂无模型数据",
                bg=self.palette.panel_bg,
                fg=self.palette.muted,
                font=("Microsoft YaHei UI", 11),
                anchor="w",
            )
            empty.pack(fill="x", padx=12, pady=12)
            return

        for title, items in sections:
            if not items:
                continue
            title_label = tk.Label(
                self.model_manager_area.content,
                text=title,
                bg=self.palette.panel_bg,
                fg=self.palette.text,
                font=("Microsoft YaHei UI", 12, "bold"),
                anchor="w",
            )
            title_label.pack(fill="x", pady=(0, 10))

            for item in items:
                model_name = str(item.get("name") or "")
                operation = self.model_operations.get(model_name, {})
                installed = bool(operation.get("installed")) if "installed" in operation else bool(item.get("installed"))
                busy = bool(operation.get("busy"))

                card = tk.Frame(
                    self.model_manager_area.content,
                    bg=self.palette.panel_alt,
                    highlightthickness=1,
                    highlightbackground=self.palette.border,
                    bd=0,
                    padx=14,
                    pady=12,
                )
                card.pack(fill="x", pady=(0, 10))

                top = tk.Frame(card, bg=self.palette.panel_alt)
                top.pack(fill="x")

                name_label = tk.Label(
                    top,
                    text=model_name,
                    bg=self.palette.panel_alt,
                    fg=self.palette.text,
                    font=("Microsoft YaHei UI", 10, "bold"),
                    anchor="w",
                )
                name_label.pack(side="left", fill="x", expand=True)

                status_label = tk.Label(top, text="", anchor="w", font=("Microsoft YaHei UI", 9, "bold"), padx=10, pady=4)
                status_label.pack(side="left", padx=(12, 10))

                action_row = tk.Frame(top, bg=self.palette.panel_alt)
                action_row.pack(side="right")

                install_button = self._make_button(
                    action_row,
                    "安装",
                    lambda name=model_name: self.install_model_from_manager(name),
                    style="PrimarySmall.TButton",
                )
                uninstall_button = self._make_button(
                    action_row,
                    "卸载",
                    lambda name=model_name: self.uninstall_model_from_manager(name),
                    style="DangerSmall.TButton",
                )

                progress_label = tk.Label(
                    card,
                    text="",
                    bg=self.palette.panel_alt,
                    fg=self.palette.muted,
                    font=("Microsoft YaHei UI", 9),
                    anchor="w",
                    justify="left",
                )
                progress_bar = ttk.Progressbar(card, orient="horizontal", mode="determinate", maximum=100)

                tone = "good" if installed else "neutral"
                status_text = "已安装" if installed else "可安装"
                if busy:
                    tone = "accent"
                    status_text = str(operation.get("text") or ("正在安装" if operation.get("kind") == "install" else "正在卸载"))
                self._set_badge(status_label, status_text, tone)

                if busy:
                    progress_label.configure(text=str(operation.get("text") or "处理中..."))
                    progress_label.pack(fill="x", pady=(10, 0))
                    if bool(operation.get("indeterminate")):
                        progress_bar.configure(mode="indeterminate")
                        progress_bar.pack(fill="x", pady=(8, 0))
                        progress_bar.start(12)
                    else:
                        progress_bar.stop()
                        progress_bar.configure(mode="determinate", value=float(operation.get("progress") or 0.0))
                        progress_bar.pack(fill="x", pady=(8, 0))
                else:
                    progress_bar.stop()
                    detail_button = self._make_button(
                        action_row,
                        "参数",
                        lambda entry=dict(item): self.open_model_detail_window(entry),
                        style="GhostSmall.TButton",
                    )
                    detail_button.pack(side="left", padx=(0, 8))
                    if installed:
                        uninstall_button.pack(side="left")
                    else:
                        install_button.pack(side="left")

    def open_model_detail_window(self, entry: dict[str, object]) -> None:
        if self.model_detail_window is not None and self.model_detail_window.winfo_exists():
            try:
                self.model_detail_window.destroy()
            except tk.TclError:
                pass

        window = tk.Toplevel(self.root)
        window.title(f"模型参数 - {entry.get('name') or '未命名模型'}")
        window.configure(bg=self.palette.root_bg)
        window.transient(self.root)
        window.resizable(False, False)
        self.model_detail_window = window

        shell = tk.Frame(window, bg=self.palette.root_bg, padx=18, pady=16)
        shell.pack(fill="both", expand=True)

        title = tk.Label(
            shell,
            text=str(entry.get("name") or "未命名模型"),
            bg=self.palette.root_bg,
            fg=self.palette.text,
            font=("Microsoft YaHei UI", 14, "bold"),
            anchor="w",
        )
        title.pack(anchor="w")

        details = entry.get("details") or {}
        if not isinstance(details, dict):
            details = {}
        model_info_lines = [
            f"模型名：{entry.get('name') or '-'}",
            f"大小：{format_model_size(entry.get('size')) if format_model_size(entry.get('size')) != '-' else '未记录'}",
            f"参数量：{details.get('parameter_size') or '未记录'}",
            f"量化：{details.get('quantization_level') or '未记录'}",
            f"家族：{details.get('family') or '未记录'}",
            f"类型：{'向量模型' if is_embedding_model_entry(entry) else '对话模型'}",
            f"修改时间：{entry.get('modified_at') or '未记录'}",
        ]
        model_info_card = tk.Frame(shell, bg=self.palette.panel_bg, highlightthickness=1, highlightbackground=self.palette.border, bd=0, padx=14, pady=12)
        model_info_card.pack(fill="x", pady=(14, 12))
        model_info_title = tk.Label(model_info_card, text="模型信息", bg=self.palette.panel_bg, fg=self.palette.text, font=("Microsoft YaHei UI", 11, "bold"), anchor="w")
        model_info_title.pack(anchor="w")
        model_info_body = tk.Label(
            model_info_card,
            text="\n".join(model_info_lines),
            bg=self.palette.panel_bg,
            fg=self.palette.text,
            font=("Microsoft YaHei UI", 10),
            justify="left",
            anchor="w",
        )
        model_info_body.pack(anchor="w", pady=(10, 0))

        estimate = estimate_model_requirements(entry)
        estimate_card = tk.Frame(shell, bg=self.palette.panel_bg, highlightthickness=1, highlightbackground=self.palette.border, bd=0, padx=14, pady=12)
        estimate_card.pack(fill="x")
        estimate_title = tk.Label(estimate_card, text="估算建议", bg=self.palette.panel_bg, fg=self.palette.text, font=("Microsoft YaHei UI", 11, "bold"), anchor="w")
        estimate_title.pack(anchor="w")
        estimate_body = tk.Label(
            estimate_card,
            text="\n".join(
                [
                    f"内存建议：{estimate['ram']}",
                    f"显卡建议：{estimate['gpu']}",
                    f"性能倾向：{estimate['performance']}",
                    f"估算公式：{estimate['formula']}",
                    estimate["note"],
                ]
            ),
            bg=self.palette.panel_bg,
            fg=self.palette.text,
            font=("Microsoft YaHei UI", 10),
            justify="left",
            anchor="w",
            wraplength=440,
        )
        estimate_body.pack(anchor="w", pady=(10, 0))

        shell.update_idletasks()
        content_width = shell.winfo_reqwidth() + 18
        content_height = shell.winfo_reqheight() + 18
        width = max(500, min(620, content_width))
        height = max(360, min(520, content_height))
        self._center_toplevel(window, width, height)

    def _create_highlight_text(self, parent, text: str, query: str, *, font, bg: str, fg: str, wraplength: int, height: int = 1):
        widget = tk.Text(
            parent,
            height=height,
            wrap="word",
            relief="flat",
            bd=0,
            highlightthickness=0,
            padx=0,
            pady=0,
            font=font,
            bg=bg,
            fg=fg,
            insertbackground=fg,
            cursor="arrow",
        )
        widget.insert("1.0", text)
        widget.tag_configure("match", background=blend_hex(self.palette.accent, bg, 0.74), foreground=self.palette.text)
        for term in search_terms(query):
            start = "1.0"
            while True:
                match_index = widget.search(term, start, stopindex="end", nocase=True)
                if not match_index:
                    break
                end_index = f"{match_index}+{len(term)}c"
                widget.tag_add("match", match_index, end_index)
                start = end_index
        widget.configure(state="disabled", width=max(8, wraplength // 8))
        return widget

    def _open_note_with_highlight(self, note_id: int, highlight_query: str = "") -> None:
        query = " ".join(str(highlight_query or "").replace("...", " ").split())
        self.open_edit_note(note_id, query)

    def _open_note_from_source(self, source: dict[str, object]) -> None:
        note_id = int(source.get("note_id"))
        query = str(source.get("snippet") or source.get("title") or "")
        self._open_note_with_highlight(note_id, query)

    def open_note_search_window(self) -> None:
        if self.current_account is None:
            self.set_status("请先登录账号。", tone="error")
            return
        if self.editor_search_window is not None and self.editor_search_window.winfo_exists():
            self.editor_search_window.deiconify()
            self.editor_search_window.lift()
            self.editor_search_window.focus_force()
            self.editor_search_var.set(self.editor_search_var.get())
            self._refresh_note_search_results()
            return

        window = tk.Toplevel(self.root)
        window.title("搜索笔记")
        window.geometry("560x460")
        window.minsize(500, 380)
        window.configure(bg=self.palette.root_bg)
        window.transient(self.root)
        window.protocol("WM_DELETE_WINDOW", self._close_note_search_window)
        self.editor_search_window = window

        shell = tk.Frame(window, bg=self.palette.root_bg, padx=16, pady=14)
        shell.pack(fill="both", expand=True)

        title = tk.Label(shell, text="搜索笔记", bg=self.palette.root_bg, fg=self.palette.text, font=("Microsoft YaHei UI", 14, "bold"), anchor="w")
        title.pack(anchor="w")
        self.label_roles.append((title, "text"))

        copy = tk.Label(shell, text="输入标题或正文关键词，回车后快速定位到对应笔记。", bg=self.palette.root_bg, fg=self.palette.muted, font=("Microsoft YaHei UI", 10), anchor="w")
        copy.pack(anchor="w", pady=(4, 12))
        self.label_roles.append((copy, "muted"))

        self.editor_search_input = tk.Entry(shell, textvariable=self.editor_search_var, relief="flat", bd=0, highlightthickness=1, font=("Microsoft YaHei UI", 11))
        self.editor_search_input.pack(fill="x", ipady=9)
        self.entry_widgets.append(self.editor_search_input)
        self.editor_search_input.bind("<Return>", lambda _event: self._refresh_note_search_results(), add="+")
        self.editor_search_input.bind("<KeyRelease>", lambda _event: self._refresh_note_search_results(), add="+")

        self.editor_search_results_area = ScrollArea(shell, self.palette.panel_bg, self.palette)
        self.editor_search_results_area.pack(fill="both", expand=True, pady=(14, 0))
        self.scroll_areas.append(self.editor_search_results_area)

        self.editor_search_input.focus_set()
        self._refresh_note_search_results()

    def _close_note_search_window(self) -> None:
        if self.editor_search_window is None:
            return
        try:
            self.editor_search_window.destroy()
        except tk.TclError:
            pass
        self.editor_search_window = None
        self.editor_search_results_area = None

    def _refresh_note_search_results(self) -> None:
        if self.editor_search_window is None or not self.editor_search_window.winfo_exists():
            return
        if self.editor_search_results_area is None or not self.editor_search_results_area.winfo_exists():
            return
        raw_query = self.editor_search_var.get().strip()
        query = raw_query.lower()
        self.editor_search_results_area.set_colors(self.palette.panel_bg, self.palette)
        self.editor_search_results_area.clear()

        candidates = list(self.notes_cache) if self.notes_cache else list_note_items()
        matches: list[dict] = []
        terms = search_terms(raw_query)
        for note in candidates:
            haystack = f"{note_title_text(note, '')}\n{note_body_text(note)}".lower()
            if not query or query in haystack or (terms and all(term in haystack for term in terms)):
                matches.append(note)

        if not matches:
            empty = tk.Label(
                self.editor_search_results_area.content,
                text="没有找到匹配内容",
                bg=self.palette.panel_bg,
                fg=self.palette.muted,
                font=("Microsoft YaHei UI", 11),
                anchor="w",
            )
            empty.pack(fill="x", padx=12, pady=12)
            return

        for note in matches[:40]:
            card = tk.Frame(
                self.editor_search_results_area.content,
                bg=self.palette.panel_alt,
                highlightthickness=1,
                highlightbackground=self.palette.border,
                bd=0,
                padx=12,
                pady=10,
            )
            card.pack(fill="x", pady=(0, 8))

            title_text = note_title_text(note, f"笔记 #{note['id']}")
            title_widget = self._create_highlight_text(
                card,
                title_text,
                raw_query,
                font=("Microsoft YaHei UI", 11, "bold"),
                bg=self.palette.panel_alt,
                fg=self.palette.text,
                wraplength=470,
                height=1,
            )
            title_widget.pack(fill="x")
            preview_widget = self._create_highlight_text(
                card,
                excerpt_with_query(f"{title_text}\n{note_body_text(note)}", raw_query, limit=140),
                raw_query,
                font=("Microsoft YaHei UI", 10),
                bg=self.palette.panel_alt,
                fg=self.palette.muted,
                wraplength=470,
                height=3,
            )
            preview_widget.pack(fill="x", pady=(6, 0))
            meta = tk.Label(card, text=f"最后修改：{format_time(note.get('updated_at') or note.get('created_at')) or '未知'}", bg=self.palette.panel_alt, fg=self.palette.muted, font=("Microsoft YaHei UI", 9), anchor="w")
            meta.pack(anchor="w", pady=(8, 0))

            open_button = self._make_button(
                card,
                "打开",
                lambda item_id=int(note["id"]), item_query=raw_query: self._open_note_from_search(item_id, item_query),
                style="PrimarySmall.TButton",
            )
            open_button.pack(anchor="w", pady=(10, 0))

    def _open_note_from_search(self, note_id: int, query: str = "") -> None:
        self._close_note_search_window()
        self._open_note_with_highlight(note_id, query)

    def _perform_logout(self) -> None:
        if self.current_account is None:
            self.show_page("login")
            return
        if not messagebox.askyesno("退出登录", f"确定退出当前账号 {self._account_signature()} 吗？"):
            return

        self.current_account = None
        self.chat_items = []
        self.current_note_id = None
        self.editing_note_id = None
        self.question_input.delete("1.0", "end")
        self._sync_question_placeholder()
        self._sync_send_button_state()
        self.editor_text.delete("1.0", "end")
        self.render_chat_history()
        self._update_account_context()
        self.show_page("login")
        self.set_status("已退出当前账号。", tone="success")

    def logout_account(self) -> None:
        if self.current_page == "editor" and self.editor_dirty:
            self._attempt_leave_editor(self._perform_logout)
            return
        self._perform_logout()

    def _describe_ollama_status(self, ollama_state: dict) -> str:
        if not ollama_state.get("installed"):
            return "待部署 · 项目私有" if ollama_state.get("bundled_available") else "缺失 · 项目私有"
        private_running = bool(ollama_state.get("private_running"))
        system_running = bool(ollama_state.get("system_running"))
        system_app_running = bool(ollama_state.get("system_app_running"))
        if private_running and system_running:
            return "运行中 · 项目私有 / 系统 Ollama"
        if private_running:
            return "运行中 · 项目私有"
        if system_running:
            return "运行中 · 系统 Ollama"
        if system_app_running:
            return "已启动 · 系统 Ollama（服务未就绪）"
        return "未运行 · 项目私有"

    def _describe_ollama_source(self, ollama_state: dict) -> str:
        selected_profile = self._runtime_profile_from_display(ollama_state.get("selected_profile"))
        selected_label = self._runtime_profile_display(selected_profile)
        version_text = str(ollama_state.get("bundled_version") or "").strip()
        if ollama_state.get("bundled_available") or ollama_state.get("private_installed"):
            return f"{selected_label} {version_text}".strip()
        return f"{selected_label} 缺失"

    def _describe_runtime_profile_badge(
        self,
        selected_profile: str,
        environment: dict,
        gpu_hardware_names: list[str],
    ) -> str:
        selected_label = self._runtime_profile_display(selected_profile)
        cpu_name = str(environment.get("cpu_name") or "").strip()
        if selected_profile == "gpu":
            gpu_name = ", ".join(name for name in gpu_hardware_names if name).strip()
            return f"{selected_label} · {gpu_name or '未检测到 NVIDIA GPU'}"
        return f"{selected_label} · {cpu_name or '未知 CPU'}"

    def _ollama_tone(self, ollama_state: dict) -> str:
        if not ollama_state.get("installed"):
            return "neutral" if ollama_state.get("bundled_available") else "danger"
        if ollama_state.get("private_running"):
            return "good"
        if ollama_state.get("system_running"):
            return "neutral"
        if ollama_state.get("system_app_running"):
            return "neutral"
        return "neutral"

    def _update_runtime_buttons(self, ollama_state: dict, runtime_model: str) -> None:
        selected_profile = self._runtime_profile_from_display(ollama_state.get("selected_profile"))
        profiles = ollama_state.get("profiles") or {}
        bundled_available = bool(ollama_state.get("bundled_available"))
        private_installed = bool(ollama_state.get("private_installed"))
        running = bool(ollama_state.get("running"))
        self._set_button_enabled(self.runtime_mode_button, not self.runtime_busy and not self.model_busy)
        self._set_button_enabled(self.install_runtime_button, not self.runtime_busy and not self.model_busy)
        self._set_button_enabled(self.start_runtime_button, private_installed and not running and not self.runtime_busy)
        self._set_button_enabled(self.stop_runtime_button, private_installed and running and not self.runtime_busy)
        self._set_button_enabled(self.refresh_status_button, not self.runtime_busy)
        if self.manage_models_button is not None:
            self._set_button_enabled(self.manage_models_button, not self.runtime_busy and not self.model_busy)
        self._set_dependency_manager_controls_enabled(not self.runtime_busy and not self.model_busy)

    def _set_button_enabled(self, button, enabled: bool) -> None:
        if isinstance(button, ttk.Combobox):
            button.configure(state="readonly" if enabled else "disabled")
            return
        if enabled:
            button.state(["!disabled"])
        else:
            button.state(["disabled"])

    def _set_badge(self, label: tk.Label, text: str, tone: str) -> None:
        self.badge_states[label] = (text, tone)
        bg, fg = self._badge_colors(tone)
        label.configure(text=text, bg=bg, fg=fg)

    def _refresh_badges(self) -> None:
        alive_badges: dict[tk.Label, tuple[str, str]] = {}
        for label, (text, tone) in self.badge_states.items():
            try:
                if not label.winfo_exists():
                    continue
                bg, fg = self._badge_colors(tone)
                label.configure(text=text, bg=bg, fg=fg)
                alive_badges[label] = (text, tone)
            except tk.TclError:
                continue
        self.badge_states = alive_badges

    def _badge_colors(self, tone: str) -> tuple[str, str]:
        if tone == "good":
            return ("#dcfce7", "#166534") if self.theme_name == "light" else ("#052e16", "#86efac")
        if tone == "danger":
            return ("#fee2e2", "#991b1b") if self.theme_name == "light" else ("#3f0a0a", "#fecaca")
        if tone == "warning":
            return ("#fef3c7", "#92400e") if self.theme_name == "light" else ("#422006", "#fcd34d")
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
        self.model_combo.configure(state="disabled")
        self.embed_model_combo.configure(state="disabled")
        self._sync_send_button_state()
        self.set_status(f"正在切换模型到 {selected}...")

        def worker():
            launcher_core.ensure_ollama_running()
            init_runtime_settings(force_refresh=True)
            current = set_llm_model(selected)
            return {
                "current_model": current,
                "current_embed_model": get_embed_model(),
                "available_models": list_chat_models(),
                "available_embed_models": list_embedding_models(),
            }

        def on_success(payload: dict) -> None:
            self.model_busy = False
            self._sync_send_button_state()
            current_model = str(payload.get("current_model") or selected)
            self._set_model_chip(current_model)
            self.model_event_suppressed = True
            self.model_combo.configure(values=payload.get("available_models") or [current_model])
            self.model_var.set(current_model)
            self.model_event_suppressed = False
            self.model_combo.configure(state="readonly")
            self.embed_model_event_suppressed = True
            self.embed_model_combo.configure(values=payload.get("available_embed_models") or [payload.get("current_embed_model") or self._safe_runtime_embed_model()])
            self.embed_model_var.set(str(payload.get("current_embed_model") or self._safe_runtime_embed_model()))
            self.embed_model_event_suppressed = False
            self.embed_model_combo.configure(state="readonly")
            self.set_status(f"模型已切换为 {current_model}。", tone="success")
            self.refresh_settings_status(silent=True)

        def on_error(exc: Exception) -> None:
            self.model_busy = False
            self._sync_send_button_state()
            self.set_status(str(exc), tone="error")
            self.refresh_settings_status(silent=True)
            messagebox.showerror("模型切换失败", str(exc))

        self._run_worker(worker, on_success, on_error)

    def on_embed_model_selected(self, _event=None) -> None:
        if self.embed_model_event_suppressed or self.model_busy:
            return
        selected = self.embed_model_var.get().strip()
        if not selected:
            return

        self.model_busy = True
        self.model_combo.configure(state="disabled")
        self.embed_model_combo.configure(state="disabled")
        self._sync_send_button_state()
        self.set_status(f"正在切换向量模型到 {selected}...")

        def worker():
            launcher_core.ensure_ollama_running()
            init_runtime_settings(force_refresh=True)
            current = set_embed_model(selected)
            return {
                "current_model": get_llm_model(),
                "current_embed_model": current,
                "available_models": list_chat_models(),
                "available_embed_models": list_embedding_models(),
            }

        def on_success(payload: dict) -> None:
            self.model_busy = False
            self._sync_send_button_state()
            current_model = str(payload.get("current_model") or self._safe_runtime_model())
            current_embed_model = str(payload.get("current_embed_model") or selected)
            self._set_model_chip(current_model)
            self.model_event_suppressed = True
            self.model_combo.configure(values=payload.get("available_models") or [current_model])
            self.model_var.set(current_model)
            self.model_event_suppressed = False
            self.model_combo.configure(state="readonly")
            self.embed_model_event_suppressed = True
            self.embed_model_combo.configure(values=payload.get("available_embed_models") or [current_embed_model])
            self.embed_model_var.set(current_embed_model)
            self.embed_model_event_suppressed = False
            self.embed_model_combo.configure(state="readonly")
            self.set_status(f"向量模型已切换为 {current_embed_model}。", tone="success")
            self.refresh_settings_status(silent=True)

        def on_error(exc: Exception) -> None:
            self.model_busy = False
            self._sync_send_button_state()
            self.set_status(str(exc), tone="error")
            self.refresh_settings_status(silent=True)
            messagebox.showerror("向量模型切换失败", str(exc))

        self._run_worker(worker, on_success, on_error)

    def _runtime_action(self, status_text: str, worker, success_text: str) -> None:
        if self.runtime_busy:
            return
        self.runtime_busy = True
        self._sync_send_button_state()
        if self.settings_payload is not None:
            self._update_runtime_buttons(self.settings_payload.get("ollama", {}), self.model_var.get().strip())
        self.dependency_feedback_label.configure(text=status_text)
        self.set_status(status_text)

        def on_success(_payload) -> None:
            self.runtime_busy = False
            self._sync_send_button_state()
            self.refresh_settings_status(silent=True)
            self.dependency_feedback_label.configure(text=success_text)
            self.set_status(success_text, tone="success")

        def on_error(exc: Exception) -> None:
            self.runtime_busy = False
            self._sync_send_button_state()
            self.refresh_settings_status(silent=True)
            self.dependency_feedback_label.configure(text=str(exc))
            self.set_status(str(exc), tone="error")
            messagebox.showerror("运行时操作失败", str(exc))

        self._run_worker(worker, on_success, on_error)

    def _switch_runtime_profile(self, selected_profile: str, *, allow_same_profile: bool = False) -> None:
        if self.runtime_busy or self.model_busy:
            return
        selected_profile = self._runtime_profile_from_display(selected_profile)
        selected_label = self._runtime_profile_display(selected_profile)
        ollama_state = (self.settings_payload or {}).get("ollama", {})
        current_profile = self._runtime_profile_from_display(ollama_state.get("selected_profile"))
        if not allow_same_profile and selected_profile == current_profile and bool(ollama_state.get("private_installed")):
            self.set_status(f"当前已是 {selected_label}。")
            return
        if not launcher_core.bundled_ollama_available(selected_profile):
            messagebox.showerror("运行模式缺失", f"{selected_label} 的项目内置运行时当前不存在。")
            self.set_status(f"{selected_label} 的项目内置运行时缺失。", tone="error")
            return
        if selected_profile == "gpu":
            gpu_profile = ((self.settings_payload or {}).get("ollama", {}).get("profiles") or {}).get("gpu") or {}
            if not bool(gpu_profile.get("hardware_available")):
                if not messagebox.askyesno("未检测到 GPU", "当前未检测到可用 NVIDIA GPU，继续切换到 GPU 模式吗？"):
                    return

        self._runtime_action(
            f"正在切换到 {selected_label}...",
            lambda: launcher_core.apply_runtime_profile(selected_profile, restart_if_running=True, ensure_installed=True),
            f"已切换到 {selected_label}。",
        )

    def toggle_runtime_profile(self) -> None:
        current_profile = self._runtime_profile_from_display(
            ((self.settings_payload or {}).get("ollama", {}) or {}).get("selected_profile")
        )
        target_profile = "gpu" if current_profile == "cpu" else "cpu"
        self._switch_runtime_profile(target_profile)

    def install_private_ollama(self) -> None:
        ollama_state = (self.settings_payload or {}).get("ollama", {})
        selected_profile = self._runtime_profile_from_display(ollama_state.get("selected_profile"))
        selected_label = self._runtime_profile_display(selected_profile)
        if bool(ollama_state.get("private_installed")):
            self.set_status(f"{selected_label} 已部署。")
            return
        if not bool(ollama_state.get("bundled_available")):
            messagebox.showerror("内置运行时缺失", f"当前项目内没有检测到 {selected_label} 的固定版私有运行时文件。")
            self.set_status(f"{selected_label} 的项目内置运行时缺失。", tone="error")
            return
        if not messagebox.askyesno("安装私有 Ollama", f"将从项目内置固定版本部署 {selected_label}，继续吗？"):
            return
        self._runtime_action(
            f"正在部署 {selected_label}...",
            lambda: launcher_core.install_private_ollama_runtime(selected_profile),
            f"{selected_label} 已部署。",
        )

    def start_ollama_runtime(self) -> None:
        ollama_state = (self.settings_payload or {}).get("ollama", {})
        selected_profile = self._runtime_profile_from_display(ollama_state.get("selected_profile"))
        selected_label = self._runtime_profile_display(selected_profile)
        if not bool(ollama_state.get("private_installed")):
            messagebox.showinfo("提示", f"当前尚未部署 {selected_label}，请先点击“安装私有 Ollama”。")
            self.set_status(f"请先部署 {selected_label}。", tone="error")
            return
        if bool(ollama_state.get("running")) and str(ollama_state.get("mode") or "") == "private":
            self.set_status(f"{selected_label} 已在运行。")
            return
        self._runtime_action(
            f"正在启动 {selected_label}...",
            lambda: (
                launcher_core.apply_runtime_profile(selected_profile, restart_if_running=False, ensure_installed=True),
                launcher_core.start_private_ollama(),
            )[-1],
            f"{selected_label} 已启动。",
        )

    def stop_ollama_runtime(self) -> None:
        ollama_state = (self.settings_payload or {}).get("ollama", {})
        selected_profile = self._runtime_profile_from_display(ollama_state.get("selected_profile"))
        selected_label = self._runtime_profile_display(selected_profile)
        if not bool(ollama_state.get("private_installed")):
            messagebox.showinfo("提示", f"当前尚未部署 {selected_label}，无需停止。")
            self.set_status(f"{selected_label} 尚未部署。")
            return
        self._runtime_action(
            f"正在停止 {selected_label}...",
            launcher_core.stop_private_ollama,
            f"{selected_label} 已停止。",
        )

    def install_recommended_models(self) -> None:
        ollama_state = (self.settings_payload or {}).get("ollama", {})
        if not bool(ollama_state.get("running")):
            messagebox.showinfo("提示", "请先启动 Ollama，再安装推荐模型。")
            self.set_status("请先启动 Ollama。", tone="error")
            return

        selected_profile = self._runtime_profile_from_display(ollama_state.get("selected_profile"))
        target_name = f"项目私有 {self._runtime_profile_display(selected_profile)}"
        recommended_models = [launcher_core.DEFAULT_LLM_MODEL, launcher_core.DEFAULT_EMBED_MODEL]

        if self.runtime_busy:
            return
        self.runtime_busy = True
        self._sync_send_button_state()
        if self.settings_payload is not None:
            self._update_runtime_buttons(self.settings_payload.get("ollama", {}), self.model_var.get().strip())
        status_text = (
            f"正在检查推荐模型：对话模型 {launcher_core.DEFAULT_LLM_MODEL}，"
            f"向量模型 {launcher_core.DEFAULT_EMBED_MODEL}。目标位置：{target_name}。"
        )
        self.dependency_feedback_label.configure(text=status_text)
        self.set_status(status_text)

        def worker():
            launcher_core.ensure_ollama_running()
            installed_models = launcher_core.get_ollama_models()
            missing_models = [
                name
                for name in recommended_models
                if not launcher_core.test_model_installed(name, installed_models)
            ]
            for model_name in missing_models:
                launcher_core.pull_model(model_name)
            return {
                "installed": missing_models,
                "skipped": [name for name in recommended_models if name not in missing_models],
                "target_name": target_name,
            }

        def on_success(payload: dict) -> None:
            self.runtime_busy = False
            self._sync_send_button_state()
            self.refresh_settings_status(silent=True)
            installed = list(payload.get("installed") or [])
            skipped = list(payload.get("skipped") or [])
            target = str(payload.get("target_name") or target_name)
            if not installed:
                message = f"推荐模型已存在于{target}，未重复安装。"
            else:
                installed_text = "、".join(installed)
                if skipped:
                    skipped_text = "、".join(skipped)
                    message = f"已安装到{target}：{installed_text}；已跳过已有模型：{skipped_text}。"
                else:
                    message = f"已安装到{target}：{installed_text}。"
            self.dependency_feedback_label.configure(text=message)
            self.set_status(message, tone="success")

        def on_error(exc: Exception) -> None:
            self.runtime_busy = False
            self._sync_send_button_state()
            self.refresh_settings_status(silent=True)
            self.dependency_feedback_label.configure(text=str(exc))
            self.set_status(str(exc), tone="error")
            messagebox.showerror("推荐模型安装失败", str(exc))

        self._run_worker(worker, on_success, on_error)

    def delete_selected_model(self) -> None:
        ollama_state = (self.settings_payload or {}).get("ollama", {})
        if not bool(ollama_state.get("running")):
            messagebox.showinfo("提示", "请先启动 Ollama，再删除模型。")
            self.set_status("请先启动 Ollama。", tone="error")
            return
        model_name = self.model_var.get().strip()
        if not model_name:
            self.set_status("当前没有可删除的模型。", tone="error")
            return
        selected_profile = self._runtime_profile_from_display(ollama_state.get("selected_profile"))
        target_name = f"项目私有 {self._runtime_profile_display(selected_profile)}"
        if not messagebox.askyesno("删除模型", f"确定从{target_name}删除当前模型 {model_name} 吗？"):
            return

        self._runtime_action(
            f"正在从{target_name}删除模型 {model_name}...",
            lambda: launcher_core.remove_model(model_name),
            f"模型 {model_name} 已从{target_name}删除。",
        )

    def toggle_theme(self) -> None:
        self.root.update_idletasks()
        old_bg = self.palette.root_bg
        overlay = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.transient(self.root)
        overlay.geometry(
            f"{self.root.winfo_width()}x{self.root.winfo_height()}+"
            f"{self.root.winfo_rootx()}+{self.root.winfo_rooty()}"
        )
        overlay.configure(bg=old_bg)
        try:
            overlay.attributes("-topmost", True)
        except tk.TclError:
            pass
        overlay.lift()
        try:
            overlay.update()
        except tk.TclError:
            pass
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self.palette = PALETTES[self.theme_name]
        self._save_theme_name()
        self.apply_theme()
        try:
            self.root.update()
        except tk.TclError:
            pass
        self.root.update_idletasks()
        self.root.after(120, overlay.destroy)
        self.set_status(f"已切换到{'浅色' if self.theme_name == 'light' else '深色'}模式。")

    def apply_theme(self) -> None:
        self.root.configure(bg=self.palette.root_bg)
        self._configure_styles()

        role_map = {
            "root": self.palette.root_bg,
            "panel": self.palette.panel_bg,
            "alt": self.palette.panel_alt,
            "input": self.palette.input_bg,
            "chrome": self.palette.chrome_bg,
        }
        for widget, role in self.surface_roles:
            kwargs = {"bg": role_map.get(role, self.palette.panel_bg)}
            if isinstance(widget, tk.Frame):
                kwargs["highlightbackground"] = self.palette.border
            widget.configure(**kwargs)

        for scroll_area in self.scroll_areas:
            scroll_area.set_colors(
                self.palette.panel_alt if scroll_area in {self.chat_area, self.notes_area} else self.palette.panel_bg,
                self.palette,
            )

        for label, role in self.label_roles:
            label.configure(bg=label.master.cget("bg"), fg=self.palette.text if role == "text" else self.palette.muted)
        self._refresh_badges()
        self.auth_toggle_link_label.configure(
            bg=self.auth_toggle_link_label.master.cget("bg"),
            fg=self.palette.accent,
        )
        self._render_password_peek_icon()
        for note_id in list(self.note_visuals):
            self._restyle_note_card(note_id, animate=False)
        self._restyle_chat_visuals()
        self._sync_question_placeholder()

        for text in self.text_widgets:
            text.configure(
                bg=self.palette.input_bg,
                fg=self.palette.input_fg,
                insertbackground=self.palette.input_fg,
                highlightbackground=self.palette.border,
                highlightcolor=self.palette.border,
            )

        for entry in self.entry_widgets:
            entry.configure(
                bg=self.palette.input_bg,
                fg=self.palette.input_fg,
                insertbackground=self.palette.input_fg,
                disabledbackground=self.palette.chrome_bg,
                disabledforeground=self.palette.muted,
                readonlybackground=self.palette.chrome_bg,
                highlightbackground=self.palette.border,
                highlightcolor=self.palette.border,
                highlightthickness=1,
                selectbackground=self.palette.accent,
                selectforeground="#ffffff",
            )
            if entry is getattr(self, "auth_password_entry", None):
                entry.configure(highlightthickness=0)

        if hasattr(self, "editor_text"):
            self.editor_text.tag_configure("current_line", background=blend_hex(self.palette.chip_bg, self.palette.panel_bg, 0.35))
            self.editor_text.tag_configure("search_match", background=blend_hex(self.palette.accent, self.palette.panel_bg, 0.72), foreground=self.palette.text)
        self.model_chip.configure(bg=self.palette.chip_bg, fg=self.palette.chip_fg)
        self.status_line.configure(bg=self.palette.root_bg)

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
        self._apply_skeleton_theme()
        self._render_login_backdrop()
        if self.current_page == "settings" and self.settings_payload is not None:
            self.apply_settings_payload(self.settings_payload)
        self._apply_editor_search_highlight(self.editor_highlight_query)
        self._refresh_note_search_results()
        self._refresh_members_window()
        self.refresh_dependency_manager_window(announce=False)
        self._refresh_model_manager_window()

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
            background="#a5b8ce" if self.theme_name == "light" else "#6c819a",
            troughcolor="#e4edf7" if self.theme_name == "light" else "#131f34",
            bordercolor=self.palette.border,
            arrowcolor=self.palette.text,
            darkcolor="#a5b8ce" if self.theme_name == "light" else "#6c819a",
            lightcolor="#a5b8ce" if self.theme_name == "light" else "#6c819a",
            relief="flat",
            gripcount=0,
            arrowsize=13,
            width=16,
        )
        self.style.map(
            "App.Vertical.TScrollbar",
            background=[("active", "#8ea6c2" if self.theme_name == "light" else "#86a0bb")],
            arrowcolor=[("active", self.palette.text)],
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

    def _perform_close(self) -> None:
        if self.shutdown_in_progress:
            return
        self.shutdown_in_progress = True
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)
        self._close_members_window()
        self._close_dependency_manager_window()
        self._close_model_manager_window()
        self._close_note_search_window()
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

    def on_close(self) -> None:
        if self.current_page == "editor" and self.editor_dirty:
            self._attempt_leave_editor(self._perform_close)
            return
        self._perform_close()

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
        self.root.deiconify()
        self.root.update_idletasks()
        self.root.after(240, lambda: self._set_login_loading(False))
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
