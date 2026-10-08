"""Exact contact evidence without whole-assembly minimum-distance searches."""
import json
import zipfile

import pytest

from thermal_service.geometry import available, import_geometry

pytestmark = pytest.mark.skipif(not available(), reason="CadQuery optional")


def import_parts(tmp_path, parts, metadata=None):
    import cadquery as cq
    source = tmp_path / "source.step"
    cq.exporters.export(cq.Compound.makeCompound(parts), str(source))
    if metadata is not None:
        archive = tmp_path / "geometry-package.zip"
        with zipfile.ZipFile(archive, "w") as package:
            package.write(source, "simplified.step")
            package.writestr("properties.json", json.dumps(metadata))
        upload = archive
    else:
        upload = source
    destination = tmp_path / "imported"
    destination.mkdir()
    result = import_geometry(upload, destination, example_names=["one", "two"])
    assert result["stepPath"].read_bytes() == source.read_bytes()
    assert len(result["components"]) == len(parts)
    return result


def test_touching_solids_retain_exact_face_contact_without_minimum_distance(tmp_path, monkeypatch):
    import cadquery as cq

    def forbidden_distance(*args, **kwargs):
        pytest.fail("Contact detection must not run the formerly blocking minimum-distance prepass")

    monkeypatch.setattr(cq.Shape, "distance", forbidden_distance)
    box = cq.Workplane("XY").box(10, 10, 10).val()
    result = import_parts(tmp_path, [box, box.translate((10, 0, 0))])
    assert len(result["links"]) == 1
    link = result["links"][0]
    assert link["contactAreaMm2"] == pytest.approx(100)
    assert link["kind"] == "contact"
    assert link["value"] == .001
    assert link["provenance"]["status"] == "estimated"
    for component_key, face_key in [("source", "sourceFace"), ("target", "targetFace")]:
        component = next(c for c in result["components"] if c["id"] == link[component_key])
        assert link[face_key] in [face["id"] for face in component["faces"]]
    assert sum(len(c["faces"]) for c in result["components"]) == 12


@pytest.mark.parametrize("offset", [(10.1, 0, 0), (10, 10, 0)])
def test_separated_or_edge_touching_solids_do_not_create_contact(tmp_path, offset):
    import cadquery as cq
    box = cq.Workplane("XY").box(10, 10, 10).val()
    result = import_parts(tmp_path, [box, box.translate(offset)])
    assert result["links"] == []


def test_overlapping_planar_bounds_inside_hole_are_not_contact_evidence(tmp_path):
    import cadquery as cq
    plate = cq.Workplane("XY").box(10, 10, 1).cut(cq.Workplane("XY").box(6, 6, 2)).val()
    over_hole = cq.Workplane("XY").box(2, 2, 1).translate((0, 0, 1)).val()
    result = import_parts(tmp_path, [plate, over_hole])
    assert result["links"] == []


@pytest.mark.parametrize("policy", [
    "source-evidence-required",
    "unverified-candidates-only; retained structure contacts require source evidence",
])
def test_equipment_envelope_contract_never_turns_envelope_contact_into_conductance(tmp_path, policy):
    import cadquery as cq
    box = cq.Workplane("XY").box(10, 10, 10).val()
    metadata = {"thermalGeometryPolicy": {"contactPolicy": policy}, "components": [
        {"stepIndex": 1, "name": "Envelope one", "id": "equipment-one"},
        {"stepIndex": 2, "name": "Envelope two", "id": "equipment-two"},
    ]}
    result = import_parts(tmp_path, [box, box.translate((10, 0, 0))], metadata)
    assert result["links"] == []
    assert {c["sourceId"] for c in result["components"]} == {"equipment-one", "equipment-two"}
    assert any(problem["field"] == "links" and "包络" in problem["message"] for problem in result["issues"])
    assert sum(len(c["faces"]) for c in result["components"]) == 12
