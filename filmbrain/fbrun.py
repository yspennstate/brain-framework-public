#!/usr/bin/env python
"""
Wrap any render or synthesis command so the film brain records it without
anyone having to remember to.

  fbrun.py --kind render --tool blender --node craft.rendering -- blender -b scene.blend ...
  fbrun.py --kind tts --tool kokoro --params '{"voice":"bm_lewis"}' -- python narrate.py ...

It records: the command, the parameters, wall time, exit status, the artefact
if one appeared, and anything the command printed as a line beginning with
FBMETRIC or FBLEARN. That last part is the trick - a render script can teach
the brain by printing

    FBMETRIC gpu_minutes_per_clip=13.1
    FBLEARN grain at strength 4 stops a 640x352 upscale reading as plastic

and nothing else has to change.
"""
import argparse, json, os, re, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fb_store import Store, slug

METRIC_RE = re.compile(r"^FBMETRIC\s+([A-Za-z0-9_.]+)\s*=\s*(.+)$")
LEARN_RE = re.compile(r"^FBLEARN\s+(.+)$", re.I)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", default="run")
    ap.add_argument("--tool", required=True)
    ap.add_argument("--params", default=None)
    ap.add_argument("--node", default=None)
    ap.add_argument("--artifact", default=None)
    ap.add_argument("--notes", default=None)
    ap.add_argument("--session", default=os.environ.get("FB_SESSION"))
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()

    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd:
        print("nothing to run")
        return 2

    params = json.loads(a.params) if a.params else {}
    params["cmd"] = " ".join(cmd)[:600]

    t0 = time.time()
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    metrics, lessons, tail = {}, [], []
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, errors="replace", bufsize=1)
    for line in p.stdout:
        if not a.quiet:
            sys.stdout.write(line)
        line = line.rstrip("\n")
        tail.append(line)
        if len(tail) > 40:
            tail.pop(0)
        m = METRIC_RE.match(line.strip())
        if m:
            k, v = m.group(1), m.group(2).strip()
            try:
                metrics[k] = float(v)
            except ValueError:
                metrics[k] = v
            continue
        m = LEARN_RE.match(line.strip())
        if m:
            lessons.append(m.group(1).strip())
    p.wait()
    secs = time.time() - t0

    art = a.artifact
    if art and not os.path.exists(art):
        art = None

    s = Store()
    outcome = "ok" if p.returncode == 0 else "fail"
    rid = s.add_run(a.kind, a.tool, params=params, outcome=outcome, metrics=metrics,
                    artifact=art, notes=(a.notes or "") + "\n" + "\n".join(tail[-12:]),
                    seconds=round(secs, 2), started=started, session=a.session)
    node = a.node or ("tool." + slug(a.tool))
    if not s.get(node):
        s.add_node(node, "tool", a.tool, title=a.tool, source="stub", tags=["stub"])
    for k, v in metrics.items():
        if isinstance(v, float):
            s.add_obs(node, k, num=v, run_id=rid)
        else:
            s.add_obs(node, k, text=str(v), run_id=rid)
    s.add_obs(node, "wall_seconds", num=round(secs, 2), run_id=rid)
    s.add_obs(node, "outcome", text=outcome, run_id=rid)
    for text in lessons:
        s.add_lesson(text, node_id=node, evidence="run " + rid, confidence="provisional",
                     session=a.session)

    print("[filmbrain] {} {} {:.1f}s {} metrics={} lessons={}".format(
        rid, outcome, secs, a.tool, len(metrics), len(lessons)), file=sys.stderr)
    return p.returncode


if __name__ == "__main__":
    sys.exit(main())
