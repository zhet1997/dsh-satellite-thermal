import types
import unittest

from thermal_executor.journals.target_readback import read_native_targets
from thermal_executor.journals.element_face_mapping import member_fingerprint


class Group:
    def __init__(self, name, entities):
        self.name, self.entities = name, entities

    def GetName(self):
        return self.name

    def GetEntities(self):
        return self.entities


class NativeBoundary:
    Name = "actual-native-condition"

    def __init__(self, groups):
        self.TargetSetManager = types.SimpleNamespace(TargetSetCount=len(groups), GetTargetSetGroup=lambda i: groups[i])


class Targets(unittest.TestCase):
    def setUp(self):
        self.snapshot = {"components": [{"id": "a", "faces": [{"id": "a:front"}, {"id": "a:back"}]}]}

    def test_wrong_native_face_cannot_pass_even_with_correct_group_name(self):
        face = types.SimpleNamespace(GetStringAttribute=lambda name: "a:back")
        native = NativeBoundary([Group("INTENDED_FACE", [face])])
        expected = [{"group": "INTENDED_FACE", "mode": "faces", "entityCount": 1, "targets": [{"componentId": "a", "faceId": "a:front"}]}]
        with self.assertRaisesRegex(RuntimeError, "members differ"):
            read_native_targets(native, self.snapshot, expected)

    def test_reopened_element_members_are_checked_beyond_counts_and_mesh_names(self):
        element = types.SimpleNamespace(Mesh=types.SimpleNamespace(Name="real-mesh"), Label=11)
        native = NativeBoundary([Group("INTENDED_ELEMENTS", [element])])
        expected = [{"group": "INTENDED_ELEMENTS", "mode": "elements", "entityCount": 1, "targets": [{"componentId": "a"}], "componentMeshNames": {"a": "real-mesh"}, "componentElementCounts": {"a": 1}}]
        actual = read_native_targets(native, self.snapshot, expected)
        self.assertEqual(actual[0]["targets"], [{"componentId": "a"}])
        expected[0]["memberIdentitySha256"] = actual[0]["memberIdentitySha256"]
        element.Label = 12
        with self.assertRaisesRegex(RuntimeError, "identities changed"):
            read_native_targets(native, self.snapshot, expected)

    def test_swapping_contact_sides_is_rejected(self):
        native = NativeBoundary([Group("SECONDARY", []), Group("PRIMARY", [])])
        expected = [{"group": "PRIMARY"}, {"group": "SECONDARY"}]
        with self.assertRaisesRegex(RuntimeError, "set 0"):
            read_native_targets(native, self.snapshot, expected)

    def test_element_face_local_side_is_verified(self):
        element=types.SimpleNamespace(Mesh=types.SimpleNamespace(Name='real-mesh'),Label=11)
        indices=[2]
        face=types.SimpleNamespace(GetElementsAndFaceIds=lambda:([element],indices))
        native=NativeBoundary([Group('ELEMENT_FACE',[face])])
        expected=[{'group':'ELEMENT_FACE','mode':'element-faces','entityCount':1,'memberCount':1,'targets':[{'componentId':'a','faceId':'a:front'}],'componentMeshNames':{'a':'real-mesh'},'memberIdentitySha256':member_fingerprint('real-mesh',[(11,2)])}]
        self.assertEqual(read_native_targets(native,self.snapshot,expected)[0]['memberCount'],1)
        indices[0]=3
        with self.assertRaisesRegex(RuntimeError,'identities changed'):
            read_native_targets(native,self.snapshot,expected)

    def test_duplicate_element_faces_are_rejected(self):
        element=types.SimpleNamespace(Mesh=types.SimpleNamespace(Name='real-mesh'),Label=11)
        face=types.SimpleNamespace(GetElementsAndFaceIds=lambda:([element,element],[2,2]))
        native=NativeBoundary([Group('ELEMENT_FACE',[face])])
        expected=[{'group':'ELEMENT_FACE','mode':'element-faces','entityCount':1,'memberCount':2,'targets':[{'componentId':'a','faceId':'a:front'}],'componentMeshNames':{'a':'real-mesh'}}]
        with self.assertRaisesRegex(RuntimeError,'duplicate'):
            read_native_targets(native,self.snapshot,expected)


if __name__ == "__main__":
    unittest.main()
