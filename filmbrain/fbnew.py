#!/usr/bin/env python
"""
Start a new film. Writes a working project you can render immediately and then
edit down to what you actually want.

  python fbnew.py "the brain explained" --kind lecture --minutes 6
  cd ~/films/the_brain_explained
  python build.py

The point is that the scaffold RENDERS before you have written anything. A
project that only works once it is finished gives you no feedback until the end;
this one gives you a watchable three-beat film in a few minutes and everything
after that is editing.
"""
import argparse, os, re, sys, textwrap

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fbplan import KINDS, classify

FILMS = os.path.expanduser(r"~/films")

BUILD = '''#!/usr/bin/env python
"""
{title}

Built on the one rule that holds: record first, cut the picture to the words.
Every beat is narrated, its REAL duration measured, and its visual rendered to
exactly that length. Nothing is ever told how long it takes.

  python build.py                 # render the whole thing
  python build.py --voice clone   # use a cloned voice instead of kokoro

Edit BEATS below. A changed sentence rebuilds only its own beat.
"""
import argparse, os, subprocess, sys

FB = r"{fb}"
sys.path.insert(0, os.path.join(FB, "pipeline"))
from beatfilm import Film
import visuals as V

NL = os.path.expanduser(r"~//narration_lab")
AB_PY = os.path.expanduser(r"~//audiobook//venv//Scripts//python.exe")
NL_PY = os.path.join(NL, "venv", "Scripts", "python.exe")
HERE = os.path.dirname(os.path.abspath(__file__))
CNW = (0x08000000 if os.name == "nt" else 0)


def kokoro(voice="bm_lewis", speed=0.90):
    """0.90 rather than 0.95: measured, the slower end has better pause
    structure and the words-per-minute depends on the text as much as the dial."""
    eng = os.path.join(NL, "engines", "eng_kokoro_onnx.py")
    def n(text, path):
        tf = path + ".txt"
        open(tf, "w", encoding="utf-8").write(text)
        subprocess.run([AB_PY, eng, "--text", tf, "--out", path, "--voice", voice,
                        "--speed", str(speed), "--threads", "3"],
                       creationflags=CNW, capture_output=True)
        os.path.exists(tf) and os.remove(tf)
    return n


def clone(ref=None, exaggeration=0.45, cfg=0.40):
    """Cloned from a public-domain reader. MIT weights, so the output is yours."""
    eng = os.path.join(NL, "engines", "eng_chatterbox.py")
    ref = ref or os.path.join(NL, "refs", "prompts", "deep_rojyoo.wav")
    def n(text, path):
        tf = path + ".txt"
        open(tf, "w", encoding="utf-8").write(text)
        subprocess.run([NL_PY, eng, "--text", tf, "--out", path, "--ref", ref,
                        "--exaggeration", str(exaggeration), "--cfg", str(cfg),
                        "--per-sentence"],
                       creationflags=CNW, capture_output=True,
                       env={{**os.environ, "CUDA_VISIBLE_DEVICES": "0"}})
        os.path.exists(tf) and os.remove(tf)
    return n


# ---------------------------------------------------------------- the film
# Each beat: the narration, the visual, and the pause after it.
# Write for the EAR: one idea a sentence, subject early, nothing that needs
# reading twice. Read every line aloud before you keep it.
BEATS = [
{beats}
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="kokoro", choices=["kokoro", "clone"])
    ap.add_argument("--ref", default=None)
    a = ap.parse_args()
    nar = clone(a.ref) if a.voice == "clone" else kokoro()
    f = Film(os.path.join(HERE, "out"), width=1920, height=1080, fps=30, narrator=nar)
    for text, visual, pause in BEATS:
        f.beat(text, visual, pause=pause)
    print("narrating and MEASURING ...")
    f.narrate()
    print("cutting the picture to those measured seconds ...")
    f.render_visuals()
    f.assemble(name="{slug}.mp4")


if __name__ == "__main__":
    main()
'''

README = '''# {title}

    python build.py                 render it
    python build.py --voice clone   with the cloned voice instead

The shape this kind of piece usually wants:

{shape}

{note}

Before you call it finished, run the checklist:

    python "{fb}/fbplan.py" "{want}" --minutes {minutes}
    python "{fb}/filmbrain.py" recall "<whatever is bothering you about it>"

and then actually watch it: a contact sheet of every shot, once through at
speed with no notes, and once on a phone.
'''


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:60]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("want")
    ap.add_argument("--kind", default=None)
    ap.add_argument("--minutes", type=float, default=6.0)
    ap.add_argument("--where", default=FILMS)
    a = ap.parse_args()

    kind = a.kind or classify(a.want)[0] or "lecture"
    spec = KINDS.get(kind, KINDS["lecture"])
    slug = slugify(a.want)
    proj = os.path.join(a.where, slug)
    os.makedirs(proj, exist_ok=True)
    os.makedirs(os.path.join(proj, "assets"), exist_ok=True)

    # one starter beat per step of the shape, so it renders immediately
    lines = []
    steps = spec["shape"]
    for i, step in enumerate(steps):
        title = step.split(" - ")[0][:44]
        text = ("Placeholder for: " + step + ". Replace this sentence with what "
                "you actually want to say here, and the picture will follow it.")
        vis = ('V.card("{}", "beat {} of {}")'.format(
            title.replace('"', "'"), i + 1, len(steps)))
        lines.append('    ("{}",\n     {}, 0.8),'.format(text.replace('"', "'"), vis))

    build = BUILD.format(title=a.want, fb=HERE, slug=slug, beats="\n".join(lines))
    open(os.path.join(proj, "build.py"), "w", encoding="utf-8").write(build)

    shape = "\n".join("{}. {}".format(i + 1, s) for i, s in enumerate(steps))
    open(os.path.join(proj, "README.md"), "w", encoding="utf-8").write(
        README.format(title=a.want, shape=shape, note=spec.get("note", ""),
                      fb=HERE.replace("\\", "/"), want=a.want, minutes=int(a.minutes)))

    print("created " + proj)
    print()
    print("  cd " + proj)
    print("  python build.py")
    print()
    print("It renders as it stands - {} placeholder beats - so you get a watchable".format(
        len(steps)))
    print("film before you have written anything, and everything after is editing.")


if __name__ == "__main__":
    main()
