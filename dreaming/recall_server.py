"""Temporary localhost resident recall server. No store writes and no query logging."""
import argparse
import base64
import contextlib
import ctypes
import gc
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import secrets
import socket
import socketserver
import sys
import time
from recall_protocol import HERE, CONTEXT_ENV, DEFAULT_STORE, DEFAULT_BUILD, canonical, code_key, code_stamp, env_key, requested_store, send_frame, receive_frame
from runtime import policy
from server_safety import resources, resident

CAP = int(1.5*1024**3)

def mutex(store):
    digest = hashlib.sha256(canonical(store).encode('utf-8')).hexdigest()
    if os.name != 'nt':
        # one server per store: an advisory lock the kernel drops when the process exits
        import fcntl
        import tempfile
        fh = open(os.path.join(tempfile.gettempdir(), 'R39MathRecall_%s.lock' % digest), 'a')
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            raise RuntimeError('second instance')
        return fh
    kernel = ctypes.windll.kernel32
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p,ctypes.c_int,ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    name = 'Global\\R39MathRecall_' + digest
    handle = kernel.CreateMutexW(None,False,name)
    error = kernel.GetLastError()
    if not handle:
        raise ctypes.WinError()
    if error == 183:
        kernel.CloseHandle(ctypes.c_void_p(handle))
        raise RuntimeError('second instance')
    return handle

class Server(socketserver.TCPServer):
    allow_reuse_address = False
    def server_bind(self):
        if hasattr(socket,'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
        super().server_bind()
    def handle_error(self,request,client_address):
        self.failures += 1

def main():
    policy()
    if os.name == 'nt':
        # Normal, not BelowNormal: the server answers callers waiting at Normal (the cold path runs at Normal in the
        # caller). 4 Nov, 88 pct CPU, same query: a BelowNormal test process 393 s / 397 s against 7.0 / 1.0 s at Normal;
        # the live server, BelowNormal (the keeper's creation flag) and unthrottled, 73 s a call. Set through psutil:
        # kernel32.SetPriorityClass(GetCurrentProcess(), ...) without a pointer restype passes an invalid handle on
        # 64-bit and fails silently (returns 0) - policy()'s own BelowNormal call never took effect either.
        try:
            import psutil
            psutil.Process().nice(psutil.NORMAL_PRIORITY_CLASS)
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument('--store',default=os.environ.get('MATHBRAIN_STORE',DEFAULT_STORE))
    ap.add_argument('--parent-pid',type=int,default=0)
    ap.add_argument('--max-age',type=float,default=0)
    ap.add_argument('--test-controls',action='store_true')
    # A server the client started on demand (recall_client._spawn, POSIX) leaves after --idle-exit seconds without a
    # served call, and with --exit-on-stale once the code it serves changed, so the next caller starts a current one.
    ap.add_argument('--idle-exit',type=float,default=0)
    ap.add_argument('--exit-on-stale',action='store_true')
    args = ap.parse_args()
    if not args.store:
        print("no store: set MATHBRAIN_STORE or pass --store", file=sys.stderr)
        return 2
    # Enforced inside the server too, before resident imports or model creation.
    probe = resources()
    if not probe['allowed']:
        return 4
    handle = mutex(args.store)
    endpoint = HERE/'endpoint.json'
    stop = HERE/'SERVER_STOP'
    server = None
    try:
        if stop.exists():
            return 5
        build = os.environ.get('MATHBRAIN_BUILD_SRC',DEFAULT_BUILD)
        if build:
            sys.path.insert(0,build)
        import math_recall as tool   # serve the live tool; the client's code stamp refuses a server older than it
        import recall_client as _rc
        _rc.try_server = lambda argv: None   # inside the server a call never goes back to the server
        import math_brain.retrieval as ranking
        fr = tool._fast_module()
        assert fr is not None
        # Retain the encoder session and the exact prepared state; no query is used to warm them.
        fr.Encoder().start().wait()
        prepared = fr.open_prepared(args.store,ranking,tool.derive_tool,tool.__file__)
        # 4 Nov 09:23: a peer's code edit restarted the server while MathRecallPrebuild5m was building the new
        # prepared state; the server exited rc 6 with no reason and the keeper backed off 300 s (outage 09:15-09:32).
        # Wait for another builder (up to 10 min) instead of exiting, and log why when it still fails.
        waited = 0
        while prepared is None and 'has been building' in str(fr.STATUS.get('why','')) and waited < 600 \
                and not stop.exists():
            time.sleep(15)
            waited += 15
            prepared = fr.open_prepared(args.store,ranking,tool.derive_tool,tool.__file__)
        if prepared is None:
            try:
                with open(HERE/'recall_server_reasons.log','a',encoding='utf-8') as fh:
                    fh.write('%s pid %d start rc 6 after %d s: %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'),os.getpid(),
                                                                     waited,str(fr.STATUS.get('why',''))[:200]))
            except Exception:
                pass
            return 6
        if max(resident().values()) >= CAP:
            return 7
        encoder=sys.modules.get('query_embed')
        model_files=[]
        if encoder is not None:
            for name in ('model.onnx','tokenizer.json'):
                path=Path(encoder.MODEL_DIR)/name
                if path.exists(): model_files.append(str(path))
        state = dict(store_key=fr._store_key(args.store),code=code_key(),stamp=code_stamp(model_files),environment=env_key(),
                     token=secrets.token_hex(32),requests=0)
        original_open=fr.open_prepared
        held={'key':state['store_key'],'prepared':prepared}
        def warm_open(store,R,derive,caller):
            key=fr._store_key(store)
            if canonical(store)==canonical(args.store) and key==held['key']:
                return held['prepared']
            value=original_open(store,R,derive,caller)
            if canonical(store)==canonical(args.store):
                held.update(key=key,prepared=value)
            return value
        fr.open_prepared=warm_open
        del prepared
        started = time.monotonic()
        reasons = HERE/'recall_server_reasons.log'

        def refuse(sock,why):
            # A reason code per refusal, never the query: 30 Oct a window of ok:0 could not be explained afterwards.
            try:
                with open(reasons,'a',encoding='utf-8') as fh:
                    fh.write('%s pid %d req %d %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'),os.getpid(),state['requests'],why))
            except Exception:
                pass
            send_frame(sock,{'ok':0,'why':why})

        class Handler(socketserver.BaseRequestHandler):
            def handle(self):
                self.request.settimeout(10)
                t0 = time.monotonic()
                reloaded = 0
                try:
                    request = receive_frame(self.request)
                    argv = request.get('argv')
                    why = None
                    if not isinstance(argv,list) or len(argv)>256 or not all(isinstance(x,str) for x in argv):
                        why = 'argv'
                    elif not secrets.compare_digest(str(request.get('token','')),state['token']):
                        why = 'token'
                    elif request.get('code') != state['code']:
                        why = 'code'
                    elif request.get('environment') != state['environment']:
                        why = 'environment'
                    elif requested_store(argv) != canonical(args.store):
                        why = 'store_path'
                    elif code_stamp(model_files) != state['stamp']:
                        why = 'stamp'
                    if why:
                        refuse(self.request,why)
                        return
                    current_key = fr._store_key(args.store)
                    if current_key != state['store_key']:
                        # Drop graph-dependent module caches, retaining query_embed's model/session.
                        importlib.reload(ranking._semantic)
                        importlib.reload(ranking)
                        fr._MEMO.clear()
                        held['key']=None
                        held['prepared']=None
                        reloaded = 1
                        state['store_key'] = current_key
                        # The old prepared state sits in reference cycles; unfreed it pushed the next call to 1.82 GB,
                        # over CAP, and the server stopped itself after every store change (30 Oct: 1,821 -> 1,053 MB).
                        gc.collect()
                    context = request.get('context',{})
                    if not isinstance(context,dict):
                        raise ValueError('context')
                    previous = {k:os.environ.get(k) for k in CONTEXT_ENV}
                    old_argv = sys.argv
                    old_cwd = os.getcwd()
                    out_raw,err_raw = io.BytesIO(),io.BytesIO()
                    out = io.TextIOWrapper(out_raw,encoding='utf-8',errors='replace',newline=None,write_through=True)
                    err = io.TextIOWrapper(err_raw,encoding='utf-8',errors='replace',newline=None,write_through=True)
                    try:
                        cwd=request.get('cwd')
                        if not isinstance(cwd,str):
                            raise ValueError('cwd')
                        os.chdir(cwd)
                        for key in CONTEXT_ENV:
                            value = context.get(key)
                            if value is None:
                                os.environ.pop(key,None)
                            elif isinstance(value,str):
                                os.environ[key] = value
                            else:
                                raise ValueError('context')
                        sys.argv = [str(request.get('program','math_recall.py'))]+argv
                        with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
                            try:
                                rc = tool.main(argv)
                            except SystemExit as exc:
                                rc = exc.code if isinstance(exc.code,int) else 1
                        out.flush()
                        err.flush()
                        stdout,stderr = out_raw.getvalue(),err_raw.getvalue()
                    finally:
                        sys.argv = old_argv
                        os.chdir(old_cwd)
                        for key,value in previous.items():
                            if value is None:
                                os.environ.pop(key,None)
                            else:
                                os.environ[key]=value
                        out.close()
                        err.close()
                    state['requests'] += 1
                    memory = resident()
                    self.server.peak = {key:max(self.server.peak[key],memory[key]) for key in memory}
                    if max(memory.values()) >= CAP:
                        gc.collect()   # judge the cap on live memory, not on garbage awaiting the cycle collector
                        memory = resident()
                    if max(memory.values()) >= CAP:
                        self.server.must_stop=True
                        refuse(self.request,'cap %d MB' % (max(memory.values())>>20))
                        return
                    if fr._store_key(args.store) != current_key:
                        refuse(self.request,'store_moved_during_call')
                        return
                    response = dict(ok=1,rc=int(rc or 0),stdout=base64.b64encode(stdout).decode('ascii'),
                               stderr=base64.b64encode(stderr).decode('ascii'),
                               stdout_sha256=hashlib.sha256(stdout).hexdigest(),stderr_sha256=hashlib.sha256(stderr).hexdigest())
                    if args.test_controls and (HERE/'TEST_TRUNCATE').exists():
                        import struct
                        payload = json.dumps(response,ensure_ascii=True,separators=(',',':')).encode('ascii')
                        self.request.sendall(struct.pack('!I',len(payload))+payload[:7])
                        (HERE/'TEST_RESPONSE_HELD').write_text('1\n',encoding='utf-8')
                        for _ in range(1000):
                            if not (HERE/'TEST_TRUNCATE').exists() or stop.exists():
                                break
                            time.sleep(.01)
                        return
                    # One timing line per served call, never the query (4 Nov: the keeper's probes timed out at 50 s
                    # while a test copy of the same set-up answered in 2-8 s; this says whether the server is slow
                    # or busy - 'idle' is the gap since the previous call ended).
                    try:
                        now = time.monotonic()
                        with open(HERE/'recall_server_timing.log','a',encoding='utf-8') as fh:
                            fh.write('%s pid %d req %d took %.1f idle %.1f reload %d rc %d\n' % (
                                time.strftime('%Y-%m-%d %H:%M:%S'),os.getpid(),state['requests'],now-t0,
                                t0-state.get('last_end',started),reloaded,int(rc or 0)))
                        state['last_end'] = now
                    except Exception:
                        pass
                    send_frame(self.request,response)
                except Exception as exc:
                    self.server.failures += 1
                    try:
                        import traceback
                        frame = traceback.extract_tb(exc.__traceback__)[-1]
                        refuse(self.request,'exception %s at %s:%d' % (type(exc).__name__,os.path.basename(frame.filename),frame.lineno))
                    except Exception:
                        pass

        server = Server(('127.0.0.1',0),Handler)
        server.timeout=.1
        server.failures=0
        server.must_stop=False
        server.peak=resident()
        metadata = dict(host='127.0.0.1',port=server.server_address[1],token=state['token'],
                        store=canonical(args.store),code=state['code'],stamp=state['stamp'],model_files=model_files,
                        environment=state['environment'],pid=os.getpid())
        temporary = HERE/'endpoint.tmp'
        temporary.write_text(json.dumps(metadata),encoding='utf-8')
        os.replace(temporary,endpoint)
        import psutil
        parent = psutil.Process(args.parent_pid) if args.parent_pid else None
        while not stop.exists() and not server.must_stop:
            if parent is not None and (not parent.is_running()):
                break
            if args.max_age and time.monotonic()-started > args.max_age:
                break
            now = time.monotonic()
            if args.idle_exit and now-state.get('last_end',started) > args.idle_exit:
                break
            if args.exit_on_stale and now-state.get('stale_checked',started) > 30:
                state['stale_checked'] = now
                try:
                    if code_stamp(model_files) != state['stamp'] or code_key() != state['code']:
                        break
                except OSError:
                    break
            server.handle_request()
        (HERE/'server_summary.json').write_text(json.dumps(dict(requests=state['requests'],failures=server.failures,
                peak=server.peak,clean_stop=1))+'\n',encoding='utf-8')
        return 0
    finally:
        if server is not None:
            server.server_close()
        # Never remove another generation's endpoint.
        if endpoint.exists():
            try:
                if json.loads(endpoint.read_text(encoding='utf-8')).get('pid') == os.getpid():
                    endpoint.unlink()
            except Exception:
                pass
        release(handle)

def release(handle):
    if os.name != 'nt':
        handle.close()
    else:
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(handle))

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        sys.stderr.write(type(exc).__name__+'\n')
        raise SystemExit(8)
