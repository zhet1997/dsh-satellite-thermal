"""Export actual UF finite-element nodes to CSV and a GLB boundary surface."""
import csv
import json
import math
import os
import struct

def cross(a, b):
    return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]

def sub(a,b):
    return [a[i]-b[i] for i in range(3)]

def area(a,b,c):
    v = cross(sub(b,a), sub(c,a))
    return math.sqrt(sum(x*x for x in v)) / 2

HEX_FACES=[(0,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)]

def triangulate(faces):
    return [triangle for face in faces for triangle in ([face] if len(face)==3 else [(face[0],face[1],face[2]),(face[0],face[2],face[3])])]

def hex_volume(points):
    tetra=[(0,1,2,6),(0,2,3,6),(0,3,7,6),(0,7,4,6),(0,4,5,6),(0,5,1,6)]
    return sum(abs(sum(x*y for x,y in zip(sub(points[b],points[a]),cross(sub(points[c],points[a]),sub(points[d],points[a])))))/6 for a,b,c,d in tetra)

def export_mesh(uf, mesh_rows, raw_dir, output_dir, snapshot):
    nodes, elements, surfaces, volumes = {}, [], {}, {}
    for row in mesh_rows:
        cid = row["componentId"]
        located = uf.Sf.LocateElement(row["meshTag"])
        tags = list(located[1]) if isinstance(located, tuple) else list(located)
        boundary = {}
        volume = 0.0
        for tag in tags:
            info = uf.Sf.AskElement(tag)
            eid = int(info[0])
            found = uf.Sf.LocateNodesOnElement(tag)
            ntags = list(found[1]) if isinstance(found, tuple) else list(found)
            labels, points = [], []
            for ntag in ntags:
                node = uf.Sf.AskNode(ntag)
                label, point = int(node[0]), [float(x) for x in node[-1]]
                labels.append(label)
                points.append(point)
                nodes[label] = {"node_id":label,"x_mm":point[0],"y_mm":point[1],"z_mm":point[2],"business_id":cid}
            count = len(labels)
            if row["kind"] == "solid" and count == 4:
                element_type = "TET4"
                faces = [(0,2,1),(0,1,3),(1,2,3),(2,0,3)]
                volume += abs(sum(x*y for x,y in zip(sub(points[1],points[0]),cross(sub(points[2],points[0]),sub(points[3],points[0])))))/6
            elif row['kind']=='solid' and count==8:
                element_type='HEX8';faces=HEX_FACES;volume+=hex_volume(points)
            elif row["kind"] == "shell" and count in (3,4):
                element_type = "TRI3" if count == 3 else "QUAD4"
                faces = [(0,1,2)] if count == 3 else [(0,1,2),(0,2,3)]
                volume += sum(area(points[a],points[b],points[c]) for a,b,c in faces) * row["thicknessMm"]
            else:
                raise RuntimeError("Unexpected actual element topology %s/%s" % (row["kind"],count))
            element = {"element_id":eid,"element_type":element_type,"business_id":cid}
            for index,label in enumerate(labels,1):
                element["n%d"%index] = label
            elements.append(element)
            for face in faces:
                ids = tuple(labels[i] for i in face)
                key = tuple(sorted(ids))
                if key in boundary:
                    del boundary[key]
                else:
                    boundary[key] = ids
        if not tags or volume <= 0:
            raise RuntimeError("Component has no positive actual meshed volume: " + cid)
        row["elementCount"] = len(tags)
        row["nodeCount"] = len([x for x in nodes.values() if x["business_id"] == cid])
        volumes[cid] = volume
        if row['kind']=='solid' and any(e['business_id']==cid and e['element_type']=='HEX8' for e in elements):
            source=next(c['volumeMm3'] for c in snapshot['components'] if c['id']==cid)
            if abs(volume/source-1)>.02:raise RuntimeError('Actual HEX8 meshed volume differs from STEP by more than 2 percent: '+cid)
        surfaces[cid] = triangulate(list(boundary.values()))
    os.makedirs(raw_dir, exist_ok=True)
    for filename, fields, rows in (("nodes_geometry.csv",["node_id","x_mm","y_mm","z_mm","business_id"],list(nodes.values())),("elements.csv",["element_id","element_type","n1","n2","n3","n4","n5","n6","n7","n8","business_id"],elements)):
        with open(os.path.join(raw_dir,filename),"w",newline="") as stream:
            writer = csv.DictWriter(stream,fieldnames=fields,extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    write_glb(nodes,surfaces,snapshot,os.path.join(output_dir,"mesh.glb"))
    return {"nodeCount":len(nodes),"elementCount":len(elements),"components":mesh_rows,"volumeMm3":volumes,"source":"NX UF finite-element mesh"}

def write_glb(nodes, surfaces, snapshot, path, temperatures=None):
    binary = bytearray()
    gltf = {"asset":{"version":"2.0","generator":"NX actual finite-element boundary"},"scene":0,"scenes":[{"nodes":[]}],"nodes":[],"meshes":[],"bufferViews":[],"accessors":[],"buffers":[],"materials":[{"pbrMetallicRoughness":{"baseColorFactor":[.24,.60,.83,1],"metallicFactor":0,"roughnessFactor":.8},"doubleSided":True}]}
    by_id = dict((x["id"],x) for x in snapshot["components"])
    if temperatures:
        gltf["materials"][0]["pbrMetallicRoughness"]["baseColorFactor"]=[1,1,1,1]
        low,high=min(temperatures.values()),max(temperatures.values())
    for cid, triangles in surfaces.items():
        groups,group_labels = {},{}
        for tri in triangles:
            points = [[nodes[n][a] for a in ("x_mm","y_mm","z_mm")] for n in tri]
            matches = []
            for face in by_id[cid].get("faces",[]):
                if str(face.get("geometryType", "PLANE")).upper() != "PLANE":
                    continue
                if all(abs(sum((p[i]-face["center"][i])*face["normal"][i] for i in range(3))) < .001 for p in points):
                    matches.append(face["id"])
            fid = matches[0] if len(matches) == 1 else None
            groups.setdefault(fid,[]).extend(points)
            group_labels.setdefault(fid,[]).extend(tri)
        for fid,points in groups.items():
            start = len(binary)
            flat = [v for point in points for v in point]
            binary.extend(struct.pack("<"+"f"*len(flat),*flat))
            view = len(gltf["bufferViews"])
            gltf["bufferViews"].append({"buffer":0,"byteOffset":start,"byteLength":len(binary)-start,"target":34962})
            access = len(gltf["accessors"])
            gltf["accessors"].append({"bufferView":view,"componentType":5126,"count":len(points),"type":"VEC3","min":[min(p[i] for p in points) for i in range(3)],"max":[max(p[i] for p in points) for i in range(3)]})
            extras = {"componentId":cid,"source":"nx-actual-mesh","units":"mm"}
            attributes={"POSITION":access}
            if temperatures:
                values=[temperatures[n] for n in group_labels[fid]]
                colors=[]
                for value in values:
                    x=(value-low)/max(high-low,1e-12)
                    stops=((0.,(.05,.2,1.)),(.33,(0.,.85,1.)),(.67,(1.,.95,0.)),(1.,(.9,.05,0.)))
                    for i in range(3):
                        a,ca=stops[i]
                        b,cb=stops[i+1]
                        if x<=b or i==2:
                            t=max(0,min(1,(x-a)/(b-a)))
                            colors.extend(ca[k]*(1-t)+cb[k]*t for k in range(3))
                            break
                color_start=len(binary)
                binary.extend(struct.pack("<"+"f"*len(colors),*colors))
                view=len(gltf["bufferViews"])
                gltf["bufferViews"].append({"buffer":0,"byteOffset":color_start,"byteLength":len(binary)-color_start,"target":34962})
                color_access=len(gltf["accessors"])
                gltf["accessors"].append({"bufferView":view,"componentType":5126,"count":len(values),"type":"VEC3"})
                attributes["COLOR_0"]=color_access
                extras.update(source="nx-nodal-temperature",temperatureUnit="K",minK=min(values),maxK=max(values),temperatureRangeK=[low,high])
            if fid:
                extras["faceId"] = fid
            mesh = len(gltf["meshes"])
            gltf["meshes"].append({"name":fid or cid,"primitives":[{"attributes":attributes,"mode":4,"material":0}],"extras":extras})
            gltf["scenes"][0]["nodes"].append(len(gltf["nodes"]))
            gltf["nodes"].append({"name":fid or cid,"mesh":mesh,"extras":extras})
    gltf["buffers"] = [{"byteLength":len(binary)}]
    document = json.dumps(gltf,separators=(",",":")).encode("utf-8")
    document += b" "*((-len(document))%4)
    binary.extend(b"\x00"*((-len(binary))%4))
    with open(path,"wb") as stream:
        stream.write(struct.pack("<III",0x46546C67,2,12+8+len(document)+8+len(binary)))
        stream.write(struct.pack("<II",len(document),0x4E4F534A))
        stream.write(document)
        stream.write(struct.pack("<II",len(binary),0x004E4942))
        stream.write(binary)

def write_temperature_glb(raw_dir,snapshot,output):
    """Read NX-exported nodes; never synthesize a temperature for missing nodes."""
    with open(os.path.join(raw_dir,"nodes.csv"),newline="") as stream:
        rows=list(csv.DictReader(stream))
    nodes,temperatures={},{}
    for row in rows:
        label=int(row["node_id"])
        nodes[label]=dict((k,float(row[k])) for k in ("x_mm","y_mm","z_mm"))
        value=float(row["temperature_K"])
        if not math.isfinite(value) or value<=0:
            raise RuntimeError("Invalid real temperature at node %s"%label)
        temperatures[label]=value
    if not temperatures:
        raise RuntimeError("No NX temperatures for visualization")
    with open(os.path.join(raw_dir,"elements.csv"),newline="") as stream:
        elements=list(csv.DictReader(stream))
    by_component={}
    for element in elements:
        kind=element["element_type"]
        if kind=="TET4":
            faces=[(0,2,1),(0,1,3),(1,2,3),(2,0,3)]
            count=4
        elif kind in ('HEX8','HEXA8'):
            faces,count=HEX_FACES,8
        elif kind=="TRI3":
            faces,count=[(0,1,2)],3
        elif kind=="QUAD4":
            faces,count=[(0,1,2),(0,2,3)],4
        else:
            raise RuntimeError("Unsupported visualization element "+kind)
        labels=[int(element["n%d"%i]) for i in range(1,count+1)]
        if any(n not in temperatures for n in labels):
            raise RuntimeError("NX result missing a mesh node")
        boundary=by_component.setdefault(element["business_id"],{})
        for face in faces:
            ids=tuple(labels[i] for i in face)
            key=tuple(sorted(ids))
            if key in boundary:
                del boundary[key]
            else:
                boundary[key]=ids
    surfaces=dict((cid,triangulate(list(boundary.values()))) for cid,boundary in by_component.items())
    write_glb(nodes,surfaces,snapshot,output,temperatures)
    return [min(temperatures.values()),max(temperatures.values())]
