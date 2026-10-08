"""Derive an explicit two-component enclosure fixture; preserve original input."""
import json
import shutil
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = root / 'runtime/nx-validation-inputs/solid-sheet'
target = root / 'runtime/nx-validation-inputs/solid-sheet-enclosure'
target.mkdir(exist_ok=True)
data = json.loads((source / 'snapshot.json').read_text(encoding='utf-8-sig'))
data['id'] += '-enclosure'
data['name'] = 'Solid + actual sheet; explicitly configured mutual radiation'
data['links'] = [{'id': 'explicit-radiation-1', 'kind': 'radiation',
                  'source': data['components'][0]['id'],
                  'target': data['components'][1]['id'], 'value': 1, 'unit': '',
                  'provenance': {'status': 'manual', 'source': 'Validation fixture: explicit mutual radiation'}}]
(target / 'snapshot.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
shutil.copy2(source / 'geometry.step', target / 'geometry.step')
print('Prepared separate solid-sheet-enclosure fixture; original retained')
