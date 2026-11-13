"""Thin fail-open client: imports no math-brain, NumPy or ONNX modules."""
import base64
import hashlib
import json
import os
import socket
import sys
import time
from recall_protocol import HERE, CONTEXT_ENV, code_stamp, env_key, requested_store, send_frame, receive_frame

def _emit(stream, data):
    if hasattr(stream,'buffer'):
        stream.buffer.write(data)
        stream.buffer.flush()
    else:
        stream.write(data.decode('utf-8',errors='replace'))
        stream.flush()

_RETRY = {'used': False}
_COLD = {'logged': False}

def _note_cold(why, t0):
    """Log a fallback to the cold path: why, the seconds spent on the server first, and (at exit) the cold run's
    own seconds, one row per process in recall_cold_calls.jsonl. The keeper's stand-down threshold assumes a cold
    run costs about half a minute; under memory pressure it can cost far more, and this row is the measurement.
    Never raises."""
    if _COLD['logged']:
        return
    _COLD['logged'] = True
    try:
        import atexit
        t1 = time.monotonic()
        log = HERE/'recall_cold_calls.jsonl'
        def _write():
            try:
                row = dict(at=time.strftime('%Y-%m-%dT%H:%M:%S'), pid=os.getpid(), why=str(why)[:80],
                           server_s=round(t1-t0, 2), cold_s=round(time.monotonic()-t1, 2))
                with open(log, 'a', encoding='utf-8') as fh:
                    fh.write(json.dumps(row)+'\n')
            except Exception:
                pass
        atexit.register(_write)
    except Exception:
        pass

def _spawn(argv):
    """Start a server for this call's store when none answers (6 Nov 2026): on Linux and macOS nothing else starts one
    (install.py registers no keeper there), so every math recall ran cold. POSIX only; Windows keeps its logon
    launcher. At most one start a minute (spawn.stamp); the server leaves after 20 idle minutes, at 6 h, or once its
    code is stale. It inherits this caller's environment, so its environment key is the caller's. The server's own
    probe still refuses a box short of memory or disk, and its per-store lock refuses a second one.
    MATH_RECALL_SPAWN=0 turns this off."""
    if os.name == 'nt' or os.environ.get('MATH_RECALL_SPAWN') == '0':
        return None
    try:
        store = requested_store(argv)
        if not store:
            return None
        stamp_file = HERE/'spawn.stamp'
        try:
            if time.time()-stamp_file.stat().st_mtime < 60:
                return None
        except OSError:
            pass
        stamp_file.write_text('%.0f\n' % time.time(),encoding='utf-8')
        import subprocess
        log = open(HERE/'recall_server_stdio.log','a',encoding='utf-8')
        child = subprocess.Popen([sys.executable,str(HERE/'recall_server.py'),'--store',store,'--max-age','21600',
                                  '--idle-exit','1200','--exit-on-stale'],cwd=str(HERE),stdin=subprocess.DEVNULL,
                                 stdout=log,stderr=log,close_fds=True,start_new_session=True)
        log.close()
        return child.pid
    except Exception:
        return None


def try_server(argv):
    if os.environ.get('MATH_RECALL_SERVER_DISABLE') == '1':
        return None
    t0 = time.monotonic()
    why = 'unknown'
    r = _try_server(argv)
    if isinstance(r, tuple):
        why, r = r
    if r is None and not _RETRY['used']:
        _note_cold(why, t0)
    return r

def _try_server(argv):
    try:
        try:
            endpoint = json.loads((HERE/'endpoint.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            _spawn(argv)
            return None
        if endpoint['host'] != '127.0.0.1' or endpoint['store'] != requested_store(argv):
            return 'other store', None
        stamp, environment = code_stamp(endpoint.get('model_files',())), env_key()
        if endpoint['stamp'] != stamp or endpoint['environment'] != environment:
            if endpoint['stamp'] != stamp:
                _spawn(argv)      # a stale on-demand server leaves within 30 s; a later start takes its place
            return 'code or environment differs', None
        signature=endpoint['code']
        # A short timeout sends a busy call to a COLD run (store and model loaded again in the caller, minutes under
        # load) while the sequential server still finishes the abandoned call, and the clients queued behind it time
        # out too. Waiting for the warm answer is cheaper than loading a second copy.
        timeout = min(600.0,max(.01,float(os.environ.get('MATH_RECALL_SERVER_TIMEOUT','180.0'))))
        deadline = time.monotonic()+timeout
        try:
            sock = socket.create_connection(('127.0.0.1',int(endpoint['port'])),timeout=min(.1,timeout))
        except ConnectionRefusedError:
            _spawn(argv)          # endpoint.json outlived its server
            return None
        with sock:
            sock.settimeout(timeout)
            send_frame(sock,dict(token=endpoint['token'],code=signature,environment=environment,
                                 argv=list(argv),program=sys.argv[0],
                                 context={k:os.environ.get(k) for k in CONTEXT_ENV},cwd=os.getcwd()))
            response = receive_frame(sock,deadline)
        if response.get('ok') != 1 and response.get('why') == 'store_moved_during_call' and not _RETRY['used'] \
                and deadline-time.monotonic() > 30:
            # The store was replaced while the call ran. Asked again, the server reloads once and answers warm,
            # which is far cheaper than a cold run with a second copy of the store in memory. One retry only.
            _RETRY['used'] = True
            try:
                return _try_server(argv)
            finally:
                _RETRY['used'] = False
        if response.get('ok') != 1 or type(response.get('rc')) is not int:
            return 'refused: %s' % response.get('why'), None
        stdout = base64.b64decode(response['stdout'],validate=True)
        stderr = base64.b64decode(response['stderr'],validate=True)
        if hashlib.sha256(stdout).hexdigest() != response['stdout_sha256']:
            return 'stdout digest', None
        if hashlib.sha256(stderr).hexdigest() != response['stderr_sha256']:
            return 'stderr digest', None
        # Optional test receipt is local to this candidate folder, numbers only.
        audit = os.environ.get('MATH_RECALL_CLIENT_AUDIT_FILE')
        if audit:
            from pathlib import Path
            target = Path(audit).resolve()
            if target.parent == HERE:
                target.write_text(json.dumps({'server':1,'numpy':int('numpy' in sys.modules),
                                             'onnxruntime':int('onnxruntime' in sys.modules),
                                             'math_brain':int('math_brain' in sys.modules)})+'\n',encoding='utf-8')
    except Exception as e:
        return type(e).__name__, None
    # A failed transport cannot emit any partial output before fallback.
    _emit(sys.stdout,stdout)
    _emit(sys.stderr,stderr)
    return response['rc']
