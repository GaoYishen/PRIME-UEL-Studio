from __future__ import annotations

import json
from pathlib import Path

from prime_studio.interface_generator import (generate_h8_reaction_face, generate_h8_reaction_faces,
                                              generate_t3_reaction_edge, generate_t3_reaction_edges,
                                              parse_h8_mesh, parse_t3_mesh)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "examples/interface/t3_pair.inp"
H8_FIXTURE = ROOT / "examples/interface/h8_pair.inp"
MULTIPHASE_FIXTURE = ROOT / "examples/interface/multiphase_t3.inp"


def test_parse_t3_mesh_reads_explicit_nodes_and_parents():
    mesh = parse_t3_mesh(FIXTURE)
    assert set(mesh.nodes) == {1, 2, 3, 4}
    assert mesh.elements[10].connectivity == (1, 2, 3)
    assert mesh.elements[20].connectivity == (2, 4, 3)


def test_generate_shared_t3_reaction_edge_in_new_copy(tmp_path):
    manifest = generate_t3_reaction_edge(FIXTURE, tmp_path / "case", 10, 20, interface_type="U2")
    assert manifest["status"] == "GENERATED_TOPOLOGY_ONLY"
    assert manifest["shared_edge"] == [2, 3]
    assert manifest["interface_element"]["connectivity"] == [2, 3, 1, 4]
    assert manifest["checks"]["source_unchanged"] is True
    generated = tmp_path / "case" / "t3_pair.inp"
    assert generated.is_file()
    text = generated.read_text(encoding="utf-8")
    assert "*ELEMENT, TYPE=U2, ELSET=PRIME_REACTION_EDGE" in text
    assert "21, 2, 3, 1, 4" in text
    saved = json.loads((tmp_path / "case" / "interface_manifest.json").read_text(encoding="utf-8"))
    assert saved["generated_inp_sha256"] == manifest["generated_inp_sha256"]


def test_generator_rejects_non_adjacent_parent_elements(tmp_path):
    try:
        generate_t3_reaction_edge(FIXTURE, tmp_path / "case", 10, 10, interface_type="U2")
    except ValueError as error:
        assert "share exactly one edge" in str(error)
    else:
        raise AssertionError("non-adjacent parent selection should fail")


def test_batch_t3_generation_finds_all_adjacent_pairs(tmp_path):
    mesh = tmp_path / "chain.inp"
    mesh.write_text(
        "*NODE\n1, 0., 0.\n2, 1., 0.\n3, 0., 1.\n4, 2., 0.\n5, 2., 1.\n"
        "*ELEMENT, TYPE=T3\n10, 1, 2, 3\n20, 2, 4, 3\n30, 4, 5, 3\n",
        encoding="utf-8",
    )
    manifest = generate_t3_reaction_edges(mesh, tmp_path / "batch", [10, 20, 30], interface_type="U2")
    assert manifest["interface_count"] == 2
    assert [item["label"] for item in manifest["interface_elements"]] == [31, 32]
    assert manifest["checks"]["source_unchanged"] is True


def test_batch_t3_generation_only_connects_solid_to_liquid(tmp_path):
    manifest = generate_t3_reaction_edges(FIXTURE, tmp_path / "cross_phase", [10],
                                          interface_type="U2", opposite_elements=[20])
    assert manifest["interface_count"] == 1
    assert manifest["phase_selection"] == {"solid": [10], "liquid": [20]}
    assert manifest["checks"]["selected_parent_count"] == 2


def test_parse_h8_mesh_and_generate_reaction_face(tmp_path):
    _, nodes, elements, _, all_labels = parse_h8_mesh(H8_FIXTURE)
    assert len(nodes) == 12
    assert all_labels == (10, 20)
    assert elements[10].connectivity == (1, 2, 3, 4, 5, 6, 7, 8)
    manifest = generate_h8_reaction_face(H8_FIXTURE, tmp_path / "case_h8", 10, 20, interface_type="U303")
    assert manifest["shared_face"] == [2, 6, 7, 3]
    assert manifest["interface_element"]["connectivity"] == [2, 6, 7, 3, 1, 4, 5, 8]
    assert manifest["checks"]["source_unchanged"] is True
    text = (tmp_path / "case_h8" / "h8_pair.inp").read_text(encoding="utf-8")
    assert "*ELEMENT, TYPE=U303, ELSET=PRIME_REACTION_FACE" in text
    assert "21, 2, 6, 7, 3, 1, 4, 5, 8" in text


def test_batch_h8_generation_accepts_selected_parent_set(tmp_path):
    manifest = generate_h8_reaction_faces(H8_FIXTURE, tmp_path / "batch_h8", [10, 20], interface_type="U303")
    assert manifest["interface_count"] == 1
    assert manifest["interface_elements"][0]["label"] == 21
    assert manifest["checks"]["source_unchanged"] is True
    text = (tmp_path / "batch_h8" / "h8_pair.inp").read_text(encoding="utf-8")
    assert text.count("21, 2, 6, 7, 3, 1, 4, 5, 8") == 1


def test_batch_h8_generation_only_connects_solid_to_liquid(tmp_path):
    manifest = generate_h8_reaction_faces(H8_FIXTURE, tmp_path / "cross_phase_h8", [10],
                                          interface_type="U303", opposite_elements=[20])
    assert manifest["interface_count"] == 1
    assert manifest["phase_selection"] == {"solid": [10], "liquid": [20]}
    assert manifest["checks"]["selected_parent_count"] == 2


def test_public_multiphase_example_generates_two_cross_phase_interfaces(tmp_path):
    manifest = generate_t3_reaction_edges(MULTIPHASE_FIXTURE, tmp_path / "public_multiphase", [101, 102],
                                          interface_type="U2", opposite_elements=[201, 202])
    assert manifest["interface_count"] == 2
    assert [item["label"] for item in manifest["interface_elements"]] == [203, 204]
    assert manifest["phase_selection"] == {"solid": [101, 102], "liquid": [201, 202]}
