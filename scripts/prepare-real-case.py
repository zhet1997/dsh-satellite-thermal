"""Create a clearly labelled real-geometry pipeline check, not a mission prediction."""
import json
from pathlib import Path
import time
import httpx

root = Path(__file__).resolve().parents[1]
source = Path('D:/Codes/dsh-satellite-geometry/cases/real-gearbox/original.step')
with httpx.Client(base_url='http://127.0.0.1:8790/api', timeout=180, trust_env=False) as client:
    def call(method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()
    project = call('POST', '/projects', json={'name': '真实减速器几何验证 · 300 K 恒温边界（非任务热工况）'})
    url = '/projects/' + project['id']
    project = call('PATCH', url, json={'revision': project['revision'], 'settings': {'autoRun': False, 'meshSizeMm': 5}})
    with source.open('rb') as stream:
        project = call('POST', url + '/geometry', files={'file': ('gearbox.step', stream, 'application/step')})
    for _ in range(60):
        if project['geometry']['status'] != 'importing':
            break
        time.sleep(1)
        project = call('GET', url)
    assert project['geometry']['status'] == 'ready', project['geometry']
    components = project['components']
    for component in components:
        component['power'] = 0
    project = call('PATCH', url, json={'revision': project['revision'], 'components': components,
        'links': [], 'boundaries': [{'id': 'uniform-temperature', 'kind': 'temperature',
            'name': 'Pipeline check: uniform 300 K', 'value': 300, 'unit': 'K',
            'targets': [{'componentId': c['id']} for c in components],
            'provenance': {'status': 'manual', 'source': 'Validation boundary, not measured mission input'}}],
        'cases': [{'id': 'uniform-300k', 'name': '几何流程验证 · 300 K', 'kind': 'manual', 'time': 0,
                   'season': 'manual', 'flux': {a: 0 for a in ['+X','-X','+Y','-Y','+Z','-Z']},
                   'source': 'Explicit zero incident flux for uniform temperature check'}]})
    snapshot = call('GET', url + '/standard')
    target = root / 'runtime/nx-validation-inputs/real-gearbox'
    target.mkdir(exist_ok=True)
    snapshot['run'] = {'id': 'real-gearbox-pipeline', 'caseId': 'uniform-300k'}
    (target / 'snapshot.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
    (target / 'geometry.step').write_bytes(source.read_bytes())
    print(json.dumps({'projectId': project['id'], 'components': len(components), 'faces': sum(len(c['faces']) for c in components)}))
