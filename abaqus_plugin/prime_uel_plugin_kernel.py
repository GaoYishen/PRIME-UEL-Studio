"""Kernel-side Abaqus/CAE bridge for PRIME UEL Studio."""

from __future__ import print_function

import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

from abaqus import mdb, session
from abaqusConstants import OFF


def _current_model():
    viewport_name = session.currentViewportName
    try:
        state = session.sessionState[viewport_name]
    except (KeyError, TypeError):
        state = {}
    model_name = state.get("modelName")
    if model_name and model_name in mdb.models:
        return model_name, mdb.models[model_name]
    if mdb.models:
        model_name = sorted(mdb.models.keys())[0]
        return model_name, mdb.models[model_name]
    return "", None


def _config_path():
    return Path(__file__).resolve().with_name("prime_uel_plugin_config.json")


def _load_config():
    config_path = _config_path()
    if not config_path.is_file():
        raise RuntimeError("Missing prime_uel_plugin_config.json")
    return json.loads(config_path.read_text(encoding="utf-8"))


def _context(model_name, model):
    viewport_name = session.currentViewportName
    viewport = session.viewports[viewport_name]
    instances = []
    for name, instance in model.rootAssembly.instances.items():
        instances.append({
            "name": name,
            "part": instance.partName,
            "type": str(instance.type),
        })
    return {
        "source": "Abaqus/CAE",
        "abaqus_version": os.environ.get("ABAQUS_BAT_PATH", "Abaqus/CAE"),
        "current_model": model_name,
        "parts": sorted(model.parts.keys()),
        "instances": instances,
        "jobs": sorted(mdb.jobs.keys()),
        "steps": sorted(model.steps.keys()),
        "viewport": viewport_name,
        "displayed_object": str(getattr(viewport, "displayedObject", "")),
    }


def _selected_element_records(elements):
    """Convert AFXPickStep element objects into a JSON-safe handoff record."""
    if elements is None:
        return []
    if isinstance(elements, (tuple, list)):
        selected = list(elements)
    else:
        selected = [elements]
    records = []
    for element in selected:
        label = getattr(element, "label", None)
        if label is None:
            raise ValueError("The CAE selection does not contain mesh-element labels")
        connectivity = getattr(element, "connectivity", ())
        records.append({
            "label": int(label),
            "connectivity": [int(node) for node in connectivity],
            "element_type": str(getattr(element, "type", "")),
            "instance_name": str(getattr(element, "instanceName", "")),
        })
    return records


def _launch_studio(context):
    config = _load_config()
    handle, path = tempfile.mkstemp(prefix="prime_uel_cae_", suffix=".json")
    os.close(handle)
    context_path = Path(path)
    context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    launcher = Path(config["launcher"]).resolve()
    command = [config.get("python_launcher", "py"), "-3", str(launcher), "--cae-context", str(context_path)]
    subprocess.Popen(command, cwd=str(launcher.parent), creationflags=0x08000000)
    return context_path


def _export_current_model_input(model_name):
    """Write the current model to an isolated INP copy for Studio."""
    config = _load_config()
    root = Path(config.get("studio_root", str(Path.home()))).resolve()
    export_dir = root / "cae_exports" / ("model_" + uuid.uuid4().hex[:10])
    export_dir.mkdir(parents=True, exist_ok=True)
    job_name = "PRIME_EXPORT_" + uuid.uuid4().hex[:10]
    previous = os.getcwd()
    try:
        os.chdir(str(export_dir))
        mdb.Job(name=job_name, model=model_name).writeInput(consistencyChecking=OFF)
    finally:
        os.chdir(previous)
        if job_name in mdb.jobs:
            del mdb.jobs[job_name]
    input_file = export_dir / (job_name + ".inp")
    if not input_file.is_file():
        raise RuntimeError("Abaqus did not create the temporary model INP")
    return str(input_file)


def open_studio():
    model_name, model = _current_model()
    context = _context(model_name, model) if model is not None else {
        "source": "Abaqus/CAE",
        "current_model": "",
        "parts": [],
        "instances": [],
        "jobs": sorted(mdb.jobs.keys()),
        "steps": [],
        "viewport": session.currentViewportName,
        "displayed_object": "",
    }
    _launch_studio(context)
    print("PRIME UEL Studio opened for Abaqus model: %s" % model_name)


def open_interface_studio(solid_elements, liquid_elements, interface_kind="T3"):
    """Launch Studio with separate solid and liquid viewport selections."""
    kind = str(interface_kind).upper()
    expected = "2D_T3_REACTION_EDGE" if kind == "T3" else "3D_H8_REACTION_FACE" if kind == "H8" else ""
    if not expected:
        raise ValueError("interface_kind must be T3 or H8")
    model_name, model = _current_model()
    if model is None:
        raise RuntimeError("No Abaqus model is open")
    solid = _selected_element_records(solid_elements)
    liquid = _selected_element_records(liquid_elements)
    if not solid or not liquid:
        raise ValueError("Select at least one solid element and one liquid element")
    selected = solid + liquid
    context = _context(model_name, model)
    context.update({
        "interface_selection": {
            "kind": expected,
            "solid_elements": solid,
            "liquid_elements": liquid,
            "source": "Abaqus/CAE viewport AFXPickStep",
        },
        "selected_elements": selected,
        "solid_elements": solid,
        "liquid_elements": liquid,
    })
    context["input_file"] = _export_current_model_input(model_name)
    _launch_studio(context)
    print("PRIME interface selection handed to Studio: %s" % expected)
