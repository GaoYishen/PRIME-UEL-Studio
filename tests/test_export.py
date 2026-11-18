from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

APP = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("studio_export", APP / "tools/export_github_project.py")
EXPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORT)


class ExportTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_allowlist_excludes_local_models_binaries_and_results(self) -> None:
        result = EXPORT.export_project(self.root / "public")
        output = Path(result["directory"])
        paths = {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()}
        self.assertIn("prime_studio/gui.py", paths)
        self.assertIn("prime_studio/file_browser.py", paths)
        self.assertIn("prime_studio/file_browser_panel.py", paths)
        self.assertIn("prime_studio/public_initial.py", paths)
        self.assertIn("docs/public_solver_interface.md", paths)
        self.assertIn("docs/user_manual_zh.md", paths)
        self.assertIn("tools/audit_public_release.py", paths)
        self.assertIn("backend/uel_lab/devflow/execution.py", paths)
        self.assertIn("backend/uel_dev.py", paths)
        self.assertIn("examples/spring/spring.f90", paths)
        self.assertFalse(any("local" in Path(path).parts or "runs" in Path(path).parts for path in paths))
        self.assertFalse(any(Path(path).suffix.casefold() in {".dll", ".exe", ".obj", ".mod", ".log", ".for"} for path in paths))
        self.assertFalse(any("material_contract" in path or "prime_e1b" in path for path in paths))
        self.assertFalse(any(path.endswith((".f90", ".for", ".f")) and not path.startswith("examples/spring/") for path in paths))
        self.assertFalse(any(Path(path).name.startswith("test_a5_") or Path(path).name == "test_initial.py" for path in paths))
        with zipfile.ZipFile(result["zip"]) as archive:
            self.assertEqual(set(archive.namelist()), {f"public/{path}" for path in paths})
        self.assertFalse(result["remote_published"])
        self.assertEqual(json.loads((output / "public_release_audit.json").read_text(encoding="utf-8"))["status"], "PASS")

    def test_manifest_hashes_cover_every_payload_file(self) -> None:
        result = EXPORT.export_project(self.root / "public")
        output = Path(result["directory"])
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        actual = {path.relative_to(output).as_posix() for path in output.rglob("*") if path.is_file()}
        self.assertEqual(set(manifest["files"]), actual - {"manifest.json"})
        for relative, expected in manifest["files"].items():
            self.assertEqual(hashlib.sha256((output / relative).read_bytes()).hexdigest(), expected)

    def test_existing_directory_or_zip_is_never_overwritten(self) -> None:
        output = self.root / "public"
        result = EXPORT.export_project(output)
        before = EXPORT.sha256(Path(result["zip"]))
        with self.assertRaises(FileExistsError):
            EXPORT.export_project(output)
        self.assertEqual(EXPORT.sha256(Path(result["zip"])), before)
        orphan = self.root / "zip_only.zip"
        orphan.write_bytes(b"existing")
        with self.assertRaises(FileExistsError):
            EXPORT.export_project(self.root / "zip_only")
        self.assertEqual(orphan.read_bytes(), b"existing")

    def test_exported_project_runs_reference_without_repository_imports(self) -> None:
        result = EXPORT.export_project(self.root / "public")
        output = Path(result["directory"])
        case = self.root / "cases/statecontract_export_reference"
        code = r'''
import json, sys
from pathlib import Path
import prime_studio.gui
import prime_studio.core as core
import uel_lab.devflow.execution as execution
from prime_studio.public_initial import calculate_initial
from uel_lab.devflow.reporting import save_run_reports
root=Path.cwd().resolve()
assert Path(core.__file__).resolve().is_relative_to(root)
assert Path(execution.__file__).resolve().is_relative_to(root)
profile=core.load_profile(root/'profiles/spring_reference.json')
values=core.validate_parameters(profile,{k:v['default'] for k,v in profile['parameters'].items()})
initial=calculate_initial(profile,values)
prepared=core.prepare_case(profile,values,initial,Path(sys.argv[1]))
assert prepared['status']=='GENERATED',prepared
result=execution.run_workflow(Path(prepared['case']),execute=True)
assert result['status']=='DIAGNOSTIC_COMPLETE',result
reports=save_run_reports(Path(prepared['case']),result)
history=core.read_history(Path(result['workdir'])/'history.csv')
print(json.dumps({'status':result['status'],'rows':len(history['rows']),'report':reports['report']}))
'''
        environment = {**os.environ, "PYTHONPATH": os.pathsep.join((str(output), str(output / "backend"))),
                       "PYTHONIOENCODING": "utf-8"}
        process = subprocess.run([sys.executable, "-c", code, str(case)], cwd=output,
                                 env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30, check=False)
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        observed = json.loads(process.stdout)
        self.assertEqual(observed["status"], "DIAGNOSTIC_COMPLETE")
        self.assertGreaterEqual(observed["rows"], 3)
        self.assertTrue(Path(observed["report"]).is_file())


if __name__ == "__main__":
    unittest.main()
