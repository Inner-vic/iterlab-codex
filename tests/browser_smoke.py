"""Owned, offline real-Edge acceptance; run only this file via run-quiet.ps1."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import traceback
from http.server import ThreadingHTTPServer
from unittest.mock import patch

PROJECT = Path(__file__).resolve().parents[1]
# test_core is a top-level test module, not an installed tests package.
sys.path.insert(0, str(PROJECT))
sys.path.insert(0, str(PROJECT / 'tests'))
from test_core import FakeRPC
from lab.kernel import Kernel
from lab.research import plan_digest
from lab.store import now
from lab.web import Application, Handler


class OfflineRPC(FakeRPC):
    """No process is launched, and unexpected native methods fail closed."""
    instances = []
    allowed = {'model/list', 'thread/resume', 'thread/read', 'thread/turns/list',
               'turn/start', 'turn/interrupt'}

    def __init__(self, command, cwd, *callbacks):
        assert command == ['browser-smoke-in-memory-only']
        assert Path(cwd).resolve() != (PROJECT / '.data').resolve()
        super().__init__(command, cwd, *callbacks)
        self.turn_number = 0
        self.instances.append(self)

    def request(self, method, params, timeout=60):
        assert method in self.allowed, f'Unexpected native operation: {method}'
        if method == 'turn/start':
            self.turn_number += 1
            turn_id = f'browser-smoke-turn-{self.turn_number}'
            self.calls.append((method, params))
            self.on_event({'method': 'turn/started', 'params': {
                'threadId': params['threadId'], 'turn': {'id': turn_id}}})
            return {'turn': {'id': turn_id}}
        return super().request(method, params, timeout)


class SmokeApplication(Application):
    def route(self, method, path, query, data):
        if path.startswith('/api/browser-smoke/'):
            assert method == 'POST', 'Fixture controls require authenticated POST'
            action = path.rsplit('/', 1)[-1]
            sid = self.fixture['session_a']
            if action == 'snapshot':
                cursor = self.store.event(sid, 'browser-smoke.snapshot', {})
                return {'cursor': cursor}
            if action == 'delta':
                self.kernel.on_event({'method': 'item/agentMessage/delta', 'params': {
                    'threadId': self.store.session(sid)['thread_id'],
                    'turnId': 'seed-tool-turn', 'itemId': 'seed-assistant',
                    'delta': '\nOFFLINE_DELTA_APPEND'}})
                return {'cursor': self.store.one('SELECT MAX(seq) AS seq FROM events')['seq']}
            if action == 'complete':
                session = self.store.session(data['session_id'])
                assert session['active_turn'], 'Fixture has no active turn to complete'
                self.kernel.on_event({'method': 'turn/completed', 'params': {
                    'threadId': session['thread_id'],
                    'turn': {'id': session['active_turn'], 'status': 'completed'}}})
                return {'completed': True}
            if action == 'unknown':
                sid = self.fixture['session_b']
                session = self.store.session(sid)
                assert not session['active_turn']
                stamp = now()
                self.store.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?,?)', (
                    'browser-smoke-original-unknown', sid, 'OFFLINE_UNKNOWN_ORIGINAL',
                    'queue', 'unknown', None, 'OFFLINE_LOST_ACK_ORIGINAL', stamp, stamp))
                self.store.patch_session(sid, status='unknown')
                self.unknown_before = self.store.one(
                    "SELECT * FROM deliveries WHERE id='browser-smoke-original-unknown'")
                self.store.event(sid, 'delivery.unknown', {'id': self.unknown_before['id']})
                return {'delivery': self.unknown_before}
            if action == 'state':
                return {
                    'deliveries': self.store.rows('SELECT * FROM deliveries ORDER BY rowid'),
                    'drafts': self.store.rows('SELECT * FROM drafts ORDER BY rowid'),
                    'reviews': self.store.rows('SELECT * FROM research_reviews ORDER BY rowid'),
                    'calls': [call for rpc in OfflineRPC.instances for call in rpc.calls],
                    'human_pauses': self.store.rows('SELECT * FROM delivery_human_pauses'),
                    'offline': True,
                }
            raise AssertionError(f'Unknown fixture control: {action}')
        return super().route(method, path, query, data)


class QuietHandler(Handler):
    def log_message(self, *args):
        pass


def seed(app):
    store = app.store
    project = store.create_project('OFFLINE browser acceptance')
    a = store.create_session(project['id'], 'codex', 'OFFLINE Session A')
    b = store.create_session(project['id'], 'codex', 'OFFLINE Session B')
    for session, name in [(a, 'a'), (b, 'b')]:
        store.patch_session(session['id'], thread_id='browser-smoke-thread-' + name)
    # B is saved first so that the review dialog initially requests A.
    plans = {}
    for name, session in [('B', b), ('A', a)]:
        body = f'# OFFLINE_PLAN_{name}\n\nEXACT_{name}_BODY_ONLY\n\nSynthetic immutable proposal {name}; no computation or external resources.'
        plans[name] = store.add_record(session['id'], {
            'kind': 'plan', 'title': f'OFFLINE Plan {name}', 'summary': f'Synthetic plan {name}',
            'content': body, 'status': 'proposed', 'evidence': [],
        }, {'origin': 'browser-smoke-offline'})
    tool_id = store.message(a['id'], 'seed-tool', 'tool', 'commandExecution',
                           'SYNTHETIC_TOOL_ONLY\nNo command was executed.\nStable selectable details.',
                           'completed', 'seed-tool-turn')
    assistant_id = store.message(a['id'], 'seed-assistant', 'assistant', 'message',
                                'OFFLINE_SEEDED_ASSISTANT', 'completed', 'seed-tool-turn')
    store.draft(a['id'], 'OFFLINE_INITIAL_DRAFT', 0)
    app.fixture = {
        'session_a': a['id'], 'session_b': b['id'], 'project_id': project['id'],
        'tool_message_id': tool_id, 'assistant_message_id': assistant_id,
        'plans': {name: {'id': plan['id'], 'digest': plan_digest(plan),
                         'body': plan['content'], 'session_id': plan['session_id']}
                  for name, plan in plans.items()},
    }
    return app.fixture


def main():
    directory = tempfile.TemporaryDirectory(prefix='iterlab-browser-smoke-')
    root = Path(directory.name)
    app = server = thread = child = None
    forbidden = []
    cleanup_errors = []

    def no_external(*args, **kwargs):
        forbidden.append('external adapter/command invoked')
        raise AssertionError('OFFLINE fixture forbids real model, CLI, cloud, SDK and experiments')

    def kernel_factory(store):
        kernel = Kernel(store, OfflineRPC)
        kernel.settings.command = lambda *args: ['browser-smoke-in-memory-only']
        kernel.settings.verify_channel = lambda: None
        kernel.settings.value['channel'] = 'codex'
        kernel.company.locator = lambda name: None
        kernel.company.runner = no_external
        kernel.company._run = no_external
        kernel.company.lpai = no_external
        kernel.company.gitlab = no_external
        kernel.company.training.sdk = no_external
        kernel.research.run = no_external
        # No scheduler or company monitor can introduce timing or autonomous work.
        kernel.company.start_monitor = lambda *args: None
        kernel.continuation.start = lambda: None
        return kernel

    code = 1
    try:
        # Settings performs executable discovery at construction; make it offline too.
        with patch('lab.settings.shutil.which', return_value=None):
            app = SmokeApplication(root, kernel_factory)
        fixture = seed(app)
        assert not app.kernel.company.config()['lpai_enabled']
        assert not app.kernel.company.config()['gitlab_enabled']
        server = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        server.daemon_threads = True
        server.app = app
        assert server.server_port not in {8781, 8769}
        thread = threading.Thread(target=server.serve_forever, name='owned-browser-smoke-http')
        thread.start()
        node = shutil.which('node')
        assert node, 'Installed Node with builtin WebSocket/fetch is required'
        env = {**os.environ, 'ITERLAB_CODEX_DATA': str(root),
               'PYTHONPATH': os.pathsep.join([str(PROJECT), str(PROJECT / 'tests')])}
        child = subprocess.Popen(
            [node, str(PROJECT / 'tests' / 'browser-smoke.mjs'),
             f'http://127.0.0.1:{server.server_port}'],
            cwd=PROJECT, env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8',
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        output, errors = child.communicate(json.dumps(fixture), timeout=150)
        print(output, end='')
        print(errors, end='', file=sys.stderr)
        assert child.returncode == 0, f'Browser child exit {child.returncode}'
        assert forbidden == [], 'Expected zero real adapter/command/model/cloud/SDK requests'
        deliveries = app.store.rows('SELECT * FROM deliveries ORDER BY rowid')
        assert len(deliveries) == 4, deliveries  # two user sends, one review, one seeded unknown
        assert [d['state'] for d in deliveries[:3]] == ['accepted'] * 3, deliveries
        assert [d['text'] for d in deliveries[:2]] == ['OFFLINE_DOUBLE_ENTER', 'OFFLINE_DELAYED_SEND']
        assert deliveries[-1] == app.unknown_before, 'Pause must preserve exact original unknown row'
        reviews = app.store.rows('SELECT * FROM research_reviews')
        assert len(reviews) == 1 and reviews[0]['plan_id'] == fixture['plans']['B']['id']
        assert reviews[0]['plan_digest'] == fixture['plans']['B']['digest']
        assert reviews[0]['decision'] == 'reject'
        for plan in fixture['plans'].values():
            assert app.store.one('SELECT content FROM records WHERE id=?', (plan['id'],))['content'] == plan['body']
        for table in ['research_runs', 'research_grants', 'company_training', 'company_operations']:
            assert app.store.rows('SELECT * FROM ' + table) == [], f'Expected no real requests in {table}'
        calls = [call for rpc in OfflineRPC.instances for call in rpc.calls]
        assert len([m for m, p in calls if m == 'turn/start']) == 3, calls
        assert all(m in OfflineRPC.allowed for m, p in calls)
        assert app.store.one('SELECT text FROM drafts WHERE session_id=?', (fixture['session_b'],))['text'] == 'OFFLINE_NEWER_B_DRAFT'
        assert len(app.store.rows('SELECT * FROM delivery_human_pauses')) == 1
        print('PASS offline invariants: zero real commands/model/cloud/SDK/experiment requests; exact delivery/review/unknown identities')
        code = 0
    except Exception:
        traceback.print_exc()
    finally:
        # Ask the child to finish its own browser/profile cleanup; never kill existing Edge.
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.communicate(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate()
        if server is not None:
            server.shutdown()
        if thread is not None:
            thread.join(5)
            if thread.is_alive():
                cleanup_errors.append('owned HTTP thread remained alive')
        if server is not None:
            server.server_close()
        if app is not None:
            try:
                app.close()
                assert all(rpc.closed for rpc in OfflineRPC.instances)
                assert app.kernel.company.monitor_thread is None
            except Exception as exc:
                cleanup_errors.append(str(exc))
        try:
            directory.cleanup()
            assert not root.exists()
            if server is not None:
                assert server.fileno() == -1
            if child is not None:
                assert child.poll() is not None
        except Exception as exc:
            cleanup_errors.append(str(exc))
        if cleanup_errors:
            print('FAIL cleanup: ' + '; '.join(cleanup_errors), file=sys.stderr)
            code = 1
        else:
            print('PASS cleanup: owned Node, HTTP server/thread, FakeRPC, Store and temporary root closed')
    return code


if __name__ == '__main__':
    raise SystemExit(main())
