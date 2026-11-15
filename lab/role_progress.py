"""Deterministic, durable role-chain liveness accounting; no model or file reads.

Only novel plan bodies, frozen evidence bytes and actual local/cloud run facts
open a progress window. Role/review prose, record identities and clock updates
are not progress. A human input opens a guidance window, not an execution grant.
"""
import hashlib
import json
import re

from .store import dumps, now


LIMIT_SETTING = 'role_handoff_no_progress_limit'
DEFAULT_LIMIT = 8
MAX_LIMIT = 64
SCHEMA = 'iterlab-role-progress/v1'
_VOLATILE = re.compile(r'^(?:last_)?(?:created|updated|observed|checked|captured|started|finished|completed|queued|requested|submitted|received|start|end|create|update)_?(?:at|time|timestamp)$', re.I)
_RECEIPT_METADATA = {'timestamp', 'time', 'seconds', 'duration', 'running_duration',
    'elapsed_seconds', 'duration_seconds', 'next_check', 'run_id', 'plan_id',
    'plan_version', 'trial_id', 'operation_id', 'directory'}


def validate_limit(value):
    if type(value) is not int or not 1 <= value <= MAX_LIMIT:
        raise ValueError('自动角色无进展交接上限必须为1到64的整数，不能关闭保护')
    return value


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()


def _json(value, default):
    return json.loads(value) if value else default


def _receipt(value):
    """Keep actual receipt facts, not polling clocks or relocated byte copies."""
    if isinstance(value, dict):
        if 'sha256' in value:
            return {k: value[k] for k in ('sha256', 'size') if k in value}
        return {k: _receipt(v) for k, v in value.items()
                if k not in _RECEIPT_METADATA and not _VOLATILE.fullmatch(k)}
    if isinstance(value, list):
        return [_receipt(v) for v in value]
    return value


class RoleProgress:
    def __init__(self, store):
        self.store = store
        with store.lock, store.db:
            store.db.executescript('''
            CREATE TABLE IF NOT EXISTS research_role_progress(
              session_id TEXT PRIMARY KEY REFERENCES sessions(id),seen TEXT NOT NULL,
              context TEXT NOT NULL,signature TEXT NOT NULL,progress_signature TEXT NOT NULL,
              consecutive_unchanged INTEGER NOT NULL,progress_pending INTEGER NOT NULL,
              state TEXT NOT NULL,blocked_handoff_id TEXT,reason TEXT NOT NULL,
              reset_reason TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS research_role_progress_checks(
              handoff_id TEXT PRIMARY KEY REFERENCES research_role_handoffs(id),
              session_id TEXT NOT NULL REFERENCES sessions(id),signature TEXT NOT NULL,
              counted INTEGER NOT NULL,state TEXT NOT NULL,consecutive_unchanged INTEGER NOT NULL,
              created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            ''')

    def settings(self, settings=None):
        if settings is not None:
            return settings
        # Lazy import avoids changing the role-model policy's shape. Settings
        # is always read from this Store's independent root, never a global root.
        from .settings import Settings
        return Settings(self.store.root).value

    def snapshot(self, sid, settings=None):
        settings = self.settings(settings)
        session = self.store.session(sid)
        pid = session['project_id']
        facts = set()
        readiness_facts=set()
        for record in self.store.rows('SELECT kind,content,evidence FROM records WHERE project_id=?', (pid,)):
            if record['kind'] == 'plan':
                facts.add(_hash(['plan', record['content'].replace('\r\n', '\n').strip()]))
            for ref in _json(record['evidence'], []):
                # Paths, titles, versions and new record IDs cannot turn a copy
                # into new evidence. Store froze these hashes at record creation.
                if isinstance(ref, dict) and isinstance(ref.get('sha256'), str):
                    facts.add(_hash(['evidence', ref['sha256'], ref.get('size')]))
        if self.store.one("SELECT name FROM sqlite_master WHERE name='research_runs'"):
            for run in self.store.rows('SELECT id,state,receipt FROM research_runs WHERE project_id=?', (pid,)):
                facts.add(_hash(['local_run', run['id'], run['state'], _receipt(_json(run['receipt'], None))]))
        if self.store.one("SELECT name FROM sqlite_master WHERE name='company_training'"):
            for run in self.store.rows("SELECT id,task_uid,state,result FROM company_training WHERE project_id=? AND state<>'prepared'", (pid,)):
                facts.add(_hash(['cloud_run', run['id'], run['task_uid'], run['state'], _receipt(_json(run['result'], None))]))
        # Completion and arrival of analyzable evidence are separate events.
        # Count readiness once, not per retry/manifest timestamp or copy path.
        # These are liveness facts, never permission or scientific validation.
        if self.store.one("SELECT name FROM sqlite_master WHERE name='run_evidence'"):
            for row in self.store.rows('SELECT location,run_id,analysis_scope FROM run_evidence WHERE project_id=? AND analysis_ready=1',(pid,)):
                readiness_facts.add(_hash(['evidence_ready',row['location'],row['run_id'],row['analysis_scope']]))
        if self.store.one("SELECT name FROM sqlite_master WHERE name='company_output_jobs'"):
            for row in self.store.rows('''SELECT j.session_id,j.task_uid,j.state,j.outcome FROM company_output_jobs j
                    JOIN sessions s ON s.id=j.session_id WHERE s.project_id=?
                      AND j.state IN ('ready','diagnostic_only')''',(pid,)):
                outcome=_json(row['outcome'],{})
                evidence=outcome.get('evidence_digest')
                if not isinstance(evidence,str) or not re.fullmatch('[a-f0-9]{64}',evidence):evidence=None
                readiness_facts.add(_hash(['cloud_evidence_ready',row['session_id'],row['task_uid'],row['state'],
                    outcome.get('archive_state'),outcome.get('result_check',{}).get('identity_verified') is True,evidence]))
        facts.update(readiness_facts)
        human = None
        if self.store.one("SELECT name FROM sqlite_master WHERE name='research_input_origins'"):
            row = self.store.one('''SELECT d.id,d.text,o.origin FROM deliveries d
                JOIN research_input_origins o ON o.delivery_id=d.id
                WHERE d.session_id=? AND o.origin IN ('user','review') ORDER BY d.rowid DESC LIMIT 1''', (sid,))
            if row:
                human = {'delivery_id': row['id'], 'origin': row['origin'], 'content_digest': _hash(row['text'])}
        from .autonomy import policy, active
        p = policy(self.store, pid)
        branch = active(self.store, pid)
        authority = self.store.one('SELECT scope_digest,grant_id FROM research_authorities WHERE id=?', (p['authority_id'],)) if p else None
        review = self.store.one('SELECT id FROM research_reviews WHERE project_id=? ORDER BY rowid DESC LIMIT 1', (pid,)) if self.store.one("SELECT name FROM sqlite_master WHERE name='research_reviews'") else None
        grant = self.store.one('SELECT id FROM research_grants WHERE project_id=? ORDER BY rowid DESC LIMIT 1', (pid,)) if self.store.one("SELECT name FROM sqlite_master WHERE name='research_grants'") else None
        context = {'human_input': human, 'authority_id': p['authority_id'] if p else None,
            'scope_digest': authority['scope_digest'] if authority else None,
            'grant_id': authority['grant_id'] if authority else grant['id'] if grant else None,
            'policy_generation': p['updated_at'] if p else None, 'mode': p['mode'] if p else None,
            'owner_session_id': p['owner_session_id'] if p else None,
            'branch_id': branch['id'] if branch else None, 'review_origin_id': review['id'] if review else None,
            'channel': session['channel'], 'configured_channel': settings.get('channel'),
            'profile': settings.get('profile'), 'role_policy': settings.get('agent_roles'),
            'evidence_readiness_version':1}
        progress_signature = _hash(sorted(facts))
        return {'facts': facts, 'readiness_facts':readiness_facts,'context': context, 'progress_signature': progress_signature,
            'signature': _hash([SCHEMA, progress_signature, context])}

    def _observe_locked(self, sid, settings):
        snapshot = self.snapshot(sid, settings)
        prior = self.store.one('SELECT * FROM research_role_progress WHERE session_id=?', (sid,))
        seen = set(_json(prior['seen'], [])) if prior else set()
        old_context = _json(prior['context'], {}) if prior else {}
        if prior and old_context.get('evidence_readiness_version')!=1:
            # Upgrade is not a new result. Baseline already-visible archives
            # without resetting any historical stall/guidance counters.
            seen.update(snapshot['readiness_facts'])
        novel = bool(prior and snapshot['facts'] - seen)
        human = bool(prior and snapshot['context']['human_input']
                     and old_context.get('human_input') != snapshot['context']['human_input'])
        reset = novel or human
        row = {'session_id': sid, 'seen': dumps(sorted(seen | snapshot['facts'])),
            'context': dumps(snapshot['context']), 'signature': snapshot['signature'],
            'progress_signature': snapshot['progress_signature'],
            'consecutive_unchanged': 0 if reset or not prior else prior['consecutive_unchanged'],
            'progress_pending': int(novel or bool(prior and prior['progress_pending'])),
            'state': 'watching' if reset or not prior else prior['state'],
            'blocked_handoff_id': None if reset or not prior else prior['blocked_handoff_id'],
            'reason': '' if reset or not prior else prior['reason'],
            'reset_reason': 'progress' if novel else 'human_input' if human else prior['reset_reason'] if prior else 'initial',
            'updated_at': now()}
        # Context/generation changes bind the signature but are not progress.
        # A restart, profile edit or new grant therefore cannot clear the count.
        changed = not prior or any(row[k] != prior[k] for k in row if k != 'updated_at')
        if changed:
            self._save_locked(row)
        else:
            row = prior
        if reset and prior['state'] == 'no_progress':
            schedule = self.store.one('SELECT state FROM research_scheduler_status WHERE session_id=?', (sid,)) if self.store.one("SELECT name FROM sqlite_master WHERE name='research_scheduler_status'") else None
            if schedule and schedule['state'] == 'no_progress':
                self._scheduler_locked(sid, 'waiting', '角色引导窗口已更新；具体执行仍须原授权、暂停和未知状态检查')
        return row, reset

    def _save_locked(self, row):
        fields = list(row)
        self.store.db.execute('INSERT INTO research_role_progress(' + ','.join(fields) + ') VALUES(' +
            ','.join('?' for _ in fields) + ') ON CONFLICT(session_id) DO UPDATE SET ' +
            ','.join(k + '=excluded.' + k for k in fields if k != 'session_id'), tuple(row.values()))

    def _scheduler_locked(self, sid, state, reason):
        if not self.store.one("SELECT name FROM sqlite_master WHERE name='research_scheduler_status'"):
            return
        prior = self.store.one('SELECT state,reason FROM research_scheduler_status WHERE session_id=?', (sid,))
        if prior and (prior['state'], prior['reason']) == (state, reason):
            return
        self.store.db.execute('''INSERT INTO research_scheduler_status VALUES(?,?,?,0,0,?)
            ON CONFLICT(session_id) DO UPDATE SET state=excluded.state,reason=excluded.reason,
            next_check=0,failures=0,updated_at=excluded.updated_at''', (sid, state, reason, now()))

    @staticmethod
    def _view(row, limit):
        blocked = row['state'] == 'no_progress'
        context = _json(row['context'], {})
        # Persist the complete settings binding, but avoid injecting another
        # four-model policy copy into every brief research_status/event.
        role_policy = context.pop('role_policy', None)
        context['role_policy_digest'] = _hash(role_policy)
        return {'schema': SCHEMA, 'limit': limit, 'consecutive_unchanged': row['consecutive_unchanged'],
            'state': row['state'], 'blocked': blocked, 'reason_code': 'no_progress' if blocked else None,
            'reason': row['reason'], 'blocked_handoff_id': row['blocked_handoff_id'],
            'signature': row['signature'], 'progress_signature': row['progress_signature'],
            'context': context, 'reset_reason': row['reset_reason'],
            'budget_reset': False}

    def status(self, sid, settings=None):
        settings = self.settings(settings)
        limit = validate_limit(settings.get(LIMIT_SETTING, DEFAULT_LIMIT))
        with self.store.lock, self.store.db:
            row, _ = self._observe_locked(sid, settings)
        return self._view(row, limit)

    def allow(self, sid, handoff, settings=None):
        """Reserve at most one count per handoff, before the non-idempotent send.

        Blocked handoffs remain pending and uncounted. A retry may consume a new
        progress/guidance window, but never another count for an already reserved
        handoff. Previously seen facts cannot reset a window by oscillating back.
        The caller MUST run its existing authorization check before calling this.
        """
        settings = self.settings(settings)
        limit = validate_limit(settings.get(LIMIT_SETTING, DEFAULT_LIMIT))
        event = None
        with self.store.lock, self.store.db:
            row, reset = self._observe_locked(sid, settings)
            check = self.store.one('SELECT * FROM research_role_progress_checks WHERE handoff_id=?', (handoff['id'],))
            if check and check['session_id'] != sid:
                raise ValueError('原角色进展检查不可转给其他会话')
            counted = bool(check and check['counted'])
            consumed_progress = bool(row['progress_pending'])
            blocked_elsewhere = row['state'] == 'no_progress' and row['blocked_handoff_id'] != handoff['id']
            within_limit = (row['consecutive_unchanged'] <= limit if counted
                            else row['consecutive_unchanged'] < limit)
            allowed = not blocked_elsewhere and (consumed_progress or within_limit)
            if allowed:
                if not counted:
                    row['consecutive_unchanged'] = 0 if consumed_progress else row['consecutive_unchanged'] + 1
                row.update(progress_pending=0, state='watching', blocked_handoff_id=None, reason='', updated_at=now())
                check_state = 'allowed'
            else:
                reason = ('连续' + str(row['consecutive_unchanged']) + '次自动角色交接无新增方案内容、真实证据或运行／收据进展；'
                    '已达到上限' + str(limit) + '，保留待交接而不投递，等待明确人工指导或真实进展（no_progress）')
                row.update(state='no_progress', blocked_handoff_id=handoff['id'], reason=reason, updated_at=now())
                self._scheduler_locked(sid, 'no_progress', reason)
                check_state = 'no_progress'
            if not check or check['state'] != check_state or check['signature'] != row['signature'] or reset:
                event = 'research.role_progress_checked' if allowed else 'research.role_no_progress'
            self._save_locked(row)
            self.store.db.execute('''INSERT INTO research_role_progress_checks VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(handoff_id) DO UPDATE SET signature=excluded.signature,counted=excluded.counted,
                state=excluded.state,consecutive_unchanged=excluded.consecutive_unchanged,updated_at=excluded.updated_at''',
                (handoff['id'], sid, row['signature'], int(counted or allowed), check_state,
                 row['consecutive_unchanged'], check['created_at'] if check else now(), now()))
            value = {**self._view(row, limit), 'allowed': allowed, 'handoff_id': handoff['id'],
                'counted': bool(allowed and not counted), 'progressed': consumed_progress and allowed}
        if event:
            self.store.event(sid, event, value)
        return value

    def lifecycle_block(self, sid, settings=None):
        """Optional Workflow integration, AFTER pause/unknown/approval projections."""
        status = self.status(sid, settings)
        if not status['blocked']:
            return None
        return {'state': 'blocked', 'label': '自动角色链因无实质进展停止',
            'reason_code': 'no_progress', 'reason': status['reason'],
            'subject_kind': 'role_handoff', 'subject_id': status['blocked_handoff_id'],
            'allowed_actions': ['discuss', 'inspect'], 'guard': status,
            'meaning': '仅自动续接保护，不授予执行权限或重置累计训练预算'}
