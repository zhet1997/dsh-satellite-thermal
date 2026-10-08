import copy

from fastapi.testclient import TestClient

from thermal_service.app import create_app
from thermal_service.nx_adapter import NXAdapter
from thermal_service.schema import new_project, normalize


def test_table_echoed_evidence_does_not_override_edit_but_new_source_is_preserved(tmp_path):
    app = create_app(tmp_path, NXAdapter(url=""), start_scheduler=False)
    p = new_project("edit evidence")
    p["components"] = [{"id": "c1", "name": "equipment", "kind": "solid", "faces": []}]
    p = normalize(p)
    app.state.store.save(p)
    with TestClient(app) as client:
        component = copy.deepcopy(p["components"][0])
        old_origin = copy.deepcopy(component["provenance"]["specificHeat"])
        assert old_origin["status"] == "estimated"
        component["specificHeat"] = 400
        response = client.patch(f"/api/projects/{p['id']}",
            json={"revision": p["revision"], "components": [component]})
        assert response.status_code == 200, response.text
        p = response.json()
        assert p["components"][0]["provenance"]["specificHeat"]["status"] == "manual"

        component = copy.deepcopy(p["components"][0])
        component["specificHeat"] = 450
        new_origin = {"status": "provided", "source": "material certificate", "reason": "batch measurement"}
        component["provenance"]["specificHeat"] = new_origin
        response = client.patch(f"/api/projects/{p['id']}",
            json={"revision": p["revision"], "components": [component]})
        assert response.status_code == 200, response.text
        assert response.json()["components"][0]["provenance"]["specificHeat"] == new_origin
