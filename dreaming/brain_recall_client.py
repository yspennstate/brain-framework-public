"""brain_recall_client.py - send a `brain.py recall` call to brain_recall_server.py; None means run it cold.

Imported by brain.py before its heavy imports. Cheap (os, sys, json, socket). Any failure returns None, so the
caller always has the cold path. When no server answers, it starts one windowless (at most one start a minute)
and returns None; the next call is served.
"""
import json, os, socket, subprocess, sys, time

D = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(D, "brain_recall_server")
ENDPOINT = os.path.join(STATE_DIR, "endpoint.json")
SPAWN_STAMP = os.path.join(STATE_DIR, "spawn.stamp")


def _spawn():
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        try:
            if time.time() - os.stat(SPAWN_STAMP).st_mtime < 60:
                return
        except OSError:
            pass
        with open(SPAWN_STAMP, "w") as f:
            f.write(str(time.time()))
        exe = sys.executable
        if exe.lower().endswith("pythonw.exe"):
            exe = exe[:-5] + ".exe"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        # 6 Nov 2026: BRS_/BRL_ (the server's own switches) are stripped too. A test harness running with
        # BRS_SAMPLE=0 BRL_LOG=0 started the shared server, and every caller after it ran with the slow-call
        # sampler and the listing log off.
        env = {k: v for k, v in os.environ.items() if not k.startswith(("BRAIN_", "BRS_", "BRL_"))}
        subprocess.Popen([exe, "-B", os.path.join(D, "brain_recall_server.py")], cwd=D, env=env,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=flags, close_fds=True, start_new_session=(os.name == "posix"))
    except Exception:
        pass


def _request(req, connect_timeout=0.5, read_timeout=180.0):
    with open(ENDPOINT, encoding="utf-8") as f:
        ep = json.load(f)
    req["token"] = ep["token"]
    s = socket.create_connection(("127.0.0.1", int(ep["port"])), timeout=connect_timeout)
    try:
        s.settimeout(read_timeout)
        s.sendall((json.dumps(req) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(1 << 16)
            if not chunk:
                break
            buf += chunk
    finally:
        s.close()
    return json.loads(buf.decode("utf-8")) if buf else None


CLIENT_LOG = os.path.join(STATE_DIR, "client.jsonl")


def _note(outcome, env):
    """One row per recall CLI call (3 Nov 2026): served, or why it ran cold, so the share of recalls the server
    actually answers is measured rather than guessed. BRS_CLIENT_LOG=0 turns it off."""
    if os.environ.get("BRS_CLIENT_LOG", "1") == "0":
        return
    try:
        sess = (env.get("CLAUDE_CODE_SESSION_ID") or env.get("CODEX_THREAD_ID") or "")[:8]
        with open(CLIENT_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"t": round(time.time(), 1), "out": outcome, "sess": sess,
                                 "brain_env": sorted(k for k in env if k.startswith("BRAIN_"))}) + "\n")
    except Exception:
        pass


def run(argv):
    env = {k: v for k, v in os.environ.items() if k.startswith(("BRAIN_", "CLAUDE", "CODEX", "AGENT"))}
    if os.environ.get("BRAIN_RECALL_SERVER", "1") == "0":
        _note("off", env)
        return None
    try:
        reply = _request({"op": "recall", "argv": list(argv), "env": env, "cwd": os.getcwd()})
    except (OSError, ValueError, KeyError) as e:
        # spawn unless the caller carries a BRAIN_ setting the server would decline (the server applies
        # BRAIN_RECALL_REFRESH per call and ignores the client's own BRAIN_RECALL_SERVER switch; 3 Nov 2026)
        if not any(k.startswith("BRAIN_") and k not in ("BRAIN_RECALL_REFRESH", "BRAIN_RECALL_LOG", "BRAIN_RECALL_SERVER")
                   for k in os.environ):
            _spawn()
        _note("no server: " + type(e).__name__, env)
        return None
    if not reply or reply.get("decline") or "rc" not in reply:
        if reply and str(reply.get("decline", "")).startswith("code changed"):
            _spawn()
        _note("declined: " + str((reply or {}).get("decline") or "empty reply"), env)
        return None
    _note("served", env)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.stdout.write(reply.get("out", ""))
    if reply.get("err"):
        sys.stderr.write(reply["err"])
    sys.stdout.flush()
    return int(reply.get("rc") or 0)
