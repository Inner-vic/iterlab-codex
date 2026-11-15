from __future__ import annotations

import argparse
import base64
import gzip
import json
import mimetypes
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .kernel import Kernel
from .store import Store, dumps, uid
from .assets import StaticBundle

ROOT = Path(__file__).resolve().parents[1]


class Application:
    def __init__(self, data_root, kernel_factory=Kernel):
        self.assets=StaticBundle(ROOT/'static')
        self.store = Store(data_root)
        self.kernel = kernel_factory(self.store)
        self.kernel.company.start_monitor(self.kernel)
        self.kernel.continuation.start()
        self.token = secrets.token_urlsafe(32)
        self.closed = False

    def close(self):
        self.closed = True
        self.kernel.stopping = True
        with self.store.changed:
            self.store.changed.notify_all()
        self.kernel.company.close()
        self.kernel.close()
        self.store.close()

    def route(self, method, path, query, data):
        parts = [unquote(p) for p in path.split('/') if p]
        store, kernel = self.store, self.kernel
        if path == '/api/state':
            return {**store.navigation(),"kernel":kernel.status(),"service":{"assets":self.assets.revision,"capabilities":["models.list"]},"cursor":store.one("SELECT COALESCE(MAX(seq),0) AS seq FROM events")["seq"]}
        if path == '/api/trash' and method == 'GET':return {'items':store.trash_items()}
        if path == '/api/trash' and method == 'POST':
            from .deletion import remove
            return remove(kernel,data['kind'],data['id'])
        if path == '/api/trash/restore' and method == 'POST':return store.restore_deleted(data['kind'],data['id'])
        if path == '/api/trash/purge' and method == 'POST':
            from .purge import purge
            return purge(kernel,data['kind'],data['id'],data.get('confirmation'))
        if path == '/api/delete/permanent' and method == 'POST':
            from .purge import purge
            return purge(kernel,data['kind'],data['id'],data.get('confirmation'),direct=True)
        if path == '/api/archived-sessions' and method == 'GET':return {'items':store.archived_sessions()}
        if path == '/api/connect' and method == 'POST':
            return kernel.connect()
        if path == '/api/models' and method == 'POST':return kernel.model_catalog(data['channel'])
        if path == '/api/settings' and method == 'POST':
            return kernel.save_settings(data)
        if path == '/api/company' and method == 'GET':
            return kernel.company.status()
        if path=='/api/company/login' and method=='GET':return kernel.company.login.options()
        if path=='/api/company/login' and method=='POST':
            if set(data)!={'kind'}:raise ValueError('登录入口仅接收平台类型，不接收凭据或任意命令')
            return kernel.company.login.start(data['kind'])
        if path == '/api/company/resources' and method == 'POST':
            if set(data)-{'cluster','refresh'}:raise ValueError('资源目录仅接受集群与刷新标记')
            return kernel.company.resource_directory.catalog(data.get('cluster'),data.get('refresh') is True)
        if path == '/api/company/resource-queue-preference' and method == 'POST':
            if set(data)!={'session_id','queue_key','expected_account_scope'}:raise ValueError('资源选择须携带刚读取的账号范围，不接受GPU授权或任务参数')
            return kernel.company.resource_directory.select_queue(data['session_id'],data['queue_key'],data['expected_account_scope'])
        if path == '/api/company/resource-preference' and method == 'POST':
            if set(data)-{'session_id','cluster','package_id','replicas'}:raise ValueError('资源选择不接受审批或预算字段')
            return kernel.company.resource_directory.select(data['session_id'],data['cluster'],data['package_id'],data.get('replicas',1))
        if path == '/api/research/status' and method == 'POST':
            result=kernel.research.status(data['session_id'])
            result['review_blocked']=bool(store.session(data['session_id'])['active_turn'] or kernel.delivery_control.barrier(data['session_id']))
            from .research import local_device
            result['local_device']=local_device()
            result['lpai_available']=kernel.company.config()['lpai_enabled'] and store.session(data['session_id'])['channel']=='ept'
            result['auto_component_available']=kernel.company.training.can_preview_component(data['session_id'])
            result['component_resource_preference']=kernel.company.resource_directory.preference(data['session_id'])
            result['project_id']=store.session(data['session_id'])['project_id']
            from .devspace import binding as devspace_binding, ssh_profile
            space=devspace_binding(store,result['project_id'])
            result['devspace_execution_supported']=True
            result['devspace_binding']={key:space[key] for key in ('name','uid','scope')} if space else None
            if space:
                profile=ssh_profile(store,result['project_id'],space)
                result['devspace_binding']['ssh_port']=profile['port'] if profile else None
            for grant in result['grants']:
                if grant['execution']=='lpai_devspace':
                    review=store.one('SELECT v.authority FROM research_grants g JOIN research_reviews v ON v.id=g.review_id WHERE g.id=?',(grant['id'],))
                    grant['devspace']=json.loads(review['authority']).get('devspace') if review else None
            result['agent_roles']=kernel.roles.status(data['session_id'],kernel.settings.value)
            binding=kernel.sdm.binding(data['session_id'])
            result['method']={key:binding[key] for key in ['name','mode','version','digest','path']}
            result['method_update']=kernel.sdm.available_update(data['session_id'])
            for item in result['plans']:
                from .research import plan_digest
                item['digest']=plan_digest(kernel.research.plan(data['session_id'],item['id']))
                item['scope_proposal']=json.loads(kernel.research.plan(data['session_id'],item['id'])['source']).get('scope_proposal')
            return result
        if path == '/api/research/component-preview' and method == 'POST':
            plan=kernel.research.plan(data['session_id'],data['plan_id'])
            from .research import plan_digest
            if data.get('expected_digest')!=plan_digest(plan):raise ValueError('方案已变化，请重新核对')
            return kernel.company.training.preview_component(data['session_id'],plan)
        if path == '/api/research/capabilities' and method == 'GET':
            from .capabilities import catalog
            return catalog()
        if path == '/api/research/autonomy' and method == 'POST':
            if data.get('confirmed') is not True:raise ValueError('需要用户明确确认研究授权或分支操作')
            args={k:v for k,v in data.items() if k not in {'action','confirmed','session_id'}}
            action=data['action'];sid=data['session_id']
            if action=='enable':return kernel.research.autonomy.enable(sid,**args)
            if action=='mode':return kernel.research.autonomy.mode(sid,**args)
            if action=='fork':
                if set(args)-{'from_plan_id','title','content','reason','activate','revision_scope'}:raise ValueError('人工界面分支不接受伪造的调用来源')
                return kernel.research.autonomy.fork(sid,**args)
            if action=='switch':return kernel.research.autonomy.switch(sid,**args)
            if action=='budget':
                with kernel.lock_for(sid):return kernel.research.change_budget(sid,**args)
            raise ValueError('未知研究策略操作')
        if path == '/api/research/method-upgrade' and method == 'POST':
            if data.get('confirmed') is not True:
                raise ValueError('需要明确确认当前项目研究方法升级')
            return kernel.upgrade_method(data['session_id'],data['expected_digest'],data['reason'],data.get('authority','用户界面显式研究方法升级'))
        if path == '/api/research/review' and method == 'POST':
            with kernel.lock_for(data['session_id']):
                if kernel.delivery_control.barrier(data['session_id']):
                    raise ValueError('原会话中断或投递待核对，暂不能审核。请从会话待办核对原状态。')
                result=kernel.research.review(data['session_id'],data)
                if data.get('continue') is True:
                    location={'lpai':'LPAI 组件库','lpai_devspace':'LPAI GPU 开发空间','local':'本地'}[data.get('execution','local')] if result['decision']=='accept' else None
                    source='用户委托开发助手审批' if data.get('actor')=='用户委托开发助手审批' else '来自用户界面操作'
                    text=(f"[方案审核事件 · {source}] 方案 {result['version']}（{result['plan_id']}）"
                          +(f'已通过审核。正式训练地点为 {location}，以此审批记录为准。请先读取research_status核对范围，再继续。' if result['decision']=='accept' else
                            '已退回，请结合以下理由修订，暂不执行实验。')+' 审核理由：'+data['reason'])
                    result['delivery']=kernel.send(data['session_id'],text,'queue','plan-review:'+result['review_id'],origin='review')['id']
            return result
        if path == '/api/research/round-stop' and method == 'POST':
            if data.get('confirmed') is not True:raise ValueError('需要人类明确审核结束建议')
            result=kernel.research.review_stop(data['session_id'],data['review_id'],data['decision'],data['reason'])
            if data['decision']=='reject_stop':
                text='[轮次结束审核事件 · 来自用户界面] 结束建议已退回。请结合理由完善本轮评审和下一方案，未经批准不开始下一轮。理由：'+data['reason']
                result['delivery']=kernel.send(data['session_id'],text,'queue','round-stop:'+result['decision_id'],origin='review')['id']
            return result
        if path == '/api/company/config' and method == 'POST':
            return kernel.company.configure(data)
        if path == '/api/company/check' and method == 'POST':
            return kernel.company.health.check(data['platform'],data.get('session_id'),data.get('component_uid'),data.get('project_id'))
        if path == '/api/company/lpai' and method == 'POST':
            return kernel.company.lpai(**data)
        if path == '/api/company/gitlab' and method == 'POST':
            return kernel.company.gitlab(data['operation'],data.get('project_id'))
        if path == '/api/company/context' and method == 'POST':
            return kernel.company.context(data['session_id'])
        if path == '/api/company/execution-location' and method == 'POST':
            from .execution_request import select
            return select(store,data['session_id'],data['execution'],data['reason'],data.get('source','用户界面选择'))
        if path == '/api/company/devspace-ssh' and method == 'POST':
            if data.get('auto') is True:
                from .ssh_discovery import configure_automatically
                return configure_automatically(kernel.company,data['session_id'],data['space_uid'])
            from .devspace import configure
            return configure(kernel.company,data['session_id'],data['space_uid'],data.get('command'),data.get('identity_path'))
        if path == '/api/company/refresh-space' and method == 'POST':
            return kernel.company.refresh_space(data['session_id'],data['uid'])
        if path == '/api/company/stop-space' and method == 'POST':
            if set(data)!={'session_id','uid','id','confirmed','expected_authority'}:
                raise ValueError('停止空间仅接受人类确认的本项目确切UID与连接摘要，不接收命令或训练取消参数')
            return kernel.company.stop_space(data['session_id'],data['uid'],data['id'],data['confirmed'],data['expected_authority'])
        if path == '/api/company/bind-space' and method == 'POST':
            return kernel.company.bind_space(data['session_id'],data['name'],data['uid'])
        if path == '/api/company/bind-repository' and method == 'POST':
            return kernel.company.bind_repository(data['session_id'],data['project_id'])
        if path == '/api/company/git' and method == 'POST':
            return kernel.company.git_read(data['session_id'],data['operation'])
        if path == '/api/company/action' and method == 'POST':
            if store.session(data['session_id'])['active_turn']:
                raise ValueError('请先暂停当前执行，避免工作区／云端操作冲突')
            if data.get('expected_authority')!=kernel.company.authority(data['session_id']):
                raise ValueError('连接或项目绑定已变化，请重新核对')
            return kernel.company.perform(data['session_id'],data['request'],data['id'],data.get('confirmed') is True)
        if path == '/api/company/watch' and method == 'POST':
            return kernel.company.watch(data['session_id'],data['uid'],data.get('auto_resume',False))
        if path == '/api/company/session-runs' and method == 'POST':
            return kernel.company.session_runs(data['session_id'],data.get('scope','session'))
        if path == '/api/company/watch-preference' and method == 'POST':
            return kernel.company.watch_preference(data['session_id'],data['watch_id'],data['auto_resume'])
        if path == '/api/company/diagnose' and method == 'POST':
            return kernel.company.training.diagnose(data['session_id'],data['uid'],data.get('refresh',False))
        if path == '/api/company/collect' and method == 'POST':
            if set(data)-{'session_id','uid','refresh'}:raise ValueError('回收只接受本会话原任务，不接受路径、命令或存储凭据')
            return kernel.company.outputs.collect(data['session_id'],data['uid'],data.get('refresh',False))
        if path == '/api/company/prepare' and method == 'POST':
            return kernel.company.training.prepare(data['session_id'],data['request'])
        if path == '/api/company/reconcile-training' and method == 'POST':
            return kernel.company.training.reconcile(data['session_id'],data['submission_id'],data['task_uid'])
        if path == '/api/company/recovery-origin' and method == 'POST':
            if set(data)!={'session_id','submission_id'}:raise ValueError('身份导出仅接受原会话/提交，不接收外部UID、授权或下载范围')
            return kernel.company.training.recovery_origin(data['session_id'],data['submission_id'])
        if path == '/api/company/unbind' and method == 'POST':
            return kernel.company.unbind(data['session_id'],data['kind'],data['uid'])
        if path == '/api/projects' and method == 'POST':
            return store.create_project(data.get('title', '新的研究'), data.get('workspace'))
        if path == '/api/sessions' and method == 'POST':
            return store.create_session(data['project_id'], kernel.settings.value['channel'], data.get('title', '新的对话'))
        if len(parts) >= 3 and parts[:2] == ['api', 'sessions']:
            session_id = parts[2]
            session = store.session(session_id)
            action = parts[3] if len(parts) > 3 else None
            if method=='GET' and action=='messages' and len(parts)==5:
                message=store.one('SELECT * FROM messages WHERE id=? AND session_id=?',(parts[4],session_id))
                if not message:raise ValueError('本会话没有该执行记录')
                return {'message':message}
            if method == 'GET' and action == 'deliveries' and len(parts) == 5:
                # Same ID inspection never reconnects, retries, or changes state.
                delivery = store.one('SELECT * FROM deliveries WHERE id=? AND session_id=?', (parts[4], session_id))
                if not delivery:
                    raise ValueError('本会话没有该投递记录')
                return delivery
            if method == 'POST' and action == 'deliveries' and len(parts) == 6 and parts[5] == 'pause':
                return kernel.pause_unknown_delivery(session_id, parts[4], data.get('confirmed'), data.get('reason'))
            if method == 'GET' and not action and len(parts) == 3:
                # Messages and their source-event cursor are one read snapshot.
                # A browser must not append deltas already included in this body.
                with store.lock:
                    session=store.session(session_id)
                    graph=store.graph(session['project_id'])
                    graph['execution_location']=kernel.research.workflow.location(session_id,graph)
                    # Native userMessage items also carry host-generated inputs. Use
                    # durable delivery provenance, including pre-dedup native echoes.
                    from .message_window import read as read_window
                    messages,history=read_window(store,session_id,before=query.get('before',[None])[0],start=query.get('history_start',[None])[0])
                    from .transcript import hide_host_echoes, handoff_note, finalization_note
                    messages=hide_host_echoes(store,session_id,messages)
                    for finalization in store.rows("""SELECT f.id,f.reason,d.created_at
                        FROM research_finalizations f JOIN deliveries d ON d.id=f.id AND d.session_id=f.session_id
                        WHERE f.session_id=? AND d.state='accepted'""",(session_id,)):
                        reason=finalization['reason']
                        body=finalization_note(reason)
                        if history['has_more'] and history['start_at'] and finalization['created_at']<history['start_at']:continue
                        if query.get('before') and (not history['end_at'] or finalization['created_at']>history['end_at']):continue
                        messages.append({'id':finalization['id'],'created_at':finalization['created_at'],
                                         'role':'system','kind':'research_finalization','text':body,'status':'completed'})
                    role_labels={'planner':'规划','generator':'方案改进','critic':'独立审议','executor':'执行'}
                    for handoff in store.rows('''SELECT m.id,m.created_at,h.reason,h.target,h.verdict,r.role,
                            p.version,st.stage FROM research_role_handoffs h
                        JOIN deliveries d ON d.id=h.delivery_id AND d.session_id=h.session_id
                        JOIN messages m ON m.session_id=h.session_id AND m.item_id='delivery:'||d.id
                        LEFT JOIN research_role_routes r ON r.delivery_id=h.source_delivery
                        LEFT JOIN records p ON p.id=h.plan_id
                        LEFT JOIN research_role_execution_stages st ON st.handoff_id=h.id
                        WHERE h.session_id=? AND d.state='accepted' ''',(session_id,)):
                        origin=role_labels.get(handoff['role'],'系统');target=role_labels.get(handoff['target'],'下一角色')
                        phase={'prepare':'实现准备','execute':'正式执行'}.get(handoff['stage']) if handoff['target']=='executor' else None
                        verdict={'approve':'审议通过','revise':'需要修订','needs-evidence':'需要补证'}.get(handoff['verdict'])
                        label=' · '.join(part for part in [f'{origin} → {target}',('方案 '+handoff['version']) if handoff['version'] else None,phase or verdict] if part)
                        reason=handoff_note(handoff)
                        if history['has_more'] and history['start_at'] and handoff['created_at']<history['start_at']:continue
                        if query.get('before') and (not history['end_at'] or handoff['created_at']>history['end_at']):continue
                        messages.append({'id':handoff['id'],'created_at':handoff['created_at'],'role':'system','kind':'role_handoff',
                                         'text':reason,'label':label,'status':'completed'})
                    result={"session": session, "messages": messages,'history':history,
                            "deliveries": store.rows("SELECT * FROM deliveries WHERE session_id=? AND state IN ('queued','requested','unknown','rejected') ORDER BY rowid", (session_id,)),
                            'delivery_controls': kernel.delivery_control.summary(session_id),
                            'delivery_origins': {row['delivery_id']: row['origin'] for row in store.rows('SELECT o.* FROM research_input_origins o JOIN deliveries d ON d.id=o.delivery_id WHERE d.session_id=?', (session_id,))},
                            "approvals": store.rows("SELECT * FROM approvals WHERE session_id=? AND state='pending' ORDER BY rowid", (session_id,)),
                            "draft": store.one("SELECT * FROM drafts WHERE session_id=?", (session_id,)) or {"text": "", "revision": 0},
                            "graph": graph,
                            "records": store.projected_records(session['project_id']),"research_lifecycle":kernel.research.workflow.publish(session_id)}
                    if store.one("SELECT name FROM sqlite_master WHERE name='dismissed_deliveries'"):
                        hidden={r['delivery_id'] for r in store.rows('SELECT delivery_id FROM dismissed_deliveries')}
                        result['deliveries']=[r for r in result['deliveries'] if r['id'] not in hidden]
                    result['cursor']=store.one('SELECT COALESCE(MAX(seq),0) AS seq FROM events')['seq']
                    return result
            if method == 'POST' and len(parts) == 4:
                if action == 'send':
                    try:
                        return kernel.send(session_id, data['text'], data.get('mode', 'queue'), data.get('id'))
                    except Exception as exc:
                        original_id=data.get('id')
                        if isinstance(original_id,str) and original_id:
                            with store.lock:
                                original=store.one('SELECT id,session_id,state FROM deliveries WHERE id=?',(original_id,))
                                exc.delivery=original or {'id':original_id,'session_id':session_id,'state':'not_registered'}
                        raise
                if action == 'interrupt':
                    return kernel.interrupt(session_id)
                if action == 'restore':
                    return kernel.restore(session_id)
                if action == 'resume-research':
                    return kernel.resume_research(session_id,data['request_id'],data['reason'],data.get('confirmed',False))
                if action == 'rename':
                    return store.rename_session(session_id,data['title'],data.get('expected_title'))
                if action == 'resume-system-recovery':
                    from .recovery import enable
                    return enable(store,session_id,data['authority_id'],data['reason'])
                if action == 'steer-queued':
                    return kernel.promote_delivery(session_id,data['id'])
                if action == 'dismiss-delivery':
                    return kernel.dismiss_delivery(session_id,data['id'])
                if action == 'cancel':
                    return kernel.cancel_delivery(session_id, data['id'])
                if action == 'draft':
                    return store.draft(session_id, data['text'], data['revision'])
                if action == 'archive':
                    from .deletion import archive
                    return archive(kernel,session['project_id'],session_id)
                if action == 'unarchive':return store.unarchive_session(session_id)
                if action == 'native':
                    allowed = {'skills/list', 'mcpServerStatus/list', 'thread/compact/start'}
                    if data['method'] not in allowed:
                        raise ValueError('不支持的界面操作')
                    if data['method'] == 'thread/compact/start':
                        return kernel.native_compact(session_id)
                    loaded = kernel.load_thread(session_id)
                    params = {'cwds': [session['workspace']],'forceReload':True} if data['method'] == 'skills/list' else {'threadId': loaded['thread_id']}
                    result=kernel.rpc.request(data['method'], params)
                    if data['method']=='skills/list':
                        from .skill_management import location
                        result={**result,'project_skill_directory':str(location(store,session_id))}
                    return result
                if action == 'import-skill':
                    from .skill_management import import_local
                    return import_local(store,session_id,data['source'])
                if action == 'disable-skill':
                    from .skill_management import disable
                    return disable(store,session_id,data['name'])
        if len(parts) == 4 and parts[:2] == ['api', 'approvals'] and parts[3] == 'answer' and method == 'POST':
            return kernel.answer(parts[2], data)
        if len(parts) == 3 and parts[:2] == ['api', 'records'] and method == 'GET':
            from .research_story import detail as story_detail
            if parts[2].startswith('story:'):
                note = story_detail(store, parts[2])
                if not note:raise ValueError('脉络注解不存在')
                return {'record_id':parts[2], 'project_id':note['project_id'], 'research_story':note,
                        'messages':[], 'evidence':[], 'session_id':None, 'plan_digest':None}
            if parts[2].startswith('series:'):
                series_id=parts[2][7:]
                members=store.rows('SELECT l.*,r.title,r.session_id,r.version FROM research_plan_lineage l JOIN records r ON r.id=l.plan_id WHERE l.series_id=? ORDER BY l.minor_number',(series_id,))
                if not members:raise ValueError('方案系列不存在')
                first=members[0]
                content=f"## 方案系列 {first['series_number']}\n\n同系列版本同层展示，各自实验归属具体方案。并列展示不代表同时执行，每个下一方案仍需用户审核。\n\n"
                content+='\n'.join(f"- **方案 {m['version']}**：{m['title']}" for m in members)
                if first['predecessor_series_id']:
                    content+='\n\n此系列承接旧系列的轮次评审形成，原路线与证据保留。\n\n'+first['reason']
                session = store.session(first['session_id'])
                return {'document': {'content': content}, 'record': {'分类依据': first['basis'], '历史保持': '原始方案、父关系、运行及审核状态均未改写'},
                        'messages': [], 'session_id': first['session_id'], 'record_id': parts[2], 'project_id': session['project_id'], 'plan_digest': None}
            record = store.one('SELECT * FROM records WHERE id=?', (parts[2],))
            if not record:
                raise ValueError('研究记录不存在')
            projection=store.record_statuses(record['project_id']).get(record['id'],{})
            source=json.loads(record['source'])
            readable={'记录来源':'Codex原生工具记录' if source.get('call_id') else source.get('origin','持久研究记录'),'版本':record['version'],
                    "保存时状态":record['status'],"当前状态":projection.get('status','未建立独立执行状态'),
                    "状态依据":{'exact_plan_review':'具体版本审核','exact_run_link':'明确关联的运行收据','program_checked_research_delegation':'程序核对用户研究授权，非逐版人工审核'}.get(projection.get('source'),'保存时记录'),
                    "说明":"正文保持形成时原文，当前状态由实际审核或运行投影，不改写历史"}
            if source.get('reviewed_plan_id'):
                basis=store.one('SELECT title,version FROM records WHERE id=?',(source['reviewed_plan_id'],))
                readable.update(评审方案=f"{basis['title']} · {basis['version']}",本轮尝试数量=len(source.get('indexed_runs',[])),明确引用运行数=len(source.get('claimed_evidence_run_ids',[])))
            if source.get('previous_plan_id'):
                basis=store.one('SELECT title,version FROM records WHERE id=?',(source['previous_plan_id'],))
                readable['承接前轮方案']=f"{basis['title']} · {basis['version']}"
            lineage=store.one('SELECT * FROM research_plan_lineage WHERE plan_id=?',(record['id'],))
            if lineage:
                readable.update(方案系列=f"系列 {lineage['series_number']}",修订范围={'major':'总体路线调整','incremental':'同系列增量修订','initial':'初始方案'}.get(lineage['revision_scope']),修订依据=lineage['reason'] or '见所引用的前轮评审及方案正文')
                triggers=[store.one('SELECT title,version FROM records WHERE id=?',(ref,)) for ref in json.loads(lineage['trigger_record_ids'])]
                readable['促成修订的记录']=[f"{r['title']}"+(f" · {r['version']}" if r['version'] else '') for r in triggers if r]
            graph_node=next((n for n in store.graph(record['project_id'])['nodes'] if n['id']==record['id']),None)
            if graph_node and graph_node.get('membership_notice'):
                readable['归属提示']=graph_node['membership_notice']
            if graph_node and graph_node.get('plan_id') and graph_node['plan_id']!=record['id']:
                owner=store.one('SELECT title,version FROM records WHERE id=?',(graph_node['plan_id'],))
                readable['归属方案']=f"{owner['version']} · {owner['title']}"
                readable['归属依据']='明确方案或运行关联' if graph_node['membership_basis']!='legacy_recorded_ancestor' else '历史父记录追溯；不据此推断科学因果'
            from .rounds import closure_details
            closure=closure_details(store,record) if record['kind']=='review' else None
            if closure:
                readable['结束原因']={'target_achieved':'目标评审完成','budget':'预算受限','no_progress':'无有效进展',
                    'blocked':'证据或权限受限','user_stop':'按用户要求停止','legacy_unspecified':'历史原因未记录'}[closure['stop_reason']]
                readable['收束方式']='自主评审收束，无需人工确认' if not closure['requires_human_confirmation'] else '结束建议需人工审核'
                readable['数值目标核验']='通过事前固定的数值条件' if closure['target_verified'] else '未由数值核验认证，不等于研究性能达标'
            runs=[]
            for run in store.rows('SELECT r.* FROM research_runs r WHERE r.id IN (SELECT run_id FROM research_run_context WHERE trial_id=? UNION SELECT run_id FROM research_record_runs WHERE record_id=?) ORDER BY r.rowid',(record['id'],record['id'])):
                receipt=json.loads(run['receipt'] or '{}');request=json.loads(run['request'])
                runs.append({'status':run['state'],'created_at':run['created_at'],
                    '运行类型':'训练拟合' if request.get('purpose')=='fit' else '无拟合评估/诊断',
                    '实验问题':request.get('question'),'预期信息':request.get('expected_information'),
                    '运行耗时秒':receipt.get('seconds'),'产物目录':run['directory'],
                    '产物文件':[item['path'] for item in receipt.get('outputs',[])]})
            document_content=record['content']
            if record['kind']=='trial':
                from .execution_history import index as execution_index,diagnostics
                from .task_outputs import status as output_status
                report_links=[]
                history=execution_index(store,record['project_id'])
                root_trial=history['roots'].get(record['id'],record['id'])
                for attempt in history['groups'].get(root_trial,[]):
                    archive=output_status(store,attempt['session_id'],attempt['task_uid']) if attempt['task_uid'] else None
                    report_path=(archive or {}).get('report_path')
                    if report_path and re.fullmatch(r'research/lpai-outputs/[\w-]+/[\w-]+/attempt-\d+/REPORT\.md',report_path):
                        report_links.append(f"- [{attempt['task_uid']} 的产物报告]({report_path})")
                    runs.append({'status':attempt['state'],'created_at':attempt['created_at'],
                                 '任务 UID':attempt['task_uid'],'前次任务 UID':attempt['predecessor_uid'],
                                 '修复内容':attempt['change_summary'],
                                 '修复验证':attempt.get('validation'),
                                 '原实验记录':attempt['trial_id'],'原方案记录':attempt['plan_id'],
                                 '失败取证':diagnostics(store,attempt['session_id'],attempt['task_uid']),
                                 '产物归档':(archive or {}).get('report_path'),
                                 '产物状态':(archive or {}).get('state')})
                runs.sort(key=lambda run:run['created_at'])
                if report_links:
                    document_content+='\n\n## 产物报告\n\n'+'\n'.join(report_links)
            from .research import plan_digest
            from .study_integrity import describe as describe_study
            return {'record_id': record['id'], 'project_id': record['project_id'],
                    'plan_digest': plan_digest(record) if record['kind'] == 'plan' else None,
                    "document": {"content": document_content}, "record":readable,'closure':closure,
                    'runs':runs,
                    'research_story':story_detail(store, record['id']),
                    'study_integrity':describe_study(record),
                    "evidence": json.loads(record['evidence']),
                    "integrity":kernel.research.evidence_integrity(record) if record['kind']=='plan' else None,
                    "messages": [], "session_id": record['session_id']}
        if len(parts) == 4 and parts[:2] == ['api', 'projects']:
            project_id, action = parts[2:]
            if action == 'research-story':
                from .research_story import load as load_story, save as save_story
                if method == 'GET':return {'story':load_story(store, project_id)}
                if method == 'POST':return save_story(store, project_id, data)
            if action=='manage' and method=='POST':
                operation=data.get('action')
                if operation in {'pin','rename'}:return store.update_project(project_id,operation,data.get('pinned') if operation=='pin' else data.get('title'),data.get('expected_title'))
                if operation in {'remove','archive_chats'}:
                    if data.get('confirmed') is not True:raise ValueError('请先确认项目操作')
                    from .deletion import archive,remove
                    return remove(kernel,'project',project_id) if operation=='remove' else archive(kernel,project_id)
                raise ValueError('不支持的项目操作')
            if action == 'files' and method == 'GET':
                return self.files(project_id, query.get('path', [''])[0])
            if action == 'file' and method == 'GET':
                path = store.evidence_path(project_id, query.get('path', [''])[0])
                if not path.is_file():
                    raise ValueError('文件不存在')
                content = path.read_bytes()
                try:
                    text = content.decode('utf-8')
                except UnicodeDecodeError:
                    return {'binary': True, 'name': path.name, 'size': path.stat().st_size}
                return {'text': text, 'name': path.name, 'size': len(content)}
            if action == 'upload' and method == 'POST':
                name = Path(data['name'].replace('\\', '/')).name
                if not name or name in {'.', '..'}:
                    raise ValueError('无效文件名')
                content = base64.b64decode(data['content'], validate=True)
                if len(content) > 16 * 1024 * 1024:
                    raise ValueError('本版本单个附件上限16 MiB')
                relative = f"attachments/{uid()[:8]}-{name}"
                path = store.safe_path(project_id, relative)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
                return {'path': relative, 'name': name, 'size': len(content)}
        raise ValueError('接口不存在')

    def files(self, project_id, relative):
        root = self.store.safe_path(project_id, relative)
        if not root.is_dir():
            raise ValueError('目录不存在')
        ignored = {'.git', '.venv', 'node_modules', '__pycache__', '.codex', '.aws', '.ssh'}
        output = []
        for item in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if item.name in ignored or item.is_symlink():
                continue
            path = (Path(relative) / item.name).as_posix()
            output.append({'name': item.name, 'path': path, 'directory': item.is_dir(), 'size': item.stat().st_size if item.is_file() else None})
            if len(output) >= 400:
                break
        return {'items': output, 'path': relative}


class Handler(BaseHTTPRequestHandler):
    server_version = 'IterLabCodex/0.1'
    protocol_version = 'HTTP/1.1'

    @property
    def app(self):
        return self.server.app

    def log_message(self, format, *args):
        # Do not put chat contents, query strings, or approval bodies in logs.
        pass

    def do_GET(self):
        self.handle_request('GET')

    def do_POST(self):
        self.handle_request('POST')

    def reply(self, status, content, mime='application/json; charset=utf-8'):
        body = content.encode('utf-8') if isinstance(content, str) else content
        immutable=status==200 and urlparse(self.path).path.startswith('/assets/')
        accepted=any(part.strip().split(';')[0].strip().lower()=='gzip' and
                     not re.search(r';\s*q=0(?:\.0*)?(?:\s|;|$)',part)
                     for part in self.headers.get('Accept-Encoding','').split(','))
        encoded=None
        if accepted and status==200:
            if immutable:encoded=self.app.assets.gzip(urlparse(self.path).path)
            elif mime.startswith('application/json') and len(body)>32768:encoded=gzip.compress(body,compresslevel=1,mtime=0)
        if encoded is not None:body=encoded
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'public, max-age=31536000, immutable' if immutable else 'no-store')
        self.send_header('Vary','Accept-Encoding')
        if encoded is not None:self.send_header('Content-Encoding','gzip')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def handle_request(self, method):
        parsed = urlparse(self.path)
        path, query = unquote(parsed.path), parse_qs(parsed.query)
        allowed_hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if self.headers.get('Host') not in allowed_hosts:
            self.reply(403, dumps({'error': 'Host rejected'}))
            return
        origin = self.headers.get('Origin')
        if origin and origin not in {f'http://{host}' for host in allowed_hosts}:
            self.reply(403, dumps({'error': 'Origin rejected'}))
            return
        try:
            if path.startswith('/api/'):
                if not secrets.compare_digest(self.headers.get('X-Workbench-Token', ''), self.app.token):
                    self.reply(403, dumps({'error': '请刷新页面重新连接本地服务'}))
                    return
                revision=self.headers.get('X-Workbench-Assets')
                if revision and revision!=self.app.assets.revision:
                    self.close_connection=True # Do not reuse a connection with an unread POST body.
                    self.reply(409,dumps({'error':'页面与服务版本不一致，请刷新原页面；草稿和研究记录保留。','code':'client_version_mismatch'}))
                    return
                if path == '/api/service/stop' and method == 'POST':
                    if self.app.store.one("SELECT id FROM research_runs WHERE state IN ('requested','running') LIMIT 1"):
                        self.close_connection=True # The rejected POST body has not been consumed.
                        self.reply(409,dumps({'error':'本地计算仍在运行；普通服务停止不会替你取消任务，请等待完成或明确处置该任务。'}))
                        return
                    self.reply(200, dumps({'stopping': True}))
                    threading.Thread(target=self.server.shutdown, name='workbench-shutdown', daemon=True).start()
                    return
                if path == '/api/events' and method == 'GET':
                    self.stream_events(int(query.get('after', ['0'])[0]))
                    return
                data = {}
                if method == 'POST':
                    length = int(self.headers.get('Content-Length', '0'))
                    if length > 24 * 1024 * 1024:
                        raise ValueError('请求过大')
                    data = json.loads(self.rfile.read(length) or '{}')
                result = self.app.route(method, path, query, data)
                self.reply(200, dumps(result))
                return
            if method != 'GET':
                raise ValueError('不支持的请求')
            if path == '/':
                html = self.app.assets.html(self.app.token)
                self.reply(200, html, 'text/html; charset=utf-8')
            else:
                content=self.app.assets.read(path)
                if content is None:
                    self.reply(404, 'Not found', 'text/plain')
                    return
                self.reply(200, content, mimetypes.guess_type(path)[0] or 'application/octet-stream')
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        except (ValueError, KeyError, TypeError) as exc:
            self.reply(400, dumps({'error': str(exc),**({'delivery':exc.delivery} if hasattr(exc,'delivery') else {})}))
        except Exception as exc:
            self.reply(500, dumps({'error': str(exc)[:1500],**({'delivery':exc.delivery} if hasattr(exc,'delivery') else {})}))

    def stream_events(self, after):
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        deadline = time.monotonic() + 50
        while not self.app.closed and time.monotonic() < deadline:
            # Large native tool results are durable evidence, not live UI text.
            # Project in SQL so one historical 80 MiB output cannot flood a tab.
            rows = self.app.store.rows('''SELECT seq,session_id,kind,created_at,
                CASE WHEN length(payload)>65536 THEN '{"details_deferred":true}' ELSE payload END AS payload
                FROM events WHERE seq>? ORDER BY seq LIMIT 200''', (after,))
            if rows:
                for row in rows:
                    row['payload'] = json.loads(row['payload'])
                    self.wfile.write(('data: ' + dumps(row) + '\n\n').encode('utf-8'))
                    after = row['seq']
                self.wfile.flush()
            else:
                self.wfile.write(b': keepalive\n\n')
                self.wfile.flush()
                with self.app.store.changed:
                    self.app.store.changed.wait(timeout=8)


def serve(data_root, port=8781):
    app = Application(data_root)
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.app = app
    # Explicitly owned service identity, with no credentials in the receipt.
    identity = {'pid': os.getpid(), 'port': port, 'data': str(app.store.root), 'runtime': 'codex-app-server-stdio'}
    (app.store.root / 'service.json').write_text(json.dumps(identity, indent=2), encoding='utf-8')
    print(f'IterLab Codex: http://127.0.0.1:{port}', flush=True)
    try:
        server.serve_forever(poll_interval=.5)
    finally:
        server.server_close()
        app.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8781)
    parser.add_argument('--data', default=os.environ.get('ITERLAB_CODEX_DATA', str(ROOT / '.data')))
    args = parser.parse_args()
    serve(args.data, args.port)


if __name__ == '__main__':
    main()
