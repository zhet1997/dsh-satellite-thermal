"""Read native NX target groups and compare their actual members to frozen input."""
from __future__ import print_function
import hashlib


def target_key(target):
    return target["componentId"], target.get("faceId", "")


def read_native_targets(native, snapshot, expected_sets):
    face_owner = dict((f["id"], c["id"]) for c in snapshot["components"] for f in c["faces"])
    mesh_owner = dict((name, cid) for expected in expected_sets for cid, name in expected.get("componentMeshNames", {}).items())
    manager = native.TargetSetManager
    if manager.TargetSetCount != len(expected_sets):
        raise RuntimeError("NX target-set count differs from input: " + native.Name)
    result = []
    for index, expected in enumerate(expected_sets):
        group = manager.GetTargetSetGroup(index)
        if group is None:
            if expected["group"] is not None:
                raise RuntimeError("NX target group is missing: " + native.Name)
            result.append({"index": index, "group": None, "entityCount": 0, "targets": [], "inputComparison": "matched"})
            continue
        if group.GetName() != expected["group"]:
            raise RuntimeError("NX target group differs from input at set %d: %s" % (index, native.Name))
        entities = list(group.GetEntities())
        identity, targets, counts, meshes = [], {}, {}, set()
        for entity in entities:
            if expected['mode'] == 'element-faces':
                elements, indices = entity.GetElementsAndFaceIds()
                if len(elements) != len(indices):
                    raise RuntimeError('NX element-face resolution lengths differ: ' + native.Name)
                for element, face_index in zip(elements, indices):
                    mesh_name=element.Mesh.Name
                    if mesh_name not in mesh_owner:
                        raise RuntimeError('NX element-face contains an unknown mesh: ' + mesh_name)
                    cid=mesh_owner[mesh_name]
                    candidates=[t for t in expected['targets'] if t['componentId']==cid and t.get('faceId') in face_owner]
                    if len(candidates)!=1:
                        raise RuntimeError('NX element-face target lacks one source region: ' + native.Name)
                    target=candidates[0]
                    targets[target_key(target)]=target
                    identity.append('element-face:%s:%d:%d' % (mesh_name,int(element.Label),int(face_index)))
                    counts[cid]=counts.get(cid,0)+1
                    meshes.add(mesh_name)
                continue
            if expected["mode"] == "faces":
                fid = entity.GetStringAttribute("FACE_REGION_ID")
                if fid not in face_owner:
                    raise RuntimeError("NX target contains a face without frozen identity: " + native.Name)
                target = {"componentId": face_owner[fid], "faceId": fid}
                identity.append("face:" + fid)
            else:
                mesh_name = entity.Mesh.Name
                if mesh_name not in mesh_owner:
                    raise RuntimeError("NX target contains an unknown component mesh: " + mesh_name)
                cid = mesh_owner[mesh_name]
                target = {"componentId": cid}
                identity.append("element:" + mesh_name + ":" + str(entity.Label))
                counts[cid] = counts.get(cid, 0) + 1
                meshes.add(mesh_name)
            targets[target_key(target)] = target
        if len(set(identity)) != len(identity):
            raise RuntimeError("NX target group contains duplicate members: " + native.Name)
        actual_targets = [targets[key] for key in sorted(targets)]
        if len(entities) != expected["entityCount"] or set(targets) != set(target_key(t) for t in expected["targets"]):
            raise RuntimeError("NX target members differ from frozen input: " + native.Name)
        if expected["mode"] == "elements" and counts != expected["componentElementCounts"]:
            raise RuntimeError("NX target component element counts differ from meshed input: " + native.Name)
        if expected['mode']=='element-faces' and len(identity)!=expected['memberCount']:
            raise RuntimeError('NX element-face member count differs from meshed input: ' + native.Name)
        fingerprint = hashlib.sha256("\n".join(sorted(identity)).encode("utf-8")).hexdigest()
        if expected.get("memberIdentitySha256") and fingerprint != expected["memberIdentitySha256"]:
            raise RuntimeError("NX reopened target member identities changed: " + native.Name)
        result.append({"index": index, "group": group.GetName(), "mode": expected["mode"], "entityCount": len(entities), "memberCount":len(identity), "targets": actual_targets, "componentElementCounts": counts, "meshNames": sorted(meshes), "memberIdentitySha256": fingerprint, "inputComparison": "matched", "source": "NX target group actual entity identities"})
    return result
