"""Small GUI entry for the public 2D T3 reaction-edge generator."""
from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .interface_generator import generate_h8_reaction_faces, generate_t3_reaction_edges


def _labels(value: str) -> list[int]:
    tokens = value.replace("，", ",").replace(";", ",").split(",")
    labels = [int(token.strip()) for token in tokens if token.strip()]
    if not labels:
        raise ValueError("每相至少输入一个单元标签，多个标签用逗号分隔")
    return list(dict.fromkeys(labels))


class InterfaceGeneratorWindow:
    def __init__(self, parent, workspace: Path | None = None, cae_context: dict | None = None) -> None:
        self.root = tk.Toplevel(parent)
        self.root.title("PRIME 2D T3 反应边生成")
        self.root.geometry("760x520")
        self.root.minsize(680, 460)
        self.workspace = Path(workspace or Path.home() / "PRIME_UEL_Projects")
        self.inp = tk.StringVar()
        self.solid_labels = tk.StringVar()
        self.liquid_labels = tk.StringVar()
        self.element_type = tk.StringVar(value="U2")
        self.elset = tk.StringVar(value="PRIME_REACTION_EDGE")
        self.note = tk.StringVar(value="先输入固相和液相单元标签，只生成两相之间的公共边。")
        self._prefill_selection(cae_context or {})
        self._layout()

    def _prefill_selection(self, context: dict) -> None:
        input_file = context.get("input_file")
        if input_file:
            self.inp.set(str(input_file))
        solid = context.get("solid_elements", [])
        liquid = context.get("liquid_elements", [])
        if solid or liquid:
            self.solid_labels.set(", ".join(str(item.get("label")) for item in solid))
            self.liquid_labels.set(", ".join(str(item.get("label")) for item in liquid))
            self.note.set(f"已带入固相 {len(solid)} 个、液相 {len(liquid)} 个单元，只生成跨相公共边。")

    def _layout(self) -> None:
        ttk.Label(self.root, text="二维 T3 反应边生成", font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w", padx=20, pady=(18, 8))
        ttk.Label(self.root, text="生成拓扑和映射清单。材料属性、DOF 合同和求解由授权 PRIME 适配器补充。", wraplength=700).pack(fill="x", padx=20, pady=(0, 14))
        form = ttk.Frame(self.root)
        form.pack(fill="x", padx=20)
        form.columnconfigure(1, weight=1)
        rows = (("输入 INP", self.inp, self.choose_inp), ("固相单元标签", self.solid_labels, None),
                ("液相单元标签", self.liquid_labels, None),
                ("界面 UEL 类型", self.element_type, None), ("界面 ELSET", self.elset, None))
        for row, (label, variable, action) in enumerate(rows):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=7)
            ttk.Entry(form, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=10, pady=7)
            if action:
                ttk.Button(form, text="浏览…", command=action).grid(row=row, column=2, pady=7)
        ttk.Label(self.root, textvariable=self.note, wraplength=700).pack(fill="x", padx=20, pady=14)
        self.output = tk.Text(self.root, height=12, wrap="word", state="disabled")
        self.output.pack(fill="both", expand=True, padx=20, pady=(0, 12))
        footer = ttk.Frame(self.root)
        footer.pack(fill="x", padx=20, pady=(0, 18))
        ttk.Button(footer, text="生成新副本", command=self.generate).pack(side="left")
        ttk.Button(footer, text="关闭", command=self.root.destroy).pack(side="right")

    def choose_inp(self) -> None:
        path = filedialog.askopenfilename(parent=self.root, filetypes=[("Abaqus INP", "*.inp"), ("All files", "*.*")])
        if path:
            self.inp.set(path)

    def generate(self) -> None:
        try:
            solid = _labels(self.solid_labels.get())
            liquid = _labels(self.liquid_labels.get())
            manifest = generate_t3_reaction_edges(self.inp.get(), self.workspace / "interface_t3_reaction_edges", solid,
                                                 interface_type=self.element_type.get().strip(), elset=self.elset.get().strip(),
                                                 opposite_elements=liquid)
        except (OSError, ValueError, TypeError) as error:
            self.note.set(str(error))
            messagebox.showerror("反应边生成", str(error), parent=self.root)
            return
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("1.0", "生成完成，尚未补充私有 UEL 属性卡。\n\n" + str(manifest))
        self.output.configure(state="disabled")
        count = manifest.get("interface_count", 1)
        self.note.set(f"已生成 {count} 个反应边、{manifest['generated_inp']} 和 interface_manifest.json。请先做 datacheck。")


class H8InterfaceGeneratorWindow:
    def __init__(self, parent, workspace: Path | None = None, cae_context: dict | None = None) -> None:
        self.root = tk.Toplevel(parent)
        self.root.title("PRIME 3D H8 反应面生成")
        self.root.geometry("760x520")
        self.root.minsize(680, 460)
        self.workspace = Path(workspace or Path.home() / "PRIME_UEL_Projects")
        self.inp = tk.StringVar()
        self.solid_labels = tk.StringVar()
        self.liquid_labels = tk.StringVar()
        self.element_type = tk.StringVar(value="U303")
        self.elset = tk.StringVar(value="PRIME_REACTION_FACE")
        self.note = tk.StringVar(value="先输入固相和液相单元标签，只生成两相之间的公共面。")
        self._prefill_selection(cae_context or {})
        self._layout()

    def _prefill_selection(self, context: dict) -> None:
        input_file = context.get("input_file")
        if input_file:
            self.inp.set(str(input_file))
        solid = context.get("solid_elements", [])
        liquid = context.get("liquid_elements", [])
        if solid or liquid:
            self.solid_labels.set(", ".join(str(item.get("label")) for item in solid))
            self.liquid_labels.set(", ".join(str(item.get("label")) for item in liquid))
            self.note.set(f"已带入固相 {len(solid)} 个、液相 {len(liquid)} 个单元，只生成跨相公共面。")

    def _layout(self) -> None:
        ttk.Label(self.root, text="三维 H8 反应面生成", font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w", padx=20, pady=(18, 8))
        ttk.Label(self.root, text="默认连接为四个界面面节点加左侧父 H8 的四个非面节点。材料属性和 DOF 合同由私有适配器补充。", wraplength=700).pack(fill="x", padx=20, pady=(0, 14))
        form = ttk.Frame(self.root)
        form.pack(fill="x", padx=20)
        form.columnconfigure(1, weight=1)
        rows = (("输入 INP", self.inp, self.choose_inp), ("固相单元标签", self.solid_labels, None),
                ("液相单元标签", self.liquid_labels, None),
                ("界面 UEL 类型", self.element_type, None), ("界面 ELSET", self.elset, None))
        for row, (label, variable, action) in enumerate(rows):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=7)
            ttk.Entry(form, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=10, pady=7)
            if action:
                ttk.Button(form, text="浏览…", command=action).grid(row=row, column=2, pady=7)
        ttk.Label(self.root, textvariable=self.note, wraplength=700).pack(fill="x", padx=20, pady=14)
        self.output = tk.Text(self.root, height=12, wrap="word", state="disabled")
        self.output.pack(fill="both", expand=True, padx=20, pady=(0, 12))
        footer = ttk.Frame(self.root)
        footer.pack(fill="x", padx=20, pady=(0, 18))
        ttk.Button(footer, text="生成新副本", command=self.generate).pack(side="left")
        ttk.Button(footer, text="关闭", command=self.root.destroy).pack(side="right")

    def choose_inp(self) -> None:
        path = filedialog.askopenfilename(parent=self.root, filetypes=[("Abaqus INP", "*.inp"), ("All files", "*.*")])
        if path:
            self.inp.set(path)

    def generate(self) -> None:
        try:
            solid = _labels(self.solid_labels.get())
            liquid = _labels(self.liquid_labels.get())
            manifest = generate_h8_reaction_faces(self.inp.get(), self.workspace / "interface_h8_reaction_faces", solid,
                                                 interface_type=self.element_type.get().strip(), elset=self.elset.get().strip(),
                                                 opposite_elements=liquid)
        except (OSError, ValueError, TypeError) as error:
            self.note.set(str(error))
            messagebox.showerror("反应面生成", str(error), parent=self.root)
            return
        self.output.configure(state="normal")
        self.output.delete("1.0", "end")
        self.output.insert("1.0", "生成完成，尚未补充私有 UEL 属性卡。\n\n" + str(manifest))
        self.output.configure(state="disabled")
        count = manifest.get("interface_count", 1)
        self.note.set(f"已生成 {count} 个反应面、{manifest['generated_inp']} 和 interface_manifest.json。请先做 datacheck。")
