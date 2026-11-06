"""Where does a slow served recall spend its time? A sampling thread reads the calling thread's stack every
INTERVAL seconds while a call runs (sys._current_frames: no tracing, the call's code and output are untouched).
After the call, if it took SLOW_MS or more, one row goes to brain_recall_server/slow_samples.jsonl: the samples
taken, the top functions by samples where they were the innermost frame (self) and by samples anywhere on the stack
(inclusive), as file:function. BRS_SAMPLE=0 turns it off; BRS_SAMPLE_SLOW_MS and BRS_SAMPLE_MS tune it.
6 Nov 2026, Mac mini: built to split live after-gap calls with a folder drop (p50 1.3 s) into their parts."""
import collections, json, os, sys, threading, time

INTERVAL = float(os.environ.get("BRS_SAMPLE_MS", "5")) / 1000.0
SLOW_MS = float(os.environ.get("BRS_SAMPLE_SLOW_MS", "500"))
MAX_BYTES = 8 << 20


def _label(code):
    return "%s:%s" % (os.path.basename(code.co_filename), code.co_name)


class Sampler:
    def __init__(self, ident):
        self.ident = ident
        self.self_c = collections.Counter()
        self.inc_c = collections.Counter()
        self.n = 0
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, name="brs-sampler", daemon=True)

    def start(self):
        self._t.start()
        return self

    def _run(self):
        while not self._stop.wait(INTERVAL):
            f = sys._current_frames().get(self.ident)
            if f is None:
                continue
            self.n += 1
            self.self_c[_label(f.f_code)] += 1
            seen = set()
            while f is not None:
                lab = _label(f.f_code)
                if lab not in seen:
                    seen.add(lab)
                    self.inc_c[lab] += 1
                f = f.f_back

    def stop(self):
        self._stop.set()
        self._t.join(1.0)

    def write(self, path, ms, extra):
        if ms < SLOW_MS or not self.n:
            return
        try:
            if os.path.exists(path) and os.path.getsize(path) > MAX_BYTES:
                return
            row = dict(extra, t=round(time.time(), 1), ms=ms, samples=self.n, every_ms=INTERVAL * 1000,
                       self=self.self_c.most_common(25), inclusive=self.inc_c.most_common(40))
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        except Exception:
            pass


def begin():
    if os.environ.get("BRS_SAMPLE", "1") == "0":
        return None
    try:
        return Sampler(threading.get_ident()).start()
    except Exception:
        return None
