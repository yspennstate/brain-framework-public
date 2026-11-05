"""spread_server.py - a resident process that answers the spread_recall prompt hook with its loaded state kept warm.

Why: the hook is the slowest one on every prompt (median ~400 ms of ~870 ms on 4 Nov). Almost all of it is reloading
the same files each prompt: the embedding sidecar and its freshness check, the edge graph, the keyword generation,
salience, habituation, the memory listing and MEMORY.md. Kept in one process those loads are paid once; on 5 Nov
(claude-95df24fa) memoizing them cut the median from 415 ms to 50 ms with the output byte-identical on 8 of 8
prompts (scratch harness memo_spread.py, kept in ~/brain/porting/spread_server/).

The hook itself is unchanged: spread_client.py sends the hook's stdin here and prints the answer; any failure, a
busy server or a decline means the hook runs cold exactly as before.

Correctness rules (same shape as brain_recall_server.py):
- Each memoized loader is keyed on the stat (inode, size, mtime_ns) of the files it reads, and dropped at most
  STATE_TTL seconds after it was filled, so an in-place memory edit is seen within that window at worst.
- Between calls a refresher refills entries before they expire, so state stays warm while prompts are minutes apart.
- The process exits when spread_recall.py or any brain module it imported changed on disk, or past RSS_CAP, or
  after IDLE_EXIT seconds without a call. The next prompt runs cold and starts a fresh server.
- A call whose BRAIN_*/SPREAD_*/MATHBRAIN_* environment differs from the server's is declined.
- One call at a time. Writes only what the hook itself writes (surfaced.tsv, its fire state).
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import io, json, os, secrets, socketserver, sys, threading, time

D = os.path.dirname(os.path.abspath(__file__))
if D not in sys.path:
    sys.path.insert(0, D)
STATE_DIR = os.path.join(D, "spread_server")
ENDPOINT = os.path.join(STATE_DIR, "endpoint.json")
LOG = os.path.join(STATE_DIR, "server.log")
WARM_KEYS = os.path.join(STATE_DIR, "warm_keys.json")   # the last server's loader calls, for a warm start
CALL_LOG = os.path.join(STATE_DIR, "calls.jsonl")   # one line a call: session prefix, served or why not, ms
STATE_TTL = float(os.environ.get("SPREAD_SERVER_TTL", "120"))
IDLE_EXIT = float(os.environ.get("SPREAD_SERVER_IDLE_EXIT", "7200"))
RSS_CAP = int(float(os.environ.get("SPREAD_SERVER_RSS_CAP_GB", "1.5")) * 1024 ** 3)
ENV_PREFIXES = ("BRAIN_", "SPREAD_", "MATHBRAIN_", "FOK_")
# Hooks this process may run. fok_probe, 5 Nov: the first A/B timed the served arm in-process against a cold process
# and read 68 vs ~85 ms; timed both as a hook runs (a fresh process each, ab_fok2.py) it is 86 vs 134 ms, 30 of 30
# byte-identical, so it is hosted. Its warm time is scoring long prompts (14 ms short, ~64 ms median on real ones).
HOSTED = ("spread_recall", "fok_probe")


def log(msg):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("%s pid %d %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid(), msg))
    except Exception:
        pass


def env_subset(env):
    return {k: v for k, v in env.items() if k.startswith(ENV_PREFIXES) and k != "SPREAD_SERVER_CHILD"}


def rss_bytes():
    try:
        import resource
        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return r if sys.platform == "darwin" else r * 1024
    except Exception:
        return 0


def st3(p):
    try:
        s = os.stat(p)
        return (s.st_ino, s.st_size, s.st_mtime_ns)
    except OSError:
        return None


class Warm:
    def __init__(self):
        t = time.perf_counter()
        import spread_recall as sr
        self.sr = sr
        self.memo = {}
        self.code_files = self._code_files()
        self.code_sig = {p: st3(p) for p in self.code_files}
        self.bm25_gen = st3(os.path.join(D, "bm25_index.json.gz"))
        self.calls = 0
        self.misses = []
        self.mods = {}
        self.orig = {}
        self.saved_keys = None
        self._wrap()
        # the exact numpy bm25.score_postings from recall_levers (306/306 real prompts equal, 98 -> 14 ms): fok_probe's
        # BM25 loop was the 129 ms a served fok call still paid on long prompts. SPREAD_NP_BM25=0 off.
        if os.environ.get("SPREAD_NP_BM25", "1") != "0":
            try:
                import numpy as np, bm25, recall_levers
                if not hasattr(bm25.score_postings, "__wrapped__"):
                    recall_levers._install_bm25(bm25, np)
                    log("exact numpy bm25 on")
            except Exception as e:
                log("exact numpy bm25 off: %s: %s" % (type(e).__name__, e))
        log("loaded in %.2f s, %d code files watched" % (time.perf_counter() - t, len(self.code_files)))

    def _code_files(self):
        out = set()
        for m in list(sys.modules.values()):
            f = getattr(m, "__file__", None) or ""
            if f.endswith(".py") and os.path.dirname(os.path.abspath(f)) == D:
                out.add(os.path.abspath(f))
        out.add(os.path.abspath(__file__))
        return sorted(out)

    def _deps(self, name):
        sr = self.sr
        mem, dr = sr.MEM, sr.DR
        j = os.path.join
        edges = [getattr(sr, "EDGES", ""), getattr(sr, "EDGES_FRESH", "")]
        kw = [getattr(sr, "KW", ""), getattr(sr, "KWG", "")]
        try:
            kw += [j(dr, n) for n in os.listdir(dr) if n.startswith("keyword") and not n.endswith(".log")]
        except OSError:
            pass
        table = {
            "_load_superseded": [mem, j(dr, "superseded.tsv")],
            "_load_habituation": [j(dr, "surfaced.tsv"), j(dr, "retrievals.tsv")],
            "_hook_graph": edges + [mem],
            "_load_graph": edges + [mem],
            "_load_embeddings": [sr.EMB, sr.EMB_AUTHORITY, mem],
            "_load_mistake_weights": [sr.MW],
            "_load_keyword_snapshot": kw,
            "_load_keyword_nodes": kw,
            "_load_salience": [sr.SAL],
            "_index_lines": [j(mem, "MEMORY.md")],
            "_mem_md_names": [mem],
        }
        return [p for p in table[name] if p]

    def _wrap(self):
        sr = self.sr
        for name in ("_load_embeddings", "_hook_graph", "_load_graph", "_load_keyword_snapshot", "_load_salience",
                     "_load_habituation", "_mem_md_names", "_index_lines", "_load_superseded",
                     "_load_mistake_weights", "_load_keyword_nodes"):
            f = getattr(sr, name, None)
            if f is None:
                continue
            deps = self._deps(name)

            def w(*a, _f=f, _name=name, _deps=deps, **k):
                key = (_name, a, tuple(sorted(k.items())))
                sig = tuple(st3(p) for p in _deps)
                hit = self.memo.get(key)
                now = time.time()
                if hit is not None and hit[0] == sig and now - hit[1] < STATE_TTL:
                    return hit[2]
                t0 = time.perf_counter()
                val = _f(*a, **k)
                self.memo[key] = (sig, now, val, _f, a, k, _deps)
                why = "new" if hit is None else ("stat" if hit[0] != sig else "ttl")
                self.misses.append("%s:%s:%.0f" % (_name, why, (time.perf_counter() - t0) * 1000))
                return val
            setattr(sr, name, w)
            self.orig[name] = (f, deps)

    # Warm start (5 Nov 2026): a new server's first prompt filled every loader cold (600-640 ms: code edits, idle
    # exits and index rebuilds all restart it). The loader calls of the last server are kept in WARM_KEYS (plain
    # json arguments only) and a new server fills them once before it serves, the same values a call would store.
    def save_keys(self):
        keys = []
        for (name, a, kv) in list(self.memo):
            vals = list(a) + [v for _k, v in kv]
            if all(v is None or isinstance(v, (str, int, float, bool)) for v in vals):
                keys.append([name, list(a), [[k2, v] for k2, v in kv]])
        keys.sort()
        if keys == self.saved_keys:
            return
        tmp = WARM_KEYS + ".%d.tmp" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(keys, f)
        os.replace(tmp, WARM_KEYS)
        self.saved_keys = keys

    def prefill(self):
        try:
            with open(WARM_KEYS, encoding="utf-8") as f:
                keys = json.load(f)
        except (OSError, ValueError):
            return 0
        n = 0
        for name, a, kv in keys:
            got = self.orig.get(name)
            if got is None:
                continue
            f, deps = got
            a, k = tuple(a), dict((k2, v) for k2, v in kv)
            key = (name, a, tuple(sorted(k.items())))
            if key in self.memo:
                continue
            try:
                sig = tuple(st3(p) for p in deps)
                val = f(*a, **k)
            except Exception:
                continue
            self.memo[key] = (sig, time.time(), val, f, a, k, deps)
            n += 1
        self.saved_keys = sorted(keys)
        return n

    def refresh(self):
        """Refill entries near the end of their STATE_TTL window with the original loader, so a prompt minutes
        after the last one still finds warm state (5 Nov: with prompts minutes apart every live call ran cold)."""
        n = 0
        now = time.time()
        for key, ent in list(self.memo.items()):
            sig, t, _val, f, a, k, deps = ent
            # also refill at once when a dependency changed (a memory written anywhere in the fleet moves the memory
            # folder's stat), so that reload is paid here and not by the next prompt
            if now - t < STATE_TTL * 0.6 and tuple(st3(p) for p in deps) == sig:
                continue
            try:
                val = f(*a, **k)
            except Exception:
                self.memo.pop(key, None)
                continue
            self.memo[key] = (tuple(st3(p) for p in deps), time.time(), val, f, a, k, deps)
            n += 1
        return n

    def stale_reason(self):
        for p, s in self.code_sig.items():
            if st3(p) != s:
                return "code changed: " + os.path.basename(p)
        # A BM25 rebuild is no reason to exit: the hook builds BM25() afresh each call, the server holds no postings
        # file open between calls (lsof, 5 Nov), and memory writes rebuild the index every few minutes.
        if rss_bytes() > RSS_CAP:
            return "rss cap"
        return None

    def call(self, raw, hook="spread_recall"):
        if hook == "spread_recall":
            mod = self.sr
        else:
            # other prompt hooks whose module caches are all stat-keyed (checked 5 Nov: fok_probe, supersession, bm25)
            mod = self.mods.get(hook)
            if mod is None:
                import importlib
                mod = self.mods[hook] = importlib.import_module(hook)
        real_in, real_out = sys.stdin, sys.stdout
        out = io.StringIO()
        self.misses = []
        try:
            sys.stdin = io.StringIO(raw)
            sys.stdout = out
            mod.main()
        finally:
            sys.stdin, sys.stdout = real_in, real_out
        self.calls += 1
        # the hook imports bm25, exact_clauses and others lazily inside main(): watch those too from now on
        for p in self._code_files():
            if p not in self.code_sig:
                self.code_sig[p] = st3(p)
        return out.getvalue()


def main():
    os.environ["SPREAD_SERVER_CHILD"] = "1"
    try:
        os.nice(10)
    except OSError:
        pass
    os.makedirs(STATE_DIR, exist_ok=True)
    warm = Warm()
    my_env = env_subset(os.environ)
    token = secrets.token_hex(16)
    last = [time.time()]
    lock = threading.Lock()
    stop = threading.Event()

    class H(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                req = json.loads(self.rfile.readline().decode("utf-8"))
            except Exception:
                return
            if req.get("token") != token:
                return
            resp = {"ok": False}
            hook = req.get("hook") or "spread_recall"
            if hook not in HOSTED:
                resp["why"] = "not hosted: %s" % hook
            elif env_subset(req.get("env") or {}) != my_env:
                resp["why"] = "env differs"
            elif not lock.acquire(timeout=1.5):
                resp["why"] = "busy"
            else:
                try:
                    why = warm.stale_reason()
                    if why:
                        resp["why"] = why
                        log("declined and exiting: " + why)
                        stop.set()
                    else:
                        t = time.perf_counter()
                        resp = {"ok": True, "out": warm.call(req.get("raw") or "{}", hook),
                                "ms": round((time.perf_counter() - t) * 1000, 1)}
                        try:
                            warm.save_keys()
                        except Exception:
                            pass
                        why = warm.stale_reason()
                        if why:
                            log("exit after answer: " + why)
                            stop.set()
                except Exception as e:
                    resp = {"ok": False, "why": "error %r" % (e,)}
                finally:
                    lock.release()
                    last[0] = time.time()
            try:
                self.wfile.write((json.dumps(resp) + "\n").encode("utf-8"))
            except Exception:
                pass
            try:
                sid = str((json.loads(req.get("raw") or "{}") or {}).get("session_id", ""))[:8]
                with open(CALL_LOG, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "sid": sid, "hook": hook,
                                    "ok": resp.get("ok"),
                                        "why": resp.get("why"), "ms": resp.get("ms"),
                                        "miss": list(warm.misses) if resp.get("ok") else None}) + "\n")
            except Exception:
                pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    tmp = ENDPOINT + ".%d.tmp" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"port": port, "token": token, "pid": os.getpid(), "started": time.time()}, f)
    os.replace(tmp, ENDPOINT)
    log("serving on 127.0.0.1:%d" % port)
    th = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True)
    th.start()
    if os.environ.get("SPREAD_WARM_START", "1") != "0":
        def warm_start():
            if lock.acquire(timeout=5):
                try:
                    t = time.perf_counter()
                    n = warm.prefill()
                    log("warm start: %d loader calls filled in %.2f s" % (n, time.perf_counter() - t))
                except Exception as e:
                    log("warm start error %r" % (e,))
                finally:
                    lock.release()
        threading.Thread(target=warm_start, daemon=True).start()
    try:
        tick = 0
        while not stop.wait(5):
            if time.time() - last[0] > IDLE_EXIT:
                log("idle exit")
                break
            tick += 1
            if tick % 2 == 0 and lock.acquire(blocking=False):
                try:
                    why = warm.stale_reason()
                    if why:
                        log("exiting between calls: " + why)
                        break
                    warm.refresh()
                except Exception as e:
                    log("refresh error %r" % (e,))
                finally:
                    lock.release()
    finally:
        srv.shutdown()
        try:
            with open(ENDPOINT, encoding="utf-8") as f:
                if json.load(f).get("pid") == os.getpid():
                    os.remove(ENDPOINT)
        except Exception:
            pass
        log("stopped after %d calls" % warm.calls)


if __name__ == "__main__":
    main()
