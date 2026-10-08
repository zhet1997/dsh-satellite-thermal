import csv
import json
from pathlib import Path
import struct
import tempfile
import unittest

from thermal_executor.journals.mesh_export import write_temperature_glb,export_mesh

class HexMeshGeometry(unittest.TestCase):
    def test_two_adjacent_cubes_have_correct_volume_and_no_internal_face(self):
        points={1:(0,0,0),2:(1,0,0),3:(1,1,0),4:(0,1,0),5:(0,0,1),6:(1,0,1),7:(1,1,1),8:(0,1,1),9:(2,0,0),10:(2,1,0),11:(2,0,1),12:(2,1,1)}
        cells={100:[1,2,3,4,5,6,7,8],101:[2,9,10,3,6,11,12,7]}
        class FakeSf:
            def LocateElement(self,tag):return (2,[100,101])
            def AskElement(self,tag):return (tag,)
            def LocateNodesOnElement(self,tag):return (8,cells[tag])
            def AskNode(self,tag):return (tag,points[tag])
        class FakeUf:Sf=FakeSf()
        with tempfile.TemporaryDirectory() as folder:
            snapshot={'components':[{'id':'two-cubes','volumeMm3':2.,'faces':[]}]}
            mesh=export_mesh(FakeUf(),[{'componentId':'two-cubes','kind':'solid','meshTag':1}],folder,folder,snapshot)
            self.assertEqual(mesh['volumeMm3']['two-cubes'],2.)
            self.assertEqual((mesh['nodeCount'],mesh['elementCount']),(12,2))
            raw=(Path(folder)/'mesh.glb').read_bytes();length,_=struct.unpack_from('<II',raw,12)
            document=json.loads(raw[20:20+length])
            positions=document['meshes'][0]['primitives'][0]['attributes']['POSITION']
            self.assertEqual(document['accessors'][positions]['count'],60)

class TemperatureGeometry(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.snapshot={"components":[{"id":"real-part","faces":[]}]}
        with (self.root/"nodes.csv").open("w",newline="") as stream:
            writer=csv.writer(stream)
            writer.writerow(["node_id","x_mm","y_mm","z_mm","temperature_K","business_id"])
            writer.writerows([[1,0,0,0,300,"real-part"],[2,1,0,0,310,"real-part"],[3,0,1,0,320,"real-part"],[4,0,0,1,330,"real-part"]])
        with (self.root/"elements.csv").open("w",newline="") as stream:
            writer=csv.writer(stream)
            writer.writerow(["element_id","element_type","n1","n2","n3","n4","business_id"])
            writer.writerow([1,"TET4",1,2,3,4,"real-part"])

    def tearDown(self):
        self.temp.cleanup()

    def test_glb_colors_and_ids_come_from_temperature_nodes(self):
        target=self.root/"temperature.glb"
        self.assertEqual(write_temperature_glb(str(self.root),self.snapshot,str(target)),[300,330])
        raw=target.read_bytes()
        magic,version,total=struct.unpack_from("<III",raw)
        self.assertEqual((magic,version,total),(0x46546C67,2,len(raw)))
        json_length,kind=struct.unpack_from("<II",raw,12)
        document=json.loads(raw[20:20+json_length])
        primitive=document["meshes"][0]["primitives"][0]
        self.assertIn("COLOR_0",primitive["attributes"])
        self.assertEqual(document["nodes"][0]["extras"]["componentId"],"real-part")
        self.assertEqual(document["nodes"][0]["extras"]["source"],"nx-nodal-temperature")
        color_accessor=document["accessors"][primitive["attributes"]["COLOR_0"]]
        self.assertEqual(color_accessor["count"],12)
        view=document["bufferViews"][color_accessor["bufferView"]]
        offset=20+json_length+8+view["byteOffset"]
        first=struct.unpack_from("<fff",raw,offset)
        self.assertAlmostEqual(first[0],.05,places=6)
        self.assertAlmostEqual(first[1],.2,places=6)
        self.assertAlmostEqual(first[2],1,places=6)

    def test_missing_result_node_refuses_to_draw(self):
        text=(self.root/"nodes.csv").read_text()
        (self.root/"nodes.csv").write_text("\n".join(text.splitlines()[:-1])+"\n")
        with self.assertRaisesRegex(RuntimeError,"missing a mesh node"):
            write_temperature_glb(str(self.root),self.snapshot,str(self.root/"bad.glb"))
        self.assertFalse((self.root/"bad.glb").exists())

if __name__=="__main__":
    unittest.main()
