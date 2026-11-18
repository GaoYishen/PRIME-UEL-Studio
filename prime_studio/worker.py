"""Isolated execution entry used by the desktop's own, tracked task process."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case", type=Path)
    args = parser.parse_args()
    from uel_lab.devflow.execution import run_workflow
    from uel_lab.devflow.reporting import save_run_reports
    result = run_workflow(args.case, execute=True)
    if result.get("workdir"):
        result["reports"] = save_run_reports(args.case, result)
    print(json.dumps(result, ensure_ascii=True, indent=2), flush=True)
    return 0 if result["status"] in {"DIAGNOSTIC_COMPLETE", "VALIDATION_CHECKS_PASSED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
