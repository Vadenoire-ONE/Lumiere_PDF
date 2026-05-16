"""
Lumiere PDF — простой и лёгкий редактор PDF.

Возможности:
  • Просмотр PDF (постранично, с масштабированием)
  • Объединение нескольких PDF в один
  • Разделение PDF (по диапазонам страниц или постранично)
  • Определение формата листа (A4, A3, Letter, ...) для каждой страницы
  • Извлечение текста (всего документа или текущей страницы)
  • Извлечение изображений из PDF

Зависимости: PyMuPDF (fitz), Pillow, Tkinter (входит в стандартную поставку Python).
"""

from __future__ import annotations

import io
import os
import threading
from dataclasses import dataclass
from typing import List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import fitz  # PyMuPDF
from PIL import Image, ImageTk


# ---------- Определение формата листа ----------------------------------------

# Размеры в миллиметрах (ширина, высота)
PAPER_SIZES_MM: List[Tuple[str, float, float]] = [
    ("A0", 841, 1189),
    ("A1", 594, 841),
    ("A2", 420, 594),
    ("A3", 297, 420),
    ("A4", 210, 297),
    ("A5", 148, 210),
    ("A6", 105, 148),
    ("B4", 250, 353),
    ("B5", 176, 250),
    ("Letter", 215.9, 279.4),
    ("Legal", 215.9, 355.6),
    ("Tabloid", 279.4, 431.8),
    ("Executive", 184.15, 266.7),
]

PT_TO_MM = 25.4 / 72.0
TOLERANCE_MM = 3.0  # допуск ±3 мм


def detect_paper_format(width_pt: float, height_pt: float) -> str:
    """Определить название формата листа по размерам в пунктах."""
    w_mm = width_pt * PT_TO_MM
    h_mm = height_pt * PT_TO_MM
    # сравниваем по длинной/короткой стороне, чтобы учитывать ориентацию
    short, long_ = sorted((w_mm, h_mm))
    for name, sw, sh in PAPER_SIZES_MM:
        ssw, ssh = sorted((sw, sh))
        if abs(short - ssw) <= TOLERANCE_MM and abs(long_ - ssh) <= TOLERANCE_MM:
            orientation = "альбомная" if w_mm > h_mm else "книжная"
            return f"{name} ({orientation})"
    return f"Custom {w_mm:.0f}×{h_mm:.0f} мм"


# ---------- Парсер диапазонов страниц ----------------------------------------

def parse_page_ranges(spec: str, total_pages: int) -> List[List[int]]:
    """
    Разобрать строку вида "1-3,5,7-9" в список групп 0-based страниц:
    [[0,1,2],[4],[6,7,8]]
    """
    groups: List[List[int]] = []
    spec = (spec or "").strip()
    if not spec:
        return groups
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            start = int(a)
            end = int(b)
        else:
            start = end = int(part)
        if start < 1 or end < 1 or start > total_pages or end > total_pages or start > end:
            raise ValueError(f"Некорректный диапазон: '{part}' (страниц всего: {total_pages})")
        groups.append(list(range(start - 1, end)))
    return groups


# ---------- Основное окно ----------------------------------------------------

@dataclass
class ViewState:
    page_index: int = 0
    zoom: float = 1.25

def _is_contiguous(pages: List[int]) -> bool:
    return all(pages[i] + 1 == pages[i + 1] for i in range(len(pages) - 1))


class SplitDialog(tk.Toplevel):
    """Диалог разделения PDF: выбор страниц вручную или по формату."""

    def __init__(self, app: "LumierePDF") -> None:
        super().__init__(app)
        self.app = app
        self.doc = app.doc
        assert self.doc is not None

        self.title("Разделить PDF")
        self.geometry("640x540")
        self.transient(app)
        self.grab_set()

        # --- Собираем форматы страниц ---
        self.page_formats: List[str] = []
        for i in range(self.doc.page_count):
            r = self.doc.load_page(i).rect
            self.page_formats.append(detect_paper_format(r.width, r.height))

        # --- Верхняя панель: режим вывода ---
        top = ttk.Frame(self, padding=8)
        top.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(top, text="Режим сохранения:").pack(side=tk.LEFT)
        self.mode_var = tk.StringVar(value="by_format")
        ttk.Radiobutton(top, text="Группировать по формату",
                        variable=self.mode_var, value="by_format").pack(side=tk.LEFT, padx=6)
        ttk.Radiobutton(top, text="Каждая страница — отдельный PDF",
                        variable=self.mode_var, value="per_page").pack(side=tk.LEFT, padx=6)
        ttk.Radiobutton(top, text="Все выбранные — в один PDF",
                        variable=self.mode_var, value="single").pack(side=tk.LEFT, padx=6)

        # --- Панель действий выбора ---
        actions = ttk.Frame(self, padding=(8, 0))
        actions.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(actions, text="Выбрать все", command=self._select_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(actions, text="Снять все", command=self._select_none).pack(side=tk.LEFT, padx=2)
        ttk.Button(actions, text="Инвертировать", command=self._invert).pack(side=tk.LEFT, padx=2)

        ttk.Separator(actions, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        ttk.Label(actions, text="По формату:").pack(side=tk.LEFT)
        self.format_var = tk.StringVar()
        formats_unique = sorted(set(self.page_formats))
        self.format_combo = ttk.Combobox(
            actions, textvariable=self.format_var, values=formats_unique,
            state="readonly", width=24,
        )
        if formats_unique:
            self.format_combo.current(0)
        self.format_combo.pack(side=tk.LEFT, padx=4)
        ttk.Button(actions, text="Выбрать", command=self._select_by_format).pack(side=tk.LEFT, padx=2)
        ttk.Button(actions, text="Добавить", command=lambda: self._select_by_format(add=True)).pack(side=tk.LEFT, padx=2)

        # --- Диапазоны ---
        ranges = ttk.Frame(self, padding=(8, 4))
        ranges.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(ranges, text="Диапазоны (например 1-3,5,7-9):").pack(side=tk.LEFT)
        self.range_var = tk.StringVar()
        ttk.Entry(ranges, textvariable=self.range_var, width=28).pack(side=tk.LEFT, padx=4)
        ttk.Button(ranges, text="Применить", command=self._apply_range).pack(side=tk.LEFT)

        # --- Таблица страниц ---
        table_frame = ttk.Frame(self, padding=(8, 4))
        table_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        cols = ("sel", "page", "format")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", selectmode="extended")
        self.tree.heading("sel", text="✓")
        self.tree.heading("page", text="Стр.")
        self.tree.heading("format", text="Формат")
        self.tree.column("sel", width=40, anchor="center", stretch=False)
        self.tree.column("page", width=70, anchor="center", stretch=False)
        self.tree.column("format", width=300, anchor="w")

        vsb = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.selected: List[bool] = [False] * self.doc.page_count
        self.iids: List[str] = []
        for i in range(self.doc.page_count):
            iid = self.tree.insert("", tk.END, values=("", i + 1, self.page_formats[i]))
            self.iids.append(iid)

        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<space>", self._on_space)
        self.tree.bind("<Double-Button-1>", self._on_double_click)

        # По умолчанию — выбраны все
        self._select_all()

        # --- Низ: статус и кнопки ---
        bottom = ttk.Frame(self, padding=8)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        self.status_var = tk.StringVar()
        ttk.Label(bottom, textvariable=self.status_var).pack(side=tk.LEFT)
        ttk.Button(bottom, text="Отмена", command=self.destroy).pack(side=tk.RIGHT, padx=2)
        ttk.Button(bottom, text="Сохранить…", command=self._on_save).pack(side=tk.RIGHT, padx=2)

        self._update_status()

    # ---- helpers ----
    def _update_status(self) -> None:
        n = sum(self.selected)
        self.status_var.set(f"Выбрано страниц: {n} из {self.doc.page_count}")

    def _refresh_row(self, idx: int) -> None:
        mark = "✓" if self.selected[idx] else ""
        self.tree.set(self.iids[idx], "sel", mark)

    def _refresh_all(self) -> None:
        for i in range(len(self.selected)):
            self._refresh_row(i)
        self._update_status()

    def _toggle(self, idx: int) -> None:
        self.selected[idx] = not self.selected[idx]
        self._refresh_row(idx)
        self._update_status()

    # ---- handlers ----
    def _on_click(self, event: tk.Event) -> None:
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        col = self.tree.identify_column(event.x)
        row = self.tree.identify_row(event.y)
        if not row:
            return
        if col == "#1":  # колонка "sel"
            try:
                idx = self.iids.index(row)
            except ValueError:
                return
            self._toggle(idx)

    def _on_space(self, _event: tk.Event) -> None:
        for row in self.tree.selection():
            try:
                idx = self.iids.index(row)
            except ValueError:
                continue
            self._toggle(idx)

    def _on_double_click(self, event: tk.Event) -> None:
        row = self.tree.identify_row(event.y)
        if not row:
            return
        try:
            idx = self.iids.index(row)
        except ValueError:
            return
        # Переходим к странице в просмотрщике
        self.app.view.page_index = idx
        self.app._render_page()

    def _select_all(self) -> None:
        self.selected = [True] * len(self.selected)
        self._refresh_all()

    def _select_none(self) -> None:
        self.selected = [False] * len(self.selected)
        self._refresh_all()

    def _invert(self) -> None:
        self.selected = [not s for s in self.selected]
        self._refresh_all()

    def _select_by_format(self, add: bool = False) -> None:
        fmt = self.format_var.get()
        if not fmt:
            return
        if not add:
            self.selected = [False] * len(self.selected)
        for i, f in enumerate(self.page_formats):
            if f == fmt:
                self.selected[i] = True
        self._refresh_all()

    def _apply_range(self) -> None:
        spec = self.range_var.get().strip()
        if not spec:
            return
        try:
            groups = parse_page_ranges(spec, len(self.selected))
        except ValueError as exc:
            messagebox.showerror("Ошибка", str(exc), parent=self)
            return
        self.selected = [False] * len(self.selected)
        for grp in groups:
            for p in grp:
                self.selected[p] = True
        self._refresh_all()

    # ---- save ----
    def _on_save(self) -> None:
        chosen = [i for i, s in enumerate(self.selected) if s]
        if not chosen:
            messagebox.showinfo("Разделение", "Не выбрано ни одной страницы.", parent=self)
            return

        mode = self.mode_var.get()
        labels: Optional[List[str]] = None
        if mode == "per_page":
            groups = [[i] for i in chosen]
        elif mode == "single":
            groups = [chosen]
        elif mode == "by_format":
            buckets: dict[str, List[int]] = {}
            for i in chosen:
                buckets.setdefault(self.page_formats[i], []).append(i)
            items = sorted(buckets.items(), key=lambda kv: kv[1][0])
            groups = [sorted(v) for _, v in items]
            labels = [fmt for fmt, _ in items]
        else:
            groups = [chosen]

        out_dir = filedialog.askdirectory(title="Папка для сохранения частей", parent=self)
        if not out_dir:
            return
        self.app._run_split(groups, out_dir, labels=labels)
        self.destroy()

class LumierePDF(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Lumiere PDF — простой редактор PDF")
        self.geometry("1100x760")
        self.minsize(900, 600)

        self.doc: Optional[fitz.Document] = None
        self.pdf_path: Optional[str] = None
        self.view = ViewState()
        self._photo: Optional[ImageTk.PhotoImage] = None  # удерживаем ссылку
        self._thumbs: List[ImageTk.PhotoImage] = []  # удерживаем ссылки на превью
        self._thumb_buttons: List[tk.Widget] = []
        self._thumbs_thread: Optional[threading.Thread] = None
        self._thumbs_token: int = 0  # для отмены устаревших задач

        self._build_ui()
        self._update_controls()

    # ---- UI ----------------------------------------------------------------

    def _build_ui(self) -> None:
        # Меню
        menubar = tk.Menu(self)
        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Открыть PDF…", accelerator="Ctrl+O", command=self.open_pdf)
        file_menu.add_command(label="Закрыть", command=self.close_pdf)
        file_menu.add_separator()
        file_menu.add_command(label="Выход", command=self.destroy)
        menubar.add_cascade(label="Файл", menu=file_menu)

        tools_menu = tk.Menu(menubar, tearoff=0)
        tools_menu.add_command(label="Объединить PDF…", command=self.merge_pdfs)
        tools_menu.add_command(label="Разделить PDF…", command=self.split_pdf)
        tools_menu.add_command(label="Разделить по формату листа", command=self.split_by_format)
        tools_menu.add_separator()
        tools_menu.add_command(label="Извлечь текст…", command=self.extract_text)
        tools_menu.add_command(label="Извлечь изображения…", command=self.extract_images)
        tools_menu.add_separator()
        tools_menu.add_command(label="Подготовить для NotebookLM…", command=self.export_for_notebooklm)
        tools_menu.add_separator()
        tools_menu.add_command(label="Информация о страницах (форматы)", command=self.show_page_formats)
        menubar.add_cascade(label="Инструменты", menu=tools_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="О программе", command=self._about)
        menubar.add_cascade(label="Справка", menu=help_menu)
        self.config(menu=menubar)

        # Панель инструментов
        toolbar = ttk.Frame(self, padding=4)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(toolbar, text="Открыть", command=self.open_pdf).pack(side=tk.LEFT, padx=2)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)

        self.btn_prev = ttk.Button(toolbar, text="◀", width=3, command=self.prev_page)
        self.btn_prev.pack(side=tk.LEFT, padx=2)
        self.page_var = tk.StringVar(value="—")
        ttk.Label(toolbar, textvariable=self.page_var, width=14, anchor="center").pack(side=tk.LEFT)
        self.btn_next = ttk.Button(toolbar, text="▶", width=3, command=self.next_page)
        self.btn_next.pack(side=tk.LEFT, padx=2)

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)
        ttk.Button(toolbar, text="−", width=3, command=lambda: self.zoom(0.8)).pack(side=tk.LEFT)
        self.zoom_var = tk.StringVar(value="125%")
        ttk.Label(toolbar, textvariable=self.zoom_var, width=6, anchor="center").pack(side=tk.LEFT)
        ttk.Button(toolbar, text="+", width=3, command=lambda: self.zoom(1.25)).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="100%", command=lambda: self.set_zoom(1.0)).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="По ширине", command=self.fit_width).pack(side=tk.LEFT, padx=2)

        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)
        ttk.Button(toolbar, text="Объединить", command=self.merge_pdfs).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Разделить", command=self.split_pdf).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Текст", command=self.extract_text).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Изображения", command=self.extract_images).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Форматы", command=self.show_page_formats).pack(side=tk.LEFT, padx=2)

        # Тело: панель миниатюр слева + область просмотра справа
        body = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True)

        # --- Панель миниатюр ---
        thumbs_frame = ttk.Frame(body, width=170)
        thumbs_frame.pack_propagate(False)
        body.add(thumbs_frame, weight=0)

        thumbs_header = ttk.Frame(thumbs_frame)
        thumbs_header.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(thumbs_header, text="Миниатюры", anchor="w", padding=(6, 4)).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.btn_toggle_thumbs = ttk.Button(thumbs_header, text="⮜", width=3, command=self.toggle_thumbnails)
        self.btn_toggle_thumbs.pack(side=tk.RIGHT)

        self.thumbs_canvas = tk.Canvas(thumbs_frame, bg="#2b2b2b", highlightthickness=0, width=160)
        thumbs_vsb = ttk.Scrollbar(thumbs_frame, orient=tk.VERTICAL, command=self.thumbs_canvas.yview)
        self.thumbs_canvas.configure(yscrollcommand=thumbs_vsb.set)
        thumbs_vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.thumbs_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.thumbs_inner = ttk.Frame(self.thumbs_canvas)
        self._thumbs_window = self.thumbs_canvas.create_window((0, 0), window=self.thumbs_inner, anchor="nw")
        self.thumbs_inner.bind(
            "<Configure>",
            lambda e: self.thumbs_canvas.configure(scrollregion=self.thumbs_canvas.bbox("all")),
        )
        self.thumbs_canvas.bind(
            "<Configure>",
            lambda e: self.thumbs_canvas.itemconfigure(self._thumbs_window, width=e.width),
        )
        self.thumbs_canvas.bind(
            "<MouseWheel>",
            lambda e: self.thumbs_canvas.yview_scroll(-1 if e.delta > 0 else 1, "units"),
        )
        self._thumbs_frame = thumbs_frame

        # --- Область просмотра ---
        viewer = ttk.Frame(body)
        body.add(viewer, weight=1)

        self.canvas = tk.Canvas(viewer, bg="#3a3a3a", highlightthickness=0)
        vsb = ttk.Scrollbar(viewer, orient=tk.VERTICAL, command=self.canvas.yview)
        hsb = ttk.Scrollbar(viewer, orient=tk.HORIZONTAL, command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._body_paned = body

        # Статус-бар
        self.status_var = tk.StringVar(value="Готово")
        status = ttk.Label(self, textvariable=self.status_var, anchor="w", relief=tk.SUNKEN, padding=(6, 2))
        status.pack(side=tk.BOTTOM, fill=tk.X)

        # Горячие клавиши
        self.bind("<Control-o>", lambda e: self.open_pdf())
        self.bind("<Left>", lambda e: self.prev_page())
        self.bind("<Right>", lambda e: self.next_page())
        self.bind("<Prior>", lambda e: self.prev_page())  # PageUp
        self.bind("<Next>", lambda e: self.next_page())   # PageDown
        self.bind("<Control-plus>", lambda e: self.zoom(1.25))
        self.bind("<Control-equal>", lambda e: self.zoom(1.25))
        self.bind("<Control-minus>", lambda e: self.zoom(0.8))
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)

    # ---- Открытие/закрытие -------------------------------------------------

    def open_pdf(self) -> None:
        path = filedialog.askopenfilename(
            title="Открыть PDF",
            filetypes=[("PDF файлы", "*.pdf"), ("Все файлы", "*.*")],
        )
        if not path:
            return
        try:
            doc = fitz.open(path)
        except Exception as exc:
            messagebox.showerror("Ошибка", f"Не удалось открыть файл:\n{exc}")
            return
        self.close_pdf()
        self.doc = doc
        self.pdf_path = path
        self.view = ViewState()
        self._render_page()
        self._build_thumbnails()
        self._update_controls()
        self.status_var.set(f"Открыт: {os.path.basename(path)} • страниц: {doc.page_count}")

    def close_pdf(self) -> None:
        if self.doc is not None:
            try:
                self.doc.close()
            except Exception:
                pass
        self.doc = None
        self.pdf_path = None
        self.canvas.delete("all")
        self._photo = None
        self._clear_thumbnails()
        self._update_controls()
        self.status_var.set("Готово")

    # ---- Рендер страницы ---------------------------------------------------

    def _render_page(self) -> None:
        if self.doc is None:
            return
        idx = max(0, min(self.view.page_index, self.doc.page_count - 1))
        self.view.page_index = idx
        page = self.doc.load_page(idx)
        matrix = fitz.Matrix(self.view.zoom, self.view.zoom)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        self._photo = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw")
        self.canvas.configure(scrollregion=(0, 0, pix.width, pix.height))
        self.page_var.set(f"{idx + 1} / {self.doc.page_count}")
        self.zoom_var.set(f"{int(self.view.zoom * 100)}%")
        self._highlight_thumbnail(idx)

    # ---- Миниатюры ---------------------------------------------------------

    def _clear_thumbnails(self) -> None:
        self._thumbs_token += 1  # отменить фоновые задачи
        for child in self.thumbs_inner.winfo_children():
            child.destroy()
        self._thumbs.clear()
        self._thumb_buttons.clear()

    def _build_thumbnails(self) -> None:
        self._clear_thumbnails()
        if self.doc is None:
            return

        token = self._thumbs_token
        page_count = self.doc.page_count

        # Создаём плейсхолдеры сразу — рендер делаем в фоне
        for i in range(page_count):
            frame = tk.Frame(
                self.thumbs_inner, bg="#2b2b2b", highlightthickness=2,
                highlightbackground="#2b2b2b", highlightcolor="#2b2b2b",
            )
            frame.pack(fill=tk.X, padx=4, pady=3)
            lbl_img = tk.Label(frame, bg="#cccccc", width=18, height=10)
            lbl_img.pack(padx=2, pady=(2, 0))
            lbl_num = tk.Label(frame, text=str(i + 1), bg="#2b2b2b", fg="#dddddd")
            lbl_num.pack(pady=(0, 2))

            def _go(_e=None, idx=i):
                if self.doc is not None:
                    self.view.page_index = idx
                    self._render_page()

            for w in (frame, lbl_img, lbl_num):
                w.bind("<Button-1>", _go)
            self._thumb_buttons.append(frame)

        self._highlight_thumbnail(self.view.page_index)

        def worker(doc_ref: fitz.Document) -> None:
            for i in range(page_count):
                if token != self._thumbs_token:
                    return
                try:
                    page = doc_ref.load_page(i)
                    pix = page.get_pixmap(matrix=fitz.Matrix(0.2, 0.2), alpha=False)
                    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    img.thumbnail((140, 180))
                except Exception:
                    continue

                def apply(idx=i, image=img):
                    if token != self._thumbs_token or idx >= len(self._thumb_buttons):
                        return
                    photo = ImageTk.PhotoImage(image)
                    # храним ссылку, чтобы не уничтожил GC
                    while len(self._thumbs) <= idx:
                        self._thumbs.append(None)  # type: ignore[arg-type]
                    self._thumbs[idx] = photo
                    frame = self._thumb_buttons[idx]
                    children = frame.winfo_children()
                    if children:
                        children[0].configure(image=photo, width=image.width, height=image.height)

                self.after(0, apply)

        t = threading.Thread(target=worker, args=(self.doc,), daemon=True)
        self._thumbs_thread = t
        t.start()

    def _highlight_thumbnail(self, idx: int) -> None:
        for i, frame in enumerate(self._thumb_buttons):
            color = "#4a90e2" if i == idx else "#2b2b2b"
            try:
                frame.configure(highlightbackground=color, highlightcolor=color)
            except tk.TclError:
                pass
        # Прокрутить к выбранной
        if 0 <= idx < len(self._thumb_buttons):
            frame = self._thumb_buttons[idx]
            try:
                self.thumbs_canvas.update_idletasks()
                bbox = self.thumbs_canvas.bbox("all")
                if bbox:
                    y = frame.winfo_y()
                    total = bbox[3] - bbox[1]
                    if total > 0:
                        self.thumbs_canvas.yview_moveto(max(0, y / total))
            except tk.TclError:
                pass

    def toggle_thumbnails(self) -> None:
        try:
            panes = self._body_paned.panes()
            frame_id = str(self._thumbs_frame)
            if frame_id in panes:
                self._body_paned.forget(self._thumbs_frame)
                self.btn_toggle_thumbs.configure(text="⮞")
            else:
                self._body_paned.insert(0, self._thumbs_frame, weight=0)
                self.btn_toggle_thumbs.configure(text="⮜")
        except tk.TclError:
            pass

    def _update_controls(self) -> None:
        has = self.doc is not None
        state = (tk.NORMAL if has else tk.DISABLED)
        for btn in (self.btn_prev, self.btn_next):
            btn.configure(state=state)
        if not has:
            self.page_var.set("—")
            self.zoom_var.set("—")

    def prev_page(self) -> None:
        if self.doc and self.view.page_index > 0:
            self.view.page_index -= 1
            self._render_page()

    def next_page(self) -> None:
        if self.doc and self.view.page_index < self.doc.page_count - 1:
            self.view.page_index += 1
            self._render_page()

    def zoom(self, factor: float) -> None:
        if not self.doc:
            return
        self.view.zoom = max(0.1, min(8.0, self.view.zoom * factor))
        self._render_page()

    def set_zoom(self, value: float) -> None:
        if not self.doc:
            return
        self.view.zoom = max(0.1, min(8.0, value))
        self._render_page()

    def fit_width(self) -> None:
        if not self.doc:
            return
        page = self.doc.load_page(self.view.page_index)
        canvas_w = self.canvas.winfo_width() or 800
        page_w = page.rect.width or 1
        self.view.zoom = max(0.1, min(8.0, (canvas_w - 20) / page_w))
        self._render_page()

    def _on_mousewheel(self, event: tk.Event) -> None:
        if not self.doc:
            return
        # Ctrl + колесо = масштаб, иначе прокрутка
        if event.state & 0x0004:
            self.zoom(1.1 if event.delta > 0 else 0.9)
        else:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    # ---- Объединение -------------------------------------------------------

    def merge_pdfs(self) -> None:
        files = filedialog.askopenfilenames(
            title="Выберите PDF-файлы для объединения (порядок = порядок выбора)",
            filetypes=[("PDF файлы", "*.pdf")],
        )
        if not files:
            return
        if len(files) < 2:
            messagebox.showinfo("Объединение", "Нужно выбрать минимум два PDF-файла.")
            return
        out = filedialog.asksaveasfilename(
            title="Сохранить объединённый PDF",
            defaultextension=".pdf",
            filetypes=[("PDF файлы", "*.pdf")],
            initialfile="merged.pdf",
        )
        if not out:
            return

        def worker() -> None:
            try:
                merged = fitz.open()
                for f in files:
                    with fitz.open(f) as src:
                        merged.insert_pdf(src)
                merged.save(out)
                merged.close()
                self.after(0, lambda: messagebox.showinfo(
                    "Объединение", f"Готово!\nСохранено: {out}"))
                self.after(0, lambda: self.status_var.set(f"Объединено {len(files)} файлов → {os.path.basename(out)}"))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Ошибка", f"Не удалось объединить:\n{exc}"))

        threading.Thread(target=worker, daemon=True).start()

    # ---- Разделение --------------------------------------------------------

    def split_pdf(self) -> None:
        if self.doc is None:
            messagebox.showinfo("Разделение", "Сначала откройте PDF.")
            return
        SplitDialog(self)

    def split_by_format(self) -> None:
        """Автоматически разделить весь документ по формату листа."""
        if self.doc is None:
            messagebox.showinfo("Разделение", "Сначала откройте PDF.")
            return

        buckets: dict[str, List[int]] = {}
        for i in range(self.doc.page_count):
            r = self.doc.load_page(i).rect
            fmt = detect_paper_format(r.width, r.height)
            buckets.setdefault(fmt, []).append(i)

        if len(buckets) <= 1:
            only = next(iter(buckets), "—")
            messagebox.showinfo(
                "Разделение по формату",
                f"В документе только один формат: {only}.\nРазделение не требуется.",
            )
            return

        summary = "\n".join(f"  • {fmt}: {len(pages)} стр." for fmt, pages in buckets.items())
        if not messagebox.askyesno(
            "Разделение по формату",
            f"Найдено форматов: {len(buckets)}\n{summary}\n\nСоздать по одному PDF на каждый формат?",
        ):
            return

        out_dir = filedialog.askdirectory(title="Папка для сохранения частей")
        if not out_dir:
            return

        items = sorted(buckets.items(), key=lambda kv: kv[1][0])
        groups = [sorted(pages) for _, pages in items]
        labels = [fmt for fmt, _ in items]
        self._run_split(groups, out_dir, labels=labels)

    def _run_split(self, groups: List[List[int]], out_dir: str,
                   labels: Optional[List[str]] = None) -> None:
        """Сохранить группы страниц в отдельные PDF в out_dir.

        Если переданы labels — они используются как суффикс имени файла."""
        if self.doc is None or not groups or not out_dir:
            return
        base = os.path.splitext(os.path.basename(self.pdf_path or "document.pdf"))[0]
        doc_ref = self.doc

        def safe(name: str) -> str:
            for ch in '<>:"/\\|?*':
                name = name.replace(ch, "_")
            return name.strip().replace(" ", "_") or "part"

        def worker() -> None:
            try:
                created: List[str] = []
                for i, pages in enumerate(groups, start=1):
                    if not pages:
                        continue
                    new = fitz.open()
                    for p in pages:
                        new.insert_pdf(doc_ref, from_page=p, to_page=p)
                    if labels and i - 1 < len(labels) and labels[i - 1]:
                        suffix = safe(labels[i - 1])
                    elif len(pages) == 1:
                        suffix = f"page{pages[0] + 1}"
                    elif _is_contiguous(pages):
                        suffix = f"part{i}_p{pages[0] + 1}-{pages[-1] + 1}"
                    else:
                        suffix = f"part{i}_{len(pages)}pages"
                    out_path = os.path.join(out_dir, f"{base}_{suffix}.pdf")
                    new.save(out_path)
                    new.close()
                    created.append(out_path)
                self.after(0, lambda: messagebox.showinfo(
                    "Разделение", f"Создано файлов: {len(created)}\nПапка: {out_dir}"))
                self.after(0, lambda: self.status_var.set(f"Разделено на {len(created)} файлов"))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Ошибка", f"Не удалось разделить:\n{exc}"))

        threading.Thread(target=worker, daemon=True).start()

    # ---- Извлечение текста -------------------------------------------------

    def extract_text(self) -> None:
        if self.doc is None:
            messagebox.showinfo("Извлечение текста", "Сначала откройте PDF.")
            return

        choice = messagebox.askyesnocancel(
            "Извлечение текста",
            "Да — извлечь текст всего документа\n"
            "Нет — только текущей страницы\n"
            "Отмена — выход",
        )
        if choice is None:
            return

        try:
            if choice:
                parts = []
                for i, page in enumerate(self.doc, start=1):
                    parts.append(f"--- Страница {i} ---\n{page.get_text()}")
                text = "\n\n".join(parts)
                default_name = (os.path.splitext(os.path.basename(self.pdf_path or 'document'))[0] + ".txt")
            else:
                page = self.doc.load_page(self.view.page_index)
                text = page.get_text()
                default_name = (
                    os.path.splitext(os.path.basename(self.pdf_path or 'document'))[0]
                    + f"_p{self.view.page_index + 1}.txt"
                )
        except Exception as exc:
            messagebox.showerror("Ошибка", f"Не удалось извлечь текст:\n{exc}")
            return

        out = filedialog.asksaveasfilename(
            title="Сохранить текст",
            defaultextension=".txt",
            filetypes=[("Текст", "*.txt"), ("Все файлы", "*.*")],
            initialfile=default_name,
        )
        if not out:
            return
        try:
            with open(out, "w", encoding="utf-8") as f:
                f.write(text)
            self.status_var.set(f"Текст сохранён: {os.path.basename(out)}")
            messagebox.showinfo("Готово", f"Текст сохранён:\n{out}")
        except Exception as exc:
            messagebox.showerror("Ошибка", f"Не удалось сохранить:\n{exc}")

    # ---- Экспорт для NotebookLM --------------------------------------------

    def export_for_notebooklm(self) -> None:
        """Подготовить загруженный PDF к загрузке в NotebookLM.

        NotebookLM лучше всего работает с текстовыми источниками. Этот
        экспорт извлекает текст постранично и сохраняет аккуратный
        Markdown-файл с разделителями страниц и заголовком документа.
        Если PDF — скан без текстового слоя, программа предложит
        сохранить уменьшенную копию PDF (NotebookLM сам распознает её).
        """
        if self.doc is None:
            messagebox.showinfo("NotebookLM", "Сначала откройте PDF.")
            return

        doc_ref = self.doc
        src_path = self.pdf_path or "document.pdf"
        base = os.path.splitext(os.path.basename(src_path))[0]

        def worker() -> None:
            try:
                pages_text: List[str] = []
                empty_pages = 0
                total_chars = 0
                for i, page in enumerate(doc_ref, start=1):
                    t = (page.get_text("text") or "").strip()
                    if not t:
                        empty_pages += 1
                    total_chars += len(t)
                    pages_text.append(t)

                page_count = doc_ref.page_count
                has_text = total_chars > 100 and empty_pages < page_count

                self.after(0, lambda: self._finish_notebooklm_export(
                    base, src_path, pages_text, empty_pages, page_count, has_text,
                ))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror(
                    "Ошибка", f"Не удалось подготовить файл:\n{exc}"))

        self.status_var.set("Подготовка для NotebookLM…")
        threading.Thread(target=worker, daemon=True).start()

    def _finish_notebooklm_export(
        self,
        base: str,
        src_path: str,
        pages_text: List[str],
        empty_pages: int,
        page_count: int,
        has_text: bool,
    ) -> None:
        if self.doc is None:
            return

        # Скан / нет текстового слоя — предложить сохранить PDF-копию
        if not has_text:
            ok = messagebox.askyesno(
                "NotebookLM",
                "В этом PDF почти нет текстового слоя (похоже на скан).\n\n"
                "Сохранить очищенную копию PDF для загрузки в NotebookLM?\n"
                "(NotebookLM сам распознает текст из скана.)",
            )
            if not ok:
                self.status_var.set("Отменено")
                return
            out = filedialog.asksaveasfilename(
                title="Сохранить PDF для NotebookLM",
                defaultextension=".pdf",
                filetypes=[("PDF", "*.pdf")],
                initialfile=f"{base}_notebooklm.pdf",
            )
            if not out:
                self.status_var.set("Отменено")
                return
            try:
                # garbage=4 + deflate ужимают файл
                self.doc.save(out, garbage=4, deflate=True, clean=True)
                self.status_var.set(f"Сохранено для NotebookLM: {os.path.basename(out)}")
                messagebox.showinfo("Готово", f"Файл готов к загрузке в NotebookLM:\n{out}")
            except Exception as exc:
                messagebox.showerror("Ошибка", f"Не удалось сохранить PDF:\n{exc}")
            return

        # Есть текст — собираем Markdown
        meta = self.doc.metadata or {}
        title = (meta.get("title") or base).strip() or base
        author = (meta.get("author") or "").strip()

        header_lines = [f"# {title}", ""]
        if author:
            header_lines.append(f"_Автор:_ {author}")
        header_lines.append(f"_Источник:_ {os.path.basename(src_path)}")
        header_lines.append(f"_Страниц:_ {page_count}")
        header_lines.append("")
        header_lines.append("---")
        header_lines.append("")

        body_parts: List[str] = []
        for i, txt in enumerate(pages_text, start=1):
            body_parts.append(f"## Страница {i}")
            body_parts.append("")
            body_parts.append(txt if txt else "_(пустая страница)_")
            body_parts.append("")

        content = "\n".join(header_lines) + "\n".join(body_parts)

        words = len(content.split())
        warn = ""
        if empty_pages:
            warn += f"\nПустых страниц: {empty_pages} из {page_count}."
        if words > 450_000:
            warn += f"\nВ файле ~{words:,} слов — близко к лимиту NotebookLM (500 000)."

        out = filedialog.asksaveasfilename(
            title="Сохранить для NotebookLM",
            defaultextension=".md",
            filetypes=[("Markdown", "*.md"), ("Текст", "*.txt")],
            initialfile=f"{base}_notebooklm.md",
        )
        if not out:
            self.status_var.set("Отменено")
            return
        try:
            with open(out, "w", encoding="utf-8") as f:
                f.write(content)
            self.status_var.set(f"Готово для NotebookLM: {os.path.basename(out)}")
            messagebox.showinfo(
                "Готово",
                f"Файл готов к загрузке в NotebookLM:\n{out}\n\n"
                f"Слов: ~{words:,}, страниц: {page_count}." + warn,
            )
        except Exception as exc:
            messagebox.showerror("Ошибка", f"Не удалось сохранить:\n{exc}")

    # ---- Извлечение изображений --------------------------------------------

    def extract_images(self) -> None:
        if self.doc is None:
            messagebox.showinfo("Извлечение изображений", "Сначала откройте PDF.")
            return

        out_dir = filedialog.askdirectory(title="Папка для извлечённых изображений")
        if not out_dir:
            return

        base = os.path.splitext(os.path.basename(self.pdf_path or "document"))[0]

        def worker() -> None:
            try:
                count = 0
                for page_index in range(self.doc.page_count):
                    page = self.doc.load_page(page_index)
                    for img_index, info in enumerate(page.get_images(full=True), start=1):
                        xref = info[0]
                        try:
                            data = self.doc.extract_image(xref)
                        except Exception:
                            continue
                        ext = data.get("ext", "png")
                        img_bytes = data["image"]
                        out_path = os.path.join(
                            out_dir, f"{base}_p{page_index + 1}_img{img_index}.{ext}"
                        )
                        with open(out_path, "wb") as f:
                            f.write(img_bytes)
                        count += 1
                self.after(0, lambda: messagebox.showinfo(
                    "Извлечение изображений",
                    f"Извлечено изображений: {count}\nПапка: {out_dir}",
                ))
                self.after(0, lambda: self.status_var.set(f"Извлечено изображений: {count}"))
            except Exception as exc:
                self.after(0, lambda: messagebox.showerror("Ошибка", f"Не удалось извлечь:\n{exc}"))

        threading.Thread(target=worker, daemon=True).start()

    # ---- Форматы страниц ---------------------------------------------------

    def show_page_formats(self) -> None:
        if self.doc is None:
            messagebox.showinfo("Форматы страниц", "Сначала откройте PDF.")
            return

        win = tk.Toplevel(self)
        win.title("Форматы страниц")
        win.geometry("520x420")
        win.transient(self)

        cols = ("page", "width", "height", "format")
        tree = ttk.Treeview(win, columns=cols, show="headings")
        tree.heading("page", text="Стр.")
        tree.heading("width", text="Ширина, мм")
        tree.heading("height", text="Высота, мм")
        tree.heading("format", text="Формат")
        tree.column("page", width=60, anchor="center")
        tree.column("width", width=110, anchor="center")
        tree.column("height", width=110, anchor="center")
        tree.column("format", width=220, anchor="w")

        vsb = ttk.Scrollbar(win, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        tree.pack(fill=tk.BOTH, expand=True)

        for i in range(self.doc.page_count):
            r = self.doc.load_page(i).rect
            w_mm = r.width * PT_TO_MM
            h_mm = r.height * PT_TO_MM
            fmt = detect_paper_format(r.width, r.height)
            tree.insert("", tk.END, values=(i + 1, f"{w_mm:.1f}", f"{h_mm:.1f}", fmt))

        ttk.Button(win, text="Закрыть", command=win.destroy).pack(pady=6)

    # ---- О программе -------------------------------------------------------

    def _about(self) -> None:
        messagebox.showinfo(
            "О программе",
            "Lumiere PDF\n\n"
            "Простой и лёгкий редактор PDF на Python (Tkinter + PyMuPDF).\n\n"
            "Возможности:\n"
            "  • Просмотр PDF (с миниатюрами)\n"
            "  • Объединение / разделение\n"
            "  • Определение формата листа\n"
            "  • Извлечение текста и изображений\n\n"
            "Запросы и донаты по СБП на 89279717400",
        )


def main() -> None:
    app = LumierePDF()
    app.mainloop()


if __name__ == "__main__":
    main()
