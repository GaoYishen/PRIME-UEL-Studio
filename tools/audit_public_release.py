"""Audit a PRIME UEL Studio export for private solver material.

The audit is intentionally conservative.  It checks the assembled public
directory, not the development checkout, and reports paths and text anchors
that should never enter a GitHub release.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
from typing import Any


PRIVATE_SUFFIXES = {".dll", ".lib", ".obj", ".mod", ".exe", ".odb", ".lck", ".msg", ".sta", ".sim", ".prt", ".com"}
PUBLIC_SOURCE_ROOTS = {"examples/spring"}
PRIVATE_NAME_TOKENS = (
    "prime_e1b", "mect_e1b", "prime2d_no_dtheta", "material_contract", "statecontract",
    "mect_v6", "e1_kernel.dll", "canonical_v6", "archive_root",
)
PRIVATE_PATH_TOKENS = (
    r"f:\research\batteryphd", r"e:\abaqusinput", r"c:\users\administrator",
)
TEXT_SUFFIXES = {".py", ".pyw", ".json", ".md", ".html", ".toml", ".cmd", ".txt", ".csv"}
DRIVE_PATH = re.compile(r"(?i)(?:[a-z]:[\\/])")
CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)-----begin [a-z ]*private key-----"),
    re.compile(r"(?i)\b(?:ghp|github_pat|sk)-[a-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|secret|password)\s*[:=]\s*['\"][^'\"]{16,}['\"]"),
)
REQUIRED_RELEASE_FILES = {"LICENSE", "NOTICE", "CITATION.cff"}


def _is_public_demo_source(relative: str) -> bool:
    path = Path(relative).as_posix()
    return path.startswith("examples/spring/")


def audit_directory(root: Path) -> dict[str, Any]:
    root = Path(root).resolve()
    findings: list[dict[str, str]] = []
    if not root.is_dir():
        return {"status": "FAIL", "root": str(root), "findings": [{"kind": "missing_root", "path": str(root)}]}

    missing_release_files = sorted(REQUIRED_RELEASE_FILES - {path.name for path in root.iterdir()})
    for name in missing_release_files:
        findings.append({"kind": "missing_release_file", "path": name, "detail": "Required public-release document is missing."})

    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        lower = relative.casefold()
        if lower == "tools/audit_public_release.py":
            continue
        if ".git" in Path(relative).parts:
            findings.append({"kind": "git_metadata", "path": relative, "detail": "A public export must not contain Git history."})
        if ("__pycache__" in Path(relative).parts or ".pytest_cache" in Path(relative).parts
                or path.suffix.casefold() == ".pyc"):
            findings.append({"kind": "generated_cache", "path": relative, "detail": "Remove Python bytecode caches before publishing."})
        suffix = path.suffix.casefold()
        if suffix in PRIVATE_SUFFIXES:
            findings.append({"kind": "private_artifact", "path": relative, "detail": f"Private or generated artifact suffix {suffix}."})
        if suffix in {".f", ".for", ".f90", ".f95", ".f03", ".f08", ".ftn"} and not _is_public_demo_source(relative):
            findings.append({"kind": "non_demo_fortran", "path": relative, "detail": "Only the toy spring demonstration may carry Fortran source."})
        if any(token in lower for token in PRIVATE_NAME_TOKENS):
            findings.append({"kind": "private_name", "path": relative, "detail": "Private solver or local-registration name detected."})
        if suffix in TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8", errors="replace")
            for line_number, line in enumerate(text.splitlines(), 1):
                lowered = line.casefold()
                if any(token in lowered for token in PRIVATE_PATH_TOKENS):
                    findings.append({"kind": "private_path", "path": relative, "line": str(line_number), "detail": line.strip()[:240]})
                if "parent_root" in lowered and DRIVE_PATH.search(line):
                    findings.append({"kind": "absolute_profile_path", "path": relative, "line": str(line_number), "detail": "A public profile must use relative paths."})
                if any(pattern.search(line) for pattern in CREDENTIAL_PATTERNS):
                    findings.append({"kind": "credential_pattern", "path": relative, "line": str(line_number), "detail": "Credential-like text detected."})
    return {"status": "PASS" if not findings else "FAIL", "root": str(root), "findings": findings,
            "files_scanned": sum(1 for item in root.rglob("*") if item.is_file()),
            "history_scope": "Git history is not included in this directory audit. Use --git-root for the separate history check."}


def audit_git_history(repository: Path) -> dict[str, Any]:
    """Run a lightweight independent path and commit-message history check."""
    repository = Path(repository).resolve()
    if not (repository / ".git").exists():
        return {"status": "SKIPPED", "root": str(repository), "reason": "No .git directory was found."}
    try:
        listed = subprocess.run(["git", "-C", str(repository), "rev-list", "--objects", "--all"],
                                capture_output=True, text=True, encoding="utf-8", check=False, timeout=60)
        messages = subprocess.run(["git", "-C", str(repository), "log", "--all", "--format=%B"],
                                  capture_output=True, text=True, encoding="utf-8", check=False, timeout=60)
    except (OSError, subprocess.SubprocessError) as error:
        return {"status": "FAIL", "root": str(repository), "reason": str(error)}
    findings = []
    for line in listed.stdout.splitlines():
        parts = line.split(" ", 1)
        candidate = parts[1].casefold() if len(parts) == 2 else ""
        if any(token in candidate for token in PRIVATE_NAME_TOKENS):
            findings.append({"kind": "historical_private_name", "value": candidate})
    for line_number, line in enumerate(messages.stdout.splitlines(), 1):
        if any(token in line.casefold() for token in PRIVATE_PATH_TOKENS):
            findings.append({"kind": "historical_private_path", "line": str(line_number), "detail": line[:240]})
    return {"status": "PASS" if not findings else "FAIL", "root": str(repository), "findings": findings,
            "scope": "Git object paths and commit messages. Historical blob contents require a separate repository review."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="Assembled public export directory")
    parser.add_argument("--report", type=Path, help="Optional JSON report path")
    parser.add_argument("--git-root", type=Path, help="Optional source repository for independent history checks")
    args = parser.parse_args()
    result = audit_directory(args.root)
    if args.git_root:
        result["git_history"] = audit_git_history(args.git_root)
        if result["git_history"]["status"] == "FAIL":
            result["status"] = "FAIL"
    if args.report:
        args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
