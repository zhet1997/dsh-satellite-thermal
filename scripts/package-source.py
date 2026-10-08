"""Package only repository source and public verification records, never runtime data."""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import zipfile

root = Path(__file__).resolve().parents[1]
dist = root / 'dist'
dist.mkdir(exist_ok=True)
version = json.loads((root / 'plugin/package.json').read_text(encoding='utf-8'))['version']
plugin = dist / f'lbk-dsh-satellite-thermal-{version}.tgz'
data = plugin.read_bytes()
with tarfile.open(plugin, 'r:gz') as archive:
    entries = [{'path': p.name.removeprefix('package/'), 'size': p.size} for p in archive if p.isfile()]
inventory = [{'id': f'@lbk/dsh-satellite-thermal@{version}', 'name': '@lbk/dsh-satellite-thermal',
              'version': version, 'filename': plugin.name, 'size': len(data),
              'unpackedSize': sum(p['size'] for p in entries), 'files': entries,
              'shasum': hashlib.sha1(data).hexdigest(), 'sha256': hashlib.sha256(data).hexdigest(),
              'integrity': 'sha512-' + base64.b64encode(hashlib.sha512(data).digest()).decode()}]
(root / 'validation/npm-pack.json').write_text(json.dumps(inventory, indent=2) + '\n', encoding='utf-8')
source_revision = None
if (root / '.git').exists():
    # A release must not absorb unrelated in-progress edits from another task.
    source_revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root).decode().strip()
    committed_version = json.loads(subprocess.check_output(['git', 'show', f'{source_revision}:plugin/package.json'], cwd=root))['version']
    assert committed_version == version, 'Commit the release sources before packaging'
    listed = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', '-z', source_revision], cwd=root)
    names = sorted(set(n.decode('utf-8') for n in listed.split(b'\0') if n))
else:
    # An unpacked source release is buildable without an initialized Git repo.
    names = []
    excluded_dirs = {'node_modules', 'runtime', '.git', 'dsh-home', '__pycache__', '.pytest_cache', 'dist', 'build', 'release'}
    for directory, children, files in os.walk(root):
        relative = Path(directory).relative_to(root).as_posix()
        children[:] = [n for n in children if n not in excluded_dirs and not n.endswith('.egg-info')
                       and not (relative == 'validation' and n in {'artifacts', 'profile'})
                       and not (relative == 'plugin' and n in {'public', 'lib'})
                       and not (relative == 'service' and n == 'data')]
        names.extend((Path(directory) / name).relative_to(root).as_posix() for name in files
                     if name != '.env' and not name.endswith(('.pyc', '.tsbuildinfo', '-config.txt')))
    names.sort()
source = dist / f'dsh-satellite-thermal-{version}-source.zip'
with zipfile.ZipFile(source, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for name in names:
        path = root / name
        if not path.is_file():
            continue
        assert path.resolve().is_relative_to(root), 'Source file resolves outside repository'
        assert not any(part in {'node_modules', 'runtime', '.git', 'dsh-home', '__pycache__', 'dist'} for part in path.relative_to(root).parts)
        assert name != '.env'
        if source_revision:
            archive.writestr('dsh-satellite-thermal/' + name,
                subprocess.check_output(['git', 'show', f'{source_revision}:{name}'], cwd=root))
        else:
            archive.write(path, 'dsh-satellite-thermal/' + name)
artifacts = [plugin, source] + sorted(dist.glob('dsh_satellite_thermal_executor-0.1.0-*.whl'))
manifest = {'version': version, 'sourceRevision': source_revision, 'hostBaseline': 'DSH Web 0.2.0-rc.2', 'nxBaseline': 'NX 12 Space Systems Thermal',
            'browserVisualReview': 'blocked by saved site permission; not performed',
            'artifacts': [{'name': p.name, 'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in artifacts]}
(dist / 'release-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
print(json.dumps(manifest, indent=2))
