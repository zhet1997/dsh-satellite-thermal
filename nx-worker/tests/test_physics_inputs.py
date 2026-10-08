import unittest

from thermal_executor.journals.physics_inputs import absorbed_case_flux, absorbed_surface_flux, face_optics, radiation_enclosures


class SurfaceOptics(unittest.TestCase):
    def setUp(self):
        self.component = {"id": "panel", "absorptivity": .3, "emissivity": .8}
        self.face = {"id": "panel:outside", "absorptivity": .6, "emissivity": .2}

    def test_incident_spectrum_uses_face_solar_and_ir_coefficients(self):
        case = {"flux": {"+X": 1150}, "fluxComponents": {"+X": {"direct": 900, "albedo": 150, "ir": 100}}}
        result = absorbed_case_flux(case, "+X", self.component, self.face)
        self.assertAlmostEqual(result["absorbedFluxWm2"], 650)
        self.assertAlmostEqual(absorbed_case_flux({"flux": {"+X": 1150}}, "+X", self.component, self.face)["absorbedFluxWm2"], 690)

    def test_already_absorbed_value_is_not_multiplied_twice(self):
        for marker in ({"valueKind": "absorbed"}, {"opticalCoefficientsApplied": True}):
            result = absorbed_case_flux(dict(marker, flux={"+X": 1150}), "+X", self.component, self.face)
            self.assertEqual(result["absorbedFluxWm2"], 1150)

    def test_null_inherits_and_zero_overrides(self):
        self.assertEqual(face_optics(self.component, {"id": "f", "absorptivity": 0, "emissivity": None}), {"absorptivity": 0, "emissivity": .8})

    def test_sheet_absorbs_both_physical_sides_into_one_mesh_surface(self):
        case = {"flux": {"+X": 1000, "-X": 300}}
        shell = dict(self.component, kind="shell")
        self.assertEqual(absorbed_surface_flux(case, "+X", shell, self.face)["absorbedFluxWm2"], 780)
        self.assertEqual(absorbed_surface_flux(case, "+X", self.component, self.face)["absorbedFluxWm2"], 600)


class RadiationTopology(unittest.TestCase):
    def setUp(self):
        self.snapshot = {"components": [{"id": cid, "faces": [{"id": cid+":front"}, {"id": cid+":back"}]} for cid in ("a", "b", "c")], "boundaries": [], "links": []}

    def link(self, a, b, af=None, bf=None):
        self.snapshot["links"].append({"id": a+":"+b, "kind": "radiation", "value": 1, "source": a, "target": b, "sourceFace": af, "targetFace": bf})

    def space(self, targets):
        self.snapshot["boundaries"].append({"id": "space", "kind": "space", "value": 3, "targets": targets})

    def test_space_without_mutual_links_preserves_three_separate_enclosures(self):
        self.space([{"componentId": cid} for cid in ("a", "b", "c")])
        groups = radiation_enclosures(self.snapshot)
        self.assertEqual(len(groups), 3)
        self.assertTrue(all(len(g["targets"]) == 1 and g["includeEnvironment"] for g in groups))

    def test_disconnected_complete_subgraphs_are_independent(self):
        self.link("a", "b")
        self.space([{"componentId": cid} for cid in ("a", "b", "c")])
        groups = radiation_enclosures(self.snapshot)
        self.assertEqual(sorted(len(g["targets"]) for g in groups), [1, 2])

    def test_missing_edge_is_rejected_instead_of_added(self):
        self.link("a", "b")
        self.link("b", "c")
        with self.assertRaisesRegex(RuntimeError, "missing explicit edge"):
            radiation_enclosures(self.snapshot)

    def test_face_space_subset_and_explicit_face_exchange(self):
        self.link("a", "b", "a:front", "b:front")
        self.space([{"componentId": "a", "faceId": "a:front"}, {"componentId": "b", "faceId": "b:front"}])
        groups = radiation_enclosures(self.snapshot)
        self.assertEqual(len(groups), 1)
        self.assertEqual([t["faceId"] for t in groups[0]["targets"]], ["a:front", "b:front"])

    def test_partial_space_on_component_region_is_rejected(self):
        self.link("a", "b")
        self.space([{"componentId": "a", "faceId": "a:front"}])
        with self.assertRaisesRegex(RuntimeError, "only part"):
            radiation_enclosures(self.snapshot)

    def test_space_covering_only_one_region_in_pair_is_rejected(self):
        self.link("a", "b")
        self.space([{"componentId": "a"}])
        with self.assertRaisesRegex(RuntimeError, "every region"):
            radiation_enclosures(self.snapshot)


if __name__ == "__main__":
    unittest.main()
