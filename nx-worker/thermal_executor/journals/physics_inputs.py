"""Pure input mappings shared by the NX journal and numerical regression checks."""
from __future__ import print_function


def face_optics(component, face):
    """A face override wins; null and absent values inherit the component."""
    result = {}
    for name in ("absorptivity", "emissivity"):
        value = face.get(name)
        if value is None:
            value = component[name]
        value = float(value)
        if not 0 <= value <= 1:
            raise RuntimeError("Invalid " + name + " for face " + face["id"])
        result[name] = value
    return result


def absorbed_case_flux(case, direction, component, face):
    optics = face_optics(component, face)
    raw = float(case.get("flux", {}).get(direction, 0))
    spectral = case.get("fluxComponents", {}).get(direction)
    if case.get("opticalCoefficientsApplied") or case.get("valueKind") == "absorbed":
        value = raw
        basis = "already-absorbed"
    elif spectral:
        solar = float(spectral.get("direct", spectral.get("solar", 0)))
        albedo = float(spectral.get("albedo", 0))
        infrared = float(spectral.get("ir", spectral.get("infrared", 0)))
        value = (solar + albedo) * optics["absorptivity"] + infrared * optics["emissivity"]
        basis = "solar-and-albedo-times-alpha-plus-infrared-times-epsilon"
    else:
        value = raw * optics["absorptivity"]
        basis = "incident-total-times-alpha"
    return dict(optics, direction=direction, absorbedFluxWm2=value, incidentFluxWm2=raw, basis=basis)


def absorbed_surface_flux(case, direction, component, face):
    """A thermal sheet has two irradiated sides sharing one midsurface mesh."""
    front = absorbed_case_flux(case, direction, component, face)
    if component.get("kind") != "shell":
        return front
    opposite = ("-" if direction[0] == "+" else "+") + direction[1:]
    back = absorbed_case_flux(case, opposite, component, face)
    return dict(face_optics(component, face), direction=direction+"/"+opposite,
                absorbedFluxWm2=front["absorbedFluxWm2"]+back["absorbedFluxWm2"],
                incidentFluxWm2=front["incidentFluxWm2"]+back["incidentFluxWm2"],
                basis="sum-of-both-sheet-sides", sides=[front, back])


def radiation_enclosures(snapshot):
    """Map non-overlapping complete radiation subgraphs without adding edges."""
    components = dict((c["id"], c) for c in snapshot["components"])
    nodes, adjacency, links = {}, {}, []

    def add(cid, fid=None):
        if cid not in components or (fid and fid not in [f["id"] for f in components[cid]["faces"]]):
            raise RuntimeError("Unknown radiation target: " + str(fid or cid))
        key = fid or cid
        nodes[key] = {"componentId": cid}
        if fid:
            nodes[key]["faceId"] = fid
        adjacency.setdefault(key, set())
        return key

    for link in snapshot["links"]:
        if link["kind"] != "radiation":
            continue
        if float(link["value"]) != 1:
            raise RuntimeError("Radiation exchange multipliers other than 1 require a validated NX property mapping: " + link["id"])
        a = add(link["source"], link.get("sourceFace"))
        b = add(link["target"], link.get("targetFace"))
        if a == b:
            raise RuntimeError("Radiation edge targets the same region twice: " + link["id"])
        adjacency[a].add(b)
        adjacency[b].add(a)
        links.append((a, b, link["id"]))
    for cid in components:
        if cid in nodes and any(n.get("faceId") and n["componentId"] == cid for n in nodes.values()):
            raise RuntimeError("Radiation graph mixes overlapping component and face regions: " + cid)

    spaces = [b for b in snapshot["boundaries"] if b["kind"] == "space"]
    temperatures = set(float(b["value"]) for b in spaces)
    if len(temperatures) > 1:
        raise RuntimeError("NX radiation environment requires one space temperature per run")
    coverage, sources = {}, {}
    for boundary in spaces:
        for target in boundary["targets"]:
            cid = target["componentId"]
            selected = [target["faceId"]] if target.get("faceId") else [f["id"] for f in components[cid]["faces"]]
            coverage.setdefault(cid, set()).update(selected)
            for fid in selected:
                sources.setdefault(fid, set()).add(boundary["id"])
    space_nodes, boundary_ids = set(), {}
    for cid, selected in coverage.items():
        all_faces = set(f["id"] for f in components[cid]["faces"])
        face_nodes = any(n["componentId"] == cid and n.get("faceId") for n in nodes.values())
        if cid in nodes and selected != all_faces:
            raise RuntimeError("Space covers only part of component radiation region; select face regions consistently: " + cid)
        if selected == all_faces and not face_nodes:
            key = add(cid)
            space_nodes.add(key)
            boundary_ids[key] = set(bid for fid in selected for bid in sources[fid])
        else:
            for fid in selected:
                key = add(cid, fid)
                space_nodes.add(key)
                boundary_ids[key] = sources[fid]
    unseen, result = set(nodes), []
    while unseen:
        seed = min(unseen)
        members, pending = set(), [seed]
        while pending:
            key = pending.pop()
            if key in members:
                continue
            members.add(key)
            pending.extend(adjacency[key] - members)
        unseen -= members
        for key in sorted(members):
            missing = members - adjacency[key] - {key}
            if missing:
                raise RuntimeError("Radiation enclosure is missing explicit edge " + key + " <-> " + min(missing) + "; NX must not invent deleted or omitted edges")
        if members & space_nodes and not members <= space_nodes:
            raise RuntimeError("Space boundary must cover every region in one radiation enclosure; mixed environment membership is unsupported: " + seed)
        result.append({"id": "radiation-enclosure-" + str(len(result) + 1), "targets": [nodes[key] for key in sorted(members)], "includeEnvironment": bool(members & space_nodes), "inputIds": sorted(set([lid for a, b, lid in links if a in members and b in members] + [bid for key in members for bid in boundary_ids.get(key, ())]))})
    return result
