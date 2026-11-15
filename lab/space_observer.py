"""Durable read-only reconciliation of the original devspace lifecycle request."""
import hashlib
import json
import os
import re
import threading
import time
import uuid

from .platform_health import credential_revision
from .store import dumps, now


PENDING = {'requested', 'submitted', 'unknown', 'pending_approval'}
PROCESS_AUTH_EPOCH = uuid.uuid4().hex


def process_auth_epoch():
    # An environment token's value is never inspected. Across service restarts
    # its identity is unknowable, so require a manual read for that old request.
    return PROCESS_AUTH_EPOCH if 'LPAI_API_KEY' in os.environ else None


class SpaceObserver:
    def __init__(self, company):
        self.company = company
        self.store = company.store
        self.poll_lock = threading.Lock()
        self.inflight = set()
        with self.store.lock, self.store.db:
            self.store.db.executescript('''
            CREATE TABLE IF NOT EXISTS company_space_reads(
              operation_id TEXT PRIMARY KEY REFERENCES company_operations(id) ON DELETE CASCADE,identity TEXT NOT NULL,state TEXT NOT NULL,
              next_check REAL NOT NULL,failures INTEGER NOT NULL,last_checked_at TEXT,
              last_success_at TEXT,error_type TEXT,message TEXT NOT NULL,updated_at TEXT NOT NULL);
            INSERT OR IGNORE INTO schema_versions VALUES(24,datetime('now'));
            ''')

    def register(self, operation_id, session, bindings, config):
        """Called with the request's short store lock, before its write boundary.

        Only file metadata is fingerprinted. No credential file is opened and
        no account probe or platform call occurs while freezing this identity.
        """
        identity = {'project_id': session['project_id'], 'revision': config['revision'],
                    'bindings_hash': hashlib.sha256(dumps(bindings).encode()).hexdigest(),
                    'credential_revision': credential_revision('lpai_cli'),
                    'process_auth_epoch': process_auth_epoch()}
        self.store.execute('INSERT INTO company_space_reads VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (operation_id, dumps(identity), 'waiting', 0, 0, None, None, None,
                            '等待只读核对原空间；不会重复发送启停请求', now()))
        self.inflight.add(operation_id)

    def finish(self, operation_id):
        with self.store.lock:
            self.inflight.discard(operation_id)

    def _target(self, operation):
        request = json.loads(operation['request'])
        if request.get('action') not in {'lpai_start', 'lpai_stop'}:
            return None
        targets = [target for target in request.get('bound_targets', []) if target.get('kind') == 'lpai']
        return targets[0] if len(targets) == 1 else None

    def _eligible(self, operation, read=None):
        if operation['id'] in self.inflight:
            return 'inflight'
        try:
            session = self.store.session(operation['session_id'])
        except ValueError:
            return 'owner_unavailable'
        target = self._target(operation)
        bound = self.store.one("SELECT kind,scope,uid,name FROM company_bindings WHERE project_id=? AND kind='lpai'",
                               (session['project_id'],))
        config = self.company.config()
        if not target or bound != target or not config['lpai_enabled'] or config['namespace'] != target['scope']:
            return 'authority_changed'
        if read:
            identity = json.loads(read['identity'])
            bindings = self.store.rows('SELECT kind,scope,uid,name FROM company_bindings WHERE project_id=? ORDER BY kind',
                                       (session['project_id'],))
            if (identity['project_id'] != session['project_id'] or identity['revision'] != config['revision'] or
                identity['bindings_hash'] != hashlib.sha256(dumps(bindings).encode()).hexdigest() or
                identity['credential_revision'] != credential_revision('lpai_cli') or
                identity.get('process_auth_epoch') != process_auth_epoch()):
                return 'authority_changed'
        return None

    def _record(self, operation_id, state, message, *, stamp=None, success=False, error=None, delay=30):
        prior = self.store.one('SELECT * FROM company_space_reads WHERE operation_id=?', (operation_id,))
        if not prior:
            return
        failures = prior['failures'] + 1 if error else 0
        if error:
            delay = min(600, 15 * 2 ** min(failures - 1, 6))
        checked = now() if stamp is not None else prior['last_checked_at']
        self.store.execute('''UPDATE company_space_reads SET state=?,next_check=?,failures=?,last_checked_at=?,
            last_success_at=?,error_type=?,message=?,updated_at=? WHERE operation_id=?''',
            (state, (time.time() if stamp is None else stamp) + delay, failures, checked,
             checked if success else prior['last_success_at'], type(error).__name__ if error else None,
             message, now(), operation_id))
        changed = (state, message, type(error).__name__ if error else None) != (prior['state'], prior['message'], prior['error_type'])
        if changed and state not in {'observed_active', 'observed_stopped'}:
            operation = self.store.one('SELECT session_id FROM company_operations WHERE id=?', (operation_id,))
            if operation:
                self.store.event(operation['session_id'], 'company.space_observation',
                                 {'id': operation_id, 'project_id': json.loads(prior['identity'])['project_id'],
                                  'state': state, 'message': message})
        return changed

    def observe(self, item, operation_id=None, *, manual=False):
        """Only the human refresh endpoint may confirm facts under a new login."""
        phase = item['phase'].lower()
        action = 'lpai_start' if phase in {'ready', 'running'} else 'lpai_stop' if phase == 'stopped' else None
        if not action:
            return False
        adopted = False
        with self.store.lock:
            rows = self.store.rows("SELECT * FROM company_operations WHERE state IN ('requested','submitted','unknown','pending_approval')"
                                   + (' AND id=?' if operation_id else ''), (operation_id,) if operation_id else ())
            for operation in rows:
                request = json.loads(operation['request'])
                target = self._target(operation)
                if request.get('action') != action or not target or (target['uid'], target['scope'], target['name']) != (
                    item['uid'], item['namespace'], item['name']):
                    continue
                read = self.store.one('SELECT * FROM company_space_reads WHERE operation_id=?', (operation['id'],))
                if self._eligible(operation, None if manual else read):
                    continue
                state = 'observed_active' if action == 'lpai_start' else 'observed_stopped'
                message = ('只读确认原UID空间当前运行' if action == 'lpai_start' else '只读确认原UID空间当前已停止') + '；不推断由本次旧请求完成，不推断审批结果'
                result = json.loads(operation['result'] or '{}')
                result.update(id=operation['id'], action=action, state=state, uid=item['uid'], name=item['name'],
                              namespace=item['namespace'], item=item, bound_targets=request['bound_targets'], message=message,
                              source='manual_readback' if manual else 'official_readback')
                if request.get('approval_source'):
                    result['approval_source'] = request['approval_source']
                self.store.execute('UPDATE company_operations SET state=?,result=?,updated_at=? WHERE id=? AND state=?',
                                   (state, dumps(result), now(), operation['id'], operation['state']))
                self._record(operation['id'], state, message, stamp=time.time(), success=True)
                project_id = self.store.session(operation['session_id'])['project_id']
                self.store.event(operation['session_id'], 'company.space_observed',
                                 {'id': operation['id'], 'project_id': project_id, 'state': state, 'item': item,
                                  'source':result['source']})
                adopted = True
        return adopted

    def poll(self, clock=None):
        stamp = time.time() if clock is None else clock
        if not self.poll_lock.acquire(blocking=False):
            return
        try:
            if not self.company.config()['lpai_enabled'] or not self.company.locator('lpai-agent'):
                return
            rows = self.store.rows('''SELECT o.* FROM company_operations o JOIN company_space_reads r ON r.operation_id=o.id
                WHERE o.state IN ('requested','submitted','unknown','pending_approval') AND r.next_check<=?
                AND r.state NOT IN ('observed_active','observed_stopped','authority_changed','owner_unavailable')
                ORDER BY r.next_check,o.rowid LIMIT 2''', (stamp,))
            for operation in rows:
                if self.company.stop_event.is_set():
                    return
                with self.store.lock:
                    read = self.store.one('SELECT * FROM company_space_reads WHERE operation_id=?', (operation['id'],))
                    reason = self._eligible(operation, read)
                    if reason == 'inflight':
                        continue
                    if reason:
                        self._record(operation['id'], reason, '原连接、凭据修订、绑定或项目已变化；保留原操作，未按新身份核对')
                        continue
                    target = self._target(operation)
                    # Persist the reservation before releasing the short lock.
                    self.store.execute('UPDATE company_space_reads SET next_check=? WHERE operation_id=?', (stamp + 60, operation['id']))
                error = None
                item = None
                try:
                    from .company import platform_item, checked_id
                    response = self.company._run([self.company.locator('lpai-agent'), '--timeout', '20', 'devspaces', 'get',
                        '--namespace', checked_id(target['scope']), '--name', checked_id(target['name'])], timeout=25)
                    if not isinstance(response, dict) or response.get('status') != 'ok':
                        raise ValueError('官方读取未确认成功')
                    item = platform_item(response.get('data'), target['scope'], 'space')
                    if (item['uid'], item['name'], item['namespace']) != (target['uid'], target['name'], target['scope']):
                        raise ValueError('官方返回了其他UID或同名新空间')
                except (ValueError, TimeoutError, OSError) as exc:
                    error = exc
                if self.company.stop_event.is_set():
                    return
                with self.store.lock:
                    current = self.store.one('SELECT * FROM company_operations WHERE id=?', (operation['id'],))
                    if not current or current['request_hash'] != operation['request_hash'] or current['state'] not in PENDING:
                        continue
                    reason = self._eligible(current, read)
                    if reason:
                        self._record(operation['id'], reason, '读取期间原连接、凭据修订、绑定或项目已变化；未采用旧读回')
                        continue
                    if error:
                        # Only exact official not-found evidence changes the
                        # binding projection, never the lifecycle/approval state.
                        missing = isinstance(error, ValueError) and any(re.search(pattern, str(error)) for pattern in (
                            r'HTTP 404: \[NOT_FOUND\]\s+notebooks/' + re.escape(target['name']) + r'\s+not found(?:\s|$)',
                            r'HTTP 404: \[DEVSPACE_NOT_FOUND\][ \t]+devspace not found\s*\Z'))
                        if missing:
                            bound = self.store.one("SELECT observation FROM company_bindings WHERE project_id=? AND kind='lpai'", (json.loads(read['identity'])['project_id'],))
                            item = {**json.loads(bound['observation']), 'phase': 'not_found', 'observed_at': now()}
                            self.store.execute("UPDATE company_bindings SET observation=? WHERE project_id=? AND kind='lpai' AND scope=? AND uid=? AND name=?",
                                (dumps(item), json.loads(read['identity'])['project_id'], target['scope'], target['uid'], target['name']))
                            self._record(operation['id'], 'not_found', '官方未找到原空间；保留原启停请求，不推断删除或审批拒绝', stamp=stamp, delay=300)
                        else:
                            self._record(operation['id'], 'read_failed', '只读核对暂未成功，按退避继续；原操作与审批状态保留', stamp=stamp, error=error)
                        continue
                    previous = self.store.one("SELECT observation FROM company_bindings WHERE project_id=? AND kind='lpai'", (json.loads(read['identity'])['project_id'],))
                    self.store.execute("UPDATE company_bindings SET observation=? WHERE project_id=? AND kind='lpai' AND scope=? AND uid=? AND name=?",
                        (dumps(item), json.loads(read['identity'])['project_id'], target['scope'], target['uid'], target['name']))
                    if not self.observe(item, operation['id']):
                        changed = self._record(operation['id'], 'waiting', '已读回原空间当前状态；继续核对原请求，不推断审批结果', stamp=stamp, success=True)
                        if not changed and json.loads(previous['observation']).get('phase') != item['phase']:
                            self.store.event(operation['session_id'], 'company.space_observation',
                                             {'id': operation['id'], 'project_id': json.loads(read['identity'])['project_id'],
                                              'state': 'waiting', 'item': item})
        finally:
            self.poll_lock.release()

    def status(self, operations):
        if not operations:
            return None
        operation = operations[0]
        read = self.store.one('SELECT * FROM company_space_reads WHERE operation_id=?', (operation['id'],))
        fields = ('state', 'next_check', 'last_checked_at', 'last_success_at', 'failures', 'error_type', 'message')
        value = {key: read[key] for key in fields} if read else {
            'state': 'legacy_manual_only', 'next_check': None, 'last_checked_at': None, 'last_success_at': None,
            'failures': 0, 'error_type': None, 'message': '旧操作未冻结认证修订，请手动精确读回原空间；不会自动重发'}
        if operation['state'] not in PENDING:
            value['next_check'] = None
            phase = operation.get('item', {}).get('phase', '').lower()
            observed = ('observed_active' if operation['action'] == 'lpai_start' and phase in {'ready', 'running'} else
                        'observed_stopped' if operation['action'] == 'lpai_stop' and phase == 'stopped' else None)
            if observed:
                checked = operation['item'].get('observed_at')
                value.update(state=observed, last_checked_at=checked, last_success_at=checked,
                             message=operation.get('message', '已精确读回原空间状态'))
        return {'operation_id': operation['id'], **value}
