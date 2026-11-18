from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from prime_studio import abaqus_results


class AbaqusResultsTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.result_dir = self.root / "battery result 中文"
        self.result_dir.mkdir()
        self.odb = self.result_dir / "actual result.ODB"
        self.odb.write_bytes(b"binary output database test fixture")
        self.launcher = self.root / "abaqus.bat"
        self.launcher.write_text("@echo off\n", encoding="ascii")
        self.handoff_dir = self.root / "new viewer log 中文"
        self.handoff_dir.mkdir()

    def test_inspection_is_read_only_and_accepts_uppercase_extension(self) -> None:
        before = {p.name: p.read_bytes() for p in self.result_dir.iterdir()}
        result = abaqus_results.inspect_odb(self.odb)
        self.assertTrue(result["ready"])
        self.assertEqual(result["size_bytes"], self.odb.stat().st_size)
        self.assertFalse(result["lock_present"])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.result_dir.iterdir()})

    def test_missing_wrong_suffix_and_empty_files_are_rejected(self) -> None:
        empty = self.root / "empty.odb"
        empty.touch()
        wrong = self.root / "file.inp"
        wrong.write_text("*Heading", encoding="ascii")
        for path in (self.root / "missing.odb", wrong, empty, self.root):
            with self.subTest(path=path), patch.object(abaqus_results.subprocess, "Popen") as launch:
                self.assertFalse(abaqus_results.inspect_odb(path)["ready"])
                with self.assertRaises(ValueError):
                    abaqus_results.open_in_abaqus(path, self.launcher)
                launch.assert_not_called()

    def test_matching_lock_blocks_handoff_and_is_never_removed(self) -> None:
        lock = self.odb.with_suffix(".LCK")
        lock.write_bytes(b"owned by a running job or database")
        with patch.object(abaqus_results.subprocess, "Popen") as launch:
            result = abaqus_results.inspect_odb(self.odb)
            self.assertTrue(result["lock_present"])
            self.assertFalse(result["ready"])
            with self.assertRaises(ValueError):
                abaqus_results.open_in_abaqus(self.odb, self.launcher)
            launch.assert_not_called()
        self.assertEqual(lock.read_bytes(), b"owned by a running job or database")

    def test_unrelated_lock_does_not_block_selected_odb(self) -> None:
        (self.result_dir / "another.lck").touch()
        self.assertTrue(abaqus_results.inspect_odb(self.odb)["ready"])

    def test_shell_control_characters_are_rejected_before_launch(self) -> None:
        # All names below are valid Windows filenames; each is dangerous to a
        # command wrapper even if shell=False is used for the outer cmd.exe.
        for name in ("file%PATH%.odb", "file!value.odb", "file&whoami.odb", "file^name.odb"):
            with self.subTest(name=name), patch.object(abaqus_results.subprocess, "Popen") as launch:
                path = self.result_dir / name
                path.write_bytes(b"odb")
                self.assertFalse(abaqus_results.inspect_odb(path)["ready"])
                with self.assertRaises(ValueError):
                    abaqus_results.open_in_abaqus(path, self.launcher)
                launch.assert_not_called()

    def test_handoff_uses_only_viewer_database_option_and_child_environment(self) -> None:
        process = Mock(pid=4123)
        parent_env = {"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PRIME_TEST_PARENT": "kept"}
        original = self.odb.read_bytes()
        with patch.dict(os.environ, parent_env), \
                patch.object(abaqus_results.tempfile, "mkdtemp", return_value=str(self.handoff_dir)), \
                patch.object(abaqus_results.subprocess, "Popen", return_value=process) as launch:
            result = abaqus_results.open_in_abaqus(self.odb, self.launcher)
            self.assertEqual(os.environ["PYTHONUTF8"], "1")
            self.assertEqual(os.environ["PYTHONIOENCODING"], "utf-8")
        self.assertEqual(result["status"], "VIEWER_LAUNCH_REQUESTED")
        self.assertFalse(result["analysis_submitted"])
        self.assertFalse(result["viewer_window_verified"])
        self.assertEqual(result["pid"], 4123)
        args, options = launch.call_args
        self.assertEqual(args[0][:4], ["cmd.exe", "/d", "/c", "call"])
        self.assertEqual(Path(args[0][4]), Path(result["wrapper"]))
        self.assertEqual(options["cwd"], self.result_dir.resolve())
        self.assertIs(options["shell"], False)
        self.assertNotIn("PYTHONUTF8", options["env"])
        self.assertNotIn("PYTHONIOENCODING", options["env"])
        self.assertEqual(options["env"]["PRIME_STUDIO_VIEWER_ODB"], str(self.odb.resolve()))
        self.assertEqual(options["env"]["PRIME_STUDIO_VIEWER_LAUNCHER"], str(self.launcher.resolve()))
        self.assertEqual(options["env"]["PRIME_TEST_PARENT"], "kept")
        wrapper = Path(result["wrapper"]).read_text(encoding="ascii")
        self.assertIn('viewer "database=%PRIME_STUDIO_VIEWER_ODB%"', wrapper)
        self.assertNotIn(str(self.odb), wrapper)
        self.assertNotIn("job=", wrapper)
        self.assertNotIn("user=", wrapper)
        self.assertNotIn("datacheck", wrapper)
        self.assertEqual(self.odb.read_bytes(), original)
        self.assertEqual(list(self.result_dir.iterdir()), [self.odb])

    def test_missing_or_unsafe_launcher_never_creates_a_handoff(self) -> None:
        unsafe = self.root / "abaqus%PATH%.bat"
        unsafe.write_text("@echo off\n", encoding="ascii")
        for path in (self.root / "missing.bat", unsafe, self.odb):
            with self.subTest(path=path), \
                    patch.object(abaqus_results.tempfile, "mkdtemp") as temporary, \
                    patch.object(abaqus_results.subprocess, "Popen") as launch:
                with self.assertRaises(ValueError):
                    abaqus_results.open_in_abaqus(self.odb, path)
                temporary.assert_not_called()
                launch.assert_not_called()

    def test_new_lock_during_handoff_blocks_launch(self) -> None:
        original_inspection = abaqus_results.inspect_odb(self.odb)
        locked = {**original_inspection, "ready": False, "lock_present": True}
        with patch.object(abaqus_results, "inspect_odb", side_effect=[original_inspection, locked]), \
                patch.object(abaqus_results.tempfile, "mkdtemp", return_value=str(self.handoff_dir)), \
                patch.object(abaqus_results.subprocess, "Popen") as launch:
            with self.assertRaises(ValueError):
                abaqus_results.open_in_abaqus(self.odb, self.launcher)
            launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
