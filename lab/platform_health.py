"""Scoped, explicit read checks. No polling, credential persistence or LLM calls."""
import hashlib
import json
import os
from pathlib import Path
import re
import time
from datetime import datetime,timezone

from .store import dumps,now


AUTH_SOURCES={'lpai_cli':'LPAI Agent 配置','lpai_sdk':'训练 SDK 独立登录','gitlab_api':'GitLab 官方登录'}


def credential_revision(kind):
    """Invalidate observations using file metadata, never credential contents."""
    if kind not in AUTH_SOURCES:return None
    if kind=='gitlab_api':
        path=Path(os.environ.get('GLAB_CONFIG_DIR') or str(Path(os.environ.get('APPDATA') or Path.home()/'.config')/'glab-cli'))/'config.yml'
        environment='GITLAB_TOKEN'
    else:
        relative,environment=(('.config/lpai-agent/config','LPAI_API_KEY') if kind=='lpai_cli' else ('.lpai/conf.json','LPAI_TOKEN'))
        path=Path.home()/relative
    try:
        stat=path.stat();marker=['present',stat.st_mtime_ns,stat.st_ctime_ns,stat.st_size,stat.st_ino]
    except FileNotFoundError:marker=['missing']
    except OSError:marker=['unreadable']
    # Process environment is immutable for the running service. Only its
    # presence is needed; do not hash or copy token values into the database.
    return hashlib.sha256(dumps([kind,marker,bool(os.environ.get(environment))]).encode()).hexdigest()


def error_category(error):
    # Inspect locally, persist only an allowlisted category/message, never raw SDK output.
    if getattr(error,'category',None) in MESSAGES:return error.category
    chain=[];item=error
    for _ in range(4):
        if item is None or any(item is previous for previous in chain):break
        chain.append(item);item=item.__cause__ or item.__context__
    text=' '.join(type(e).__name__+' '+str(e) for e in chain).lower()
    for category,markers in [
        ('proxy',['proxyconnect','proxyerror','proxy connection','proxyconnect tcp','代理']),
        ('namespace',['namespace不符','namespace mismatch']),
        ('authentication',['unauthorized','401','authentication failed','not logged','invalid token','认证缺失',
            'not authenticated','no api key','api key is required','api_key is required','token not found','lpai.token not found','glab auth login']),
        ('permission',['403','forbidden','permission denied','access denied']),
        ('tls',['certificate','sslerror','tls','x509']),
        ('timeout',['timeout','timed out','超时']),
        ('network',['connectionerror','connection refused','connection reset','could not resolve','connectex','network','无法解析']),
        ('dependency',['filenotfounderror','modulenotfounderror','未安装','尚未安装']),
        ('response',['jsondecodeerror','格式','收据','响应','未核实']),
        ('http_client',['httpclientexception'])]:
        if any(marker in text for marker in markers):return category
    return 'unconfirmed'


MESSAGES={'authentication':'官方认证未通过，请在服务电脑完成官方登录。','permission':'当前账号无权访问此对象。',
    'proxy':'本机网络代理连接失败，尚未验证账号或平台权限；请确认代理服务与公司网络后重新检测。',
    'http_client':'官方SDK的HTTP请求未成功；需核对网络、认证或平台响应，尚不能认定账号失效。',
    'namespace':'SDK工作空间与所选namespace不一致。','tls':'证书校验未通过；未关闭TLS验证。',
    'timeout':'读取超时；未重复提交任何任务。','network':'平台网络不可达，请检查网络或代理。',
    'dependency':'所需官方工具或独立SDK未安装。','response':'平台响应未能确认，不能认定已连接。',
    'unconfirmed':'检查未通过，原因尚未确认；未输出原始凭据或异常正文。'}


class PlatformError(ValueError):
    def __init__(self,category):
        self.category=category if category in MESSAGES else 'unconfirmed'
        super().__init__(MESSAGES[self.category])


class PlatformHealth:
    def __init__(self,company):
        self.company=company;self.store=company.store
        self.store.execute('''CREATE TABLE IF NOT EXISTS company_health(
          id TEXT PRIMARY KEY,kind TEXT NOT NULL,target TEXT NOT NULL,config_revision INTEGER NOT NULL,
          state TEXT NOT NULL,category TEXT,message TEXT NOT NULL,checked_at TEXT NOT NULL,
          last_success_at TEXT,seconds REAL NOT NULL)''')
        self.store.execute("INSERT OR IGNORE INTO schema_versions VALUES(10,datetime('now'))")
        self.store.execute('''CREATE TABLE IF NOT EXISTS company_health_sources(
          health_id TEXT PRIMARY KEY,credential_revision TEXT NOT NULL)''')
        self.store.execute("INSERT OR IGNORE INTO schema_versions VALUES(23,datetime('now'))")

    def status(self):
        revision=self.company.config()['revision'];clock=time.time()
        rows=self.store.rows('SELECT * FROM company_health ORDER BY checked_at DESC LIMIT 30')
        for row in rows:
            source=self.store.one('SELECT credential_revision FROM company_health_sources WHERE health_id=?',(row['id'],))
            current=credential_revision(row['kind'])
            credentials_changed=current is not None and (not source or source['credential_revision']!=current)
            row['stale_reason']=('credentials_changed' if credentials_changed else 'config_changed' if row['config_revision']!=revision else
                'expired' if clock-datetime.fromisoformat(row['checked_at']).timestamp()>300 else None)
            row['stale']=row['stale_reason'] is not None
            if row['kind'] in AUTH_SOURCES:row['authentication_source']=AUTH_SOURCES[row['kind']]
        return rows

    def check(self,platform,session_id=None,component_uid=None,project_id=None,kinds=None):
        if platform not in {'lpai','gitlab'}:raise ValueError('仅支持已注册的平台连接检查')
        company=self.company;config=company.config()
        if not config[platform+'_enabled']:raise ValueError('请先启用此平台连接')
        checks=[]
        if platform=='lpai':
            from .company import checked_id
            if component_uid:checked_id(component_uid)
            checks=[('lpai_cli',config['namespace'],lambda: company.lpai('spaces'),
                     '官方CLI已读回开发空间列表；不代表SDK或训练已通过。'),
                    ('lpai_sdk',config['namespace']+('/'+component_uid if component_uid else ''),
                     lambda:company.training.sdk('connection_check',**({'component_uid':component_uid} if component_uid else {})),
                     '官方SDK已读回组件；仅验证读取，未提交训练。')]
        else:
            checks=[('gitlab_api',config['gitlab_host'],lambda:company.gitlab('account'),
                     'GitLab官方账号接口已读回；不代表Git传输已通过。')]
            item=None
            if session_id:
                session=self.store.session(session_id)
                binding=self.store.one("SELECT observation FROM company_bindings WHERE project_id=? AND kind='gitlab'",(session['project_id'],))
                if binding:item=json.loads(binding['observation'])
            if project_id:
                if not re.fullmatch(r'[A-Za-z0-9_.\-/]{1,240}',str(project_id)):
                    raise ValueError('需要仓库ID或group/project路径，不接受凭据或完整网址')
                target=config['gitlab_host']+' / '+str(project_id)+' / '+config['git_protocol']
                read=lambda:company.git_probe(company.gitlab('repository',project_id))
            elif item:
                target=item['host']+' / '+item['path_with_namespace']+' / '+config['git_protocol']
                read=lambda:company.git_probe(item)
            else:target=None
            if target:
                checks.append(('git_transport',target,read,
                               '所选Git传输已读回仓库引用；未克隆、推送或验证写权限。'))
        if kinds is not None:
            if not kinds or set(kinds)-{row[0] for row in checks}:raise ValueError('未注册的平台认证检查')
            checks=[row for row in checks if row[0] in kinds]
        results=[]
        for kind,target,read,message in checks:
            source_revision=credential_revision(kind)
            key=hashlib.sha256(dumps([kind,target,config['revision']]).encode()).hexdigest()
            previous=self.store.one('SELECT * FROM company_health WHERE id=?',(key,))
            saved_source=self.store.one('SELECT credential_revision FROM company_health_sources WHERE health_id=?',(key,))
            if source_revision is not None and (not saved_source or saved_source['credential_revision']!=source_revision):previous=None
            start=time.monotonic();stamp=now()
            try:
                for attempt in range(2):
                    try:
                        read();break
                    except Exception as transient:
                        if attempt or error_category(transient) not in {'timeout','network'}:raise
                state='passed';category=None;last_success=stamp
            except Exception as exc:
                state='failed';category=error_category(exc);message=MESSAGES[category]
                last_success=previous['last_success_at'] if previous else None
            if company.config()['revision']!=config['revision']:
                raise ValueError('检查期间连接配置已变化，请重新检查；未将旧结果用于新配置')
            if credential_revision(kind)!=source_revision:
                raise ValueError('检查期间官方认证配置已变化，请重新检测；未沿用旧认证结果')
            row={'id':key,'kind':kind,'target':target,'config_revision':config['revision'],'state':state,'category':category,
                 'message':message,'checked_at':stamp,'last_success_at':last_success,'seconds':round(time.monotonic()-start,3)}
            with self.store.lock,self.store.db:
                self.store.db.execute('''INSERT INTO company_health VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                  state=excluded.state,category=excluded.category,message=excluded.message,checked_at=excluded.checked_at,
                  last_success_at=excluded.last_success_at,seconds=excluded.seconds''',tuple(row.values()))
                if source_revision is not None:
                    self.store.db.execute('INSERT OR REPLACE INTO company_health_sources VALUES(?,?)',(key,source_revision))
            if kind in AUTH_SOURCES:row['authentication_source']=AUTH_SOURCES[kind]
            results.append(row)
        return {'checks':results,'git_transport_not_checked':platform=='gitlab' and len(results)==1,
                'scope':'只读检查不验证训练提交、资源配额、SSH训练通道、推送权限或产物回收。'}
