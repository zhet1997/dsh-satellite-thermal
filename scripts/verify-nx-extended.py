"""Verify native mixed meshes, optical overrides and genuine handoff geometry."""
import json
from pathlib import Path
import struct

root = Path(__file__).resolve().parents[1]
jobs = {'solid-sheet-mutual': '2de5083384c348e48db5b088c21bb9a3',
        'solid-sheet-independent': '9ac24a96e0d74968bd3d0f4768c4ff8e',
        'face-optics': 'cbc15532ac1f4d5a92b88e97a9d2ce93',
        'real-gearbox': 'b7af50487d584a6c9e9c4e4a32883537'}
evidence = {}
for name, job in jobs.items():
    folder = root / 'runtime/nx-jobs' / job
    read = lambda p: json.loads((folder / p).read_text(encoding='utf-8-sig'))
    state, summary, readback = read('state.json'), read('results/summary.json'), read('results/readback.json')
    assert state['status'] == 'completed'
    binary = (folder / 'results/temperature.glb').read_bytes()
    gltf = json.loads(binary[20:20 + struct.unpack('<I', binary[12:16])[0]])
    assert all('COLOR_0' in p['attributes'] for m in gltf['meshes'] for p in m['primitives'])
    assert all(len(m['extras']['temperatureRangeK']) == 2 for m in gltf['meshes'])
    checks = {'completed': True, 'actualTemperatureColors': True, 'actualTemperatureRange': True}
    if name.startswith('solid-sheet'):
        assert summary['element_types']['TRI3'] > 0 and summary['element_types']['TET4'] > 0
        assert all(c['inputComparison'] == 'matched' for c in readback['components'])
        shell = next(c for c in readback['components'] if c['kind'] == 'shell')
        assert shell['thickness'] > 0
        if name.endswith('independent'):
            enclosures = [c for c in readback['conditions'] if c['kind'] == 'radiation']
            assert len(enclosures) == 2 and all(c['nativeDescriptor'] == 'Enclosure Radiation' for c in enclosures)
        checks.update(mixedNativeElements=True, actualShellThickness=shell['thickness'])
    if name == 'face-optics':
        reference = json.loads((root / 'runtime/nx-validation-inputs/radiation-face/analytic-reference.json').read_text())
        assert abs(summary['temperature_mean_K'] - reference['isothermalReferenceK']) < .5
        assert abs(state['result']['energyBalance']['heatFlowIntoSinksW'] - 10.4) < .02
        overrides = [c['opticalOverride'] for c in readback['conditions'] if c.get('opticalOverride')]
        assert len(overrides) == 12 and all(o['inputComparison'] == 'matched' for o in overrides)
        flux = next(c['fluxMapping'] for c in readback['conditions'] if c.get('fluxMapping'))
        assert abs(flux['absorbedFluxWm2'] - 40) < 1e-9
        checks.update(referenceMeanK=reference['isothermalReferenceK'], faceOverridesMatched=12, absorbedFaceFluxWm2=40)
    if name == 'real-gearbox':
        assert len(readback['faces']) == 133
        assert max(f['mappingErrorMm'] for f in readback['faces']) < .02
        assert abs(summary['temperature_min_K'] - 300) < .001
        assert abs(summary['temperature_max_K'] - 300) < .001
        checks.update(actualFaceCount=133, maxMappingErrorMm=max(f['mappingErrorMm'] for f in readback['faces']),
                      uniform300K=True, missionThermalValidation=False)
    evidence[name] = {'jobId': job, 'checks': checks, 'summary': summary,
                      'energyBalance': state['result']['energyBalance']}
report = {'kind': 'real-nx12-extended-validation', 'allPassed': True, 'evidence': evidence,
          'notes': ['Real gearbox uses artificial uniform 300 K boundary; this validates geometry and pipeline, not mission thermal conditions.',
                    'Near-zero heat flow in the uniform-temperature case makes relative energy residual ill-conditioned; absolute and relative values are both retained.',
                    'Native target group membership is covered by the final separate integration run; these earlier runs retain original artifacts.']}
(root / 'validation/nx-extended-result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'allPassed': True, 'jobs': jobs}))
