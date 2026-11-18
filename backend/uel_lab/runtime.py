from __future__ import annotations

import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .io_utils import read_text


INCREMENT_RE = re.compile(
    r"INCREMENT\s+(\d+)\s+STARTS\. ATTEMPT NUMBER\s+(\d+), TIME INCREMENT\s+([0-9.E+-]+)",
    re.IGNORECASE,
)
ITERATION_RE = re.compile(
    r"CONVERGENCE CHECKS FOR EQUILIBRIUM ITERATION\s+(\d+)",
    re.IGNORECASE,
)
RESIDUAL_RE = re.compile(
    r"LARGEST(?:\s+SCALED)?\s+RESIDUAL\s+([A-Z. ]+?)\s+([\-0-9.E+]+)\s+AT NODE\s+(\d+)\s+DOF\s+(\d+)",
    re.IGNORECASE,
)
CONVERGED_RE = re.compile(
    r"THE\s+([A-Z ]+?)\s+EQUILIBRIUM\s+(EQUATIONS HAVE CONVERGED|RESPONSE WAS LINEAR IN THIS INCREMENT)",
    re.IGNORECASE,
)
NOT_ACHIEVED_RE = re.compile(
    r"([A-Z ]+?)\s+EQUILIBRIUM NOT ACHIEVED WITHIN TOLERANCE\.",
    re.IGNORECASE,
)


def normalize_field(raw: str) -> str:
    field = " ".join(raw.upper().split())
    aliases = {
        "FORCE": "mechanical_force",
        "HEAT FLUX": "thermal_heat_flux",
        "MOMENT": "moment",
        "FLUX": "flux",
    }
    return aliases.get(field, field.lower().replace(" ", "_"))


def parse_log(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {
            "path": str(path) if path else None,
            "exists": False,
            "compiled": False,
            "linked": False,
            "input_processed": False,
            "analysis_started": False,
            "analysis_completed": False,
            "analysis_failed": False,
            "warnings": [],
            "errors": ["log file not found"],
        }
    text = read_text(path)
    warnings = [line.strip() for line in text.splitlines() if "warning" in line.lower()]
    errors = [line.strip() for line in text.splitlines() if "error" in line.lower()]
    return {
        "path": str(path),
        "exists": True,
        "compiled": "End Compiling Abaqus/Standard User Subroutines" in text,
        "linked": "End Linking Abaqus/Standard User Subroutines" in text,
        "input_processed": "End Analysis Input File Processor" in text,
        "analysis_started": "Begin Abaqus/Standard Analysis" in text,
        "analysis_completed": "Abaqus/Analysis exited with errors" not in text and "completed successfully" in text.lower(),
        "analysis_failed": "Abaqus/Analysis exited with errors" in text or "Abaqus Error:" in text,
        "warnings": warnings,
        "errors": errors,
    }


def parse_msg(path: Path | None) -> dict[str, Any]:
    if not path or not path.exists():
        return {
            "path": str(path) if path else None,
            "exists": False,
            "errors": ["message file not found"],
            "increment_timeline": [],
            "field_convergence_summary": {},
            "dominant_failure_signals": {},
            "recommended_actions": [],
        }

    attempts: list[dict[str, Any]] = []
    field_summary: dict[str, Counter] = defaultdict(Counter)
    failed_dofs: Counter[int] = Counter()
    failed_nodes: Counter[int] = Counter()
    error_lines: list[str] = []
    warning_counts: Counter[str] = Counter()
    current_attempt: dict[str, Any] | None = None
    current_iteration: int | None = None
    lines = read_text(path).splitlines()

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        increment_match = INCREMENT_RE.search(stripped)
        if increment_match:
            current_attempt = {
                "increment": int(increment_match.group(1)),
                "attempt": int(increment_match.group(2)),
                "time_increment": float(increment_match.group(3)),
                "iteration_count": 0,
                "diverging": False,
                "fields": {},
            }
            attempts.append(current_attempt)
            current_iteration = None
            continue
        if current_attempt is None:
            if stripped.startswith("***ERROR:"):
                error_lines.append(stripped)
            continue

        iteration_match = ITERATION_RE.search(stripped)
        if iteration_match:
            current_iteration = int(iteration_match.group(1))
            current_attempt["iteration_count"] = max(current_attempt["iteration_count"], current_iteration)
            continue

        residual_match = RESIDUAL_RE.search(stripped)
        if residual_match:
            field_label = " ".join(residual_match.group(1).upper().split())
            field_key = normalize_field(field_label)
            entry = current_attempt["fields"].setdefault(
                field_key,
                {
                    "field_label": field_label,
                    "last_iteration": current_iteration,
                },
            )
            entry.update(
                {
                    "residual": float(residual_match.group(2)),
                    "node": int(residual_match.group(3)),
                    "dof": int(residual_match.group(4)),
                    "last_iteration": current_iteration,
                }
            )
            continue

        converged_match = CONVERGED_RE.search(stripped)
        if converged_match:
            field_label = " ".join(converged_match.group(1).upper().split())
            field_key = normalize_field(field_label)
            status = "linear" if "LINEAR" in converged_match.group(2).upper() else "converged"
            entry = current_attempt["fields"].setdefault(field_key, {"field_label": field_label})
            entry["status"] = status
            field_summary[field_key][status] += 1
            continue

        not_achieved_match = NOT_ACHIEVED_RE.search(stripped)
        if not_achieved_match:
            field_label = " ".join(not_achieved_match.group(1).upper().split())
            field_key = normalize_field(field_label)
            entry = current_attempt["fields"].setdefault(field_key, {"field_label": field_label})
            entry["status"] = "not_achieved"
            field_summary[field_key]["not_achieved"] += 1
            if "node" in entry:
                failed_nodes.update([entry["node"]])
            if "dof" in entry:
                failed_dofs.update([entry["dof"]])
            continue

        if "SOLUTION APPEARS TO BE DIVERGING" in stripped.upper():
            current_attempt["diverging"] = True
            for field_key in current_attempt["fields"]:
                field_summary[field_key]["diverging_attempts"] += 1
            warning_counts["solution_diverging"] += 1
            continue

        if stripped.startswith("***ERROR:"):
            error_lines.append(stripped)
            continue

        upper = stripped.upper()
        if "NEGATIVE EIGENVALUE" in upper:
            warning_counts["negative_eigenvalue"] += 1
        if "NUMERICAL SINGULARITY" in upper:
            warning_counts["numerical_singularity"] += 1
        if "ZERO PIVOT" in upper:
            warning_counts["zero_pivot"] += 1
        if "TOO MANY ATTEMPTS" in upper:
            warning_counts["too_many_attempts"] += 1
        if "DET" in upper and "NEGATIVE" in upper:
            warning_counts["negative_jacobian"] += 1

    dominant_field = None
    if field_summary:
        dominant_field = max(
            field_summary.items(),
            key=lambda item: (item[1]["not_achieved"], item[1]["diverging_attempts"], item[0]),
        )[0]

    min_time_increment = min((attempt["time_increment"] for attempt in attempts), default=None)
    max_increment = max((attempt["increment"] for attempt in attempts), default=None)
    first_diverging_increment = next((attempt["increment"] for attempt in attempts if attempt["diverging"]), None)
    cutback_count = sum(1 for attempt in attempts if attempt["attempt"] > 1)
    mech_stable_while_other_fail = any(
        attempt["fields"].get("mechanical_force", {}).get("status") in {"converged", "linear"}
        and any(
            field_key != "mechanical_force" and field_data.get("status") == "not_achieved"
            for field_key, field_data in attempt["fields"].items()
        )
        for attempt in attempts
    )

    failure_modes: list[str] = []
    specific_errors = [
        line
        for line in error_lines
        if "TERMINATED DUE TO PREVIOUS ERRORS" not in line.upper()
    ]
    final_error = specific_errors[-1] if specific_errors else (error_lines[-1] if error_lines else None)
    if any("TIME INCREMENT REQUIRED IS LESS THAN THE MINIMUM SPECIFIED" in line for line in error_lines):
        failure_modes.append("minimum_time_increment_exhausted")
    if warning_counts["solution_diverging"]:
        failure_modes.append("solution_diverging")
    if warning_counts["numerical_singularity"]:
        failure_modes.append("numerical_singularity")
    if warning_counts["negative_eigenvalue"]:
        failure_modes.append("negative_eigenvalue")
    if warning_counts["negative_jacobian"]:
        failure_modes.append("negative_jacobian")

    field_convergence_summary = {
        field: {
            "converged": counts["converged"],
            "linear": counts["linear"],
            "not_achieved": counts["not_achieved"],
            "diverging_attempts": counts["diverging_attempts"],
        }
        for field, counts in sorted(field_summary.items())
    }

    dominant_failure_signals = {
        "dominant_field": dominant_field,
        "final_error": final_error,
        "failure_modes": failure_modes,
        "top_failed_dofs": [
            {"dof": dof, "count": count}
            for dof, count in failed_dofs.most_common(5)
        ],
        "top_failed_nodes": [
            {"node": node, "count": count}
            for node, count in failed_nodes.most_common(5)
        ],
        "mechanical_field_stable_while_other_fields_fail": mech_stable_while_other_fail,
        "first_diverging_increment": first_diverging_increment,
        "cutback_count": cutback_count,
        "min_time_increment": min_time_increment,
    }

    recommendations: list[str] = []
    if dominant_field == "thermal_heat_flux":
        recommendations.append(
            "Thermal residuals dominate. Check heat source consistency, heat capacity, conductivity, and thermal cross-coupling tangents first."
        )
    if mech_stable_while_other_fail:
        recommendations.append(
            "Mechanical equilibrium appears stable while another field fails. Prioritize electrochemical or thermal residual assembly before revisiting the mechanical block."
        )
    if "minimum_time_increment_exhausted" in failure_modes:
        recommendations.append(
            "The solve exhausted the minimum time increment. Inspect late-increment source terms, state-variable update ordering, and cross-block Jacobian entries around the last converged step."
        )
    if cutback_count > 10:
        recommendations.append(
            "The job is heavily cutback-limited. Consider smoother loading ramps or temporary debug hooks around the first diverging increment."
        )

    return {
        "path": str(path),
        "exists": True,
        "errors": error_lines,
        "increment_timeline": attempts,
        "field_convergence_summary": field_convergence_summary,
        "dominant_failure_signals": dominant_failure_signals,
        "recommended_actions": recommendations,
        "analysis_summary": {
            "attempt_count": len(attempts),
            "max_increment": max_increment,
            "cutback_count": cutback_count,
            "min_time_increment": min_time_increment,
        },
    }


def find_job_artifacts(job: str, workdir: Path) -> dict[str, Path | None]:
    suffixes = [".log", ".msg", ".sta"]
    paths: dict[str, Path | None] = {}
    for suffix in suffixes:
        candidate = workdir / f"{job}{suffix}"
        paths[suffix[1:]] = candidate if candidate.exists() else None
    return paths


def diagnose_job(job: str, workdir: Path) -> dict[str, Any]:
    artifacts = find_job_artifacts(job, workdir)
    log_data = parse_log(artifacts["log"])
    msg_data = parse_msg(artifacts["msg"])
    return {
        "job": job,
        "workdir": str(workdir.resolve()),
        "artifacts": {key: (str(value) if value else None) for key, value in artifacts.items()},
        "build_status": log_data,
        "increment_timeline": msg_data["increment_timeline"],
        "field_convergence_summary": msg_data["field_convergence_summary"],
        "dominant_failure_signals": msg_data["dominant_failure_signals"],
        "recommended_actions": msg_data["recommended_actions"],
        "runtime_errors": msg_data["errors"],
    }
