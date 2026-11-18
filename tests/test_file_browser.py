from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from prime_studio import file_browser
from prime_studio.core import load_profile


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class FileBrowserContractTests(unittest.TestCase):
    """Exercise browsing real files without a Tk window or launching a file."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.parent = self.root / "frozen_parent"
        payloads = {
            "program/unit.f90": b"program unit\n! read only\nend program\n",
            "cases/full/CASE.inp": b"*HEADING\nFixture\n*INCLUDE,INPUT=include/properties.inc\n",
            "include/properties.inc": b"*UEL PROPERTY, ELSET=FIXTURE\n1.,2.,3.\n",
            "driver.py": b"raise RuntimeError('must never execute during preview')\n",
            "tables/material.txt": "材料表\n温度=298.15\n".encode("utf-8"),
            "native.dll": b"MZ\x00\x00not a runnable library\x00",
        }
        for relative, data in payloads.items():
            path = self.parent / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        self.card = self.root / "model.json"
        self.card_data = {
            "schema_version": 1, "id": "browser_fixture", "label": "File browser fixture",
            "mode": "preview", "parent_root": str(self.parent), "source": "program/unit.f90",
            "inp": "cases/full/CASE.inp",
            "includes": [name for name in payloads if name not in {"program/unit.f90", "cases/full/CASE.inp"}],
            "files": {name: sha(self.parent / name) for name in payloads},
            "parameters": {}, "bindings": [], "initializer": {"kind": "unconfigured"},
            "actions": {}, "execution": {"enabled": False},
        }
        self.profile = self.reload()

    def reload(self) -> dict:
        self.card.write_text(json.dumps(self.card_data), encoding="utf-8")
        return load_profile(self.card)

    def baseline_hashes(self) -> dict[str, str]:
        return {name: sha(self.parent / name) for name in self.profile["files"]}

    def items(self, index: dict) -> dict[str, dict]:
        return {item["relative"]: item for item in index["items"]}

    def draft(self) -> Path:
        target = self.root / "copied_draft"
        target.mkdir()
        for relative in self.profile["files"]:
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.parent / relative, destination)
        state = {"status": "generated_draft_not_run", "execution_enabled": False,
                 "immutable_parent": str(self.parent.resolve()), "parent_sha256": self.profile["files"]}
        (target / "statecontract.json").write_text(json.dumps(state), encoding="utf-8")
        (target / "changes.diff").write_text("--- parent\n+++ draft\n", encoding="utf-8")
        artifacts = {relative: sha(target / relative) for relative in self.profile["files"]}
        artifacts.update({relative: sha(target / relative) for relative in ("statecontract.json", "changes.diff")})
        manifest = {
            "status": "DRAFT_GENERATED", "execution_enabled": False, "runtime_result": "not_run",
            "case": str(target.resolve()), "immutable_parent": str(self.parent.resolve()),
            "model_card_sha256": self.profile["_profile_sha256"], "source_byte_identical": True,
            "parent_hashes_rechecked": True, "artifacts_sha256": artifacts,
        }
        (target / "draft_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return target

    def edit_manifest(self, draft: Path, **changes) -> None:
        path = draft / "draft_manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(changes)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_model_shows_explicit_sources_inputs_and_dependencies_only(self) -> None:
        before = self.baseline_hashes()
        (self.parent / "unregistered_secret.txt").write_text("not part of this model", encoding="utf-8")
        index = file_browser.model_index(self.profile)
        self.assertFalse(index["execution_enabled"])
        self.assertTrue(index["matched"])
        self.assertEqual(set(self.items(index)), set(self.profile["files"]))
        for relative, item in self.items(index).items():
            self.assertEqual(Path(item["path"]), (self.parent / relative).resolve())
            self.assertEqual(item["integrity"], "matched")
            self.assertTrue(item["exists"])
            self.assertTrue(item["role"])
            self.assertEqual(item["size"], (self.parent / relative).stat().st_size)
        self.assertEqual(before, self.baseline_hashes())

    def test_fortran_and_script_are_read_as_text_without_execution(self) -> None:
        before = self.baseline_hashes()
        index = file_browser.model_index(self.profile)
        source = file_browser.read_text_preview(index, "program/unit.f90")
        script = file_browser.read_text_preview(index, "driver.py")
        self.assertEqual(source["status"], "TEXT_PREVIEW")
        self.assertIn("end program", source["text"])
        self.assertIn("must never execute", script["text"])
        self.assertEqual(before, self.baseline_hashes())

    def test_utf8_material_text_is_preserved(self) -> None:
        result = file_browser.read_text_preview(file_browser.model_index(self.profile), "tables/material.txt")
        self.assertEqual(result["status"], "TEXT_PREVIEW")
        self.assertIn("材料表", result["text"])
        self.assertIn("温度=298.15", result["text"])

    def test_utf8_preview_does_not_misdecode_a_truncated_chinese_character(self) -> None:
        path = self.parent / "tables/material.txt"
        path.write_text("材料" * 100, encoding="utf-8")
        self.card_data["files"]["tables/material.txt"] = sha(path)
        self.profile = self.reload()
        result = file_browser.read_text_preview(file_browser.model_index(self.profile), "tables/material.txt", max_bytes=4)
        self.assertEqual(result["status"], "TEXT_PREVIEW")
        self.assertTrue(result["truncated"])
        self.assertEqual(result["text"], "材")

    def test_legacy_chinese_comment_encoding_is_identified(self) -> None:
        path = self.parent / "tables/legacy.dat"
        path.write_bytes("材料参数\n".encode("gb18030"))
        self.card_data["files"]["tables/legacy.dat"] = sha(path)
        self.profile = self.reload()
        result = file_browser.read_text_preview(file_browser.model_index(self.profile), "tables/legacy.dat")
        self.assertEqual(result["status"], "TEXT_PREVIEW")
        self.assertEqual(result["text"], "材料参数\n")

    def test_missing_registered_file_remains_visible_and_cannot_be_opened(self) -> None:
        (self.parent / "cases/full/CASE.inp").unlink()
        index = file_browser.model_index(self.profile)
        row = self.items(index)["cases/full/CASE.inp"]
        self.assertEqual(row["integrity"], "missing")
        self.assertFalse(row["exists"])
        self.assertFalse(row["openable"])
        with self.assertRaises(ValueError):
            file_browser.resolve_file(index, "cases/full/CASE.inp")

    def test_hash_mismatch_remains_visible_and_blocks_preview(self) -> None:
        (self.parent / "include/properties.inc").write_text("modified property\n", encoding="utf-8")
        index = file_browser.model_index(self.profile)
        row = self.items(index)["include/properties.inc"]
        self.assertEqual(row["integrity"], "mismatch")
        self.assertFalse(row["openable"])
        with self.assertRaises(ValueError):
            file_browser.read_text_preview(index, "include/properties.inc")

    def test_file_mutation_after_index_is_not_read_with_a_stale_hash(self) -> None:
        index = file_browser.model_index(self.profile)
        (self.parent / "program/unit.f90").write_text("changed after preview\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            file_browser.read_text_preview(index, "program/unit.f90")

    def test_index_cannot_be_repointed_to_an_unregistered_file(self) -> None:
        index = file_browser.model_index(self.profile)
        tampered = deepcopy(index)
        row = self.items(tampered)["program/unit.f90"]
        row["path"] = str(self.root / "other.f90")
        (self.root / "other.f90").write_text("different source", encoding="utf-8")
        with self.assertRaises(ValueError):
            file_browser.resolve_file(tampered, "program/unit.f90")

    def test_unlisted_file_and_parent_traversal_never_resolve(self) -> None:
        index = file_browser.model_index(self.profile)
        outside = self.root / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        for relative in ("../outside.txt", "unregistered.txt", str(outside.resolve())):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                file_browser.resolve_file(index, relative)

    def test_registered_symlink_escape_is_not_openable(self) -> None:
        outside = self.root / "external.txt"
        outside.write_text("external", encoding="utf-8")
        link = self.parent / "escaped.txt"
        try:
            link.symlink_to(outside)
        except OSError as error:
            self.skipTest(f"Symlinks unavailable: {error}")
        self.card_data["files"]["escaped.txt"] = sha(outside)
        self.profile = self.reload()
        index = file_browser.model_index(self.profile)
        self.assertEqual(self.items(index)["escaped.txt"]["integrity"], "unsafe")
        with self.assertRaises(ValueError):
            file_browser.resolve_file(index, "escaped.txt")

    def test_binary_asset_is_visible_but_has_no_text_preview(self) -> None:
        index = file_browser.model_index(self.profile)
        row = self.items(index)["native.dll"]
        self.assertEqual(row["kind"], "binary")
        result = file_browser.read_text_preview(index, "native.dll")
        self.assertEqual(result["status"], "BINARY_UNAVAILABLE")
        self.assertEqual(result["text"], "")
        self.assertEqual(file_browser.resolve_file(index, "native.dll"), (self.parent / "native.dll").resolve())

    def test_large_text_preview_is_bounded_and_reports_truncation(self) -> None:
        path = self.parent / "large.dat"
        path.write_bytes(b"coefficient=1\n" * 200000)
        self.card_data["files"]["large.dat"] = sha(path)
        self.profile = self.reload()
        result = file_browser.read_text_preview(file_browser.model_index(self.profile), "large.dat", max_bytes=64)
        self.assertEqual(result["status"], "TEXT_PREVIEW")
        self.assertTrue(result["truncated"])
        self.assertEqual(result["size"], path.stat().st_size)
        self.assertLessEqual(len(result["text"].encode("utf-8")), 64)
        self.assertTrue(result["text"].startswith("coefficient=1"))

    def test_explicit_initializer_asset_can_be_inspected_without_guessing(self) -> None:
        material = self.root / "external_material.json"
        material.write_text('{"phase":"fixture"}\n', encoding="utf-8")
        self.card_data["initializer"]["material"] = {"path": str(material), "sha256": sha(material)}
        self.profile = self.reload()
        index = file_browser.model_index(self.profile)
        row = self.items(index)["@initializer/material"]
        self.assertEqual(Path(row["path"]), material.resolve())
        self.assertEqual(row["integrity"], "matched")
        result = file_browser.read_text_preview(index, "@initializer/material")
        self.assertIn('"phase":"fixture"', result["text"])

    def test_draft_lists_manifest_artifacts_and_keeps_sources_read_only(self) -> None:
        before = self.baseline_hashes()
        draft = self.draft()
        (draft / "unlisted.txt").write_text("not a registered artifact", encoding="utf-8")
        index = file_browser.draft_index(self.profile, draft)
        expected = set(self.profile["files"]) | {"statecontract.json", "changes.diff", "draft_manifest.json"}
        self.assertEqual(set(self.items(index)), expected)
        self.assertFalse(index["execution_enabled"])
        self.assertTrue(index["matched"])
        self.assertIn("end program", file_browser.read_text_preview(index, "program/unit.f90")["text"])
        self.assertEqual(before, self.baseline_hashes())

    def test_missing_draft_artifact_is_visible_and_blocked(self) -> None:
        draft = self.draft()
        (draft / "include/properties.inc").unlink()
        index = file_browser.draft_index(self.profile, draft)
        self.assertEqual(self.items(index)["include/properties.inc"]["integrity"], "missing")
        with self.assertRaises(ValueError):
            file_browser.read_text_preview(index, "include/properties.inc")

    def test_modified_draft_artifact_is_not_silently_trusted(self) -> None:
        draft = self.draft()
        (draft / "include/properties.inc").write_text("different copied values", encoding="utf-8")
        index = file_browser.draft_index(self.profile, draft)
        self.assertEqual(self.items(index)["include/properties.inc"]["integrity"], "mismatch")
        with self.assertRaises(ValueError):
            file_browser.resolve_file(index, "include/properties.inc")

    def test_source_identity_reflects_the_actual_copied_source_not_the_manifest_claim(self) -> None:
        draft = self.draft()
        source = draft / "program/unit.f90"
        original = source.read_bytes()
        for integrity in ("missing", "mismatch"):
            with self.subTest(integrity=integrity):
                source.write_bytes(original)
                if integrity == "missing":
                    source.unlink()
                else:
                    source.write_text("changed without updating manifest\n", encoding="utf-8")
                index = file_browser.draft_index(self.profile, draft)
                row = self.items(index)["program/unit.f90"]
                self.assertEqual(row["integrity"], integrity)
                self.assertFalse(row["openable"])
                self.assertFalse(index["source_byte_identical"])
                with self.assertRaises(ValueError):
                    file_browser.resolve_file(index, "program/unit.f90")

    def test_wrong_profile_parent_or_executable_manifest_is_rejected(self) -> None:
        draft = self.draft()
        manifest = json.loads((draft / "draft_manifest.json").read_text(encoding="utf-8"))
        for changes in ({"model_card_sha256": "0" * 64},
                        {"immutable_parent": str(self.root / "other_parent")},
                        {"case": str(self.root / "other_draft")},
                        {"execution_enabled": True}, {"status": "DRAFT_INCOMPLETE"}):
            with self.subTest(changes=changes):
                (draft / "draft_manifest.json").write_text(json.dumps({**manifest, **changes}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    file_browser.draft_index(self.profile, draft)

    def test_untrusted_state_identity_is_rejected_before_using_parent_hashes(self) -> None:
        draft = self.draft()
        state = draft / "statecontract.json"
        document = json.loads(state.read_text(encoding="utf-8"))
        document["parent_sha256"]["program/unit.f90"] = "0" * 64
        state.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaises(ValueError):
            file_browser.draft_index(self.profile, draft)
        manifest_path = draft / "draft_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts_sha256"]["statecontract.json"] = sha(state)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ValueError):
            file_browser.draft_index(self.profile, draft)

    def test_draft_cannot_hide_a_registered_dependency_from_its_artifact_index(self) -> None:
        draft = self.draft()
        path = draft / "draft_manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        del manifest["artifacts_sha256"]["include/properties.inc"]
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ValueError):
            file_browser.draft_index(self.profile, draft)

    def test_a_rehashed_changed_source_does_not_become_the_frozen_source(self) -> None:
        draft = self.draft()
        source = draft / "program/unit.f90"
        source.write_text("program different\nend program\n", encoding="utf-8")
        manifest_path = draft / "draft_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifacts_sha256"]["program/unit.f90"] = sha(source)
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ValueError):
            file_browser.draft_index(self.profile, draft)


@unittest.skipUnless(os.environ.get("PRIME_FILE_BROWSER_PROFILE") and os.environ.get("PRIME_FILE_BROWSER_DRAFT"),
                     "Set local profile and draft paths for the read-only actual A5 browsing check")
class LocalA5FileBrowserTests(unittest.TestCase):
    def test_existing_a5_model_and_draft_remain_unchanged(self) -> None:
        profile = load_profile(Path(os.environ["PRIME_FILE_BROWSER_PROFILE"]))
        parent = Path(profile["parent_root"])
        before = {relative: sha(parent / relative) for relative in profile["files"]}
        model = file_browser.model_index(profile)
        draft = file_browser.draft_index(profile, Path(os.environ["PRIME_FILE_BROWSER_DRAFT"]))
        self.assertTrue(model["matched"])
        self.assertTrue(draft["matched"])
        self.assertFalse(draft["execution_enabled"])
        preview = file_browser.read_text_preview(draft, profile["inp"], max_bytes=8192)
        self.assertEqual(preview["status"], "TEXT_PREVIEW")
        self.assertIn("*INCLUDE", preview["text"])
        self.assertEqual(before, {relative: sha(parent / relative) for relative in profile["files"]})


if __name__ == "__main__":
    unittest.main()
