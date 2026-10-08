"""Create a relocatable DSH Web deployment kit without user data or credentials."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

root = Path(__file__).resolve().parents[1]
dist = root / 'dist'
version = json.loads((root / 'plugin/package.json').read_text(encoding='utf-8'))['version']
kit_name = f'dsh-thermal-web-{version}'
kit = root / 'release' / kit_name
kit.mkdir(parents=True, exist_ok=True)
sources = {
    f'lbk-dsh-satellite-thermal-{version}.tgz': dist / f'lbk-dsh-satellite-thermal-{version}.tgz',
    'dsh_satellite_thermal_executor-0.1.0-py3-none-any.whl': dist / 'dsh_satellite_thermal_executor-0.1.0-py3-none-any.whl',
    f'dsh-satellite-thermal-{version}-source.zip': dist / f'dsh-satellite-thermal-{version}-source.zip',
    '先读我-安装与使用.md': root / 'docs/安装与使用.md',
    'compose.yaml': root / 'compose.yaml',
    '.env.example': root / '.env.example',
    'service/README.md': root / 'service/README.md',
    'service/Dockerfile': root / 'service/Dockerfile',
    'service/.dockerignore': root / 'service/.dockerignore',
    'service/requirements.txt': root / 'service/requirements.txt',
    'nx-worker/README.md': root / 'nx-worker/README.md',
    'docs/0.2.0-会话工作台.md': root / 'docs/0.2.0-会话工作台.md',
    'docs/开发目录迁移.md': root / 'docs/开发目录迁移.md',
    'docs/验证记录.md': root / 'docs/验证记录.md',
    'docs/新建项目卡顿定位与修复.md': root / 'docs/新建项目卡顿定位与修复.md',
}
for path in (root / 'packaging').glob('*.ps1'):
    sources['scripts/' + path.name] = path
for directory in ('service/thermal_service', 'service/vendor'):
    for path in (root / directory).rglob('*'):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
            sources[path.relative_to(root).as_posix()] = path
records = []
for relative, path in sorted(sources.items()):
    target = kit / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
    records.append({'path': relative, 'bytes': target.stat().st_size,
                    'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
manifest = {'name': kit_name, 'version': version, 'hostBaseline': 'DSH Web 0.2.0-rc.2',
            'nxBaseline': 'NX 12 Space Systems Thermal', 'offlineDependenciesIncluded': False,
            'userDataIncluded': False, 'files': records}
(kit / 'installation-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
archive_path = dist / (kit_name + '-installation.zip')
with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for relative in sorted([*sources, 'installation-manifest.json']):
        archive.write(kit / relative, kit_name + '/' + relative)
with zipfile.ZipFile(archive_path) as archive:
    assert archive.testzip() is None
    for record in records:
        assert hashlib.sha256(archive.read(kit_name + '/' + record['path'])).hexdigest() == record['sha256']
release_file = dist / 'release-manifest.json'
release = json.loads(release_file.read_text(encoding='utf-8'))
release['artifacts'] = [entry for entry in release['artifacts'] if entry['name'] != archive_path.name]
release['artifacts'].append({'name': archive_path.name, 'bytes': archive_path.stat().st_size,
                             'sha256': hashlib.sha256(archive_path.read_bytes()).hexdigest()})
release_file.write_text(json.dumps(release, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'installationZip': str(archive_path), 'readyDirectory': str(kit),
                  'fileCount': len(records) + 1, 'bytes': archive_path.stat().st_size,
                  'verified': True}, ensure_ascii=True))
