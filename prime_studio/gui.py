"""Windows desktop forms for registered UEL parameters, initial states and jobs."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import __version__
from .core import available_actions, load_profile, prepare_action_case, read_history, registry_status, validate_parameters
try:
    from .initial import calculate_initial
except ImportError:  # public export omits private material initializers
    from .public_initial import calculate_initial
from .model_wizard import ModelFilesWindow
from .file_browser_panel import FileBrowserPanel

APP = Path(__file__).resolve().parents[1]
INK, MUTED, TEAL = "#19334A", "#60778A", "#087E8B"
BG, WHITE, LINE = "#F1F5F8", "#FFFFFF", "#DCE5EC"
FONT = "Microsoft YaHei UI"
ACTION_LABELS = {"all": "完整注册流程", "compile": "只编译", "run": "只运行"}
PAGE_NAMES = ("模型与文件", "参数配置", "初始状态", "编译与检查", "计算任务", "结果查看")


def present_startup_window(root) -> None:
    """Place this newly opened application on screen and request focus once."""
    root.update_idletasks()
    screen_width, screen_height = root.winfo_screenwidth(), root.winfo_screenheight()
    width = min(1280, max(1, screen_width - 64))
    height = min(850, max(1, screen_height - 90))
    root.minsize(min(940, width), min(620, height))
    root.geometry(f"{width}x{height}+{max(0, (screen_width - width) // 2)}+{max(0, (screen_height - height) // 2)}")
    root.deiconify()
    root.lift()
    root.focus_force()


class Studio:
    def __init__(self, root: tk.Tk, profile: Path | None = None, cae_context: Path | None = None) -> None:
        self.root = root
        self.root.title(f"PRIME UEL Studio {__version__} — 参数与输入配置")
        self.root.geometry("1280x850")
        self.root.minsize(1080, 730)
        self.root.configure(bg=BG)
        self.profile: dict = {}
        self.profile_paths: list[Path] = []
        self.initial: dict | None = None
        self.case: Path | None = None
        self.result: dict | None = None
        self.prepared_action: str | None = None
        self.reuse_run: Path | None = None
        self.registration_ok = False
        self.started_run_ids: set[str] = set()
        self.variables: dict[str, tk.StringVar] = {}
        self.entries: list[ttk.Entry] = []
        self.history: dict | None = None
        self.busy = False
        self.process: subprocess.Popen | None = None
        self.process_stream = None
        self.process_log: Path | None = None
        self.pending = queue.Queue()
        self.building = False
        self.closed = False
        self.cae_context: dict = {}
        self.a5_window = None
        self.a5_runtime_window = None
        self.a5_operating_window = None
        self.odb_path: Path | None = None
        self.workspace = Path.home() / "PRIME_UEL_Projects"
        self._style()
        self._layout()
        self.refresh_profiles(profile)
        self.load_cae_context(cae_context)
        self.show_page(self.default_page())
        if self.cae_context.get("interface_selection"):
            self.root.after(350, self.open_selected_interface_generator)
        self.root.after(200, self.poll)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def load_cae_context(self, path: Path | None) -> None:
        if path is None or not path.is_file():
            return
        try:
            self.cae_context = json.loads(path.read_text(encoding="utf-8"))
            model = self.cae_context.get("current_model", "未识别")
            self.subtitle.set(f"来自 Abaqus/CAE：{model} · 已读取当前模型上下文，可继续选择 PRIME 配置卡和操作步骤。")
        except (OSError, json.JSONDecodeError):
            self.cae_context = {}

    def _style(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("TFrame", background=WHITE)
        style.configure("TLabel", background=WHITE, foreground=INK, font=(FONT, 10))
        style.configure("Muted.TLabel", foreground=MUTED, font=(FONT, 9))
        style.configure("Title.TLabel", font=(FONT, 17, "bold"))
        style.configure("Section.TLabel", font=(FONT, 11, "bold"))
        style.configure("TButton", font=(FONT, 10), padding=(12, 7), background="#EAF0F4", borderwidth=0)
        style.configure("Accent.TButton", background=TEAL, foreground="white")
        style.map("Accent.TButton", background=[("active", "#096B76"), ("disabled", "#B5C4CD")])
        style.configure("TEntry", padding=6, font=(FONT, 10))
        style.configure("TCombobox", padding=5, font=(FONT, 10))
        style.configure("Treeview", rowheight=29, font=(FONT, 10), background=WHITE, fieldbackground=WHITE)
        style.configure("Treeview.Heading", font=(FONT, 9, "bold"), background="#EAF0F4")

    def _layout(self) -> None:
        rail = tk.Frame(self.root, bg="#142B43", width=204)
        rail.pack(side="left", fill="y")
        rail.pack_propagate(False)
        tk.Label(rail, text="PRIME", fg="white", bg="#142B43", font=("Segoe UI", 29, "bold")).pack(anchor="w", padx=23, pady=(29, 0))
        tk.Label(rail, text="UEL STUDIO", fg="#81D3D3", bg="#142B43", font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=25, pady=(0, 34))
        self.nav = {}
        for name in PAGE_NAMES:
            button = tk.Button(rail, text="Abaqus 结果" if name == "结果查看" else name, command=lambda n=name: self.show_page(n), anchor="w",
                               bg="#142B43", fg="#C9D8E3", activebackground="#22435E", activeforeground="white",
                               relief="flat", bd=0, font=(FONT, 11), padx=25, pady=14, cursor="hand2")
            button.pack(fill="x", pady=3)
            self.nav[name] = button
        tk.Label(rail, text=f"桌面版 {__version__}\n计算在本机执行", justify="left", fg="#8EA8BC", bg="#142B43", font=(FONT, 9)).pack(side="bottom", anchor="w", padx=25, pady=24)
        body = tk.Frame(self.root, bg=BG)
        body.pack(side="left", fill="both", expand=True)
        header = ttk.Frame(body, padding=(25, 19))
        header.pack(fill="x")
        ttk.Label(header, text="参数调整、初始条件与输入文件", style="Title.TLabel").pack(side="left")
        ttk.Button(header, text="打开参数项目", command=self.open_project).pack(side="right", padx=4)
        ttk.Button(header, text="保存参数项目", command=self.save_project).pack(side="right", padx=4)
        self.subtitle = tk.StringVar(value="选择模型 → 调整参数／初态 → 预览文件改动 → 生成新副本；各步骤可单独使用，后处理交给 Abaqus。")
        tk.Label(body, textvariable=self.subtitle, bg=BG, fg=MUTED, font=(FONT, 10), anchor="w").pack(fill="x", padx=26, pady=(15, 10))
        self.host = tk.Frame(body, bg=BG)
        self.host.pack(fill="both", expand=True, padx=24, pady=(0, 12))
        self.pages = {name: ttk.Frame(self.host, padding=20) for name in self.nav}
        self._model_page()
        self._parameters_page()
        self._initial_page()
        self._compile_page()
        self._job_page()
        self._results_page()
        footer = ttk.Frame(body, padding=(24, 8))
        footer.pack(fill="x")
        self.status = tk.StringVar(value="就绪")
        ttk.Label(footer, textvariable=self.status, style="Muted.TLabel").pack(side="left", fill="x", expand=True)
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=130)
        self.progress.pack(side="right")

    def _model_page(self) -> None:
        page = self.pages["模型与文件"]
        top = ttk.Frame(page)
        top.pack(fill="x")
        ttk.Label(top, text="选择已登记模型或自己的文件", style="Section.TLabel").pack(side="left")
        ttk.Button(top, text="导入配置卡", command=self.import_profile).pack(side="right")
        self.model_name = tk.StringVar()
        self.selector = ttk.Combobox(page, textvariable=self.model_name, state="readonly")
        self.selector.pack(fill="x", pady=(12, 8))
        self.selector.bind("<<ComboboxSelected>>", lambda _: self.select_profile(self.profile_paths[self.selector.current()]))
        self.model_description = tk.StringVar()
        ttk.Label(page, textvariable=self.model_description, style="Muted.TLabel", wraplength=920).pack(fill="x", pady=(0, 8))
        row = ttk.Frame(page)
        row.pack(fill="x", pady=(0, 8))
        self.files_button = ttk.Button(row, text="选择自己的 FOR / INP / 外接文件", style="Accent.TButton", command=self.register_files)
        self.files_button.pack(side="left")
        self.model_check_button = ttk.Button(row, text="核对文件版本", command=self.update_registry)
        self.model_check_button.pack(side="left", padx=10)
        ttk.Button(row, text="生成 T3 反应边", command=self.open_interface_generator).pack(side="left", padx=10)
        ttk.Button(row, text="生成 H8 反应面", command=self.open_h8_interface_generator).pack(side="left", padx=10)
        battery = ttk.Frame(page)
        battery.pack(fill="x", pady=(0, 8))
        self.a5_register_button = ttk.Button(battery, text="登记真实二维 A5 电池", command=self.register_a5_model)
        self.a5_register_button.pack(side="left")
        ttk.Label(battery, text="读取冻结 A5 参数和文件；只提供改动预览及新草稿生成。", style="Muted.TLabel").pack(side="left", padx=12)
        self.model_notebook = ttk.Notebook(page)
        self.model_notebook.pack(fill="both", expand=True)
        self.file_browser = FileBrowserPanel(self.model_notebook, on_status=self.browser_status)
        details = ttk.Frame(self.model_notebook)
        self.model_notebook.add(self.file_browser, text="文件树")
        self.model_notebook.add(details, text="版本详情")
        self.registry_text = tk.Text(details, bg="#F6F9FB", fg=INK, font=("Consolas", 10), wrap="word", relief="flat", padx=14, pady=12)
        self.registry_text.pack(fill="both", expand=True)
        self.model_notebook.select(self.file_browser)

    def _parameters_page(self) -> None:
        page = self.pages["参数配置"]
        ttk.Label(page, text="参数名称、单位与取值范围", style="Section.TLabel").pack(anchor="w")
        self.parameter_note = tk.StringVar(value="选择模型后，在这里编辑该配置卡明确登记的参数。")
        ttk.Label(page, textvariable=self.parameter_note, style="Muted.TLabel", wraplength=900).pack(fill="x", pady=12)
        self.form = ttk.Frame(page)
        self.form.pack(fill="both", expand=True, anchor="nw")
        self.form.columnconfigure(0, weight=1)
        self.form.columnconfigure(1, weight=1)
        row = ttk.Frame(page)
        row.pack(fill="x", pady=(18, 0))
        self.a5_preview_button = ttk.Button(row, text="预览参数改动／生成新副本", command=self.preview_a5_parameters, style="Accent.TButton", state="disabled")
        self.a5_preview_button.pack(side="left")
        self.a5_operating_button = ttk.Button(row, text="工况与初态", command=self.open_a5_operating, style="Accent.TButton", state="disabled")
        self.a5_operating_button.pack(side="left", padx=8)
        ttk.Button(row, text="打开初态工具", command=lambda: self.show_page("初始状态")).pack(side="left", padx=10)
        ttk.Button(row, text="保存参数项目", command=self.save_project).pack(side="left")
        ttk.Button(row, text="选择输出位置", command=self.choose_workspace).pack(side="right")
        self.output_label = tk.StringVar(value=str(self.workspace))
        ttk.Label(page, textvariable=self.output_label, style="Muted.TLabel", wraplength=900).pack(fill="x", pady=(10, 0))

    def _initial_page(self) -> None:
        page = self.pages["初始状态"]
        ttk.Label(page, text="独立计算与导出初始状态", style="Section.TLabel").pack(anchor="w")
        ttk.Label(page, text="使用参数页的当前数值。这里只计算初态，不会自动编译或提交计算。", style="Muted.TLabel", wraplength=900).pack(fill="x", pady=12)
        table = ttk.Frame(page)
        table.pack(fill="both", expand=True)
        self.initial_tree = ttk.Treeview(table, columns=("quantity", "value", "unit"), show="headings", height=13)
        for key, title, width in (("quantity", "计算量", 290), ("value", "数值", 220), ("unit", "单位", 180)):
            self.initial_tree.heading(key, text=title)
            self.initial_tree.column(key, width=width, minwidth=65)
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.initial_tree.yview)
        self.initial_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.initial_tree.pack(side="left", fill="both", expand=True)
        self.initial_note = tk.StringVar(value="修改输入后，点击“计算初始状态”。")
        ttk.Label(page, textvariable=self.initial_note, style="Muted.TLabel", wraplength=900).pack(fill="x", pady=(10, 0))
        bottom = ttk.Frame(page)
        bottom.pack(fill="x", pady=(18, 0))
        self.initial_button = ttk.Button(bottom, text="计算初始状态", style="Accent.TButton", command=self.compute_initial)
        self.initial_button.pack(side="left")
        self.export_initial_button = ttk.Button(bottom, text="导出初态 JSON", command=self.export_initial)
        self.export_initial_button.pack(side="left", padx=10)
        ttk.Button(bottom, text="返回参数配置", command=lambda: self.show_page("参数配置")).pack(side="right")

    def _compile_page(self) -> None:
        page = self.pages["编译与检查"]
        ttk.Label(page, text="文件检查与独立编译", style="Section.TLabel").pack(anchor="w")
        ttk.Label(page, text="可以先核对所选文件版本，也可以只执行已登记的编译步骤。只编译不要求先计算初态，不会自动运行求解。", style="Muted.TLabel", wraplength=900).pack(fill="x", pady=12)
        row = ttk.Frame(page)
        row.pack(fill="x", pady=(0, 14))
        self.check_button = ttk.Button(row, text="核对文件版本", command=self.update_registry)
        self.check_button.pack(side="left")
        self.compile_button = ttk.Button(row, text="只编译", style="Accent.TButton", command=lambda: self.prepare("compile", auto_start=True))
        self.compile_button.pack(side="left", padx=10)
        self.a5_runtime_check_button = ttk.Button(row, text="A5 Abaqus 联通检查", command=self.open_a5_runtime, state="disabled")
        self.a5_runtime_check_button.pack(side="left")
        ttk.Button(row, text="选择输出位置", command=self.choose_workspace).pack(side="right")
        self.compile_note = tk.StringVar(value="当前模型尚未登记编译操作。")
        ttk.Label(page, textvariable=self.compile_note, style="Muted.TLabel", wraplength=900).pack(fill="x", pady=(0, 12))
        self.check_text = tk.Text(page, bg="#F6F9FB", fg=INK, font=("Consolas", 10), wrap="word", relief="flat", padx=14, pady=12)
        self.check_text.pack(fill="both", expand=True)

    def _job_page(self) -> None:
        page = self.pages["计算任务"]
        ttk.Label(page, text="选择要执行的操作", style="Section.TLabel").pack(anchor="w")
        choices = ttk.Frame(page)
        choices.pack(fill="x", pady=(12, 8))
        ttk.Label(choices, text="本次操作").pack(side="left")
        self.action_choice = tk.StringVar()
        self.action_selector = ttk.Combobox(choices, textvariable=self.action_choice, state="readonly", width=22)
        self.action_selector.pack(side="left", padx=10)
        self.action_choice.trace_add("write", self.action_changed)
        self.reuse_button = ttk.Button(choices, text="选择已有编译目录", command=self.choose_compile_run)
        self.reuse_button.pack(side="left", padx=5)
        self.a5_runtime_run_button = ttk.Button(choices, text="A5 短程联通计算", command=self.open_a5_runtime, state="disabled")
        self.a5_runtime_run_button.pack(side="right")
        self.reuse_label = tk.StringVar(value="尚未选择编译结果。Python参考运行不需要此项。")
        ttk.Label(page, textvariable=self.reuse_label, style="Muted.TLabel", wraplength=900).pack(fill="x", pady=(0, 8))
        self.job_summary = tk.StringVar(value="按所需操作生成新副本。只运行不会自动补做编译；缺少匹配产物时会提示。")
        ttk.Label(page, textvariable=self.job_summary, wraplength=900).pack(fill="x", pady=(12, 14))
        row = ttk.Frame(page)
        row.pack(fill="x", pady=(0, 14))
        self.prepare_button = ttk.Button(row, text="生成本次操作副本", command=self.prepare)
        self.prepare_button.pack(side="left", padx=(0, 8))
        self.run_button = ttk.Button(row, text="开始计算", style="Accent.TButton", command=self.start)
        self.run_button.pack(side="left")
        self.stop_button = ttk.Button(row, text="停止本次任务", command=self.stop)
        self.stop_button.pack(side="left", padx=8)
        ttk.Button(row, text="打开算例目录", command=self.open_case_folder).pack(side="left", padx=5)
        ttk.Button(row, text="查看报告", command=self.open_report).pack(side="left", padx=5)
        self.log = tk.Text(page, bg="#102638", fg="#D3E6ED", insertbackground="white", font=("Consolas", 10), relief="flat", padx=14, pady=12, wrap="word")
        self.log.pack(fill="both", expand=True)
        self.log.insert("end", "等待开始计算。\n")

    def _results_page(self) -> None:
        page = self.pages["结果查看"]
        self.abaqus_result_panel = ttk.Frame(page)
        ttk.Label(self.abaqus_result_panel, text="直接使用 Abaqus 查看结果", style="Section.TLabel").pack(anchor="w")
        ttk.Label(self.abaqus_result_panel, text="本工具负责参数、初态、输入与任务准备。计算后，用 Abaqus/Viewer 打开 ODB 查看已有输出。自定义 UEL 场量仍需相应的 ODB 输出映射。", style="Muted.TLabel", wraplength=900).pack(fill="x", pady=14)
        row = ttk.Frame(self.abaqus_result_panel)
        row.pack(fill="x")
        ttk.Button(row, text="选择已有 ODB", command=self.choose_odb).pack(side="left")
        ttk.Button(row, text="用 Abaqus 打开 ODB", command=self.open_abaqus_results, style="Accent.TButton").pack(side="left", padx=10)
        self.odb_note = tk.StringVar(value="可选择已有 ODB；也可打开 A5 任务窗口对应的结果。此操作不提交计算。")
        ttk.Label(self.abaqus_result_panel, textvariable=self.odb_note, wraplength=900).pack(fill="x", pady=16)
        self.csv_result_panel = ttk.Frame(page)
        row = ttk.Frame(self.csv_result_panel)
        row.pack(fill="x")
        ttk.Label(row, text="结果曲线", style="Section.TLabel").pack(side="left")
        ttk.Button(row, text="导入 CSV", command=self.import_csv).pack(side="right")
        self.series = tk.StringVar()
        self.series_selector = ttk.Combobox(row, textvariable=self.series, state="readonly", width=25)
        self.series_selector.pack(side="right", padx=12)
        self.series_selector.bind("<<ComboboxSelected>>", lambda _: self.draw_chart())
        self.result_note = tk.StringVar(value="只显示已生成或导入的数据。可选择力、位移、能量或注册的结果通道。")
        ttk.Label(self.csv_result_panel, textvariable=self.result_note, style="Muted.TLabel", wraplength=900).pack(fill="x", pady=12)
        self.canvas = tk.Canvas(self.csv_result_panel, bg=WHITE, highlightthickness=1, highlightbackground=LINE)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda _: self.draw_chart())

    def show_page(self, name: str) -> None:
        for page in self.pages.values():
            page.pack_forget()
        self.pages[name].pack(fill="both", expand=True)
        for title, button in self.nav.items():
            button.configure(bg="#24435A" if title == name else "#142B43", fg="white" if title == name else "#C9D8E3")
        if name == "结果查看":
            self.configure_result_page()
            if not self.is_a5_draft():
                self.root.after(60, self.draw_chart)

    def default_page(self) -> str:
        return "参数配置" if self.is_a5_draft() else "模型与文件"

    def configure_result_page(self) -> None:
        if not hasattr(self, "abaqus_result_panel"):
            return
        self.abaqus_result_panel.pack_forget()
        self.csv_result_panel.pack_forget()
        panel = self.abaqus_result_panel if self.is_a5_draft() else self.csv_result_panel
        panel.pack(fill="both", expand=True)
        self.nav["结果查看"].configure(text="Abaqus 结果" if self.is_a5_draft() else "示例曲线")

    def choose_odb(self) -> None:
        path = filedialog.askopenfilename(parent=self.root, title="选择 Abaqus 结果数据库", filetypes=[("Abaqus ODB", "*.odb")])
        if not path:
            return
        try:
            from .abaqus_results import inspect_odb
            result = inspect_odb(Path(path))
            if not result["ready"]:
                raise ValueError("；".join(result["issues"]))
            self.odb_path = Path(result["path"])
            self.odb_note.set("已选择：" + str(self.odb_path))
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.error(error)

    def open_abaqus_results(self) -> None:
        try:
            from .abaqus_results import open_in_abaqus
            path = getattr(self, "odb_path", None)
            runtime = getattr(self, "a5_runtime_window", None)
            if path is None and runtime is not None:
                path = runtime.result_odb()
            if path is None:
                self.choose_odb()
                path = getattr(self, "odb_path", None)
            if path is None:
                return
            result = open_in_abaqus(Path(path))
            self.odb_note.set("已请求 Abaqus/Viewer 打开：" + str(result["odb"]))
            self.status.set("结果由 Abaqus/Viewer 查看；本次没有提交计算。")
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.error(error)

    def refresh_profiles(self, preferred: Path | None = None) -> None:
        paths = sorted((APP / "local").glob("*.json")) + sorted((APP / "profiles").glob("*.json"))
        if preferred and preferred.resolve() not in [path.resolve() for path in paths]:
            paths.insert(0, preferred)
        labels, valid = [], []
        for path in paths:
            try:
                profile = load_profile(path)
                labels.append(profile["label"])
                valid.append(path)
            except (OSError, ValueError, KeyError) as exc:
                self.status.set(f"跳过无效配置卡 {path.name}：{exc}")
        self.profile_paths = valid
        self.selector.configure(values=labels)
        if valid:
            index = next((i for i, path in enumerate(valid) if preferred and path.resolve() == preferred.resolve()), 0)
            self.selector.current(index)
            self.select_profile(valid[index])
        else:
            self.model_description.set("尚无模型配置卡。可导入本地登记文件，或检查 profiles 目录。")

    def select_profile(self, path: Path) -> None:
        if self.busy:
            return
        try:
            profile = load_profile(path)
            current_a5 = getattr(self, "a5_window", None)
            if current_a5 is not None:
                current_a5.close()
            current_runtime = getattr(self, "a5_runtime_window", None)
            if current_runtime is not None and not current_runtime.close():
                return
            operating = getattr(self, 'a5_operating_window', None)
            if operating is not None and operating.root.winfo_exists():
                operating.close()
            self.a5_operating_window = None
            self.profile = profile
            self.odb_path = None
            self.initial = None
            self.case = None
            self.result = None
            self.history = None
            self.prepared_action = None
            self.reuse_run = None
            self.registration_ok = False
            browser = getattr(self, "file_browser", None)
            if browser is not None:
                browser.set_profile(profile)
            self.model_description.set(profile.get("description", ""))
            self.building = True
            for child in self.form.winfo_children():
                child.destroy()
            self.variables, self.entries = {}, []
            parameters = profile.get("parameters", {})
            for row, (key, spec) in enumerate(parameters.items()):
                box = ttk.Frame(self.form)
                box.grid(row=row // 2 if self.is_a5_draft() else row, column=row % 2 if self.is_a5_draft() else 0,
                         columnspan=1 if self.is_a5_draft() else 2, sticky="ew", padx=(0, 18) if self.is_a5_draft() and row % 2 == 0 else 0, pady=(0, 12))
                box.columnconfigure(0, weight=1)
                ttk.Label(box, text=spec.get("label", key)).grid(row=0, column=0, sticky="w", pady=(0, 4))
                ttk.Label(box, text=spec.get("unit", ""), style="Muted.TLabel").grid(row=0, column=1, sticky="e")
                value = tk.StringVar(value=str(spec["default"]))
                entry = ttk.Entry(box, textvariable=value, width=25)
                entry.a5_source_fixed = self.is_a5_draft() and key in {
                    'graphite_bulk_G_MPa', 'graphite_bulk_K_MPa', 'ncm_bulk_G_MPa', 'ncm_bulk_K_MPa'}
                entry.grid(row=1, column=0, columnspan=2, sticky="ew")
                value.trace_add("write", self.parameters_changed)
                self.variables[key] = value
                self.entries.append(entry)
            self.form.columnconfigure(0, weight=1)
            self.form.columnconfigure(1, weight=1)
            if not parameters:
                ttk.Label(self.form, text="当前文件登记尚未提供具名参数映射。\n可以先核对文件版本，或导入已经完成映射的模型配置卡。", style="Muted.TLabel", wraplength=850).grid(row=0, column=0, columnspan=2, sticky="nw", pady=10)
            self.parameter_note.set("参数只会写入新的操作副本，不修改原文件。" if parameters else "仅登记文件不会自动猜测参数含义；计算选项以该模型已接通的能力为准。")
            if self.is_a5_draft():
                self.parameter_note.set("点击“工况与初态”设置倍率、初温、占位率、浓度和时间。下方可改扩散系数、SiOₓ 模量及输出间隔；石墨/NCM 弹性由当前源码固定，其 G/K 属性栏只读。所有改动只进入新副本。")
            self.initial_tree.delete(*self.initial_tree.get_children())
            supported_initial = profile.get("initializer", {}).get("kind") in {"spring_demo", "e1_phase"}
            self.initial_note.set("使用参数页的当前数值，单独计算或导出初态。" if supported_initial else "当前模型尚未登记初始化算法；文件版本检查和已有结果查看仍可独立使用。")
            if self.is_a5_draft():
                self.initial_note.set("A5 请使用参数页的“工况与初态”：调用当前源的材料初态核，反求 SiOₓ 初态并预览输入。局部初态检查不代替全网格 OCV/CDI。")
            actions = self.actions()
            choices = [ACTION_LABELS[key] for key in ("all", "run") if key in actions]
            self.action_selector.configure(values=choices)
            self.action_choice.set(choices[0] if choices else "未登记运行操作")
            self.compile_note.set("已登记独立编译；此操作不运行求解。" if "compile" in actions else "当前模型尚未登记独立编译；可先核对文件或导入完整执行配置。")
            if self.is_a5_draft():
                self.compile_note.set("A5 使用右侧独立联通入口：冻结材料和初态，先完成 Abaqus 输入检查（含编译），再执行 0.3 秒短程。通用运行按钮保持关闭。")
            self.reuse_label.set("尚未选择编译结果。Python参考运行不需要此项。")
            self.job_summary.set("尚未生成本次操作副本。")
            self.building = False
            self.update_registry()
            self.configure_result_page()
            self.set_buttons()
            if self.is_a5_draft():
                self.show_page("参数配置")
        except Exception as exc:
            self.building = False
            self.error(exc)

    def parameters_changed(self, *_args) -> None:
        if not self.building:
            self.initial = None
            self.case = None
            self.prepared_action = None
            self.initial_note.set("参数已修改，请重新计算初始状态。已有运行结果仍保留在原目录。")
            current_a5 = getattr(self, "a5_window", None)
            if current_a5 is not None:
                current_a5.invalidate()
            current_runtime = getattr(self, "a5_runtime_window", None)
            if current_runtime is not None:
                current_runtime.invalidate()
            operating = getattr(self, 'a5_operating_window', None)
            if operating is not None and operating.root.winfo_exists():
                operating.invalidate()
            if self.is_a5_draft():
                self.initial_note.set("A5 参数已修改；请刷新电池文件预览。材料改动后的同源初态与相应核验完成前，草稿不能提交计算。")
            self.set_buttons()

    def is_a5_draft(self) -> bool:
        return self.profile.get("adapter", {}).get("kind") == "a5_draft_v1"

    def open_a5_operating(self) -> None:
        if self.busy:
            return
        try:
            if not self.is_a5_draft() or not self.registration_ok:
                raise ValueError("请先选择已核对的 A5 模型。")
            current = getattr(self, 'a5_operating_window', None)
            if current is None or not current.root.winfo_exists():
                from .a5_operating_window import A5OperatingWindow
                current = A5OperatingWindow(self.root, self.profile, self.parameters, on_created=self.a5_draft_created)
                current.on_runtime_busy = self.a5_runtime_busy
                self.a5_operating_window = current
            present_startup_window(current.root)
        except Exception as error:
            self.error(error)

    def open_a5_operating_case(self, case: Path) -> None:
        """Restore an explicit copied diagnostic without preparing or running it."""
        try:
            from .a5_operating_runtime import read_runtime_status
            state = read_runtime_status(case)
            contract = state['contract']
            if contract['model_card_sha256'] != self.profile.get('_profile_sha256'):
                raise ValueError('该工况计算副本与当前模型卡不匹配。')
            values = contract['parameters']
            for key, value in values['materials'].items():
                self.variables[key].set(str(value))
            self.open_a5_operating()
            window = self.a5_operating_window
            for key, value in values['settings'].items():
                window.variables[key].set(str(value))
            registration = Path(values['registration'])
            window.kernels[registration.name] = registration
            window.kernel_choice.set(registration.name)
            window.open_check()
            window.runtime_window.open_case(case)
        except Exception as error:
            self.error(error)

    def register_a5_model(self) -> None:
        if self.busy:
            return
        path = filedialog.askdirectory(parent=self.root, title="选择冻结二维 A5 statecontract 根目录")
        if not path:
            return
        try:
            from .a5_draft import register_a5
            self.background("正在只读核对 A5 电池文件并登记…", lambda: register_a5(Path(path), APP / "local"), self.a5_registered)
        except Exception as exc:
            self.error(exc)

    def a5_registered(self, path: Path) -> None:
        self.refresh_profiles(Path(path))
        self.show_page("参数配置")
        self.status.set("真实 A5 电池已登记；修改参数后可预览文件改动并生成草稿，尚不提交计算。")

    def preview_a5_parameters(self) -> None:
        if self.busy:
            return
        try:
            if not self.is_a5_draft() or not self.registration_ok:
                raise ValueError("请先登记并核对真实 A5 电池模型。")
            self.parameters()
            current_a5 = getattr(self, "a5_window", None)
            if current_a5 is not None and not current_a5.closed:
                current_a5.root.lift()
                current_a5.refresh()
                return
            from .a5_draft_window import A5DraftWindow
            self.a5_window = A5DraftWindow(self.root, self.profile, self.parameters,
                                          on_created=self.a5_draft_created, on_close=self.a5_preview_closed)
        except Exception as exc:
            self.error(exc)

    def a5_preview_closed(self) -> None:
        self.a5_window = None

    def open_a5_runtime(self, case: Path | None = None) -> None:
        if self.busy:
            return
        try:
            if not self.is_a5_draft() or not self.registration_ok:
                raise ValueError("请先选择并核对冻结二维 A5 模型。")
            current = getattr(self, "a5_runtime_window", None)
            if current is None or current.closed:
                from .a5_runtime_window import A5RuntimeWindow
                current = A5RuntimeWindow(self.root, self.profile, self.parameters,
                                          on_history=self.a5_runtime_history,
                                          on_busy=self.a5_runtime_busy,
                                          on_close=self.a5_runtime_closed)
                self.a5_runtime_window = current
            else:
                current.root.lift()
                current.refresh()
            if case is not None:
                current.open_case(Path(case))
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.error(error)

    def a5_runtime_closed(self) -> None:
        self.a5_runtime_window = None

    def restore_runtime_startup_parameters(self, case: Path) -> None:
        """Restore an explicitly requested CLI case into the new startup form."""
        runtime = getattr(self, "a5_runtime_window", None)
        if runtime is None or runtime.case is None or runtime.case.resolve() != Path(case).resolve():
            return
        try:
            values = validate_parameters(self.profile, runtime.case_parameters)
            if set(values) != set(self.variables):
                raise ValueError("已有联通算例的参数与当前表单不一致。")
            previous_building = self.building
            self.building = True
            try:
                for key, value in values.items():
                    self.variables[key].set(str(value))
            finally:
                self.building = previous_building
            runtime.refresh()
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.error(error)

    def a5_runtime_busy(self, busy: bool) -> None:
        self.busy = busy
        if busy:
            self.progress.start(12)
            self.status.set("A5 操作进行中；当前窗口显示准备或计算阶段。")
        else:
            self.progress.stop()
            self.status.set("A5 联通操作已结束，请按实际回执查看状态与结果。")
        self.set_buttons()

    def a5_runtime_history(self, path: Path) -> None:
        self.load_csv(Path(path))
        self.show_page("结果查看")
        self.status.set("已载入 A5 真实短程结果；不代表完整充电工况通过。")

    def a5_draft_created(self, result: dict) -> None:
        if result.get("status") != "DRAFT_GENERATED" or result.get("execution_enabled") is not False:
            raise ValueError("A5 结果不是禁用执行的草稿。")
        self.status.set("A5 草稿已生成：" + str(result["draft"]) + "；未编译或启动计算。")
        browser = getattr(self, "file_browser", None)
        if browser is not None:
            browser.open_draft(Path(result["draft"]))
            self.model_notebook.select(browser)
            self.show_page("模型与文件")

    def browser_status(self, message: str) -> None:
        if hasattr(self, "status"):
            self.status.set(message)

    def open_browser_draft(self, path: Path) -> None:
        try:
            self.file_browser.open_draft(Path(path))
            self.model_notebook.select(self.file_browser)
            self.show_page("模型与文件")
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.error(error)

    def parameters(self) -> dict:
        if not self.profile.get("parameters"):
            return {}
        return validate_parameters(self.profile, {key: value.get() for key, value in self.variables.items()})

    def actions(self) -> dict[str, list[str]]:
        if not self.profile:
            return {}
        return available_actions(self.profile)

    def selected_action(self) -> str | None:
        selected = self.action_choice.get()
        return next((key for key, label in ACTION_LABELS.items() if label == selected), None)

    def action_changed(self, *_args) -> None:
        if not self.building:
            self.case = None
            self.prepared_action = None
            self.job_summary.set("操作选择已更改，请生成对应的新副本；不会自动补做其他步骤。")
            self.set_buttons()

    def choose_compile_run(self) -> None:
        if self.busy:
            return
        path = filedialog.askdirectory(parent=self.root, title="选择包含 result.json 的已完成编译运行目录")
        if path:
            candidate = Path(path).resolve()
            if not (candidate / "result.json").is_file():
                self.error(ValueError("请选择包含 result.json 的编译运行目录，不是源文件目录。"))
                return
            self.reuse_run = candidate
            self.reuse_label.set("已选编译结果：" + str(candidate))
            self.case = None
            self.prepared_action = None
            self.set_buttons()

    def register_files(self) -> None:
        if self.busy:
            return
        ModelFilesWindow(self.root, on_registered=self.files_registered)

    def open_interface_generator(self) -> None:
        from .interface_generator_window import InterfaceGeneratorWindow
        InterfaceGeneratorWindow(self.root, self.workspace, self.cae_context)

    def open_selected_interface_generator(self) -> None:
        kind = self.cae_context.get("interface_selection", {}).get("kind", "")
        if kind == "2D_T3_REACTION_EDGE":
            self.open_interface_generator()
        elif kind == "3D_H8_REACTION_FACE":
            self.open_h8_interface_generator()

    def open_h8_interface_generator(self) -> None:
        from .interface_generator_window import H8InterfaceGeneratorWindow
        H8InterfaceGeneratorWindow(self.root, self.workspace, self.cae_context)

    def files_registered(self, path: Path) -> None:
        self.refresh_profiles(path)
        self.show_page("模型与文件")
        self.status.set("本地文件已登记；可核对版本，计算操作需由完整配置卡明确声明。")

    def update_registry(self) -> None:
        if not self.profile:
            return
        try:
            result = registry_status(self.profile)
            self.registration_ok = result["matched"]
            lines = [f"模型：{self.profile['label']}", f"版本 ID：{self.profile['id']}",
                     f"版本匹配：{'通过' if result['matched'] else '未通过'}", "",
                     f"单元分类：{self.profile.get('dimension', self.profile.get('element_type', '由模型配置声明'))}",
                     f"Fortran：{result.get('source_path', '')}", f"SHA-256：{result.get('source_sha256', '')}", "",
                     f"初态适配器：{self.profile.get('initializer', {}).get('kind', '')}",
                     f"计算已登记：{'是' if result.get('execution_enabled') else '否（当前仅开放初态/证据预览）'}", ""]
            if self.profile.get("inp"):
                lines += ["INP：" + str(Path(self.profile["parent_root"]) / self.profile["inp"])]
            files = self.profile.get("files", {})
            lines += ["登记文件："] + ["  " + str(name) for name in files]
            actions = self.actions()
            lines += ["", "可选操作：" + ("、".join(ACTION_LABELS.get(key, key) for key in actions) if actions else "文件版本核对；已有结果查看")]
            lines.extend(str(issue) for issue in result.get("issues", []))
            if self.profile.get("execution", {}).get("reason"):
                lines += ["", str(self.profile["execution"]["reason"])]
            if self.is_a5_draft():
                lines += ["", "A5 电池工具：参数配置 → 电池参数与文件预览 → 生成新草稿。", "草稿不接入编译／运行按钮；当前原生捕获采样与只读初态按冻结版本保留。"]
            lines += ["", "参数映射："]
            lines += [f"  {binding['value']} → {binding['file']}" for binding in self.profile.get("bindings", [])]
            self.registry_text.delete("1.0", "end")
            self.registry_text.insert("1.0", "\n".join(lines))
            self.check_text.delete("1.0", "end")
            self.check_text.insert("1.0", "\n".join(lines))
            browser = getattr(self, "file_browser", None)
            if browser is not None:
                browser.refresh()
            self.status.set("模型版本匹配通过" if result["matched"] else "模型文件不匹配，请查看模型与文件页")
            self.set_buttons()
        except Exception as exc:
            self.error(exc)

    def compute_initial(self) -> None:
        try:
            if self.profile.get("initializer", {}).get("kind") not in {"spring_demo", "e1_phase"}:
                raise ValueError("当前模型尚未登记初始化算法。文件检查和结果查看可分别使用。")
            parameters = self.parameters()
            if not registry_status(self.profile)["matched"]:
                raise ValueError("模型文件与登记哈希不符，请先刷新版本匹配。")
            profile = dict(self.profile)
            self.background("正在计算材料初态…", lambda: calculate_initial(profile, parameters), self.initial_ready)
        except Exception as exc:
            self.error(exc)

    def initial_ready(self, result: dict) -> None:
        self.initial = result
        labels = {"theta": "初始占位率 θ", "temperature_K": "温度", "mu_mJ_mol": "化学势（程序原值）", "mu_J_mol": "化学势", "mu_scaled": "固相主变量初值", "ocv_V": "材料开路电位", "phi_s_V": "固相电势候选", "phi_e_V": "液相电势参考", "c_reference_mol_m3": "参考体积浓度", "c_current_mol_m3": "当前体积浓度", "initial_displacement_m": "初始位移", "initial_force_N": "初始力", "initial_energy_J": "初始弹性能", "target_displacement_m": "终点位移", "theta_lo": "占位率下界", "theta_hi": "占位率上界", "deformation_J": "体积比 J", "local_eta_V": "局部过电位", "local_current_A_m2": "局部反应电流密度"}
        short_units = {"mu_mJ_mol": "mJ/mol", "mu_scaled": "无量纲", "c_reference_mol_m3": "mol/m³", "c_current_mol_m3": "mol/m³", "local_current_A_m2": "A/m²"}
        self.initial_tree.delete(*self.initial_tree.get_children())
        values = result.get("values", {})
        priority = [key for key in labels if key in values]
        priority += [key for key in values if key not in labels]
        for key in priority:
            value = values[key]
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                self.initial_tree.insert("", "end", values=(labels.get(key, key), f"{value:.9g}", short_units.get(key, result.get("units", {}).get(key, ""))))
        failed = [key for key, passed in result.get("checks", {}).items() if passed is not True]
        scope = ("已调用所选版本的实际材料核。结果用于单相局部初始化；完整电池初始平衡需单独检查。"
                 if self.profile.get("initializer", {}).get("kind") == "e1_phase" else "弹簧演示：初始力和弹性能由当前参数解析计算。")
        self.initial_note.set(("检查未通过：" + ", ".join(failed) if failed else "材料初态检查通过。") + "\n" + scope)
        self.status.set("初态计算完成；可查看实际数值与检查范围")
        self.set_buttons()

    def export_initial(self) -> None:
        if self.initial is None:
            self.error(ValueError("请先计算当前参数的初始状态。"))
            return
        path = filedialog.asksaveasfilename(parent=self.root, title="导出当前初始状态", defaultextension=".json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            target = self.safe_project_path(Path(path))
            target.write_text(json.dumps(self.initial, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            self.status.set("初始状态已独立导出，未编译或启动计算。")
        except Exception as exc:
            self.error(exc)

    def choose_workspace(self) -> None:
        path = filedialog.askdirectory(title="选择新算例输出位置", parent=self.root)
        if path:
            self.workspace = Path(path).resolve()
            self.output_label.set(str(self.workspace))

    def prepare(self, action: str | None = None, *, auto_start: bool = False) -> None:
        try:
            action = action or self.selected_action()
            if action not in self.actions():
                raise ValueError("请先选择该模型明确登记的操作。")
            if action != "compile" and self.initial is None:
                raise ValueError("请先计算当前参数对应的初始状态。")
            parameters = self.parameters()
            profile = dict(self.profile)
            initial = dict(self.initial) if self.initial is not None and action != "compile" else None
            reuse_run = self.reuse_run if action == "run" else None
            archive = profile.get("archive_root") if profile.get("mode") == "abaqus" else None
            if profile.get("mode") == "abaqus" and not archive:
                from uel_lab.devflow.contract import ARCHIVE_ROOT
                archive = str(ARCHIVE_ROOT)
            destination = Path(archive) if archive else self.workspace
            name = "statecontract_" + datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:6]
            self.background(f"正在准备{ACTION_LABELS[action]}的新副本…",
                            lambda: prepare_action_case(profile, parameters, destination / name, action=action,
                                                        initial=initial, reuse_run=reuse_run),
                            lambda result: self.case_ready(result, action=action, auto_start=auto_start))
        except Exception as exc:
            self.error(exc)

    def case_ready(self, result: dict, *, action: str, auto_start: bool = False) -> None:
        if result.get("status") != "GENERATED" or not result.get("preflight", {}).get("passed"):
            raise ValueError("新算例预检未通过：" + "; ".join(result.get("preflight", {}).get("issues", [])))
        self.case = Path(result["case"])
        self.prepared_action = action
        self.job_summary.set(f"本次操作：{ACTION_LABELS[action]}\n新副本：{self.case}\n只执行所选操作，不改原文件。")
        self.status.set(f"{ACTION_LABELS[action]}副本已准备完成")
        self.show_page("编译与检查" if action == "compile" else "计算任务")
        self.set_buttons()
        if auto_start:
            self.start(prepared_action=action)

    def start(self, *, prepared_action: str | None = None) -> None:
        try:
            if self.case is None or not self.profile.get("execution", {}).get("enabled"):
                raise ValueError("请先生成可执行的新算例。")
            expected_action = prepared_action or self.selected_action()
            if expected_action != self.prepared_action:
                raise ValueError("当前副本与所选操作不同，请重新生成本次操作副本。")
            self.started_run_ids = {path.name for path in (self.case / "runs").glob("*") if path.is_dir()}
            self.process_log = self.case / ("studio_job_" + uuid.uuid4().hex[:8] + ".log")
            self.process_stream = self.process_log.open("wb")
            env = os.environ.copy()
            env.update(PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
            paths = [str(APP)]
            backend = APP / "backend"
            if not backend.is_dir():
                backend = APP.parents[1] / "src"
            if backend.is_dir():
                paths.append(str(backend))
            if env.get("PYTHONPATH"):
                paths.append(env["PYTHONPATH"])
            env["PYTHONPATH"] = os.pathsep.join(paths)
            self.process = subprocess.Popen([sys.executable, "-m", "prime_studio.worker", str(self.case)],
                                            cwd=APP, env=env, stdout=self.process_stream, stderr=subprocess.STDOUT,
                                            shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.result = None
            self.busy = True
            self.progress.start(12)
            self.status.set(f"正在计算 · 本次任务 PID {self.process.pid}")
            self.log.delete("1.0", "end")
            self.log.insert("end", "已启动注册计算，等待运行日志…\n")
            self.set_buttons()
        except Exception as exc:
            if self.process_stream:
                self.process_stream.close()
            self.error(exc)

    def stop(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self.process.pid), "/T", "/F"], capture_output=True, check=False, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                self.process.terminate()
            receipt = {"status": "INTERRUPTED", "pid": self.process.pid, "scope": "This Studio task only; incomplete results are not a pass."}
            if self.case:
                (self.case / f"studio_stopped_{uuid.uuid4().hex[:8]}.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
            self.status.set("本次任务已手动停止，未完成结果不记为通过")
        except Exception as exc:
            self.error(exc)

    def background(self, message: str, function, callback) -> None:
        if self.busy:
            raise ValueError("请等待当前操作完成。")
        self.busy = True
        self.progress.start(12)
        self.status.set(message)
        self.set_buttons()
        def work():
            try:
                self.pending.put((callback, function(), None))
            except Exception as exc:
                self.pending.put((None, None, exc))
        threading.Thread(target=work, daemon=True).start()

    def poll(self) -> None:
        if self.closed:
            return
        try:
            callback, result, error = self.pending.get_nowait()
            self.busy = False
            self.progress.stop()
            if error:
                self.error(error)
            else:
                try:
                    callback(result)
                except Exception as exc:
                    self.error(exc)
            self.set_buttons()
        except queue.Empty:
            pass
        if self.process:
            self.refresh_logs()
            code = self.process.poll()
            if code is not None:
                self.process = None
                if self.process_stream:
                    self.process_stream.close()
                    self.process_stream = None
                self.busy = False
                self.progress.stop()
                try:
                    self.load_completed_result(code)
                except Exception as exc:
                    self.error(exc)
                self.set_buttons()
        self.root.after(350, self.poll)

    def refresh_logs(self) -> None:
        paths = []
        if self.case:
            runs = sorted((self.case / "runs").glob("*"))
            if runs:
                paths += sorted(runs[-1].glob("*.console.log"))
        if self.process_log:
            paths.append(self.process_log)
        text = []
        for path in paths:
            try:
                with path.open("rb") as stream:
                    size = path.stat().st_size
                    stream.seek(max(0, size - 18000))
                    text.append(f"[{path.name}]\n" + stream.read().decode("utf-8", errors="replace"))
            except OSError:
                continue
        content = "\n\n".join(text)
        if content and content != self.log.get("1.0", "end-1c"):
            self.log.delete("1.0", "end")
            self.log.insert("1.0", content)
            self.log.see("end")
            if self.prepared_action == "compile":
                self.check_text.delete("1.0", "end")
                self.check_text.insert("1.0", content)
                self.check_text.see("end")

    def load_completed_result(self, code: int) -> None:
        if self.case is None:
            return
        results = sorted(path for path in (self.case / "runs").glob("*/result.json")
                         if path.parent.name not in self.started_run_ids)
        if not results:
            self.status.set(f"任务已结束（返回码 {code}）；未生成完整运行记录，请查看日志。")
            return
        self.result = json.loads(results[-1].read_text(encoding="utf-8"))
        if code != 0:
            self.status.set(f"本次任务未通过或被停止 · {self.result.get('status', 'INCOMPLETE')} · 返回码 {code}")
            return
        names = {"DIAGNOSTIC_COMPLETE": "诊断计算与所列检查通过", "VALIDATION_CHECKS_PASSED": "所列验证检查通过"}
        if self.prepared_action == "compile":
            self.reuse_run = results[-1].parent
            self.reuse_label.set("已选本次编译结果：" + str(self.reuse_run))
            self.status.set("独立编译完成；可计算初态后选择“只运行”，也可浏览其他匹配编译目录。")
            return
        self.status.set("计算完成 · " + names.get(self.result.get("status"), self.result.get("status", "未知")))
        visual = self.profile.get("visualization", {})
        if visual.get("file"):
            path = Path(self.result["workdir"]) / visual["file"]
            if path.is_file():
                self.load_csv(path)
                self.show_page("结果查看")

    def load_csv(self, path: Path) -> None:
        data = read_history(path)
        self.history = data
        self.history_path = path
        visual = self.profile.get("visualization", {})
        self.x_column = visual.get("x") if visual.get("x") in data["columns"] else data["columns"][0]
        options = [column for column in data["columns"] if column != self.x_column]
        definitions = {item["column"]: item for item in visual.get("series", [])}
        self.series_options = {}
        for column in options:
            definition = definitions.get(column, {})
            label = definition.get("label", column)
            if definition.get("unit"):
                label += f" ({definition['unit']})"
            self.series_options[label] = column
        self.series_selector.configure(values=list(self.series_options))
        preferred = next((item["column"] for item in visual.get("series", []) if item.get("column") in options), options[0] if options else "")
        self.series.set(next((label for label, column in self.series_options.items() if column == preferred), ""))
        self.result_note.set(f"{path.name} · {len(data['rows'])} 个数据点。曲线来自该文件的实际数据。")
        self.draw_chart()

    def draw_chart(self) -> None:
        self.canvas.delete("all")
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        if width < 100 or height < 100:
            return
        if not self.history or not self.series.get():
            self.canvas.create_text(width / 2, height / 2, text="运行完成后在这里查看曲线\n也可以导入已有 CSV", fill=MUTED, font=(FONT, 13), justify="center")
            return
        rows = self.history["rows"]
        ykey = self.series_options.get(self.series.get(), self.series.get())
        points = [(float(row[self.x_column]), float(row[ykey])) for row in rows]
        x0, x1 = min(x for x, _ in points), max(x for x, _ in points)
        y0, y1 = min(y for _, y in points), max(y for _, y in points)
        if x1 == x0:
            x1 += 1
        if y1 == y0:
            y0, y1 = y0 - 0.5, y1 + 0.5
        padding = 0.06 * (y1 - y0)
        y0, y1 = y0 - padding, y1 + padding
        left, top, right, bottom = 78, 40, width - 28, height - 60
        for tick in range(6):
            fraction = tick / 5
            y = bottom - fraction * (bottom - top)
            x = left + fraction * (right - left)
            self.canvas.create_line(left, y, right, y, fill="#E8EFF4")
            self.canvas.create_text(left - 12, y, text=f"{y0 + fraction * (y1 - y0):.4g}", anchor="e", fill=MUTED, font=("Segoe UI", 9))
            self.canvas.create_text(x, bottom + 17, text=f"{x0 + fraction * (x1 - x0):.4g}", fill=MUTED, font=("Segoe UI", 9))
        self.canvas.create_line(left, top, left, bottom, right, bottom, fill="#92A7B7")
        coords = [coordinate for x, y in points for coordinate in (left + (x - x0) / (x1 - x0) * (right - left), bottom - (y - y0) / (y1 - y0) * (bottom - top))]
        if len(coords) >= 4:
            self.canvas.create_line(*coords, fill=TEAL, width=2.5)
        self.canvas.create_text(left, 18, text=self.series.get(), anchor="w", fill=INK, font=(FONT, 11, "bold"))
        xlabel = "时间 (s)" if self.x_column == "time_s" else self.x_column
        self.canvas.create_text((left + right) / 2, height - 17, text=xlabel, fill=INK, font=(FONT, 10))

    def import_csv(self) -> None:
        path = filedialog.askopenfilename(parent=self.root, title="选择数值结果 CSV", filetypes=[("CSV", "*.csv")])
        if path:
            try:
                self.load_csv(Path(path))
            except Exception as exc:
                self.error(exc)

    def import_profile(self) -> None:
        if self.busy:
            return
        path = filedialog.askopenfilename(parent=self.root, title="导入模型配置卡", filetypes=[("JSON", "*.json")])
        if path:
            self.refresh_profiles(Path(path))

    def save_project(self) -> None:
        if self.busy or not self.profile:
            return
        try:
            parameters = self.parameters()
            path = filedialog.asksaveasfilename(parent=self.root, title="保存参数项目", defaultextension=".json", filetypes=[("JSON", "*.json")])
            if path:
                data = {"schema_version": 1, "profile": self.profile["_profile_path"], "parameters": parameters, "workspace": str(self.workspace)}
                self.safe_project_path(Path(path)).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
                self.status.set("项目参数已保存；已有计算目录不会被覆盖。")
        except Exception as exc:
            self.error(exc)

    def open_project(self) -> None:
        if self.busy:
            return
        path = filedialog.askopenfilename(parent=self.root, title="打开参数项目", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            self.refresh_profiles(Path(data["profile"]))
            values = validate_parameters(self.profile, data["parameters"]) if self.profile.get("parameters") else {}
            for key, value in values.items():
                self.variables[key].set(str(value))
            self.workspace = Path(data.get("workspace", str(self.workspace))).resolve()
            self.output_label.set(str(self.workspace))
            self.status.set("项目参数已加载，请重新计算初始状态。")
            if self.is_a5_draft():
                self.status.set("A5 参数项目已加载；请刷新电池文件预览。当前仅生成草稿，不提交计算。")
        except Exception as exc:
            self.error(exc)

    def open_case_folder(self) -> None:
        if self.case and os.name == "nt":
            os.startfile(str(self.case))

    def open_report(self) -> None:
        if self.result:
            path = Path(self.result["workdir"]).parent / "report.md"
            if path.is_file() and os.name == "nt":
                os.startfile(str(path))

    def safe_project_path(self, path: Path) -> Path:
        target = path.resolve()
        if target.suffix.casefold() != ".json":
            raise ValueError("项目和初态请保存为 JSON 文件。")
        parent = self.profile.get("parent_root")
        if parent and target.is_relative_to(Path(parent).resolve()):
            raise ValueError("请将项目/初态另存到工作目录，不要写入已登记的原模型目录。")
        protected = {Path(self.profile["_profile_path"]).resolve()} if self.profile.get("_profile_path") else set()
        for key in ("dll", "material"):
            asset = self.profile.get("initializer", {}).get(key, {})
            if asset.get("path"):
                protected.add(Path(asset["path"]).resolve())
        if target in protected:
            raise ValueError("不能覆盖已登记的模型、材料或DLL。")
        return target

    def set_buttons(self) -> None:
        actions = self.actions()
        executable = bool(self.profile.get("execution", {}).get("enabled")) and self.registration_ok
        initial_supported = self.profile.get("initializer", {}).get("kind") in {"spring_demo", "e1_phase"}
        valid_initial = self.initial is not None and bool(self.initial.get("checks")) and all(value is True for value in self.initial["checks"].values())
        selected = self.selected_action()
        for button, enabled in ((self.initial_button, initial_supported and self.registration_ok),
                                (self.export_initial_button, valid_initial),
                                (self.compile_button, executable and "compile" in actions),
                                (self.prepare_button, executable and valid_initial and selected in actions),
                                (self.run_button, executable and self.case is not None and self.prepared_action == selected),
                                (self.model_check_button, bool(self.profile)),
                                (self.check_button, bool(self.profile)),
                                (self.files_button, True),
                                (self.reuse_button, "run" in actions)):
            button.configure(state="normal" if enabled and not self.busy else "disabled")
        for name, enabled in (("a5_register_button", True),
                              ("a5_preview_button", self.is_a5_draft() and self.registration_ok),
                              ("a5_operating_button", self.is_a5_draft() and self.registration_ok),
                              ("a5_runtime_check_button", self.is_a5_draft() and self.registration_ok),
                              ("a5_runtime_run_button", self.is_a5_draft() and self.registration_ok)):
            button = getattr(self, name, None)
            if button is not None:
                button.configure(state="normal" if enabled and not self.busy else "disabled")
        self.stop_button.configure(state="normal" if self.process and self.process.poll() is None else "disabled")
        self.selector.configure(state="disabled" if self.busy else "readonly")
        self.action_selector.configure(state="disabled" if self.busy or not actions else "readonly")
        for entry in self.entries:
            if self.busy:
                state = 'disabled'
            elif getattr(entry, 'a5_source_fixed', False):
                state = 'readonly'
            else:
                state = 'normal'
            entry.configure(state=state)
        browser = getattr(self, "file_browser", None)
        if browser is not None:
            browser.set_busy(self.busy)

    def error(self, error: Exception) -> None:
        self.status.set(str(error))
        messagebox.showerror("PRIME UEL Studio", str(error), parent=self.root)

    def close(self) -> None:
        operating = getattr(self, 'a5_operating_window', None)
        if operating is not None and operating.busy:
            messagebox.showinfo('正在准备', '请等待当前初态计算或副本生成结束。', parent=self.root)
            return
        check = getattr(operating, 'runtime_window', None)
        if check is not None and (check.busy or check.running):
            messagebox.showinfo('A5 任务仍在进行', '请先在工况计算窗口停止任务并等待回执，或等待它结束。', parent=self.root)
            return
        runtime = getattr(self, "a5_runtime_window", None)
        if runtime is not None and (runtime.busy or runtime.running):
            messagebox.showinfo("A5 任务仍在进行", "请先在联通窗口停止任务并等待回执，或等待它结束。", parent=self.root)
            return
        if self.process and self.process.poll() is None:
            if not messagebox.askokcancel("本次计算仍在运行", "关闭窗口后本次任务会继续运行。现在关闭窗口吗？", parent=self.root):
                return
        if operating is not None and operating.root.winfo_exists():
            if not operating.close():
                return
        if runtime is not None:
            runtime.close()
        self.closed = True
        self.root.destroy()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--draft", type=Path, help="Open a matching generated A5 draft for read-only browsing")
    parser.add_argument("--cae-context", type=Path, help="Open with the current Abaqus/CAE model context")
    parser.add_argument("--runtime-case", type=Path, help="Inspect an existing matching A5 short-runtime case")
    parser.add_argument("--page", choices=PAGE_NAMES, help="Open one toolbox page without starting a task")
    parser.add_argument("--operating-setup", action="store_true", help="Open A5 operating/initial inputs without submitting a job")
    parser.add_argument('--operating-case', type=Path, help='Inspect an existing operating diagnostic; never submit on startup')
    args = parser.parse_args()
    root = tk.Tk()
    studio = Studio(root, args.profile, args.cae_context)
    if args.draft is not None:
        studio.open_browser_draft(args.draft)
    if args.runtime_case is not None:
        studio.open_a5_runtime(args.runtime_case)
        studio.restore_runtime_startup_parameters(args.runtime_case)
    if args.page is not None:
        studio.show_page(args.page)
    present_startup_window(root)
    if args.operating_setup:
        studio.open_a5_operating()
    if args.operating_case is not None:
        studio.open_a5_operating_case(args.operating_case)
    if args.runtime_case is not None and args.page is None and studio.a5_runtime_window is not None:
        present_startup_window(studio.a5_runtime_window.root)
    root.mainloop()
