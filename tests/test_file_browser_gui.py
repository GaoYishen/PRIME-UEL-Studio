from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from prime_studio.file_browser_panel import FileBrowserPanel
from prime_studio.gui import Studio


class Value:
    def __init__(self, value="") -> None:
        self.value = value

    def set(self, value) -> None:
        self.value = value

    def get(self):
        return self.value


class Tree:
    def __init__(self) -> None:
        self.nodes = {}
        self.selected = None

    def get_children(self):
        return tuple(self.nodes)

    def delete(self, *_items) -> None:
        self.nodes.clear()

    def insert(self, parent, _position, **item) -> None:
        if parent and parent not in self.nodes:
            raise AssertionError("Tree parent does not exist")
        self.nodes[item["iid"]] = {"parent": parent, **item}

    def selection_set(self, item) -> None:
        self.selected = item

    def focus(self, _item) -> None:
        pass

    def see(self, _item) -> None:
        pass


class FileBrowserGuiTests(unittest.TestCase):
    """Check file/view routing without starting Tk or opening native apps."""

    def panel(self) -> FileBrowserPanel:
        panel = FileBrowserPanel.__new__(FileBrowserPanel)
        panel.profile = {"id": "selected_model"}
        panel.model = {"kind": "model", "items": []}
        panel.index = panel.model
        panel.draft = {"kind": "draft", "items": []}
        panel.draft_path = Path("existing_draft")
        panel.choice = Value(FileBrowserPanel.DRAFT_LABEL)
        panel.summary = Value()
        panel.busy = False
        panel._display_current = Mock()
        panel._render = Mock()
        panel.set_busy = Mock()
        return panel

    def test_switching_model_clears_previous_draft_association(self) -> None:
        panel = self.panel()
        panel.refresh = Mock()
        selected = {"id": "another_model"}
        panel.set_profile(selected)
        self.assertIs(panel.profile, selected)
        self.assertIsNone(panel.draft)
        self.assertIsNone(panel.draft_path)
        self.assertEqual(panel.choice.get(), panel.MODEL_LABEL)
        panel.refresh.assert_called_once_with()

    def test_refresh_revalidates_and_retains_the_selected_draft(self) -> None:
        panel = self.panel()
        refreshed = {"kind": "draft", "items": [{"relative": "CASE.inp"}]}
        with patch("prime_studio.file_browser_panel.model_index", return_value=panel.model), \
             patch("prime_studio.file_browser_panel.draft_index", return_value=refreshed) as read:
            panel.refresh()
        read.assert_called_once_with(panel.profile, Path("existing_draft"))
        self.assertIs(panel.draft, refreshed)
        self.assertEqual(panel.choice.get(), panel.DRAFT_LABEL)
        panel._display_current.assert_called_once_with()

    def test_wrong_model_draft_is_rejected_without_replacing_the_current_view(self) -> None:
        panel = self.panel()
        previous_draft = panel.draft
        previous_index = panel.index
        with patch("prime_studio.file_browser_panel.draft_index", side_effect=ValueError("Wrong parent")):
            with self.assertRaisesRegex(ValueError, "Wrong parent"):
                panel.open_draft(Path("different_model_draft"))
        self.assertIs(panel.draft, previous_draft)
        self.assertIs(panel.index, previous_index)
        self.assertEqual(panel.draft_path, Path("existing_draft"))
        panel._display_current.assert_not_called()

    def test_unsafe_absolute_or_traversal_keys_remain_flat_visible_leaves(self) -> None:
        panel = self.panel()
        panel.tree = Tree()
        panel.path_note = Value()
        panel.selection_changed = Mock()
        invalid = ["/outside.f90", "C:/outside.f90", "../outside.f90", "\\\\server\\share\\outside.f90"]
        items = [{"relative": key, "integrity": "unsafe", "status": "路径越界", "size": None} for key in invalid]
        items.append({"relative": "program/unit.f90", "integrity": "matched", "status": "匹配", "size": 12})
        FileBrowserPanel._render(panel, {"items": items})
        leaves = {node["text"]: node for key, node in panel.tree.nodes.items() if key.startswith("file:")}
        self.assertEqual(len(leaves), 5)
        for key in invalid:
            self.assertEqual(leaves[key]["parent"], "")
            self.assertEqual(leaves[key]["values"][1], "路径越界")
        self.assertEqual(leaves["unit.f90"]["parent"], "dir:program")
        self.assertEqual({key for key in panel.tree.nodes if key.startswith("dir:")}, {"dir:program"})

    def test_new_a5_draft_attaches_to_browser_without_becoming_an_executable_case(self) -> None:
        studio = Studio.__new__(Studio)
        existing_case = Path("unrelated_execution_case")
        studio.case = existing_case
        studio.profile = {"execution": {"enabled": False}}
        studio.status = Value()
        studio.file_browser = Mock()
        studio.model_notebook = Mock()
        studio.show_page = Mock()
        studio.a5_draft_created({"status": "DRAFT_GENERATED", "execution_enabled": False, "draft": "new_draft"})
        self.assertIs(studio.case, existing_case)
        self.assertFalse(studio.profile["execution"]["enabled"])
        studio.file_browser.open_draft.assert_called_once_with(Path("new_draft"))
        studio.model_notebook.select.assert_called_once_with(studio.file_browser)
        studio.show_page.assert_called_once_with("模型与文件")

    def test_binary_double_click_cannot_launch_a_viewer_or_os_file_handler(self) -> None:
        panel = self.panel()
        panel._selected = lambda: {"relative": "kernel.dll", "kind": "binary"}
        panel._error = Mock()
        result = {"status": "BINARY_UNAVAILABLE", "reason": "Binary files are not text"}
        with patch("prime_studio.file_browser_panel.read_text_preview", return_value=result) as read, \
             patch("prime_studio.file_browser_panel.tk.Toplevel") as window, \
             patch("prime_studio.file_browser_panel.os.startfile", create=True) as launch:
            panel.preview_selected()
        read.assert_called_once_with(panel.index, "kernel.dll")
        window.assert_not_called()
        launch.assert_not_called()
        panel._error.assert_called_once()

    def test_changed_source_is_blocked_before_any_text_window_opens(self) -> None:
        panel = self.panel()
        panel._selected = lambda: {"relative": "program/unit.f90", "kind": "text"}
        panel._error = Mock()
        with patch("prime_studio.file_browser_panel.read_text_preview", side_effect=ValueError("File hash changed")), \
             patch("prime_studio.file_browser_panel.tk.Toplevel") as window, \
             patch("prime_studio.file_browser_panel.os.startfile", create=True) as launch:
            panel.preview_selected()
        window.assert_not_called()
        launch.assert_not_called()
        panel._error.assert_called_once()

    def test_folder_reveal_passes_only_parent_directory_to_os_handler(self) -> None:
        panel = self.panel()
        panel._selected = lambda: {"relative": "program/unit.f90"}
        panel._error = Mock()
        source = Path("registered_model/program/unit.f90")
        with patch("prime_studio.file_browser_panel.resolve_file", return_value=source), \
             patch("prime_studio.file_browser_panel.os.name", "nt"), \
             patch("prime_studio.file_browser_panel.os.startfile", create=True) as launch:
            panel.reveal_selected()
        launch.assert_called_once_with(str(source.parent))
        panel._error.assert_not_called()


if __name__ == "__main__":
    unittest.main()
