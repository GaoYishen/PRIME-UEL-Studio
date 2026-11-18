"""Read-only runtime evidence and explicit numeric assertions for copied cases."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from ..runtime import INCREMENT_RE


_FAILURES = (
    ("minimum_increment", re.compile(r"TIME INCREMENT REQUIRED IS LESS THAN THE MINIMUM", re.I)),
    ("too_many_attempts", re.compile(r"TOO MANY ATTEMPTS", re.I)),
    ("segmentation_fault", re.compile(r"SEGMENTATION (?:FAULT|VIOLATION)|ACCESS VIOLATION", re.I)),
    ("fortran_runtime", re.compile(r"forrtl:\s*severe|FORTRAN RUNTIME ERROR", re.I)),
    ("compiler_error", re.compile(r"(?:fatal\s+)?error\s*#\d+|fatal error:|ifort:\s*error|ifx:\s*error", re.I)),
    ("linker_error", re.compile(r"error\s+LNK\d+|undefined reference to|unresolved external symbol", re.I)),
    ("analysis_aborted", re.compile(r"EXITED WITH (?:AN? )?ERRORS?|COMPLETED WITH ERRORS|ANALYSIS (?:HAS NOT BEEN COMPLETED|HAS BEEN TERMINATED|ABORTED)|JOB .+ ABORTED", re.I)),
    ("abaqus_error", re.compile(r"\*\*\*\s*ERROR|ABAQUS ERROR:", re.I)),
)
_WARNINGS = (
    ("zero_pivot", re.compile(r"ZERO PIVOT", re.I)),
    ("numerical_singularity", re.compile(r"NUMERICAL SINGULARITY", re.I)),
    ("negative_eigenvalue", re.compile(r"NEGATIVE EIGENVALUE", re.I)),
    ("solution_diverging", re.compile(r"SOLUTION APPEARS TO BE DIVERGING", re.I)),
)
_SOLVER_COMPLETE = re.compile(
    r"THE ANALYSIS HAS COMPLETED SUCCESSFULLY|ABAQUS/(?:STANDARD|EXPLICIT) COMPLETED SUCCESSFULLY",
    re.I,
)
_SOLVER_START = re.compile(r"BEGIN ABAQUS/(?:STANDARD|EXPLICIT) ANALYSIS", re.I)
_DATA_CHECK = re.compile(r"DATA\s*CHECK", re.I)
_STA_NUMERIC = re.compile(r"^\s*(\d+)\s+(\d+)\s+\d+\s+\d+\s+\d+\s+\d+\s+[-+0-9.EeDd]+")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def inspect_abaqus(workdir: Path, job: str, stage: str = "run") -> dict[str, Any]:
    """Require positive completion evidence; a launcher's exit code is not evidence.

    Numeric rows in STA are reported as clues, rather than a physical acceptance
    claim. Warnings such as a negative eigenvalue are also not terminal failures.
    """
    root = Path(workdir).resolve()
    result: dict[str, Any] = {
        "status": "MISSING", "stage": stage, "job": job,
        "artifacts": {}, "failure_signatures": [],
        "accepted_increment": None, "cutback_count": None,
        "analysis_started": False, "solver_completed": False,
        "job_completed": False, "input_processed": False,
        "datacheck_observed": False,
    }
    if not isinstance(job, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", job):
        result.update(status="FAILED", detail="Job must be a basename without path separators")
        return result
    if stage not in {"run", "datacheck"}:
        result.update(status="FAILED", detail=f"Unsupported runtime evidence stage: {stage}")
        return result
    job_complete = re.compile(r"ABAQUS JOB\s+" + re.escape(job) + r"\s+COMPLETED\b", re.I)
    seen_content = False
    hard_failure = False
    seen_attempts: set[tuple[int, int]] = set()
    saw_increment = False
    for suffix in ("log", "msg", "sta", "dat", "lck"):
        path = root / f"{job}.{suffix}"
        if not path.exists():
            continue
        try:
            if not path.is_file() or not path.resolve().is_relative_to(root):
                raise ValueError("Artifact is not a regular file inside the run directory")
            result["artifacts"][suffix] = {
                "file": str(path), "size": path.stat().st_size, "sha256": _sha256(path),
            }
            if suffix == "lck":
                continue
            with path.open(encoding="utf-8-sig", errors="replace") as handle:
                for number, line in enumerate(handle, 1):
                    text = line.strip()
                    seen_content = seen_content or bool(text)
                    result["analysis_started"] |= bool(_SOLVER_START.search(text))
                    result["solver_completed"] |= bool(_SOLVER_COMPLETE.search(text))
                    result["job_completed"] |= bool(job_complete.search(text))
                    result["input_processed"] |= "END ANALYSIS INPUT FILE PROCESSOR" in text.upper()
                    result["datacheck_observed"] |= bool(_DATA_CHECK.search(text))
                    for severity, patterns in (("error", _FAILURES), ("warning", _WARNINGS)):
                        for code, pattern in patterns:
                            if pattern.search(text):
                                hard_failure |= severity == "error"
                                result["failure_signatures"].append({
                                    "code": code, "message": text[:2000], "source": str(path),
                                    "line": number, "severity": severity,
                                })
                                break
                    attempt = INCREMENT_RE.search(text)
                    if suffix == "msg" and attempt:
                        saw_increment = True
                        key = int(attempt.group(1)), int(attempt.group(2))
                        if key[1] > 1:
                            seen_attempts.add(key)
                    sta = _STA_NUMERIC.match(text) if suffix == "sta" else None
                    if sta:
                        result["accepted_increment"] = {
                            "step": int(sta.group(1)), "increment": int(sta.group(2)),
                            "source": str(path), "line": number, "text": text,
                        }
        except (OSError, ValueError) as exc:
            hard_failure = True
            result["failure_signatures"].append({
                "code": "artifact_unreadable", "message": str(exc), "source": str(path),
                "line": None, "severity": "error",
            })
    result["cutback_count"] = len(seen_attempts) if saw_increment else None
    if hard_failure:
        result["status"] = "FAILED"
    elif "lck" in result["artifacts"]:
        result["status"] = "RUNNING"
    elif stage == "datacheck" and result["job_completed"] and result["input_processed"]:
        result["status"] = "COMPLETED"
    elif stage == "run" and not result["datacheck_observed"] and (
        result["solver_completed"] or (result["job_completed"] and result["analysis_started"])
    ):
        result["status"] = "COMPLETED"
    elif seen_content:
        result["status"] = "INCOMPLETE"
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON constant: {value}")


def _json_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"Non-finite JSON number: {value}")
    return number


def _report_value(value: Any) -> Any:
    """Keep failed assertions serializable even when their configuration is bad."""
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _report_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_report_value(item) for item in value]
    return value


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number, not {type(value).__name__}")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def _pointer(document: Any, pointer: Any) -> Any:
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        raise ValueError("JSON pointer must be empty or begin with '/'")
    value = document
    for token in pointer.split("/")[1:]:
        if re.search(r"~(?![01])", token):
            raise ValueError("Invalid JSON pointer escape")
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            if not re.fullmatch(r"0|[1-9][0-9]*", key):
                raise ValueError(f"Invalid array index: {key}")
            value = value[int(key)]
        elif isinstance(value, dict):
            value = value[key]
        else:
            raise ValueError(f"JSON pointer cannot descend through {type(value).__name__}")
    return value


def _csv_actual(path: Path, assertion: dict[str, Any]) -> int | float:
    aggregate = assertion.get("aggregate")
    if aggregate not in {"max", "min", "max_abs", "count"}:
        raise ValueError("CSV aggregate must be max, min, max_abs, or count")
    minimum = assertion.get("min_rows", 1)
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 1:
        raise ValueError("min_rows must be a positive integer")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("CSV must have unique named columns")
        column = assertion.get("column")
        if column is not None and column not in reader.fieldnames:
            raise ValueError(f"CSV column missing: {column}")
        if aggregate != "count" and not isinstance(column, str):
            raise ValueError("Numeric CSV aggregates require a column")
        values: list[float] = []
        count = 0
        for row in reader:
            count += 1
            if None in row or any(value is None for value in row.values()):
                raise ValueError(f"Malformed CSV row {count + 1}")
            if aggregate != "count":
                try:
                    value = float(row[column])
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"Non-numeric CSV value at row {count + 1}") from exc
                values.append(_number(value, f"CSV row {count + 1}"))
    if count < minimum:
        raise ValueError(f"CSV has {count} rows; requires at least {minimum}")
    if aggregate == "count":
        return count
    if aggregate == "min":
        return min(values)
    if aggregate == "max_abs":
        return max(abs(value) for value in values)
    return max(values)


def _compare(actual: Any, assertion: dict[str, Any]) -> bool:
    if "expected" not in assertion:
        raise ValueError("Assertion requires expected")
    expected = assertion["expected"]
    operation = assertion.get("op")
    if operation == "eq":
        if isinstance(expected, bool):
            return isinstance(actual, bool) and actual is expected
        if expected is None or isinstance(expected, str):
            return type(actual) is type(expected) and actual == expected
        return _number(actual, "actual") == _number(expected, "expected")
    left = _number(actual, "actual")
    right = _number(expected, "expected")
    if operation == "le":
        return left <= right
    if operation == "ge":
        return left >= right
    if operation == "abs_le":
        if right < 0:
            raise ValueError("abs_le expected must be non-negative")
        return abs(left) <= right
    if operation == "close":
        atol = _number(assertion.get("atol", 0), "atol")
        rtol = _number(assertion.get("rtol", 0), "rtol")
        if min(atol, rtol) < 0:
            raise ValueError("close tolerances must be non-negative")
        return math.isclose(left, right, abs_tol=atol, rel_tol=rtol)
    raise ValueError(f"Unsupported assertion operation: {operation}")


def evaluate_assertions(root: Path, assertions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Evaluate declarative JSON/CSV evidence without executing project scripts."""
    root = Path(root).resolve()
    results = []
    for index, assertion in enumerate(assertions):
        record: dict[str, Any] = {
            "id": f"assertion_{index + 1}", "passed": False, "status": "FAIL",
            "actual": None, "expected": None, "detail": "", "file": None, "sha256": None,
        }
        try:
            if not isinstance(assertion, dict):
                raise ValueError("Assertion must be an object")
            record.update(id=assertion.get("id", record["id"]), expected=_report_value(assertion.get("expected")))
            filename = assertion.get("file")
            if not isinstance(filename, str) or not filename:
                raise ValueError("Assertion requires a nonempty file path")
            path = (root / filename).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Evidence file escapes case root")
            record["file"] = str(path)
            if not path.is_file():
                raise ValueError("Evidence file is missing or is not a regular file")
            record["sha256"] = _sha256(path)
            kind = assertion.get("kind")
            if kind == "json":
                document = json.loads(
                    path.read_text(encoding="utf-8-sig"),
                    parse_constant=_reject_json_constant, parse_float=_json_float,
                )
                record["actual"] = _pointer(document, assertion.get("pointer", ""))
            elif kind == "csv":
                record["actual"] = _csv_actual(path, assertion)
            else:
                raise ValueError(f"Unsupported evidence kind: {kind}")
            passed = _compare(record["actual"], assertion)
            record.update(passed=passed, status="PASS" if passed else "FAIL", detail="Assertion satisfied" if passed else "Assertion not satisfied")
        except (OSError, ValueError, TypeError, KeyError, IndexError, OverflowError, csv.Error) as exc:
            record["detail"] = f"{type(exc).__name__}: {exc}"
            record["actual"] = _report_value(record["actual"])
        results.append(record)
    return results
