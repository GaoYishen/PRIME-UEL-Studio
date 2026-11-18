"""Hand an existing ODB to the installed Abaqus/Viewer.

This module neither reads UEL fields nor submits an analysis.  ``ready`` means
that a file can be handed off; Abaqus determines whether the ODB is readable.
The execution option is ``viewer database=...`` (Abaqus Execution Reference).
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any


DEFAULT_LAUNCHER = Path("C:/Users/Public/abaqus.bat")
_SHELL_META = re.compile(r'[%!&|<>^\r\n\x00"]')
_WRAPPER = (
    "@echo off\n"
    "setlocal DisableDelayedExpansion\n"
    "set PYTHONUTF8=\n"
    "set PYTHONIOENCODING=\n"
    'call "%PRIME_STUDIO_VIEWER_LAUNCHER%" viewer "database=%PRIME_STUDIO_VIEWER_ODB%"\n'
    "exit /b %errorlevel%\n"
)


def _safe_path(path: Path) -> bool:
    return _SHELL_META.search(str(path)) is None


def inspect_odb(path: str | Path) -> dict[str, Any]:
    """Check handoff prerequisites without opening or modifying the database."""
    selected = Path(path).expanduser().resolve()
    issues: list[str] = []
    size = None
    modified = None
    lock = selected.with_suffix(".lck")
    lock_present = False
    if selected.suffix.casefold() != ".odb":
        issues.append("请选择 Abaqus 输出数据库（.odb）。")
    if not _safe_path(selected):
        issues.append("该路径含命令特殊字符，请选择不含这些字符的 ODB 路径。")
    try:
        if not selected.is_file():
            issues.append("所选 ODB 文件不存在。")
        else:
            stat = selected.stat()
            size, modified = stat.st_size, stat.st_mtime_ns
            if size == 0:
                issues.append("所选 ODB 文件为空。")
            # Windows is case-insensitive; keep the same contract in tests on
            # other platforms and for .LCK/.ODB spellings.
            actual_lock = next((item for item in selected.parent.iterdir()
                                if item.name.casefold() == lock.name.casefold()), lock)
            lock_present = actual_lock.exists()
            if lock_present:
                lock = actual_lock
                issues.append("同名 .lck 存在；请先确认该作业或数据库已关闭，再打开结果。")
    except OSError as error:
        issues.append("无法检查所选 ODB：" + str(error))
    return {"ready": not issues, "path": str(selected), "size_bytes": size,
            "modified_ns": modified, "lock_path": str(lock),
            "lock_present": lock_present, "issues": issues,
            "scope": "File handoff only; Abaqus validates the database and provides postprocessing."}


def open_in_abaqus(path: str | Path, launcher: str | Path | None = None) -> dict[str, Any]:
    """Request a Viewer window for this ODB, without claiming it has opened.

    A fixed ASCII wrapper and child-only environment preserve spaces/Unicode
    paths.  Command control characters are rejected before creating any files.
    Launcher output is retained in a unique temporary directory, outside the
    selected result directory.  No solver, compile, stop or delete command is
    exposed here.
    """
    info = inspect_odb(path)
    if not info["ready"]:
        raise ValueError("；".join(info["issues"]))
    odb = Path(info["path"])
    executable = Path(launcher) if launcher is not None else DEFAULT_LAUNCHER
    executable = executable.expanduser().resolve()
    if (not executable.is_file() or executable.suffix.casefold() not in {".bat", ".cmd"}
            or not _safe_path(executable)):
        raise ValueError("未找到可用的已安装 Abaqus 启动脚本。")
    launcher_sha = hashlib.sha256(executable.read_bytes()).hexdigest()
    work = Path(tempfile.mkdtemp(prefix="prime_uel_viewer_" )).resolve()
    if not _safe_path(work):
        raise ValueError("Viewer 日志目录含命令特殊字符，无法安全启动。")
    wrapper = work / "open_viewer.cmd"
    with wrapper.open("x", encoding="ascii", newline="\r\n") as stream:
        stream.write(_WRAPPER)
    log = work / "viewer.log"
    env = {key: value for key, value in os.environ.items()
           if key not in {"PYTHONUTF8", "PYTHONIOENCODING"}}
    env["PRIME_STUDIO_VIEWER_LAUNCHER"] = str(executable)
    env["PRIME_STUDIO_VIEWER_ODB"] = str(odb)
    # Recheck the lock immediately before handing off.  Never remove a lock.
    current = inspect_odb(odb)
    if not current["ready"] or current["size_bytes"] != info["size_bytes"] or current["modified_ns"] != info["modified_ns"]:
        raise ValueError("ODB 在启动前发生变化或仍在使用，请重新选择已关闭的结果。")
    with log.open("xb") as output:
        process = subprocess.Popen(["cmd.exe", "/d", "/c", "call", str(wrapper)],
                                   cwd=odb.parent, env=env, shell=False,
                                   stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return {"status": "VIEWER_LAUNCH_REQUESTED", "odb": str(odb),
            "pid": process.pid, "log": str(log), "wrapper": str(wrapper),
            "launcher": str(executable), "launcher_sha256": launcher_sha,
            "viewer_window_verified": False, "analysis_submitted": False,
            "scope": "Open the selected existing ODB in Abaqus/Viewer; no analysis submission."}
