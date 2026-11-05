"""certify_no_flash.py - run a launch under the window witness and file what the OS reported.

    python certify_no_flash.py <label> <exe> [args...]

The no-flashing-window rule was verified by polling, and the poller could no longer hold its own 50 ms
bar (its last runs: 50.6 / 61.4 / 57.6 ms per sample, each ending "INCONCLUSIVE") while costing enough
desktop to raise ui_freeze. window_witness.py replaces the sampling with notification, so this wrapper
can afford to wrap an individual launch instead of sweeping for eighteen minutes: start the witness,
perform the launch, let it settle, stop, and write the certificate.

Exit code is 0 when no console window was shown and 1 when one was, so this can gate a change.
A certificate records what was actually observed; it does not assert anything about launches it did
not wrap.
"""
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time

if any(a in ("-h", "--help") for a in sys.argv[1:]):
    # Discovery is not execution: this file does its work at module scope, so the
    # guard must come before the first statement that runs.
    print(__doc__ or os.path.basename(__file__))
    raise SystemExit(0)


HERE = os.path.dirname(os.path.abspath(__file__))
EVID = os.path.join(HERE, "state", "no_flash_certificates")


def _witness():
    spec = importlib.util.spec_from_file_location("ww", os.path.join(HERE, "window_witness.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def certify(label, cmd, settle=4.0):
    ww = _witness()
    ww.sightings = []
    result = {}

    def run_watch():
        result["watch"] = ww.watch(settle + 2.0)

    t = threading.Thread(target=run_watch, daemon=True)
    t.start()
    time.sleep(0.6)                      # let the hook arm before the launch
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=settle)
        rc, err = proc.returncode, (proc.stderr or "")[-200:]
    except subprocess.TimeoutExpired:
        rc, err = None, "(still running at the settle deadline; the launch itself was not waited out)"
    except Exception as exc:
        rc, err = None, "launch failed: %s" % exc
    t.join(timeout=settle + 6.0)
    w = result.get("watch") or {"verdict": "UNKNOWN", "sightings": []}
    cert = {"label": label, "command": cmd, "started": started,
            "launch_rc": rc, "launch_stderr_tail": err,
            "verdict": w["verdict"], "sightings": w["sightings"],
            "method": "SetWinEventHook EVENT_OBJECT_SHOW - the OS reports every window shown, so a "
                      "flash of any duration is observed; this is not a sampled poll",
            "scope": "covers only the launch wrapped here, for the seconds wrapped"}
    os.makedirs(EVID, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)[:60]
    path = os.path.join(EVID, "%s_%s.json" % (time.strftime("%Y%m%dT%H%M%S"), safe))
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cert, fh, indent=1)
    cert["certificate_path"] = path
    return cert


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.stderr.write("usage: certify_no_flash.py <label> <exe> [args...]\n")
        raise SystemExit(2)
    c = certify(sys.argv[1], sys.argv[2:])
    print(json.dumps({k: c[k] for k in ("label", "verdict", "launch_rc", "sightings",
                                        "certificate_path")}, indent=1))
    raise SystemExit(0 if c["verdict"] == "NO_CONSOLE_SHOWN" else 1)
