"""Windows dependency setup. No login, model calls, database access or cloud writes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
LPAI_HUB = 'https://lpai.lixiang.com/lpai/api/agent-hub/v1/artifacts/lpai-skill'
LPAI_INDEX = 'https://artifactory.ep.chehejia.com/artifactory/api/pypi/liauto-pypi-l5/simple'
SDK_VERSIONS = {'lpai': '1.0.1', 'lpai-asset': '3.0.23'}


def run(argv, timeout=30, env=None):
    """Capture diagnostics privately: installers/official CLIs may print credentials."""
    try:
        result = subprocess.run([str(a) for a in argv], capture_output=True, timeout=timeout,
                                env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return result.returncode, result.stdout.decode('utf-8-sig', errors='replace')
    except subprocess.TimeoutExpired:
        return 124, ''
    except OSError:
        return 127, ''


def download(url, target):
    # Only the vendor endpoint observed in its own official install.sh. No cookies,
    # bearer token, shell script execution or cross-host redirects.
    class VendorRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            from urllib.parse import urlsplit
            parsed = urlsplit(newurl)
            if parsed.scheme != 'https' or parsed.hostname != 'lpai.lixiang.com':
                raise ValueError('unexpected download redirect')
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    opener = urllib.request.build_opener(VendorRedirect())
    started = time.monotonic()
    size = 0
    with opener.open(url, timeout=30) as response, target.open('xb') as stream:
        while chunk := response.read(256 * 1024):
            size += len(chunk)
            if size > 160 * 1024 * 1024 or time.monotonic() - started > 180:
                raise ValueError('download limit')
            stream.write(chunk)


class Bootstrap:
    def __init__(self, root=ROOT, *, check_only=False, skip_lpai=False, runner=run,
                 which=shutil.which, downloader=download):
        self.root = Path(root)
        self.check_only, self.skip_lpai = check_only, skip_lpai
        self.run, self.which, self.download = runner, which, downloader
        self.entries, self.paths = [], []

    def add(self, name, state, message, **details):
        self.entries.append(dict(name=name, state=state, message=message, **details))

    def known_tool(self, name):
        found = self.which(name)
        if found:
            if name=='codex' and Path(found).suffix.lower() in {'.cmd','.bat','.ps1'}:
                base=Path(found).parent/'node_modules/@openai'
                for native in sorted(base.glob('codex*/**/codex.exe')):
                    if native.is_file() and not native.is_symlink():
                        self.paths.append(str(native.parent))
                        return str(native)
            return str(Path(found).resolve())
        # Per-user installation roots; do not search other users' homes.
        roots = [self.root / '.runtime/bin']
        local = os.environ.get('LOCALAPPDATA')
        if local:
            roots += [Path(local) / 'bin']
        if name == 'ept':
            roots += [Path.home() / '.ept/bin']
        for folder in roots:
            path = folder / (name + '.exe')
            if path.is_file():
                self.paths.append(str(folder))
                return str(path)
        if name == 'codex':
            base = self.root / '.runtime/codex/node_modules/@openai'
            candidates = sorted(base.glob('codex*/**/codex.exe')) if base.exists() else []
            for path in candidates:
                if path.is_file() and not path.is_symlink():
                    self.paths.append(str(path.parent))
                    return str(path)
        return None

    def probe(self, name, path):
        args = [path, '--help' if name == 'lpai-agent' else 'version' if name == 'ept' else '--version']
        code, output = self.run(args, timeout=30)
        # No arbitrary version/help text is persisted (may include login diagnostics).
        valid = code == 0 and (('lpai-agent' in output and 'devspaces' in output)
                                  if name == 'lpai-agent' else bool(output.strip()))
        return valid, code

    def model_cli(self):
        # Only inspect the selected channel, never change it or copy its credentials.
        data = Path(os.environ.get('ITERLAB_CODEX_DATA') or self.root / '.data')
        channel = None
        if (data / 'settings.json').is_file():
            try:
                channel = json.loads((data / 'settings.json').read_text(encoding='utf-8'))['channel']
            except (ValueError, KeyError):
                self.add('model_cli', 'failed', '模型连接配置无法核对，未改写或更换渠道。')
                return
        if channel is None:
            channel = 'ept' if self.known_tool('ept') else 'codex'
        if channel not in {'codex', 'ept'}:
            self.add('model_cli', 'failed', '未知模型渠道，未切换。')
            return
        path = self.known_tool(channel)
        if not path and not self.check_only and channel == 'codex':
            self.install_codex()
            path = self.known_tool(channel)
        if not path:
            self.add(channel, 'missing', '请通过公司官方入口安装 EPT CLI；不会替换为个人 Codex。'
                     if channel == 'ept' else 'Codex CLI 缺失；可安装 Node.js LTS 后重新运行安装器。')
            return
        valid, code = self.probe(channel, path)
        self.add(channel, 'ready' if valid else 'failed',
                 '命令可运行；登录和模型权限在界面中另外检查。' if valid else '已有 CLI 探测失败，未覆盖安装或更换渠道。',
                 path=path, exit_code=code, login_checked=False)

    def install_codex(self):
        node = self.which('node')
        if not node:
            winget = self.which('winget')
            if not winget:
                return
            code, _ = self.run([winget, 'install', '--id', 'OpenJS.NodeJS.LTS', '--exact',
                                '--source', 'winget', '--silent', '--accept-package-agreements',
                                '--accept-source-agreements', '--disable-interactivity'], timeout=600)
            if code:
                self.add('node', 'failed', 'Node.js 官方安装未完成；请检查系统安装权限或网络。', exit_code=code)
                return
            # Newly installed PATH is not inherited by this process.
            for base in [os.environ.get('ProgramFiles', ''), os.environ.get('LOCALAPPDATA', '')]:
                candidate = Path(base) / 'nodejs/node.exe'
                if base and candidate.is_file():
                    node = str(candidate)
                    break
        if not node:
            return
        npm = Path(node).parent / 'node_modules/npm/bin/npm-cli.js'
        if not npm.is_file():
            self.add('node', 'failed', 'Node.js 已找到，但 npm 缺失；未运行未知安装脚本。')
            return
        env = dict(os.environ, PATH=str(Path(node).parent) + os.pathsep + os.environ.get('PATH', ''))
        code, _ = self.run([node, npm, 'install', '--prefix', self.root / '.runtime/codex',
                            '@openai/codex@latest', '--registry=https://registry.npmjs.org',
                            '--no-audit', '--no-fund'], timeout=600, env=env)
        if code:
            self.add('codex_install', 'failed', '官方 Codex npm 包安装未完成；保留已有环境。', exit_code=code)

    def lpai_cli(self):
        path = self.known_tool('lpai-agent')
        if not path and not self.check_only:
            arch = {'amd64': 'amd64', 'x86_64': 'amd64', 'arm64': 'arm64', 'aarch64': 'arm64'}.get(platform.machine().lower())
            if not arch:
                self.add('lpai-agent', 'failed', '当前 CPU 架构未获官方安装器支持。')
                return
            url = f'{LPAI_HUB}/latest/cli/windows/{arch}'
            folder = self.root / '.runtime/bin'
            folder.mkdir(parents=True, exist_ok=True)
            temp = folder / (uuid.uuid4().hex + '.exe')
            target = folder / 'lpai-agent.exe'
            try:
                self.download(url, temp)
                with temp.open('rb') as stream:
                    if stream.read(2) != b'MZ':
                        raise ValueError('not a Windows executable')
                valid, code = self.probe('lpai-agent', str(temp))
                if not valid:
                    raise ValueError('downloaded CLI failed verification')
                if target.exists():
                    raise ValueError('another installation already exists')
                # A local digest records the observed bytes; it is not a vendor signature.
                with temp.open('rb') as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                temp.rename(target)
                self.add('lpai-agent_download', 'ready', '已从官方制品入口安装独立 CLI。', source=url, sha256=digest)
                path = str(target)
                self.paths.append(str(folder))
            except Exception:
                self.add('lpai-agent_install', 'failed', '官方 CLI 下载或验证未完成，请检查公司网络。未修改认证配置。')
            finally:
                temp.unlink(missing_ok=True)
        if not path:
            self.add('lpai-agent', 'missing', '缺少官方 LPAI Agent CLI。')
            return
        valid, code = self.probe('lpai-agent', path)
        self.add('lpai-agent', 'ready' if valid else 'failed',
                 '官方命令可用；账号、权限和飞书审批另行验证。' if valid else '已有官方命令探测失败，未覆盖或重装。',
                 path=path, exit_code=code, login_checked=False)

    def sdk_state(self, python):
        if not python.is_file():
            return None
        code = '''import importlib.metadata as m,json
result={}
for name in ("lpai","lpai-asset"):
 try: result[name]=m.version(name)
 except m.PackageNotFoundError: result[name]=None
print(json.dumps(result))'''
        rc, out = self.run([python, '-I', '-c', code], timeout=30)
        if rc:
            return {'_probe': 'failed'}
        try:
            return json.loads(out)
        except ValueError:
            return {'_probe': 'failed'}

    def sdk_sources(self, python):
        # Hash reviewed source anchors without importing/configuring either SDK.
        code = '''import sys,sysconfig,hashlib,json
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from lab.cloud_evidence import SDK_SOURCES
base=Path(sysconfig.get_paths()["purelib"])
print(json.dumps(all((base/n).is_file() and not (base/n).is_symlink() and hashlib.sha256((base/n).read_bytes()).hexdigest()==h for n,h in SDK_SOURCES.items())))'''
        rc, out = self.run([python, '-I', '-c', code, self.root], timeout=30)
        return rc == 0 and out.strip() == 'true'

    def lpai_sdk(self):
        python = self.root / '.runtime/lpai/Scripts/python.exe'
        state = self.sdk_state(python)
        if state == SDK_VERSIONS:
            valid = self.sdk_sources(python)
            self.add('lpai_sdk', 'ready' if valid else 'failed',
                     '训练与产物 SDK 版本及已审核源码锚点符合要求。' if valid else 'SDK 源码锚点与当前适配器不符，未覆盖或放宽校验。',
                     versions=state, login_checked=False)
            return
        # Do not upgrade incompatible installed versions in place.
        if state and any(value is not None and SDK_VERSIONS.get(name) != value for name, value in state.items()):
            self.add('lpai_sdk', 'failed', '已有 SDK 版本不匹配，需明确迁移；未自动升级。', versions=state)
            return
        if self.check_only:
            self.add('lpai_sdk', 'missing', '独立环境缺少训练或产物 SDK。')
            return
        if state is None:
            rc, _ = self.run([sys.executable, '-m', 'venv', python.parent.parent], timeout=120)
            if rc:
                self.add('lpai_sdk', 'failed', '无法创建独立 Python 环境。', exit_code=rc)
                return
        rc, _ = self.run([python, '-m', 'pip', '--isolated', 'install',
                          *[f'{name}=={version}' for name, version in SDK_VERSIONS.items()],
                          '--index-url', LPAI_INDEX, '--disable-pip-version-check', '--no-input',
                          '--retries', '1', '--timeout', '30'], timeout=600)
        state = self.sdk_state(python)
        valid = rc == 0 and state == SDK_VERSIONS and self.sdk_sources(python)
        self.add('lpai_sdk', 'ready' if valid else 'failed',
                 '独立 SDK 安装和版本检查通过。' if valid else 'SDK 安装未完成；请检查公司网络及软件仓库访问权限。',
                 exit_code=rc, versions=state, login_checked=False)

    def execute(self):
        self.add('python', 'ready', 'Python 版本符合要求。', version=platform.python_version(), path=sys.executable)
        self.model_cli()
        if not self.skip_lpai:
            self.lpai_cli()
            self.lpai_sdk()
        return {'schema': 'iterlab-dependencies/v1', 'check_only': self.check_only,
                'complete': all(item['state'] == 'ready' for item in self.entries),
                'entries': self.entries, 'paths': list(dict.fromkeys(self.paths)),
                'authentication': 'not_checked', 'model_calls': 0, 'cloud_writes': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--skip-lpai', action='store_true')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    bootstrap = Bootstrap(check_only=args.check_only, skip_lpai=args.skip_lpai)
    result = bootstrap.execute()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    temp = args.report.with_suffix('.tmp')
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(args.report)
    for item in result['entries']:
        print(f"[{item['state']}] {item['name']}: {item['message']}")
    return 0 if result['complete'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
