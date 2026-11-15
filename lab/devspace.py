"""Transport for an explicitly bound, already provisioned LPAI GPU space."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tarfile


HOST = 'ssh-d.lpai.lixiang.com'
WORKER = Path(__file__).with_name('devspace_worker.py')
PROTOCOL_LIMIT = 46 * 1024 * 1024
MAX_INPUT_BYTES = 2 * 1024 * 1024 * 1024
INHERIT_IDENTITY=object()
RESERVED = {'.iterlab-job.json', '.iterlab-receipt.json', '.iterlab-receipt.tmp',
            '.iterlab-input.tar', '.iterlab-response.json', 'receipt.json',
            'input-manifest.json', 'stdout.log', 'stderr.log', '.iterlab-supervisor.py',
            '.iterlab-launch.json', '.iterlab-executing', '.iterlab-supervisor.log'}


def compute(plan):
    proposal = json.loads(plan['source']).get('scope_proposal') or {}
    value = proposal.get('compute')
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {'gpu_count', 'workflow'} or (
        type(value['gpu_count']) is not int or not 0 <= value['gpu_count'] <= 128
        or value['workflow'] not in ('interactive', 'batch')
    ):
        raise ValueError('计算建议需明确 GPU 数量和交互/批量用途')
    return value


def binding(store, project_id):
    if not store.one("SELECT name FROM sqlite_master WHERE name='company_bindings'"):
        return None
    row = store.one("SELECT * FROM company_bindings WHERE project_id=? AND kind='lpai'", (project_id,))
    return row


def ssh_profile(store, project_id, bound):
    if not bound:
        return None
    profile=store.one(
        'SELECT port,verified_at FROM company_space_ssh WHERE project_id=? AND scope=? AND uid=?',
        (project_id, bound['scope'], bound['uid']))
    draft=store.one('SELECT port,state FROM company_space_ssh_checks WHERE project_id=? AND scope=? AND uid=?',
                    (project_id,bound['scope'],bound['uid']))
    if draft and (not profile or draft['port']!=profile['port'] or draft['state']!='verified'):return None
    return profile


def ssh_settings(store,project_id,bound):
    if not bound:return None
    saved=store.one('SELECT port,state,gpu_count,verified_at,message FROM company_space_ssh_checks WHERE project_id=? AND scope=? AND uid=?',
                    (project_id,bound['scope'],bound['uid']))
    if saved:return saved
    profile=ssh_profile(store,project_id,bound)
    if profile:return profile
    endpoint=json.loads(bound.get('observation','{}')).get('ssh')
    if endpoint and endpoint.get('host')==HOST and endpoint.get('user')=='root' and type(endpoint.get('port')) is int:
        return {'port':endpoint['port'],'state':'saved','gpu_count':None,'verified_at':None,'message':'已识别官方SSH端口，尚未验证连接。'}
    return None


def command_port(command):
    """Parse the official endpoint, never execute pasted shell/options."""
    try:parts=shlex.split(command.strip()) if isinstance(command,str) else []
    except ValueError:parts=[]
    if not parts or parts.pop(0)!='ssh':raise ValueError('请粘贴 LPAI 页面提供的 SSH 连接命令')
    port=None;peer=None
    while parts:
        part=parts.pop(0)
        if part=='-p' and parts and port is None:
            value=parts.pop(0)
            if not value.isdigit() or not 1<=int(value)<=65535:raise ValueError('SSH 端口无效')
            port=int(value)
        elif part=='-T':continue
        elif part=='-t' and parts==['bash']:
            # The official UI opens an interactive bash. Only recognize this
            # literal tail; training still uses our own noninteractive command.
            parts.pop(0)
        elif part=='-o' and parts:
            option=parts.pop(0)
            # Official commands sometimes disable known-host checks. Accept the
            # endpoint but keep the application's strict host-key policy.
            if not re.fullmatch(r'(?:StrictHostKeyChecking[= ](?:yes|no|accept-new)|UserKnownHostsFile[= ](?:/dev/null|NUL))',option):
                raise ValueError('连接命令含不支持的 SSH 选项；请仅保留官方端口与主机')
        elif part=='root@'+HOST and peer is None:peer=part
        else:raise ValueError('仅接受官方 SSH 端口与 root@'+HOST+'；不会执行粘贴的附加命令')
    if port is None or peer is None:raise ValueError('请粘贴 ssh -p 端口 root@'+HOST)
    return port


def configure(company, session_id, space_uid, command, identity_path=None):
    from .store import now
    store = company.store
    project_id = store.session(session_id)['project_id']
    bound = binding(store, project_id)
    if not bound or bound['uid'] != space_uid:
        raise ValueError('开发空间绑定已改变，请刷新项目页')
    profile = ssh_profile(store, project_id, bound)
    settings=ssh_settings(store,project_id,bound)
    selected=INHERIT_IDENTITY
    if identity_path is not None:
        if not isinstance(identity_path,str) or len(identity_path)>700:raise ValueError('SSH密钥需为服务电脑上的文件路径，不接受密钥内容')
        selected=identity_path.strip()
        if selected:
            path=Path(selected).expanduser()
            if not path.is_absolute() or not path.is_file() or '\n' in selected or '\r' in selected:
                raise ValueError('SSH密钥文件不存在；请填服务电脑上的既有文件路径，不粘贴私钥')
            selected=str(path.resolve())
    if command is None:
        if not settings:
            raise ValueError('请先粘贴 LPAI 官方 SSH 连接命令')
        port = settings['port']
    else:
        port = command_port(command)
    scope = observe(company, project_id, space_uid)
    phase=scope.pop('phase').lower()
    scope['port'] = port
    if phase not in {'ready','running'}:
        store.execute('INSERT INTO company_space_ssh_checks VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(project_id,scope,uid) DO UPDATE SET port=excluded.port,state=excluded.state,gpu_count=NULL,verified_at=NULL,message=excluded.message',
            (project_id,bound['scope'],space_uid,port,'saved',None,None,'连接信息已保存；空间尚未就绪，就绪后验证，不启动训练。'))
        return ssh_settings(store,project_id,bound)
    try:
        args=connection(company,project_id,scope,trust_new=True,identity=selected)
        initialize_marker(company,project_id,scope,args)
        gpu_count=check_peer(args, scope)
    except ValueError as exc:
        # Preserve a previously verified endpoint on a failed edit. A first
        # unverified endpoint can be saved, but is never an execution profile.
        if not profile:
            store.execute('INSERT INTO company_space_ssh_checks VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(project_id,scope,uid) DO UPDATE SET port=excluded.port,state=excluded.state,gpu_count=NULL,verified_at=NULL,message=excluded.message',
                (project_id,bound['scope'],space_uid,port,'failed',None,None,str(exc)))
        raise
    with store.lock:
        current = binding(store, project_id)
        if not current or (current['uid'], current['scope']) != (space_uid, bound['scope']):
            raise ValueError('验证期间开发空间绑定已改变，请重新检查')
        if ssh_profile(store, project_id, current) != profile:
            raise ValueError('验证期间 SSH 配置已改变，请重新检查')
        store.execute(
            'INSERT INTO company_space_ssh VALUES(?,?,?,?,?) ON CONFLICT(project_id,scope,uid) '
            'DO UPDATE SET port=excluded.port,verified_at=excluded.verified_at',
            (project_id, bound['scope'], space_uid, port, now()))
        store.execute('INSERT INTO company_space_ssh_checks VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(project_id,scope,uid) DO UPDATE SET port=excluded.port,state=excluded.state,gpu_count=excluded.gpu_count,verified_at=excluded.verified_at,message=excluded.message',
            (project_id,bound['scope'],space_uid,port,'verified',gpu_count,now(),'SSH 对端身份已核对；提交前重新验证，未启动训练。'))
        if selected is not INHERIT_IDENTITY:
            store.execute('INSERT INTO company_space_ssh_identities VALUES(?,?,?,?) ON CONFLICT(project_id,scope,uid) DO UPDATE SET identity_path=excluded.identity_path',
                (project_id,bound['scope'],space_uid,selected))
        return ssh_settings(store, project_id, current)


def observe(company, project_id, space_uid):
    bound = binding(company.store, project_id)
    if not bound or bound['uid'] != space_uid:
        raise ValueError('请先绑定本项目指定的 LPAI 开发空间；不能切换到其他执行地点')
    item = company.lpai('space', name=bound['name'])['item']
    if item['uid'] != space_uid or item['namespace'] != bound['scope']:
        raise ValueError('开发空间身份已变化，需重新核对和授权')
    gpus = item['resources'].get('gpu')
    if type(gpus) not in {int, str} or not str(gpus).isdigit() or not 0 <= int(gpus) <= 128:gpus=None
    return {'uid': space_uid, 'name': bound['name'], 'namespace': bound['scope'],
            'gpus': int(gpus) if gpus is not None else None, 'phase': item['phase']}


def approval(company, project_id, space_uid, port, required_gpus):
    bound = binding(company.store, project_id)
    profile = ssh_profile(company.store, project_id, bound)
    if not profile:
        raise ValueError('请在项目的执行页配置并验证开发空间 SSH')
    if port is not None and port != profile['port']:
        raise ValueError('SSH 端口以项目中已验证的配置为准')
    port = profile['port']
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('请填写该开发空间官方连接页面显示的 SSH 端口')
    item = observe(company, project_id, space_uid)
    if item['gpus'] is None:
        measured=ssh_settings(company.store,project_id,bound)
        if not measured or measured.get('state')!='verified' or not isinstance(measured.get('gpu_count'),int):
            raise ValueError('平台GPU配额未知；请先验证该空间SSH并明确本次GPU使用上限')
        if type(required_gpus) is not int or not 0<=required_gpus<=measured['gpu_count']:
            raise ValueError('平台GPU配额未知，方案必须明确不超过已验证可见设备的GPU使用上限；可见数不是配额授权')
        item['gpus']=required_gpus
        item['gpu_source']='verified_ssh_visibility_with_explicit_plan_limit'
    elif required_gpus is not None:
        if required_gpus>item['gpus']:raise ValueError('开发空间 GPU 配额少于方案要求')
        item['platform_gpus']=item['gpus'];item['gpus']=required_gpus
    if required_gpus and item['gpus'] < required_gpus:
        raise ValueError('开发空间 GPU 配额与方案要求不符')
    item.pop('phase')
    return {**item, 'port': port}


def connection(company, project_id, approved,trust_new=False,identity=INHERIT_IDENTITY):
    current = observe(company, project_id, approved['uid'])
    if current.pop('phase').lower() not in {'ready','running'}:
        raise ValueError('已批准的开发空间尚未运行；等该空间就绪后重试')
    if any(current[key] != approved[key] for key in current if key!='gpus') or current['gpus'] is not None and current['gpus']<approved['gpus']:
        raise ValueError('开发空间资源或身份改变，需重新授权')
    bound=binding(company.store,project_id)
    endpoint=json.loads(bound['observation']).get('ssh',{})
    if endpoint and endpoint.get('port')!=approved['port']:
        raise ValueError('官方SSH端口已变化，请重新保存并验证该空间连接；未沿旧端口提交')
    first_trust=trust_new and endpoint=={'host':HOST,'user':'root','port':approved['port'],'source':'official_devspace_access'}
    executable = shutil.which('ssh')
    if not executable or Path(executable).suffix.lower() in {'.bat', '.cmd'}:
        raise ValueError('IterLab 宿主缺少可用 OpenSSH 客户端')
    args = [executable, '-T', '-F', 'none', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking='+('accept-new' if first_trust else 'yes'),
            '-o', 'ConnectTimeout=10', '-o', 'ClearAllForwardings=yes', '-p', str(approved['port'])]
    if identity is INHERIT_IDENTITY:
        saved=company.store.one('SELECT identity_path FROM company_space_ssh_identities WHERE project_id=? AND scope=? AND uid=?',
            (project_id,bound['scope'],bound['uid']))
        identity=saved['identity_path'] if saved else os.environ.get('ITERLAB_LPAI_SSH_IDENTITY')
    if identity:
        args += ['-o', 'IdentitiesOnly=yes', '-i', identity]
    return [*args, 'root@' + HOST]


def initialize_marker(company,project_id,approved,args):
    """Only an official UID→SSH endpoint mapping may initialize the marker."""
    bound=binding(company.store,project_id)
    if not bound or bound['uid']!=approved['uid']:raise ValueError('SSH验证期间空间已换绑')
    endpoint=json.loads(bound['observation']).get('ssh',{})
    if endpoint!={'host':HOST,'user':'root','port':approved['port'],'source':'official_devspace_access'}:
        return  # Legacy spaces keep the explicit web-console marker contract.
    body=('from pathlib import Path\nimport os\n'
          'p=Path("/lpai/.iterlab-space-uid")\n'
          'if p.is_symlink(): raise RuntimeError("Invalid identity marker")\n'
          f'value={approved["uid"]!r}+"\\n"\n'
          'try:\n f=p.open("x",encoding="utf8");f.write(value);f.close()\n'
          'except FileExistsError:\n'
          ' if p.read_text(encoding="utf8").strip()!=value.strip(): raise RuntimeError("Identity marker differs")\n')
    try:reply=subprocess.run([*args,shlex.join(['python3','-c',body])],stdin=subprocess.DEVNULL,capture_output=True,timeout=15,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    except (OSError,subprocess.TimeoutExpired) as exc:raise ValueError('官方SSH端点身份初始化未确认；未上传训练代码、未启动训练') from exc
    if reply.returncode:raise ValueError(ssh_error(reply))


def ssh_error(reply):
    text=reply.stderr.decode('utf8',errors='replace') if isinstance(getattr(reply,'stderr',None),bytes) else str(getattr(reply,'stderr',''))
    if 'Host key verification failed' in text or 'REMOTE HOST IDENTIFICATION HAS CHANGED' in text:
        return 'SSH主机指纹尚未信任或已变化；请在服务电脑核对官方网关指纹后首次连接，系统不会关闭主机校验。未上传训练代码、未启动训练。'
    if 'Permission denied' in text:
        return 'SSH密钥认证未通过；请核对空间绑定公钥与服务电脑已有密钥/SSH代理，非默认密钥可配置ITERLAB_LPAI_SSH_IDENTITY。未上传训练代码、未启动训练。'
    if 'Connection refused' in text:
        return '该空间SSH端口暂未就绪或官方端口已变更；请核对空间连接页后重试。未上传训练代码、未启动训练。'
    return '官方SSH连接或空间身份核对失败；未覆盖既有身份文件、未上传训练代码、未启动训练。'


def check_peer(args, approved):
    # Some gateway SSH keys identify the gateway, not the individual space.
    # Check a marker placed from that space's official web console before sending bytes.
    command = 'test -w /lpai && python3 -c "import sys; assert sys.version_info >= (3, 9)" && cat /lpai/.iterlab-space-uid'
    if approved['gpus'] != 0:
        command += ' && nvidia-smi -L'
    try:
        reply = subprocess.run([*args, command], stdin=subprocess.DEVNULL, capture_output=True,
                               timeout=15, creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError('开发空间 SSH 预检未通过；未上传输入，未启动训练') from exc
    lines = reply.stdout.splitlines()
    if reply.returncode:raise ValueError(ssh_error(reply))
    if reply.returncode or not lines or lines[0].strip() != approved['uid'].encode('ascii'):
        raise ValueError('SSH 未确认是已绑定空间：核对官方端口、主机密钥、GPU、Python 及空间身份文件；未上传输入，未启动训练')
    count=sum(line.startswith(b'GPU ') for line in lines[1:])
    if approved['gpus'] is not None and count < approved['gpus']:
        raise ValueError('SSH 可见 GPU 数少于已批准的本次需要；未上传输入，未启动训练')
    return count


class ComputePreflightError(ValueError):
    def __init__(self,message,report):
        super().__init__(message)
        self.report=report


def check_compute(args, approved):
    """Exact allocated devices only; runs before recording a training attempt."""
    if not approved['gpus']:
        return {'ready':True,'requested_gpus':0,'fitting':False,'devices':[]}
    source=Path(__file__).with_name('gpu_preflight.py').read_text(encoding='utf-8')
    command=shlex.join(['python3','-c',source,str(approved['gpus'])])
    try:
        response=subprocess.run([*args,command],stdin=subprocess.DEVNULL,capture_output=True,timeout=45,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        if len(response.stdout)>65536:raise ValueError('oversized preflight receipt')
        value=json.loads(response.stdout)
        if (value.get('schema')!='iterlab-cuda-preflight/v1' or value.get('requested_gpus')!=approved['gpus']
            or type(value.get('ready')) is not bool or value.get('fitting') is not False):
            raise ValueError('invalid preflight receipt')
        if value['ready'] and (not isinstance(value.get('devices'),list) or len(value['devices'])!=approved['gpus']
                or any(not isinstance(item,dict) or item.get('allocation_bytes')!=4 or not isinstance(item.get('uuid'),str)
                       or not re.fullmatch(r'GPU-[a-fA-F0-9-]{36}',item['uuid'])
                       or any(type(item.get(k)) is not int or item[k]<0 for k in ('index','free_bytes','total_bytes'))
                       or item['free_bytes']>item['total_bytes'] for item in value['devices'])):
            raise ValueError('missing device allocation evidence')
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        raise ComputePreflightError('GPU可用性预检未确认；未提交训练、未扣任务次数，保持原运行位置。',
            {'ready':False,'reason':'protocol_unconfirmed','fitting':False}) from error
    if response.returncode or not value['ready']:
        reason=value.get('reason','cuda_preflight_unavailable')
        if not isinstance(reason,str) or not re.fullmatch(r'(?:cu[A-Za-z0-9_]+:[0-9]{1,10}|[a-z_]{1,80})',reason):
            reason='cuda_preflight_unavailable'
        raise ComputePreflightError('GPU预检失败（'+reason+'）；属于环境/资源问题，未提交训练、未扣任务次数，不能切回本地。',
            {'ready':False,'reason':reason,'fitting':False,'requested_gpus':approved['gpus'],
             'probe_sha256':hashlib.sha256(source.encode('utf-8')).hexdigest()})
    return {'schema':value['schema'],'ready':True,'requested_gpus':approved['gpus'],'fitting':False,
            'devices':[{key:item[key] for key in ('index','uuid','free_bytes','total_bytes','allocation_bytes')} for item in value['devices']],
            'probe_sha256':hashlib.sha256(source.encode('utf-8')).hexdigest()}


def stage(directory, run_id, scope, manifest, script, args, outputs, timeout, detached=False):
    job = {'run_id': run_id, 'script': script, 'args': args, 'outputs': outputs,
           'timeout': timeout, 'gpus': scope['gpus'], 'space_uid':scope['uid'],
           'inputs': {name: item['sha256'] for name,item in manifest.items()}}
    worker=WORKER.read_bytes()
    if detached:job['worker_digest']=hashlib.sha256(worker).hexdigest()
    with tarfile.open(directory / '.iterlab-input.tar', 'x:gz' if detached else 'x',**({'compresslevel':1} if detached else {})) as archive:
        for name in manifest:
            archive.add(directory / name, arcname=name, recursive=False)
        body = json.dumps(job, ensure_ascii=False).encode('utf-8')
        header = tarfile.TarInfo('.iterlab-job.json')
        header.size = len(body)
        archive.addfile(header, io.BytesIO(body))
        if detached:
            header=tarfile.TarInfo('.iterlab-supervisor.py');header.size=len(worker)
            archive.addfile(header,io.BytesIO(worker))
    if detached:
        return hashlib.sha256(json.dumps(job,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return shlex.join(['python3', '-c', WORKER.read_text(encoding='utf-8'), run_id, scope['uid']])


def remote_command(mode,run_id,space_uid,job_digest):
    if mode not in {'submit','status'}:raise ValueError('Unknown remote control action')
    return shlex.join(['python3','-c',WORKER.read_text(encoding='utf8'),mode,run_id,space_uid,job_digest])


def collect(directory, run_id, space_uid, outputs, idempotent=False):
    path = directory / '.iterlab-response.json'
    if path.stat().st_size > PROTOCOL_LIMIT:
        raise ValueError('开发空间返回超出产物回收上限')
    value = json.loads(path.read_bytes())
    if (value['run_id'] != run_id or value['space_uid'] != space_uid
        or value['state'] not in {'completed', 'failed', 'timed_out', 'invalid_inputs'}
        or value['state']=='completed' and (type(value['exit_code']) is not int or value['exit_code']!=0)
        or not isinstance(value['files'],dict) or not {'stdout.log','stderr.log'}<=value['files'].keys()):
        raise ValueError('开发空间运行身份或回执无效')
    allowed = {'stdout.log', 'stderr.log', *outputs}
    decoded=[]
    for name, data in value['files'].items():
        dest = directory / name
        if name not in allowed or dest.resolve() != dest or not dest.is_relative_to(directory):
            raise ValueError('开发空间返回了未声明的产物路径')
        dest.parent.mkdir(parents=True, exist_ok=True)
        body = base64.b64decode(data, validate=True)
        if idempotent:
            if dest.is_symlink():raise ValueError('产物路径包含链接')
            if dest.exists() and dest.read_bytes()!=body:raise ValueError('已保存产物与原远端回执不符；不覆盖')
        decoded.append((dest,name,body))
    for dest,name,body in decoded:
        if idempotent:
            from .devspace_jobs import DevspaceJobs
            DevspaceJobs._immutable(dest,body)
            continue
        if name in {'stdout.log', 'stderr.log'}:
            with dest.open('ab') as stream:
                stream.write(body)
        else:
            with dest.open('xb') as stream:
                stream.write(body)
    return value['state'], value['exit_code']
