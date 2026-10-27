import math,unittest
from thermal_executor.journals.element_face_mapping import map_component_facets


class Mapping(unittest.TestCase):
    def cube(self):
        # A rotated box exercises arbitrary world normals, not six-axis labels.
        local=[(0,0,0),(1,0,0),(1,1,0),(0,1,0),(0,0,1),(1,0,1),(1,1,1),(0,1,1)]
        angle=.47;c=math.cos(angle);s=math.sin(angle)
        nodes={i+1:(c*x-s*y,s*x+c*y,z) for i,(x,y,z) in enumerate(local)}
        topology=[(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)]
        normals=[(0,0,-1),(0,0,1),(0,-1,0),(1,0,0),(0,1,0),(-1,0,0)]
        faces=[]
        for i,(facet,normal) in enumerate(zip(topology,normals)):
            points=[nodes[n+1] for n in facet];x,y,z=normal
            faces.append({'id':f'cube:f{i}','geometryType':'BSPLINE' if i<2 else 'PLANE','normal':[c*x-s*y,s*x+c*y,z],'center':[sum(p[k] for p in points)/4 for k in range(3)],'boundsMm':[min(p[k] for p in points) for k in range(3)]+[max(p[k] for p in points) for k in range(3)],'areaMm2':1})
        return {'id':'cube','faces':faces},nodes,[dict(element_id=11,element_type='HEX8',**{f'n{i}':i for i in range(1,9)})]

    def test_rotated_flat_bspline_caps_keep_native_local_sides_and_area(self):
        component,nodes,elements=self.cube();mapped=map_component_facets(component,nodes,elements)
        for i in range(6):
            self.assertEqual(mapped[f'cube:f{i}']['members'],[(11,i)])
            self.assertAlmostEqual(mapped[f'cube:f{i}']['areaMm2'],1)

    def test_wrong_region_normal_is_rejected(self):
        component,nodes,elements=self.cube();component['faces'][0]['normal']=[0,0,1]
        with self.assertRaisesRegex(RuntimeError,'maps to 0'):
            map_component_facets(component,nodes,elements)

    def test_ambiguous_coplanar_region_is_rejected(self):
        component,nodes,elements=self.cube();component['faces'].append(dict(component['faces'][0],id='duplicate-region'))
        with self.assertRaisesRegex(RuntimeError,'maps to 2'):
            map_component_facets(component,nodes,elements)

if __name__=='__main__':unittest.main()
