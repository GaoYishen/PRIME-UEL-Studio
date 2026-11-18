from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
SPRING = APP / "examples/spring"


class PublicExampleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        (self.work / "parameters.dat").write_text("1000\n0\n1\n10\n50\n", encoding="ascii")

    def read_csv(self, filename: str) -> list[dict[str, str]]:
        with (self.work / filename).open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle))

    def assert_expected_history(self) -> None:
        history = self.read_csv("history.csv")
        self.assertEqual(len(history), 51)
        self.assertEqual(float(history[0]["time_s"]), 0)
        self.assertAlmostEqual(float(history[-1]["time_s"]), 10)
        self.assertAlmostEqual(float(history[-1]["displacement_mm"]), 1)
        self.assertAlmostEqual(float(history[-1]["force_N"]), 1)
        self.assertAlmostEqual(float(history[-1]["energy_J"]), 0.0005)
        self.assertLessEqual(float(self.read_csv("metrics.csv")[0]["relative_tangent_error"]), 1e-5)

    def test_profiles_pin_all_declared_inputs_without_private_paths(self) -> None:
        for name in ("spring_reference.json", "spring_fortran.json"):
            path = APP / "profiles" / name
            profile = json.loads(path.read_text(encoding="utf-8"))
            parent = (path.parent / profile["parent_root"]).resolve()
            self.assertEqual(parent, SPRING.resolve())
            for relative, expected in profile["files"].items():
                self.assertEqual(hashlib.sha256((parent / relative).read_bytes()).hexdigest(), expected)
            self.assertEqual(profile["mode"], "offline")
            self.assertTrue(profile["execution"]["enabled"])
            self.assertFalse(Path(profile["parent_root"]).is_absolute())
            content = (parent / "parameters.dat").read_text(encoding="utf-8")
            for binding in profile["bindings"]:
                self.assertEqual(content.count(binding["token"]), 1)

    def test_python_reference_generates_history_and_checked_tangent(self) -> None:
        result = subprocess.run([sys.executable, str(SPRING / "driver.py")], cwd=self.work,
                                capture_output=True, text=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_expected_history()

    def test_python_reference_rejects_invalid_parameters(self) -> None:
        for values in ("-1\n0\n1\n10\n50\n", "1000\n0\n1\n10\n2.5\n", "NaN\n0\n1\n10\n50\n"):
            (self.work / "parameters.dat").write_text(values, encoding="ascii")
            result = subprocess.run([sys.executable, str(SPRING / "driver.py")], cwd=self.work,
                                    capture_output=True, text=True, timeout=10, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((self.work / "history.csv").exists())

    @unittest.skipUnless(os.name == "nt" and shutil.which("ifort"), "Registered Windows ifort not available")
    def test_actual_fortran_compilation_matches_reference_history(self) -> None:
        source = self.work / "spring.f90"
        shutil.copy2(SPRING / "spring.f90", source)
        compile_result = subprocess.run([sys.executable, str(SPRING / "compile_spring.py")], cwd=self.work, capture_output=True,
                                        text=True, timeout=60, check=False)
        self.assertEqual(compile_result.returncode, 0, compile_result.stdout + compile_result.stderr)
        result = subprocess.run([str(self.work / "spring.exe")], cwd=self.work, capture_output=True,
                                text=True, timeout=10, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_expected_history()
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), hashlib.sha256((SPRING / "spring.f90").read_bytes()).hexdigest())

    def test_local_registration_is_explicit_preview_only_and_preserves_assets(self) -> None:
        module_path = APP / "tools/register_local_prime.py"
        if not module_path.is_file():
            self.skipTest("Private local registration helper is not part of the public export")
        spec = importlib.util.spec_from_file_location("studio_registration", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        source = self.work / "registered_source.f90"
        dll = self.work / "registered.dll"
        material = self.work / "material.json"
        source.write_text("! local ABI fixture; not executed\n", encoding="ascii")
        dll.write_bytes(b"test fixture, not executable")
        material.write_text(json.dumps({"graphite": [1.0] * 110, "siox": [1.0] * 110}), encoding="utf-8")
        before = {str(path): path.read_bytes() for path in (source, dll, material)}
        paths = module.register(dll, material, source, self.work / "local")
        self.assertEqual(len(paths), 2)
        for path in paths:
            profile = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(profile["mode"], "preview")
            self.assertFalse(profile["execution"]["enabled"])
            self.assertEqual(profile["initializer"]["kind"], "e1_phase")
            self.assertEqual(profile["initializer"]["dll"]["sha256"], hashlib.sha256(dll.read_bytes()).hexdigest())
        self.assertEqual(before, {str(path): path.read_bytes() for path in (source, dll, material)})
        with self.assertRaises(FileExistsError):
            module.register(dll, material, source, self.work / "local")


if __name__ == "__main__":
    unittest.main()
