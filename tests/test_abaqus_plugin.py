from pathlib import Path
import ast
import json


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "abaqus_plugin"


def test_plugin_registers_a_native_kernel_menu_button():
    text = (PLUGIN / "prime_uel_plugin.py").read_text(encoding="utf-8")
    assert "registerKernelMenuButton" in text
    assert "PRIME UEL Studio..." in text
    assert "prime_uel_plugin_kernel" in text


def test_plugin_registers_viewport_pickers_for_t3_and_h8():
    text = (PLUGIN / "prime_uel_plugin_gui.py").read_text(encoding="utf-8")
    for token in ("AFXPickStep", "AFXPickStep.ELEMENTS", "solidKw", "liquidKw", "getNextStep", "选择并生成 T3 反应边", "选择并生成 H8 反应面", "open_interface_studio"):
        assert token in text


def test_kernel_bridge_writes_current_model_context_and_launches_studio():
    text = (PLUGIN / "prime_uel_plugin_kernel.py").read_text(encoding="utf-8")
    for token in ("current_model", "parts", "instances", "jobs", "steps", "--cae-context"):
        assert token in text
    for token in ("_selected_element_records", "_export_current_model_input", "input_file", "selected_elements", "solid_elements", "liquid_elements"):
        assert token in text


def test_plugin_installer_declares_the_expected_files():
    text = (ROOT / "tools" / "install_abaqus_plugin.py").read_text(encoding="utf-8")
    for name in ("prime_uel_plugin.py", "prime_uel_plugin_gui.py", "prime_uel_plugin_kernel.py", "prime_uel_plugin_help.html"):
        assert name in text


def test_phase_picker_finishes_after_two_selection_steps():
    tree = ast.parse((PLUGIN / "prime_uel_plugin_gui.py").read_text(encoding="utf-8"))
    picker_class = next(node for node in tree.body if isinstance(node, ast.ClassDef))

    class Procedure:
        def __init__(self, owner):
            pass

    class Step:
        ELEMENTS, MANY, TUPLE = range(3)

        def __init__(self, *args):
            self.keyword = args[1]

    namespace = {"AFXProcedure": Procedure, "AFXPickStep": Step,
                 "AFXGuiCommand": lambda *args: object(),
                 "AFXObjectKeyword": lambda *args: args[1],
                 "AFXStringKeyword": lambda *args: args[1]}
    exec(compile(ast.Module(body=[picker_class], type_ignores=[]), "picker", "exec"), namespace)
    picker = namespace["ReactionInterfacePicker"](None, "T3")
    solid = picker.getFirstStep()
    liquid = picker.getNextStep(solid)
    assert solid.keyword == "solid_elements"
    assert liquid.keyword == "liquid_elements"
    assert picker.getNextStep(liquid) is None


def test_phase_label_input_accepts_one_element_per_phase():
    from prime_studio.interface_generator_window import _labels
    assert _labels("101") == [101]
    assert _labels("101, 102, 101") == [101, 102]
