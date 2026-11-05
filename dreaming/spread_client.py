"""spread_client.py - hand the spread_recall hook's stdin to spread_server.py; None means run the hook cold.

Cheap (os, sys, json, socket, time). Any failure returns None. With no server answering it starts one in the
background (at most once a minute, its own session, no console) and returns None, so the next prompt is served.
Off switch: SPREAD_SERVER=0.
"""
import json, os, socket, subprocess, sys, time

D = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(D, "spread_server")
ENDPOINT = os.path.join(STATE_DIR, "endpoint.json")
SPAWN_STAMP = os.path.join(STATE_DIR, "spawn.stamp")
READ_TIMEOUT = float(os.environ.get("SPREAD_CLIENT_TIMEOUT", "3"))


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
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen([sys.executable, "-s", "-B", os.path.join(D, "spread_server.py")], cwd=D,
                         env=dict(os.environ), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, creationflags=flags, close_fds=True,
                         start_new_session=(os.name == "posix"))
    except Exception:
        pass


def serve(raw, hook="spread_recall"):
    """The hook's output from the warm server, or None (then the caller runs cold)."""
    if os.environ.get("SPREAD_SERVER", "1") == "0" or os.environ.get("SPREAD_SERVER_CHILD"):
        return None
    try:
        with open(ENDPOINT, encoding="utf-8") as f:
            ep = json.load(f)
    except Exception:
        _spawn()
        return None
    try:
        s = socket.create_connection(("127.0.0.1", int(ep["port"])), timeout=0.3)
    except Exception:
        _spawn()
        return None
    try:
        s.settimeout(READ_TIMEOUT)
        env = {k: v for k, v in os.environ.items() if k.startswith(("BRAIN_", "SPREAD_", "MATHBRAIN_", "FOK_"))}
        s.sendall((json.dumps({"token": ep["token"], "hook": hook, "raw": raw, "env": env}) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        resp = json.loads(buf.decode("utf-8"))
    except Exception:
        return None
    finally:
        try:
            s.close()
        except Exception:
            pass
    if resp.get("ok"):
        return resp.get("out", "")
    if str(resp.get("why", "")).startswith(("code changed", "bm25", "rss")):
        _spawn_after_exit()
    return None


def _spawn_after_exit():
    # the old server exits after this decline: start its successor now, so the next prompt is already served. Safe
    # beside the old one: the new server writes its own endpoint, and the old removes the endpoint only if it is its own.
    try:
        os.remove(SPAWN_STAMP)
    except OSError:
        pass
    _spawn()
