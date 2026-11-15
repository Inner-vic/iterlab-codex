"""Read-only preflight by default; explicit tests use isolated, offline fixtures."""
import argparse
import ast
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
MODULES=[
    'test_closure_boundaries','test_loop_closure','test_execution_integrity','test_run_timing',
    'test_rounds','test_workflow','test_autonomy','test_run_evidence','test_roles','test_role_progress',
    'test_comparison','test_autonomous_host','test_cloud_continuation_host',
    'test_cloud_execution_integrity','test_lpai_identity','test_lpai_recovery','test_company',
    'test_closure_http','test_transport_web','test_core','test_capabilities',
    'test_research_guidance','test_research','test_lineage','test_assets',
    'test_cloud_recovery','test_cloud_probe','test_cloud_origin_http',
]
NODE_FILES=['research-ui.test.mjs','conversation.test.mjs','delivery.test.mjs']
FLAGS={'creationflags':subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0}


def preflight():
    required=['tests/__init__.py','tests/fake_rpc_server.py','static/index.html',
              'methods/research/contract.md','docs/capability-consolidation-20261028.md',
              'skills/sdm-research/SKILL.md','skills/sdm-research/source-manifest.json',
              'skills/sdm-research/references/assessment.md','skills/sdm-research/references/conduct.md',
              'skills/sdm-research/references/planning.md','skills/sdm-research/references/responsibilities.md',
              'lab/cloud_evidence.py','scripts/lpai_asset_bridge.py','scripts/probe_cloud_outputs.py',
              'scripts/run-quiet.ps1']
    required += ['tests/'+name+'.py' for name in MODULES]+['tests/'+name for name in NODE_FILES]
    missing=[name for name in required if not (ROOT/name).is_file()]
    syntax=[]
    for path in sorted((ROOT/'lab').glob('*.py'))+[(ROOT/'tests'/(name+'.py')) for name in MODULES]:
        if not path.is_file():continue
        try:ast.parse(path.read_text(encoding='utf-8'),filename=str(path))
        except (SyntaxError,UnicodeError,OSError):syntax.append(path.relative_to(ROOT).as_posix())
    node=shutil.which('node');version=None
    if node:
        try:
            result=subprocess.run([node,'--version'],capture_output=True,text=True,timeout=5,**FLAGS)
            if result.returncode==0:version=result.stdout.strip()
        except (OSError,subprocess.TimeoutExpired):pass
    try:node_ready=bool(version and int(version.lstrip('v').split('.')[0])>=24)
    except ValueError:node_ready=False
    return {'python':sys.version.split()[0],'python_supported':sys.version_info>=(3,11),
            'platform':sys.platform,'node':version,'node_24_supported':node_ready,
            'missing_files':missing,'syntax_errors':syntax,'preflight_only':True,
            'real_model_calls':0,'cloud_submissions':0,'production_store_opened':False,
            'meaning':'离线工程预检；不检查官方登录，不证明Windows或真实AI科研闭环通过'}


def run_python(log):
    sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tests'))
    from lab.settings import Settings
    from lab.company import Company
    settings_init=Settings.__init__;company_init=Company.__init__
    def fixture_settings(self,root):
        settings_init(self,root)
        if not self.path.exists():
            from lab.roles import default_policy
            self.value['channel']='ept';self.value['agent_roles']=default_policy('ept')
        self.command=lambda *args:['offline-fixture-no-model']
        self.verify_channel=lambda:None
    def fixture_company(self,*args,**kwargs):
        company_init(self,*args,**kwargs)
        if 'locator' not in kwargs and len(args)<3:
            from lab.company import locate
            self.locator=lambda program:(locate(program) or 'fixture-installed-'+program) if program=='git' else 'fixture-installed-'+program
    with patch.object(Settings,'__init__',fixture_settings),patch.object(Company,'__init__',fixture_company):
        suite=unittest.defaultTestLoader.loadTestsFromNames(['tests.'+name for name in MODULES])
        with log.open('x',encoding='utf-8') as stream:
            result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    return {'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
            'skipped':len(result.skipped),'passed':result.testsRun-len(result.failures)-len(result.errors)-len(result.skipped),
            'successful':result.wasSuccessful(),'log':log.relative_to(ROOT).as_posix(),
            'scope':'临时研究库、脚本化原生/平台接口和真实标准库短进程；不是模型研究验收'}


def run_node(log):
    node=shutil.which('node')
    with log.open('xb') as stream:
        try:
            result=subprocess.run([node,'--test',*[str(ROOT/'tests'/name) for name in NODE_FILES]],
                cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT,timeout=60,**FLAGS)
            code=result.returncode
        except (OSError,subprocess.TimeoutExpired):code=1
    return {'exit_code':code,'successful':code==0,'log':log.relative_to(ROOT).as_posix(),
            'scope':'选定DOM/内存会话/投递回归；未控制浏览器或正式服务'}


def main():
    parser=argparse.ArgumentParser(description='默认只预检；显式运行离线工程回归，不调用模型或云平台。')
    mode=parser.add_mutually_exclusive_group()
    mode.add_argument('--preflight',action='store_true')
    mode.add_argument('--run-tests',action='store_true')
    parser.add_argument('--skip-node',action='store_true',help='明确仅验收Python，不把前端未运行称通过')
    args=parser.parse_args();check=preflight()
    if not args.run_tests:
        print(json.dumps(check,ensure_ascii=False,indent=2))
        return int(bool(check['missing_files'] or check['syntax_errors'] or not check['python_supported']))
    if check['missing_files'] or check['syntax_errors'] or not check['python_supported']:
        print(json.dumps(check,ensure_ascii=False,indent=2));return 2
    if not args.skip_node and not check['node_24_supported']:
        print('完整本机工程验收需要Node24+；未运行测试，也未自动安装依赖。可明确用--skip-node仅验证Python。')
        return 2
    name=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    directory=ROOT/'.evidence/goal-loop'/name;directory.mkdir(parents=True,exist_ok=False)
    report={'preflight':check,'started_at':datetime.now(timezone.utc).isoformat(),
            'real_model_calls':0,'cloud_submissions':0,'production_store_opened':False}
    report['python']=run_python(directory/'python.txt')
    report['node']={'state':'not_run','successful':None} if args.skip_node else run_node(directory/'node.txt')
    code=int(not report['python']['successful'] or not args.skip_node and not report['node']['successful'])
    report.update(finished_at=datetime.now(timezone.utc).isoformat(),exit_code=code,
                  meaning='真实AI研究闭环、Windows/Node24运行范围以本次环境及实际收据为准；工程测试不认证科研质量')
    receipt=directory/'receipt.json'
    with receipt.open('x',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    print('验证收据：'+receipt.relative_to(ROOT).as_posix())
    print(json.dumps({'python':report['python'],'node':report['node'],'exit_code':code},ensure_ascii=False,indent=2))
    return code


if __name__=='__main__':sys.exit(main())
