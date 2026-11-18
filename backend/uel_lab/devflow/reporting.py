"""Human reports and append-only-ledger candidates for copied-case evidence."""
from __future__ import annotations

import csv
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .contract import CONFIG, PROVENANCE, inside, read_json, sha256, utc_now, write_json
from .evidence import evaluate_assertions


def _safe(value: Any) -> Any:
    """Keep diagnostic payloads serializable even when their data are invalid."""
    if isinstance(value, float) and not math.isfinite(value):
        return f"non-finite:{value}"
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    return value


def _text(value: Any) -> str:
    value = _safe(value)
    if value is None:
        return "unknown"
    if isinstance(value, (dict, list, bool)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    return str(value)


def _cell(value: Any) -> str:
    return _text(value).replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def _checks_csv(path: Path, checks: list[dict[str, Any]]) -> None:
    columns = ["id", "status", "passed", "actual", "expected", "detail", "file", "sha256"]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for check in checks:
            writer.writerow({column: _text(check.get(column)) for column in columns})


def render_report(result: dict[str, Any]) -> str:
    """State the observed checks and limits without promoting a solver claim."""
    checks = result.get("assertions", [])
    steps = result.get("steps", [])
    audit = result.get("mode") == "evidence_audit"
    lines = ["# UEL 开发调试报告", "", f"状态：**{_cell(result.get('status'))}**", "",
             f"记录：`{_cell(result.get('run_id', result.get('audit_id', 'unknown')))}`；"
             f"算例：`{_cell(result.get('case_id'))}`。", "",
             "## 已完成工作", "",
             "只读审计已有证据，未启动新求解。" if audit else "本次执行仅覆盖下列复制算例步骤与声明的检查。",
             "", f"允许解释范围：{_cell(result.get('claim_scope', 'Only the declared checks.'))}", ""]
    if steps:
        lines.extend(["| 步骤 | 类型 | 状态 | 返回码 | 耗时 / s | 说明 |",
                      "|---|---|---|---|---|---|"])
        for step in steps:
            lines.append("| " + " | ".join(_cell(step.get(key)) for key in
                         ("id", "kind", "status", "return_code", "elapsed_seconds", "detail")) + " |")
        lines.append("")
        for step in steps:
            runtime = step.get("runtime")
            if not isinstance(runtime, dict):
                continue
            lines.extend([f"运行健康线索（`{_cell(step.get('id'))}`）："
                          f"完成状态={_cell(runtime.get('status'))}；"
                          f"接受增量={_cell(runtime.get('accepted_increment'))}；"
                          f"cutback={_cell(runtime.get('cutback_count'))}。", ""])
            signatures = runtime.get("failure_signatures", [])
            for signature in signatures[:25]:
                lines.append(f"- `{_cell(signature.get('code'))}`：{_cell(signature.get('message'))} "
                             f"（{_cell(signature.get('source'))}:{_cell(signature.get('line'))}）")
            if len(signatures) > 25:
                lines.append(f"- 其余 {len(signatures) - 25} 条签名见 result.json。")
            if signatures:
                lines.append("")
    if checks:
        lines.extend(["| 检查 | 状态 | 实际值 | 判据值 | 说明 |", "|---|---|---|---|---|"])
        for check in checks:
            lines.append("| " + " | ".join(_cell(check.get(key)) for key in
                         ("id", "status", "actual", "expected", "detail")) + " |")
        lines.append("")
    else:
        lines.extend(["没有已执行的结果断言；不得据此声称物理验证通过。", ""])
    config = result.get("configuration", {})
    if config:
        lines.extend(["## 工况与改动", "", f"工况：{_cell(config.get('case_conditions'))}", "",
                      f"运行前假设：{_cell(config.get('hypothesis'))}", "",
                      f"预期观察：{_cell(config.get('expected_observation'))}", "",
                      f"停止条件：{_cell(config.get('stop_condition'))}", ""])
        changes = config.get("parameter_changes", [])
        if changes:
            lines.extend(["| 参数 | 改前 | 改后 | 单位 |", "|---|---|---|---|"])
            for change in changes:
                if isinstance(change, dict):
                    lines.append("| " + " | ".join(_cell(change.get(key)) for key in
                                 ("name", "before", "after", "unit")) + " |")
            lines.append("")
    lines.extend(["## 文件与当前结构", "",
                  f"证据/工作目录：`{_cell(result.get('evidence_dir') if audit else result.get('workdir'))}`。", "",
                  "报告目录保留 result.json、report.md、checks.csv 和 artifacts.json；执行记录另有冻结配置、"
                  "快照、逐步日志和 ledger_args.json。", "", "## 已知问题与限制", "",
                  "诊断完成、进程返回 0、datacheck 完成及部分断言通过均不等价于完整物理资格。",
                  "原始证据缺失、超时、停止或失败均保留其实际状态；本报告不发布全电极、热源或终端电压验证结论。", ""])
    issues = result.get("issues", [])
    if issues:
        lines.extend(f"- {_cell(issue)}" for issue in issues)
        lines.append("")
    if not audit:
        lines.extend(["台账状态：`pending_append`。本工具只生成 dry-run 导入候选，尚未正式追加 CSV 或更新 XLSX。",
                      "真实 MECT 调试必须完成现有调试记录规范要求的台账追加后，才能推进 gate。", ""])
    lines.extend(["## 下一最小动作", "",
                  "确认这些历史文件对应的父版本与检查范围；新版本需要在新复制算例中重新执行。" if audit else
                  "先处理首个失败或缺失证据；完成所需物理 gate 和串行台账追加，再决定下一步。", ""])
    return "\n".join(lines)


def _artifact_index(paths: dict[str, Path], output: Path) -> dict[str, Any]:
    index = {name: {"path": str(path.resolve()), "sha256": sha256(path), "size_bytes": path.stat().st_size}
             for name, path in paths.items() if path.is_file()}
    write_json(output, {"created_at": utc_now(), "artifacts": index,
                        "note": "The index excludes itself to avoid a circular hash."})
    return {**{name: item["path"] for name, item in index.items()},
            "artifacts": str(output.resolve()), "sha256": {name: item["sha256"] for name, item in index.items()}}


def _ledger_status(status: str) -> str:
    if status in {"FAILED", "TIMED_OUT"}:
        return "failed"
    if status == "BLOCKED":
        return "blocked"
    if status == "VALIDATION_CHECKS_PASSED":
        return "partial_pass"
    return "diagnostic_only"


def save_run_reports(case: Path, result: dict[str, Any]) -> dict[str, Any]:
    """Write reports only beside an existing run; never append the global ledger."""
    case = Path(case).resolve()
    if not isinstance(result.get("workdir"), str):
        raise ValueError("Reporting requires an executed run with a workdir")
    work = Path(result["workdir"]).resolve()
    run = work.parent
    if work.name != "work" or run.parent != case / "runs" or not work.is_dir():
        raise ValueError("Run workdir must be case/runs/<id>/work")
    if result.get("run_id") != run.name:
        raise ValueError("run_id and recorded workdir disagree")
    config_path = run / CONFIG
    if not config_path.is_file():
        config_path = run / "config.json"
    if not config_path.is_file():
        raise ValueError("Frozen run configuration is missing; current config cannot substitute")
    if result.get("config_sha256") and sha256(config_path) != result["config_sha256"]:
        raise ValueError("Frozen run configuration no longer matches its recorded SHA-256")
    config = read_json(config_path)
    if not isinstance(config, dict):
        raise ValueError("Frozen run configuration must be an object")
    provenance = read_json(case / PROVENANCE)
    status = str(result.get("status", "unknown"))
    report_data = {**result, "ledger_status": "pending_append", "configuration": config}
    report_path, checks_path, ledger_path = run / "report.md", run / "checks.csv", run / "ledger_args.json"
    report_path.write_text(render_report(report_data), encoding="utf-8")
    _checks_csv(checks_path, result.get("assertions", []))
    failures = [step.get("detail", step.get("status", "unknown")) for step in result.get("steps", [])
                if step.get("status") != "COMPLETED"]
    failures.extend(check.get("detail", check.get("status", "unknown")) for check in result.get("assertions", [])
                    if check.get("passed") is not True)
    failures.extend(result.get("issues", []))
    failures.extend(signature for step in result.get("steps", [])
                    for signature in step.get("runtime", {}).get("failure_signatures", [])
                    if signature.get("severity") == "error")
    scope = config.get("scope", {})
    source, inp = inside(work, config["source"]), inside(work, config["inp"])
    ledger = {
        "version": f"{config.get('case_id', case.name)}_{run.name}",
        "parent": provenance.get("parent_root", "unknown"), "scope": _text(scope),
        "changed": _text({"source_delta": scope.get("source_delta"),
                           "parameter_changes": config.get("parameter_changes", [])}),
        "physical_delta": _text(scope.get("property_delta", "not declared")),
        "numerical_delta": _text(config.get("numerical_delta", "not declared")),
        "case": _text(config.get("case_conditions", {})),
        "expected": config.get("expected_observation", "not declared"),
        "result": _text({"status": status, "steps": len(result.get("steps", [])),
                         "checks": len(result.get("assertions", [])), "elapsed_seconds": result.get("elapsed_seconds")}),
        "failure": _text(failures) if failures else "none observed in declared checks",
        "status": _ledger_status(status),
        "fortran": str(source) if source.is_file() else "", "inp": str(inp) if inp.is_file() else "",
        "artifacts": "; ".join(str(path) for path in (run / "result.json", report_path, checks_path, run / "artifacts.json")),
        "conclusion": f"{status}; declared copied-case checks only; pending formal ledger append and XLSX update",
        "next_action": "Resolve missing/failed evidence; append this record serially under the MECT recording specification before gate advancement",
        "source": "uel_devtool", "record_id": f"ueldev_{run.name}",
        "date": datetime.now(timezone(timedelta(hours=8))).date().isoformat(), "dry_run": True,
    }
    write_json(ledger_path, _safe(ledger))
    paths = {"result": run / "result.json", "report": report_path, "checks": checks_path,
             "ledger_args": ledger_path, "configuration": config_path, "snapshot": run / "snapshot.json"}
    return _artifact_index(paths, run / "artifacts.json")


def audit_evidence(recipe_path: Path, evidence_dir: Path, output: Path) -> dict[str, Any]:
    """Audit historical results without running scripts or modifying their tree."""
    recipe_path, evidence_dir, output = Path(recipe_path).resolve(), Path(evidence_dir).resolve(), Path(output).resolve()
    if not evidence_dir.is_dir():
        raise ValueError("Evidence directory must exist")
    if output.exists():
        raise ValueError("Audit output must be a new directory")
    if output.is_relative_to(evidence_dir) or evidence_dir.is_relative_to(output):
        raise ValueError("Audit output and evidence directory must be disjoint")
    recipe_hash = sha256(recipe_path)
    recipe = read_json(recipe_path)
    if not isinstance(recipe, dict):
        raise ValueError("Audit recipe must be an object")
    issues = []
    assertions = recipe.get("assertions")
    if not isinstance(assertions, list) or not assertions:
        issues.append("Audit requires a nonempty assertions list")
        assertions = []
    assertion_ids = [item.get("id") for item in assertions if isinstance(item, dict)]
    if len(assertion_ids) != len(assertions) or any(not isinstance(name, str) or not name for name in assertion_ids) or len(set(assertion_ids)) != len(assertion_ids):
        issues.append("Audit assertions require unique nonempty string ids")
    checks = evaluate_assertions(evidence_dir, assertions)
    for check in checks:
        if check.get("sha256"):
            try:
                unchanged = sha256(Path(check["file"])) == check["sha256"]
            except OSError:
                unchanged = False
            if not unchanged:
                check.update(passed=False, status="FAIL", detail="Evidence changed or disappeared while being audited")
    if sha256(recipe_path) != recipe_hash:
        issues.append("Recipe changed while being audited")
    result = {"schema_version": 1, "mode": "evidence_audit", "audit_id": output.name,
              "case_id": recipe.get("case_id", "historical_evidence"), "created_at": utc_now(),
              "status": "EVIDENCE_PASSED" if checks and not issues and all(check.get("passed") is True for check in checks) else "EVIDENCE_FAILED",
              "claim_scope": "existing evidence only; no new solve", "steps": [], "assertions": checks,
              "issues": issues, "recipe": str(recipe_path), "recipe_sha256": recipe_hash,
              "evidence_dir": str(evidence_dir), "scope": recipe.get("scope"), "notes": recipe.get("notes")}
    output.mkdir(parents=True, exist_ok=False)
    result_path, report_path, checks_path = output / "result.json", output / "report.md", output / "checks.csv"
    write_json(result_path, _safe(result))
    report_path.write_text(render_report(result), encoding="utf-8")
    _checks_csv(checks_path, checks)
    result["artifacts"] = _artifact_index({"result": result_path, "report": report_path, "checks": checks_path}, output / "artifacts.json")
    return result
