"""One bounded native research-method review against a prepared isolated store.

Uses configured official channel/model, no fallback, training or platform tools.
Preparation, evidence provenance and scoring criteria live in the case directory.
No personal paths/identities are embedded in this portable driver.
"""
import argparse
import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lab.store import Store
from lab.kernel import Kernel, RECORD_TOOL, SNAPSHOT_TOOL, RESEARCH_TOOLS, RESEARCH_INSTRUCTIONS
from lab.settings import Settings
from lab.transport import CodexRPC
from lab.research_modules import sha


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def run(case, settings_dir):
    ledger_path = case / 'ledger.json'
    ledger = json.loads(ledger_path.read_bytes())
    if ledger['state'] != 'prepared' or ledger.get('native_turns_requested', 0):
        raise ValueError('原案例已请求或状态未知；检查原收据，不重复投递')
    settings = Settings(settings_dir)
    config = settings.value
    model = config['agent_roles']['models']['planner'] if config.get('agent_roles') else config.get('model')
    identity = {key: config.get(key) for key in ['channel', 'profile', 'effort']}
    identity['model'] = model
    if identity != ledger['model_config']:
        raise ValueError('准备时的官方通道/模型/强度已变化，不静默切换')
    settings.verify_channel()
    store = Store(case / 'store')
    kernel = Kernel(store)
    kernel.settings.value.update({**identity, 'agent_roles': None})
    sid = ledger['session_id']
    session = store.session(sid)
    prompt_path = case / 'input.md'
    if sha(prompt_path.read_bytes()) != ledger['input_sha256']:
        raise ValueError('案例输入已改变')
    done = threading.Event()
    state, messages, calls, usage, activities = {}, [], [], [], []
    rpc = None

    def event(packet):
        method, params = packet.get('method'), packet.get('params', {})
        if method == 'turn/started':
            store.patch_session(sid, active_turn=params['turn']['id'], status='running')
        if method == 'item/completed':
            item = params.get('item', {})
            if item.get('type') == 'agentMessage': messages.append(item.get('text', ''))
            elif item.get('type') not in {'reasoning', 'userMessage'}: activities.append(item.get('type'))
        if method == 'thread/tokenUsage/updated': usage.append(params.get('tokenUsage'))
        if method == 'turn/completed': state['turn'] = params.get('turn'); done.set()

    def request(packet, client):
        params = packet.get('params', {})
        tool, args = params.get('tool'), params.get('arguments') or {}
        calls.append({'tool': tool, 'arguments': args})
        allowed = tool in {'research_status', 'research_snapshot', 'research_method'} or tool == 'research_record' and args.get('kind') in {'research', 'decision', 'delivery'}
        if packet.get('method') != 'item/tool/call' or not allowed or len(calls) > 24:
            client.respond(packet['id'], {'contentItems': [{'type': 'inputText', 'text': '本案例仅允许有界只读研究检查和保存判断；不授予执行权限'}], 'success': False})
            return
        kernel.on_request(packet, client)

    started = time.monotonic()
    try:
        ledger['state'] = 'initializing'; save(ledger_path, ledger)
        rpc = CodexRPC(settings.command(identity['channel']), session['workspace'], event, request, lambda *_: done.set())
        rpc.start(timeout=120)
        instructions = kernel.sdm.instructions(sid, RESEARCH_INSTRUCTIONS)
        instructions += '\n本轮是隔离既有研究证据评审。只用注册的研究读取/方法检查/记录工具，不用终端、联网、训练、平台操作、角色交接或方案采纳。保存有依据的研究判断，不伪造本隔离项目执行过历史训练。'
        tools = [RECORD_TOOL, SNAPSHOT_TOOL, *[t for t in RESEARCH_TOOLS if t['name'] in {'research_status', 'research_method'}]]
        result = rpc.request('thread/start', {'cwd': session['workspace'], 'approvalPolicy': 'never', 'sandbox': 'read-only',
            'developerInstructions': instructions, 'dynamicTools': tools})
        thread_id = result['thread']['id']
        store.patch_session(sid, thread_id=thread_id)
        ledger.update(state='requested', thread_id=thread_id, native_turns_requested=1)
        save(ledger_path, ledger)  # Durable before the uncertain write.
        params = {'threadId': thread_id, 'model': model, 'approvalPolicy': 'never', 'sandboxPolicy': {'type': 'readOnly'},
                  'input': [{'type': 'text', 'text': prompt_path.read_text(encoding='utf-8')}]}
        if config.get('effort'): params['effort'] = config['effort']
        reply = rpc.request('turn/start', params, timeout=60)
        ledger['turn_id'] = reply['turn']['id']; save(ledger_path, ledger)
        if not done.wait(360):
            rpc.request('turn/interrupt', {'threadId': thread_id, 'turnId': ledger['turn_id']})
            done.wait(15)
        ledger.update(state='completed' if state.get('turn', {}).get('status') == 'completed' else 'incomplete',
                      native_turn=state.get('turn'), seconds=round(time.monotonic() - started, 3),
                      calls=calls, activities=activities, token_usage=usage[-1] if usage else None,
                      method_reports=store.rows('SELECT id,module,state,plan_id FROM research_method_reports ORDER BY rowid'),
                      research_records=store.rows("SELECT id,kind,title,source FROM records WHERE kind<>'plan' ORDER BY rowid"),
                      training_attempts=len(store.rows('SELECT id FROM research_runs')),
                      cloud_submissions=len(store.rows('SELECT id FROM company_training')))
        ledger['behavioral_state']='needs_manual_review' if ledger['state']=='completed' and ledger['method_reports'] and ledger['research_records'] else 'not_demonstrated'
        # A protocol completion alone is never research-method acceptance.
        store.patch_session(sid,status='idle' if ledger['state']=='completed' else 'unknown',active_turn=None)
        (case / 'response.md').write_text('\n\n'.join(messages), encoding='utf-8')
        save(ledger_path, ledger)
        print(json.dumps({k: ledger.get(k) for k in ['state', 'seconds', 'native_turns_requested', 'training_attempts', 'cloud_submissions', 'method_reports']}, ensure_ascii=False))
    except Exception as exc:
        ledger.update(error_type=type(exc).__name__, seconds=round(time.monotonic() - started, 3))
        save(ledger_path, ledger)
        raise
    finally:
        if rpc: rpc.close()
        kernel.close(); store.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--case-dir', type=Path, required=True)
    parser.add_argument('--settings-dir', type=Path, required=True)
    args = parser.parse_args()
    run(args.case_dir.resolve(), args.settings_dir.resolve())
