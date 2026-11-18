"""Run declared checks in a fresh copied workspace, preserving every attempt."""

from __future__ import annotations

import math
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contract import CONFIG, get_snapshot, inside, load_case, preflight_case, sha256, write_json
from .evidence import evaluate_assertions, inspect_abaqus


CLAIM_SCOPE = "Only the declared checks in this copied case; no broader physical qualification."
SAFE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,79}\Z")
_CMD_META = re.compile(r'[&|<>^%\r\n!()"]')
_JOB_OUTPUT_SUFFIXES = {".log", ".msg", ".sta", ".dat", ".lck", ".odb", ".sim", ".prt",
                        ".com", ".res", ".mdl", ".stt", ".pac", ".abq", ".sel", ".ipm", ".023"}


def _argv(step: dict[str, Any], config: dict[str, Any], work: Path) -> list[str]:
    if step["kind"] == "command":
        return [arg.replace("{python}", sys.executable).replace("{work}", str(work)) for arg in step["argv"]]
    job = step["job"]
    if not SAFE_ID.fullmatch(job) or step["stage"] not in {"datacheck", "run"}:
        raise ValueError("Abaqus needs a safe job name and an explicit datacheck/run stage")
    arguments = list(step.get("launcher", ["abaqus"]))
    arguments.extend([f"job={job}", f"input={config['inp']}", f"user={config['source']}",
                      "cpus=1", "interactive", "ask_delete=OFF"])
    if step["stage"] == "datacheck":
        arguments.append("datacheck")
    return arguments


def _process_argv(arguments: list[str]) -> list[str]:
    """Batch launchers need cmd.exe; never give it expandable shell syntax."""
    if os.name == "nt":
        arguments = [shutil.which(arguments[0]) or arguments[0], *arguments[1:]]
    if os.name == "nt" and Path(arguments[0]).suffix.lower() in {".bat", ".cmd"}:
        if any(_CMD_META.search(arg) for arg in arguments):
            raise ValueError("Batch launcher arguments contain cmd.exe metacharacters")
        return ["cmd.exe", "/d", "/v:off", "/s", "/c", subprocess.list2cmdline(arguments)]
    return arguments


def _kill_tree(process: subprocess.Popen[Any]) -> None:
    if os.name == "nt":
        try:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           capture_output=True, check=False, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _run_process(arguments: list[str], work: Path, log: Path, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    launched = _process_argv(arguments)
    process_command: list[str] | str = launched
    if launched[:5] == ["cmd.exe", "/d", "/v:off", "/s", "/c"]:
        # cmd.exe uses its own quote rules. Passing this as a list would make
        # Python backslash-escape the inner quotes for the MS C runtime instead.
        process_command = subprocess.list2cmdline(launched[:-1]) + ' "' + launched[-1] + '"'
    process: subprocess.Popen[Any] | None = None
    timed_out = False
    with log.open("wb") as output:
        try:
            process = subprocess.Popen(process_command, cwd=work, stdout=output, stderr=subprocess.STDOUT,
                                       shell=False, start_new_session=os.name != "nt")
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            assert process is not None
            _kill_tree(process)
            code = process.returncode
        except BaseException:
            if process is not None and process.poll() is None:
                _kill_tree(process)
            raise
    return {"argv": arguments, "process_argv": launched, "return_code": code,
            "timed_out": timed_out, "elapsed_seconds": round(time.monotonic() - started, 6),
            "console_log": str(log)}


def _expected_paths(work: Path, step: dict[str, Any]) -> list[Path]:
    expected = step.get("expected_files", [])
    if not isinstance(expected, list) or any(not isinstance(name, str) for name in expected):
        raise ValueError("expected_files must be an array of relative file paths")
    return [inside(work, name) for name in expected]


def _run_step(step: dict[str, Any], config: dict[str, Any], work: Path, run: Path) -> dict[str, Any]:
    started = time.monotonic()
    result: dict[str, Any] = {"id": step["id"], "kind": step["kind"], "status": "FAILED"}
    try:
        expected = _expected_paths(work, step)
        stale = [str(path.relative_to(work)) for path in expected if path.exists()]
        if step["kind"] == "abaqus":
            stale.extend(path.name for path in work.glob(f"{step['job']}.*") if path.suffix.lower() in _JOB_OUTPUT_SUFFIXES)
        if stale:
            raise ValueError(f"Expected outputs or job artifacts already exist before step: {sorted(set(stale))}")
        result.update(_run_process(_argv(step, config, work), work, run / f"{step['id']}.console.log",
                                   float(step["timeout_seconds"])))
        if result["timed_out"]:
            result["status"] = "TIMED_OUT"
            result["detail"] = "Step exceeded its declared deadline; process tree was terminated"
            return result
        # Resolve again after the process: a produced directory must not redirect
        # managed output reads through a symlink outside the run workspace.
        expected = _expected_paths(work, step)
        missing = [str(path.relative_to(work)) for path in expected if not path.is_file() or path.stat().st_size == 0]
        result["outputs"] = [{"file": str(path.relative_to(work)), "sha256": sha256(path)}
                             for path in expected if path.is_file()]
        if result["return_code"] != 0:
            result["detail"] = "Process returned a nonzero exit code"
            return result
        if missing:
            result["detail"] = f"Missing or empty fresh expected outputs: {missing}"
            return result
        if step["kind"] == "abaqus":
            result["stage"] = step["stage"]
            result["job"] = step["job"]
            result["runtime"] = inspect_abaqus(work, step["job"], step["stage"])
            if result["runtime"]["status"] != "COMPLETED":
                result["detail"] = "Abaqus completion was not established by current-job evidence"
                return result
        result["status"] = "COMPLETED"
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result["detail"] = str(exc)
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - started, 6)
    return result


def _extra_issues(config: dict[str, Any], case: Path) -> list[str]:
    """Check runtime-only file paths before even creating a run directory."""
    issues = []
    tracked = get_snapshot(case)["files"]
    try:
        inside(case, "runs")
    except ValueError as exc:
        issues.append(str(exc))
    steps = config.get("steps", [])
    for step in steps:
        try:
            _expected_paths(case, step)
            _process_argv(_argv(step, config, case))
            timeout = step["timeout_seconds"]
            if isinstance(timeout, bool) or not math.isfinite(float(timeout)) or float(timeout) <= 0:
                raise ValueError("Timeout must be finite and positive")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            issues.append(f"{step.get('id', 'unknown')}: {exc}")
    for assertion in config.get("assertions", []):
        try:
            path = inside(case, assertion["file"])
            if path.relative_to(case).as_posix() in tracked:
                issues.append(f"Assertion {assertion['id']} points to a snapshotted input, not fresh run evidence")
        except (ValueError, KeyError, TypeError) as exc:
            issues.append(f"Invalid assertion output path: {exc}")
    return issues


def _gate_issues(config: dict[str, Any], checks: list[dict[str, Any]], steps: list[dict[str, Any]]) -> list[str]:
    if config["purpose"] != "validation":
        return []
    issues = []
    if not any(step.get("kind") == "abaqus" and step.get("stage") == "run"
               and step.get("status") == "COMPLETED" for step in steps):
        issues.append("Validation requires a completed Abaqus run; datacheck or offline exit code is insufficient")
    for gate in config.get("required_gates", []):
        indices = [index for index, assertion in enumerate(config.get("assertions", [])) if assertion.get("gate") == gate]
        if not indices or any(index >= len(checks) or checks[index].get("passed") is not True for index in indices):
            issues.append(f"Required gate not established: {gate}")
    return issues


def run_workflow(case: Path, execute: bool = False) -> dict[str, Any]:
    """Plan without side effects by default; execute only a valid copied contract."""
    case = Path(case).resolve()
    preflight = preflight_case(case)
    result: dict[str, Any] = {"case_id": case.name, "status": "BLOCKED", "execute": execute,
                              "preflight": preflight, "steps": [], "assertions": [],
                              "claim_scope": CLAIM_SCOPE}
    if not preflight["passed"]:
        return result
    try:
        config = load_case(case)
        snapshot = get_snapshot(case)
        extra = _extra_issues(config, case)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        preflight["passed"] = False
        preflight["issues"].append(str(exc))
        return result
    if extra:
        preflight["passed"] = False
        preflight["issues"].extend(extra)
        return result
    result["case_id"] = config["case_id"]
    result["purpose"] = config["purpose"]
    result["snapshot_id"] = snapshot["snapshot_id"]
    result["config_sha256"] = sha256(case / CONFIG)
    if not execute:
        result["status"] = "PLANNED"
        result["steps"] = [{"id": step["id"], "argv": _argv(step, config, case / "runs" / "<new-run>" / "work"),
                             "timeout_seconds": step["timeout_seconds"]} for step in config["steps"]]
        return result

    started = time.monotonic()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ_") + uuid.uuid4().hex[:8]
    run = inside(case, f"runs/{run_id}")
    work = run / "work"
    result.update({"run_id": run_id, "run_dir": str(run), "workdir": str(work), "issues": []})
    try:
        work.mkdir(parents=True, exist_ok=False)
        shutil.copy2(case / CONFIG, run / CONFIG)
        if sha256(run / CONFIG) != result["config_sha256"] or load_case(run) != config:
            raise ValueError("Configuration changed while freezing the run")
        write_json(run / "snapshot.json", snapshot)
        for relative, expected in snapshot["files"].items():
            source = inside(case / "snapshots" / snapshot["snapshot_id"] / "files", relative)
            destination = inside(work, relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            if sha256(destination) != expected:
                raise ValueError(f"Snapshot changed during staging: {relative}")
        for step in config["steps"]:
            outcome = _run_step(step, config, work, run)
            result["steps"].append(outcome)
            if outcome["status"] != "COMPLETED":
                break
        result["assertions"] = evaluate_assertions(work, config.get("assertions", []))
        after = preflight_case(case)
        result["postflight"] = after
        if not after["passed"]:
            result["issues"].extend(after["issues"])
        if sha256(case / CONFIG) != result["config_sha256"]:
            result["issues"].append("Case configuration changed during execution")
        if get_snapshot(case)["snapshot_id"] != snapshot["snapshot_id"]:
            result["issues"].append("Case snapshot changed during execution")
        for relative, expected in snapshot["files"].items():
            path = inside(work, relative)
            if not path.is_file() or sha256(path) != expected:
                result["issues"].append(f"Staged input changed during execution: {relative}")
        result["issues"].extend(_gate_issues(config, result["assertions"], result["steps"]))
        if any(step["status"] == "TIMED_OUT" for step in result["steps"]):
            result["status"] = "TIMED_OUT"
        elif (result["issues"] or len(result["steps"]) != len(config["steps"])
              or any(step["status"] != "COMPLETED" for step in result["steps"])
              or any(check.get("passed") is not True for check in result["assertions"])):
            result["status"] = "FAILED"
        else:
            result["status"] = "DIAGNOSTIC_COMPLETE" if config["purpose"] == "diagnostic" else "VALIDATION_CHECKS_PASSED"
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result["status"] = "FAILED"
        result["issues"].append(str(exc))
    except KeyboardInterrupt:
        result["status"] = "INTERRUPTED"
        result["issues"].append("User interrupted the run; no pass status is assigned")
    finally:
        result["elapsed_seconds"] = round(time.monotonic() - started, 6)
        if run.is_dir():
            write_json(run / "result.json", result)
    return result
