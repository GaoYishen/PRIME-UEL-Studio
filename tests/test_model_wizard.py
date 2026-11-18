from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from prime_studio.core import load_profile, registry_status
from prime_studio.model_wizard import register_local_files
from prime_studio.gui import Studio


class Value:
    def __init__(self, value="") -> None:
        self.value = value

    def get(self):
        return self.value

    def set(self, value) -> None:
        self.value = value


class Widget:
    def __init__(self) -> None:
        self.options = {}

    def configure(self, **options) -> None:
        self.options.update(options)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class LocalModelRegistrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.parent = self.root / "model"
        self.parent.mkdir()
        self.source = self.parent / "unit.for"
        self.source.write_text("      END\n", encoding="ascii")
        self.inp = self.parent / "case.inp"
        self.inp.write_text("*Heading\nOnly registration\n", encoding="ascii")
        self.external = self.parent / "table.csv"
        self.external.write_text("x,y\n0,1\n", encoding="ascii")
        self.output = self.root / "registration"

    def register(self, **options) -> Path:
        values = dict(label="My UEL", parent_root=self.parent, source=self.source, inp=self.inp,
                      auxiliary=[self.external], dimension="2D Q4", output_dir=self.output)
        values.update(options)
        return register_local_files(**values)

    def test_explicit_files_are_pinned_without_modifying_originals(self) -> None:
        before = {path.name: sha(path) for path in self.parent.iterdir()}
        card_path = self.register()
        card = json.loads(card_path.read_text(encoding="utf-8"))
        self.assertEqual(card["files"], before)
        self.assertEqual(card["dimension"], "2D Q4")
        self.assertEqual(card["parameters"], {})
        self.assertEqual(card["initializer"]["kind"], "unconfigured")
        self.assertFalse(card["execution"]["enabled"])
        self.assertEqual({path.name: sha(path) for path in self.parent.iterdir()}, before)
        status = registry_status(load_profile(card_path))
        self.assertTrue(status["matched"], status)
        self.assertFalse(status["execution_enabled"])

    def test_source_only_registration_is_supported_and_unique(self) -> None:
        first = self.register(inp=None, auxiliary=[])
        second = self.register(inp=None, auxiliary=[])
        self.assertNotEqual(first, second)
        self.assertNotIn("inp", json.loads(first.read_text(encoding="utf-8")))
        self.assertTrue(registry_status(load_profile(second))["matched"])

    def test_external_and_missing_inputs_are_rejected(self) -> None:
        outside = self.root / "outside.for"
        outside.write_text("      END\n", encoding="ascii")
        for values in ({"source": outside}, {"source": self.parent / "missing.for"},
                       {"auxiliary": [outside]}, {"inp": self.external}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.register(**values)
        self.assertFalse(self.output.exists())

    def test_output_inside_original_model_is_rejected(self) -> None:
        for output in (self.parent, self.parent / "registration"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                self.register(output_dir=output)
        self.assertEqual(sorted(path.name for path in self.parent.iterdir()), ["case.inp", "table.csv", "unit.for"])

    def test_duplicate_and_case_alias_selections_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.register(auxiliary=[self.source])
        with self.assertRaises(ValueError):
            self.register(auxiliary=[self.external, self.external])

    def test_symlink_escape_does_not_register_external_source(self) -> None:
        outside = self.root / "outside.for"
        outside.write_text("      END\n", encoding="ascii")
        link = self.parent / "linked.for"
        try:
            link.symlink_to(outside)
        except OSError as error:
            self.skipTest(f"Symlinks unavailable: {error}")
        with self.assertRaises(ValueError):
            self.register(source=link)

    def test_empty_labels_and_unknown_dimensions_are_rejected(self) -> None:
        for values in ({"label": " "}, {"label": "bad\nname"}, {"dimension": "unknown"}, {"source": self.inp}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.register(**values)


class ModularGuiLogicTests(unittest.TestCase):
    """Exercise action routing with stand-ins, without opening a Tk window."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def studio(self) -> Studio:
        studio = Studio.__new__(Studio)
        studio.profile = {"mode": "offline", "execution": {"enabled": True},
                          "initializer": {"kind": "spring_demo"}}
        studio.initial = None
        studio.case = None
        studio.prepared_action = None
        studio.reuse_run = None
        studio.registration_ok = True
        studio.busy = False
        studio.process = None
        studio.workspace = self.root
        studio.entries = []
        studio.actions = lambda: {"all": ["compile", "run"], "compile": ["compile"], "run": ["run"]}
        studio.selected_action = lambda: "all"
        studio.parameters = lambda: {"sample": 1.0}
        studio.error = lambda error: (_ for _ in ()).throw(error)
        for name in ("initial_button", "export_initial_button", "compile_button", "prepare_button", "run_button",
                     "model_check_button", "check_button", "files_button", "reuse_button", "stop_button",
                     "selector", "action_selector"):
            setattr(studio, name, Widget())
        return studio

    def test_compile_routing_does_not_require_or_compute_initial_state(self) -> None:
        studio = self.studio()
        calls = []
        studio.background = lambda _message, function, _callback: calls.append(function())
        with patch("prime_studio.gui.prepare_action_case", return_value={"status": "GENERATED"}) as prepare:
            studio.prepare("compile", auto_start=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(prepare.call_args.kwargs["action"], "compile")
        self.assertIsNone(prepare.call_args.kwargs["initial"])
        self.assertIsNone(prepare.call_args.kwargs["reuse_run"])

    def test_run_only_routes_selected_artifact_and_current_initial_state(self) -> None:
        studio = self.studio()
        studio.initial = {"values": {}, "checks": {"finite": True}}
        studio.reuse_run = self.root / "completed_compile"
        studio.background = lambda _message, function, _callback: function()
        with patch("prime_studio.gui.prepare_action_case", return_value={}) as prepare:
            studio.prepare("run")
        self.assertEqual(prepare.call_args.kwargs["action"], "run")
        self.assertEqual(prepare.call_args.kwargs["reuse_run"], studio.reuse_run)
        self.assertEqual(prepare.call_args.kwargs["initial"], studio.initial)

    def test_compile_button_is_available_without_initial_state(self) -> None:
        studio = self.studio()
        studio.set_buttons()
        self.assertEqual(studio.compile_button.options["state"], "normal")
        self.assertEqual(studio.prepare_button.options["state"], "disabled")
        self.assertEqual(studio.run_button.options["state"], "disabled")

    def test_unconfigured_model_keeps_file_checks_but_disables_calculation(self) -> None:
        studio = self.studio()
        studio.profile = {"execution": {"enabled": False}, "initializer": {"kind": "unconfigured"}}
        studio.actions = lambda: {}
        studio.set_buttons()
        self.assertEqual(studio.check_button.options["state"], "normal")
        self.assertEqual(studio.initial_button.options["state"], "disabled")
        self.assertEqual(studio.compile_button.options["state"], "disabled")

    def test_csv_can_be_opened_without_any_model_or_initial_state(self) -> None:
        studio = self.studio()
        studio.profile = {}
        studio.series = Value()
        studio.series_selector = Widget()
        studio.result_note = Value()
        studio.draw_chart = lambda: None
        history = self.root / "history.csv"
        history.write_text("time_s,value\n0,1\n1,2\n", encoding="ascii")
        studio.load_csv(history)
        self.assertEqual(studio.x_column, "time_s")
        self.assertEqual(studio.series.get(), "value")
        self.assertEqual(len(studio.history["rows"]), 2)

    def test_project_exports_cannot_overwrite_registered_model(self) -> None:
        studio = self.studio()
        parent = self.root / "frozen_model"
        parent.mkdir()
        studio.profile = {"parent_root": str(parent), "_profile_path": str(self.root / "card.json")}
        for target in (parent / "initial.json", self.root / "card.json", self.root / "unit.for"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                studio.safe_project_path(target)
        self.assertEqual(studio.safe_project_path(self.root / "new_project.json"), self.root / "new_project.json")



if __name__ == "__main__":
    unittest.main()
