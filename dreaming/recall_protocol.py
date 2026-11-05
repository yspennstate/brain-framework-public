"""Small stdlib-only protocol helpers shared by the warm recall client and server."""
import hashlib
import json
import os
from pathlib import Path
import socket
import struct

HERE = Path(__file__).resolve().parent
DEFAULT_STORE = str(Path.home() / 'math-brain-build/local/population/brain.sqlite3')
DEFAULT_BUILD = str(Path.home() / 'math-brain-build/src')
MAX_FRAME = 16 * 1024 * 1024
CONTROL_ENV = {'MATH_RECALL_SERVER_DISABLE', 'MATH_RECALL_SERVER_TIMEOUT',
               'MATH_RECALL_CLIENT_AUDIT_FILE'}
CONTEXT_ENV = ('AGENT_MESH_ID', 'AGENT_MESH_SESSION', 'CLAUDE_SESSION_ID', 'CLAUDE_CODE_SESSION_ID',
               'CODEX_THREAD_ID', 'CLAUDE_PROJECT_DIR', 'SESSION_ID')

def canonical(path):
    return os.path.normcase(os.path.abspath(path))

def env_key():
    pairs = [(k,v) for k,v in sorted(os.environ.items())
             if (k.startswith(('MATHBRAIN_', 'MATH_BRAIN_', 'MATH_RECALL_', 'MATH_STRENGTH_'))
                 and k not in CONTROL_ENV)]
    return hashlib.sha256(json.dumps(pairs,ensure_ascii=True).encode('ascii')).hexdigest()

def code_files():
    # fastrecall_delta.py since 4 Nov: the server runs its extend() on every store change, and without it in the
    # stamp a fix to it never reached the resident server (claude-c462c100)
    files = [HERE / name for name in ('candidate_manifest.json','baseline_math_recall.py','math_recall.py','fastrecall.py',
                                     'fastrecall_delta.py','math_strength.py','recall_protocol.py','recall_client.py',
                                     'recall_server.py')]
    package = Path(os.environ.get('MATHBRAIN_BUILD_SRC',DEFAULT_BUILD)) / 'math_brain'
    files += sorted(package.glob('*.py'))
    files.append(Path.home()/'.claude/dreaming/query_embed.py')
    return files

def code_stamp(extra=()):
    rows=[]
    for path in code_files()+[Path(p) for p in extra]:
        st=path.stat()
        rows.append((canonical(path),st.st_size,st.st_mtime_ns))
    return hashlib.sha256(json.dumps(rows,separators=(',',':')).encode('utf-8')).hexdigest()

def code_key():
    digest = hashlib.sha256()
    for path in code_files():
        digest.update(canonical(path).encode('utf-8') + b'\0')
        digest.update(path.read_bytes())
    return digest.hexdigest()

def requested_store(argv):
    path = os.environ.get('MATHBRAIN_STORE',DEFAULT_STORE)
    for index,arg in enumerate(argv):
        if arg == '--store':
            if index + 1 == len(argv):
                return None
            path = argv[index+1]
        elif arg.startswith('--store='):
            path = arg.split('=',1)[1]
    return canonical(path)

def send_frame(sock, payload):
    data = json.dumps(payload,ensure_ascii=True,separators=(',',':')).encode('ascii')
    if len(data) > MAX_FRAME:
        raise ValueError('frame size')
    sock.sendall(struct.pack('!I',len(data)) + data)

def receive_frame(sock, deadline=None):
    import time
    def read(count):
        chunks = []
        while count:
            if deadline is not None:
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                sock.settimeout(remaining)
            data = sock.recv(count)
            if not data:
                raise EOFError()
            chunks.append(data)
            count -= len(data)
        return b''.join(chunks)
    size = struct.unpack('!I',read(4))[0]
    if size > MAX_FRAME:
        raise ValueError('frame size')
    return json.loads(read(size).decode('ascii'))
