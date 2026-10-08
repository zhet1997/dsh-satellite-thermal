"""Compare retained, real NX results with independent analytical references."""
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'nx-worker'))
from thermal_executor.results import solver_balance

jobs = {
    'conduction': '9c766c672f60488b81ea902fc92e700e',
    'contact': '4dd6a48284ed4de593e12eff176bb98c',
    'radiation': '57005a0d30734b50946152d5fca7c0c9',
}
checks, evidence = [], {}
for case, job in jobs.items():
    folder = ROOT / 'runtime/nx-jobs' / job
    state = json.loads((folder / 'state.json').read_text())
    assert state['status'] == 'completed', (case, state['status'])
    summary = json.loads((folder / 'results/summary.json').read_text())
    snapshot = json.loads((folder / 'input/snapshot.json').read_text(encoding='utf-8-sig'))
    balance = solver_balance(folder, sum(c['power'] for c in snapshot['components']))
    evidence[case] = {
        'jobId': job, 'summary': summary, 'solverReportedBalance': balance,
        'snapshotSha256': hashlib.sha256((folder / 'input/snapshot.json').read_bytes()).hexdigest(),
        'msgfSha256': hashlib.sha256((folder / 'nx/MSGF').read_bytes()).hexdigest(),
    }
    values = {'steady_heat_flow_magnitude_W': balance['heatFlowIntoSinksW'],
              'mean_temperature_K': summary['temperature_mean_K'],
              'isothermal_limit_temperature_K': summary['temperature_mean_K'],
              'net_radiated_power_W': balance['heatFlowIntoSinksW']}
    if case == 'contact':
        ordered = sorted(snapshot['components'], key=lambda c: c['center'][0])
        stats = summary['component_temperatures_K']
        values['left_contact_temperature_K'] = stats[ordered[0]['id']]['max_K']
        values['right_contact_temperature_K'] = stats[ordered[1]['id']]['min_K']
        readback = json.loads((folder / 'results/readback.json').read_text())
        contact = next(c for c in readback['conditions'] if c['kind'] == 'contact')
        assert len(contact['targetSets']) == 2
        for index, target_set in enumerate(contact['targetSets']):
            assert target_set['index'] == index and target_set['inputComparison'] == 'matched'
            assert target_set['targets'] == [contact['expectedTargets'][index]]
            assert target_set['memberIdentitySha256'] == contact['expectedTargetSets'][index]['memberIdentitySha256']
        evidence[case]['actualNativeContactTargetsVerified'] = True
    evidence[case]['measured'] = values
with (ROOT / 'runtime/nx-validation-inputs/analytic-expected.csv').open(encoding='utf-8-sig') as stream:
    for row in csv.DictReader(stream):
        actual = evidence[row['case']]['measured'][row['quantity']]
        error = abs(actual - float(row['reference']))
        checks.append(dict(row, actual=actual, absoluteError=error,
                           passed=error <= float(row['absoluteTolerance'])))
report = {'kind': 'real-nx12-analytical-validation', 'allPassed': all(c['passed'] for c in checks),
          'checks': checks, 'evidence': evidence,
          'note': 'Original run artifacts are preserved. Generic balances are re-parsed from actual MSGF; sink heat is called radiation only in the isolated radiation-only fixture.'}
out = ROOT / 'validation/nx-analytical-result.json'
out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'allPassed': report['allPassed'], 'checks': checks}, ensure_ascii=True, indent=2))
assert report['allPassed']
