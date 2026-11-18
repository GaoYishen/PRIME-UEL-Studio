"""Source-bound model cards and named input generation for PRIME UEL Studio."""
from __future__ import annotations

import csv
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
from typing import Any


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> Any:
    def reject(value: str) -> None:
        raise ValueError(f"Non-finite JSON constant: {value}")

    def finite(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"Non-finite JSON number: {value}")
        return number

    return json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=reject, parse_float=finite)


def _signature(profile: dict[str, Any]) -> str:
    payload = {key: value for key, value in profile.items() if not key.startswith("_")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _inside(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative.strip() or Path(relative).is_absolute():
        raise ValueError(f"Expected a relative registered path: {relative!r}")
    target = (root.resolve() / relative).resolve()
    if target == root.resolve() or not target.is_relative_to(root.resolve()):
        raise ValueError(f"Registered path escapes the model parent: {relative}")
    return target


def _relative(root: Path, value: str) -> str:
    return _inside(root, value).relative_to(root.resolve()).as_posix()


def _absolute(base: Path, value: str) -> str:
    path = Path(value)
    return str((path if path.is_absolute() else base / path).resolve())


def load_profile(path: Path) -> dict[str, Any]:
    """Load and pin a model card; relative asset paths belong to the card."""
    path = Path(path).resolve()
    original_hash = _sha(path)
    profile = _json(path)
    if not isinstance(profile, dict) or profile.get("schema", profile.get("schema_version")) != 1:
        raise ValueError("Model card must be a schema=1 object")
    for key in ("parent_root", "canonical", "archive_root"):
        if key in profile:
            if not isinstance(profile[key], str) or not profile[key].strip():
                raise ValueError(f"Model card {key} must be a path")
            profile[key] = _absolute(path.parent, profile[key])
    initializer = profile.get("initializer", {})
    if not isinstance(initializer, dict):
        raise ValueError("initializer must be an object")
    for key in ("dll", "material"):
        asset = initializer.get(key)
        if asset is not None:
            if not isinstance(asset, dict) or not isinstance(asset.get("path"), str):
                raise ValueError(f"initializer.{key} requires a registered path")
            asset["path"] = _absolute(path.parent, asset["path"])
    if _sha(path) != original_hash:
        raise ValueError("Model card changed while loading")
    profile.update(_profile_path=str(path), _profile_sha256=original_hash)
    profile["_loaded_signature"] = _signature(profile)
    return profile


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{name} must be a finite number")
    if isinstance(value, str) and not value.strip():
        raise ValueError(f"{name} cannot be empty")
    try:
        number = float(value)
    except (ValueError, OverflowError) as error:
        raise ValueError(f"{name} must be a finite number") from error
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def validate_parameters(profile: dict[str, Any], values: dict[str, Any]) -> dict[str, int | float]:
    """Validate named GUI values without guessing units or property positions."""
    specs = profile.get("parameters")
    if not isinstance(specs, dict) or not specs or not isinstance(values, dict):
        raise ValueError("Model card parameters and values must be nonempty objects")
    if set(values) != set(specs):
        raise ValueError(f"Parameter names differ from the model card; missing={sorted(set(specs) - set(values))}, "
                         f"unknown={sorted(set(values) - set(specs))}")
    normalized: dict[str, int | float] = {}
    for name, spec in specs.items():
        if not isinstance(spec, dict) or spec.get("type", "float") not in {"float", "int"}:
            raise ValueError(f"Unsupported parameter specification: {name}")
        number = _number(values[name], name)
        if spec.get("type", "float") == "int":
            if not number.is_integer():
                raise ValueError(f"{name} must be an integer")
            number = int(number)
        for bound, comparison in (("minimum", lambda a, b: a >= b), ("maximum", lambda a, b: a <= b),
                                  ("exclusive_minimum", lambda a, b: a > b), ("exclusive_maximum", lambda a, b: a < b)):
            if bound in spec and not comparison(number, _number(spec[bound], f"{name}.{bound}")):
                raise ValueError(f"{name} violates its declared {bound}: {spec[bound]}")
        normalized[name] = number
    return normalized


def _registered_files(profile: dict[str, Any]) -> dict[str, str]:
    parent = Path(profile["parent_root"]).resolve()
    files = profile.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("Model card needs explicit file hashes")
    registered: dict[str, str] = {}
    insensitive: set[str] = set()
    for name, expected in files.items():
        relative = _relative(parent, name)
        if relative.casefold() in insensitive:
            raise ValueError("Registered file paths collide on Windows")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            raise ValueError(f"Invalid SHA-256 for {name}")
        insensitive.add(relative.casefold())
        registered[relative] = expected.lower()
    return registered


def registry_status(profile: dict[str, Any]) -> dict[str, Any]:
    """Match a source by pinned identity; a similar filename is never enough."""
    result: dict[str, Any] = {"matched": False, "issues": [], "source_path": None,
                              "source_sha256": None, "execution_enabled": False}
    issues = result["issues"]
    try:
        if profile.get("schema", profile.get("schema_version")) != 1:
            raise ValueError("Unsupported model card schema")
        if profile.get("mode") not in {"offline", "abaqus", "preview"}:
            raise ValueError("Model card mode must be offline, abaqus or preview")
        if not isinstance(profile.get("id"), str) or not profile["id"].strip():
            raise ValueError("Model card id is missing")
        if not profile.get("_profile_path") or not profile.get("_profile_sha256"):
            raise ValueError("Load the registered model card before using it")
        if _sha(Path(profile["_profile_path"])) != profile["_profile_sha256"]:
            issues.append("Model card file changed; reload it")
        if _signature(profile) != profile.get("_loaded_signature"):
            issues.append("Loaded model card was changed in memory; reload it")
        parent = Path(profile["parent_root"]).resolve()
        if not parent.is_dir():
            raise ValueError("Registered parent directory is missing")
        registered = _registered_files(profile)
        source = _relative(parent, profile["source"])
        if Path(source).suffix.casefold() not in {".f", ".for", ".f90", ".f95", ".f03", ".f08", ".ftn"}:
            raise ValueError("Register a Fortran source file, not a filename alias")
        if source not in registered:
            raise ValueError("Fortran source is not registered")
        path = _inside(parent, source)
        result.update(source_path=str(path), source_sha256=_sha(path) if path.is_file() else None)
        required = [source]
        if profile["mode"] != "preview":
            required.append(_relative(parent, profile["inp"]))
            includes = profile.get("includes", [])
            if not isinstance(includes, list):
                raise ValueError("includes must be a list")
            required.extend(_relative(parent, item) for item in includes)
        if not set(required).issubset(registered):
            raise ValueError("Source, INP and every include must have an explicit registered hash")
        for relative, expected in registered.items():
            asset = _inside(parent, relative)
            if not asset.is_file() or _sha(asset) != expected:
                issues.append(f"Registered file changed or missing: {relative}")
        initializer = profile.get("initializer", {})
        for name in ("dll", "material"):
            asset = initializer.get(name)
            if asset is not None:
                path = Path(asset["path"])
                expected = asset.get("sha256")
                if not isinstance(expected, str) or not path.is_file() or _sha(path) != expected.lower():
                    issues.append(f"Initializer {name} changed or missing")
        execution = profile.get("execution", {})
        if not isinstance(execution, dict):
            raise ValueError("execution must be an object")
        if "enabled" in execution and type(execution["enabled"]) is not bool:
            raise ValueError("execution.enabled must be a Boolean")
        if profile["mode"] != "abaqus" and any(step.get("kind") == "abaqus" for step in execution.get("steps", []) if isinstance(step, dict)):
            raise ValueError("Abaqus steps require an abaqus model card and its canonical archive")
        result["matched"] = not issues
        result["execution_enabled"] = not issues and profile["mode"] != "preview" and execution.get("enabled") is True
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        issues.append(str(error))
    return result


def _binding_changes(profile: dict[str, Any], values: dict[str, int | float],
                     initial: dict[str, Any], *, compile_only: bool = False) -> dict[str, str]:
    parent = Path(profile["parent_root"])
    files = _registered_files(profile)
    bindings = profile.get("bindings", [])
    if not isinstance(bindings, list):
        raise ValueError("bindings must be a list")
    changed: dict[str, str] = {}
    tokens: set[tuple[str, str]] = set()
    mapped_parameters: set[str] = set()
    initial_bindings = False
    forbidden = {".for", ".f", ".f90", ".f95", ".f03", ".f08", ".inc", ".py", ".pyw",
                 ".c", ".cpp", ".h", ".dll", ".exe"}
    for binding in bindings:
        if not isinstance(binding, dict):
            raise ValueError("Every binding must be an object")
        relative = _relative(parent, binding["file"])
        if relative not in files:
            raise ValueError(f"Binding file is not registered: {relative}")
        if Path(relative).suffix.casefold() in forbidden or relative == _relative(parent, profile["source"]):
            raise ValueError("GUI parameter bindings cannot rewrite program source or binaries")
        token = binding.get("token")
        if not isinstance(token, str) or not token:
            raise ValueError("A named binding requires a nonempty token")
        identity = relative.casefold(), token
        if identity in tokens:
            raise ValueError(f"Duplicate binding token: {relative}: {token}")
        tokens.add(identity)
        key = binding.get("value")
        if not isinstance(key, str):
            raise ValueError("A binding value must name a parameter or initial value")
        if key.startswith("initial."):
            if compile_only:
                continue
            number = _number(initial["values"][key[len("initial."):]], key)
            initial_bindings = True
        else:
            parameter = key.removeprefix("parameters.")
            number = _number(values[parameter], key)
            mapped_parameters.add(parameter)
        text = changed.get(relative)
        if text is None:
            text = _inside(parent, relative).read_text(encoding="utf-8")
        if text.count(token) != 1:
            raise ValueError(f"Binding token must appear exactly once: {relative}: {token}")
        replacement = format(number, ".17g")
        changed[relative] = text.replace(token, replacement, 1)
    for name, spec in profile["parameters"].items():
        if name in mapped_parameters:
            continue
        if spec.get("write_mode") == "initial" and (compile_only or initial_bindings):
            continue
        raise ValueError(f"Editable parameter has no declared file binding: {name}")
    return changed


def available_actions(profile: dict[str, Any]) -> dict[str, list[str]]:
    """Expose only model-card actions; filenames never imply an operation."""
    actions = profile.get("actions", {})
    if not isinstance(actions, dict):
        raise ValueError("Model-card actions must be an object")
    steps = profile.get("execution", {}).get("steps", [])
    if not isinstance(steps, list) or any(not isinstance(step, dict) for step in steps):
        raise ValueError("Registered execution steps must be an array of objects")
    step_ids = [step.get("id") for step in steps]
    if any(not isinstance(name, str) or not name for name in step_ids) or len(set(step_ids)) != len(step_ids):
        raise ValueError("Registered execution steps need unique ids")
    result = {}
    for action, names in actions.items():
        if action not in {"all", "compile", "run"}:
            raise ValueError(f"Unsupported model-card action: {action}")
        if (not isinstance(names, list) or not names or any(not isinstance(name, str) for name in names)
                or len(set(names)) != len(names) or not set(names).issubset(step_ids)):
            raise ValueError(f"Action {action} must list unique registered step ids")
        if names != [name for name in step_ids if name in names]:
            raise ValueError(f"Action {action} must preserve the registered execution order")
        if action == "all" and names != step_ids:
            raise ValueError("The all action must cover the complete registered workflow")
        selected = [step for step in steps if step["id"] in names]
        if action == "compile" and any(step.get("role") != "compile" or step.get("kind") != "command" for step in selected):
            raise ValueError("Compile-only actions may contain only role=compile command steps")
        if action == "run" and any(step.get("role") == "compile" for step in selected):
            raise ValueError("Run-only actions cannot include compilation steps")
        result[action] = list(names)
    return result


_SOURCE_SUFFIXES = {".f", ".for", ".f90", ".f95", ".f03", ".f08", ".ftn", ".inc", ".py", ".pyw",
                    ".c", ".cpp", ".h", ".s", ".lib", ".obj", ".dll", ".cmd", ".bat"}


def _source_inputs(profile: dict[str, Any]) -> dict[str, str]:
    """Bind source modules, build helpers and explicit textual includes."""
    registered = _registered_files(profile)
    parent = Path(profile["parent_root"])
    sources = {name for name in registered if Path(name).suffix.casefold() in _SOURCE_SUFFIXES}
    sources.add(_relative(parent, profile["source"]))
    pending = list(sources)
    while pending:
        relative = pending.pop()
        if Path(relative).suffix.casefold() in {".dll", ".lib", ".obj"}:
            continue
        path = _inside(parent, relative)
        text = path.read_text(encoding="utf-8", errors="replace")
        names = re.findall(r"(?im)^\s*(?:\d+\s+)?include\s*['\"]([^'\"]+)['\"]", text)
        names += re.findall(r"(?im)^\s*#\s*include\s*[<\"]([^>\"]+)[>\"]", text)
        for name in names:
            if name.casefold() in {"aba_param.inc", "vaba_param.inc"}:
                continue
            included = (path.parent / name).resolve()
            if not included.is_relative_to(parent.resolve()):
                raise ValueError(f"Source include escapes registered parent: {relative}: {name}")
            target = included.relative_to(parent.resolve()).as_posix()
            if target not in registered:
                raise ValueError(f"Unregistered source include: {target}")
            if target not in sources:
                sources.add(target)
                pending.append(target)
    return {name: registered[name] for name in sorted(sources)}


def _required_run_artifacts(profile: dict[str, Any], steps: list[dict[str, Any]]) -> list[str]:
    parent = Path(profile["parent_root"])
    registered = _registered_files(profile)
    required = set()
    for step in steps:
        names = step.get("requires", [])
        if not isinstance(names, list) or any(not isinstance(name, str) for name in names):
            raise ValueError("Run-step requires must list relative compiled artifacts")
        required.update(_relative(parent, name) for name in names)
        argv = step.get("argv", [])
        if argv and isinstance(argv[0], str) and argv[0].startswith(("{work}/", "{work}\\")):
            name = _relative(parent, argv[0][len("{work}") + 1:])
            if name not in registered:
                required.add(name)
    return sorted(required)


def _reuse_compile(profile: dict[str, Any], run: Path, required: list[str]) -> dict[str, Any]:
    run = Path(run).resolve()
    if run.is_file() and run.name == "result.json":
        run = run.parent
    result_path, config_path, snapshot_path = run / "result.json", run / "uel-dev.json", run / "snapshot.json"
    result, config, snapshot = _json(result_path), _json(config_path), _json(snapshot_path)
    if result.get("status") != "DIAGNOSTIC_COMPLETE":
        raise ValueError("Reused compilation must have completed successfully")
    if result.get("preflight", {}).get("passed") is not True or result.get("postflight", {}).get("passed") is not True:
        raise ValueError("Reused compilation lacks passing before/after provenance checks")
    if result.get("config_sha256") != _sha(config_path):
        raise ValueError("Reused compilation configuration changed")
    if Path(result.get("run_dir", "")).resolve() != run:
        raise ValueError("Reused compilation directory differs from its recorded owner")
    work = (run / "work").resolve()
    if not work.is_relative_to(run) or Path(result.get("workdir", "")).resolve() != work:
        raise ValueError("Reused compilation work directory escaped its owner")
    if config.get("studio_action") != "compile":
        raise ValueError("Select a completed compile-only action, not a physical or full-workflow run")
    sources = _source_inputs(profile)
    if config.get("source") != _relative(Path(profile["parent_root"]), profile["source"]):
        raise ValueError("Reused executable belongs to a different Fortran source")
    if config.get("case_conditions", {}).get("studio_source_files") != sources:
        raise ValueError("Reused executable source/dependency hashes differ from the selected model")
    for name, expected in sources.items():
        path = _inside(work, name)
        if snapshot.get("files", {}).get(name) != expected or not path.is_file() or _sha(path) != expected:
            raise ValueError(f"Reused compilation source or dependency changed: {name}")
    declared = config.get("steps", [])
    outcomes = result.get("steps", [])
    if (not declared or any(step.get("role") != "compile" or step.get("kind") != "command" for step in declared)
            or [step.get("id") for step in outcomes] != [step.get("id") for step in declared]
            or any(step.get("status") != "COMPLETED" or step.get("return_code") != 0 for step in outcomes)):
        raise ValueError("Reused run does not contain complete declared compilation evidence")
    current_compile_ids = available_actions(profile).get("compile", [])
    current_compile = [step for step in profile["execution"]["steps"] if step["id"] in current_compile_ids]
    if declared != current_compile:
        raise ValueError("Reused compilation options differ from the selected model card")
    allowed = set()
    for step in profile["execution"]["steps"]:
        if step["id"] in current_compile_ids:
            allowed.update(_relative(Path(profile["parent_root"]), name) for name in step.get("expected_files", []))
    if not set(required).issubset(allowed):
        raise ValueError("Requested executable is not a registered compilation output")
    outputs = {}
    for declaration, outcome in zip(declared, outcomes):
        expected_outputs = {_relative(work, name) for name in declaration.get("expected_files", [])}
        for output in outcome.get("outputs", []):
            name = _relative(work, output.get("file"))
            if name in outputs or name not in expected_outputs:
                raise ValueError("Reused compilation output ownership is ambiguous")
            outputs[name] = output.get("sha256")
    artifacts = []
    for name in required:
        path = _inside(work, name)
        expected = outputs.get(name)
        if not expected or not path.is_file() or _sha(path) != expected:
            raise ValueError(f"Reused compilation artifact is missing or changed: {name}")
        artifacts.append({"file": name, "from": str(path), "sha256": expected})
    return {"run_dir": str(run), "run_id": result.get("run_id"),
            "result_sha256": _sha(result_path), "config_sha256": _sha(config_path),
            "snapshot_sha256": _sha(snapshot_path), "source_files": sources, "artifacts": artifacts}


def _check_initial(profile: dict[str, Any], parameters: dict[str, Any], initial: dict[str, Any]) -> None:
    if not isinstance(initial, dict) or not isinstance(initial.get("checks"), dict) or not initial["checks"]:
        raise ValueError("Compute and check the initial state before generating a case")
    if any(value is not True for value in initial["checks"].values()):
        raise ValueError("Initial state checks have not all passed")
    if initial.get("input_parameters") != parameters:
        raise ValueError("Initial state is stale or lacks a parameter snapshot; recalculate it")
    provenance = initial.get("provenance", {})
    initializer = profile.get("initializer", {})
    if provenance.get("initializer") != initializer.get("kind"):
        raise ValueError("Initial state belongs to a different initialization adapter")
    if initializer.get("kind") == "e1_phase":
        if provenance.get("phase") != initializer.get("phase"):
            raise ValueError("Initial state belongs to a different material phase")
        for asset in ("dll", "material"):
            if provenance.get(asset, {}).get("sha256") != initializer.get(asset, {}).get("sha256"):
                raise ValueError(f"Initial state belongs to a different {asset} registration")
    if not isinstance(initial.get("values"), dict):
        raise ValueError("Initial values are missing")
    json.dumps(initial, allow_nan=False)


def _prepare_case(profile: dict[str, Any], values: dict[str, Any], initial: dict[str, Any] | None,
                  destination: Path, *, action: str = "all", steps: list[dict[str, Any]] | None = None,
                  reuse: dict[str, Any] | None = None) -> dict[str, Any]:
    from uel_lab.devflow.contract import (ARCHIVE_ROOT, CANONICAL_V6, CONFIG, create_case,
                                          preflight_case, snapshot_case,
                                          validate_recipe, write_json)

    status = registry_status(profile)
    if not status["matched"]:
        raise ValueError("; ".join(status["issues"]))
    if not status["execution_enabled"]:
        raise ValueError("This model card has no enabled execution adapter; initial preview only")
    parameters = validate_parameters(profile, values)
    compile_only = action == "compile"
    if compile_only:
        initial = {"status": "not_generated", "values": {}, "checks": {}, "input_parameters": parameters,
                   "scope": "Compile-only action; no initial state or physical result was generated."}
    else:
        _check_initial(profile, parameters, initial)
    changed = _binding_changes(profile, parameters, initial, compile_only=compile_only)
    sources = _source_inputs(profile)
    if set(changed).intersection(sources):
        raise ValueError("Named input bindings cannot rewrite a compiled source dependency")
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError("Case destination already exists; choose a new name")
    parent = Path(profile["parent_root"]).resolve()
    archive = Path(profile.get("archive_root", ARCHIVE_ROOT if profile["mode"] == "abaqus" else destination.parent)).resolve()
    if profile["mode"] == "abaqus" and archive != ARCHIVE_ROOT.resolve():
        raise ValueError("Abaqus cases must use the canonical copied-statecontract archive")
    canonical = Path(profile.get("canonical", CANONICAL_V6)).resolve()
    if profile["mode"] == "abaqus" and canonical != CANONICAL_V6.resolve():
        raise ValueError("Abaqus cases must record the canonical V6 baseline")
    execution = profile["execution"]
    config = {
        "schema_version": 1, "case_id": destination.name, "purpose": "diagnostic",
        "source": _relative(parent, profile["source"]), "inp": _relative(parent, profile["inp"]),
        "hypothesis": profile.get("hypothesis", "Only the declared model-card checks are evaluated."),
        "expected_observation": profile.get("expected_observation", "Every declared output and assertion must be present and pass."),
        "stop_condition": profile.get("stop_condition", "Stop at the first failed process, timeout or assertion."),
        "case_conditions": {"model_card": profile["id"], "parameters": parameters, "initial": deepcopy(initial),
                            "studio_action": action, "studio_source_files": sources, "reused_compile": deepcopy(reuse)},
        "parameter_changes": [{"name": name, "before": spec.get("default"), "after": parameters[name],
                               "unit": spec.get("unit", "")} for name, spec in profile["parameters"].items()],
        "scope": deepcopy(execution.get("scope", {})), "steps": deepcopy(steps if steps is not None else execution.get("steps", [])),
        "assertions": [] if compile_only else deepcopy(execution.get("assertions", [])),
        "required_gates": [] if compile_only else deepcopy(execution.get("required_gates", [])), "studio_action": action
    }
    if compile_only:
        config["hypothesis"] = "Only the explicitly registered compilation steps are executed."
        config["expected_observation"] = "Fresh registered compilation outputs; no initial state or physical result claim."
    errors = validate_recipe(config)
    if errors:
        raise ValueError("Model-card execution contract is incomplete: " + "; ".join(errors))
    registered = _registered_files(profile)
    includes = [name for name in registered if name not in {config["source"], config["inp"]}]
    create_case(parent, destination, config["source"], config["inp"], includes,
                canonical=canonical, archive_root=archive, purpose="diagnostic")
    # Rewrite generated metadata before sealing only the exact copied-file delta.
    write_json(destination / CONFIG, config)
    modified = []
    for relative, replacement in changed.items():
        copied = _inside(destination, relative)
        before = copied.read_text(encoding="utf-8")
        if replacement != before:
            copied.write_text(replacement, encoding="utf-8")
            modified.append(relative)
    if reuse:
        for artifact in reuse["artifacts"]:
            copied = _inside(destination, artifact["file"])
            if copied.exists():
                raise ValueError("Reused artifact collides with a copied model input")
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(artifact["from"], copied)
            if _sha(copied) != artifact["sha256"]:
                raise ValueError("Reused compilation artifact changed during copying")
            modified.append(artifact["file"])
        write_json(destination / "reused_compile.json", reuse, exclusive=True)
        modified.append("reused_compile.json")
    snapshot = None
    if modified:
        snapshot = snapshot_case(destination, modified, f"Studio {action} action: named input bindings and declared reused artifacts; source remains byte-identical")
    if not registry_status(profile)["matched"]:
        raise ValueError(f"Registered model changed during generation; retained diagnostic case: {destination}")
    record = {
        "schema_version": 1, "model_id": profile["id"], "model_label": profile.get("label", profile["id"]),
        "model_card": profile["_profile_path"], "model_card_sha256": profile["_profile_sha256"],
        "parameters": parameters, "initial": deepcopy(initial), "source_sha256": status["source_sha256"],
        "bindings_applied": list(changed), "action": action, "reused_compile": deepcopy(reuse),
        "claim_scope": "Compilation only; no physical qualification." if compile_only else
            "Declared copied-case checks only; not a production or whole-cell qualification."
    }
    project_path = destination / "studio_project.json"
    write_json(project_path, record, exclusive=True)
    checked = preflight_case(destination)
    return {"status": "GENERATED" if checked["passed"] else "BLOCKED", "case": str(destination),
            "config": str(destination / CONFIG), "studio_project": str(project_path),
            "snapshot_id": snapshot["snapshot_id"] if snapshot else "000001", "preflight": checked, "action": action}


def prepare_case(profile: dict[str, Any], values: dict[str, Any], initial: dict[str, Any],
                 destination: Path) -> dict[str, Any]:
    """Preserve the original complete-workflow API."""
    return _prepare_case(profile, values, initial, destination)


def prepare_action_case(profile: dict[str, Any], values: dict[str, Any], destination: Path, *,
                        action: str, initial: dict[str, Any] | None = None,
                        reuse_run: Path | None = None) -> dict[str, Any]:
    """Prepare one explicitly registered operation, without executing any step."""
    actions = available_actions(profile)
    if action not in actions:
        raise ValueError(f"This model card has no registered {action!r} action")
    if action == "all":
        if reuse_run is not None:
            raise ValueError("Complete workflows compile their own artifacts; reuse_run is for run-only actions")
        return prepare_case(profile, values, initial, destination)
    selected = [deepcopy(step) for step in profile["execution"]["steps"] if step["id"] in actions[action]]
    reuse = None
    if action == "run":
        required = _required_run_artifacts(profile, selected)
        if required and reuse_run is None:
            raise ValueError("Run-only action requires a completed compilation run: " + ", ".join(required))
        if reuse_run is not None:
            if not required:
                raise ValueError("This run action does not require compiled artifacts")
            reuse = _reuse_compile(profile, reuse_run, required)
    elif reuse_run is not None:
        raise ValueError("Compilation cannot reuse an old output directory")
    return _prepare_case(profile, values, initial, destination, action=action, steps=selected, reuse=reuse)


def read_history(path: Path) -> dict[str, Any]:
    """Read a finite, rectangular numeric CSV; missing values are not zeros."""
    with Path(path).open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames
        if not columns or any(not name.strip() for name in columns) or len(set(columns)) != len(columns):
            raise ValueError("History CSV needs unique nonempty column names")
        rows = []
        for line, row in enumerate(reader, 2):
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"History CSV row {line} has missing or extra values")
            rows.append({name: _number(row[name], f"row {line}, {name}") for name in columns})
    if len(rows) < 2:
        raise ValueError("History CSV needs at least two numeric rows for a curve")
    return {"columns": list(columns), "rows": rows}
