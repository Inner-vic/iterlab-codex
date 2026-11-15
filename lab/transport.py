"""A bidirectional stdio client. No prompt interpreter or tool execution loop."""
from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import uuid
from collections import deque
from concurrent.futures import ThreadPoolExecutor


class RPCError(RuntimeError):
    pass


class RPCUnknown(RPCError):
    """Delivery may have succeeded. Reconcile instead of automatically retrying."""


class CodexRPC:
    def __init__(self, command, cwd, on_event, on_request, on_exit):
        self.command, self.cwd = command, str(cwd)
        self.on_event, self.on_request, self.on_exit = on_event, on_request, on_exit
        self.generation = uuid.uuid4().hex
        self.process = None
        self.pending = {}
        # Keep only explicitly tracked timed-out write requests until this
        # connection closes. A late ACK is evidence, never an automatic retry.
        self.late_pending = {}
        self.lock, self.write_lock = threading.RLock(), threading.Lock()
        self.stderr = deque(maxlen=30)
        self.closed = False
        self.workers = ThreadPoolExecutor(max_workers=4, thread_name_prefix="codex-rpc-request")

    def start(self, *, timeout=120):
        if type(timeout) not in (int,float) or not 0<timeout<=120:
            raise ValueError('初始化等待需为0..120秒，不改变研究请求期限')
        self.process = subprocess.Popen(
            self.command, cwd=self.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.reader = threading.Thread(target=self._read, name="codex-stdout", daemon=True)
        self.error_reader = threading.Thread(target=self._stderr, name="codex-stderr", daemon=True)
        self.reader.start()
        self.error_reader.start()
        result = self.request("initialize", {
            "clientInfo": {"name": "iterlab_codex", "title": "IterLab Codex", "version": "0.1.0"},
            "capabilities": {"experimentalApi": True},
        }, timeout=timeout) # Separate cold bootstrap budget; no turn is started here.
        self.notify("initialized", {})
        return result

    def _send(self, packet):
        with self.write_lock:
            if not self.process or self.process.poll() is not None or self.closed:
                raise RPCError("Codex process is not running")
            try:
                self.process.stdin.write(json.dumps(packet, ensure_ascii=False) + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError) as exc:
                raise RPCUnknown("Codex connection lost while sending; delivery is unknown") from exc

    def request(self, method, params, timeout=60):
        return self._request(method, params, timeout)

    def request_tracked(self, method, params, on_receipt, timeout=60):
        """Persist the exact reply, including late replies after RPCUnknown.

        on_receipt(packet, late) runs in source order on the reader thread. It
        must be local/nonblocking with respect to RPC: enqueue control work,
        never request/wait for a response from the reader itself.
        """
        return self._request(method, params, timeout, on_receipt)

    def _request(self, method, params, timeout, on_receipt=None):
        request_id = uuid.uuid4().hex
        result = queue.Queue(maxsize=1)
        entry = {"waiter": result, "on_receipt": on_receipt, "receipt_seen": False}
        with self.lock:
            self.pending[request_id] = entry
        uncertain = False
        try:
            self._send({"id": request_id, "method": method, "params": params})
            try:
                packet = result.get(timeout=timeout)
            except queue.Empty as exc:
                uncertain = True
                raise RPCUnknown(f"{method} timed out; check the original thread before retrying") from exc
            if isinstance(packet, Exception):
                raise packet
            if "error" in packet:
                raise RPCError(json.dumps(packet["error"], ensure_ascii=False))
            return packet.get("result", {})
        except RPCUnknown:
            uncertain = True
            raise
        finally:
            with self.lock:
                current = self.pending.pop(request_id, None)
                if uncertain and current and on_receipt and not current['receipt_seen'] and not self.closed:
                    self.late_pending[request_id] = current

    def notify(self, method, params):
        self._send({"method": method, "params": params})

    def respond(self, request_id, result=None, error=None):
        self._send({"id": request_id, **({"error": error} if error else {"result": result})})

    def _handle_request(self, packet):
        try:
            # Interactive requests deliberately remain pending until the UI replies.
            self.on_request(packet, self)
        except Exception as exc:
            try:
                self.respond(packet["id"], error={"code": -32603, "message": str(exc)[:1000]})
            except RPCError:
                pass

    def _handle_response(self, packet):
        with self.lock:
            entry = self.pending.get(packet['id'])
            late = entry is None
            if late:
                entry = self.late_pending.pop(packet['id'], None)
            if not entry or entry['receipt_seen']:
                return
            entry['receipt_seen'] = True
        callback_error = None
        if entry['on_receipt']:
            try:
                entry['on_receipt'](packet, late)
            except Exception as exc:
                # A failed persistence callback cannot turn a sent write into a
                # rejection. Keep the native reader alive and mark uncertainty.
                callback_error = RPCUnknown('Codex reply received but local receipt could not be saved')
                self.stderr.append(str(exc)[:1000])
        if not late:
            try:
                entry['waiter'].put_nowait(callback_error or packet)
            except queue.Full:
                pass

    def _read(self):
        failure = None
        try:
            for line in self.process.stdout:
                try:
                    packet = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "method" in packet:
                    if "id" in packet:
                        self.workers.submit(self._handle_request, packet)
                    else:
                        # Keep source event order, including terminal notifications.
                        self.on_event(packet)
                elif "id" in packet:
                    self._handle_response(packet)
        except Exception as exc:
            failure = str(exc)
        finally:
            error = RPCUnknown("Codex process exited; original request state must be reconciled")
            with self.lock:
                for entry in self.pending.values():
                    try:
                        entry['waiter'].put_nowait(error)
                    except queue.Full:
                        pass
                self.late_pending.clear()
            self.on_exit({"generation": self.generation, "expected": self.closed, "error": failure,
                          "exit_code":self.process.poll(),"stderr_present":bool(self.stderr)})

    def _stderr(self):
        for line in self.process.stderr:
            self.stderr.append(line.strip()[:1000])

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.process:
            try:
                self.process.stdin.close()
                self.process.wait(timeout=8)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                if self.process.poll() is None:
                    self.process.terminate()
                    self.process.wait(timeout=8)
        self.workers.shutdown(wait=True, cancel_futures=True)
        for thread in (getattr(self, 'reader', None), getattr(self, 'error_reader', None)):
            if thread and thread is not threading.current_thread():
                thread.join(timeout=3)
        if self.process:
            self.process.stdout.close()
            self.process.stderr.close()
