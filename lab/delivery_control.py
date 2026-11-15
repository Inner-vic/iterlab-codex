"""Durable delivery evidence and directed interruption, not a second agent loop."""
from __future__ import annotations

import json

from .store import dumps, now, uid


TERMINAL_TURNS = {"completed", "interrupted", "failed", "cancelled"}
SERVICE_SHUTDOWN_REASON='服务关闭，保留原定向中断意图'


def turn_id_from(result):
    if not isinstance(result, dict):
        return None
    turn = result.get("turn") or {}
    value = turn.get("id") if isinstance(turn, dict) else None
    value = value or result.get("turnId")
    return value if isinstance(value, str) and value else None


def echoed_delivery_ids(turn):
    """Only protocol identity fields; never inspect text or infer by ordering."""
    refs = set()
    value = turn.get("clientUserMessageId")
    if isinstance(value, str) and value:
        refs.add(value)
    for item in turn.get("items", []):
        if isinstance(item, dict) and item.get("type") == "userMessage":
            value = item.get("clientUserMessageId")
            if isinstance(value, str) and value:
                refs.add(value)
    return refs


class DeliveryControl:
    def __init__(self, store):
        self.store = store
        # Keep the original nine-column deliveries table and its states intact.
        with store.lock, store.db:
            store.db.executescript("""
            CREATE TABLE IF NOT EXISTS delivery_requests(
              delivery_id TEXT PRIMARY KEY REFERENCES deliveries(id),request_id TEXT NOT NULL UNIQUE,
              method TEXT NOT NULL,thread_id TEXT,generation TEXT NOT NULL,expected_turn_id TEXT,
              observed_turn_id TEXT,finished INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS delivery_receipts(
              id INTEGER PRIMARY KEY AUTOINCREMENT,delivery_id TEXT NOT NULL REFERENCES deliveries(id),
              request_id TEXT NOT NULL,thread_id TEXT,generation TEXT NOT NULL,source TEXT NOT NULL,
              outcome TEXT NOT NULL,turn_id TEXT,payload TEXT NOT NULL,created_at TEXT NOT NULL,
              UNIQUE(request_id,payload));
            CREATE TABLE IF NOT EXISTS delivery_interrupt_intents(
              id TEXT PRIMARY KEY,session_id TEXT NOT NULL REFERENCES sessions(id),
              delivery_id TEXT REFERENCES deliveries(id),thread_id TEXT,turn_id TEXT,state TEXT NOT NULL,
              reason TEXT NOT NULL,error TEXT,generation TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS delivery_human_pauses(
              delivery_id TEXT PRIMARY KEY REFERENCES deliveries(id),session_id TEXT NOT NULL REFERENCES sessions(id),
              reason TEXT NOT NULL,confirmed_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS delivery_turn_observations(
              session_id TEXT NOT NULL REFERENCES sessions(id),thread_id TEXT NOT NULL,turn_id TEXT NOT NULL,
              status TEXT NOT NULL,source TEXT NOT NULL,updated_at TEXT NOT NULL,
              PRIMARY KEY(session_id,thread_id,turn_id));
            """)

    def _one(self, sql, args=()):
        row = self.store.db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def _audit(self, sid, kind, payload):
        # Called inside the same transaction as the CAS, without a nested commit.
        self.store.db.execute("INSERT INTO events(session_id,kind,payload,created_at) VALUES(?,?,?,?)",
                              (sid, kind, dumps(payload), now()))

    def _notify(self):
        with self.store.changed:
            self.store.changed.notify_all()

    def has_unknown(self, sid):
        return bool(self.store.one("SELECT id FROM deliveries WHERE session_id=? AND state='unknown' LIMIT 1", (sid,)))

    def barrier(self, sid):
        return bool(self.store.one("SELECT id FROM deliveries WHERE session_id=? AND state IN ('requested','unknown') LIMIT 1", (sid,))
                    or self.store.one("SELECT id FROM delivery_interrupt_intents WHERE session_id=? AND state<>'completed' LIMIT 1", (sid,)))

    def summary(self, sid):
        with self.store.lock:
            rows = self.store.rows("SELECT id,state FROM deliveries WHERE session_id=? AND state IN ('requested','unknown') ORDER BY rowid", (sid,))
            intents = self.store.rows("SELECT * FROM delivery_interrupt_intents WHERE session_id=? ORDER BY rowid", (sid,))
            pauses = self.store.rows("SELECT * FROM delivery_human_pauses WHERE session_id=? ORDER BY rowid", (sid,))
            return {"barrier": bool(rows or any(row['state'] != 'completed' for row in intents)),
                    "unknown_delivery_ids": [row['id'] for row in rows if row['state'] == 'unknown'],
                    "requested_delivery_ids": [row['id'] for row in rows if row['state'] == 'requested'],
                    "interrupt_intents": intents, "human_pauses": pauses}

    def recover(self, reason):
        with self.store.lock, self.store.db:
            for row in self.store.db.execute("SELECT * FROM deliveries WHERE state='requested'").fetchall():
                self.store.db.execute("UPDATE deliveries SET state='unknown',error=COALESCE(error,?),updated_at=? WHERE id=? AND state='requested'",
                                      (reason, now(), row['id']))
                self._audit(row['session_id'], 'delivery.unknown', {"id": row['id'], "error": row['error'] or reason, "cause": reason})
            self.store.db.execute("UPDATE delivery_interrupt_intents SET state='unknown',error=COALESCE(error,?),updated_at=? WHERE state='requested'",
                                  (reason, now()))
            self.store.db.execute("UPDATE sessions SET status='unknown' WHERE id IN (SELECT session_id FROM deliveries WHERE state='unknown')")
        self._notify()

    def begin(self, delivery, method, params, generation):
        sid = delivery['session_id']
        with self.store.lock, self.store.db:
            if self.barrier(sid):
                return None
            stamp, request_id = now(), uid()
            changed = self.store.db.execute("UPDATE deliveries SET state='requested',updated_at=? WHERE id=? AND session_id=? AND state='queued'",
                                            (stamp, delivery['id'], sid)).rowcount
            if not changed:
                return None
            self.store.db.execute("INSERT INTO delivery_requests(delivery_id,request_id,method,thread_id,generation,expected_turn_id,created_at) VALUES(?,?,?,?,?,?,?)",
                                  (delivery['id'], request_id, method, params.get('threadId'), generation, params.get('expectedTurnId'), stamp))
            if method in {'turn/start', 'thread/compact/start'}:
                self.store.db.execute("UPDATE sessions SET status=?,updated_at=? WHERE id=?",
                                      ('starting' if method == 'turn/start' else 'compacting', stamp, sid))
            self._audit(sid, 'delivery.requested', {"id": delivery['id'], "method": method, "request_id": request_id})
        self._notify()
        return request_id

    def fail(self, delivery_id, error, unknown):
        with self.store.lock, self.store.db:
            row = self._one("SELECT * FROM deliveries WHERE id=?", (delivery_id,))
            if not row:
                return
            state = 'unknown' if unknown else 'rejected'
            changed = self.store.db.execute("UPDATE deliveries SET state=?,error=COALESCE(error,?),updated_at=? WHERE id=? AND state='requested'",
                                            (state, error, now(), delivery_id)).rowcount
            # A receipt may win the race with a timeout. Never downgrade it, but
            # keep the original uncertainty/error available for inspection.
            if not changed and unknown and row['state'] == 'accepted':
                self.store.db.execute("UPDATE deliveries SET error=COALESCE(error,?) WHERE id=?", (error, delivery_id))
            if self.has_unknown(row['session_id']):
                self.store.db.execute("UPDATE sessions SET status='unknown',updated_at=? WHERE id=?", (now(), row['session_id']))
            elif changed and not unknown:
                session = self._one("SELECT active_turn FROM sessions WHERE id=?", (row['session_id'],))
                request = self._one("SELECT observed_turn_id,expected_turn_id FROM delivery_requests WHERE delivery_id=?", (delivery_id,))
                receipts, _ = self._receipt_turns(row)
                if not receipts and (not request or not request['observed_turn_id'] and not request['expected_turn_id']):
                    # Only a definite rejection of a never-bound requested
                    # start can settle a no-target intent. Unknown is immutable
                    # here, including a late error response after timeout.
                    self.store.db.execute("UPDATE delivery_interrupt_intents SET state='completed',updated_at=? WHERE delivery_id=? AND state='pending' AND turn_id IS NULL", (now(), delivery_id))
                self.store.db.execute("UPDATE sessions SET status=?,updated_at=? WHERE id=?",
                                      ('running' if session['active_turn'] else 'idle', now(), row['session_id']))
            self._audit(row['session_id'], 'delivery.unknown' if unknown else 'delivery.error',
                        {"id": delivery_id, "error": row['error'] or error, "cause": error, "changed": bool(changed)})
        self._notify()

    def _receipt_turns(self, delivery):
        turns, sources = set(), set()
        session = self._one("SELECT thread_id FROM sessions WHERE id=?", (delivery['session_id'],))
        request = self._one("SELECT thread_id FROM delivery_requests WHERE delivery_id=?", (delivery['id'],))
        if request and request['thread_id'] != session['thread_id']:
            return turns, sources
        for row in self.store.db.execute("SELECT turn_id,source,thread_id FROM delivery_receipts WHERE delivery_id=? AND outcome='accepted'", (delivery['id'],)):
            if row['turn_id'] and row['thread_id'] == session['thread_id']:
                turns.add(row['turn_id']); sources.add(row['source'])
        # Compatibility with receipts produced before companion tables existed.
        # These are local accepted audit events, not native user text echoes.
        for row in self.store.db.execute("SELECT payload FROM events WHERE session_id=? AND kind='delivery.accepted'", (delivery['session_id'],)):
            payload = json.loads(row['payload'])
            if payload.get('id') == delivery['id'] and isinstance(payload.get('turn_id'), str) and payload['turn_id']:
                turns.add(payload['turn_id']); sources.add('local_accepted_receipt')
        return turns, sources

    def _status(self, sid, turn_id):
        session = self._one("SELECT thread_id FROM sessions WHERE id=?", (sid,))
        row = self._one("SELECT status FROM delivery_turn_observations WHERE session_id=? AND thread_id=? AND turn_id=?",
                        (sid, session['thread_id'], turn_id))
        return row['status'] if row else None

    def _observe(self, sid, thread_id, turn_id, status, source):
        if not thread_id or not turn_id:
            return
        old = self._one("SELECT status FROM delivery_turn_observations WHERE session_id=? AND thread_id=? AND turn_id=?", (sid, thread_id, turn_id))
        if old and old['status'] in TERMINAL_TURNS:
            self.store.db.execute("UPDATE delivery_interrupt_intents SET state='completed',updated_at=? WHERE session_id=? AND thread_id=? AND turn_id=? AND state<>'completed'", (now(), sid, thread_id, turn_id))
            return # Late start/ACK cannot resurrect a completed exact turn.
        self.store.db.execute("INSERT INTO delivery_turn_observations VALUES(?,?,?,?,?,?) ON CONFLICT(session_id,thread_id,turn_id) DO UPDATE SET status=excluded.status,source=excluded.source,updated_at=excluded.updated_at",
                              (sid, thread_id, turn_id, status, source, now()))
        if status in TERMINAL_TURNS:
            self.store.db.execute("UPDATE delivery_interrupt_intents SET state='completed',updated_at=? WHERE session_id=? AND thread_id=? AND turn_id=? AND state<>'completed'",
                                  (now(), sid, thread_id, turn_id))

    def _bind(self, sid, delivery_id, thread_id, turn_id):
        if not turn_id:
            return
        state = 'completed' if self._status(sid, turn_id) in TERMINAL_TURNS else 'ready'
        self.store.db.execute("UPDATE delivery_interrupt_intents SET thread_id=?,turn_id=?,state=?,updated_at=? WHERE session_id=? AND delivery_id=? AND state='pending' AND (turn_id IS NULL OR turn_id=?)",
                              (thread_id, turn_id, state, now(), sid, delivery_id, turn_id))

    def receipt(self, delivery_id, request_id, packet, late=False):
        """Reader-safe: persist correlated ACK evidence; never call an RPC here."""
        target = None
        with self.store.lock, self.store.db:
            delivery = self._one("SELECT * FROM deliveries WHERE id=?", (delivery_id,))
            request = self._one("SELECT * FROM delivery_requests WHERE delivery_id=? AND request_id=?", (delivery_id, request_id))
            if not delivery or not request:
                return None
            sid = delivery['session_id']
            if request['thread_id'] != self._one('SELECT thread_id FROM sessions WHERE id=?', (sid,))['thread_id']:
                self._audit(sid, 'delivery.receipt_conflict', {'id': delivery_id, 'request_id': request_id, 'reason': '原请求 thread 身份与当前会话不同；未应用回执'})
                return None
            result = packet.get('result', {})
            outcome = 'rejected' if 'error' in packet else 'accepted'
            payload = {'error': packet['error']} if outcome == 'rejected' else {'result': result}
            target = turn_id_from(result) if outcome == 'accepted' else None
            inserted = self.store.db.execute("INSERT OR IGNORE INTO delivery_receipts(delivery_id,request_id,thread_id,generation,source,outcome,turn_id,payload,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                                            (delivery_id, request_id, request['thread_id'], request['generation'], 'late_ack_receipt' if late else 'local_accepted_receipt', outcome, target, dumps(payload), now())).rowcount
            if inserted:
                self._audit(sid, 'delivery.receipt', {"id": delivery_id, "request_id": request_id, "outcome": outcome, "turn_id": target, "late": late})
            if outcome != 'accepted':
                return None # A late rejection is not proof the original unknown failed.
            receipt_turns, _ = self._receipt_turns(delivery)
            expected = request['expected_turn_id'] or request['observed_turn_id']
            conflict = len(receipt_turns) > 1 or bool(target and expected and target != expected) or bool(target and delivery['turn_id'] and delivery['turn_id'] != target)
            missing = request['method'] in {'turn/start', 'turn/steer'} and not target
            if conflict or missing:
                error = '原请求的 turn 身份回执冲突，保留未知状态' if conflict else '启动回执没有确切 turn ID；请核对原会话'
                self.store.db.execute("UPDATE deliveries SET state='unknown',error=COALESCE(error,?),updated_at=? WHERE id=? AND state='requested'", (error, now(), delivery_id))
                self.store.db.execute("UPDATE sessions SET status='unknown',updated_at=? WHERE id=?", (now(), sid))
                self._audit(sid, 'delivery.receipt_conflict', {"id": delivery_id, "turn_id": target, "expected_turn_id": expected, "reason": error})
                return None
            if target:
                status = (result.get('turn') or {}).get('status') if isinstance(result, dict) and isinstance(result.get('turn') or {}, dict) else None
                self._observe(sid, request['thread_id'], target, status if status in TERMINAL_TURNS else 'inProgress', 'accepted_receipt')
                self._bind(sid, delivery_id, request['thread_id'], target)
            changed = self.store.db.execute("UPDATE deliveries SET state='accepted',turn_id=?,updated_at=? WHERE id=? AND state='requested'", (target, now(), delivery_id)).rowcount
            if changed:
                self._audit(sid, 'delivery.accepted', {"id": delivery_id, "turn_id": target, "request_id": request_id})
            # Unknown deliveries stay unknown until explicit restore uses the
            # receipt. ACKs can still supply the exact interruption target now.
            session = self._one("SELECT active_turn,status FROM sessions WHERE id=?", (sid,))
            if target and self._status(sid, target) not in TERMINAL_TURNS:
                if not session['active_turn'] or session['active_turn'] == target:
                    self.store.db.execute("UPDATE sessions SET active_turn=?,status=?,updated_at=? WHERE id=?",
                                          (target, 'unknown' if self.has_unknown(sid) else 'waiting_approval' if session['status'] == 'waiting_approval' else 'running', now(), sid))
                else:
                    self.store.db.execute("UPDATE sessions SET status='unknown',updated_at=? WHERE id=?", (now(), sid))
                    self._audit(sid, 'delivery.active_turn_conflict', {"id": delivery_id, "turn_id": target, "active_turn": session['active_turn']})
            elif target and self._status(sid, target) in TERMINAL_TURNS and session['active_turn'] in {None, target}:
                status = self._status(sid, target)
                self.store.db.execute("UPDATE sessions SET active_turn=NULL,status=?,updated_at=? WHERE id=?",
                                      ('unknown' if self.has_unknown(sid) else 'idle' if status == 'completed' else status, now(), sid))
            elif changed and request['method'] == 'thread/compact/start' and request['finished']:
                self.store.db.execute("UPDATE sessions SET status='idle',updated_at=? WHERE id=? AND active_turn IS NULL", (now(), sid))
        self._notify()
        return target

    def started(self, sid, thread_id, turn, generation, extra_echo=None):
        turn_id = turn.get('id')
        if not isinstance(turn_id, str) or not turn_id:
            return
        with self.store.lock, self.store.db:
            self._observe(sid, thread_id, turn_id, 'inProgress', 'turn/started')
            refs = echoed_delivery_ids(turn)
            if isinstance(extra_echo, str) and extra_echo:
                refs.add(extra_echo)
            requests = self.store.rows("SELECT r.*,d.session_id,d.state,d.turn_id,d.text FROM delivery_requests r JOIN deliveries d ON d.id=r.delivery_id WHERE d.session_id=? AND r.thread_id=? AND r.generation=? AND r.method='turn/start' AND d.state IN ('requested','unknown','accepted')", (sid, thread_id, generation))
            candidates = [row for row in requests if (row['delivery_id'] in refs if refs else not row['observed_turn_id'] and row['state'] in {'requested','unknown'})]
            if len(candidates) == 1:
                row = candidates[0]
                receipts, _ = self._receipt_turns({**row, 'id': row['delivery_id']})
                if (not receipts or receipts == {turn_id}) and (not row['observed_turn_id'] or row['observed_turn_id'] == turn_id):
                    # A same-generation, single locally reserved start can bind
                    # an interruption notification. This is NOT acceptance
                    # evidence for restore when clientUserMessageId is absent.
                    self.store.db.execute("UPDATE delivery_requests SET observed_turn_id=? WHERE delivery_id=? AND observed_turn_id IS NULL", (turn_id, row['delivery_id']))
                    self._bind(sid, row['delivery_id'], thread_id, turn_id)
            session = self._one("SELECT active_turn FROM sessions WHERE id=?", (sid,))
            if self._status(sid, turn_id) not in TERMINAL_TURNS:
                if not session['active_turn'] or session['active_turn'] == turn_id:
                    self.store.db.execute("UPDATE sessions SET active_turn=?,status=?,updated_at=? WHERE id=?", (turn_id, 'unknown' if self.has_unknown(sid) else 'running', now(), sid))
                else:
                    self.store.db.execute("UPDATE sessions SET status='unknown',updated_at=? WHERE id=?", (now(), sid))
                    self._audit(sid, 'delivery.active_turn_conflict', {"turn_id": turn_id, "active_turn": session['active_turn']})
        self._notify()

    def completed(self, sid, thread_id, turn_id, status):
        if not isinstance(turn_id, str) or not turn_id:
            return False
        status = status if status in TERMINAL_TURNS else 'completed'
        with self.store.lock, self.store.db:
            self._observe(sid, thread_id, turn_id, status, 'turn/completed')
            session = self._one("SELECT active_turn FROM sessions WHERE id=?", (sid,))
            changed = session['active_turn'] == turn_id
            if changed:
                self.store.db.execute("UPDATE sessions SET active_turn=NULL,status=?,updated_at=? WHERE id=? AND active_turn=?",
                                      ('unknown' if self.has_unknown(sid) else 'idle' if status == 'completed' else status, now(), sid, turn_id))
        self._notify()
        return changed

    def compact_completed(self, sid, thread_id):
        with self.store.lock, self.store.db:
            session = self._one("SELECT active_turn,status FROM sessions WHERE id=?", (sid,))
            requests = self.store.rows("SELECT r.delivery_id FROM delivery_requests r JOIN deliveries d ON d.id=r.delivery_id WHERE d.session_id=? AND r.thread_id=? AND r.method='thread/compact/start' AND r.finished=0 AND d.state IN ('requested','accepted')", (sid, thread_id))
            # Native compaction may be an item inside an ordinary active turn.
            # Only a locally explicit idle /compact owns this release point.
            if session['active_turn'] or session['status'] != 'compacting' or len(requests) != 1:
                return False
            self.store.db.execute("UPDATE delivery_requests SET finished=1 WHERE delivery_id=?", (requests[0]['delivery_id'],))
            self.store.db.execute("UPDATE sessions SET status=?,updated_at=? WHERE id=? AND active_turn IS NULL", ('unknown' if self.has_unknown(sid) else 'idle', now(), sid))
        self._notify()
        return True

    def reconcile(self, sid, thread_id, turns):
        with self.store.lock, self.store.db:
            native, refs, conflicts, item_refs = {}, {}, set(), {}
            duplicate_turn_ids = set()
            for turn in turns:
                turn_id = turn.get('id')
                if not isinstance(turn_id, str) or not turn_id:
                    continue
                if turn_id in native:
                    duplicate_turn_ids.add(turn_id)
                    conflicts.update(echoed_delivery_ids(turn) | echoed_delivery_ids(native[turn_id]))
                native[turn_id] = turn
                for delivery_id in echoed_delivery_ids(turn):
                    refs.setdefault(delivery_id, set()).add(turn_id)
                for index, item in enumerate(turn.get('items', [])):
                    if isinstance(item, dict) and item.get('type') == 'userMessage' and isinstance(item.get('clientUserMessageId'), str):
                        item_refs.setdefault(item['clientUserMessageId'], set()).add((turn_id, item.get('id') or index))
                status = turn.get('status')
                if status in TERMINAL_TURNS or status == 'inProgress':
                    self._observe(sid, thread_id, turn_id, status, 'thread/read')
            conflicts.update(key for key, items in item_refs.items() if len(items) > 1)
            for delivery in self.store.rows("SELECT * FROM deliveries WHERE session_id=? AND state='unknown' ORDER BY rowid", (sid,)):
                receipt_turns, sources = self._receipt_turns(delivery)
                candidates = refs.get(delivery['id'], set()) | receipt_turns
                if delivery['id'] in conflicts or candidates & duplicate_turn_ids or len(candidates) > 1 or (candidates and delivery['turn_id'] and delivery['turn_id'] not in candidates):
                    self._audit(sid, 'delivery.reconcile_conflict', {"id": delivery['id'], "candidate_turn_ids": sorted(candidates), "original_error": delivery['error']})
                    continue
                if len(candidates) != 1:
                    continue # No exact echo/receipt: unknown remains a barrier.
                target = next(iter(candidates))
                if target not in native:
                    continue # A receipt identifies the turn, not its missing current state.
                basis = 'clientUserMessageId' if refs.get(delivery['id']) else 'late_ack_receipt' if 'late_ack_receipt' in sources else 'local_accepted_receipt'
                changed = self.store.db.execute("UPDATE deliveries SET state='accepted',turn_id=?,updated_at=? WHERE id=? AND session_id=? AND state='unknown'", (target, now(), delivery['id'], sid)).rowcount
                if changed:
                    self._bind(sid, delivery['id'], thread_id, target)
                    self._audit(sid, 'delivery.reconciled', {"id": delivery['id'], "turn_id": target, "basis": basis, "original_error": delivery['error']})
            # Resolve stored interruption targets only by their exact turn IDs.
            for intent in self.store.rows("SELECT * FROM delivery_interrupt_intents WHERE session_id=? AND state='pending'", (sid,)):
                if not intent['delivery_id']:
                    continue
                delivery = self._one("SELECT * FROM deliveries WHERE id=?", (intent['delivery_id'],))
                receipt_turns, _ = self._receipt_turns(delivery)
                candidates = refs.get(delivery['id'], set()) | receipt_turns
                if delivery['id'] not in conflicts and not candidates & duplicate_turn_ids and len(candidates) == 1:
                    target = next(iter(candidates))
                    if target in native:
                        self._bind(sid, delivery['id'], thread_id, target)
            # Uncertain interrupts may settle only by exact observed completion.
            # They are never made ready/retried automatically after a restart.
            self.store.db.execute("UPDATE delivery_interrupt_intents SET state='completed',updated_at=? WHERE session_id=? AND thread_id=? AND turn_id IN (SELECT turn_id FROM delivery_turn_observations WHERE session_id=? AND thread_id=? AND status IN ('completed','interrupted','failed','cancelled'))", (now(), sid, thread_id, sid, thread_id))
            active_ids = {key for key, turn in native.items() if turn.get('status') == 'inProgress' and self._status(sid, key) not in TERMINAL_TURNS}
            session = self._one("SELECT active_turn FROM sessions WHERE id=?", (sid,))
            old_active = session['active_turn']
            active = next(iter(active_ids)) if len(active_ids) == 1 else None
            active_state_missing = any(turn.get('status') not in (TERMINAL_TURNS | {'inProgress'}) for turn in native.values())
            unverified = bool(len(active_ids) > 1 or duplicate_turn_ids or active_state_missing)
            if old_active and self._status(sid, old_active) not in TERMINAL_TURNS and old_active != active:
                active, unverified = old_active, True
            state = 'unknown' if self.has_unknown(sid) or unverified or (not active and self.barrier(sid)) else 'running' if active else 'idle'
            self.store.db.execute("UPDATE sessions SET status=?,active_turn=?,updated_at=? WHERE id=?", (state, active, now(), sid))
        self._notify()

    def _capture_interrupts(self, sid, reason, *, pause_research=True):
        session = self._one("SELECT thread_id,active_turn FROM sessions WHERE id=?", (sid,))
        stamp = now()
        if pause_research:
            self.store.db.execute("UPDATE deliveries SET state='cancelled',updated_at=? WHERE session_id=? AND state='queued'", (stamp, sid))
            self.store.db.execute("INSERT INTO research_controls VALUES(?,1,?,?) ON CONFLICT(session_id) DO UPDATE SET paused=1,reason=excluded.reason,updated_at=excluded.updated_at", (sid, reason, stamp))
            self.store.db.execute("UPDATE company_watches SET auto_resume=0 WHERE session_id=?", (sid,))
        targets = set()
        for delivery in self.store.rows("SELECT * FROM deliveries WHERE session_id=? AND state IN ('requested','unknown')", (sid,)):
            request = self._one("SELECT * FROM delivery_requests WHERE delivery_id=?", (delivery['id'],))
            if request and request['method'] == 'thread/compact/start':
                continue # Compaction has no exact turn/interrupt protocol target.
            receipts, _ = self._receipt_turns(delivery)
            target = (request['expected_turn_id'] or request['observed_turn_id']) if request else None
            if not target and len(receipts) == 1:
                target = next(iter(receipts))
            thread_id = request['thread_id'] if request else session['thread_id']
            state = 'completed' if target and self._status(sid, target) in TERMINAL_TURNS else 'ready' if target else 'pending'
            self.store.db.execute("INSERT OR IGNORE INTO delivery_interrupt_intents VALUES(?,?,?,?,?,?,?,?,?,?,?)", ('delivery:' + delivery['id'], sid, delivery['id'], thread_id, target, state, reason, None, None, stamp, stamp))
            if target:
                targets.add(target)
        # An accepted ACK can precede turn/started. Its stored exact identity
        # remains interruptible even when active_turn has not been projected.
        for delivery in self.store.rows("SELECT * FROM deliveries WHERE session_id=? AND state='accepted' AND turn_id IS NOT NULL", (sid,)):
            request = self._one("SELECT thread_id,method FROM delivery_requests WHERE delivery_id=?", (delivery['id'],))
            turn_id = delivery['turn_id']
            thread_id = request['thread_id'] if request else session['thread_id']
            if thread_id != session['thread_id'] or (request and request['method'] == 'thread/compact/start') or self._status(sid, turn_id) in TERMINAL_TURNS:
                continue
            self.store.db.execute("INSERT OR IGNORE INTO delivery_interrupt_intents VALUES(?,?,?,?,?,?,?,?,?,?,?)", ('delivery:' + delivery['id'], sid, delivery['id'], thread_id, turn_id, 'ready', reason, None, None, stamp, stamp))
            targets.add(turn_id)
        if session['active_turn'] and session['active_turn'] not in targets:
            turn_id = session['active_turn']
            state = 'completed' if self._status(sid, turn_id) in TERMINAL_TURNS else 'ready'
            self.store.db.execute("INSERT OR IGNORE INTO delivery_interrupt_intents VALUES(?,?,?,?,?,?,?,?,?,?,?)", ('turn:' + sid + ':' + turn_id, sid, None, session['thread_id'], turn_id, state, reason, None, None, stamp, stamp))
            targets.add(turn_id)
        return sorted(targets)

    def interrupt(self, sid, reason='用户中断研究执行', *, origin='user'):
        if origin not in {'user','service_shutdown'} or origin=='service_shutdown' and reason!=SERVICE_SHUTDOWN_REASON:
            raise ValueError('中断来源与用途无效')
        with self.store.lock, self.store.db:
            session=self.store.session(sid)
            targets=self._capture_interrupts(sid, reason,pause_research=origin=='user')
            self._audit(sid, 'turn.interrupt_requested', {"reason": reason, "durable": True,
                'origin':origin,'thread_id':session['thread_id'],'turn_ids':targets})
        self._notify()

    def pause_unknown(self, sid, delivery_id, confirmed, reason):
        if confirmed is not True or not isinstance(reason, str) or not reason.strip():
            raise ValueError('暂停未知投递需要 confirmed=true 与明确理由；不确认失败或允许重发')
        reason = reason.strip()
        with self.store.lock, self.store.db:
            self.store.session(sid)
            delivery = self._one("SELECT * FROM deliveries WHERE id=? AND session_id=?", (delivery_id, sid))
            if not delivery or delivery['state'] != 'unknown':
                raise ValueError('仅可对本会话原 unknown 投递记录明确人工暂停，不能改写已拒绝或其他状态')
            old = self._one("SELECT * FROM delivery_human_pauses WHERE delivery_id=?", (delivery_id,))
            if old and old['reason'] != reason:
                raise ValueError('原人工暂停已绑定该理由，不能用同一投递身份改写')
            if not old:
                self.store.db.execute("INSERT INTO delivery_human_pauses VALUES(?,?,?,?)", (delivery_id, sid, reason, now()))
                self._capture_interrupts(sid, reason)
                self._audit(sid, 'delivery.human_paused', {"id": delivery_id, "reason": reason, "confirmed": True, "original_error": delivery['error'], "state_preserved": 'unknown'})
        self._notify()
        return not bool(old)

    def ready_intents(self, sid):
        return self.store.rows("SELECT id FROM delivery_interrupt_intents WHERE session_id=? AND state='ready' AND thread_id IS NOT NULL AND turn_id IS NOT NULL", (sid,))

    def claim_interrupt(self, intent_id, generation):
        with self.store.lock, self.store.db:
            intent = self._one("SELECT * FROM delivery_interrupt_intents WHERE id=? AND state='ready'", (intent_id,))
            if not intent or not intent['thread_id'] or not intent['turn_id']:
                return None
            if self._status(intent['session_id'], intent['turn_id']) in TERMINAL_TURNS:
                self.store.db.execute("UPDATE delivery_interrupt_intents SET state='completed',updated_at=? WHERE id=? AND state='ready'", (now(), intent_id))
                return None
            other = self._one("SELECT id FROM delivery_interrupt_intents WHERE thread_id=? AND turn_id=? AND id<>? AND state IN ('requested','acknowledged','unknown','failed') LIMIT 1", (intent['thread_id'], intent['turn_id'], intent_id))
            if other:
                return None # Coalesced exact target; completion will settle both.
            changed = self.store.db.execute("UPDATE delivery_interrupt_intents SET state='requested',generation=?,updated_at=? WHERE id=? AND state='ready'", (generation, now(), intent_id)).rowcount
            if not changed:
                return None
            self._audit(intent['session_id'], 'turn.interrupt_dispatched', {"id": intent_id, "thread_id": intent['thread_id'], "turn_id": intent['turn_id'], "generation": generation})
        self._notify()
        return intent

    def finish_interrupt(self, intent_id, state, error=None):
        with self.store.lock, self.store.db:
            intent = self._one("SELECT * FROM delivery_interrupt_intents WHERE id=?", (intent_id,))
            if not intent:
                return
            changed = self.store.db.execute("UPDATE delivery_interrupt_intents SET state=?,error=COALESCE(error,?),updated_at=? WHERE id=? AND state='requested'", (state, error, now(), intent_id)).rowcount
            if changed:
                self._audit(intent['session_id'], 'turn.interrupt_receipt', {"id": intent_id, "turn_id": intent['turn_id'], "state": state, "error": error})
        self._notify()
