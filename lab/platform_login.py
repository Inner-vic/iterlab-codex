"""User-only local login launcher. No credentials traverse HTTP, DB or models."""
import os,subprocess,sys,threading
from pathlib import Path
from .company import HOST

ROOT=Path(__file__).resolve().parents[1]
KINDS={'lpai_cli','lpai_sdk','gitlab'}

class PlatformLogin:
    def __init__(self,company):
        self.company=company;self.jobs={};self.lock=threading.RLock()

    def options(self):
        config=self.company.config();items=[]
        health={}
        for row in self.company.health.status():health.setdefault(row['kind'],row)
        for kind in ('lpai_cli','lpai_sdk','gitlab'):
            path=(ROOT/'.runtime/lpai/Scripts/python.exe' if os.name=='nt' else ROOT/'.runtime/lpai/bin/python') if kind=='lpai_sdk' else None
            installed=path.is_file() if path else bool(self.company.locator('glab' if kind=='gitlab' else 'lpai-agent'))
            observation=health.get('gitlab_api' if kind=='gitlab' else kind)
            state=('unavailable' if not installed else 'needs_check' if not observation or observation['stale'] else
                   'connected' if observation['state']=='passed' else
                   'needs_login' if observation['category']=='authentication' else 'attention')
            items.append({'kind':kind,'installed':installed,'login_state':state,
                'message':observation['message'] if observation and not observation['stale'] else None,
                'running':bool(self.jobs.get(kind) and self.jobs[kind].poll() is None),
                'verification':'not_checked_by_launcher','configuration_is_not_login':True})
        return {'items':items,'interactive_window_supported':os.name=='nt',
            'lpai_agent_url':f"https://lpai.lixiang.com/lpaiweb/xspacex_{config['namespace']}/subApp/agent/overview",
            'lpai_url':'https://lpai.lixiang.com','gitlab_url':'https://'+config['gitlab_host']+'/users/sign_in',
            'credential_storage':'仅本机官方工具配置；不进入IterLab数据库、会话或模型',
            'lpai_credentials_are_independent':True}

    def start(self,kind):
        if kind not in KINDS:raise ValueError('仅支持已注册的官方平台登录')
        config=self.company.config()
        if not HOST.fullmatch(config['gitlab_host']):raise ValueError('GitLab地址无效')
        with self.lock:
            job=self.jobs.get(kind)
            if job and job.poll() is None:return {'started':True,'already_running':True,'kind':kind}
            platform='gitlab' if kind=='gitlab' else 'lpai'
            if not config[platform+'_enabled']:raise ValueError('请先启用此平台连接')
            # Fresh read before requesting credentials: a working official
            # login must be reused, never replaced just because a button was clicked.
            check_kind='gitlab_api' if kind=='gitlab' else kind
            checks=self.company.health.check(platform,kinds={check_kind})['checks']
            observation=next(row for row in checks if row['kind']==check_kind)
            if observation['state']=='passed':
                return {'started':False,'reused':True,'kind':kind,'checks':checks,'message':'已自动验证并复用本机登录，无需重新配置。'}
            if observation['category']!='authentication':
                return {'started':False,'kind':kind,'checks':checks,'needs_attention':True,'message':observation['message']}
            # A login can change the owner of all subsequent platform reads.
            store=self.company.store
            if store.one("SELECT id FROM sessions WHERE active_turn IS NOT NULL OR status IN ('starting','running','unknown','waiting_approval') LIMIT 1"):
                raise ValueError('请先暂停正在执行的研究并核对原请求，再更换平台登录；不会停止云任务')
            if store.one("SELECT id FROM company_training WHERE state IN ('requested','unknown','submitted','pending','running','pending_approval') LIMIT 1"):
                raise ValueError('仍有在途或未知云任务，请先核对原身份，再更换账号')
            if os.name!='nt':
                return {'started':False,'manual_required':True,'kind':kind,
                    'command':['.runtime/lpai/bin/python' if kind=='lpai_sdk' else 'python','scripts/platform_login.py',kind,'--host',config['gitlab_host'],'--namespace',config['namespace']]}
            python=ROOT/'.runtime/lpai/Scripts/python.exe' if kind=='lpai_sdk' else Path(sys.executable)
            if not python.is_file():raise ValueError('训练SDK尚未安装，请先运行scripts/setup.ps1')
            binary=self.company.locator('glab' if kind=='gitlab' else 'lpai-agent') if kind!='lpai_sdk' else None
            if kind!='lpai_sdk' and not binary:
                raise ValueError('请先安装GitLab官方CLI（glab）并重启服务' if kind=='gitlab' else '官方工具尚未安装，请先运行scripts/setup.ps1')
            args=[str(python),'-B',str(ROOT/'scripts/platform_login.py'),kind,'--host',config['gitlab_host'],'--namespace',config['namespace']]
            if binary:args+=['--tool',binary]
            self.jobs[kind]=subprocess.Popen(args,cwd=ROOT,creationflags=subprocess.CREATE_NEW_CONSOLE)
            store.event(None,'company.login_window_opened',{'kind':kind,'credentials_recorded':False})
            return {'started':True,'kind':kind,'message':'请在新打开的本机窗口完成登录，返回后重新检测。'}
