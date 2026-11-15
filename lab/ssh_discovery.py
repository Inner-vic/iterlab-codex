"""Bounded SSH setup using an official endpoint and existing local key pairs.

Public key names only rank candidates. Only successful noninteractive SSH
authentication and the existing space identity checks establish a connection.
No private-key contents are read/copied by the application or sent to a model.
"""
import os
from pathlib import Path
import re
import subprocess

from . import devspace


def local_identities(key_names=(), directory=None):
    directory=Path(directory) if directory else Path.home()/'.ssh'
    def words(value):
        return {word for word in re.split(r'[^a-z0-9]+',value.lower())
                if len(word)>=3 and not word.isdigit() and word not in {'ssh','key','ed25519','rsa'}}
    hints=set().union(*(words(name) for name in key_names if isinstance(name,str))) if key_names else set()
    candidates=[]
    for public in sorted(directory.glob('*.pub')):
        private=public.with_suffix('')
        if public.is_symlink() or private.is_symlink() or not private.is_file():continue
        # Avoid reading arbitrary files masquerading as public keys.
        try:
            if not public.is_file() or public.stat().st_size>16384:continue
            prefix=public.read_text(encoding='utf8',errors='replace').split()
        except OSError:continue
        if len(prefix)<2 or not prefix[0].startswith(('ssh-','ecdsa-','sk-')):continue
        score=len(words(public.stem)&hints)
        candidates.append((score,private))
    return [path for _,path in sorted(candidates,key=lambda item:(-item[0],item[1].name))][:8]


def configure_automatically(company,sid,space_uid):
    store=company.store;pid=store.session(sid)['project_id'];bound=devspace.binding(store,pid)
    if not bound or bound['uid']!=space_uid:raise ValueError('开发空间绑定已改变，请刷新后重新识别')
    from .company import checked_id,platform_item
    cli=company.locator('lpai-agent')
    if not cli:raise ValueError('尚未安装官方LPAI CLI，不能自动读取空间SSH')
    raw=company._run([cli,'--timeout','20','devspaces','get','--namespace',checked_id(bound['scope']),
                      '--name',checked_id(bound['name'])],timeout=25)
    if not isinstance(raw,dict) or raw.get('status')!='ok':raise ValueError('官方SSH信息读取未成功')
    data=raw.get('data');item=platform_item(data,bound['scope'],'space')
    if (item['uid'],item['name'],item['namespace'])!=(space_uid,bound['name'],bound['scope']):
        raise ValueError('官方返回的空间身份已变化，未尝试SSH连接')
    endpoint=item.get('ssh')
    if not endpoint:raise ValueError('平台没有返回该空间的官方SSH端点，请先在LPAI启用SSH；也可在高级配置填写官方命令')
    spec=data.get('spec') or {}
    names=[spec.get('sshSecretName'),*[a.get('pubkeyName') for a in spec.get('accesses',[])
        if isinstance(a,dict) and a.get('targetPort')==22]]
    paths=local_identities(names)
    saved=store.one('SELECT identity_path FROM company_space_ssh_identities WHERE project_id=? AND scope=? AND uid=?',
                    (pid,bound['scope'],space_uid))
    selected=saved['identity_path'] if saved and saved['identity_path'] else os.environ.get('ITERLAB_LPAI_SSH_IDENTITY')
    if selected and Path(selected).is_file():paths=[Path(selected),*[p for p in paths if p!=Path(selected)]]
    paths=paths[:8]
    command='ssh -p '+str(endpoint['port'])+' root@'+devspace.HOST
    if item['phase'].lower() not in {'ready','running'}:
        return devspace.configure(company,sid,space_uid,command)
    # A failed network/host-key/identity check is not permission to try more
    # keys. Only explicit authentication failures advance this bounded search.
    scope={'uid':space_uid,'name':bound['name'],'namespace':bound['scope'],
           'gpus':0,'port':endpoint['port']}
    candidates=[str(path.resolve()) for path in paths]+[None]
    for identity in candidates:
        args=devspace.connection(company,pid,scope,trust_new=True,identity=identity)
        try:
            reply=subprocess.run([*args,"printf 'iterlab-ssh-auth-ok\\n'"],stdin=subprocess.DEVNULL,
                capture_output=True,timeout=12,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        except (OSError,subprocess.TimeoutExpired) as exc:
            raise ValueError('SSH自动识别连接超时或客户端不可用；未继续尝试其他密钥') from exc
        if reply.returncode:
            text=reply.stderr.decode('utf8',errors='replace') if isinstance(reply.stderr,bytes) else str(reply.stderr)
            if 'Permission denied' in text:continue
            raise ValueError(devspace.ssh_error(reply))
        if reply.stdout.strip()!=b'iterlab-ssh-auth-ok':raise ValueError('SSH验证返回内容未确认，未保存连接')
        result=devspace.configure(company,sid,space_uid,command,identity or '')
        return {**result,'discovery':'official_endpoint_and_verified_local_identity'}
    raise ValueError('已识别官方SSH端点，但本机已有密钥均未完成认证。请核对空间绑定公钥，或解锁本机SSH代理中的密钥；无需发送私钥。')
