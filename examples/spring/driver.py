"""Reference calculation for the one-dimensional elastic spring example.

This is a workflow example, not a battery or PRIME qualification case.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path


def calculate(folder: Path = Path.cwd()) -> None:
    lines = (folder / "parameters.dat").read_text(encoding="utf-8").splitlines()
    if len(lines) != 5:
        raise ValueError("The spring input requires exactly five scalar lines")
    stiffness, initial_mm, final_mm, duration, count = map(float, lines)
    if not all(math.isfinite(value) for value in (stiffness, initial_mm, final_mm, duration, count)):
        raise ValueError("All spring inputs must be finite")
    if stiffness <= 0 or duration <= 0 or not count.is_integer() or not 2 <= count <= 1000:
        raise ValueError("Require positive stiffness/duration and an integer step count in [2,1000]")
    steps = int(count)
    with (folder / "history.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["time_s", "displacement_mm", "force_N", "energy_J"])
        for index in range(steps + 1):
            fraction = index / steps
            displacement_mm = initial_mm + (final_mm - initial_mm) * fraction
            displacement_m = displacement_mm * 1e-3
            writer.writerow([duration * fraction, displacement_mm, stiffness * displacement_m,
                             0.5 * stiffness * displacement_m * displacement_m])
    probe = 0.5 * (initial_mm + final_mm) * 1e-3
    perturbation = 1e-7
    numerical = (stiffness * (probe + perturbation) - stiffness * (probe - perturbation)) / (2 * perturbation)
    error = abs(numerical - stiffness) / stiffness
    with (folder / "metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["relative_tangent_error"])
        writer.writerow([error])
    print(f"Spring example completed: {steps + 1} frames; relative tangent error={error:.6g}")


if __name__ == "__main__":
    calculate()
