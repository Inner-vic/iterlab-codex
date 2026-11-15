// No packages: installed Edge, Node builtin fetch/WebSocket, and direct CDP only.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtemp, readFile, readdir, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {setTimeout as delay} from 'node:timers/promises';

const base = process.argv[2];
const serverURL = new URL(base);
assert.equal(serverURL.hostname, '127.0.0.1');
assert(!['8781', '8769'].includes(serverURL.port));
assert.equal(serverURL.protocol, 'http:');
assert.equal(typeof WebSocket, 'function', 'Node builtin WebSocket required');
let input = '';
for await (const chunk of process.stdin) input += chunk;
const fixture = JSON.parse(input);
const edgePath = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
const profile = await mkdtemp(join(tmpdir(), 'iterlab-edge-smoke-'));
let browser, browserCDP, page, exitPromise, browserError, browserStderr = '';
let stopping = false;
const pageErrors = [], externalRequests = [], failedRequests = [], responses = [], requests = [];
const eventErrors = [];
let fatalTimeout;
const watchdog = new Promise((_, reject) => {
  fatalTimeout = setTimeout(() => reject(Error('Owned browser acceptance deadline (110s) exceeded')), 110000);
});

class CDP {
  constructor(socket) {
    this.socket = socket;
    this.sequence = 0;
    this.pending = new Map();
    this.listeners = new Map();
    socket.addEventListener('message', event => {
      const packet = JSON.parse(String(event.data));
      if (packet.id) {
        const pending = this.pending.get(packet.id);
        if (!pending) return;
        clearTimeout(pending.timer);
        this.pending.delete(packet.id);
        if (packet.error) pending.reject(Error(JSON.stringify(packet.error)));
        else pending.resolve(packet.result);
      } else {
        for (const listener of this.listeners.get(packet.method) || []) {
          Promise.resolve().then(() => listener(packet.params)).catch(error => eventErrors.push(error.stack));
        }
      }
    });
    socket.addEventListener('close', () => {
      for (const {reject, timer} of this.pending.values()) {
        clearTimeout(timer);
        reject(Error('Owned CDP socket closed'));
      }
      this.pending.clear();
    });
  }
  static async open(url) {
    const socket = new WebSocket(url);
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => {socket.close(); reject(Error('CDP connect timeout'));}, 6000);
      socket.addEventListener('open', () => {clearTimeout(timer); resolve();}, {once: true});
      socket.addEventListener('error', () => {clearTimeout(timer); reject(Error('CDP connect failed'));}, {once: true});
    });
    return new CDP(socket);
  }
  on(method, listener) {
    const listeners = this.listeners.get(method) || [];
    listeners.push(listener);
    this.listeners.set(method, listeners);
  }
  call(method, params = {}, timeout = 6000) {
    const id = ++this.sequence;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {this.pending.delete(id); reject(Error(`CDP ${method} timed out`));}, timeout);
      this.pending.set(id, {resolve, reject, timer});
      this.socket.send(JSON.stringify({id, method, params}));
    });
  }
  close() {this.socket.close();}
}

function ownedBrowserExited() {
  if (!browser || browser.exitCode !== null || browser.signalCode !== null) return true;
  // Some installed Edge builds do not notify Node's libuv child-exit watcher
  // after Browser.close/taskkill. Confirm the exact owned PID is gone instead.
  try {process.kill(browser.pid, 0); return false;}
  catch (error) {if (error.code === 'ESRCH') return true; throw error;}
}

async function waitFor(label, predicate, timeout = 10000) {
  const deadline = Date.now() + timeout;
  let last;
  while (Date.now() < deadline) {
    if (browserError) throw browserError;
    if (eventErrors.length) throw Error(eventErrors.join('\n'));
    last = await predicate();
    if (last) return last;
    await delay(25);
  }
  throw Error(`Timed out: ${label}; last=${JSON.stringify(last)}`);
}

async function evaluate(expression) {
  const result = await page.call('Runtime.evaluate', {
    expression, returnByValue: true, awaitPromise: true, userGesture: true,
  });
  if (result.exceptionDetails) throw Error(JSON.stringify(result.exceptionDetails));
  return result.result.value;
}
const js = value => JSON.stringify(value);
const promptValue = () => evaluate('document.querySelector("#prompt").value');
const uiReady = sid => evaluate(`location.hash===${js('#session/' + sid)} && document.querySelector('#session-title').textContent===${js(sid === fixture.session_a ? 'OFFLINE Session A' : 'OFFLINE Session B')} && !!document.querySelector('[data-plan-review]') && !!window.IterLabExplorationMap`);
async function typePrompt(text) {
  await evaluate(`document.querySelector('#prompt').focus(); document.querySelector('#prompt').value=${js(text)}; document.querySelector('#prompt').dispatchEvent(new Event('input',{bubbles:true}));`);
}
async function click(selector) {
  await evaluate(`(() => {const element=document.querySelector(${js(selector)}); if(!element||element.disabled||element.hidden)throw Error('Unavailable click '+${js(selector)});element.click();})()`);
}
async function selectSession(sid) {
  const title = sid === fixture.session_a ? 'OFFLINE Session A' : 'OFFLINE Session B';
  await click(`.session-row[title="${title}"]`);
  await waitFor('selected session ' + title, () => uiReady(sid));
}
async function api(path, body) {
  return evaluate(`(async()=>{const r=await fetch(${js(path)}, {method:${js(body === undefined ? 'GET' : 'POST')},headers:{'X-Workbench-Token':document.querySelector('meta[name=workbench-token]').content,'X-Workbench-Assets':document.querySelector('meta[name=workbench-assets]').content,'Content-Type':'application/json'},${body === undefined ? '' : 'body:' + js(JSON.stringify(body)) + ','}});const value=await r.json();if(!r.ok)throw Error('Fixture API '+r.status+' '+JSON.stringify(value));return value;})()`);
}
const state = () => api('/api/browser-smoke/state', {});
const passes = [];
function pass(name) {passes.push(name); console.log('PASS ' + name);}

// Response gates do not fake acknowledgements: real isolated server responses
// are held by CDP and then fulfilled byte-for-byte in the selected order.
const holds = new Map();
function hold(path) {
  assert(!holds.has(path), 'Gate already exists: ' + path);
  const gate = {path, packet: null};
  holds.set(path, gate);
  return gate;
}
async function gated(gate) {
  return waitFor('response gate ' + gate.path, () => gate.packet);
}
async function release(gate, mutate) {
  const packet = await gated(gate);
  const body = await page.call('Fetch.getResponseBody', {requestId: packet.requestId});
  let bytes = Buffer.from(body.body, body.base64Encoded ? 'base64' : 'utf8');
  if (mutate) bytes = Buffer.from(JSON.stringify(mutate(JSON.parse(bytes.toString('utf8')))));
  const headers = (packet.responseHeaders || []).filter(h => !['content-length', 'content-encoding', 'transfer-encoding'].includes(h.name.toLowerCase()));
  headers.push({name: 'Content-Length', value: String(bytes.length)});
  holds.delete(gate.path);
  await page.call('Fetch.fulfillRequest', {
    requestId: packet.requestId, responseCode: packet.responseStatusCode,
    responseHeaders: headers, body: bytes.toString('base64'),
  });
}

async function scenarios() {
  browser = spawn(edgePath, [
    '--headless', '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
    `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check',
    '--disable-background-networking', '--disable-component-update', '--disable-sync',
    '--disable-default-apps', '--disable-extensions', '--disable-domain-reliability',
    '--disable-features=msEdgeSidebarV2,msEdgeShoppingAssistant,msEdgeCollections',
    '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
    '--no-proxy-server', '--metrics-recording-only', '--window-size=1440,1000', 'about:blank',
  ], {windowsHide: true, stdio: ['ignore', 'ignore', 'ignore']});
  browser.on('error', error => {browserError = error;});
  exitPromise = new Promise(resolve => browser.once('exit', (code, signal) => resolve({code, signal})));
  browser.once('exit', (code, signal) => {browserStderr += `\nOWNED_PROCESS_EXIT pid=${browser.pid} code=${code} signal=${signal}`;});
  const debugInfo = await waitFor('owned Edge DevToolsActivePort', async () => {
    try {return (await readFile(join(profile, 'DevToolsActivePort'), 'utf8')).trim().split(/\r?\n/);}
    catch (error) {if (error.code !== 'ENOENT') throw error; return false;}
  });
  assert(!['8781', '8769'].includes(debugInfo[0]));
  const debugBase = `http://127.0.0.1:${debugInfo[0]}`;
  browserCDP = await CDP.open(`ws://127.0.0.1:${debugInfo[0]}${debugInfo[1]}`);
  const created = await browserCDP.call('Target.createTarget', {url: 'about:blank'});
  const targets = await (await fetch(debugBase + '/json/list', {signal: AbortSignal.timeout(5000)})).json();
  const target = targets.find(t => t.id === created.targetId);
  assert(target, 'Owned page target not found');
  page = await CDP.open(target.webSocketDebuggerUrl);
  page.on('Runtime.exceptionThrown', event => pageErrors.push(JSON.stringify(event.exceptionDetails)));
  page.on('Runtime.consoleAPICalled', event => {
    if (event.type === 'error') pageErrors.push('console.error: ' + event.args.map(a => a.description || a.value).join(' '));
  });
  page.on('Log.entryAdded', event => {
    if (event.entry.level === 'error' && event.entry.source !== 'network') pageErrors.push('page log: ' + event.entry.text);
  });
  page.on('Network.requestWillBeSent', event => {
    requests.push({url: event.request.url, method: event.request.method, body: event.request.postData});
    if (/^https?:/.test(event.request.url) && new URL(event.request.url).origin !== base) externalRequests.push(event.request.url);
  });
  page.on('Network.responseReceived', event => responses.push({url: event.response.url, status: event.response.status}));
  page.on('Network.loadingFailed', event => {if (!stopping && !event.canceled) failedRequests.push(event.errorText);});
  page.on('Fetch.requestPaused', async packet => {
    const url = new URL(packet.request.url);
    if (/^https?:/.test(url.protocol) && url.origin !== base) {
      externalRequests.push(url.href);
      await page.call('Fetch.failRequest', {requestId: packet.requestId, errorReason: 'BlockedByClient'});
      return;
    }
    const gate = holds.get(url.pathname);
    if (packet.responseStatusCode && gate) {
      assert(!gate.packet, 'Duplicate response at deterministic gate');
      gate.packet = packet;
      return;
    }
    await page.call('Fetch.continueRequest', {requestId: packet.requestId});
  });
  await Promise.all(['Page.enable', 'Runtime.enable', 'Network.enable', 'Log.enable'].map(method => page.call(method)));
  await page.call('Network.setCacheDisabled', {cacheDisabled: true});
  await page.call('Fetch.enable', {patterns: [
    {urlPattern: '*', requestStage: 'Request'},
    {urlPattern: `${base}/api/records/*`, requestStage: 'Response'},
    {urlPattern: `${base}/api/sessions/*/send`, requestStage: 'Response'},
  ]});
  await page.call('Emulation.setDeviceMetricsOverride', {width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false});
  await page.call('Page.navigate', {url: `${base}/#session/${fixture.session_a}`});
  await waitFor('real app initialized with seeded draft', async () => await uiReady(fixture.session_a) && await promptValue() === 'OFFLINE_INITIAL_DRAFT');
  const sourceRoot = join(dirname(fileURLToPath(import.meta.url)), '..', 'static');
  const modules = (await readdir(sourceRoot)).filter(name => name.endsWith('.js'));
  for (const name of [...modules, 'vendor/exploration-map.js']) {
    assert(responses.some(r => new URL(r.url).pathname.endsWith('/' + name) && r.status === 200), `Real UI module not loaded: ${name}`);
  }
  assert.deepEqual(pageErrors, [], 'No page initialization/runtime errors');
  pass('real app.js, all current UI modules and exploration map load without page JS errors');

  // Expand real native <details>, select actual browser text, and retain the
  // exact DOM node and selection through an SSE-triggered snapshot and delta.
  await click(`[data-message="activity:seed-tool-turn"] .activity-heading`);
  await click('[data-item="' + fixture.tool_message_id + '"] summary');
  await evaluate(`(() => {const d=document.querySelector('[data-item="${fixture.tool_message_id}"] details');if(!d.open)throw Error('Tool details did not open');window.__smokeDetails=d;const text=d.querySelector('pre').firstChild;const r=document.createRange();r.setStart(text,0);r.setEnd(text,19);const s=getSelection();s.removeAllRanges();s.addRange(r);window.__smokeSelection=s.toString();})()`);
  const snapshotCount = responses.filter(r => new URL(r.url).pathname === '/api/sessions/' + fixture.session_a).length;
  await api('/api/browser-smoke/snapshot', {});
  await waitFor('SSE-driven full session snapshot', () => responses.filter(r => new URL(r.url).pathname === '/api/sessions/' + fixture.session_a).length > snapshotCount);
  await waitFor('snapshot applied without replacing tool details', () => evaluate(`document.querySelector('[data-item="${fixture.tool_message_id}"] details')===window.__smokeDetails && window.__smokeDetails.open && getSelection().toString()===window.__smokeSelection`));
  await api('/api/browser-smoke/delta', {});
  await waitFor('real SSE assistant delta displayed', () => evaluate(`document.querySelector('[data-message="${fixture.assistant_message_id}"] .message-body').textContent.includes('OFFLINE_DELTA_APPEND')`));
  assert(await evaluate('window.__smokeDetails.open && getSelection().toString()===window.__smokeSelection'));
  assert.equal(await evaluate(`document.querySelector('[data-message="${fixture.assistant_message_id}"] .message-body').textContent.split('OFFLINE_DELTA_APPEND').length-1`), 1);
  pass('open tool details, keyed DOM identity and native text selection survive snapshot and SSE delta');

  await typePrompt('OFFLINE_DOUBLE_ENTER');
  await waitFor('draft autosaved by actual UI', async () => (await state()).drafts.some(d => d.session_id === fixture.session_a && d.text === 'OFFLINE_DOUBLE_ENTER'));
  // Two synchronous DOM keyboard events intentionally exercise the same-turn
  // guard; browser CDP keyboard events may cross awaits and cannot prove this race.
  await evaluate(`(() => {const p=document.querySelector('#prompt');for(let i=0;i<2;i++)p.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',bubbles:true,cancelable:true}));})()`);
  await waitFor('one native FakeRPC delivery accepted', async () => {
    const s = await state();
    return s.deliveries.length === 1 && s.deliveries[0].state === 'accepted';
  });
  assert.equal(await promptValue(), '');
  let persisted = await state();
  assert.equal(persisted.calls.filter(([m]) => m === 'turn/start').length, 1);
  assert.equal(requests.filter(r => new URL(r.url).pathname === `/api/sessions/${fixture.session_a}/send`).length, 1);
  assert(persisted.calls.some(([m, p]) => m === 'turn/start' && p.input.some(i => i.type === 'text' && i.text === 'OFFLINE_DOUBLE_ENTER')));
  await api('/api/browser-smoke/complete', {session_id: fixture.session_a});
  pass('real draft autosave + synchronous double Enter produces exactly one send and one native FakeRPC delivery');

  await typePrompt('OFFLINE_DELAYED_SEND');
  const delayedSend = hold(`/api/sessions/${fixture.session_a}/send`);
  await click('#send');
  await gated(delayedSend);
  await selectSession(fixture.session_b);
  await typePrompt('OFFLINE_NEWER_B_DRAFT');
  await release(delayedSend);
  await waitFor('delayed send finished', () => evaluate('!document.querySelector("#send").disabled'));
  assert.equal(await promptValue(), 'OFFLINE_NEWER_B_DRAFT');
  await waitFor('newer B draft persisted', async () => (await state()).drafts.some(d => d.session_id === fixture.session_b && d.text === 'OFFLINE_NEWER_B_DRAFT'));
  persisted = await state();
  assert.equal(persisted.deliveries.length, 2);
  assert.equal(persisted.deliveries[1].session_id, fixture.session_a);
  assert.equal(persisted.deliveries[1].text, 'OFFLINE_DELAYED_SEND');
  await api('/api/browser-smoke/complete', {session_id: fixture.session_a});
  pass('switching sessions while real send response is held preserves the newer destination draft');

  await selectSession(fixture.session_a);
  await waitFor('completed A turn projection', async () => !(await api('/api/sessions/' + fixture.session_a)).session.active_turn);
  await click('[data-tab="experiments"]');
  const planAPath = '/api/records/' + fixture.plans.A.id;
  const planBPath = '/api/records/' + fixture.plans.B.id;
  const reviewOpen = async () => {
    await click('[data-plan-review]');
    await waitFor('review dialog open', () => evaluate('document.querySelector("#research-review-dialog").open'));
  };
  const choosePlan = id => evaluate(`(() => {const s=document.querySelector('#research-review-dialog [name=plan_id]');if(s.disabled)throw Error('Plan selector unavailable');s.value=${js(id)};s.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  const preview = () => evaluate(`(() => {const d=document.querySelector('#research-review-dialog');return {body:d.querySelector('[data-content]').textContent,plan:d.querySelector('[name=plan_id]').value,acceptDisabled:d.querySelector('[type=submit]').disabled,rejectDisabled:d.querySelector('[data-reject]').disabled};})()`);
  let aGate = hold(planAPath), bGate = hold(planBPath);
  await reviewOpen();
  await gated(aGate);
  assert((await preview()).acceptDisabled && (await preview()).rejectDisabled);
  await choosePlan(fixture.plans.B.id);
  await gated(bGate);
  assert((await preview()).acceptDisabled && (await preview()).rejectDisabled);
  await release(bGate);
  await waitFor('exact newer B preview enabled', async () => {
    const p = await preview();
    return p.plan === fixture.plans.B.id && p.body.includes('EXACT_B_BODY_ONLY') && !p.rejectDisabled && !p.acceptDisabled;
  });
  await release(aGate);
  await waitFor('stale A response fully handled', () => evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(()=>resolve(true))))'));
  let shown = await preview();
  assert(shown.body.includes('EXACT_B_BODY_ONLY') && !shown.body.includes('EXACT_A_BODY_ONLY'));
  assert.equal(shown.plan, fixture.plans.B.id);
  await click('#research-review-dialog [data-close]');
  pass('delayed A then B record fetches: newest exact B body/digest wins; late A cannot overwrite preview');

  // Opposite arrival order: stale A returns while B is pending. No stale
  // preview or enabled review action may appear during the gap.
  aGate = hold(planAPath); bGate = hold(planBPath);
  await reviewOpen(); await gated(aGate);
  await choosePlan(fixture.plans.B.id); await gated(bGate);
  await release(aGate);
  await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
  shown = await preview();
  assert(shown.acceptDisabled && shown.rejectDisabled);
  assert(!shown.body.includes('EXACT_A_BODY_ONLY') && !shown.body.includes('EXACT_B_BODY_ONLY'));
  await release(bGate);
  await waitFor('B preview after stale A completed', async () => !(await preview()).rejectDisabled);
  assert((await preview()).body.includes('EXACT_B_BODY_ONLY'));
  await click('#research-review-dialog [data-close]');
  pass('opposite A/B arrival order keeps review actions disabled until exact current B preview arrives');

  // Deliberately alter only the isolated browser response digest. The real
  // saved record remains intact; mismatched identity must never enable action.
  aGate = hold(planAPath);
  await reviewOpen(); await gated(aGate);
  await release(aGate, record => ({...record, plan_digest: fixture.plans.B.digest}));
  await waitFor('digest mismatch rejected', async () => (await preview()).body.includes('预览载入失败'));
  shown = await preview();
  assert(shown.acceptDisabled && shown.rejectDisabled);
  assert(!shown.body.includes('EXACT_A_BODY_ONLY'));
  await click('#research-review-dialog [data-close]');
  await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
  pass('wrong frozen digest fails closed with no review POST');

  await reviewOpen();
  await waitFor('exact A reopened', async () => (await preview()).body.includes('EXACT_A_BODY_ONLY'));
  await choosePlan(fixture.plans.B.id);
  await waitFor('exact B ready for rejection', async () => (await preview()).body.includes('EXACT_B_BODY_ONLY') && !(await preview()).rejectDisabled);
  await evaluate(`(() => {const f=document.querySelector('#research-review-dialog form');f.elements.reason.value='OFFLINE exact B preview verified; return synthetic proposal';f.elements.autonomous.checked=false;f.elements.autonomous.dispatchEvent(new Event('change',{bubbles:true}));})()`);
  const reviewPostsBefore = requests.filter(r => new URL(r.url).pathname === '/api/research/review').length;
  assert.equal(reviewPostsBefore, 0, 'Preview-only and mismatched digest must cause zero review writes');
  await click('#research-review-dialog [data-reject]');
  await waitFor('exact B review saved', async () => (await state()).reviews.length === 1);
  await waitFor('review continuation native delivery accepted', async () => (await state()).deliveries.length === 3 && (await state()).deliveries[2].state === 'accepted');
  const reviewRequests = requests.filter(r => new URL(r.url).pathname === '/api/research/review');
  assert.equal(reviewRequests.length, 1);
  const posted = JSON.parse(reviewRequests[0].body);
  assert.equal(posted.session_id, fixture.session_a);
  assert.equal(posted.plan_id, fixture.plans.B.id);
  assert.equal(posted.expected_digest, fixture.plans.B.digest);
  assert.equal(posted.decision, 'reject');
  assert.notEqual(fixture.plans.B.session_id, fixture.session_a, 'Same-project authorship differs intentionally');
  await api('/api/browser-smoke/complete', {session_id: fixture.session_a});
  pass('review POST uses exact previewed B ID/digest with selected same-project A session, not record authorship');

  await selectSession(fixture.session_b);
  const original = (await api('/api/browser-smoke/unknown', {})).delivery;
  await waitFor('seeded unknown pause action', () => evaluate(`[...document.querySelectorAll('#queue button')].some(b=>b.textContent==='保留未知并暂停')`));
  assert(await evaluate('document.querySelector("#send").disabled'), 'Unknown barrier blocks replacement send');
  await evaluate(`[...document.querySelectorAll('#queue button')].find(b=>b.textContent==='保留未知并暂停').click()`);
  assert(await evaluate('document.querySelector("#unknown-delivery-dialog").open'));
  await evaluate(`(() => {const f=document.querySelector('#unknown-delivery-dialog form');f.elements.reason.value='OFFLINE preserve exact lost ACK; pause without replay';f.elements.confirmed.checked=true;f.requestSubmit();})()`);
  await waitFor('unknown exact pause persisted', async () => (await state()).human_pauses.length === 1);
  await waitFor('unknown pause UI updated', () => evaluate(`[...document.querySelectorAll('#queue button')].some(b=>b.textContent==='原未知已保留并暂停'&&b.disabled)`));
  const pauses = requests.filter(r => new URL(r.url).pathname.endsWith('/pause'));
  assert.equal(pauses.length, 1);
  assert.equal(new URL(pauses[0].url).pathname, `/api/sessions/${fixture.session_b}/deliveries/${original.id}/pause`);
  assert.equal(JSON.parse(pauses[0].body).confirmed, true);
  persisted = await state();
  assert.deepEqual(persisted.deliveries.find(d => d.id === original.id), original);
  assert.equal(persisted.calls.filter(([m]) => m === 'turn/start').length, 3, 'Unknown pause cannot replay or initiate a real request');
  assert.equal(await promptValue(), 'OFFLINE_NEWER_B_DRAFT');
  pass('unknown pause posts exact original endpoint with explicit confirmation; original row/error/barrier/draft preserved, no replay');

  assert.deepEqual(pageErrors, [], 'Expected no page JavaScript/runtime/console errors');
  assert.deepEqual(eventErrors, [], 'Expected no CDP interception errors');
  assert.deepEqual(externalRequests, [], 'Expected ZERO real/off-origin browser requests');
  assert.deepEqual(failedRequests, [], 'Expected no failed application resource/API requests');
  assert(responses.every(r => r.status < 400 || new URL(r.url).pathname === '/favicon.ico'), 'Application HTTP request failed');
  assert(!requests.some(r => /\/api\/company\/(lpai|gitlab|action|check|prepare)|\/api\/service\/stop|\/api\/research\/autonomy/.test(new URL(r.url).pathname)), 'Offline acceptance must not invoke cloud/SDK/deployment/autonomy endpoints');
  assert.equal(persisted.offline, true);
  pass('explicit offline expected-zero assertions: no external browser requests, real adapter operations, failed modules or page runtime errors');
}

try {
  await Promise.race([scenarios(), watchdog]);
  console.log(`Browser acceptance: ${passes.length} scenarios passed`);
} catch (error) {
  process.exitCode = 1;
  console.error('FAIL browser acceptance: ' + error.stack);
  if (browserStderr) console.error('Owned Edge stderr: ' + browserStderr);
  if (page) {
    try {console.error('DOM diagnostics: ' + await evaluate(`JSON.stringify({title:document.querySelector('#session-title')?.textContent,prompt:document.querySelector('#prompt')?.value,toast:document.querySelector('#toast')?.textContent,dialogs:[...document.querySelectorAll('dialog[open]')].map(d=>({id:d.id,text:d.textContent.slice(0,2000)}))})`));}
    catch { /* Original failure remains primary. */ }
  }
} finally {
  clearTimeout(fatalTimeout);
  stopping = true;
  try {
    if (browserCDP && browser && browser.exitCode === null) {
      try {await browserCDP.call('Browser.close', {}, 3000);} catch { /* Socket may close before reply. */ }
    }
    // Close target sockets before waiting for the process handle on Windows;
    // outstanding CDP/SSE activity must not retain Node's browser exit watcher.
    page?.close(); browserCDP?.close();
    if (browser && browser.exitCode === null) {
      const graceful = await Promise.race([exitPromise.then(() => true), delay(7000).then(() => false)]);
      if (!graceful) {
        // Kill only the PID launched above and its owned process tree. Never
        // match process names, existing browser profiles, or another server.
        if (process.platform === 'win32') {
          const killer = spawn('taskkill.exe', ['/PID', String(browser.pid), '/T', '/F'], {windowsHide: true, stdio: ['ignore', 'pipe', 'pipe']});
          killer.stdout.on('data', chunk => {browserStderr += chunk;});
          killer.stderr.on('data', chunk => {browserStderr += chunk;});
          await new Promise((resolve, reject) => {killer.once('exit', resolve); killer.once('error', reject);});
        } else browser.kill('SIGKILL');
        await waitFor('exact owned Edge PID exit', ownedBrowserExited, 15000);
      }
    }
    assert(ownedBrowserExited(), 'Owned Edge must exit before profile cleanup');
    // Release a stale libuv watcher only after independent PID-exit verification.
    browser?.unref();
    page?.close(); browserCDP?.close();
    await rm(profile, {recursive: true, force: true, maxRetries: 20, retryDelay: 100});
    let exists = true;
    try {await readdir(profile);} catch (error) {if (error.code === 'ENOENT') exists = false; else throw error;}
    assert.equal(exists, false, 'Owned temporary browser profile must be removed');
    assert(ownedBrowserExited(), 'Owned Edge must remain exited after profile cleanup');
    console.log('PASS cleanup: only owned Edge stopped; temporary user-data-dir removed after browser exit');
  } catch (error) {
    process.exitCode = 1;
    console.error('FAIL owned browser cleanup: ' + error.stack + '\n' + browserStderr);
  }
}
