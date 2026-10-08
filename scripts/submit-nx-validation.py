"""Submit an isolated analytical fixture to the local worker; no synthetic results."""
import json
import sys
import time
from pathlib import Path
import httpx

root = Path(__file__).resolve().parents[1]
key = sys.argv[1] if len(sys.argv) > 1 else 'conduction'
folder = root / 'runtime' / 'nx-validation-inputs' / key
snapshot = json.loads((folder / 'snapshot.json').read_text(encoding='utf-8-sig'))
snapshot['run']['id'] = 'validation-' + key + '-' + str(time.time_ns())
snapshot['run']['caseId'] = snapshot['cases'][0]['id']
with httpx.Client(base_url='http://127.0.0.1:8791', timeout=60, trust_env=False) as client:
    with (folder / 'geometry.step').open('rb') as step:
        result = client.post('/jobs', data={'snapshot': json.dumps(snapshot), 'idempotencyKey': snapshot['run']['id']}, files={'geometry': ('geometry.step', step, 'application/step')})
    result.raise_for_status()
    job = result.json()
    (root / 'runtime' / ('last-nx-' + key + '.json')).write_text(json.dumps(job, indent=2), encoding='utf-8')
    print(json.dumps({k: job[k] for k in ('id','status','stage','message')}))
