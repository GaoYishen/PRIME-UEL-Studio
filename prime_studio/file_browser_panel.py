"""Visible registered-file tree and internal, read-only text previews."""
from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .file_browser import draft_index, model_index, read_text_preview, resolve_file


class FileBrowserPanel(ttk.Frame):
    """Browse a registered model or its explicitly selected copied draft."""

    MODEL_LABEL = "原模型（只读）"
    DRAFT_LABEL = "已选草稿（只读）"

    def __init__(self, parent, *, on_status=None) -> None:
        super().__init__(parent, padding=(0, 10, 0, 0))
        self.profile: dict = {}
        self.index: dict | None = None
        self.model: dict | None = None
        self.draft: dict | None = None
        self.draft_path: Path | None = None
        self.file_rows: dict[str, str] = {}
        self.busy = False
        self.on_status = on_status
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self.choice = tk.StringVar(value=self.MODEL_LABEL)
        controls = ttk.Frame(self)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(controls, text="查看范围").pack(side="left")
        self.scope_selector = ttk.Combobox(controls, textvariable=self.choice, state="disabled", width=21)
        self.scope_selector.pack(side="left", padx=8)
        self.scope_selector.bind("<<ComboboxSelected>>", self.scope_changed)
        self.load_button = ttk.Button(controls, text="打开已有草稿", command=self.choose_draft, state="disabled")
        self.load_button.pack(side="left", padx=(0, 8))
        self.refresh_button = ttk.Button(controls, text="刷新文件", command=self.refresh, state="disabled")
        self.refresh_button.pack(side="left")
        self.summary = tk.StringVar(value="选择模型后，源码、INP 和依赖文件会显示在这里。")
        self.summary_label = ttk.Label(self, textvariable=self.summary, style="Muted.TLabel", wraplength=880)
        self.summary_label.grid(row=1, column=0, sticky="ew", pady=(0, 7))
        frame = ttk.Frame(self)
        frame.grid(row=2, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(frame, columns=("role", "status", "size"), show="tree headings", height=5, selectmode="browse")
        for key, title, width in (("#0", "文件／目录", 340), ("role", "用途", 175), ("status", "文件状态", 120), ("size", "大小", 90)):
            self.tree.heading(key, text=title)
            self.tree.column(key, width=width, minwidth=75, stretch=key in {"#0", "role"})
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<<TreeviewSelect>>", self.selection_changed)
        self.tree.bind("<Double-1>", self.double_click)
        self.path_note = tk.StringVar(value="选中文件可查看完整路径；双击文本文件只读预览。")
        self.path_label = ttk.Label(self, textvariable=self.path_note, wraplength=880, style="Muted.TLabel")
        self.path_label.grid(row=3, column=0, sticky="ew", pady=(8, 6))
        actions = ttk.Frame(self)
        actions.grid(row=4, column=0, sticky="ew")
        self.preview_button = ttk.Button(actions, text="只读预览", command=self.preview_selected, state="disabled")
        self.preview_button.pack(side="left")
        self.folder_button = ttk.Button(actions, text="打开所在文件夹", command=self.reveal_selected, state="disabled")
        self.folder_button.pack(side="left", padx=8)
        self.copy_button = ttk.Button(actions, text="复制完整路径", command=self.copy_selected, state="disabled")
        self.copy_button.pack(side="left")
        self.bind("<Configure>", self.resize_labels)

    def resize_labels(self, event) -> None:
        width = max(200, event.width - 8)
        self.summary_label.configure(wraplength=width)
        self.path_label.configure(wraplength=width)

    @staticmethod
    def tree_path(item: dict) -> PurePosixPath | None:
        relative = item["relative"]
        path = PurePosixPath(relative)
        if (item.get("integrity") == "unsafe" or path.is_absolute() or PureWindowsPath(relative).drive
                or "\\" in relative or ".." in path.parts or path.as_posix() == "."):
            return None
        return path

    def set_profile(self, profile: dict) -> None:
        self.profile = profile
        self.draft = None
        self.draft_path = None
        self.choice.set(self.MODEL_LABEL)
        self.refresh()

    def refresh(self) -> None:
        if self.busy or not self.profile:
            return
        try:
            self.model = model_index(self.profile)
            if self.draft_path is not None:
                self.draft = draft_index(self.profile, self.draft_path)
            self._display_current()
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.summary.set("文件核对未完成：" + str(error))
            self._render(None)
        self.set_busy(False)

    def open_draft(self, directory: Path) -> dict:
        """Attach only a verified matching draft; never change an execution case."""
        if self.busy:
            raise ValueError("当前操作进行中，请完成后再切换草稿。")
        if not self.profile:
            raise ValueError("请先选择与草稿对应的已登记模型。")
        selected = draft_index(self.profile, Path(directory))
        self.draft = selected
        self.draft_path = Path(directory).resolve()
        self.choice.set(self.DRAFT_LABEL)
        self._display_current()
        self.set_busy(False)
        return selected

    def choose_draft(self) -> None:
        if self.busy:
            return
        selected = filedialog.askdirectory(parent=self.winfo_toplevel(), title="选择含 draft_manifest.json 的 A5 草稿目录")
        if selected:
            try:
                self.open_draft(Path(selected))
            except (OSError, ValueError, KeyError, TypeError) as error:
                self._error(error)

    def scope_changed(self, *_args) -> None:
        self._display_current()

    def _display_current(self) -> None:
        self.scope_selector.configure(values=[self.MODEL_LABEL, *([self.DRAFT_LABEL] if self.draft is not None else [])])
        selected = self.draft if self.choice.get() == self.DRAFT_LABEL else self.model
        self._render(selected)
        if selected:
            count = len(selected.get("items", []))
            problem_count = sum(item.get("integrity") != "matched" for item in selected.get("items", []))
            label = "已选草稿" if selected.get("kind") == "draft" else "原模型"
            note = f"{label} · {count} 个已登记文件 · {problem_count} 项缺失或不匹配。只读浏览，不启动计算。"
            self.summary.set(note)
            if self.on_status:
                self.on_status(note)

    def _render(self, index: dict | None) -> None:
        self.index = index
        self.file_rows = {}
        self.tree.delete(*self.tree.get_children())
        self.path_note.set("选中文件可查看完整路径；双击文本文件只读预览。")
        if index:
            items = sorted(index.get("items", []), key=lambda item: item["relative"].casefold())
            paths = [self.tree_path(item) for item in items]
            folders: set[str] = set()
            for path in paths:
                if path is None:
                    continue
                parent = path.parent
                while parent.as_posix() != ".":
                    folders.add(parent.as_posix())
                    if parent.parent == parent:
                        break
                    parent = parent.parent
            for name in sorted(folders, key=lambda value: (len(PurePosixPath(value).parts), value.casefold())):
                parent = PurePosixPath(name).parent.as_posix()
                self.tree.insert("" if parent == "." else "dir:" + parent, "end", iid="dir:" + name,
                                 text=PurePosixPath(name).name, open=True, values=("目录", "", ""))
            for number, item in enumerate(items):
                relative = item["relative"]
                path = paths[number]
                parent = path.parent.as_posix() if path is not None else "."
                identifier = "file:" + str(number)
                self.file_rows[identifier] = relative
                size = item.get("size")
                size_text = "—" if size is None else (f"{size / 1024:.1f} KB" if size >= 1024 else f"{size} B")
                self.tree.insert("" if parent == "." else "dir:" + parent, "end", iid=identifier,
                                 text=path.name if path is not None else relative,
                                 values=(item.get("role", "文件"), item.get("status", ""), size_text))
            preferred = self.profile.get("inp") or self.profile.get("source")
            selected = next((key for key, value in self.file_rows.items() if value == preferred), next(iter(self.file_rows), None))
            if selected is not None:
                self.tree.selection_set(selected)
                self.tree.focus(selected)
                self.tree.see(selected)
        self.selection_changed()

    def _selected(self) -> dict | None:
        selected = self.tree.selection()
        relative = self.file_rows.get(selected[0]) if selected else None
        return next((item for item in (self.index or {}).get("items", []) if item["relative"] == relative), None)

    def selection_changed(self, *_args) -> None:
        item = self._selected()
        enabled = item is not None and item.get("openable") is True and not self.busy
        self.preview_button.configure(state="normal" if enabled and item.get("kind") == "text" else "disabled")
        self.folder_button.configure(state="normal" if enabled else "disabled")
        self.copy_button.configure(state="normal" if enabled else "disabled")
        if item:
            extra = "；二进制文件不在本工具内执行。" if item.get("kind") == "binary" else ""
            self.path_note.set(str(item.get("path", "")) + "\n" + str(item.get("status", "")) + extra)

    def set_busy(self, busy: bool) -> None:
        self.busy = busy
        enabled = bool(self.profile) and not busy
        self.scope_selector.configure(state="readonly" if enabled else "disabled")
        self.load_button.configure(state="normal" if enabled else "disabled")
        self.refresh_button.configure(state="normal" if enabled else "disabled")
        self.selection_changed()

    def double_click(self, event) -> None:
        identifier = self.tree.identify_row(event.y)
        if identifier in self.file_rows:
            self.tree.selection_set(identifier)
            self.preview_selected()

    def preview_selected(self) -> None:
        item = self._selected()
        if not item or self.busy:
            return
        try:
            result = read_text_preview(self.index, item["relative"])
            if result.get("status") != "TEXT_PREVIEW":
                raise ValueError(result.get("reason", "该文件不能作为文本预览；可打开所在文件夹查看位置。"))
            root = tk.Toplevel(self.winfo_toplevel())
            root.title("只读预览 — " + Path(result["path"]).name)
            root.geometry("1080x730")
            root.minsize(740, 480)
            ttk.Label(root, text=result["path"], wraplength=1000).pack(fill="x", padx=14, pady=(14, 6))
            note = f"只读 · {result['encoding']} · {result['size']} 字节"
            if result.get("truncated"):
                note += " · 当前仅显示文件开头，完整内容未载入预览"
            ttk.Label(root, text=note, style="Muted.TLabel").pack(fill="x", padx=14, pady=(0, 8))
            frame = ttk.Frame(root)
            frame.pack(fill="both", expand=True, padx=14, pady=(0, 14))
            frame.columnconfigure(0, weight=1)
            frame.rowconfigure(0, weight=1)
            text = tk.Text(frame, wrap="none", font=("Consolas", 10), undo=False)
            yscroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
            xscroll = ttk.Scrollbar(frame, orient="horizontal", command=text.xview)
            text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
            text.grid(row=0, column=0, sticky="nsew")
            yscroll.grid(row=0, column=1, sticky="ns")
            xscroll.grid(row=1, column=0, sticky="ew")
            text.insert("1.0", result["text"])
            text.configure(state="disabled")
        except (OSError, ValueError, KeyError, TypeError) as error:
            self._error(error)

    def reveal_selected(self) -> None:
        item = self._selected()
        if not item or self.busy:
            return
        try:
            path = resolve_file(self.index, item["relative"])
            if os.name != "nt":
                raise ValueError("所在文件夹定位目前支持 Windows；可复制完整路径。")
            os.startfile(str(path.parent))
        except (OSError, ValueError, KeyError, TypeError) as error:
            self._error(error)

    def copy_selected(self) -> None:
        item = self._selected()
        if not item or self.busy:
            return
        try:
            path = resolve_file(self.index, item["relative"])
            self.clipboard_clear()
            self.clipboard_append(str(path))
        except (OSError, ValueError, KeyError, TypeError) as error:
            self._error(error)

    def _error(self, error: Exception) -> None:
        self.summary.set(str(error))
        messagebox.showerror("模型文件", str(error), parent=self.winfo_toplevel())
