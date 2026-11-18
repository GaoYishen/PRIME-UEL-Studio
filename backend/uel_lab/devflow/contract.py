"""Explicit copied-file provenance and immutable development snapshots."""
from __future__ import annotations

import difflib
import hashlib
import json
import math
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
import os
from typing import Any

ARCHIVE_ROOT = Path(os.environ.get("PRIME_UEL_ARCHIVE_ROOT", Path.home() / "PRIME_UEL_Projects" / "archive" / "statecontracts"))
CANONICAL_V6 = Path(os.environ.get("PRIME_UEL_CANONICAL", Path(__file__).resolve().parents[3] / "examples" / "spring" / "spring.f90"))
CONFIG = "uel-dev.json"
PROVENANCE = "provenance.json"
SAFE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,79}\Z")
REQUIRED_GATES = ["tangent", "zero_blocks", "conservation", "accepted_state", "phase_window", "newton"]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    def invalid(value: str) -> None:
        raise ValueError(f"Non-finite JSON number: {value}")
    def finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"Non-finite JSON number: {value}")
        return number
    return json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=invalid, parse_float=finite_float)


def write_json(path: Path, data: Any, *, exclusive: bool = False) -> None:
    with path.open("x" if exclusive else "w", encoding="utf-8") as handle:
        handle.write(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def inside(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative.strip() or Path(relative).is_absolute():
        raise ValueError(f"Expected a relative file path: {relative!r}")
    root = root.resolve()
    target = (root / relative).resolve()
    if target == root or not target.is_relative_to(root):
        raise ValueError(f"Path escapes its root: {relative}")
    return target


def _name(root: Path, value: str) -> str:
    return inside(root, value).relative_to(root.resolve()).as_posix()


def _is_managed_path(name: str) -> bool:
    normalized = name.casefold()
    return (normalized.split("/")[0] in {"runs", "snapshots", "reports"}
            or normalized in {CONFIG, PROVENANCE, ".snapshot.lock"})


def snapshot_storage(case: Path, snapshot_id: str) -> Path:
    if not isinstance(snapshot_id, str) or not re.fullmatch(r"[0-9]{6}", snapshot_id):
        raise ValueError("Snapshot id must have six digits")
    return inside(case, f"snapshots/{snapshot_id}/files")


def load_case(case: Path) -> dict[str, Any]:
    config = read_json(case / CONFIG)
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise ValueError("uel-dev.json must be a schema_version=1 object")
    return config


def get_snapshot(case: Path) -> dict[str, Any]:
    snapshots = sorted(inside(case, "snapshots").glob("[0-9][0-9][0-9][0-9][0-9][0-9].json"))
    if not snapshots:
        raise ValueError("No immutable snapshot found")
    snapshot = read_json(inside(case, f"snapshots/{snapshots[-1].name}"))
    if snapshot.get("snapshot_id") != snapshots[-1].stem:
        raise ValueError("Snapshot metadata id differs from its filename")
    snapshot_storage(case, snapshot["snapshot_id"])
    if not isinstance(snapshot.get("files"), dict) or not snapshot["files"]:
        raise ValueError("Empty snapshot")
    return snapshot


def _write_snapshot(case: Path, paths: list[str], number: int, note: str,
                    changes: list[dict[str, Any]]) -> dict[str, Any]:
    snapshot_id = f"{number:06d}"
    storage = snapshot_storage(case, snapshot_id)
    storage.mkdir(parents=True, exist_ok=False)
    fingerprints = {}
    for relative in sorted(paths):
        source = inside(case, relative)
        copied = inside(storage, relative)
        copied.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, copied)
        fingerprints[relative] = sha256(copied)
        if fingerprints[relative] != sha256(source):
            raise ValueError(f"File changed while snapshotting: {relative}")
    snapshot = {"snapshot_id": snapshot_id, "created_at": utc_now(), "note": note,
                "files": fingerprints, "changes": changes}
    write_json(inside(case, f"snapshots/{snapshot_id}.json"), snapshot, exclusive=True)
    return snapshot


def create_case(parent: Path, destination: Path, source: str, inp: str,
                includes: list[str] | None = None, *, canonical: Path = CANONICAL_V6,
                archive_root: Path = ARCHIVE_ROOT, purpose: str = "diagnostic") -> Path:
    """Copy only named files; never overwrite or edit an existing case."""
    parent, destination, archive_root = parent.resolve(), destination.resolve(), archive_root.resolve()
    if not parent.is_dir() or destination.exists():
        raise ValueError("Parent must exist and destination must be new")
    if not destination.is_relative_to(archive_root) or destination == archive_root:
        raise ValueError("Destination must be inside the declared archive root")
    if not destination.name.startswith("statecontract") or re.match(r"statecontract77[n-q](?:_|$)", destination.name, re.I):
        raise ValueError("Use a new statecontract name outside legacy 77n-77q")
    if destination.is_relative_to(parent) or parent.is_relative_to(destination):
        raise ValueError("Parent and copied case must be disjoint directories")
    if purpose not in {"diagnostic", "validation"}:
        raise ValueError("purpose must be diagnostic or validation")
    canonical = canonical.resolve()
    if not canonical.is_file():
        raise ValueError(f"Canonical V6 missing: {canonical}")
    paths = sorted({_name(parent, value) for value in [source, inp, *(includes or [])]})
    if len({value.casefold() for value in paths}) != len(paths):
        raise ValueError("Copied paths must be unique on case-insensitive Windows filesystems")
    if any(not inside(parent, value).is_file() for value in paths):
        raise ValueError("Every declared parent file must exist")
    if any(_is_managed_path(value) for value in paths):
        raise ValueError("Managed metadata paths cannot be copied inputs")
    provenance = {"schema_version": 1, "created_at": utc_now(), "archive_root": str(archive_root),
                  "parent_root": str(parent), "parent_files": {value: sha256(inside(parent, value)) for value in paths},
                  "canonical_v6": {"path": str(canonical), "sha256": sha256(canonical)}}
    destination.mkdir(parents=True, exist_ok=False)
    for value in paths:
        target = inside(destination, value)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(inside(parent, value), target)
        if sha256(target) != provenance["parent_files"][value]:
            raise ValueError(f"Parent changed during copying: {value}")
    write_json(destination / PROVENANCE, provenance, exclusive=True)
    _write_snapshot(destination, paths, 1, "Byte-identical explicit parent copy", [])
    config = {"schema_version": 1, "case_id": destination.name, "purpose": purpose,
              "source": _name(parent, source), "inp": _name(parent, inp),
              "hypothesis": "", "expected_observation": "", "stop_condition": "",
              "case_conditions": {}, "parameter_changes": [],
              "scope": {"paper": "paper1", "element_families": [], "active_physics": [],
                        "state_variables": [], "residual_rows": [], "tangent_columns": [],
                        "property_delta": "none; byte-identical copy", "source_delta": "byte-identical copy",
                        "predecessor_gate": None},
              "steps": [], "assertions": [], "required_gates": REQUIRED_GATES if purpose == "validation" else []}
    write_json(destination / CONFIG, config, exclusive=True)
    return destination / CONFIG


def validate_recipe(config: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if config.get("purpose") not in {"diagnostic", "validation"}:
        issues.append("purpose must be diagnostic or validation")
    for field in ("case_id", "hypothesis", "expected_observation", "stop_condition"):
        if not isinstance(config.get(field), str) or not config[field].strip():
            issues.append(f"Fill {field} before execution")
    if not isinstance(config.get("case_conditions"), dict) or not config["case_conditions"]:
        issues.append("Declare case_conditions explicitly")
    scope = config.get("scope", {})
    if not isinstance(scope, dict):
        return issues + ["scope must be an object"]
    if scope.get("paper") not in {"paper1", "paper2", "paper3"}:
        issues.append("Declare the paper boundary")
    for field in ("element_families", "active_physics", "state_variables", "residual_rows", "tangent_columns"):
        if not isinstance(scope.get(field), list) or not scope[field]:
            issues.append(f"Declare scope.{field}")
    for variable in scope.get("state_variables", []) if isinstance(scope.get("state_variables"), list) else []:
        if (not isinstance(variable, dict) or not variable.get("name") or not variable.get("role")
            or variable.get("kind") not in {"primary", "internal", "accepted_history", "slow_aging"}
            or variable.get("owner") not in {"bulk", "interphase", "interface"}):
            issues.append("State variables require name, kind, owner and energy/dissipation role")
    for field in ("property_delta", "source_delta"):
        if not isinstance(scope.get(field), str) or not scope[field].strip():
            issues.append(f"Declare scope.{field}")
    steps = config.get("steps")
    if not isinstance(steps, list) or not steps:
        return issues + ["Declare at least one execution step"]
    ids: set[str] = set()
    jobs: set[str] = set()
    for step in steps:
        if not isinstance(step, dict) or not SAFE_ID.fullmatch(str(step.get("id", ""))):
            issues.append("Steps require a safe unique id")
            continue
        if step["id"] in ids:
            issues.append("Duplicate step id")
        ids.add(step["id"])
        timeout = step.get("timeout_seconds")
        try:
            valid_timeout = not isinstance(timeout, bool) and isinstance(timeout, (int, float)) and math.isfinite(float(timeout)) and timeout > 0
        except OverflowError:
            valid_timeout = False
        if not valid_timeout:
            issues.append(f"{step['id']}: timeout_seconds must be finite and positive")
        if step.get("kind") == "command":
            argv = step.get("argv")
            if not isinstance(argv, list) or not argv or any(not isinstance(arg, str) or not arg for arg in argv):
                issues.append(f"{step['id']}: argv must be a nonempty string array")
            elif re.fullmatch(r"(?:abaqus|abq\d+)(?:\.bat|\.cmd|\.exe)?", Path(argv[0]).name, re.I):
                issues.append(f"{step['id']}: use kind=abaqus so CPU, restart and completion rules apply")
            if not isinstance(step.get("expected_files"), list) or not step["expected_files"]:
                issues.append(f"{step['id']}: name fresh expected_files; exit code alone is insufficient")
        elif step.get("kind") == "abaqus":
            job = str(step.get("job", ""))
            if not SAFE_ID.fullmatch(job) or job in jobs:
                issues.append(f"{step['id']}: use a safe unique job name")
            jobs.add(job)
            if step.get("stage") not in {"datacheck", "run"}:
                issues.append(f"{step['id']}: stage must be datacheck or run")
            launcher = step.get("launcher", ["abaqus"])
            if not isinstance(launcher, list) or len(launcher) != 1 or not isinstance(launcher[0], str) or not launcher[0]:
                issues.append(f"{step['id']}: launcher is a one-element executable path array")
            if "extra_args" in step:
                issues.append("Free Abaqus extra_args are not supported")
        else:
            issues.append(f"{step['id']}: kind must be command or abaqus")
    assertions = config.get("assertions", [])
    if not isinstance(assertions, list):
        issues.append("assertions must be a list")
    else:
        assertion_ids = [item.get("id") for item in assertions if isinstance(item, dict)]
        if len(assertion_ids) != len(assertions) or any(not name for name in assertion_ids) or len(set(assertion_ids)) != len(assertion_ids):
            issues.append("Assertions need unique nonempty ids")
    gates = config.get("required_gates", [])
    if not isinstance(gates, list) or any(not isinstance(gate, str) or not gate for gate in gates):
        issues.append("required_gates must be a string array")
    elif config.get("purpose") == "validation":
        if not gates or not set(REQUIRED_GATES).issubset(gates):
            issues.append("Validation requires tangent, zero_blocks, conservation, accepted_state, phase_window and newton gates")
        covered = {item.get("gate") for item in assertions if isinstance(item, dict)} if isinstance(assertions, list) else set()
        if not set(gates).issubset(covered):
            issues.append("Every required gate needs a declared assertion")
        for gate, maximum in (("tangent", 1e-5), ("zero_blocks", 1e-10)):
            numeric_checks = []
            for assertion in assertions:
                if not isinstance(assertion, dict) or assertion.get("gate") != gate:
                    continue
                threshold = assertion.get("expected")
                if isinstance(threshold, bool) or not isinstance(threshold, (float, int)):
                    continue
                numeric_checks.append(assertion)
                if assertion.get("op") not in {"le", "abs_le"} or not 0 <= threshold <= maximum:
                    issues.append(f"{gate} requires a nonnegative error bound at most {maximum}")
            if not numeric_checks:
                issues.append(f"{gate} requires numeric error evidence, not only a named Boolean gate")
    return issues


def _include_issues(case: Path, files: dict[str, str]) -> list[str]:
    issues = []
    for relative in files:
        path = inside(case, relative)
        if path.suffix.lower() not in {".inp", ".f", ".for", ".f90", ".inc"} or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix.lower() == ".inp":
            if re.search(r"(?im)^\s*\*restart\b", text):
                issues.append(f"Restart keyword is outside this tool's gate: {relative}")
            directives = re.findall(r"(?im)^\s*\*include\b([^\r\n]*)", text)
            names = []
            for directive in directives:
                match = re.search(r"\binput\s*=\s*(\"[^\"]+\"|'[^']+'|[^,]+)", directive, re.I)
                if not match:
                    issues.append(f"Unsupported/multiline INCLUDE: {relative}")
                else:
                    names.append(match.group(1).strip().strip("\"'"))
        else:
            names = re.findall(r"(?im)^\s*(?:\d+\s+)?include\s*['\"]([^'\"]+)['\"]", text)
            includes = re.findall(r"(?im)^\s*(?:\d+\s+)?include\b[^\r\n]*", text)
            preprocessor = re.findall(r"(?im)^\s*#\s*include\b[^\r\n]*", text)
            preprocessor_names = re.findall(r"(?im)^\s*#\s*include\s*[<\"]([^>\"]+)[>\"]", text)
            if len(includes) != len(names) or len(preprocessor) != len(preprocessor_names):
                issues.append(f"Unsupported/multiline/macro Fortran INCLUDE: {relative}")
            names.extend(preprocessor_names)
        for name in names:
            if path.suffix.lower() != ".inp" and name.lower() in {"aba_param.inc", "vaba_param.inc"}:
                continue
            try:
                if Path(name).is_absolute():
                    raise ValueError("Absolute include")
                target = (path.parent / name).resolve()
                if not target.is_relative_to(case.resolve()) or target.relative_to(case.resolve()).as_posix() not in files:
                    raise ValueError("Untracked or escaping include")
            except ValueError:
                issues.append(f"Undeclared/absolute/escaping include in {relative}: {name}")
    return issues


def preflight_case(case: Path, include_recipe: bool = True) -> dict[str, Any]:
    issues: list[str] = []
    hashes: dict[str, Any] = {}
    try:
        case = case.resolve()
        provenance = read_json(case / PROVENANCE)
        archive = Path(provenance["archive_root"]).resolve()
        parent = Path(provenance["parent_root"]).resolve()
        if not case.is_relative_to(archive) or case == archive or case.is_relative_to(parent) or parent.is_relative_to(case):
            issues.append("Copied-case/archive/parent path contract violated")
        snapshot = get_snapshot(case)
        for relative, expected in provenance["parent_files"].items():
            path = inside(parent, relative)
            if not path.is_file() or sha256(path) != expected:
                issues.append(f"Immutable parent changed or missing: {path}")
        canonical = provenance["canonical_v6"]
        if not Path(canonical["path"]).is_file() or sha256(Path(canonical["path"])) != canonical["sha256"]:
            issues.append("Canonical V6 changed or missing")
        for relative, expected in snapshot["files"].items():
            path = inside(case, relative)
            frozen = inside(snapshot_storage(case, snapshot["snapshot_id"]), relative)
            hashes[relative] = sha256(path) if path.is_file() else None
            if hashes[relative] != expected:
                issues.append(f"Unregistered copied-file change: {relative}")
            if not frozen.is_file() or sha256(frozen) != expected:
                issues.append(f"Frozen snapshot changed: {relative}")
        issues.extend(_include_issues(case, snapshot["files"]))
        config = load_case(case)
        for field in ("source", "inp"):
            if config.get(field) not in snapshot["files"]:
                issues.append(f"{field} must name a tracked copied file")
        if include_recipe:
            issues.extend(validate_recipe(config))
            gate = config.get("scope", {}).get("predecessor_gate")
            if not isinstance(gate, dict):
                issues.append("Declare a predecessor gate or justified diagnostic not_required")
            elif gate.get("not_required") is True:
                if config.get("purpose") != "diagnostic" or any(s.get("kind") == "abaqus" for s in config.get("steps", [])) or not gate.get("reason"):
                    issues.append("not_required is restricted to explained offline diagnostics")
            else:
                from .evidence import evaluate_assertions
                path = Path(gate.get("file", ""))
                if not path.is_absolute() or not path.is_file() or sha256(path) != gate.get("sha256"):
                    issues.append("Predecessor gate requires an existing absolute path and pinned SHA-256")
                else:
                    assertion = {"id": "predecessor", "kind": "json", "file": path.name,
                                 "pointer": gate.get("pointer", "/passed"), "op": "eq", "expected": True}
                    if not all(item["passed"] for item in evaluate_assertions(path.parent, [assertion])):
                        issues.append("Predecessor gate did not explicitly pass")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        issues.append(f"Invalid or missing case contract: {exc}")
    return {"passed": not issues, "issues": issues, "hashes": hashes}


def snapshot_case(case: Path, allowed: list[str], note: str) -> dict[str, Any]:
    case = case.resolve()
    if not note.strip() or not allowed:
        raise ValueError("Snapshot requires --note and an explicit --allow list")
    previous = get_snapshot(case)
    # Parent and frozen data must remain intact even when working copies have changed.
    integrity = preflight_case(case, include_recipe=False)
    fatal = [issue for issue in integrity["issues"]
             if not issue.startswith(("Unregistered copied-file change:", "Undeclared/absolute/escaping include"))]
    if fatal:
        raise ValueError("; ".join(fatal))
    allowed_names = {_name(case, value) for value in allowed}
    for name in allowed_names:
        if _is_managed_path(name):
            raise ValueError("Managed metadata cannot be snapshotted as source")
        if not inside(case, name).is_file():
            raise ValueError(f"Allowed file is missing: {name}")
    changed = {name for name, digest in previous["files"].items() if not inside(case, name).is_file() or sha256(inside(case, name)) != digest}
    changed.update(name for name in allowed_names if name not in previous["files"])
    if changed != allowed_names:
        raise ValueError(f"--allow must exactly match actual changes; actual={sorted(changed)}")
    paths = sorted(set(previous["files"]) | allowed_names)
    include_errors = _include_issues(case, dict.fromkeys(paths, ""))
    if include_errors:
        raise ValueError("; ".join(include_errors))
    changes = []
    for name in sorted(changed):
        frozen = inside(snapshot_storage(case, previous["snapshot_id"]), name)
        before = frozen.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True) if frozen.is_file() else []
        after = inside(case, name).read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        changes.append({"path": name, "before_sha256": previous["files"].get(name),
                        "after_sha256": sha256(inside(case, name)),
                        "diff": "".join(difflib.unified_diff(before, after, fromfile=f"before/{name}", tofile=f"after/{name}"))})
    lock = case / ".snapshot.lock"
    handle = lock.open("x", encoding="utf-8")
    try:
        with handle:
            return _write_snapshot(case, paths, int(previous["snapshot_id"]) + 1, note, changes)
    finally:
        # Windows cannot unlink an open lock file.
        lock.unlink(missing_ok=True)
