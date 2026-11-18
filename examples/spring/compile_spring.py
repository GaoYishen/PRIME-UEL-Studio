"""Compile the standalone example with Intel's existing Windows toolchain.

No compiler is installed by this script. The child environment is not persisted.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess


def compiler_environment(compiler: Path) -> dict[str, str]:
    minimal = minimal_windows_environment(compiler)
    if minimal is not None and not os.environ.get("PRIME_INTEL_ENV"):
        return minimal
    override = os.environ.get("PRIME_INTEL_ENV")
    candidates = [Path(override)] if override else [
        compiler.parent.parent / "ipsxe-comp-vars.bat",
        compiler.parent.parent / "ifortvars.bat",
        compiler.parent.parent.parent / "env/vars.bat",
    ]
    script = next((path.resolve() for path in candidates if path.is_file()), None)
    if script is None:
        raise RuntimeError("Intel environment script not found. Start from an Intel developer prompt, or set PRIME_INTEL_ENV.")
    if re.search(r'[&|<>^%\r\n!\"]', str(script)):
        raise ValueError("Unsupported command characters in the Intel environment path")
    # /u makes SET output Unicode. Do not print or persist it: inherited
    # environment variables can contain unrelated private credentials.
    inner = "call " + subprocess.list2cmdline([str(script), "intel64", "vs2019"]) + " >nul 2>&1 && set"
    command = 'cmd.exe /d /u /v:off /s /c "' + inner + '"'
    result = subprocess.run(command, capture_output=True, encoding="utf-16le", errors="replace",
                            timeout=30, shell=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    if result.returncode:
        raise RuntimeError("Intel/MSVC environment setup failed. Check the installed compiler environment.")
    environment = os.environ.copy()
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key and not key.startswith("="):
            # Windows environment variable names are case insensitive.
            for old in list(environment):
                if old.casefold() == key.casefold() and old != key:
                    del environment[old]
            environment[key] = value
    return environment


def minimal_windows_environment(compiler: Path) -> dict[str, str] | None:
    """Find existing product layouts without running broad vendor setup scripts."""
    windows = compiler.parent.parent.parent
    intel_lib = windows / "compiler/lib/intel64_win"
    if not intel_lib.is_dir():
        return None
    msvc_roots = []
    explicit = os.environ.get("PRIME_MSVC_ROOT")
    if explicit:
        msvc_roots.append(Path(explicit))
    # Common side-by-side Intel/MSVC installation, relative to the selected
    # compiler; these are candidate layouts, never a recursive disk search.
    for ancestor in compiler.parents:
        msvc_roots.append(ancestor / "VS/VC/Tools/MSVC")
    msvc = None
    for root in msvc_roots:
        candidates = [root] if (root / "lib/x64").is_dir() else sorted(root.glob("*"), reverse=True)
        msvc = next((path for path in candidates
                     if (path / "bin/Hostx64/x64/link.exe").is_file() and (path / "lib/x64").is_dir()), None)
        if msvc is not None:
            break
    sdk_root = Path(os.environ.get("WindowsSdkDir") or
                    (Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Windows Kits/10"))
    sdk = next((path for path in sorted((sdk_root / "Lib").glob("*"), reverse=True)
                if (path / "um/x64/kernel32.lib").is_file() and (path / "ucrt/x64/ucrt.lib").is_file()), None)
    if msvc is None or sdk is None:
        return None
    environment = os.environ.copy()
    libraries = [intel_lib, msvc / "lib/x64", sdk / "um/x64", sdk / "ucrt/x64"]
    executables = [msvc / "bin/Hostx64/x64", windows / "redist/intel64_win/compiler", sdk_root / "bin" / sdk.name / "x64"]
    for key, values in (("LIB", libraries), ("PATH", executables)):
        previous = next((value for name, value in environment.items() if name.casefold() == key.casefold()), "")
        for old in list(environment):
            if old.casefold() == key.casefold():
                del environment[old]
        environment[key] = os.pathsep.join(str(path) for path in values if path.is_dir()) + os.pathsep + previous
    return environment


def main() -> int:
    compiler_name = shutil.which("ifort")
    if os.name != "nt" or compiler_name is None:
        raise RuntimeError("This profile requires an existing Windows Intel ifort installation")
    environment = compiler_environment(Path(compiler_name))
    command = [compiler_name, "/nologo", "/Od", "/check:all", "/traceback", "/exe:spring.exe", "spring.f90"]
    result = subprocess.run(command, env=environment, timeout=60, shell=False,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
    if result.returncode or not Path("spring.exe").is_file():
        raise RuntimeError(f"Standalone spring compilation failed (exit {result.returncode})")
    print("Existing Intel/MSVC toolchain compiled the standalone spring example.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
