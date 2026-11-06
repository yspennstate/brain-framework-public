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
    try:
        try:
            endpoint = json.loads((HERE/'endpoint.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            _spawn(argv)
            return None
        if endpoint['host'] != '127.0.0.1' or endpoint['store'] != requested_store(argv):
            return None
        stamp, environment = code_stamp(endpoint.get('model_files',())), env_key()
        if endpoint['stamp'] != stamp or endpoint['environment'] != environment:
            if endpoint['stamp'] != stamp:
                _spawn(argv)      # a stale on-demand server leaves within 30 s; a later start takes its place
            return None
        signature=endpoint['code']
        # 5 Nov 2026: 30 s / cap 60 sent 23 of 513 calls that day to a COLD run (store + model load in the caller, up to
        # 623 s under load) while the sequential server still finished the abandoned call, and every client queued
        # behind it timed out too. Waiting for the warm answer is cheaper than loading a second copy.
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
        if response.get('ok') != 1 or type(response.get('rc')) is not int:
            return None
        stdout = base64.b64decode(response['stdout'],validate=True)
        stderr = base64.b64decode(response['stderr'],validate=True)
        if hashlib.sha256(stdout).hexdigest() != response['stdout_sha256']:
            return None
        if hashlib.sha256(stderr).hexdigest() != response['stderr_sha256']:
            return None
        # Optional test receipt is local to this candidate folder, numbers only.
        audit = os.environ.get('MATH_RECALL_CLIENT_AUDIT_FILE')
        if audit:
            from pathlib import Path
            target = Path(audit).resolve()
            if target.parent == HERE:
                target.write_text(json.dumps({'server':1,'numpy':int('numpy' in sys.modules),
                                             'onnxruntime':int('onnxruntime' in sys.modules),
                                             'math_brain':int('math_brain' in sys.modules)})+'\n',encoding='utf-8')
    except Exception:
        return None
    # A failed transport cannot emit any partial output before fallback.
    _emit(sys.stdout,stdout)
    _emit(sys.stderr,stderr)
    return response['rc']
