from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid.uuid4().hex


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.changed = threading.Condition()
        self.db = sqlite3.connect(self.root / "workbench.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS schema_versions(version INTEGER PRIMARY KEY, applied_at TEXT);
        CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,title TEXT NOT NULL,workspace TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),title TEXT NOT NULL,
          thread_id TEXT UNIQUE,channel TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'idle',active_turn TEXT,model TEXT,effort TEXT,
          usage TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL,updated_at TEXT NOT NULL,archived INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id),turn_id TEXT,
          item_id TEXT,role TEXT NOT NULL,kind TEXT NOT NULL,text TEXT NOT NULL DEFAULT '',status TEXT NOT NULL,created_at TEXT NOT NULL,
          UNIQUE(session_id,item_id));
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT,kind TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS events_session ON events(session_id,seq);
        CREATE INDEX IF NOT EXISTS events_research_story ON events(json_extract(payload,'$.project_id'),seq)
          WHERE kind='research.story.saved';
        CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id),text TEXT NOT NULL,
          mode TEXT NOT NULL,state TEXT NOT NULL,turn_id TEXT,error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id),generation TEXT NOT NULL,
          rpc_id TEXT NOT NULL,method TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL,response TEXT,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,project_id TEXT NOT NULL REFERENCES projects(id),session_id TEXT NOT NULL REFERENCES sessions(id),
          parent_id TEXT REFERENCES records(id),kind TEXT NOT NULL,title TEXT NOT NULL,summary TEXT NOT NULL,content TEXT NOT NULL,
          status TEXT NOT NULL,version TEXT,evidence TEXT NOT NULL,source TEXT NOT NULL,branch_reason TEXT,created_at TEXT NOT NULL,
          dedupe_key TEXT UNIQUE);
        CREATE TABLE IF NOT EXISTS drafts(session_id TEXT PRIMARY KEY REFERENCES sessions(id),text TEXT NOT NULL,revision INTEGER NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS workbench_trash(kind TEXT NOT NULL,object_id TEXT NOT NULL,deleted_at TEXT NOT NULL,PRIMARY KEY(kind,object_id));
        CREATE TABLE IF NOT EXISTS project_preferences(project_id TEXT PRIMARY KEY REFERENCES projects(id),pinned INTEGER NOT NULL DEFAULT 0,updated_at TEXT NOT NULL);
        INSERT OR IGNORE INTO schema_versions VALUES(13,datetime('now'));
        INSERT OR IGNORE INTO schema_versions VALUES(12,datetime('now'));
        INSERT OR IGNORE INTO schema_versions VALUES(1,datetime('now'));
        """)
        self.db.commit()
        from .lineage import install
        install(self)
        from .run_evidence import RunEvidence
        self.evidence=RunEvidence(self)
        from .research_modules import install as install_methods
        install_methods(self)

    def rows(self, sql, args=()):
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.rows(sql, args)
        return rows[0] if rows else None

    def execute(self, sql, args=()):
        with self.lock, self.db:
            return self.db.execute(sql, args).rowcount

    def event(self, session_id, kind, payload):
        with self.lock, self.db:
            seq = self.db.execute("INSERT INTO events(session_id,kind,payload,created_at) VALUES(?,?,?,?)",
                                  (session_id, kind, dumps(payload), now())).lastrowid
        with self.changed:
            self.changed.notify_all()
        return seq

    def session(self, session_id):
        result = self.one("SELECT s.*,p.workspace,p.title AS project_title FROM sessions s JOIN projects p ON p.id=s.project_id WHERE s.id=?", (session_id,))
        if not result:
            raise ValueError("会话不存在")
        if self.deleted('project',result['project_id']) or self.deleted('session',session_id):
            raise ValueError('此项目或会话已移入已删除，请先恢复')
        result["usage"] = json.loads(result["usage"])
        return result

    def patch_session(self, session_id, **fields):
        allowed = {"thread_id", "status", "active_turn", "model", "effort", "usage", "archived", "title"}
        if not fields or not set(fields) <= allowed:
            raise ValueError("Invalid session update")
        fields["updated_at"] = now()
        values = [dumps(v) if isinstance(v, dict) else v for v in fields.values()]
        self.execute("UPDATE sessions SET " + ",".join(f"{key}=?" for key in fields) + " WHERE id=?", (*values, session_id))

    def create_project(self, title, workspace=None):
        project_id = uid()
        if workspace:
            root = Path(workspace).expanduser()
            if not root.is_absolute() or not root.is_dir():
                raise ValueError("工作目录必须是已经存在的绝对目录")
            root = root.resolve()
        else:
            root = self.root / "workspaces" / project_id
            root.mkdir(parents=True)
        self.execute("INSERT INTO projects VALUES(?,?,?,?)", (project_id, title[:160] or "新的研究", str(root), now()))
        self.event(None, "project.created", {"id": project_id})
        return self.one("SELECT * FROM projects WHERE id=?", (project_id,))

    def create_session(self, project_id, channel, title="新的对话", model=None, effort=None):
        if self.deleted('project',project_id):raise ValueError('请先恢复已删除项目')
        if not self.one("SELECT id FROM projects WHERE id=?", (project_id,)):
            raise ValueError("项目不存在")
        session_id, stamp = uid(), now()
        self.execute("INSERT INTO sessions(id,project_id,title,channel,model,effort,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                     (session_id, project_id, title[:160], channel, model, effort, stamp, stamp))
        self.event(session_id, "session.created", {"id": session_id})
        return self.session(session_id)

    def deleted(self,kind,id):
        return bool(self.one('SELECT object_id FROM workbench_trash WHERE kind=? AND object_id=?',(kind,id)))

    def navigation(self):
        return {'projects':self.rows("SELECT p.*,COALESCE(v.pinned,0) AS pinned FROM projects p LEFT JOIN project_preferences v ON v.project_id=p.id WHERE NOT EXISTS(SELECT 1 FROM workbench_trash t WHERE t.kind='project' AND t.object_id=p.id) ORDER BY COALESCE(v.pinned,0) DESC,CASE WHEN v.pinned=1 THEN v.updated_at END DESC,p.created_at,p.id"),
                'sessions':self.rows("SELECT s.* FROM sessions s WHERE s.archived=0 AND NOT EXISTS(SELECT 1 FROM workbench_trash t WHERE (t.kind='project' AND t.object_id=s.project_id) OR (t.kind='session' AND t.object_id=s.id)) ORDER BY s.updated_at DESC")}

    def rename_session(self,id,title,expected_title=None):
        if not isinstance(title,str) or not 1<=len(title.strip())<=160:
            raise ValueError('会话名称需为1到160个字符')
        with self.lock,self.db:
            session=self.session(id)
            if self.deleted('project',session['project_id']):raise ValueError('请先恢复已移除的项目')
            if expected_title is not None and expected_title!=session['title']:
                raise ValueError('会话已在其他页面重命名，请刷新后重试')
            self.db.execute('UPDATE sessions SET title=?,updated_at=? WHERE id=?',(title.strip(),now(),id))
        self.event(id,'session.renamed',{'title':title.strip()})
        return self.session(id)

    def update_project(self,id,action,value,expected_title=None):
        with self.lock,self.db:
            project=self.one('SELECT * FROM projects WHERE id=?',(id,))
            if not project or self.deleted('project',id):raise ValueError('项目不存在或已移除，请先恢复')
            if action=='rename':
                if not isinstance(value,str) or not 1<=len(value.strip())<=160:raise ValueError('项目名称需为1到160个字符')
                if expected_title is not None and expected_title!=project['title']:raise ValueError('项目已在其他页面修改，请重新打开编辑')
                self.db.execute('UPDATE projects SET title=? WHERE id=?',(value.strip(),id))
            elif action=='pin':
                if type(value) is not bool:raise ValueError('置顶状态必须为布尔值')
                self.db.execute('INSERT INTO project_preferences VALUES(?,?,?) ON CONFLICT(project_id) DO UPDATE SET pinned=excluded.pinned,updated_at=excluded.updated_at',(id,int(value),now()))
            else:raise ValueError('不支持的项目设置')
            updated=self.one('SELECT p.*,COALESCE(v.pinned,0) AS pinned FROM projects p LEFT JOIN project_preferences v ON v.project_id=p.id WHERE p.id=?',(id,))
        self.event(None,'project.updated',{'id':id,'action':action})
        return updated

    def archived_sessions(self):
        return self.rows("SELECT s.id,s.title,s.project_id,s.updated_at,p.title AS project_title,EXISTS(SELECT 1 FROM workbench_trash t WHERE t.kind='project' AND t.object_id=p.id) AS project_removed FROM sessions s JOIN projects p ON p.id=s.project_id WHERE s.archived=1 AND NOT EXISTS(SELECT 1 FROM workbench_trash t WHERE t.kind='session' AND t.object_id=s.id) ORDER BY s.updated_at DESC")

    def unarchive_session(self,id):
        session=self.session(id) # Removed projects must be restored first.
        if not session['archived']:return {'restored':True,'execution_resumed':False}
        self.patch_session(id,archived=0)
        self.event(id,'session.unarchived',{})
        return {'restored':True,'execution_resumed':False}

    def trash_items(self):
        return self.rows("SELECT t.*,COALESCE(p.title,s.title) title FROM workbench_trash t LEFT JOIN projects p ON t.kind='project' AND p.id=t.object_id LEFT JOIN sessions s ON t.kind='session' AND s.id=t.object_id ORDER BY t.deleted_at DESC")

    def restore_deleted(self,kind,id):
        if kind not in {'project','session'}:raise ValueError('不支持的恢复类型')
        row=self.one('SELECT * FROM '+('projects' if kind=='project' else 'sessions')+' WHERE id=?',(id,))
        if not row:raise ValueError('对象不存在')
        if kind=='session' and self.deleted('project',row['project_id']):raise ValueError('请先恢复项目')
        self.execute('DELETE FROM workbench_trash WHERE kind=? AND object_id=?',(kind,id))
        self.event(None,'workbench.restored',{'kind':kind,'id':id})
        return {'restored':True,'execution_resumed':False}

    def message(self, session_id, item_id, role, kind, text, status, turn_id=None, append=False):
        with self.lock, self.db:
            # Raw command deltas remain in events. The transcript needs only
            # a tail, so an active command cannot grow every SQLite update.
            tail = 16000 if append and kind == 'output' else None
            existing = self.db.execute(
                "SELECT id,CASE WHEN ? IS NULL THEN text ELSE substr(text,-?) END AS text FROM messages WHERE session_id=? AND item_id=?",
                (tail,tail,session_id,item_id)).fetchone()
            if existing:
                body = existing["text"] + text if append else text
                if tail is not None:
                    body = body[-tail:]
                self.db.execute("UPDATE messages SET text=?,status=?,turn_id=COALESCE(?,turn_id),kind=CASE WHEN ? THEN kind ELSE ? END WHERE id=?", (body, status, turn_id, append, kind, existing["id"]))
                return existing["id"]
            message_id = uid()
            if tail is not None:
                text = text[-tail:]
            self.db.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?)",
                            (message_id, session_id, turn_id, item_id, role, kind, text, status, now()))
            return message_id

    def draft(self, session_id, text, expected_revision):
        self.session(session_id)
        with self.lock, self.db:
            old = self.db.execute("SELECT * FROM drafts WHERE session_id=?", (session_id,)).fetchone()
            revision = old["revision"] if old else 0
            if revision != expected_revision:
                return {"conflict": True, **dict(old)}
            self.db.execute("INSERT INTO drafts VALUES(?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET text=excluded.text,revision=excluded.revision,updated_at=excluded.updated_at",
                            (session_id, text, revision + 1, now()))
        return {"text": text, "revision": revision + 1}

    def safe_path(self, project_id, relative):
        project = self.one("SELECT * FROM projects WHERE id=?", (project_id,))
        if not project:
            raise ValueError("项目不存在")
        root = Path(project["workspace"]).resolve()
        path = (root / relative).resolve()
        if not path.is_relative_to(root):
            raise ValueError("文件必须位于当前项目目录内")
        return path

    def evidence_path(self, project_id, relative):
        # Validate the project boundary before enabling Windows extended-length
        # IO. Keep ordinary path projections/relative_to behavior unchanged.
        from .task_output_files import local_path
        return local_path(self.safe_path(project_id, relative))

    def add_record(self, session_id, record, source, dedupe_key=None):
        session = self.session(session_id)
        if dedupe_key:
            found = self.one("SELECT * FROM records WHERE dedupe_key=?", (dedupe_key,))
            if found:
                return found
        kind = record.get("kind")
        if kind not in {"question", "alignment", "research", "plan", "branch", "trial", "result", "decision", "review", "delivery"}:
            raise ValueError("不支持的研究记录类型")
        from .run_references import normalize as normalize_run_references
        record,source=normalize_run_references(self,session['project_id'],record,source)
        if 'evaluation_contract' in record and kind!='plan':raise ValueError('数值评价合同只能声明在具体方案上')
        if 'research_protocol' in record and kind!='plan':raise ValueError('内置研究协议只能固定在具体方案上')
        method_ids=record.get('method_report_ids',[])
        if not isinstance(method_ids,list) or len(method_ids)>12 or any(not isinstance(id,str) for id in method_ids) or len(set(method_ids))!=len(method_ids):
            raise ValueError('内置方法报告需为最多12个不重复的实际报告ID')
        method_refs=[]
        if method_ids:
            if kind not in {'trial','research','result','decision','review','delivery'} or not record.get('plan_id'):
                raise ValueError('方法报告应关联具体方案的研究、实验、结果或评审')
            from .research_modules import ResearchModules
            methods=ResearchModules(self)
            for id in method_ids:
                report=methods.read_report(session_id,id)
                if report['plan_id']!=record['plan_id']:raise ValueError('方法报告不属于此具体方案')
                if record.get('stop_reason')=='target_achieved' and report['state'] in {'blocked','needs_evidence'}:
                    raise ValueError('所引用方法报告存在冲突或证据不足，不能宣称目标达成')
                method_refs.append({'id':id,'module':report['module'],'state':report['state'],'method_digest':report['method_digest']})
        comparison_ids=record.get('comparison_ids',[])
        if not isinstance(comparison_ids,list) or len(comparison_ids)>8 or any(not isinstance(id,str) for id in comparison_ids) or len(set(comparison_ids))!=len(comparison_ids):raise ValueError('数值核验引用需为最多8个明确不重复ID')
        if 'stop_reason' in record and kind!='review':raise ValueError('结束原因只能声明在轮次评审')
        comparison_refs=[]
        if comparison_ids:
            if kind not in {'trial','result','decision','review','delivery'}:raise ValueError('数值核验应关联实验、结果或评审，不建立额外方案节点')
            from .comparison import read_report
            for id in comparison_ids:
                value=read_report(self,session['project_id'],id)
                report=self.one('SELECT markdown_path FROM research_comparisons WHERE id=?',(id,))
                comparison_refs.append({'id':id,'plan_id':value['plan_id'],'state':value['state'],'rules_passed':value['rules_passed'],'targets_met':value['targets_met'],'prospective':value['prospective'],'report_path':report['markdown_path']})
        basis=record.get('basis_record_id')
        if basis:
            if kind!='trial':raise ValueError('basis_record_id 仅可用于实验；决策、结果或方案引用前序证据请使用 trigger_record_ids 或 cloud_run_ids')
            if not self.one("SELECT id FROM records WHERE id=? AND project_id=? AND kind IN ('result','decision','review')",(basis,session['project_id'])):raise ValueError('实验依据必须是同项目已保存的结果、决策或评审，不能凭时间顺序猜测')
        run_ids=record.get('run_ids',[])
        if not isinstance(run_ids,list) or len(run_ids)>(100 if kind=='review' else 20) or any(not isinstance(id,str) for id in run_ids):
            raise ValueError('结果关联需要有界的明确运行ID列表；轮次评审最多100个，普通结果最多20个')
        if run_ids:
            if kind not in {'result','decision','review','delivery'} or not self.one("SELECT name FROM sqlite_master WHERE name='research_record_runs'"):
                raise ValueError('只有结果、决策、轮次评审或交付能关联现有研究运行')
            for id in run_ids:
                if not self.one('SELECT id FROM research_runs WHERE id=? AND project_id=?',(id,session['project_id'])):
                    raise ValueError('关联运行必须属于当前项目且实际存在')
        cloud_ids=record.get('cloud_run_ids',[])
        if not isinstance(cloud_ids,list) or len(cloud_ids)>100 or any(not isinstance(id,str) for id in cloud_ids):raise ValueError('云端运行引用需要明确有界ID列表')
        if cloud_ids:
            if kind not in {'result','decision','review','delivery'} or not self.one("SELECT name FROM sqlite_master WHERE name='company_training'"):raise ValueError('只有结果/评审等可引用实际云端运行')
            for id in cloud_ids:
                if not self.one("SELECT id FROM company_training WHERE id=? AND project_id=? AND state<>'prepared'",(id,session['project_id'])):raise ValueError('云端运行必须属于当前项目且实际提交过')
        title, content = str(record.get("title", "")).strip(), str(record.get("content", "")).strip()
        if not title or not content:
            raise ValueError("研究记录需要简洁标题和完整正文")
        parent_id = record.get("parent_id") or (record.get('review_record_id') if kind=='plan' else None)
        with self.lock, self.db:
            from . import lineage
            lineage.validate_links(self, session['project_id'], record)
            source = dict(source)
            from .research_story import prepare_record as prepare_story
            source = prepare_story(self, session, record, source)
            if method_refs:source['method_reports']=method_refs
            if comparison_refs:source['comparisons']=comparison_refs
            if basis:source['basis_record_id']=basis
            if cloud_ids:source['cloud_run_ids']=list(dict.fromkeys(cloud_ids))
            for field in ['plan_id', 'trigger_record_ids', 'depends_on_trial_ids', 'revision_scope', 'revision_reason','authority_id','scope_digest','scope_proposal']:
                if field in record:
                    source[field] = record[field]
            if kind=='review' or record.get('previous_plan_id') or record.get('review_record_id'):
                from .rounds import prepare_record
                source=prepare_record(self,session,record,source,run_ids)
            if parent_id:
                parent = self.db.execute("SELECT * FROM records WHERE id=? AND project_id=?", (parent_id, session["project_id"])).fetchone()
                if not parent:
                    raise ValueError("父节点必须属于当前项目")
            elif kind != "question":
                parent = self.db.execute("SELECT * FROM records WHERE project_id=? ORDER BY rowid DESC LIMIT 1", (session["project_id"],)).fetchone()
                parent_id = parent["id"] if parent else None
            if kind == "branch" and not str(record.get("branch_reason", "")).strip():
                raise ValueError("新分支必须说明为什么不能沿当前研究主线继续")
            evidence = record.get("evidence", [])
            if not isinstance(evidence, list) or any(not isinstance(ref, str) for ref in evidence):
                raise ValueError("证据应为项目内文件路径数组")
            evidence=list(dict.fromkeys([*evidence,*[ref['report_path'] for ref in comparison_refs]]))
            checked = []
            for ref in evidence:
                path = self.evidence_path(session["project_id"], ref)
                if not path.is_file():
                    raise ValueError(f"证据文件不存在：{ref}")
                digest = hashlib.sha256()
                with path.open('rb') as evidence_file:
                    for block in iter(lambda: evidence_file.read(1024 * 1024), b''):
                        digest.update(block)
                checked.append({"path": ref, "sha256": digest.hexdigest(), "size": path.stat().st_size})
            from .inquiry import prepare_record as prepare_inquiry
            source=prepare_inquiry(self,session,record,checked,source)
            from .study_integrity import prepare_record as prepare_study, prepare_plan as prepare_confirmation
            source=prepare_study(self,session,record,checked,source)
            if run_ids or cloud_ids:
                from .round_evidence import observations
                observed=observations(self,session['project_id'])
                source['evidence_observations']={key:observed[key] for key in
                    [*['local:'+id for id in run_ids],*['cloud:'+id for id in cloud_ids]] if key in observed}
            if kind=='plan':
                from .evaluation_contract import prepare_plan
                checked,content,source=prepare_plan(self,session,record,checked,content,source)
                from .research_modules import prepare_plan as prepare_methods
                source=prepare_methods(self,session,record,checked,content,source)
                source=prepare_confirmation(self,session,record,checked,source)
            version = None
            plan_lineage = None
            if kind == "plan":
                plan_lineage = lineage.allocate(self, session['project_id'], record,source)
                if source.get('release_candidate'):
                    plan_lineage = None
                else:
                    version = plan_lineage['version']
            status = record.get("status", "recorded")
            if status not in {"proposed", "recorded", "running", "completed", "blocked", "failed", "rejected"}:
                raise ValueError("无效研究状态")
            row = {"id": uid(), "project_id": session["project_id"], "session_id": session_id, "parent_id": parent_id,
                   "kind": kind, "title": title[:100], "summary": str(record.get("summary") or content.splitlines()[0])[:240],
                   "content": content, "status": status, "version": version, "evidence": dumps(checked), "source": dumps(source),
                   "branch_reason": record.get("branch_reason"), "created_at": now(), "dedupe_key": dedupe_key}
            self.db.execute("INSERT INTO records(" + ",".join(row) + ") VALUES(" + ",".join("?" for _ in row) + ")", tuple(row.values()))
            if plan_lineage:
                lineage.save(self, row, plan_lineage)
            for run_id in set(run_ids):
                self.db.execute('INSERT INTO research_record_runs VALUES(?,?)',(row['id'],run_id))
        # Independent immutable Markdown exports; never execute their contents.
        directory = self.root / "records" / session["project_id"]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / (row["id"] + ".md")).write_text(f"# {title}\n\n{content}\n", encoding="utf-8")
        self.event(session_id, "research.recorded", {"id": row["id"], "kind": kind})
        return row

    def record_statuses(self,project_id):
        projected={}
        from .autonomy import installed
        if installed(self):
            for row in self.rows('SELECT plan_id FROM research_plan_admissions a JOIN records r ON r.id=a.plan_id WHERE r.project_id=?',(project_id,)):
                projected[row['plan_id']]={'status':'approved','source':'program_checked_research_delegation'}
        if self.one("SELECT name FROM sqlite_master WHERE type='table' AND name='research_reviews'"):
            for review in self.rows('SELECT plan_id,decision FROM research_reviews WHERE project_id=? ORDER BY rowid',(project_id,)):
                projected[review['plan_id']]={'status':'review_approved' if review['decision']=='accept' else 'request_changes','source':'exact_plan_review'}
        if self.one("SELECT name FROM sqlite_master WHERE type='table' AND name='research_run_context'"):
            trials={}
            for run in self.rows('SELECT r.id,r.state,c.trial_id FROM research_runs r JOIN research_run_context c ON c.run_id=r.id WHERE r.project_id=? AND c.trial_id IS NOT NULL ORDER BY r.rowid',(project_id,)):
                trials.setdefault(run['trial_id'],[]).append(run)
            for trial,runs in trials.items():
                live=next((r for r in runs if r['state'] in {'unknown','running','requested'}),None)
                current=live or runs[-1]
                status={'requested':'running','running':'running','completed':'completed','failed':'failed','timed_out':'failed',
                        'invalid_inputs':'failed','artifacts_missing':'blocked','cancelled':'blocked','unknown':'blocked'}.get(current['state'],'blocked')
                projected[trial]={'status':status,'source':'exact_run_link','run_state':current['state'],'run_ids':[r['id'] for r in runs]}
        if self.one("SELECT name FROM sqlite_master WHERE name='company_training'"):
            from .task_outputs import status as output_status
            for run in self.rows("SELECT id,state,trial_id,session_id,task_uid FROM company_training WHERE project_id=? AND state<>'prepared' ORDER BY rowid",(project_id,)):
                phase=run['state'].lower()
                status='running' if phase in {'requested','submitted','running','pending'} else 'failed' if phase in {'failed','error'} else 'blocked'
                collection=output_status(self,run['session_id'],run['task_uid']) if run['task_uid'] else None
                if (phase in {'succeeded','success','completed'} and collection and collection['state']=='ready'
                        and (collection.get('result_check') or {}).get('identity_verified') is True):
                    status='completed'  # Execution and archive; never a scientific conclusion.
                projected[run['trial_id']]={'status':status,'source':'exact_cloud_run_link','run_state':phase,'cloud_run_ids':[run['id']],
                                          'meaning':'云端完成表示执行及归档就绪，不代表科学评审通过；无可校验归档的成功终态仍待取证'}
        return projected

    def projected_records(self,project_id):
        records=self.rows('SELECT * FROM records WHERE project_id=? ORDER BY rowid',(project_id,))
        statuses=self.record_statuses(project_id)
        for record in records:
            current=statuses.get(record['id'],{})
            record.update(recorded_status=record['status'],current_status=current.get('status'),status_source=current.get('source','historical_record'),
                          execution_run_ids=current.get('run_ids',[]),run_state=current.get('run_state'))
        return records

    def graph(self, project_id):
        from .lineage import project
        from .research_story import project as project_story
        from .study_integrity import project as project_study
        return project_study(self, project_story(self, project(self, project_id, self.projected_records(project_id))))

    def close(self):
        with self.lock:
            self.db.close()
