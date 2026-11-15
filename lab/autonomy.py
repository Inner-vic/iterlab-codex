"""Research delegation, active branches and durable continuation identities.

No model client lives here. A plan's scientific merit remains Codex's decision;
scope identity, evidence, resource limits and execution ownership are checked here.
"""
import hashlib
import json

from .store import dumps,now,uid


def installed(store):
    return bool(store.one("SELECT name FROM sqlite_master WHERE name='research_autonomy_policies'"))


def policy(store,pid):
    return store.one('SELECT * FROM research_autonomy_policies WHERE project_id=?',(pid,)) if installed(store) else None


def active(store,pid):
    return store.one('''SELECT b.* FROM research_active_branches a JOIN research_branches b ON b.id=a.branch_id
                        WHERE a.project_id=?''',(pid,)) if installed(store) else None


def admitted(store,plan_id):
    return store.one('SELECT * FROM research_plan_admissions WHERE plan_id=?',(plan_id,)) if installed(store) else None


def finalizing(store,project_id):
    """A reserved reporting identity stops this authorized plan, even after takeover."""
    if not store.one("SELECT name FROM sqlite_master WHERE name='research_finalizations'"):return None
    return store.one('''SELECT f.* FROM research_finalizations f
        JOIN research_autonomy_policies p ON p.project_id=? AND p.authority_id=f.authority_id
        JOIN research_active_branches a ON a.project_id=p.project_id AND a.branch_id=f.branch_id
        JOIN research_branches b ON b.id=a.branch_id AND b.head_plan_id=f.plan_id
        WHERE f.reason<>'budget_pause'
        ORDER BY f.rowid LIMIT 1''',(project_id,))


def execution_grant(store,plan):
    from .research import plan_digest
    from .budgets import effective
    branch=active(store,plan['project_id'])
    if branch and branch['head_plan_id']!=plan['id']:raise ValueError('方案不在当前活动分支，不能执行其他分支的计算')
    grant=store.one('SELECT * FROM research_grants WHERE plan_id=? ORDER BY rowid DESC LIMIT 1',(plan['id'],))
    review=store.one('SELECT decision FROM research_reviews WHERE plan_id=? ORDER BY rowid DESC LIMIT 1',(plan['id'],))
    if grant and review and review['decision']=='accept' and grant['plan_digest']==plan_digest(plan):return effective(store,grant)
    delegated=Autonomy.permission(store,plan)
    if delegated:return effective(store,delegated)
    raise ValueError('该具体方案尚未通过有效审核或未被采纳；请核对具体授权，不能自批或绕过')


class Autonomy:
    def __init__(self,research):
        self.research=research;self.store=research.store
        with self.store.lock,self.store.db:
            self.store.db.executescript('''
            CREATE TABLE IF NOT EXISTS research_authorities(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,
              grant_id TEXT NOT NULL REFERENCES research_grants(id),scope TEXT NOT NULL,scope_digest TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS research_autonomy_policies(project_id TEXT PRIMARY KEY,authority_id TEXT NOT NULL REFERENCES research_authorities(id),
              mode TEXT NOT NULL,owner_session_id TEXT NOT NULL,reason TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS research_branches(id TEXT PRIMARY KEY,project_id TEXT NOT NULL,authority_id TEXT NOT NULL,
              root_plan_id TEXT NOT NULL REFERENCES records(id),head_plan_id TEXT NOT NULL REFERENCES records(id),
              parent_branch_id TEXT,fork_plan_id TEXT,origin TEXT NOT NULL,reason TEXT NOT NULL,source_delivery_id TEXT,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS research_active_branches(project_id TEXT PRIMARY KEY,branch_id TEXT NOT NULL REFERENCES research_branches(id),updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS research_plan_admissions(plan_id TEXT PRIMARY KEY REFERENCES records(id),authority_id TEXT NOT NULL,
              branch_id TEXT NOT NULL REFERENCES research_branches(id),plan_digest TEXT NOT NULL,scope_digest TEXT NOT NULL,reason TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS research_continuations(id TEXT PRIMARY KEY,session_id TEXT NOT NULL,signature TEXT NOT NULL,
              delivery_id TEXT NOT NULL,state TEXT NOT NULL,created_at TEXT NOT NULL,UNIQUE(session_id,signature));
            CREATE TABLE IF NOT EXISTS research_delivery_policies(delivery_id TEXT PRIMARY KEY REFERENCES deliveries(id),
              authority_id TEXT NOT NULL,suffix TEXT NOT NULL,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS research_scheduler_status(session_id TEXT PRIMARY KEY,state TEXT NOT NULL,
              reason TEXT NOT NULL,next_check REAL NOT NULL,failures INTEGER NOT NULL,updated_at TEXT NOT NULL);
            INSERT OR IGNORE INTO schema_versions VALUES(11,datetime('now'));
            ''')

    def quiet(self,pid):
        if self.store.one("SELECT id FROM sessions WHERE project_id=? AND (active_turn IS NOT NULL OR status IN ('starting','running','compacting','waiting_approval','unknown','disconnected'))",(pid,)):
            raise ValueError('先暂停或核对当前原生执行，再切换研究策略／分支')
        self.unresolved(pid)

    def unresolved(self,pid):
        if self.store.one("SELECT a.id FROM approvals a JOIN sessions s ON s.id=a.session_id WHERE s.project_id=? AND a.state='pending'",(pid,)):
            raise ValueError('项目有待确认的原生操作，不能通过分支切换覆盖审批')
        if self.store.one("SELECT r.id FROM deliveries r JOIN sessions s ON s.id=r.session_id WHERE s.project_id=? AND r.state IN ('requested','unknown')",(pid,)):
            raise ValueError('项目有未知投递，先核对原身份')
        if self.store.one("SELECT id FROM research_runs WHERE project_id=? AND state IN ('requested','running','unknown')",(pid,)):
            raise ValueError('项目有在途/未知计算，先核对原身份')
        if self.store.one("SELECT name FROM sqlite_master WHERE name='company_training'"):
            from .rounds import pending_cloud,pending_platform_operation
            if pending_platform_operation(self.store,pid):
                raise ValueError('项目有未决平台操作，先核对原身份')
            if pending_cloud(self.store,pid):
                raise ValueError('项目有在途/未知云任务，先核对原身份')
            if self.store.one("SELECT w.id FROM company_watches w JOIN sessions s ON s.id=w.session_id WHERE s.project_id=? AND lower(w.state) NOT IN ('succeeded','completed','success','failed','stopped','terminated','cancelled','canceled','error')",(pid,)):
                raise ValueError('项目有已绑定的在途外部任务，不能切换后掩盖原任务')

    def normalize_scope(self,sid,plan,scope):
        if not isinstance(scope,dict) or set(scope)-{'objective','evaluation','data_boundary','allowed_changes','frozen_files'}:
            raise ValueError('研究范围需要目标、固定评价协议、数据边界、允许修改及可选固定文件')
        value={}
        for name in ['objective','evaluation','data_boundary','allowed_changes']:
            text=scope.get(name)
            if not isinstance(text,str) or not 1<=len(text.strip())<=6000:raise ValueError('请明确填写研究目标、评价协议、数据边界和允许调整范围')
            value[name]=text.strip()
        files=scope.get('frozen_files',[])
        if not isinstance(files,list) or len(files)>30 or any(not isinstance(p,str) for p in files):raise ValueError('固定文件需为项目内路径数组')
        refs={r['path']:r for r in json.loads(plan['evidence'])}
        if any(p not in refs for p in files):raise ValueError('固定文件必须已纳入初始具体方案的冻结证据')
        value['frozen_files']=[refs[p] for p in dict.fromkeys(files)]
        from .evaluation_contract import require_scope_files
        require_scope_files(self.store,plan,scope)
        value['channel']=self.store.session(sid)['channel']
        from .settings import Settings
        value['model_profile']=Settings(self.store.root).value['profile']
        return value

    def enable(self,sid,plan_id,expected_digest,scope,reason):
        from .research import plan_digest
        from . import rounds
        plan=self.research.plan(sid,plan_id);pid=plan['project_id']
        if not plan['version']:
            raise ValueError('候选方案须先通过 Critic 发版审查')
        with self.research.lock,self.store.lock,self.store.db:
            self.quiet(pid)
            if not str(reason).strip():raise ValueError('启用自主研究需明确授权理由')
            if plan_digest(plan)!=expected_digest:raise ValueError('初始方案版本已变化，请重新核对')
            self.research.check_plan_evidence(plan)
            current=rounds.approved_plan(self.store,pid)
            latest=self.store.one('SELECT plan_id,decision FROM research_reviews WHERE project_id=? ORDER BY rowid DESC LIMIT 1',(pid,))
            review=self.store.one('SELECT decision FROM research_reviews WHERE plan_id=? ORDER BY rowid DESC LIMIT 1',(plan_id,))
            grant=self.store.one('SELECT * FROM research_grants WHERE plan_id=? ORDER BY rowid DESC LIMIT 1',(plan_id,))
            if not latest or latest['plan_id']!=plan_id or not grant or grant['plan_digest']!=expected_digest or not review or review['decision']!='accept':raise ValueError('先明确审核当前初始方案与运行范围，不能借新模式批准旧待审方案')
            previous_policy=policy(self.store,pid);previous_branch=active(self.store,pid)
            if previous_policy:
                previous_authority=self.store.one('SELECT grant_id FROM research_authorities WHERE id=?',(previous_policy['authority_id'],))
                if previous_authority['grant_id']==grant['id']:raise ValueError('此授权已登记；可暂停或恢复，不覆盖原边界。改变范围需先明确审核新方案/额度')
            normalized=self.normalize_scope(sid,plan,scope)
            scope_digest=hashlib.sha256(dumps(normalized).encode()).hexdigest();authority_id=uid();branch_id=uid()
            self.store.db.execute('INSERT INTO research_authorities VALUES(?,?,?,?,?,?)',(authority_id,pid,grant['id'],dumps(normalized),scope_digest,now()))
            self.store.db.execute('INSERT INTO research_branches VALUES(?,?,?,?,?,?,?,?,?,?,?)',(branch_id,pid,authority_id,plan_id,plan_id,previous_branch['id'] if previous_branch else None,previous_branch['head_plan_id'] if previous_branch else None,'initial',reason,None,now()))
            self.store.db.execute('INSERT INTO research_active_branches VALUES(?,?,?) ON CONFLICT(project_id) DO UPDATE SET branch_id=excluded.branch_id,updated_at=excluded.updated_at',(pid,branch_id,now()))
            self.store.db.execute('INSERT INTO research_autonomy_policies VALUES(?,?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET authority_id=excluded.authority_id,mode=excluded.mode,owner_session_id=excluded.owner_session_id,reason=excluded.reason,updated_at=excluded.updated_at',(pid,authority_id,'autonomous',sid,reason,now()))
            self.store.db.execute('UPDATE research_controls SET paused=0,reason=?,updated_at=? WHERE session_id=?',('用户明确启用新的研究授权',now(),sid))
            self.store.db.execute("UPDATE deliveries SET state='cancelled',updated_at=? WHERE session_id=? AND state='queued' AND id LIKE 'research-continue:%'",(now(),sid))
        self.store.event(sid,'research.autonomy_enabled',{'authority_id':authority_id,'plan_id':plan_id,'source':'用户确认研究授权','scope_digest':scope_digest})
        if self.store.one("SELECT name FROM sqlite_master WHERE name='research_recovery_authorities'"):
            from .recovery import enable
            enable(self.store,sid,authority_id,'首次自主授权包含系统无进展后的独立修复审议；不扩展原范围')
        return self.status(sid)

    def status(self,sid):
        pid=self.store.session(sid)['project_id'];p=policy(self.store,pid)
        if not p:return {'mode':'manual','meaning':'原逐版人工审核；未静默启用自主执行','branches':[]}
        authority=self.store.one('SELECT * FROM research_authorities WHERE id=?',(p['authority_id'],))
        return {**p,'scope':json.loads(authority['scope']),'scope_digest':authority['scope_digest'],
                'active_branch':active(self.store,pid),'branches':self.store.rows('SELECT * FROM research_branches WHERE project_id=? ORDER BY rowid',(pid,)),
                'last_continuation':self.store.one('SELECT state,created_at FROM research_continuations WHERE session_id=? ORDER BY rowid DESC LIMIT 1',(sid,)),
                'scheduler':self.store.one('SELECT state,reason,updated_at FROM research_scheduler_status WHERE session_id=?',(sid,)),
                'admissions':self.store.rows('SELECT * FROM research_plan_admissions WHERE authority_id=? ORDER BY rowid DESC LIMIT 12',(authority['id'],))}

    def mode(self,sid,mode,reason):
        if mode not in {'manual','autonomous'} or not str(reason).strip():raise ValueError('需要明确研究模式与理由')
        session=self.store.session(sid);p=policy(self.store,session['project_id'])
        if not p:raise ValueError('项目尚未登记自主研究授权')
        with self.research.lock,self.store.lock,self.store.db:
            self.quiet(session['project_id'])
            self.store.db.execute('UPDATE research_autonomy_policies SET mode=?,owner_session_id=?,reason=?,updated_at=? WHERE project_id=?',(mode,sid,reason,now(),session['project_id']))
            if mode=='autonomous':self.store.db.execute('UPDATE research_controls SET paused=0,reason=?,updated_at=? WHERE session_id=?',('用户明确恢复自主研究',now(),sid))
            else:self.store.db.execute("UPDATE deliveries SET state='cancelled',updated_at=? WHERE session_id=? AND state='queued' AND id LIKE 'research-continue:%'",(now(),sid))
            self.store.db.execute('UPDATE research_scheduler_status SET next_check=0,failures=0 WHERE session_id=?',(sid,))
        self.store.event(sid,'research.autonomy_changed',{'mode':mode,'reason':reason})
        return self.status(sid)

    def check_continuation(self,sid,settings,authority_id=None,*,reporting=False,recovering=False):
        """A saved automatic handoff is work, never a fresh authorization."""
        session=self.store.session(sid);p=policy(self.store,session['project_id'])
        if (not p or p['mode']!='autonomous' or p['owner_session_id']!=sid
                or authority_id and p['authority_id']!=authority_id):
            raise ValueError('原自动交接的研究授权已停用或变更，保留交接，不自动投递')
        if self.research.workflow.paused(sid):raise ValueError('研究已暂停，不自动续接角色')
        authority=self.store.one('SELECT * FROM research_authorities WHERE id=?',(p['authority_id'],))
        scope=json.loads(authority['scope'])
        if (session['channel']!=settings['channel'] or session['channel']!=scope['channel']
                or settings['profile']!=scope['model_profile']):
            raise ValueError('原自动交接的模型通道／profile已改变，未切换模型或继续投递')
        from . import rounds
        current=rounds.state(self.store,session['project_id'])
        if current and current['state'] in {'assessment_complete','settled','waiting_stop_review'}:
            from .recovery import context
            if not recovering or not context(self.store,session['project_id']):
                raise ValueError('研究已收束或等待结束审核，不能自动交接或重启旧方案')
        branch=active(self.store,session['project_id'])
        plan=self.research.plan(sid,branch['head_plan_id'])
        self.research.execution_grant(plan)
        # Only an exact, restricted reporting delivery may describe lost or
        # changed evidence. Normal continuation still requires frozen bytes.
        if not reporting:self.research.check_plan_evidence(plan)
        return branch

    @staticmethod
    def permission(store,plan,require_active=True):
        """Resolve an exact delegated version to the original cumulative grant."""
        from .research import plan_digest
        admission=admitted(store,plan['id'])
        if not admission:return None
        from . import rounds
        source=json.loads(plan['source'])
        parent=source.get('fork_from_plan_id') if source.get('origin')=='human_intervention' else None
        closed=rounds.round_review(store,plan['project_id'],parent) if parent else None
        if (closed and json.loads(closed['source']).get('round_outcome')=='stop_requested'
                and closed['created_at']<=admission['created_at']):
            raise ValueError('人工分支在原轮次收束后继承的旧采纳无效；保留收据，须正式审核新范围')
        p=policy(store,plan['project_id']);branch=active(store,plan['project_id'])
        if not p or p['mode']!='autonomous' or p['authority_id']!=admission['authority_id']:raise ValueError('自主授权已停用或变更，不能沿用旧采纳')
        if require_active and (not branch or branch['head_plan_id']!=plan['id'] or branch['id']!=admission['branch_id']):raise ValueError('此方案不是活动研究分支，不能启动计算')
        if admission['plan_digest']!=plan_digest(plan):raise ValueError('已采纳的具体方案证据或正文已变化')
        authority=store.one('SELECT * FROM research_authorities WHERE id=?',(admission['authority_id'],))
        review=store.one('SELECT v.decision FROM research_reviews v JOIN research_grants g ON g.review_id=v.id WHERE g.id=?',(authority['grant_id'],))
        root=store.one('SELECT plan_id FROM research_grants WHERE id=?',(authority['grant_id'],))
        latest_root=store.one('SELECT decision FROM research_reviews WHERE plan_id=? ORDER BY rowid DESC LIMIT 1',(root['plan_id'],))
        explicit=store.one('SELECT decision FROM research_reviews WHERE plan_id=? ORDER BY rowid DESC LIMIT 1',(plan['id'],))
        if not review or review['decision']!='accept' or not latest_root or latest_root['decision']!='accept' or explicit and explicit['decision']=='reject':raise ValueError('原授权或具体方案已被退回，不能自动继续')
        return store.one('SELECT * FROM research_grants WHERE id=?',(authority['grant_id'],))

    def adopt(self,sid,plan_id,expected_digest,reason,branch_id=None):
        from .research import plan_digest
        from . import rounds
        plan=self.research.plan(sid,plan_id);pid=plan['project_id']
        if not plan['version']:
            raise ValueError('候选方案须先通过 Critic 发版审查')
        with self.research.lock,self.store.lock,self.store.db:
            p=policy(self.store,pid);current=active(self.store,pid)
            if not p or p['mode']!='autonomous' or p['owner_session_id']!=sid:raise ValueError('本会话未获自主研究授权')
            if self.research.workflow.paused(sid):raise ValueError('研究已暂停，不能自主采纳')
            repair=None
            from .recovery import context as recovery_context
            if finalizing(self.store,pid) or recovery_context(self.store,pid):
                from .recovery import require_critic
                repair=require_critic(self.research,sid,plan)
            self.unresolved(pid)
            from .execution_request import status as execution_request
            selected=execution_request(self.store,pid)
            authority_route=self.store.one('SELECT g.execution FROM research_authorities a JOIN research_grants g ON g.id=a.grant_id WHERE a.id=?',(p['authority_id'],))
            if selected and authority_route and selected['execution']!=authority_route['execution']:
                raise ValueError('用户选择了新的执行位置 '+selected['execution']+'，原授权仍是 '+authority_route['execution']+'。这是真实的新执行范围，必须保留完整scope_proposal并走首次正式方案审核；Critic批准不代替该审核，不得省略scope_proposal或改成继承旧授权来绕过，也不要为此新建元信息版本。旧累计与任务保持。')
            from .task_outputs import require_collected
            require_collected(self.store,pid,registered_only=True)
            if not str(reason).strip():raise ValueError('需要具体采纳理由')
            if plan_digest(plan)!=expected_digest:
                raise ValueError('方案摘要与当前确切版本不符；读取research_snapshot(record_id=plan_id)的plan_digest。Critic发布编号后摘要会更新，不使用文件SHA、scope_digest或发版前摘要，也不要手工重算')
            self.research.check_plan_evidence(plan)
            source=json.loads(plan['source'])
            fork_parent=source.get('fork_from_plan_id') if source.get('origin')=='human_intervention' else None
            fork_review=rounds.round_review(self.store,pid,fork_parent) if fork_parent else None
            if fork_review and json.loads(fork_review['source']).get('round_outcome')=='stop_requested':
                raise ValueError('原轮次已收束，人工分支不能继承旧授权重新开启；请保存新scope_proposal并正式审核新范围，历史保留')
            prior=admitted(self.store,plan_id)
            if prior:
                self.permission(self.store,plan);return {'adopted':True,'plan_id':plan_id,'reused':True}
            source=json.loads(plan['source']);authority=self.store.one('SELECT * FROM research_authorities WHERE id=?',(p['authority_id'],))
            initial=self.store.one('SELECT * FROM research_grants WHERE id=?',(authority['grant_id'],))
            if initial and initial['plan_id']==plan_id and current['head_plan_id']==plan_id and current['authority_id']==authority['id']:
                # Formal first approval already activated this exact root. Its
                # historical source predates the newly issued authority IDs;
                # do not ask the model to rewrite history or publish a copy.
                if branch_id is not None and branch_id!=current['id']:
                    raise ValueError('当前正式初始方案不属于该探索分支')
                if rounds.round_review(self.store,pid,plan_id):
                    raise ValueError('当前初始方案轮次已评审，不能通过重复采纳重新开启')
                self.research.execution_grant(plan)
                return {'adopted':True,'plan_id':plan_id,'branch_id':current['id'],'authority_id':authority['id'],
                    'reused':True,'source':'formal_initial_approval','budget_reset':False}
            original_plan=self.store.one('SELECT source FROM records WHERE id=?',(current['head_plan_id'],))
            if json.loads(original_plan['source']).get('evaluation_contract')!=source.get('evaluation_contract'):
                raise ValueError('数值评价合同新增、修改或移除，需用户重新审核研究边界，不能沿旧自主授权采纳')
            # Old native tool schemas could not supply these host-owned IDs.
            # Bind a same-generation, independently reviewed successor in the
            # admission receipt, without rewriting its historical source/body.
            if (source.get('origin')=='codex-native-tool' and source.get('previous_plan_id')==current['head_plan_id']
                    and plan['created_at']>=p['updated_at'] and source.get('authority_id') in {None,authority['id']}
                    and source.get('scope_digest') in {None,authority['scope_digest']}
                    and self.store.one('''SELECT 1 FROM research_role_handoffs h JOIN research_role_routes r ON r.delivery_id=h.source_delivery
                        WHERE h.session_id=? AND h.plan_id=? AND h.plan_digest=? AND h.verdict='approve' AND r.role='critic' ''',
                        (sid,plan_id,expected_digest))):
                source={**source,'authority_id':authority['id'],'scope_digest':authority['scope_digest']}
            if source.get('authority_id')!=authority['id'] or source.get('scope_digest')!=authority['scope_digest']:raise ValueError('下一方案未明确引用当前研究授权和固定边界摘要')
            proposal=source.get('scope_proposal')
            changed=[key for key in ['objective','evaluation','data_boundary','allowed_changes'] if proposal and proposal.get(key)!=json.loads(authority['scope'])[key]]
            if changed:
                raise ValueError('scope_proposal重新声明了研究边界，字段不同：'+', '.join(changed)+'。该字段只用于申请新授权，不是子方案说明。若实际边界未变，请保留本提案并另存元信息修订，省略scope_proposal，以原authority_id、scope_digest、previous_plan_id和review_record_id继承授权，再经Critic审议；不要仅因重述目标就找用户确认。若确实扩大数据、评价或资源范围，仍须用户授权；预算以已登记的结构化账本为准，不由方案文字追加。')
            if self.store.session(sid)['channel']!=json.loads(authority['scope'])['channel']:raise ValueError('研究渠道与授权不同，未回退模型通道')
            explicit=self.store.one('SELECT decision FROM research_reviews WHERE plan_id=? ORDER BY rowid DESC LIMIT 1',(plan_id,))
            if explicit and explicit['decision']=='reject':raise ValueError('用户已退回该具体方案，须另存修订版')
            if branch_id:
                branch=self.store.one('SELECT * FROM research_branches WHERE id=? AND project_id=?',(branch_id,pid))
                if not branch or branch['authority_id']!=authority['id'] or branch['head_plan_id']!=plan_id:raise ValueError('人工探索分支与具体方案不一致')
            else:
                branch=current
                if source.get('previous_plan_id')!=current['head_plan_id']:raise ValueError('下一方案必须承接活动主线；人工改向请新建探索分支')
                review=rounds.round_review(self.store,pid,current['head_plan_id'])
                if not repair and (not review or source.get('review_record_id')!=review['id'] or json.loads(review['source']).get('round_outcome')!='continue'):raise ValueError('先保存本轮证据评审，再采纳下一具体方案')
            frozen=json.loads(authority['scope'])['frozen_files'];refs={r['path']:r for r in json.loads(plan['evidence'])}
            for ref in frozen:
                if refs.get(ref['path'])!=ref:raise ValueError('固定评价/数据边界文件缺失或已变化，须重新授权')
            grant=self.store.one('SELECT * FROM research_grants WHERE id=?',(authority['grant_id'],))
            from .devspace import compute
            original=self.store.one('SELECT source FROM records WHERE id=?',(grant['plan_id'],))
            if compute(plan) is not None and compute(plan)!=compute(original):
                raise ValueError('GPU 数量或交互/批量执行方式改变，需明确审核；旧方案的执行位置不能被自主修订覆盖')
            root_review=self.store.one('SELECT decision FROM research_reviews WHERE plan_id=? ORDER BY rowid DESC LIMIT 1',(grant['plan_id'],))
            if not root_review or root_review['decision']!='accept':raise ValueError('原研究授权已退回')
            self.store.db.execute('INSERT INTO research_plan_admissions VALUES(?,?,?,?,?,?,?)',(plan_id,authority['id'],branch['id'],expected_digest,authority['scope_digest'],reason,now()))
            self.store.db.execute('UPDATE research_branches SET head_plan_id=? WHERE id=?',(plan_id,branch['id']))
            self.store.db.execute('UPDATE research_active_branches SET branch_id=?,updated_at=? WHERE project_id=?',(branch['id'],now(),pid))
            if repair:
                final,critic=repair
                self.store.db.execute('UPDATE research_recoveries SET admitted_plan_id=?,critic_handoff_id=? WHERE finalization_id=?',
                    (plan_id,critic['id'],final['id']))
        self.store.event(sid,'research.plan_adopted',{'plan_id':plan_id,'branch_id':branch['id'],'authority_id':authority['id'],
            'source':'程序核对已授权范围；不是用户逐版审批','reason':reason})
        if repair:self.store.event(sid,'research.recovery_admitted',{'previous_plan_id':repair[0]['plan_id'],
            'plan_id':plan_id,'critic_handoff_id':repair[1]['id'],'finalization_id':repair[0]['id'],'budget_reset':False})
        return {'adopted':True,'plan_id':plan_id,'branch_id':branch['id'],'authority_id':authority['id'],'budget_reset':False}

    def fork(self,sid,from_plan_id,title,content,reason,activate=False,user_delivery_id=None,origin='human_ui',revision_scope='incremental'):
        from .research import plan_digest
        from . import rounds
        plan=self.research.plan(sid,from_plan_id);pid=plan['project_id'];p=policy(self.store,pid)
        if type(activate) is not bool:raise ValueError('是否切换分支必须明确为true或false')
        if not p or p['mode']!='autonomous':raise ValueError('先明确启用本项目自主研究范围')
        if not str(reason).strip():raise ValueError('人工修订需要具体理由')
        previous_review=rounds.round_review(self.store,pid,from_plan_id)
        if activate and previous_review and json.loads(previous_review['source']).get('round_outcome')=='stop_requested':
            raise ValueError('原轮次已收束，不能通过激活人工分支继承旧授权；请先保存新scope_proposal候选，确认新范围后执行')
        self.research.check_plan_evidence(plan)
        if origin=='interpreted_user_request':
            delivery=self.store.one('''SELECT d.* FROM deliveries d JOIN research_input_origins o ON o.delivery_id=d.id
              WHERE d.id=? AND d.session_id=? AND o.origin='user' ''',(user_delivery_id,sid))
            if not delivery:raise ValueError('对话改向必须引用本会话实际用户消息，不可把模型建议标成人工干预')
            session=self.store.session(sid)
            if not session['active_turn'] or delivery['turn_id']!=session['active_turn']:raise ValueError('只能整理当前原生轮次中的人工输入，不能借旧对话伪造新指令')
        elif origin!='human_ui':raise ValueError('未知人工干预来源')
        if activate:self.quiet(pid) if origin=='human_ui' else self.unresolved(pid)
        authority=self.store.one('SELECT * FROM research_authorities WHERE id=?',(p['authority_id'],))
        parent=admitted(self.store,from_plan_id)
        root_branch=self.store.one('SELECT id FROM research_branches WHERE root_plan_id=? AND authority_id=?',(from_plan_id,authority['id']))
        parent_branch=parent['branch_id'] if parent else root_branch['id'] if root_branch else None
        if not parent_branch:raise ValueError('此方案没有可确认的研究分支归属')
        identity=hashlib.sha256(dumps([sid,from_plan_id,title,content,reason,user_delivery_id,revision_scope]).encode()).hexdigest()
        record=self.store.add_record(sid,{'kind':'plan','title':title,'content':content,'status':'proposed',
            'previous_plan_id':from_plan_id,'parent_id':from_plan_id,'revision_reason':reason,'revision_scope':revision_scope,'trigger_record_ids':[from_plan_id],
            'authority_id':authority['id'],'scope_digest':authority['scope_digest'],
            'evidence':[r['path'] for r in json.loads(plan['evidence'])]},
            {'origin':'human_intervention','intervention_source':origin,'user_delivery_id':user_delivery_id,'intervention_reason':reason},'human-fork:'+identity)
        existing=self.store.one('SELECT * FROM research_branches WHERE root_plan_id=?',(record['id'],))
        if existing:return {'branch_id':existing['id'],'plan_id':record['id'],'version':record['version'],'activated':active(self.store,pid)['id']==existing['id'],'reused':True}
        branch_id=uid()
        self.store.execute('INSERT INTO research_branches VALUES(?,?,?,?,?,?,?,?,?,?,?)',(branch_id,pid,authority['id'],record['id'],record['id'],parent_branch,from_plan_id,origin,reason,user_delivery_id,now()))
        if activate:self.adopt(sid,record['id'],plan_digest(record),reason,branch_id)
        self.store.event(sid,'research.branch_created',{'branch_id':branch_id,'from_plan_id':from_plan_id,'plan_id':record['id'],'activated':activate,'origin':origin})
        return {'branch_id':branch_id,'plan_id':record['id'],'version':record['version'],'activated':activate,'history_preserved':True}

    def switch(self,sid,branch_id,reason):
        from .research import plan_digest
        pid=self.store.session(sid)['project_id'];self.quiet(pid)
        branch=self.store.one('SELECT * FROM research_branches WHERE id=? AND project_id=?',(branch_id,pid))
        if not branch:raise ValueError('探索分支不属于当前项目')
        plan=self.research.plan(sid,branch['head_plan_id'])
        prior=admitted(self.store,plan['id'])
        if prior:
            self.permission(self.store,plan,require_active=False)
            self.store.execute('UPDATE research_active_branches SET branch_id=?,updated_at=? WHERE project_id=?',(branch_id,now(),pid))
        elif branch['origin']=='initial':
            from . import rounds
            p=policy(self.store,pid)
            review=self.store.one('SELECT decision FROM research_reviews WHERE plan_id=? ORDER BY rowid DESC LIMIT 1',(plan['id'],))
            if not p or p['mode']!='autonomous' or branch['authority_id']!=p['authority_id'] or rounds.round_review(self.store,pid,plan['id']) or not review or review['decision']!='accept':raise ValueError('原主方案已收尾或授权失效；请从旧方案新建修订分支，不重开旧轮次')
            self.research.check_plan_evidence(plan)
            self.store.execute('UPDATE research_active_branches SET branch_id=?,updated_at=? WHERE project_id=?',(branch_id,now(),pid))
        else:return self.adopt(sid,plan['id'],plan_digest(plan),reason,branch_id)
        self.store.event(sid,'research.branch_switched',{'branch_id':branch_id,'reason':reason})
        return self.status(sid)
