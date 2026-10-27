"""NX12 journal entry: isolated import/model/readback/solve/extract processes.

Existing importer, FEM/SIM construction, TET4 meshing and result export come
from the frozen nx-ug-thermal source. New descriptor names are runtime-probed.
"""
from __future__ import print_function
import json
import math
import os
import sys
import traceback
import itertools
import re

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "vendor"))
import NXOpen
import NXOpen.CAE
import NXOpen.UF
import build_geometry_master as geometry
import build_and_solve as legacy
import extract_results as extraction
from mesh_export import export_mesh
from probe import table_info
from physics_inputs import absorbed_surface_flux, face_optics, radiation_enclosures
from target_readback import read_native_targets, target_key
from element_face_mapping import map_exported_facets, member_fingerprint

_BOUNDS_CACHE={}

def read(path):
    with open(path,"r",encoding="utf-8-sig") as stream:
        return json.load(stream)

def write(path,value):
    with open(path,"w") as stream:
        json.dump(value,stream,indent=2,sort_keys=True)

def nxpath(task,name):
    return os.path.join(task["run_dir"],"nx",name)

def save(part):
    part.Save(part.SaveComponents.TrueValue,part.CloseAfterSave.FalseValue)

def bounds(uf,obj):
    # AskBoundingBox is an approximation and drifts on curved faces. Probe 13
    # verified exact native bounds against the neutral geometry to ~1e-7 mm.
    tag=int(obj.Tag)
    if tag in _BOUNDS_CACHE:
        return _BOUNDS_CACHE[tag]
    corner,directions,distances=uf.ModlGeneral.AskBoundingBoxExact(obj.Tag,0)
    corners=[[float(corner[axis])+sum(float(directions[i][axis])*float(distances[i])*flags[i] for i in range(3)) for axis in range(3)] for flags in itertools.product((0,1),repeat=3)]
    result=[min(point[i] for point in corners) for i in range(3)]+[max(point[i] for point in corners) for i in range(3)]
    _BOUNDS_CACHE[tag]=result
    return result

def max_error(a,b):
    return max(abs(float(x)-float(y)) for x,y in zip(a,b))

def import_geometry(task,snapshot,session,uf):
    _BOUNDS_CACHE.clear()
    part = geometry.create_master_part(session,task["master_part"])
    intermediate = nxpath(task,"imported_geometry.prt")
    # NX12's installed profile approximates STEP curves to 0.0254 mm. Small
    # trimmed cylinders can then drift beyond the 0.02 mm identity check.
    # Freeze a tighter per-job profile instead of loosening identity matching
    # or changing the user's installed translator settings.
    tolerance = float(snapshot.get("settings", {}).get("stepImportToleranceMm", 0.0001))
    if not math.isfinite(tolerance) or not 1e-8 <= tolerance <= 0.001:
        raise RuntimeError("stepImportToleranceMm must be between 1e-8 and 0.001 mm")
    installed_profile = os.path.join(task["nx_base"],"STEP214UG","step214ug.def")
    with open(installed_profile,"r") as stream:
        profile = stream.read()
    profile, count = re.subn(r"(?m)^PA_EDGE_FACE_BSPLINE_APPROX_TOL\s*=.*$", "PA_EDGE_FACE_BSPLINE_APPROX_TOL = %.12g" % tolerance, profile)
    if count != 1:
        raise RuntimeError("NX STEP import profile lacks a unique curve approximation tolerance")
    settings_file = nxpath(task,"step_import.def")
    with open(settings_file,"w") as stream:
        stream.write(profile)
    geometry.translate_step_to_part(session,{"business_id":"assembly","step_file":task["geometry_path"]},intermediate,settings_file)
    bodies = geometry.import_native_part(part,intermediate)
    if len(bodies) != len(snapshot["components"]):
        raise RuntimeError("NX imported %d bodies, snapshot declares %d"%(len(bodies),len(snapshot["components"])))
    unused = list(bodies)
    face_tolerance = float(snapshot.get("settings", {}).get("faceMappingToleranceMm", 0.02))
    if not math.isfinite(face_tolerance) or not 1e-6 <= face_tolerance <= 0.05:
        raise RuntimeError("faceMappingToleranceMm must be between 1e-6 and 0.05 mm")
    binding = {"source":"NX imported STEP","components":[],"faces":[],"importProfile":{"file":settings_file,"curveApproximationToleranceMm":tolerance,"faceMatchToleranceMm":face_tolerance},"mappingWarnings":[]}
    for component in sorted(snapshot["components"],key=lambda x:x["stepIndex"]):
        target = component.get("boundsMm")
        if target is None:
            target = [component["center"][i]-component["size"][i]/2 for i in range(3)] + [component["center"][i]+component["size"][i]/2 for i in range(3)]
        candidates = [(max_error(bounds(uf,b),target),b) for b in unused]
        candidates.sort(key=lambda x:x[0])
        if not candidates or candidates[0][0]>.02 or (len(candidates)>1 and abs(candidates[0][0]-candidates[1][0])<1e-6):
            raise RuntimeError("NX body geometry has no unique match for " + component["id"])
        body = candidates[0][1]
        unused.remove(body)
        if component["kind"] == "shell" and body.IsSolidBody:
            raise RuntimeError("Solid body marked shell requires a supplied midsurface STEP; automatic midsurface extraction has not been validated")
        if component["kind"] == "solid" and not body.IsSolidBody:
            raise RuntimeError("Solid component imported as a sheet")
        geometry.set_string_attribute(body,"AGENT_OBJECT_ID",component["id"])
        geometry.set_string_attribute(body,"OBJECT_TYPE",component["kind"])
        geometry.set_string_attribute(body,"MATERIAL_ID","MAT_"+legacy.safe_name(component["id"]))
        body.SetName("BODY_"+legacy.safe_name(component["id"]))
        binding["components"].append({"id":component["id"],"kind":component["kind"],"tag":int(body.Tag),"boundsMm":bounds(uf,body),"stepIndex":component["stepIndex"],"isSolidBody":bool(body.IsSolidBody)})
        remaining = list(body.GetFaces())
        for face in component["faces"]:
            if not face.get("boundsMm"):
                raise RuntimeError("Face boundsMm required for verified cross-kernel mapping: " + face["id"])
            face_candidates = [(max_error(bounds(uf,native),face["boundsMm"]),native) for native in remaining]
            face_candidates.sort(key=lambda x:x[0])
            if not face_candidates or face_candidates[0][0]>face_tolerance or (len(face_candidates)>1 and face_candidates[1][0]<=face_tolerance):
                raise RuntimeError("NX face geometry has no unique match for " + face["id"])
            native = face_candidates[0][1]
            remaining.remove(native)
            geometry.set_string_attribute(native,"FACE_REGION_ID",face["id"])
            native.SetName(legacy.safe_name(face["id"]))
            binding["faces"].append({"id":face["id"],"componentId":component["id"],"tag":int(native.Tag),"boundsMm":bounds(uf,native),"mappingErrorMm":face_candidates[0][0]})
            if face_candidates[0][0] > 0.02:
                measured = part.MeasureManager.NewFaceProperties(part.UnitCollection.FindObject("SquareMilliMeter"),part.UnitCollection.FindObject("MilliMeter"),0.999,[native])
                native_area = float(measured.Area)
                measured.Dispose()
                source_area = float(face.get("areaMm2",0))
                area_error = abs(native_area-source_area)/max(source_area,1e-12)
                if source_area <= 0 or area_error > 0.02:
                    raise RuntimeError("NX face area mismatch exceeds 2 percent for " + face["id"])
                binding["mappingWarnings"].append({"faceId":face["id"],"boundsErrorMm":face_candidates[0][0],"nativeAreaMm2":native_area,"sourceAreaMm2":source_area,"areaRelativeError":area_error,"meaning":"Unique identity accepted within explicit job tolerance; native geometry is not asserted identical to STEP"})
    save(part)
    write(nxpath(task,"binding.json"),binding)
    return binding

def named_property(fem,descriptor,name):
    # Neutral descriptor and language are validated by the environment probe.
    return fem.ModelingObjectPropertyTables.CreateModelingObjectPropertyTable(descriptor,"NX SPACE SYSTEMS THERMAL - Thermal","NX SPACE SYSTEMS THERMAL",name,0)

def polygon_faces_by_id(uf,cae_body):
    # Probe 14 verified FACE_REGION_ID survives FEM polygon conversion for
    # all 133 planar and curved faces of the imported gearbox geometry.
    located=uf.Sf.BodyAskFaces(cae_body.Tag)
    tags=list(located[1]) if isinstance(located,tuple) else list(located)
    result={}
    for tag in tags:
        native=NXOpen.TaggedObjectManager.GetTaggedObject(int(tag))
        try:
            fid=native.GetStringAttribute("FACE_REGION_ID")
        except Exception:
            continue
        if not fid:
            continue
        if fid in result:
            raise RuntimeError("NX polygon face identity is not unique: "+fid)
        result[fid]=native
    return result

def shell_mesh(fem,cae_body,size_mm,uf):
    builder = fem.BaseFEModel.MeshManager.CreateMesh2dBuilder(NXOpen.CAE.Mesh2d.Null)
    try:
        builder.ElementType.ElementDimension = NXOpen.CAE.ElementTypeBuilder.ElementType.Shell
        builder.ElementType.ElementOrder = NXOpen.CAE.ElementTypeBuilder.ElementOrderType.Linear
        supported = list(builder.ElementType.GetElementTypeNames())
        if "TRI3 Thin Shell" not in supported:
            raise RuntimeError("NX does not advertise TRI3 Thin Shell")
        builder.ElementType.ElementTypeName = "TRI3 Thin Shell"
        builder.PropertyTable.SetScalarWithDataPropertyValue("quad mesh overall edge size",float(size_mm),fem.UnitCollection.FindObject("MilliMeter"))
        builder.PropertyTable.SetBooleanPropertyValue("mapped mesh option bool",False)
        located=uf.Sf.BodyAskFaces(cae_body.Tag)
        tags=list(located[1]) if isinstance(located,tuple) else list(located)
        faces=[NXOpen.TaggedObjectManager.GetTaggedObject(int(tag)) for tag in tags]
        builder.SelectionList.Add(faces)
        meshes = list(builder.CommitMesh())
        if len(meshes)!=1:
            raise RuntimeError("Shell builder must return exactly one component mesh")
        return meshes[0]
    finally:
        builder.Destroy()

def swept_plate_mesh(fem,cae_body,component,size_mm,uf):
    """Sweep a verified six-face substrate along its measured cap normal.
    Face identities come from the frozen STEP mapping, including rotated
    substrates. No automatic fallback to a different meshing algorithm.
    """
    faces=component['faces']
    if len(faces)!=6:
        raise RuntimeError('Swept substrate must have six source faces')
    first=max(faces,key=lambda f:f['areaMm2'])
    opposing=[f for f in faces if sum(a*b for a,b in zip(first['normal'],f['normal']))<-.999999 and abs(first['areaMm2']/f['areaMm2']-1)<1e-6]
    if len(opposing)!=1:raise RuntimeError('Swept prism has no unique opposing equal-area cap')
    caps=[first,opposing[0]]
    thickness=abs(sum(caps[0]['normal'][i]*(caps[1]['center'][i]-caps[0]['center'][i]) for i in range(3)))
    native=polygon_faces_by_id(uf,cae_body)
    builder=fem.BaseFEModel.MeshManager.CreateMesh3dHexBuilder(NXOpen.CAE.SweptMesh.Null)
    try:
        builder.CreationType=NXOpen.CAE.Mesh3dHexBuilder.Type.BetweenAutomatic
        builder.ElementType.ElementDimension=NXOpen.CAE.ElementTypeBuilder.ElementType.SweepSolid
        builder.ElementType.ElementOrder=NXOpen.CAE.ElementTypeBuilder.ElementOrderType.Linear
        if 'HEXA8' not in builder.ElementType.GetElementTypeNames():
            raise RuntimeError('NX does not advertise HEXA8')
        builder.ElementType.ElementTypeName='HEXA8'
        builder.ElementType.DestinationCollector.AutomaticMode=True
        builder.SelectionList.Add(cae_body)
        builder.SourceFaceList.Add(native[caps[0]['id']]);builder.TargetFaceList.Add(native[caps[1]['id']])
        for face in faces:
            if face not in caps:builder.WallFaceList.Add(native[face['id']])
        unit=fem.UnitCollection.FindObject('MilliMeter')
        for key in ('quad mesh overall edge size','source element size'):
            builder.PropertyTable.SetScalarWithDataPropertyValue(key,float(size_mm),unit)
        builder.PropertyTable.SetBooleanPropertyValue('mapped mesh option bool',True)
        builder.PropertyTable.SetIntegerPropertyValue('quad only option',1)
        builder.PropertyTable.SetBooleanPropertyValue('Number of Layers bool',True)
        builder.PropertyTable.SetIntegerPropertyValue('number of layers',max(1,int(round(thickness/size_mm))))
        before=set(int(m.Tag) for m in fem.BaseFEModel.MeshManager.GetMeshes())
        builder.CommitMesh()
        meshes=[m for m in fem.BaseFEModel.MeshManager.GetMeshes() if int(m.Tag) not in before]
        if len(meshes)!=1:raise RuntimeError('Swept substrate produced %d meshes'%len(meshes))
        return meshes[0]
    finally:builder.Destroy()

def assign_collector(fem,mesh,component,material):
    descriptor = "ThinShell" if component["kind"]=="shell" else "Solid"
    builder = fem.BaseFEModel.MeshManager.CreateCollectorBuilder(mesh.MeshCollector,descriptor)
    options = fem.NewMaterialOptions()
    try:
        builder.CollectorName = "MC_"+legacy.safe_name(component["id"])
        options.Material, options.MaterialInherited = material, False
        builder.PropertyTable.SetPhysicalMaterialPropertyValue("material",options)
        optical = named_property(fem,"Optical Properties","OPT_"+legacy.safe_name(component["id"]))
        _,optical_unit = optical.PropertyTable.GetScalarWithDataPropertyValue("emissivity")
        if optical_unit is None:
            optical.PropertyTable.SetDoublePropertyValue("emissivity",float(component["emissivity"]))
        else:
            optical.PropertyTable.SetScalarWithDataPropertyValue("emissivity",float(component["emissivity"]),optical_unit)
        builder.PropertyTable.SetBooleanPropertyValue("radiation",True)
        if component["kind"] == "shell":
            shell = fem.PhysicalPropertyTables.CreatePhysicalPropertyTable("Shell Property","NX SPACE SYSTEMS THERMAL - Thermal","NX SPACE SYSTEMS THERMAL","THK_"+legacy.safe_name(component["id"]),0)
            current,unit = shell.PropertyTable.GetScalarWithDataPropertyValue("element thickness")
            shell.PropertyTable.SetScalarWithDataPropertyValue("element thickness",float(component["thickness"]),unit)
            builder.PropertyTable.SetNamedPropertyTablePropertyValue("Shell Property",shell)
            builder.PropertyTable.SetNamedPropertyTablePropertyValue("top optical properties",optical)
            builder.PropertyTable.SetNamedPropertyTablePropertyValue("bottom optical properties",optical)
        else:
            builder.PropertyTable.SetNamedPropertyTablePropertyValue("optical properties",optical)
        builder.Commit()
    finally:
        builder.Destroy()
        if hasattr(options,"FreeResource"):
            options.FreeResource()

def build_model(task,snapshot,session,uf):
    enclosures=radiation_enclosures(snapshot)
    master = legacy.open_base_display(session,task["master_part"])
    semantics = {"bodies":[{"business_id":c["id"]} for c in snapshot["components"]]}
    fem = legacy.create_fem(session,master,task["fem_path"],semantics)
    session.Parts.SetDisplay(fem,False,False)
    session.Parts.SetWork(fem)
    materials = legacy.create_materials(fem,dict(("MAT_"+legacy.safe_name(c["id"]),{"density_kg_m3":c["density"],"conductivity_W_mK":c["conductivity"],"heat_capacity_J_kgK":c["specificHeat"]}) for c in snapshot["components"]))
    modeling = legacy.body_map(master)
    mesh_rows, all_elements = [], []
    groups,region_faces = {},{}
    for component in snapshot["components"]:
        cid = component["id"]
        print("NX_MESH_BEGIN " + json.dumps({"componentId":cid,"stepIndex":component["stepIndex"],"faceCount":len(component["faces"])}))
        sys.stdout.flush()
        cae_body = legacy.polygon_body(uf,modeling[cid])
        size = float(snapshot["settings"]["meshSizeMm"])
        if component["kind"]=="shell":
            mesh = shell_mesh(fem,cae_body,size,uf)
        elif cid in snapshot['settings'].get('sweptSolidComponentIds',[]):
            size=float(snapshot['settings'].get('sweptMeshSizeMm',size))
            mesh=swept_plate_mesh(fem,cae_body,component,size,uf)
        else:
            mesh,size,_,_ = legacy.create_tet_mesh(fem,cae_body,size,retry_scales=(1.0,))
        mesh.SetName("MESH_"+legacy.safe_name(cid))
        assign_collector(fem,mesh,component,materials["MAT_"+legacy.safe_name(cid)])
        located=uf.Sf.LocateElement(mesh.Tag)
        tags = list(located[1]) if isinstance(located,tuple) else list(located)
        print("NX_MESH_END " + json.dumps({"componentId":cid,"elementCount":len(tags),"sizeMm":size}))
        sys.stdout.flush()
        if not tags:
            raise RuntimeError("NX generated no elements for component " + cid + "; inspect the native mesher output")
        mesh_rows.append({"componentId":cid,"kind":component["kind"],"meshTag":int(mesh.Tag),"meshName":mesh.Name,"collectorName":mesh.MeshCollector.Name,"sizeMm":size,"thicknessMm":float(component.get("thickness",0))})
    save(fem)
    write(nxpath(task,'mesh-creation-report.json'),mesh_rows)
    mesh_report = export_mesh(uf,mesh_rows,task["raw_results_dir"],task["output_dir"],snapshot)
    facet_map = map_exported_facets(task['raw_results_dir'],snapshot)
    facet_specs = {}
    source_by_id=dict((c['id'],c) for c in snapshot['components'])
    flux_faces=set()
    for boundary in snapshot['boundaries']:
        if boundary['kind']=='heatflux' and float(boundary['value'])!=0:
            for target in boundary['targets']:
                flux_faces.update([target['faceId']] if target.get('faceId') else [f['id'] for f in source_by_id[target['componentId']]['faces']])
    if any(float(value) for case in snapshot.get('cases',[]) for value in case.get('flux',{}).values()) or any(float(value) for case in snapshot.get('cases',[]) for spectrum in case.get('fluxComponents',{}).values() for value in spectrum.values()):
        flux_faces.update(facet_map)
    # Each mesh commit can rebuild the polygon model and invalidate objects
    # captured before subsequent meshes. Discover groups only after meshing.
    for component in snapshot['components']:
        cid=component['id'];cae_body=legacy.polygon_body(uf,modeling[cid])
        groups[cid]=fem.CaeGroups.CreateGroup('BODY_'+legacy.safe_name(cid),[cae_body])
        native_faces=polygon_faces_by_id(uf,cae_body);region_faces[cid]=native_faces
        for face in component['faces']:
            native=native_faces.get(face['id'])
            if native is None:raise RuntimeError('Fresh NX polygon face lacks frozen identity: '+face['id'])
            groups[face['id']]=fem.CaeGroups.CreateGroup('FACE_'+legacy.safe_name(face['id']),[native])
        row=next(r for r in mesh_rows if r['componentId']==cid)
        located=uf.Sf.LocateElement(row['meshTag']);tags=list(located[1]) if isinstance(located,tuple) else list(located)
        elements=[NXOpen.TaggedObjectManager.GetTaggedObject(int(t)) for t in tags]
        groups['elements:'+cid]=fem.CaeGroups.CreateGroup('ELEMENTS_'+legacy.safe_name(cid),elements)
        by_label = dict((int(e.Label),e) for e in elements)
        for face in component['faces']:
            fid=face['id']
            if fid not in facet_map or fid not in flux_faces:
                continue
            mapped=facet_map[fid]
            groups['element-faces:'+fid]=fem.CaeGroups.CreateGroup('ELEM_FACE_'+legacy.safe_name(fid),[by_label[label].GetElemFace(index) for label,index in mapped['members']])
            facet_specs[fid]={'mode':'element-faces','targets':[{'componentId':cid,'faceId':fid}], 'entityCount':len(groups['element-faces:'+fid].GetEntities()),'memberCount':len(mapped['members']), 'componentMeshNames':{cid:row['meshName']},'memberIdentitySha256':member_fingerprint(row['meshName'],mapped['members']), 'meshAreaMm2':mapped['areaMm2'],'sourceAreaMm2':mapped['sourceAreaMm2'],'areaRelativeError':mapped['areaRelativeError']}
        all_elements.extend(elements)
    groups["all"] = fem.CaeGroups.CreateGroup("ALL_ELEMENTS",all_elements)
    component_by_id=dict((c["id"],c) for c in snapshot["components"])
    for enclosure in enclosures:
        top,bottom=[],[]
        for target in enclosure["targets"]:
            cid=target["componentId"]
            selected=[region_faces[cid][target["faceId"]]] if target.get("faceId") else list(region_faces[cid].values())
            top.extend(selected)
            if component_by_id[cid]["kind"]=="shell":
                bottom.extend(selected)
        eid=enclosure["id"]
        groups[eid+":top"]=fem.CaeGroups.CreateGroup(legacy.safe_name(eid)+"_TOP",top)
        if bottom:
            groups[eid+":bottom"]=fem.CaeGroups.CreateGroup(legacy.safe_name(eid)+"_BOTTOM",bottom)
    write(nxpath(task,'element-face-mapping-report.json'),facet_specs)
    save(fem)
    sim = legacy.create_sim(session,fem,task["sim_path"])
    session.Parts.SetWork(sim)
    by_name = dict((g.GetName(),g) for g in sim.CaeGroups)
    sim_groups = dict((key,by_name[group.GetName()]) for key,group in groups.items())
    solution,applied = build_physics(sim,sim_groups,snapshot,mesh_report,enclosures,facet_specs)
    save(sim)
    internal=sum(float(c['power']) for c in snapshot['components'])
    source_components=dict((c['id'],c) for c in snapshot['components'])
    source_faces=dict((f['id'],f) for c in snapshot['components'] for f in c['faces'])
    external=sum(float(b['value'])*sum(f['areaMm2'] for f in ([source_faces[t['faceId']]] if t.get('faceId') else source_components[t['componentId']]['faces']))*1e-6 for b in snapshot['boundaries'] if b['kind']=='heatflux' for t in b['targets'])
    # Explicit face flux runs have zero six-axis case loads. Preserve the
    # legacy denominator when a generic case contributes additional loads.
    explicit_only=not any(float(x) for case in snapshot['cases'] for x in case.get('flux',{}).values())
    thermal_case={'total_power_W':internal+external if explicit_only else internal,'internal_power_W':internal,'absorbed_explicit_external_power_W':external,'input_power_basis':'internal W plus absorbed face flux W/m2 times area mm2 times 1e-6' if explicit_only else 'internal W; additional case flux not integrated here'}
    write(task["thermal_case_path"],thermal_case)
    write(nxpath(task,"model-report.json"),{"mesh":mesh_report,"applied":applied,"solution":solution.Name})
    return {"mesh":mesh_report,"applied":applied}

def build_physics(sim,groups,snapshot,mesh,enclosures,facet_specs=None):
    simulation = sim.Simulation
    solution = simulation.CreateSolution("NX SPACE SYSTEMS THERMAL","Thermal","Space Systems Thermal","steady_state_thermal")
    simulation.ActiveSolution = solution
    solution.PropertyTable.SetBooleanPropertyValue("Foreground",False)
    solution.PropertyTable.SetBooleanPropertyValue("Run Solution in Parallel",False)
    solution.PropertyTable.SetBooleanPropertyValue("Temperatures",True)
    space = [b for b in snapshot["boundaries"] if b["kind"]=="space"]
    radiation_links=[link for link in snapshot["links"] if link["kind"]=="radiation"]
    temperatures = set(float(b["value"]) for b in space)
    if len(temperatures)>1:
        raise RuntimeError("NX global radiation environment requires one space temperature per run")
    temperature = next(iter(temperatures)) if temperatures else float(snapshot["settings"]["spaceTemperatureK"])
    solution.PropertyTable.SetDoublePropertyValue("Radiative Environment Temperature",temperature-273.15)
    component_by_id=dict((c["id"],c) for c in snapshot["components"])
    mesh_by_id=dict((row["componentId"],row) for row in mesh["components"])
    group_specs={}
    for fid,spec in (facet_specs or {}).items():
        group=groups['element-faces:'+fid]
        group_specs[group.GetName()]=dict(spec,group=group.GetName())
    def expect_group(key,mode,targets):
        spec={"group":groups[key].GetName(),"mode":mode,"targets":targets}
        if mode=="elements":
            ids=[t["componentId"] for t in targets]
            spec["componentElementCounts"]=dict((cid,mesh_by_id[cid]["elementCount"]) for cid in ids)
            spec["componentMeshNames"]=dict((cid,mesh_by_id[cid]["meshName"]) for cid in ids)
            spec["entityCount"]=sum(spec["componentElementCounts"].values())
        else:
            spec["entityCount"]=len(targets)
        group_specs[spec["group"]]=spec
    expect_group("all","elements",[{"componentId":c["id"]} for c in snapshot["components"]])
    for component in snapshot["components"]:
        expect_group("elements:"+component["id"],"elements",[{"componentId":component["id"]}])
        for face in component["faces"]:
            expect_group(face["id"],"faces",[{"componentId":component["id"],"faceId":face["id"]}])
    for enclosure in enclosures:
        top,bottom=[],[]
        for target in enclosure["targets"]:
            cid=target["componentId"]
            selected=[target] if target.get("faceId") else [{"componentId":cid,"faceId":face["id"]} for face in component_by_id[cid]["faces"]]
            top.extend(selected)
            if component_by_id[cid]["kind"]=="shell":
                bottom.extend(selected)
        expect_group(enclosure["id"]+":top","faces",top)
        if bottom:
            expect_group(enclosure["id"]+":bottom","faces",bottom)
    applied=[]
    def commit(builder,input_id,kind,targets):
        expected=[]
        for index in range(builder.TargetSetManager.TargetSetCount):
            group=builder.TargetSetManager.GetTargetSetGroup(index)
            expected.append(dict(group_specs[group.GetName()],index=index) if group is not None else {"index":index,"group":None})
        bc=legacy.commit_bc(builder,solution)
        actual=read_native_targets(bc,snapshot,expected)
        for native_set,expected_set in zip(actual,expected):
            if native_set.get("memberIdentitySha256"):
                expected_set["memberIdentitySha256"]=native_set["memberIdentitySha256"]
        actual_targets=dict((target_key(t),t) for native_set in actual for t in native_set["targets"])
        applied.append({"inputId":input_id,"kind":kind,"name":bc.Name,"tag":int(bc.Tag),"targets":[actual_targets[key] for key in sorted(actual_targets)],"expectedTargets":targets,"expectedTargetSets":expected,"targetSets":actual,"properties":table_info(bc.PropertyTable),"source":"NX committed object readback"})
        return bc
    element=NXOpen.CAE.CaeSetGroupFilterType.Element
    geom=NXOpen.CAE.CaeSetGroupFilterType.GeomFace
    def target_flux(builder,fid):
        key='element-faces:'+fid
        if key in groups:
            legacy.target_group(builder,0,NXOpen.CAE.CaeSetGroupFilterType.ElementFace,groups[key])
        else:
            legacy.target_group(builder,0,geom,groups[fid])
    # Probe 15 validated these exact native descriptors and property names.
    for component in snapshot["components"]:
        for face in component["faces"]:
            optics=face_optics(component,face)
            for key,descriptor,property_name in (("emissivity","Override Thermal Emissivity","Emissivity"),("absorptivity","Override Thermal Solar Absorptivity","Solar Absorptivity")):
                if face.get(key) is None or abs(optics[key]-float(component[key]))<=1e-9:
                    continue
                builder=simulation.CreateBcBuilderForSimulationObjectDescriptor(descriptor,"OPT_"+key+"_"+legacy.safe_name(face["id"]))
                legacy.target_group(builder,0,geom,groups[face["id"]])
                _,unit=builder.PropertyTable.GetScalarWithDataPropertyValue(property_name)
                if unit is None:
                    builder.PropertyTable.SetDoublePropertyValue(property_name,optics[key])
                else:
                    builder.PropertyTable.SetScalarWithDataPropertyValue(property_name,optics[key],unit)
                builder.PropertyTable.SetIntegerPropertyValue("Apply Override Set to",0)
                commit(builder,face["id"],"face-"+key,[{"componentId":component["id"],"faceId":face["id"]}])
                applied[-1]["opticalOverride"]={"property":property_name,"value":optics[key],"shellSides":"top-and-bottom"}
    initial=simulation.CreateBcBuilderForConstraintDescriptor("Initial Temperature","INITIAL_TEMPERATURE")
    legacy.target_group(initial,0,element,groups["all"])
    initial.PropertyTable.SetDoublePropertyValue("Temperature",float(snapshot["settings"].get("initialTemperatureK",293.15))-273.15)
    commit(initial,"initial","initial",[])
    for component in snapshot["components"]:
        if float(component["power"])==0:
            continue
        builder=simulation.CreateBcBuilderForLoadDescriptor("Heat Generation","HEAT_"+legacy.safe_name(component["id"]))
        legacy.target_group(builder,0,element,groups["elements:"+component["id"]])
        builder.PropertyTable.SetScalarWithDataPropertyValue("Heat Generation",float(component["power"])/mesh["volumeMm3"][component["id"]],sim.UnitCollection.FindObject("HeatGeneration_Metric2"))
        commit(builder,component["id"],"power",[{"componentId":component["id"]}])
    for link in snapshot["links"]:
        if link["kind"]=="radiation":
            if float(link["value"]) != 1:
                raise RuntimeError("Radiation exchange multipliers other than 1 require a validated NX property mapping")
            continue
        source,target=link.get("sourceFace"),link.get("targetFace")
        if not source or not target:
            raise RuntimeError("Thermal coupling requires explicitly matched source and target faces: "+link["id"])
        builder=simulation.CreateBcBuilderForSimulationObjectDescriptor("Contact Thermal Coupling",legacy.safe_name(link["id"]))
        legacy.target_group(builder,0,geom,groups[source])
        legacy.target_group(builder,1,geom,groups[target])
        if link["kind"]=="conductance":
            builder.PropertyTable.SetIntegerPropertyValue("Type",0)
            builder.PropertyTable.SetScalarWithDataPropertyValue("Total Conductance",float(link["value"]),sim.UnitCollection.FindObject("ThermalConductance_Metric4"))
        else:
            area=float(link.get("contactAreaMm2",0))
            if area<=0:
                raise RuntimeError("Area-resistance contact requires measured contactAreaMm2: "+link["id"])
            builder.PropertyTable.SetIntegerPropertyValue("Type",1)
            builder.PropertyTable.SetScalarWithDataPropertyValue("Total Resistance",float(link["value"])/(area*1e-6),sim.UnitCollection.FindObject("ThermalResistance_Metric4"))
        commit(builder,link["id"],link["kind"],[{"componentId":link["source"],"faceId":source},{"componentId":link["target"],"faceId":target}])
    # Each native enclosure represents one complete, disjoint graph component.
    # Its two target sets mean top/bottom surface, not source/destination.
    for enclosure in enclosures:
        eid=enclosure["id"]
        global_region=len(enclosures)==1 and all(not t.get("faceId") for t in enclosure["targets"]) and set(t["componentId"] for t in enclosure["targets"])==set(c["id"] for c in snapshot["components"])
        descriptor="All Radiation" if global_region else "Enclosure Radiation"
        radiation=simulation.CreateBcBuilderForSimulationObjectDescriptor(descriptor,legacy.safe_name(eid))
        if global_region:
            legacy.target_group(radiation,0,element,groups["all"])
            radiation.PropertyTable.SetBooleanPropertyValue("Radiate from Faces of Solid Elements",True)
        else:
            legacy.target_group(radiation,0,geom,groups[eid+":top"])
            if eid+":bottom" in groups:
                legacy.target_group(radiation,1,geom,groups[eid+":bottom"])
            radiation.PropertyTable.SetBooleanPropertyValue("Elements not Part of this Enclosure Can Shadow",True)
        radiation.PropertyTable.SetBooleanPropertyValue("Include Radiative Environment",enclosure["includeEnvironment"])
        commit(radiation,eid,"radiation",enclosure["targets"])
        applied[-1]["representedInputIds"]=enclosure["inputIds"]
        applied[-1]["topologyCheck"]="disjoint complete enclosure; no missing graph edges introduced"
        applied[-1]["nativeDescriptor"]=descriptor
    faces=dict((f["id"],(c,f)) for c in snapshot["components"] for f in c["faces"])
    for boundary in snapshot["boundaries"]:
        if boundary["kind"]=="space":
            continue
        for index,target in enumerate(boundary["targets"]):
            selected=[target["faceId"]] if target.get("faceId") else [f["id"] for c in snapshot["components"] if c["id"]==target["componentId"] for f in c["faces"]]
            for fid in selected:
                value=float(boundary["value"])
                if boundary["kind"]=="heatflux" and value==0:
                    continue
                if boundary["kind"]=="heatflux":
                    builder=simulation.CreateBcBuilderForLoadDescriptor("Heat Flux","FLUX_"+legacy.safe_name(boundary["id"]+fid))
                    builder.PropertyTable.SetScalarWithDataPropertyValue("Heat Flux",value,sim.UnitCollection.FindObject("HeatFlux_Metric5"))
                else:
                    builder=simulation.CreateBcBuilderForConstraintDescriptor("Temperature","TEMP_"+legacy.safe_name(boundary["id"]+fid))
                    builder.PropertyTable.SetScalarWithDataPropertyValue("Temperature",value,sim.UnitCollection.FindObject("Kelvin"))
                if boundary['kind']=='heatflux':
                    target_flux(builder,fid)
                else:
                    legacy.target_group(builder,0,geom,groups[fid])
                commit(builder,boundary["id"],boundary["kind"],[{"componentId":target["componentId"],"faceId":fid}])
    cases=snapshot.get("cases",[])
    case_id=snapshot.get("run",{}).get("caseId")
    case=next((c for c in cases if c["id"]==case_id),cases[0] if cases else {})
    for fid,(component,face) in faces.items():
        normal=face["normal"]
        axis=max(range(3),key=lambda i:abs(normal[i]))
        if abs(normal[axis])<.999:
            if any(float(x) for x in case.get("flux",{}).values()) or any(float(x) for spectrum in case.get("fluxComponents",{}).values() for x in spectrum.values()):
                raise RuntimeError("Six-axis case mapping requires axis-aligned faces; use explicit face heat-flux boundaries for this geometry")
            continue
        direction=("+" if normal[axis]>0 else "-")+"XYZ"[axis]
        mapping=absorbed_surface_flux(case,direction,component,face)
        absorbed=mapping["absorbedFluxWm2"]
        if absorbed:
            builder=simulation.CreateBcBuilderForLoadDescriptor("Heat Flux","CASE_FLUX_"+legacy.safe_name(fid))
            target_flux(builder,fid)
            builder.PropertyTable.SetScalarWithDataPropertyValue("Heat Flux",absorbed,sim.UnitCollection.FindObject("HeatFlux_Metric5"))
            commit(builder,case.get("id","case"),"case-heatflux",[{"componentId":component["id"],"faceId":fid}])
            applied[-1]["fluxMapping"]=mapping
    return solution,applied

def readback(task,snapshot,session,uf):
    fem=legacy.open_base_display(session,task["fem_path"])
    session.Parts.SetWork(fem)
    report=read(nxpath(task,"model-report.json"))
    mesh_by_name=dict((m.Name,m) for m in fem.BaseFEModel.MeshManager.GetMeshes())
    for row in report["mesh"]["components"]:
        native=mesh_by_name[row["meshName"]]
        row["meshTag"]=int(native.Tag)
    actual_mesh=export_mesh(uf,report["mesh"]["components"],task["raw_results_dir"],task["output_dir"],snapshot)
    component_rows=[]
    for component in snapshot["components"]:
        native=mesh_by_name["MESH_"+legacy.safe_name(component["id"])]
        builder=fem.BaseFEModel.MeshManager.CreateCollectorBuilder(native.MeshCollector,"ThinShell" if component["kind"]=="shell" else "Solid")
        try:
            collector=[r for r in table_info(builder.PropertyTable) if r["name"] in ("material","Shell Property","optical properties","top optical properties","bottom optical properties","radiation")]
            options=builder.PropertyTable.GetPhysicalMaterialPropertyValue("material")
            material=options.Material
            if material is None or material.Name!="MAT_"+legacy.safe_name(component["id"]):
                raise RuntimeError("Actual collector material differs from frozen component "+component["id"])
            properties=[r for r in table_info(material.GetPropTable()) if r["name"] in ("MassDensity","ThermalConductivity","SpecificHeat")]
            actual={}
            for property_name,key in (("MassDensity","density"),("ThermalConductivity","conductivity"),("SpecificHeat","specificHeat")):
                value,unit=material.GetPropTable().GetBaseScalarWithDataPropertyValue(property_name)
                if abs(float(value)-float(component[key]))>max(1e-10,abs(float(component[key]))*1e-9):
                    raise RuntimeError("NX readback does not match standard "+key+" for "+component["id"])
                actual[key]=float(value)
            optical_keys=("top optical properties","bottom optical properties") if component["kind"]=="shell" else ("optical properties",)
            for optical_key in optical_keys:
                optical=builder.PropertyTable.GetNamedPropertyTablePropertyValue(optical_key)
                if optical is None:
                    raise RuntimeError("NX collector optical property is missing")
                actual["emissivity"]=float(optical.PropertyTable.GetDoublePropertyValue("emissivity"))
                if abs(actual["emissivity"]-float(component["emissivity"]))>1e-9:
                    raise RuntimeError("NX emissivity differs from standard input")
            if component["kind"]=="shell":
                shell=builder.PropertyTable.GetNamedPropertyTablePropertyValue("Shell Property")
                thickness,unit=shell.PropertyTable.GetScalarWithDataPropertyValue("element thickness")
                actual["thickness"]=float(thickness)
                if abs(actual["thickness"]-float(component["thickness"]))>1e-9:
                    raise RuntimeError("NX shell thickness differs from standard input")
            component_rows.append(dict(actual,id=component["id"],kind=component["kind"],meshTag=int(native.Tag),materialName=material.Name,materialProperties=properties,collectorProperties=collector,source="NX reopened FEM",inputComparison="matched"))
        finally:
            builder.Destroy()
    sim=legacy.open_base_display(session,task["sim_path"])
    solution=next(s for s in sim.Simulation.Solutions if s.Name=="steady_state_thermal")
    bcs=dict((b.Name,b) for b in solution.GetBcs())
    for applied in report["applied"]:
        native=bcs[applied["name"]]
        applied["tag"]=int(native.Tag)
        applied["properties"]=table_info(native.PropertyTable)
        applied["source"]="NX reopened SIM"
        applied["targetSets"]=read_native_targets(native,snapshot,applied["expectedTargetSets"])
        actual_targets=dict((target_key(t),t) for native_set in applied["targetSets"] for t in native_set["targets"])
        applied["targets"]=[actual_targets[key] for key in sorted(actual_targets)]
        if applied.get("opticalOverride"):
            expected=applied["opticalOverride"]
            actual=float(native.PropertyTable.GetDoublePropertyValue(expected["property"]))
            if abs(actual-expected["value"])>1e-9:
                raise RuntimeError("NX face optical override readback differs from input: "+applied["name"])
            expected["actualValue"]=actual
            expected["inputComparison"]="matched"
        if applied.get("fluxMapping"):
            flux,unit=native.PropertyTable.GetScalarWithDataPropertyValue("Heat Flux")
            expected=applied["fluxMapping"]["absorbedFluxWm2"]
            if abs(float(flux)-expected)>max(1e-9,abs(expected)*1e-9):
                raise RuntimeError("NX case flux readback differs from face optical mapping: "+applied["name"])
            applied["fluxMapping"]["actualAbsorbedFluxWm2"]=float(flux)
            applied["fluxMapping"]["inputComparison"]="matched"
    value={"source":"nx","checks":{"status":"readback-complete","solverExecuted":False},"components":component_rows,"faces":read(nxpath(task,"binding.json"))["faces"],"mesh":actual_mesh,"conditions":report["applied"],"meshArtifact":"mesh.glb"}
    write(os.path.join(task["output_dir"],"readback.json"),value)
    return value

def solve(task,session):
    sim=legacy.open_base_display(session,task["sim_path"])
    session.Parts.SetWork(sim)
    solution=next(s for s in sim.Simulation.Solutions if s.Name=="steady_state_thermal")
    sim.Simulation.ActiveSolution=solution
    # Solve performs the complete setup check and writes the input itself.
    # A preceding WriteSolverInputFile duplicates the expensive native export.
    check='CompleteCheckAndOutputErrors requested within Solve'
    status=solution.Solve(NXOpen.CAE.SimSolution.SolveOption.Solve,NXOpen.CAE.SimSolution.SetupCheckOption.CompleteCheckAndOutputErrors)
    files=legacy.wait_for_solver_files(task["sim_path"],solution.Name,task["solver_timeout_seconds"])
    save(sim)
    report={"ok":True,"setup_status":str(check),"solve_status":str(status),"solver_completed":True,"solver_files":files,"physics":read(nxpath(task,"model-report.json"))["applied"]}
    write(nxpath(task,"solve_report.json"),report)
    return report

def main():
    task=read(os.environ["NX_THERMAL_TASK"])
    result={"ok":False,"stage":task["stage"]}
    try:
        if task["stage"]=="extract":
            extraction.main()
            return
        session=NXOpen.Session.GetSession()
        uf=NXOpen.UF.UFSession.GetUFSession()
        snapshot=read(task["snapshot_path"])
        if task["stage"]=="import":
            value=import_geometry(task,snapshot,session,uf)
        elif task["stage"]=="model":
            value=build_model(task,snapshot,session,uf)
        elif task["stage"]=="readback":
            value=readback(task,snapshot,session,uf)
        elif task["stage"]=="solve":
            value=solve(task,session)
        else:
            raise RuntimeError("Unknown NX stage")
        result.update(ok=True,details=value)
    except Exception:
        result["error"]=traceback.format_exc()
    write(task["result_path"],result)

if __name__=="__main__":
    main()
