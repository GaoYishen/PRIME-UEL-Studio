from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from prime_studio.core import (available_actions, load_profile, prepare_action_case, prepare_case,
                               read_history, registry_status, validate_parameters)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class StudioCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.parent = self.root / "parent"
        self.parent.mkdir()
        (self.parent / "unit.for").write_text("      END\n", encoding="ascii")
        (self.parent / "case.inp").write_text("*Heading\nTooling fixture\n", encoding="ascii")
        (self.parent / "parameters.dat").write_text("K={{K}}\nU0={{U0}}\nSTEPS={{STEPS}}\n", encoding="ascii")
        (self.parent / "driver.py").write_text("from pathlib import Path\nPath('out.csv').write_text('x,y\\n0,0\\n1,2\\n')\n", encoding="ascii")
        self.card = self.root / "model.json"
        self.data = {
            "schema": 1, "id": "unit_fixture", "label": "Unit fixture", "mode": "offline",
            "parent_root": "parent", "source": "unit.for", "inp": "case.inp", "includes": ["parameters.dat", "driver.py"],
            "files": {path.name: sha(path) for path in self.parent.iterdir()}, "canonical": "parent/unit.for",
            "parameters": {"stiffness": {"label": "K", "unit": "N/m", "default": 10.0, "minimum": 1.0},
                           "steps": {"label": "Steps", "unit": "1", "type": "int", "default": 5, "minimum": 2, "maximum": 100}},
            "bindings": [{"file": "parameters.dat", "token": "{{K}}", "value": "stiffness"},
                         {"file": "parameters.dat", "token": "{{U0}}", "value": "initial.displacement_m"},
                         {"file": "parameters.dat", "token": "{{STEPS}}", "value": "steps"}],
            "initializer": {"kind": "test"},
            "execution": {"enabled": True, "steps": [{"id": "probe", "kind": "command", "argv": ["{python}", "driver.py"],
                                                       "timeout_seconds": 5, "expected_files": ["out.csv"]}],
                          "assertions": [], "scope": {
                              "paper": "paper1", "element_families": ["tooling fixture"], "active_physics": ["none"],
                              "state_variables": [{"name": "u", "kind": "primary", "owner": "bulk", "role": "fixture"}],
                              "residual_rows": ["u"], "tangent_columns": ["u"], "property_delta": "named input only",
                              "source_delta": "unchanged", "predecessor_gate": {"not_required": True, "reason": "Offline tooling fixture"}}}
        }

    def load(self, **changes: object) -> dict:
        self.data.update(changes)
        self.card.write_text(json.dumps(self.data), encoding="utf-8")
        return load_profile(self.card)

    def initial(self) -> dict:
        return {"values": {"displacement_m": 0.001}, "units": {"displacement_m": "m"},
                "checks": {"finite": True}, "scope": "fixture", "input_parameters": {"stiffness": 20.0, "steps": 5},
                "provenance": {"initializer": "test"}}

    def test_load_resolves_and_pins_assets(self) -> None:
        profile = self.load()
        self.assertEqual(Path(profile["parent_root"]), self.parent.resolve())
        result = registry_status(profile)
        self.assertTrue(result["matched"], result)
        self.assertTrue(result["execution_enabled"])

    def test_registration_never_guesses_changed_or_missing_source(self) -> None:
        profile = self.load()
        (self.parent / "unit.for").write_text("changed", encoding="ascii")
        self.assertFalse(registry_status(profile)["matched"])
        self.assertFalse(registry_status(profile)["execution_enabled"])

    def test_model_card_file_and_in_memory_drift_are_blocked(self) -> None:
        profile = self.load()
        self.card.write_text(self.card.read_text() + " ", encoding="utf-8")
        self.assertFalse(registry_status(profile)["matched"])
        profile = self.load()
        profile["source"] = "different.for"
        self.assertFalse(registry_status(profile)["matched"])

    def test_initializer_asset_hash_is_checked(self) -> None:
        material = self.root / "material.json"
        material.write_text("{}", encoding="utf-8")
        profile = self.load(initializer={"kind": "test", "material": {"path": "material.json", "sha256": sha(material)}})
        self.assertTrue(registry_status(profile)["matched"])
        material.write_text('{"changed":true}', encoding="utf-8")
        self.assertFalse(registry_status(profile)["matched"])

    def test_parameters_reject_bool_nonfinite_fraction_and_unknown(self) -> None:
        profile = self.load()
        self.assertEqual(validate_parameters(profile, {"stiffness": "20", "steps": "5"}), {"stiffness": 20.0, "steps": 5})
        for values in ({"stiffness": True, "steps": 5}, {"stiffness": "nan", "steps": 5},
                       {"stiffness": "inf", "steps": 5}, {"stiffness": 20, "steps": 2.5},
                       {"stiffness": 0, "steps": 5}, {"stiffness": 20, "steps": 101},
                       {"stiffness": 20, "steps": 5, "unknown": 1}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_parameters(profile, values)

    def test_named_bindings_generate_new_copy_and_leave_parent_unchanged(self) -> None:
        profile = self.load()
        before = {path.name: sha(path) for path in self.parent.iterdir()}
        target = self.root / "archive/statecontract_studio_fixture"
        result = prepare_case(profile, {"stiffness": "20", "steps": 5}, self.initial(), target)
        self.assertEqual(result["status"], "GENERATED", result)
        self.assertTrue(result["preflight"]["passed"], result)
        self.assertEqual((target / "parameters.dat").read_text(), "K=20\nU0=0.001\nSTEPS=5\n")
        self.assertEqual({path.name: sha(path) for path in self.parent.iterdir()}, before)
        self.assertEqual(sha(target / "unit.for"), before["unit.for"])
        self.assertEqual(result["snapshot_id"], "000002")
        self.assertTrue((target / "studio_project.json").is_file())
        with self.assertRaises(ValueError):
            prepare_case(profile, {"stiffness": 20, "steps": 5}, self.initial(), target)

    def test_bad_binding_or_source_binding_cannot_create_case(self) -> None:
        for binding in ({"file": "parameters.dat", "token": "missing", "value": "stiffness"},
                        {"file": "unit.for", "token": "END", "value": "stiffness"},
                        {"file": "../outside.dat", "token": "K", "value": "stiffness"}):
            profile = self.load(bindings=[binding])
            target = self.root / "archive/statecontract_bad_binding"
            with self.subTest(binding=binding), self.assertRaises(ValueError):
                prepare_case(profile, {"stiffness": 20, "steps": 5}, self.initial(), target)
            self.assertFalse(target.exists())

    def test_stale_or_failed_initial_state_cannot_create_case(self) -> None:
        profile = self.load()
        target = self.root / "archive/statecontract_stale"
        initial = self.initial()
        initial["input_parameters"]["stiffness"] = 10
        with self.assertRaises(ValueError):
            prepare_case(profile, {"stiffness": 20, "steps": 5}, initial, target)
        initial = self.initial()
        initial["checks"]["finite"] = False
        with self.assertRaises(ValueError):
            prepare_case(profile, {"stiffness": 20, "steps": 5}, initial, target)
        self.assertFalse(target.exists())

    def test_unmapped_parameters_and_wrong_initializer_cannot_be_silent(self) -> None:
        profile = self.load(bindings=self.data["bindings"][:2])
        target = self.root / "archive/statecontract_unmapped"
        with self.assertRaisesRegex(ValueError, "no declared file binding"):
            prepare_case(profile, {"stiffness": 20, "steps": 5}, self.initial(), target)
        self.assertFalse(target.exists())
        profile = self.load()
        initial = self.initial()
        initial["provenance"]["initializer"] = "another_adapter"
        with self.assertRaisesRegex(ValueError, "different initialization"):
            prepare_case(profile, {"stiffness": 20, "steps": 5}, initial, target)
        self.assertFalse(target.exists())

    def test_preview_card_can_match_but_cannot_prepare(self) -> None:
        profile = self.load(mode="preview", execution={"enabled": False})
        self.assertTrue(registry_status(profile)["matched"])
        self.assertFalse(registry_status(profile)["execution_enabled"])
        with self.assertRaises(ValueError):
            prepare_case(profile, {"stiffness": 20, "steps": 5}, self.initial(), self.root / "statecontract_preview")

    def test_unregistered_inputs_and_escaping_file_paths_block(self) -> None:
        profile = self.load(files={"unit.for": self.data["files"]["unit.for"]})
        self.assertFalse(registry_status(profile)["matched"])
        profile = self.load(files={"../unit.for": "0" * 64})
        self.assertFalse(registry_status(profile)["matched"])

    def test_history_requires_numeric_rectangular_finite_rows(self) -> None:
        path = self.root / "history.csv"
        path.write_text("time_s,force_N\n0,0\n1,2.5\n", encoding="utf-8")
        result = read_history(path)
        self.assertEqual(result["columns"], ["time_s", "force_N"])
        self.assertEqual(result["rows"][1]["force_N"], 2.5)
        for content in ("x,x\n0,0\n1,1\n", "x,y\n0,0\n", "x,y\n0,nan\n1,2\n",
                        "x,y\n0,\n1,2\n", "x,y\n0\n1,2\n", "x,y\n0,1,2\n1,2\n"):
            path.write_text(content, encoding="utf-8")
            with self.subTest(content=content), self.assertRaises(ValueError):
                read_history(path)

    def action_profile(self) -> dict:
        compile_script = self.parent / "compile.py"
        compile_script.write_text("from pathlib import Path\nPath('spring.exe').write_bytes(b'compiled fixture; not executable')\n", encoding="ascii")
        self.data["files"]["compile.py"] = sha(compile_script)
        self.data["includes"].append("compile.py")
        self.data["execution"]["steps"] = [
            {"id": "compile_spring", "role": "compile", "kind": "command", "argv": ["{python}", "compile.py"],
             "timeout_seconds": 5, "expected_files": ["spring.exe"]},
            {"id": "run_spring", "role": "run", "kind": "command", "argv": ["{work}/spring.exe"],
             "timeout_seconds": 5, "expected_files": ["out.csv"]}
        ]
        self.data["actions"] = {"all": ["compile_spring", "run_spring"], "compile": ["compile_spring"], "run": ["run_spring"]}
        return self.load()

    def compiled_run(self, profile: dict) -> dict:
        from uel_lab.devflow.execution import run_workflow
        target = self.root / "archive/statecontract_compile_fixture"
        prepared = prepare_action_case(profile, {"stiffness": 20, "steps": 5}, target, action="compile")
        self.assertEqual(prepared["status"], "GENERATED", prepared)
        run = run_workflow(target, execute=True)
        self.assertEqual(run["status"], "DIAGNOSTIC_COMPLETE", run)
        return run

    def test_actions_are_explicit_and_compile_never_selects_running(self) -> None:
        profile = self.load()
        self.assertEqual(available_actions(profile), {})
        with self.assertRaisesRegex(ValueError, "no registered"):
            prepare_action_case(profile, {"stiffness": 20, "steps": 5}, self.root / "statecontract_none", action="compile")
        profile = self.action_profile()
        self.assertEqual(available_actions(profile)["compile"], ["compile_spring"])
        self.data["actions"]["compile"] = ["run_spring"]
        with self.assertRaisesRegex(ValueError, "role=compile"):
            available_actions(self.load())

    def test_compile_only_needs_no_initial_and_keeps_initial_template(self) -> None:
        profile = self.action_profile()
        target = self.root / "archive/statecontract_compile_only"
        result = prepare_action_case(profile, {"stiffness": 20, "steps": 5}, target, action="compile")
        config = json.loads((target / "uel-dev.json").read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "GENERATED", result)
        self.assertEqual([step["id"] for step in config["steps"]], ["compile_spring"])
        self.assertEqual(config["assertions"], [])
        self.assertEqual(config["case_conditions"]["initial"]["status"], "not_generated")
        self.assertIn("{{U0}}", (target / "parameters.dat").read_text())
        self.assertFalse((target / "spring.exe").exists())

    def test_run_only_requires_declared_compilation_artifact(self) -> None:
        profile = self.action_profile()
        target = self.root / "archive/statecontract_run_without_exe"
        with self.assertRaisesRegex(ValueError, "requires a completed compilation"):
            prepare_action_case(profile, {"stiffness": 20, "steps": 5}, target, action="run", initial=self.initial())
        self.assertFalse(target.exists())

    def test_valid_compile_artifact_is_copied_and_snapshotted_in_new_case(self) -> None:
        profile = self.action_profile()
        compiled = self.compiled_run(profile)
        old_exe = Path(compiled["workdir"]) / "spring.exe"
        old_hash = sha(old_exe)
        target = self.root / "archive/statecontract_run_only"
        initial = self.initial()
        initial["input_parameters"]["stiffness"] = 30
        prepared = prepare_action_case(profile, {"stiffness": 30, "steps": 5}, target, action="run",
                                       initial=initial, reuse_run=Path(compiled["run_dir"]))
        self.assertEqual(prepared["status"], "GENERATED", prepared)
        self.assertEqual(sha(target / "spring.exe"), old_hash)
        self.assertEqual(sha(old_exe), old_hash)
        self.assertIn("K=30\n", (target / "parameters.dat").read_text())
        snapshot = json.loads((target / "snapshots/000002.json").read_text(encoding="utf-8"))
        self.assertEqual(snapshot["files"]["spring.exe"], old_hash)
        config = json.loads((target / "uel-dev.json").read_text(encoding="utf-8"))
        self.assertEqual([step["id"] for step in config["steps"]], ["run_spring"])
        self.assertTrue((target / "reused_compile.json").is_file())

    def test_reused_executable_tamper_and_different_source_are_rejected(self) -> None:
        profile = self.action_profile()
        compiled = self.compiled_run(profile)
        old_exe = Path(compiled["workdir"]) / "spring.exe"
        saved_bytes = old_exe.read_bytes()
        old_exe.write_bytes(b"changed executable")
        target = self.root / "archive/statecontract_changed_exe"
        with self.assertRaisesRegex(ValueError, "artifact is missing or changed"):
            prepare_action_case(profile, {"stiffness": 20, "steps": 5}, target, action="run",
                                initial=self.initial(), reuse_run=Path(compiled["run_dir"]))
        self.assertFalse(target.exists())
        old_exe.write_bytes(saved_bytes)
        source = self.parent / "unit.for"
        source.write_text("C A NEW REGISTERED SOURCE\n      END\n", encoding="ascii")
        self.data["files"]["unit.for"] = sha(source)
        changed_profile = self.load()
        with self.assertRaisesRegex(ValueError, "source/dependency hashes differ"):
            prepare_action_case(changed_profile, {"stiffness": 20, "steps": 5}, target, action="run",
                                initial=self.initial(), reuse_run=Path(compiled["run_dir"]))
        self.assertFalse(target.exists())

    def test_reference_run_action_requires_initial_but_no_compilation(self) -> None:
        self.data["execution"]["steps"][0]["role"] = "run"
        profile = self.load(actions={"all": ["probe"], "run": ["probe"]})
        target = self.root / "archive/statecontract_reference_run"
        with self.assertRaisesRegex(ValueError, "initial state"):
            prepare_action_case(profile, {"stiffness": 20, "steps": 5}, target, action="run")
        prepared = prepare_action_case(profile, {"stiffness": 20, "steps": 5}, target,
                                       action="run", initial=self.initial())
        self.assertEqual(prepared["status"], "GENERATED", prepared)

    def test_reuse_rejects_changed_compilation_flags_or_failed_evidence(self) -> None:
        profile = self.action_profile()
        compiled = self.compiled_run(profile)
        self.data["execution"]["steps"][0]["argv"].append("changed-build-option")
        changed = self.load()
        target = self.root / "archive/statecontract_reuse_wrong_flags"
        with self.assertRaisesRegex(ValueError, "compilation options differ"):
            prepare_action_case(changed, {"stiffness": 20, "steps": 5}, target, action="run",
                                initial=self.initial(), reuse_run=Path(compiled["run_dir"]))
        result_path = Path(compiled["run_dir"]) / "result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["status"] = "FAILED"
        result_path.write_text(json.dumps(result), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "completed successfully"):
            prepare_action_case(profile, {"stiffness": 20, "steps": 5}, target, action="run",
                                initial=self.initial(), reuse_run=Path(compiled["run_dir"]))
        self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
