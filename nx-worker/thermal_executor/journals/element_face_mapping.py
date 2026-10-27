"""Map actual exterior TET4/HEX8 facets to planar STEP regions.

Uses exported native node coordinates and connectivity, not geometry-to-mesh
associations. Rejects missing/ambiguous regions and verifies integrated area.
"""
import csv
import hashlib
import math
import os
try:
    from .mesh_export import HEX_FACES, area, cross, sub
except ImportError:
    from mesh_export import HEX_FACES, area, cross, sub

TET_FACES = [(0, 2, 1), (0, 1, 3), (1, 2, 3), (2, 0, 3)]


def member_fingerprint(mesh_name, members):
    identities = ['element-face:%s:%d:%d' % (mesh_name, label, index) for label, index in members]
    return hashlib.sha256('\n'.join(sorted(identities)).encode('utf-8')).hexdigest()


def map_component_facets(component, nodes, elements, tolerance_mm=0.05):
    boundary = {}
    for element in elements:
        topology = HEX_FACES if element['element_type'] == 'HEX8' else TET_FACES if element['element_type'] == 'TET4' else None
        if topology is None:
            raise RuntimeError('Unsupported solid facet topology: ' + element['element_type'])
        labels = [int(element['n%d' % i]) for i in range(1, 9 if element['element_type'] == 'HEX8' else 5)]
        center = [sum(nodes[n][i] for n in labels) / len(labels) for i in range(3)]
        for index, local in enumerate(topology):
            ids = tuple(labels[i] for i in local)
            key = tuple(sorted(ids))
            if key in boundary:
                del boundary[key]
            else:
                boundary[key] = (int(element['element_id']), index, ids, center)
    result = dict((f['id'], {'members': [], 'areaMm2': 0.0}) for f in component['faces'])
    for label, index, ids, element_center in boundary.values():
        points = [nodes[n] for n in ids]
        normal = cross(sub(points[1], points[0]), sub(points[2], points[0]))
        length = math.sqrt(sum(x*x for x in normal))
        if length <= 0:
            raise RuntimeError('Zero-area exterior mesh facet')
        center = [sum(p[i] for p in points) / len(points) for i in range(3)]
        sign = 1 if sum(normal[i]*(center[i]-element_center[i]) for i in range(3)) > 0 else -1
        normal = [sign*x/length for x in normal]
        candidates = []
        for face in component['faces']:
            fn = face['normal']; origin = face['center']; bounds = face['boundsMm']
            if sum(normal[i]*fn[i] for i in range(3)) < 0.999:
                continue
            if not all(abs(sum((p[i]-origin[i])*fn[i] for i in range(3))) <= tolerance_mm for p in points):
                continue
            if not all(bounds[i]-tolerance_mm <= p[i] <= bounds[i+3]+tolerance_mm for p in points for i in range(3)):
                continue
            candidates.append(face['id'])
        if len(candidates) != 1:
            raise RuntimeError('Exterior facet %s/%d/%d maps to %d STEP regions: %s' % (component['id'], label, index, len(candidates), candidates))
        row = result[candidates[0]]
        row['members'].append((label, index))
        row['areaMm2'] += area(points[0], points[1], points[2])
        if len(points) == 4:
            row['areaMm2'] += area(points[0], points[2], points[3])
    for face in component['faces']:
        row = result[face['id']]
        row['sourceAreaMm2'] = float(face['areaMm2'])
        row['areaRelativeError'] = abs(row['areaMm2']/row['sourceAreaMm2']-1)
        if not row['members'] or row['areaRelativeError'] > 0.02:
            raise RuntimeError('Exterior facet area/coverage mismatch: %s, error=%g' % (face['id'], row['areaRelativeError']))
    return result


def map_exported_facets(raw_dir, snapshot):
    with open(os.path.join(raw_dir, 'nodes_geometry.csv'), newline='') as stream:
        nodes = dict((int(r['node_id']), [float(r[k]) for k in ('x_mm', 'y_mm', 'z_mm')]) for r in csv.DictReader(stream))
    by_component = {}
    with open(os.path.join(raw_dir, 'elements.csv'), newline='') as stream:
        for row in csv.DictReader(stream):
            by_component.setdefault(row['business_id'], []).append(row)
    result = {}
    for component in snapshot['components']:
        # Rotated planar sweep caps can be serialized as BSPLINE. A swept
        # component is eligible only if every native exterior facet passes
        # the same plane, normal, unique-region and area-closure checks.
        swept=component['id'] in snapshot['settings'].get('sweptSolidComponentIds',[])
        if component['kind'] == 'solid' and (swept or all(f['geometryType'] == 'PLANE' for f in component['faces'])):
            result.update(map_component_facets(component, nodes, by_component[component['id']]))
    return result
