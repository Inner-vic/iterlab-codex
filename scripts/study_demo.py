"""Build an integrated, offline SDM demo with real local fixture evaluation.

No model fitting/inference, Codex connection, enterprise API or production DB.
The workbench APIs create records, freeze inputs, execute tiny evaluations and
derive the outcomes. All values are explicitly synthetic demo observations.
"""
import argparse
from html import escape
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import threading

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from lab.store import Store
from lab.research import Research, plan_digest
from lab.web import Application
from lab.study_integrity import payload


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',default='.evidence/sdm-inheritance-20261110/demo')
    parser.add_argument('--serve',action='store_true',help='只读预览已有Demo，不启动Agent')
    parser.add_argument('--port',type=int,default=8783)
    args=parser.parse_args();out=(ROOT/args.out).resolve()
    if not out.is_relative_to(ROOT):raise ValueError('Demo输出需在本项目内')
    if args.serve:
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path not in {'/','/preview.html'}:self.send_error(404);return
                body=(out/'preview.html').read_bytes()
                self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
                self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def do_POST(self):
                if self.path!='/stop-demo' or self.headers.get('Origin') or self.headers.get('Host')!=f'127.0.0.1:{args.port}':self.send_error(403);return
                self.send_response(200);self.send_header('Content-Length','0');self.end_headers()
                threading.Thread(target=self.server.shutdown,daemon=True).start()
        server=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
        try:server.serve_forever()
        finally:server.server_close()
        return
    out.mkdir(parents=True,exist_ok=True);data=out/'data'
    if (data/'workbench.sqlite3').exists():raise ValueError('Demo数据已存在，使用新的--out目录；不覆盖原示例')
    store=Store(data);research=Research(store)
    try:
        pid=store.create_project('SDM 基础演示（合成数据，非真实研究成果）')['id']
        sid=store.create_session(pid,'ept','生命周期思想如何落到实际记录')['id']
        workspace=Path(store.session(sid)['workspace'])
        def write(name,value):
            (workspace/name).write_text(value if isinstance(value,str) else json.dumps(value,ensure_ascii=False),encoding='utf-8')
        def record(kind,title,**fields):
            return store.add_record(sid,{'kind':kind,'title':title,'content':'仅为合成指标上的工程演示，没有训练模型，也不代表真实算法结论。',**fields},
                                    {'origin':'explicit-synthetic-demo'})
        write('protocol.json',{'purpose':'demo-no-training','data':'synthetic observations','threshold':0.5})
        write('reference-A.json',{'demo_candidate':'A','purpose':'identity demonstration only'})
        write('candidate-B.json',{'demo_candidate':'B','purpose':'identity demonstration only'})
        write('evaluate.py',"from pathlib import Path\nimport json,sys\n# Copies synthetic observations for pipeline evaluation; does not train.\nrows=json.loads(Path(sys.argv[1]).read_text())\nPath('metrics.json').write_text(json.dumps({'results':rows}))\n")
        q=record('question','演示起点：候选、基线和确认性证据怎样区分？',
                 content='本项目是合成数据Demo。展示真实程序路径与可追溯记录，不证明Agent自主研究质量。')
        a=record('research','候选 A：仅导入身份，未自动设为基线',evidence=['reference-A.json'])
        b=record('research','候选 B：探索对象，未声称训练有效',evidence=['candidate-B.json'])
        def selection(candidate,name,path,previous=None):
            return {'scope':'演示：同一固定指标条件','candidate_record_id':candidate['id'],'candidate':name,
                    'identity_files':[path],'previous_selection_id':previous,'reason':'演示显式选择与适用目标；不根据单次分数认定冠军。',
                    'comparison_impact':'原方案继续引用原选择；评价口径变化时旧比较需另行核对。'}
        base=record('decision','明确选择参考 A，保留候选 B',evidence=['reference-A.json'],
                    baseline_selection=selection(a,'演示参考 A','reference-A.json'))
        spec={'schema':1,'title':'演示：每个配对的指标至少改善0.01','artifact':'metrics.json','rows':'/results',
              'group_by':'/variant','pair_by':'/seed','baseline':'reference','candidate':'candidate','pairs':[1,2],
              'protocol_files':['protocol.json'],'rules':[{'key':'score','label':'演示指标','value':'/score','direction':'maximize',
              'aggregation':'each_pair','comparison':'improve','tolerance':0.01,'bounds':[0,1]}]}
        review={'readiness':[{'phase':'protocol','status':'checked','reason':'核对了实际演示协议；非模型训练就绪认证。',
                 'checks':[{'basis':'source','claim':'协议明确为无训练演示','reason':'逐字读取固定来源',
                 'source_quotes':[{'path':'protocol.json','text':'demo-no-training'}],'command_ids':[]}]}],
                'lenses':[{'lens':'domain','status':'not_applicable','reason':'纯合成指标管线，没有几何模型。','checks':[]},
                          {'lens':'statistical_validity','status':'needs_evidence','reason':'没有真实采样总体，不提供显著性或泛化结论。','checks':[]}]}
        plan=record('plan','冻结演示评价规则，引用参考 A',evidence=['protocol.json','reference-A.json','candidate-B.json','evaluate.py'],
                    baseline_ref=base['id'],evaluation_contract=spec,study_review=review)
        research.review(sid,{'plan_id':plan['id'],'expected_digest':plan_digest(plan),'decision':'accept',
            'actor':'用户委托开发助手审批','reason':'用户要求基础Demo；仅本地合成数据评估，禁止拟合。',
            'authority':'隔离Demo评估范围，不涉及用户真实项目或GPU','execution':'local','allow_local':True,
            'python':sys.executable,'max_runs':3,'max_fits':0,'timeout':10})
        def assess(level='exploratory'):
            return {'claim':'这些演示指标是否满足固定条件','basis':'comparison_rules','level':level,
                    'reason':'程序读取实际评估产物与事前冻结条件。','limitations':'合成演示；不能说明真实模型性能、统计独立或创新。','evidence':[]}
        results=[];run_ids=[];comparison_ids=[]
        for index,(name,scores) in enumerate([('positive',[.83,.84]),('negative',[.79,.84]),('invalid',[])],1):
            rows=[]
            for seed,score in enumerate(scores,1):rows += [{'variant':'reference','seed':seed,'score':.8},{'variant':'candidate','seed':seed,'score':score}]
            filename='observations-'+name+'.json';write(filename,rows)
            request={'plan_id':plan['id'],'script':'evaluate.py',
                     'files':['evaluate.py',filename,'protocol.json','reference-A.json','candidate-B.json'],
                     'args':[filename],'purpose':'evaluation','question':'演示比较器的输入条件分支',
                     'expected_information':'仅核验工程逻辑，不训练或推理模型','outputs':['metrics.json']}
            run=research.run(sid,request,'explicit-demo-evaluation-'+str(index))
            if run['state']!='completed':raise ValueError('演示评估脚本未完成：'+str(run['state']))
            run_ids.append(run['run_id'])
            compared=research.comparison.compare(sid,plan['id'],[run['run_id']],collect_missing=True)
            cid=compared['comparison_id'];comparison_ids.append(cid)
            result=record('result',{'positive':'支持：满足演示数值条件','negative':'反对：未满足全部数值条件','invalid':'比较无效：缺少必要观测'}[name],
                          plan_id=plan['id'],run_ids=[run['run_id']],comparison_ids=[cid],scientific_assessment=assess())
            results.append(result)
        pending=record('result','证据不足：搜索结果不能直接变成确认性结果',plan_id=plan['id'],
                       comparison_ids=[comparison_ids[0]],scientific_assessment=assess('confirmatory'))
        updated=record('decision','演示更新参考：旧方案仍固定引用 A',evidence=['candidate-B.json'],
                       baseline_selection=selection(b,'演示参考 B','candidate-B.json',base['id']))
        stop=record('review','演示收尾：停止原因与科学判断分开',plan_id=plan['id'],run_ids=run_ids,
                    round_outcome='stop_requested',stop_reason='budget')
        outcomes=[payload(r)['scientific_assessment']['outcome'] for r in [*results,pending]]
        assert outcomes==['supported','refuted','invalid','inconclusive']
        assert payload(plan)['baseline_ref']==base['id']
        assert not payload(stop).get('scientific_assessment')
        graph=store.graph(pid)
        app=Application.__new__(Application);app.store=store;app.kernel=SimpleNamespace(research=research)
        details={n['id']:app.route('GET','/api/records/'+n['id'],{}, {}) for n in graph['nodes']}
        graph['execution_location']={'state':'idle','active_node_ids':[],'focus_node_id':None,'summary':'已生成的合成Demo；没有Agent或训练正在运行。'}
        saved=json.dumps({'graph':graph,'details':details},ensure_ascii=False).replace('<','\\u003c')
        bundle=(ROOT/'static/vendor/exploration-map.js').read_text(encoding='utf-8').replace('</script','<\\/script')
        css=(ROOT/'static/vendor/exploration-map.css').read_text(encoding='utf-8')
        notice=escape((ROOT/'static/vendor/exploration-map.NOTICE.txt').read_text(encoding='utf-8'))
        html='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>SDM 思想承接 · 基础演示</title>
<style>body{margin:0;font:14px system-ui;color:#263955;background:#f6f8fb}header{padding:12px 20px;border-bottom:1px solid #dfe5ef}h1{font-size:18px;margin:0 0 6px}p{margin:0;font-size:12px;color:#64748b}#map{height:calc(100vh - 78px)}</style><style>'''+css+'''</style>
<header><h1>SDM 思想承接 · 基础演示</h1><p>合成数据 · 三次本地静态指标评估 · 零训练/推理/模型调用 · 点击节点查看程序结果与依据</p></header><div id="map"></div>
<script type="application/json" id="saved">'''+saved+'''</script><script>'''+bundle+'''</script><script>const saved=JSON.parse(document.getElementById('saved').textContent);window.map=window.IterLabExplorationMap.mount(document.getElementById('map'),{data:saved.graph,loadDetail:async id=>saved.details[id]});window.map.frameContext(saved.graph.nodes.find(node=>node.kind==='plan').id);</script>
<template id="third-party-notices">'''+notice+'''</template></html>'''
        (out/'preview.html').write_text(html,encoding='utf-8')
        (out/'graph.json').write_text(json.dumps(graph,ensure_ascii=False,indent=2),encoding='utf-8')
        (out/'details.json').write_text(json.dumps(details,ensure_ascii=False,indent=2),encoding='utf-8')
        verification={'project_id':pid,'session_id':sid,'synthetic_demo':True,'actual_local_evaluations':len(run_ids),
                      'fitting_runs':0,'model_calls':0,'outcomes':outcomes,'baseline_update_keeps_old_reference':True,
                      'budget_stop_does_not_imply_refutation':True,'main_database_used':False}
        (out/'verification.json').write_text(json.dumps(verification,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(verification,ensure_ascii=False))
    finally:research.close();store.close()


if __name__=='__main__':main()
