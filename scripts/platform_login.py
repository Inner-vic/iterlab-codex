"""Interactive local setup. Tokens never appear in arguments, HTTP or logs."""
import argparse,getpass,hmac,os,re,shutil,subprocess,sys,tempfile,webbrowser
from pathlib import Path

def token_value(value):
    if not isinstance(value,str) or not 8<=len(value)<=16000 or any(ord(c)<33 or ord(c)>126 for c in value):
        raise ValueError('Invalid token. Paste only the token, without spaces or quotes.')
    return value

def check_environment(name,token):
    existing=os.environ.get(name)
    if existing and not hmac.compare_digest(existing,token):
        raise ValueError('An environment variable overrides this login. Update it on this computer and restart IterLab first.')

def save_agent_token(token,home=None):
    token_value(token);check_environment('LPAI_API_KEY',token)
    path=Path(home or Path.home())/'.config/lpai-agent/config'
    if path.is_symlink() or any(p.is_symlink() for p in path.parents):raise ValueError('Configuration path must not be a symbolic link.')
    original=path.read_text(encoding='utf-8') if path.exists() else ''
    pattern=r'(?m)^\s*api_key\s*=.*$'
    if len(re.findall(pattern,original))>1:raise ValueError('Duplicate api_key settings; review the official configuration locally.')
    updated=re.sub(pattern,lambda _: 'api_key='+token,original) if re.search(pattern,original) else original.rstrip()+'\napi_key='+token+'\n'
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(dir=path.parent,prefix='.login-')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as out:out.write(updated)
        os.chmod(temp,0o600);os.replace(temp,path)
    finally:
        if Path(temp).exists():Path(temp).unlink()

def save_sdk_token(token,namespace):
    token_value(token);check_environment('LPAI_TOKEN',token)
    if token.startswith('lpak_'):raise ValueError('Use the Training token, not an LPAI Agent token.')
    from lpai.conf.lipai_conf import LpaiConf
    conf=LpaiConf(env='prod',token=token,namespace=namespace)
    conf.save()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind',choices=['lpai_cli','lpai_sdk','gitlab'])
    parser.add_argument('--host',default='gitlab.chehejia.com');parser.add_argument('--namespace',default='vc-chassis')
    parser.add_argument('--tool');args=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9.-]+(?::[0-9]{1,5})?',args.host) or not re.fullmatch(r'[A-Za-z0-9_.-]+',args.namespace):
        raise ValueError('Invalid platform target.')
    if args.kind=='gitlab':
        tool=args.tool or shutil.which('glab')
        if not tool:raise ValueError('Install official GitLab CLI (glab), then restart IterLab.')
        return subprocess.call([tool,'auth','login','--hostname',args.host,'--web','--git-protocol','https'])
    if not sys.stdin.isatty():raise ValueError('Run in an interactive local terminal; credentials are never accepted from a pipe.')
    if args.kind=='lpai_cli':
        webbrowser.open('https://lpai.lixiang.com/lpaiweb/xspacex_'+args.namespace+'/subApp/agent/overview')
        print('LPAI Agent login: create/copy your own Agent token on the official page. This is NOT the Training SDK token.')
        token=getpass.getpass('Agent token (hidden): ');save_agent_token(token)
    else:
        webbrowser.open('https://lpai.lixiang.com')
        print('Training SDK login: open Personal Center and copy your own Training token. Do NOT paste the LPAI Agent token.')
        token=getpass.getpass('Training SDK token (hidden): ');save_sdk_token(token,args.namespace)
    print('Saved to official local configuration. IterLab will verify automatically. No task was submitted.')
    return 0

if __name__=='__main__':
    try:code=main()
    except Exception:print('Login setup did not complete. Check the official tool installation, token type, file permissions and environment overrides. No token was logged.');code=1
    if code and sys.stdin.isatty():input('Press Enter to close this window...')
    raise SystemExit(code)
