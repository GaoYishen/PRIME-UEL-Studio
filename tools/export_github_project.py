"""Export the public desktop source project and its small original backend.

This creates a directory and ZIP locally.  It never creates a remote repository
or copies local solver registrations, results, compilers or licensed software.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import uuid
import zipfile

APP = Path(__file__).resolve().parents[1]
REPO = APP.parents[1]
EXAMPLE_SUFFIXES = {".py", ".f90", ".inp", ".dat", ".md", ".json", ".svg", ".png"}
ROOT_FILES = ("launcher.pyw", "Launch PRIME UEL Studio.cmd", "pyproject.toml", "README.md", ".gitignore",
              "LICENSE", "NOTICE", "CITATION.cff", ".gitattributes")
TOOL_FILES = ("tools/export_github_project.py", "tools/audit_public_release.py",
              "tools/install_abaqus_plugin.py")
PUBLIC_STUDIO_FILES = (
    "__init__.py", "gui.py", "worker.py", "core.py", "public_initial.py",
    "file_browser.py", "file_browser_panel.py", "abaqus_results.py",
    "model_wizard.py", "ui_jobs.py", "interface_generator.py", "interface_generator_window.py",
)
PUBLIC_TEST_FILES = (
    "test_abaqus_plugin.py", "test_abaqus_results.py", "test_core.py", "test_examples.py",
    "test_export.py", "test_file_browser.py", "test_file_browser_gui.py", "test_model_wizard.py",
    "test_interface_generator.py",
)
CLI_WRAPPER = '''"""Standalone entry for the bundled copied-case development backend."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from uel_lab.devflow.cli import standalone_main

if __name__ == "__main__":
    raise SystemExit(standalone_main())
'''


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _checked_file(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise ValueError(f"Public export source is missing or escaped its source root: {path.name}")
    return resolved


def _check_public_profile(path: Path) -> None:
    profile = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(profile, dict):
        raise ValueError(f"Public profile must be an object: {path.name}")
    for key in ("parent_root", "canonical", "archive_root"):
        value = profile.get(key)
        if value is not None and (not isinstance(value, str) or Path(value).is_absolute()):
            raise ValueError(f"Public profile contains a machine-specific {key}: {path.name}")
    initializer = profile.get("initializer", {})
    if not isinstance(initializer, dict):
        raise ValueError(f"Invalid initializer in public profile: {path.name}")
    for key in ("dll", "material"):
        asset = initializer.get(key)
        if asset is not None:
            value = asset.get("path") if isinstance(asset, dict) else None
            if not isinstance(value, str) or Path(value).is_absolute():
                raise ValueError(f"Local {key} registration cannot enter a public profile: {path.name}")


def collect_sources(app_root: Path = APP, repo_root: Path | None = None) -> dict[str, Path]:
    """Select only explicit public source files, never a recursive app copy."""
    app_root = app_root.resolve()
    sources: dict[str, Path] = {}
    for relative in ROOT_FILES + TOOL_FILES:
        sources[relative] = _checked_file(app_root, app_root / relative)
    for relative in ("abaqus_plugin/prime_uel_plugin.py", "abaqus_plugin/prime_uel_plugin_gui.py",
                     "abaqus_plugin/prime_uel_plugin_kernel.py",
                     "abaqus_plugin/prime_uel_plugin_help.html"):
        sources[relative] = _checked_file(app_root, app_root / relative)
    for relative in ("docs/public_solver_interface.md", "docs/user_manual_zh.md", "docs/validation_status.md"):
        sources[relative] = _checked_file(app_root, app_root / relative)
    for relative in PUBLIC_STUDIO_FILES:
        path = _checked_file(app_root, app_root / "prime_studio" / relative)
        sources[f"prime_studio/{relative}"] = path
    for path in sorted((app_root / "profiles").glob("*.json")):
        checked = _checked_file(app_root, path)
        _check_public_profile(checked)
        sources[f"profiles/{path.name}"] = checked
    for name in PUBLIC_TEST_FILES:
        path = _checked_file(app_root, app_root / "tests" / name)
        sources[f"tests/{name}"] = path
    examples = app_root / "examples/spring"
    for path in sorted(examples.iterdir()):
        if path.is_file() and path.suffix.casefold() in EXAMPLE_SUFFIXES:
            sources[f"examples/spring/{path.name}"] = _checked_file(app_root, path)
    examples_interface = app_root / "examples/interface"
    for path in sorted(examples_interface.iterdir()):
        if path.is_file() and path.suffix.casefold() in EXAMPLE_SUFFIXES:
            sources[f"examples/interface/{path.name}"] = _checked_file(app_root, path)
    for required in ("prime_studio/gui.py", "prime_studio/worker.py", "prime_studio/core.py",
                     "prime_studio/public_initial.py", "prime_studio/file_browser.py",
                     "prime_studio/file_browser_panel.py", "prime_studio/abaqus_results.py",
                     "prime_studio/model_wizard.py", "prime_studio/ui_jobs.py",
                     "prime_studio/interface_generator.py", "prime_studio/interface_generator_window.py",
                     "profiles/spring_reference.json", "examples/spring/driver.py"):
        if required not in sources:
            raise ValueError(f"Public source project is incomplete: {required}")

    bundled = app_root / "backend/uel_lab"
    if bundled.is_dir():
        backend_root = bundled.resolve()
    else:
        repository = Path(repo_root).resolve() if repo_root is not None else app_root.parents[1]
        backend_root = (repository / "src/uel_lab").resolve()
    public_contract = app_root / "public_backend/uel_lab/devflow/contract.py"
    if public_contract.is_file():
        public_contract = _checked_file(app_root, public_contract)
    else:
        public_contract = _checked_file(backend_root, backend_root / "devflow/contract.py")
    for relative in ("__init__.py", "runtime.py", "io_utils.py"):
        sources[f"backend/uel_lab/{relative}"] = _checked_file(backend_root, backend_root / relative)
    devflow = backend_root / "devflow"
    for path in sorted(devflow.glob("*.py")):
        sources[f"backend/uel_lab/devflow/{path.name}"] = _checked_file(backend_root, path)
    sources["backend/uel_lab/devflow/contract.py"] = public_contract
    for required in ("contract.py", "execution.py", "evidence.py", "reporting.py", "cli.py", "__init__.py"):
        if f"backend/uel_lab/devflow/{required}" not in sources:
            raise ValueError(f"Copied-case backend is incomplete: {required}")
    return sources


def export_project(output: Path, *, app_root: Path = APP, repo_root: Path | None = None) -> dict:
    """Create a new source directory, content manifest and same-name ZIP."""
    app_root, output = app_root.resolve(), Path(output).resolve()
    archive = output.parent / (output.name + ".zip")
    if output.exists() or archive.exists():
        raise FileExistsError("Export directory and ZIP must both be new; existing exports are never overwritten")
    if output.is_relative_to(app_root) or app_root.is_relative_to(output):
        raise ValueError("Export and source application directories must be disjoint")
    sources = collect_sources(app_root, repo_root)
    source_hashes = {relative: sha256(path) for relative, path in sources.items()}
    output.mkdir(parents=True, exist_ok=False)
    for relative, source in sources.items():
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        if sha256(destination) != source_hashes[relative] or sha256(source) != source_hashes[relative]:
            raise ValueError(f"Source changed during export; retained incomplete export: {relative}")
    wrapper = output / "backend/uel_dev.py"
    wrapper.write_text(CLI_WRAPPER, encoding="utf-8")
    files = {relative: sha256(output / relative) for relative in sorted(sources)}
    files["backend/uel_dev.py"] = sha256(wrapper)
    # A release audit is part of the export gate.  It runs on the assembled
    # directory, so copied files and generated wrappers are checked together.
    audit_module = output / "tools/audit_public_release.py"
    audit_spec = __import__("importlib.util").util.spec_from_file_location("prime_public_audit", audit_module)
    if audit_spec is None or audit_spec.loader is None:
        raise ValueError("Unable to load the public-release audit module")
    audit = __import__("importlib.util").util.module_from_spec(audit_spec)
    previous_dont_write = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        audit_spec.loader.exec_module(audit)
    finally:
        sys.dont_write_bytecode = previous_dont_write
    audit_result = audit.audit_directory(output)
    audit_result["root"] = "."
    audit_path = output / "public_release_audit.json"
    audit_path.write_text(json.dumps(audit_result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if audit_result["status"] != "PASS":
        raise ValueError("Public release audit failed: " + json.dumps(audit_result["findings"], ensure_ascii=False))
    manifest = {
        "schema_version": 1,
        "release_label": "v0.8.0-public",
        "package_version": "0.8.0+public",
        "format": "source_project",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
        "manifest_excludes_itself": True,
        "scope": "PRIME UEL Studio public plugin, model-preparation layer and original lightweight devflow backend only.",
        "requirements": "Existing Python 3.10+ with Tk; real Fortran/Abaqus execution needs separately installed tools and registered local models.",
        "not_included": ["Python runtime", "compiler", "Abaqus", "local registrations", "real model DLL/FOR/materials", "results", "license configuration"],
        "distribution": "Source checkout, not an executable installer or qualified wheel.",
        "public_solver_interface": "docs/public_solver_interface.md",
        "release_audit": "public_release_audit.json",
    }
    files["public_release_audit.json"] = sha256(audit_path)
    manifest_path = output / "manifest.json"
    with manifest_path.open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
    with zipfile.ZipFile(archive, mode="x", compression=zipfile.ZIP_DEFLATED) as handle:
        for relative in sorted([*files, "manifest.json"]):
            handle.write(output / relative, arcname=f"{output.name}/{relative}")
    return {"directory": str(output), "zip": str(archive), "files": len(files),
            "manifest": str(manifest_path), "zip_sha256": sha256(archive),
            "scope": manifest["scope"], "remote_published": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="New export directory; its ZIP is created beside it")
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = args.output or REPO / "outputs" / f"prime_uel_studio_github_{stamp}_{uuid.uuid4().hex[:6]}"
    try:
        result = export_project(output)
    except (OSError, ValueError, KeyError) as error:
        print(json.dumps({"status": "BLOCKED", "error": str(error)}, ensure_ascii=True))
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
