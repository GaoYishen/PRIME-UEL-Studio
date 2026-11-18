"""Command line integration; managed executions are always opt-in."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from .contract import ARCHIVE_ROOT, CANONICAL_V6, create_case, preflight_case, read_json, sha256, snapshot_case


def _print(data: object) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False))


def _result_path(case: Path, run_id: str) -> Path:
    from .contract import inside
    runs = sorted((case / "runs").glob("*/result.json"))
    if run_id == "latest":
        if not runs:
            raise ValueError("No executed run found")
        return inside(case, runs[-1].relative_to(case).as_posix())
    return inside(case / "runs", f"{run_id}/result.json")


def dispatch(args: argparse.Namespace) -> int:
    from .execution import run_workflow
    from .reporting import audit_evidence, render_report, save_run_reports
    try:
        command = args.dev_command
        if command == "doctor":
            _print({"python": sys.executable, "package": str(Path(__file__).resolve()),
                    "abaqus": shutil.which("abaqus"), "ifort": shutil.which("ifort"),
                    "ifx": shutil.which("ifx"), "gfortran": shutil.which("gfortran"),
                    "canonical_v6": str(CANONICAL_V6),
                    "canonical_sha256": sha256(CANONICAL_V6) if CANONICAL_V6.is_file() else None,
                    "note": "Discovery only; no compiler, Abaqus or license checkout was invoked."})
            return 0
        if command == "init":
            path = create_case(args.parent, args.case, args.source, args.inp, args.include,
                               canonical=args.canonical, archive_root=args.archive_root, purpose=args.purpose)
            _print({"status": "GENERATED", "config": str(path),
                    "next": "Fill scope, hypothesis, conditions, predecessor gate, steps and assertions; then preflight."})
            return 0
        if command == "snapshot":
            _print(snapshot_case(args.case, args.allow, args.note))
            return 0
        if command == "preflight":
            result = preflight_case(args.case)
            _print(result)
            return 0 if result["passed"] else 2
        if command in {"plan", "run"}:
            result = run_workflow(args.case, execute=command == "run" and args.execute)
            if result.get("workdir") and result.get("run_id"):
                result["reports"] = save_run_reports(args.case, result)
            _print(result)
            return 0 if result["status"] in {"PLANNED", "DRY_RUN", "DIAGNOSTIC_COMPLETE", "VALIDATION_CHECKS_PASSED"} else 2
        if command == "audit":
            result = audit_evidence(args.recipe, args.evidence_dir, args.output)
            _print(result)
            return 0 if result["status"] == "EVIDENCE_PASSED" else 2
        result_path = _result_path(args.case, args.run_id)
        result = read_json(result_path)
        if args.format == "json":
            _print(result)
        else:
            report_path = result_path.parent / "report.md"
            print(report_path.read_text(encoding="utf-8") if report_path.is_file() else render_report(result))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        _print({"status": "BLOCKED", "error": str(exc)})
        return 2


def _populate(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="dev_command", required=True)
    sub.add_parser("doctor", help="Discover this checkout and tools without launching them")
    init = sub.add_parser("init", help="Create a new copied-only statecontract from explicit parent files")
    init.add_argument("case", type=Path)
    init.add_argument("--parent", type=Path, required=True)
    init.add_argument("--source", required=True, help="Fortran path relative to parent")
    init.add_argument("--inp", required=True, help="INP path relative to parent")
    init.add_argument("--include", action="append", default=[], help="Additional relative parent file; repeat as needed")
    init.add_argument("--canonical", type=Path, default=CANONICAL_V6)
    init.add_argument("--archive-root", type=Path, default=ARCHIVE_ROOT)
    init.add_argument("--purpose", choices=["diagnostic", "validation"], default="diagnostic")
    for name in ("preflight", "snapshot", "plan", "run", "report"):
        item = sub.add_parser(name, help={"preflight": "Verify provenance, includes and declared execution scope",
                                         "snapshot": "Register exactly allowlisted copied-file changes",
                                         "plan": "Show the execution plan without running commands",
                                         "run": "Execute in a fresh run directory only with --execute",
                                         "report": "Read an existing run report"}[name])
        item.add_argument("case", type=Path)
        if name == "snapshot":
            item.add_argument("--allow", action="append", required=True)
            item.add_argument("--note", required=True)
        if name == "run":
            item.add_argument("--execute", action="store_true")
        if name == "report":
            item.add_argument("--run-id", default="latest")
            item.add_argument("--format", choices=["md", "json"], default="md")
    audit = sub.add_parser("audit", help="Read existing JSON/CSV evidence without running the solver")
    audit.add_argument("--recipe", type=Path, required=True)
    audit.add_argument("--evidence-dir", type=Path, required=True)
    audit.add_argument("--output", type=Path, required=True, help="New output directory, disjoint from evidence")


def add_dev_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("dev", help="Copied-case development, assertions and regression workflow")
    _populate(parser)
    parser.set_defaults(func=dispatch)


def standalone_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="uel-lab dev", description=__doc__)
    _populate(parser)
    return dispatch(parser.parse_args(argv))
