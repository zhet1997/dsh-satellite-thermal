"""Optional fixtures from the user's retained handoff; never bundled in release."""
import os
from pathlib import Path

import pytest

from thermal_service.geometry import available, import_geometry

FIXTURES = Path(os.environ.get("THERMAL_HANDOFF_CASES", "/mnt/d/Codes/dsh-satellite-geometry"))


@pytest.mark.skipif(not available() or not FIXTURES.exists(), reason="private handoff fixtures unavailable")
def test_real_valid_satellite_gearbox_retains_faces_and_names(tmp_path):
    path = FIXTURES / "cases/real-gearbox/original.step"
    result = import_geometry(path, tmp_path)
    assert len(result["components"]) == 1
    assert len(result["components"][0]["faces"]) == 133
    assert result["triangleCount"] > 100
    assert result["previewPath"].read_bytes()[:4] == b"glTF"
    assert result["sha256"] == "eabc4c4634b8bcdb27795900dae4c07394c891ba40a01352448945ae94b249d1"


@pytest.mark.skipif(not available() or not FIXTURES.exists(), reason="private handoff fixtures unavailable")
def test_official_geometry_package_names_match_by_measured_geometry(tmp_path):
    path = FIXTURES / "validation/model-test-data/projects/7a21b084584347cdabc8fe6df873c67f/artifacts/r2/satellite-package.zip"
    result = import_geometry(path, tmp_path)
    names = {c["name"] for c in result["components"]}
    assert "Mounting_plate" in names
    assert "Four_foot_device" in names
    assert all(c.get("sourceId") for c in result["components"])
    assert result["links"]


@pytest.mark.skipif(not available(), reason="CadQuery optional")
def test_real_mixed_solid_and_sheet_survive_import(tmp_path):
    import cadquery as cq
    solid = cq.Workplane("XY").box(10, 10, 10).val()
    wire = cq.Workplane("XY").rect(30, 30).val()
    sheet = cq.Face.makeFromWires(wire).translate((40, 0, 0))
    source = tmp_path / "mixed.step"
    cq.exporters.export(cq.Compound.makeCompound([solid, sheet]), str(source))
    result = import_geometry(source, tmp_path)
    assert sorted(c["kind"] for c in result["components"]) == ["shell", "solid"]
    assert len([f for c in result["components"] for f in c["faces"]]) == 7
