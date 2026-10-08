"""New fixture with explicit face optics and independently known absorbed power."""
import json
from pathlib import Path
import shutil

root = Path(__file__).resolve().parents[1]
source = root / 'runtime/nx-validation-inputs/radiation'
target = root / 'runtime/nx-validation-inputs/radiation-face'
target.mkdir(exist_ok=True)
data = json.loads((source / 'snapshot.json').read_text(encoding='utf-8-sig'))
data['id'] += '-face-optics'
data['name'] = 'Actual face emissivity 0.6; absorptivity 0.4; +Z incident flux 100 W/m2'
for component in data['components']:
    for face in component['faces']:
        face['emissivity'] = 0.6
        face['absorptivity'] = 0.4
data['cases'][0]['flux']['+Z'] = 100
data['cases'][0].pop('fluxComponents', None)
data['cases'][0]['opticalCoefficientsApplied'] = False
data['cases'][0]['valueKind'] = 'incident-total'
(target / 'snapshot.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
shutil.copy2(source / 'geometry.step', target / 'geometry.step')
reference = {'radiativeAreaM2': 0.06, 'illuminatedAreaM2': 0.01, 'emissivity': 0.6, 'absorptivity': 0.4,
             'sourcePowerW': 10, 'absorbedFluxPowerW': 0.4, 'totalPowerW': 10.4,
             'isothermalReferenceK': (10.4 / (5.670374419e-8 * 0.6 * 0.06) + 3**4)**0.25}
(target / 'analytic-reference.json').write_text(json.dumps(reference, indent=2), encoding='utf-8')
print(json.dumps(reference))
