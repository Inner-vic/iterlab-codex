"""Audit/export Git-eligible source without opening the research store or CLI.

Uses an empty temporary Git repository, so ignored files already tracked in an
existing checkout cannot leak into the bundle. No git add/commit/push is run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = {'.data', '.evidence', '.runtime', '.local', '.git', '.t', '.venv', 'node_modules', '__pycache__'}
REQUIRED = ['app.cmd', '.gitignore', '.gitattributes', 'README.md', 'LICENSE', 'NOTICE.md',
            'lab/web.py', 'static/index.html', 'static/vendor/exploration-map.js',
            'frontend/exploration-map/package-lock.json', 'methods/research/contract.md',
            'skills/sdm-research/source-manifest.json', 'docs/capability-consolidation-20261028.md',
            'scripts/start.ps1', 'scripts/open-frontend.ps1', 'scripts/run-quiet.ps1',
            'scripts/lpai_sdk_bridge.py', 'scripts/lpai_asset_bridge.py',
            'scripts/lpai_task_files.py', 'scripts/probe_cloud_outputs.py']
REQUIRED += ['lab/research_modules.py','docs/builtin-research-modules.md',
             *['methods/research/modules/'+name+'.md' for name in ['data','design','training','engineering','evidence']]]
REQUIRED += ['scripts/bootstrap.py','scripts/dependencies.ps1','scripts/setup.ps1','docs/dependency-setup.md']
REQUIRED += ['scripts/source_bundle.py','docs/core-handover.md']
REQUIRED += ['lab/study_integrity.py','methods/research/sdm-inheritance.json','docs/sdm-inheritance.md','scripts/study_demo.py']
REQUIRED += ['scripts/check_docs.py','docs/README.md','docs/repository-audit.md']
REQUIRED += ['docs/local-database.md','docs/preservation-status.md']
REQUIRED += ['scripts/platform_login.py','lab/platform_login.py','static/platform-login.js','docs/first-platform-login.md']
REQUIRED += ['lab/preparation_checks.py','lab/host_preparation.py']
REQUIRED += ['lab/approval_frontier.py']
REQUIRED += ['lab/work_package.py']
SECRET = re.compile(r'''(?i)(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)["']?\s*[:=]\s*["']([A-Za-z0-9_./+=-]{24,})["']''')
PRIVATE_KEY = re.compile(r'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----')


def source_files(root=ROOT):
    git = shutil.which('git')
    if not git:
        raise ValueError('Git is required to evaluate .gitignore; no export was created.')
    scratch = root / '.evidence/source-bundle'
    scratch.mkdir(parents=True, exist_ok=True)
    temporary = scratch / uuid.uuid4().hex
    temporary.mkdir(mode=0o777)
    flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    try:
        def run(*args):
            result = subprocess.run([git, *args], cwd=root, capture_output=True, timeout=45, **flags)
            if result.returncode:
                raise ValueError('Local Git source enumeration failed; no export was created.')
            return result.stdout
        run('init', '--bare', '--quiet', str(temporary))
        data = run('--git-dir=' + str(temporary), '--work-tree=' + str(root),
                   '-c', 'core.excludesFile=' + os.devnull, 'ls-files',
                   '--others', '--exclude-standard', '-z')
        return sorted(item.decode('utf-8') for item in data.split(b'\0') if item)
    finally:
        if temporary.resolve().parent != scratch.resolve() or temporary.is_symlink():
            raise ValueError('Refusing cleanup outside the owned audit directory.')
        shutil.rmtree(temporary)


def inspect(root, names):
    problems = []
    manifest = []
    for name in names:
        path = root / name
        parts = Path(name).parts
        if any(part in PRIVATE for part in parts) or path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            problems.append({'file': name, 'reason': 'private directory or link'})
            continue
        if not path.resolve().is_relative_to(root.resolve()) or not path.is_file():
            problems.append({'file': name, 'reason': 'not a regular source file'})
            continue
        content = path.read_bytes()
        if name in REQUIRED and not content.strip():
            problems.append({'file': name, 'reason': 'required distributable file is empty'})
        if path.suffix.lower() in {'.py', '.js', '.mjs', '.json', '.md', '.ps1', '.cmd', '.txt', '.toml', '.yaml', '.yml'}:
            text = content.decode('utf-8-sig', errors='replace')
            if SECRET.search(text) or PRIVATE_KEY.search(text):
                # Report the filename only, never the suspected credential.
                problems.append({'file': name, 'reason': 'possible embedded credential; review before sharing'})
        manifest.append({'path': name, 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()})
    for name in REQUIRED:
        if name not in names:
            problems.append({'file': name, 'reason': 'required distributable file missing or ignored'})
    return manifest, problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Create a new ZIP under .evidence/distribution; never overwrite.')
    args = parser.parse_args()
    names = source_files()
    manifest, problems = inspect(ROOT, names)
    report = ROOT / '.evidence/source-bundle/latest.json'
    report.write_text(json.dumps({'files': manifest, 'problems': problems}, ensure_ascii=False, indent=2), encoding='utf-8')
    if problems:
        print(json.dumps({'status': 'blocked', 'problems': problems, 'report': str(report)}, ensure_ascii=False))
        return 1
    output = None
    if args.output:
        output = (ROOT / args.output).resolve() if not args.output.is_absolute() else args.output.resolve()
        allowed = (ROOT / '.evidence/distribution').resolve()
        if output.parent != allowed or output.suffix.lower() != '.zip':
            raise ValueError('Bundle output must be a .zip directly under .evidence/distribution.')
        allowed.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, mode='x', compression=zipfile.ZIP_DEFLATED) as archive:
            for item in manifest:
                path = ROOT / item['path']
                content = path.read_bytes()
                if path.is_symlink() or not path.resolve().is_relative_to(ROOT) or hashlib.sha256(content).hexdigest() != item['sha256']:
                    raise ValueError('Source changed during export; do not distribute this incomplete archive.')
                archive.writestr(item['path'], content)
    print(json.dumps({'status': 'passed', 'files': len(manifest), 'bytes': sum(item['bytes'] for item in manifest),
                      'report': str(report), 'bundle': str(output) if output else None,
                      'scope': 'Source allowlist and common credential checks; not a full security audit.'}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
