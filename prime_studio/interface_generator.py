"""Public topology generator for a copied 2D T3 reaction-edge case.

The generator owns mesh topology and provenance only.  The private adapter
supplies the validated UEL property card and solver-specific execution steps.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Sequence


_KEYWORD = re.compile(r"^\s*\*([^,\s]+)(.*)$", re.IGNORECASE)
_OPTION = re.compile(r"([A-Za-z_]+)\s*=\s*([^,\s]+)", re.IGNORECASE)
_ALLOWED_PARENT_TYPES = {"T3", "CPE3", "CPS3", "CPEG3", "CAX3"}
_ALLOWED_H8_TYPES = {"H8", "C3D8"}
_H8_FACES = (
    (0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 7, 3),
    (1, 5, 6, 2), (0, 1, 5, 4), (3, 2, 6, 7),
)


@dataclass(frozen=True)
class Node:
    label: int
    coordinates: tuple[float, float]


@dataclass(frozen=True)
class Element:
    label: int
    element_type: str
    connectivity: tuple[int, int, int]


@dataclass(frozen=True)
class ParsedMesh:
    lines: tuple[str, ...]
    nodes: dict[int, Node]
    elements: dict[int, Element]
    element_block_end: int
    all_element_labels: tuple[int, ...]


@dataclass(frozen=True)
class H8Element:
    label: int
    element_type: str
    connectivity: tuple[int, int, int, int, int, int, int, int]


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _keyword(line: str) -> tuple[str, dict[str, str]] | None:
    match = _KEYWORD.match(line)
    if not match:
        return None
    options = {key.casefold(): value for key, value in _OPTION.findall(match.group(2))}
    return match.group(1).casefold(), options


def _numbers(line: str) -> list[str]:
    return [item.strip() for item in line.split(",") if item.strip()]


def parse_t3_mesh(path: str | Path) -> ParsedMesh:
    """Parse explicit 2D nodes and T3 parent elements from an INP copy."""
    path = Path(path).resolve()
    lines = tuple(path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True))
    nodes: dict[int, Node] = {}
    elements: dict[int, Element] = {}
    all_element_labels: set[int] = set()
    active: tuple[str, dict[str, str]] | None = None
    element_block_end = -1
    for index, line in enumerate(lines):
        if line.lstrip().startswith("**") or not line.strip():
            continue
        card = _keyword(line)
        if card:
            active = card
            if card[0] == "element":
                element_block_end = index + 1
            continue
        if active is None:
            continue
        values = _numbers(line)
        if active[0] == "node":
            if len(values) < 3:
                raise ValueError(f"Invalid *NODE row at line {index + 1}")
            label = int(values[0])
            if label in nodes:
                raise ValueError(f"Duplicate node label {label}")
            nodes[label] = Node(label, (float(values[1]), float(values[2])))
        elif active[0] == "element":
            if values:
                all_element_labels.add(int(values[0]))
            element_type = active[1].get("type", "").upper()
            if element_type in _ALLOWED_PARENT_TYPES:
                if len(values) != 4:
                    raise ValueError(f"T3 parent element {values[:1]} must have three nodes")
                label = int(values[0])
                if label in elements:
                    raise ValueError(f"Duplicate element label {label}")
                elements[label] = Element(label, element_type, tuple(map(int, values[1:])))
            element_block_end = index + 1
    if not nodes or not elements:
        raise ValueError("The INP must contain explicit *NODE and T3/CPE3/CPS3 parent blocks")
    if element_block_end < 0:
        raise ValueError("No explicit parent *ELEMENT block was found")
    missing = sorted({node for element in elements.values() for node in element.connectivity if node not in nodes})
    if missing:
        raise ValueError(f"Parent elements reference missing nodes: {missing}")
    return ParsedMesh(lines, nodes, elements, element_block_end, tuple(sorted(all_element_labels)))


def parse_h8_mesh(path: str | Path) -> tuple[tuple[str, ...], dict[int, Node], dict[int, H8Element], int, tuple[int, ...]]:
    """Parse explicit 3D nodes and H8/C3D8 parent elements from an INP copy."""
    path = Path(path).resolve()
    lines = tuple(path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True))
    nodes: dict[int, Node] = {}
    elements: dict[int, H8Element] = {}
    all_element_labels: set[int] = set()
    active: tuple[str, dict[str, str]] | None = None
    element_block_end = -1
    for index, line in enumerate(lines):
        if line.lstrip().startswith("**") or not line.strip():
            continue
        card = _keyword(line)
        if card:
            active = card
            if card[0] == "element":
                element_block_end = index + 1
            continue
        if active is None:
            continue
        values = _numbers(line)
        if active[0] == "node":
            if len(values) < 4:
                raise ValueError(f"Invalid 3D *NODE row at line {index + 1}")
            label = int(values[0])
            if label in nodes:
                raise ValueError(f"Duplicate node label {label}")
            nodes[label] = Node(label, (float(values[1]), float(values[2]), float(values[3])))
        elif active[0] == "element":
            if values:
                all_element_labels.add(int(values[0]))
            element_type = active[1].get("type", "").upper()
            if element_type in _ALLOWED_H8_TYPES:
                if len(values) != 9:
                    raise ValueError(f"H8 parent element {values[:1]} must have eight nodes")
                label = int(values[0])
                if label in elements:
                    raise ValueError(f"Duplicate element label {label}")
                elements[label] = H8Element(label, element_type, tuple(map(int, values[1:])))
            element_block_end = index + 1
    if not nodes or not elements:
        raise ValueError("The INP must contain explicit *NODE and H8/C3D8 parent blocks")
    missing = sorted({node for element in elements.values() for node in element.connectivity if node not in nodes})
    if missing:
        raise ValueError(f"H8 parent elements reference missing nodes: {missing}")
    return lines, nodes, elements, element_block_end, tuple(sorted(all_element_labels))


def _oriented_edge(element: Element, edge: set[int]) -> tuple[int, int]:
    for first, second in zip(element.connectivity, element.connectivity[1:] + element.connectivity[:1]):
        if {first, second} == edge:
            return first, second
    raise ValueError(f"Element {element.label} does not contain the selected edge")


def _signed_area(element: Element, nodes: dict[int, Node]) -> float:
    points = [nodes[label].coordinates for label in element.connectivity]
    return 0.5 * sum(points[i][0] * points[(i + 1) % 3][1] - points[(i + 1) % 3][0] * points[i][1] for i in range(3))


def _edge_normal(edge: tuple[int, int], nodes: dict[int, Node]) -> tuple[float, float]:
    x1, y1 = nodes[edge[0]].coordinates
    x2, y2 = nodes[edge[1]].coordinates
    dx, dy = x2 - x1, y2 - y1
    length = (dx * dx + dy * dy) ** 0.5
    if length <= 0:
        raise ValueError("The selected reaction edge has zero length")
    return (-dy / length, dx / length)


def _face_area(face: tuple[int, int, int, int], nodes: dict[int, Node]) -> float:
    points = [nodes[label].coordinates for label in face]
    vectors = []
    for index in range(4):
        a = points[index]
        b = points[(index + 1) % 4]
        vectors.append((b[0] - a[0], b[1] - a[1], b[2] - a[2]))
    cross_a = (vectors[0][1] * vectors[1][2] - vectors[0][2] * vectors[1][1],
               vectors[0][2] * vectors[1][0] - vectors[0][0] * vectors[1][2],
               vectors[0][0] * vectors[1][1] - vectors[0][1] * vectors[1][0])
    cross_b = (vectors[2][1] * vectors[3][2] - vectors[2][2] * vectors[3][1],
               vectors[2][2] * vectors[3][0] - vectors[2][0] * vectors[3][2],
               vectors[2][0] * vectors[3][1] - vectors[2][1] * vectors[3][0])
    return 0.5 * ((sum(value * value for value in cross_a) ** 0.5) + (sum(value * value for value in cross_b) ** 0.5))


def _face_normal(face: tuple[int, int, int, int], nodes: dict[int, Node]) -> tuple[float, float, float]:
    a, b, c = (nodes[label].coordinates for label in face[:3])
    ab = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
    ac = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
    normal = (ab[1] * ac[2] - ab[2] * ac[1], ab[2] * ac[0] - ab[0] * ac[2], ab[0] * ac[1] - ab[1] * ac[0])
    length = sum(value * value for value in normal) ** 0.5
    if length <= 0:
        raise ValueError("The selected reaction face is degenerate")
    return tuple(value / length for value in normal)


def _oriented_face(element: H8Element, shared: set[int]) -> tuple[int, int, int, int]:
    for local_face in _H8_FACES:
        candidate = tuple(element.connectivity[index] for index in local_face)
        if set(candidate) == shared:
            return candidate
    raise ValueError(f"Element {element.label} does not contain the selected face")


def generate_h8_reaction_face(
    inp_path: str | Path,
    output_dir: str | Path,
    left_element: int,
    right_element: int,
    *,
    interface_type: str,
    elset: str = "PRIME_REACTION_FACE",
    node_policy: str = "face_plus_left_parent",
) -> dict[str, Any]:
    """Generate a new INP containing one eight-node H8 reaction-face element."""
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", interface_type):
        raise ValueError("interface_type must be a valid Abaqus element type token")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", elset):
        raise ValueError("elset must be a valid Abaqus set name")
    if node_policy != "face_plus_left_parent":
        raise ValueError("The first public H8 generator supports face_plus_left_parent only")
    source = Path(inp_path).resolve()
    source_hash = _sha(source)
    lines, nodes, elements, element_block_end, all_element_labels = parse_h8_mesh(source)
    try:
        left, right = elements[int(left_element)], elements[int(right_element)]
    except KeyError as error:
        raise ValueError(f"Unknown H8 parent element label: {error.args[0]}") from error
    shared = set(left.connectivity) & set(right.connectivity)
    if len(shared) != 4:
        raise ValueError("The selected H8 parent elements must share exactly one face")
    face = _oriented_face(left, shared)
    nonface_left = tuple(node for node in left.connectivity if node not in shared)
    if len(nonface_left) != 4:
        raise ValueError("The left H8 parent must have four non-face nodes")
    area = _face_area(face, nodes)
    if area <= 0:
        raise ValueError("The selected reaction face has zero area")
    normal = _face_normal(face, nodes)
    element_label = max(all_element_labels) + 1
    connectivity = list(face + nonface_left)
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"Output directory already exists: {output}")
    output.mkdir(parents=True)
    block = [f"*ELEMENT, TYPE={interface_type}, ELSET={elset}\n",
             f"{element_label}, {', '.join(map(str, connectivity))}\n"]
    generated_lines = list(lines)
    generated_lines[element_block_end:element_block_end] = block
    generated_inp = output / source.name
    generated_inp.write_text("".join(generated_lines), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "status": "GENERATED_TOPOLOGY_ONLY",
        "interface_kind": "3D_H8_REACTION_FACE",
        "source_inp": source.name,
        "source_inp_sha256": source_hash,
        "generated_inp": generated_inp.name,
        "generated_inp_sha256": _sha(generated_inp),
        "parent_elements": [{"label": left.label, "type": left.element_type, "connectivity": list(left.connectivity)},
                             {"label": right.label, "type": right.element_type, "connectivity": list(right.connectivity)}],
        "shared_face": list(face),
        "interface_element": {"label": element_label, "type": interface_type, "elset": elset,
                               "connectivity": connectivity},
        "nonface_parent_nodes": list(nonface_left),
        "node_policy": node_policy,
        "normal_from_left_face": list(normal),
        "reference_face_area": area,
        "property_status": "PRIVATE_ADAPTER_REQUIRED",
        "checks": {"shared_face_has_four_nodes": True, "parent_node_coverage": True,
                    "positive_face_area": area > 0,
                    "source_unchanged": _sha(source) == source_hash},
        "next_step": "Attach the authorized PRIME UEL property contract and run Abaqus datacheck on this new copy.",
    }
    (output / "interface_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def generate_t3_reaction_edge(
    inp_path: str | Path,
    output_dir: str | Path,
    left_element: int,
    right_element: int,
    *,
    interface_type: str,
    elset: str = "PRIME_REACTION_EDGE",
    node_policy: str = "shared",
) -> dict[str, Any]:
    """Generate a new INP containing one four-node T3 reaction-edge element."""
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", interface_type):
        raise ValueError("interface_type must be a valid Abaqus element type token")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", elset):
        raise ValueError("elset must be a valid Abaqus set name")
    if node_policy != "shared":
        raise ValueError("The first public generator supports shared interface nodes only")
    source = Path(inp_path).resolve()
    source_hash = _sha(source)
    mesh = parse_t3_mesh(source)
    try:
        left, right = mesh.elements[int(left_element)], mesh.elements[int(right_element)]
    except KeyError as error:
        raise ValueError(f"Unknown parent element label: {error.args[0]}") from error
    shared = set(left.connectivity) & set(right.connectivity)
    if len(shared) != 2:
        raise ValueError("The selected parent elements must share exactly one edge")
    edge = _oriented_edge(left, shared)
    opposite_left = next(node for node in left.connectivity if node not in shared)
    opposite_right = next(node for node in right.connectivity if node not in shared)
    area_left = _signed_area(left, mesh.nodes)
    area_right = _signed_area(right, mesh.nodes)
    if area_left == 0 or area_right == 0 or area_left * area_right <= 0:
        raise ValueError("Parent T3 elements must be non-degenerate and have the same orientation")
    normal = _edge_normal(edge, mesh.nodes)
    element_label = max(mesh.all_element_labels) + 1
    connectivity = [edge[0], edge[1], opposite_left, opposite_right]
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"Output directory already exists: {output}")
    output.mkdir(parents=True)
    insertion = mesh.element_block_end
    block = [f"*ELEMENT, TYPE={interface_type}, ELSET={elset}\n",
             f"{element_label}, {', '.join(map(str, connectivity))}\n"]
    generated_lines = list(mesh.lines)
    generated_lines[insertion:insertion] = block
    generated_inp = output / source.name
    generated_inp.write_text("".join(generated_lines), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "status": "GENERATED_TOPOLOGY_ONLY",
        "interface_kind": "2D_T3_REACTION_EDGE",
        "source_inp": source.name,
        "source_inp_sha256": source_hash,
        "generated_inp": generated_inp.name,
        "generated_inp_sha256": _sha(generated_inp),
        "parent_elements": [{"label": left.label, "type": left.element_type, "connectivity": list(left.connectivity)},
                             {"label": right.label, "type": right.element_type, "connectivity": list(right.connectivity)}],
        "shared_edge": list(edge),
        "interface_element": {"label": element_label, "type": interface_type, "elset": elset,
                               "connectivity": connectivity},
        "opposite_nodes": {"left": opposite_left, "right": opposite_right},
        "node_policy": node_policy,
        "normal_from_left_edge": list(normal),
        "property_status": "PRIVATE_ADAPTER_REQUIRED",
        "checks": {"shared_edge_has_two_nodes": True, "parent_node_coverage": True,
                    "positive_parent_orientation": area_left > 0 and area_right > 0,
                    "source_unchanged": _sha(source) == source_hash},
        "next_step": "Attach the authorized PRIME UEL property contract and run Abaqus datacheck on this new copy.",
    }
    (output / "interface_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def generate_t3_reaction_edges(
    inp_path: str | Path,
    output_dir: str | Path,
    parent_elements: Sequence[int],
    *,
    interface_type: str,
    elset: str = "PRIME_REACTION_EDGE",
    node_policy: str = "shared",
    opposite_elements: Sequence[int] | None = None,
) -> dict[str, Any]:
    """Generate reaction edges within one set or across two phase selections."""
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", interface_type):
        raise ValueError("interface_type must be a valid Abaqus element type token")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", elset):
        raise ValueError("elset must be a valid Abaqus set name")
    if node_policy != "shared":
        raise ValueError("The public batch generator supports shared interface nodes only")
    labels = tuple(dict.fromkeys(int(label) for label in parent_elements))
    if len(labels) < (2 if opposite_elements is None else 1):
        raise ValueError("Select at least two T3 parent elements for batch generation")
    opposite_labels = tuple(dict.fromkeys(int(label) for label in opposite_elements or ()))
    if opposite_elements is not None and not opposite_labels:
        raise ValueError("Select at least one element in the second phase")
    source = Path(inp_path).resolve()
    source_hash = _sha(source)
    mesh = parse_t3_mesh(source)
    missing = sorted((set(labels) | set(opposite_labels)) - set(mesh.elements))
    if missing:
        raise ValueError(f"Unknown T3 parent element labels: {missing}")
    interfaces: list[dict[str, Any]] = []
    for index, left_label in enumerate(labels):
        left = mesh.elements[left_label]
        candidates = labels[index + 1:] if opposite_elements is None else opposite_labels
        for right_label in candidates:
            right = mesh.elements[right_label]
            shared = set(left.connectivity) & set(right.connectivity)
            if len(shared) != 2:
                continue
            area_left = _signed_area(left, mesh.nodes)
            area_right = _signed_area(right, mesh.nodes)
            if area_left == 0 or area_right == 0 or area_left * area_right <= 0:
                raise ValueError(f"Parent T3 elements {left_label} and {right_label} have incompatible orientation")
            edge = _oriented_edge(left, shared)
            opposite_left = next(node for node in left.connectivity if node not in shared)
            opposite_right = next(node for node in right.connectivity if node not in shared)
            interfaces.append({
                "parent_elements": [left.label, right.label],
                "shared_edge": list(edge),
                "connectivity": [edge[0], edge[1], opposite_left, opposite_right],
                "opposite_nodes": {"left": opposite_left, "right": opposite_right},
                "normal": list(_edge_normal(edge, mesh.nodes)),
            })
    if not interfaces:
        raise ValueError("The selected T3 elements contain no adjacent pair sharing exactly one edge")
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"Output directory already exists: {output}")
    output.mkdir(parents=True)
    next_label = max(mesh.all_element_labels) + 1
    for item in interfaces:
        item["label"] = next_label
        next_label += 1
    block = [f"*ELEMENT, TYPE={interface_type}, ELSET={elset}\n"]
    block.extend(f"{item['label']}, {', '.join(map(str, item['connectivity']))}\n" for item in interfaces)
    generated_lines = list(mesh.lines)
    generated_lines[mesh.element_block_end:mesh.element_block_end] = block
    generated_inp = output / source.name
    generated_inp.write_text("".join(generated_lines), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "status": "GENERATED_TOPOLOGY_ONLY",
        "interface_kind": "2D_T3_REACTION_EDGE_BATCH",
        "source_inp": source.name,
        "source_inp_sha256": source_hash,
        "generated_inp": generated_inp.name,
        "generated_inp_sha256": _sha(generated_inp),
        "selected_parent_elements": list(labels) + list(opposite_labels),
        "phase_selection": {"solid": list(labels), "liquid": list(opposite_labels)} if opposite_elements is not None else None,
        "interface_count": len(interfaces),
        "interface_elements": [{"label": item["label"], "type": interface_type,
                                "elset": elset, "connectivity": item["connectivity"],
                                "parent_elements": item["parent_elements"],
                                "shared_edge": item["shared_edge"],
                                "opposite_nodes": item["opposite_nodes"],
                                "normal_from_left_edge": item["normal"]} for item in interfaces],
        "node_policy": node_policy,
        "property_status": "PRIVATE_ADAPTER_REQUIRED",
        "checks": {"selected_parent_count": len(labels) + len(opposite_labels), "adjacent_pair_count": len(interfaces),
                    "unique_interface_labels": len({item["label"] for item in interfaces}) == len(interfaces),
                    "source_unchanged": _sha(source) == source_hash},
        "next_step": "Attach the authorized PRIME UEL property contract and run Abaqus datacheck on this new copy.",
    }
    (output / "interface_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def generate_h8_reaction_faces(
    inp_path: str | Path,
    output_dir: str | Path,
    parent_elements: Sequence[int],
    *,
    interface_type: str,
    elset: str = "PRIME_REACTION_FACE",
    node_policy: str = "face_plus_left_parent",
    opposite_elements: Sequence[int] | None = None,
) -> dict[str, Any]:
    """Generate reaction faces within one set or across two phase selections."""
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,31}", interface_type):
        raise ValueError("interface_type must be a valid Abaqus element type token")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", elset):
        raise ValueError("elset must be a valid Abaqus set name")
    if node_policy != "face_plus_left_parent":
        raise ValueError("The public batch generator supports face_plus_left_parent only")
    labels = tuple(dict.fromkeys(int(label) for label in parent_elements))
    if len(labels) < (2 if opposite_elements is None else 1):
        raise ValueError("Select at least two H8 parent elements for batch generation")
    opposite_labels = tuple(dict.fromkeys(int(label) for label in opposite_elements or ()))
    if opposite_elements is not None and not opposite_labels:
        raise ValueError("Select at least one element in the second phase")
    source = Path(inp_path).resolve()
    source_hash = _sha(source)
    lines, nodes, elements, element_block_end, all_element_labels = parse_h8_mesh(source)
    missing = sorted((set(labels) | set(opposite_labels)) - set(elements))
    if missing:
        raise ValueError(f"Unknown H8 parent element labels: {missing}")
    interfaces: list[dict[str, Any]] = []
    for index, left_label in enumerate(labels):
        left = elements[left_label]
        candidates = labels[index + 1:] if opposite_elements is None else opposite_labels
        for right_label in candidates:
            right = elements[right_label]
            shared = set(left.connectivity) & set(right.connectivity)
            if len(shared) != 4:
                continue
            face = _oriented_face(left, shared)
            nonface_left = tuple(node for node in left.connectivity if node not in shared)
            if len(nonface_left) != 4:
                raise ValueError(f"H8 parent element {left_label} has invalid non-face node count")
            area = _face_area(face, nodes)
            if area <= 0:
                raise ValueError(f"The shared face of H8 elements {left_label} and {right_label} is degenerate")
            interfaces.append({
                "parent_elements": [left.label, right.label],
                "shared_face": list(face),
                "connectivity": list(face + nonface_left),
                "nonface_parent_nodes": list(nonface_left),
                "normal": list(_face_normal(face, nodes)),
                "area": area,
            })
    if not interfaces:
        raise ValueError("The selected H8 elements contain no adjacent pair sharing exactly one face")
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"Output directory already exists: {output}")
    output.mkdir(parents=True)
    next_label = max(all_element_labels) + 1
    for item in interfaces:
        item["label"] = next_label
        next_label += 1
    block = [f"*ELEMENT, TYPE={interface_type}, ELSET={elset}\n"]
    block.extend(f"{item['label']}, {', '.join(map(str, item['connectivity']))}\n" for item in interfaces)
    generated_lines = list(lines)
    generated_lines[element_block_end:element_block_end] = block
    generated_inp = output / source.name
    generated_inp.write_text("".join(generated_lines), encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "status": "GENERATED_TOPOLOGY_ONLY",
        "interface_kind": "3D_H8_REACTION_FACE_BATCH",
        "source_inp": source.name,
        "source_inp_sha256": source_hash,
        "generated_inp": generated_inp.name,
        "generated_inp_sha256": _sha(generated_inp),
        "selected_parent_elements": list(labels) + list(opposite_labels),
        "phase_selection": {"solid": list(labels), "liquid": list(opposite_labels)} if opposite_elements is not None else None,
        "interface_count": len(interfaces),
        "interface_elements": [{"label": item["label"], "type": interface_type,
                                "elset": elset, "connectivity": item["connectivity"],
                                "parent_elements": item["parent_elements"],
                                "shared_face": item["shared_face"],
                                "nonface_parent_nodes": item["nonface_parent_nodes"],
                                "normal_from_left_face": item["normal"],
                                "reference_face_area": item["area"]} for item in interfaces],
        "node_policy": node_policy,
        "property_status": "PRIVATE_ADAPTER_REQUIRED",
        "checks": {"selected_parent_count": len(labels) + len(opposite_labels), "adjacent_pair_count": len(interfaces),
                    "unique_interface_labels": len({item["label"] for item in interfaces}) == len(interfaces),
                    "source_unchanged": _sha(source) == source_hash},
        "next_step": "Attach the authorized PRIME UEL property contract and run Abaqus datacheck on this new copy.",
    }
    (output / "interface_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest
