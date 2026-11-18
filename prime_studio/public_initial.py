"""Public initial-state adapter for the standalone demonstration model.

The public package deliberately exposes only the non-proprietary spring
demonstration.  Private material and constitutive initializers stay in the
local checkout and are supplied through the solver adapter contract.
"""
from __future__ import annotations

import math
from copy import deepcopy
from typing import Any


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError(f"{label} must be a finite number") from error
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def calculate_initial(profile: dict[str, Any], parameters: dict[str, Any]) -> dict[str, Any]:
    """Calculate only the public spring demonstration initial state."""
    if profile.get("initializer", {}).get("kind") != "spring_demo":
        raise ValueError("The public package has no private material initializer")
    stiffness = _number(parameters.get("stiffness_N_m"), "stiffness_N_m")
    displacement = _number(parameters.get("initial_displacement_mm", 0), "initial_displacement_mm") / 1000.0
    target = _number(parameters.get("displacement_mm"), "displacement_mm") / 1000.0
    duration = _number(parameters.get("duration_s"), "duration_s")
    steps = _number(parameters.get("steps"), "steps")
    if stiffness <= 0 or duration <= 0 or steps < 1 or not steps.is_integer():
        raise ValueError("Spring stiffness/duration must be positive and steps a positive integer")
    return {
        "values": {"initial_displacement_m": displacement, "initial_force_N": stiffness * displacement,
                   "initial_energy_J": 0.5 * stiffness * displacement * displacement,
                   "target_displacement_m": target},
        "units": {"initial_displacement_m": "m", "initial_force_N": "N", "initial_energy_J": "J",
                  "target_displacement_m": "m"},
        "checks": {"finite": True, "positive_stiffness": True, "positive_time": True},
        "scope": "Analytic single-spring tooling example only; no battery or UEL physics qualification.",
        "provenance": {"initializer": "spring_demo", "law": "force=k*u; stored_energy=k*u*u/2",
                       "state": "displacement is a primary scalar; spring stiffness is an input parameter"},
        "input_parameters": deepcopy(parameters),
    }
