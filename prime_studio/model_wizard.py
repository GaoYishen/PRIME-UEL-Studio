"""Explicit, read-only registration of user-selected local model files."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

APP = Path(__file__).resolve().parents[1]
DIMENSIONS = ("2D T3", "2D Q4", "3D T4", "3D H8", "自定义")
FORTRAN_SUFFIXES = {".f", ".for", ".f90", ".f95", ".f03", ".f08", ".ftn"}


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _selected_file(parent: Path, value, label: str) -> Path:
    if not isinstance(value, (str, os.PathLike)) or not str(value).strip():
        raise ValueError(f"请明确选择{label}文件。")
    raw = Path(value)
    path = (raw if raw.is_absolute() else parent / raw).resolve()
    if path == parent or not path.is_relative_to(parent):
        raise ValueError(f"{label}必须位于所选模型父目录内：{value}")
    if not path.is_file():
        raise ValueError(f"{label}文件不存在或不是普通文件：{path}")
    return path


def register_local_files(label, parent_root, source, inp=None, auxiliary=None,
                         dimension="自定义", output_dir=None) -> Path:
    """Pin explicitly selected assets without copying or editing their contents.

    Registration grants file identity checks only. Parameter mappings, native
    initialization and execution remain unconfigured until separately declared.
    """
    if not isinstance(label, str) or not label.strip() or len(label.strip()) > 100:
        raise ValueError("模型名称需为1至100个字符。")
    if any(ord(character) < 32 for character in label):
        raise ValueError("模型名称不能包含换行或控制字符。")
    if dimension not in DIMENSIONS:
        raise ValueError("请选择已列出的单元类型，或选择“自定义”。")
    if not isinstance(parent_root, (str, os.PathLike)) or not str(parent_root).strip():
        raise ValueError("请选择模型父目录。")
    parent = Path(parent_root).resolve()
    if not parent.is_dir():
        raise ValueError("模型父目录不存在。")
    output = Path(output_dir if output_dir is not None else APP / "local").resolve()
    if output == parent or output.is_relative_to(parent):
        raise ValueError("登记目录不能位于原模型父目录内；原模型目录保持只读。")
    selected_source = _selected_file(parent, source, "Fortran源")
    if selected_source.suffix.casefold() not in FORTRAN_SUFFIXES:
        raise ValueError("请选择真实Fortran文件（.for、.f90等），不能用其他文件名代替。")
    selected_inp = _selected_file(parent, inp, "INP") if inp is not None else None
    if selected_inp is not None and selected_inp.suffix.casefold() != ".inp":
        raise ValueError("所选输入文件必须使用.inp扩展名。")
    if auxiliary is None:
        auxiliary = []
    if not isinstance(auxiliary, (list, tuple)):
        raise ValueError("外接文件应为明确选择的文件列表。")
    selected_auxiliary = [_selected_file(parent, value, "外接") for value in auxiliary]
    selected = [selected_source, *([selected_inp] if selected_inp else []), *selected_auxiliary]
    relative = [path.relative_to(parent).as_posix() for path in selected]
    if len({name.casefold() for name in relative}) != len(relative):
        raise ValueError("所选文件存在重复或Windows大小写冲突，请每个文件只登记一次。")
    fingerprints = {name: _digest(path) for name, path in zip(relative, selected)}
    identifier = datetime.now(timezone.utc).strftime("local_model_%Y%m%dT%H%M%SZ_") + uuid.uuid4().hex[:12]
    card = {
        "schema_version": 1,
        "id": identifier,
        "label": label.strip(),
        "description": "用户显式选择的本地模型文件；当前开放文件与版本核对，参数、初态和执行配方待登记。",
        "mode": "preview",
        "dimension": dimension,
        "parent_root": str(parent),
        "source": relative[0],
        "includes": [path.relative_to(parent).as_posix() for path in selected_auxiliary],
        "files": fingerprints,
        "parameters": {},
        "bindings": [],
        "initializer": {"kind": "unconfigured"},
        "execution": {"enabled": False, "reason": "尚未登记参数映射、初态与执行配方；当前可核对文件版本。"},
        "actions": {},
        "visualization": {"file": "", "x": "", "series": []},
        "scope": "Local file identity registration only; no inferred material, initial-state, compilation or solver qualification.",
    }
    if selected_inp is not None:
        card["inp"] = selected_inp.relative_to(parent).as_posix()
    if any(_digest(path) != fingerprints[name] for name, path in zip(relative, selected)):
        raise ValueError("登记期间原文件发生变化，请重新核对后登记。")
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"{identifier}.json"
    with target.open("x", encoding="utf-8") as stream:
        json.dump(card, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")
    return target


class ModelFilesWindow:
    """A file selection form; it never edits the selected model assets."""

    def __init__(self, parent, on_registered=None, output_dir=None) -> None:
        import tkinter as tk
        from tkinter import ttk
        self.on_registered = on_registered
        self.output_dir = output_dir
        self.auxiliary: list[str] = []
        self.root = tk.Toplevel(parent)
        self.root.title("登记本地UEL模型文件")
        self.root.geometry("850x610")
        self.root.minsize(730, 550)
        self.values = {key: tk.StringVar(value="") for key in ("label", "parent", "source", "inp")}
        self.dimension = tk.StringVar(value="自定义")
        ttk.Label(self.root, text="选择自己的FOR、INP与外接文件", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w", padx=20, pady=(18, 8))
        ttk.Label(self.root, text="登记只记录路径和文件指纹，不复制或修改原文件。参数映射、初态算法与执行步骤需要单独配置；不会根据文件名猜模型。", wraplength=790).pack(fill="x", padx=20, pady=(0, 15))
        form = ttk.Frame(self.root)
        form.pack(fill="x", padx=20)
        rows = (("模型名称", "label", None), ("模型父目录", "parent", self.choose_parent),
                ("Fortran源文件", "source", self.choose_source), ("INP输入（可选）", "inp", self.choose_inp))
        for index, (title, key, action) in enumerate(rows):
            ttk.Label(form, text=title).grid(row=index, column=0, sticky="w", pady=7)
            ttk.Entry(form, textvariable=self.values[key]).grid(row=index, column=1, sticky="ew", padx=10, pady=7)
            if action:
                ttk.Button(form, text="浏览…", command=action).grid(row=index, column=2, pady=7)
        ttk.Label(form, text="单元类型（登记信息）").grid(row=4, column=0, sticky="w", pady=7)
        ttk.Combobox(form, values=DIMENSIONS, textvariable=self.dimension, state="readonly").grid(row=4, column=1, sticky="ew", padx=10, pady=7)
        form.columnconfigure(1, weight=1)
        box = ttk.Frame(self.root)
        box.pack(fill="both", expand=True, padx=20, pady=(12, 0))
        ttk.Label(box, text="外接文件（可选，可多选）").pack(anchor="w")
        self.files = tk.Listbox(box, height=5, selectmode="extended")
        self.files.pack(fill="both", expand=True, pady=7)
        actions = ttk.Frame(box)
        actions.pack(fill="x")
        ttk.Button(actions, text="添加外接文件…", command=self.choose_auxiliary).pack(side="left")
        ttk.Button(actions, text="移出列表", command=self.remove_auxiliary).pack(side="left", padx=10)
        self.message = tk.StringVar(value="所选文件必须位于同一模型父目录内。单元类型只是登记信息，不自动改变物理。")
        ttk.Label(self.root, textvariable=self.message, wraplength=790).pack(fill="x", padx=20, pady=12)
        footer = ttk.Frame(self.root)
        footer.pack(fill="x", padx=20, pady=(0, 18))
        ttk.Button(footer, text="登记文件并返回", command=self.save).pack(side="left")
        ttk.Button(footer, text="取消", command=self.root.destroy).pack(side="right")

    def _start_directory(self) -> str:
        value = self.values["parent"].get().strip()
        return value if value and Path(value).is_dir() else str(Path.home())

    def choose_parent(self) -> None:
        from tkinter import filedialog
        path = filedialog.askdirectory(parent=self.root, title="选择包含全部模型文件的父目录", initialdir=self._start_directory())
        if path:
            self.values["parent"].set(path)

    def choose_source(self) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.root, title="选择Fortran源文件", initialdir=self._start_directory(),
                                          filetypes=[("Fortran", "*.for *.f *.f90 *.f95 *.f03 *.f08 *.ftn"), ("All files", "*.*")])
        if path:
            self.values["source"].set(path)
            if not self.values["parent"].get().strip():
                self.values["parent"].set(str(Path(path).parent))

    def choose_inp(self) -> None:
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.root, title="选择INP输入文件", initialdir=self._start_directory(), filetypes=[("Abaqus INP", "*.inp")])
        if path:
            self.values["inp"].set(path)

    def choose_auxiliary(self) -> None:
        from tkinter import filedialog
        paths = filedialog.askopenfilenames(parent=self.root, title="选择外接文件", initialdir=self._start_directory(), filetypes=[("All files", "*.*")])
        for path in paths:
            if path not in self.auxiliary:
                self.auxiliary.append(path)
                self.files.insert("end", path)

    def remove_auxiliary(self) -> None:
        for index in reversed(self.files.curselection()):
            self.auxiliary.pop(index)
            self.files.delete(index)

    def save(self) -> None:
        from tkinter import messagebox
        try:
            path = register_local_files(self.values["label"].get(), self.values["parent"].get(),
                                        self.values["source"].get(), self.values["inp"].get().strip() or None,
                                        self.auxiliary, self.dimension.get(), self.output_dir)
            self.message.set(f"已登记文件：{path.name}。计算相关映射仍需单独配置。")
            if self.on_registered:
                self.on_registered(path)
            self.root.destroy()
        except (OSError, ValueError, TypeError) as error:
            self.message.set(str(error))
            messagebox.showerror("本地模型登记", str(error), parent=self.root)
