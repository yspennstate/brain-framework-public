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

def try_server(argv):
    if os.environ.get('MATH_RECALL_SERVER_DISABLE') == '1':
        return None
    try:
        endpoint = json.loads((HERE/'endpoint.json').read_text(encoding='utf-8'))
        if endpoint['host'] != '127.0.0.1' or endpoint['store'] != requested_store(argv):
            return None
        stamp, environment = code_stamp(endpoint.get('model_files',())), env_key()
        if endpoint['stamp'] != stamp or endpoint['environment'] != environment:
            return None
        signature=endpoint['code']
        # 5 Nov 2026: 30 s / cap 60 sent 23 of 513 calls that day to a COLD run (store + model load in the caller, up to
        # 623 s under load) while the sequential server still finished the abandoned call, and every client queued
        # behind it timed out too. Waiting for the warm answer is cheaper than loading a second copy.
        timeout = min(600.0,max(.01,float(os.environ.get('MATH_RECALL_SERVER_TIMEOUT','180.0'))))
        deadline = time.monotonic()+timeout
        with socket.create_connection(('127.0.0.1',int(endpoint['port'])),timeout=min(.1,timeout)) as sock:
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
