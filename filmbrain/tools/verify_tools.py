#!/usr/bin/env python
"""
Prove each tool is USABLE, not merely installed.

A folder existing is not evidence. This finds the executable, runs it for a
version string where it has one, and records the result in the film brain as an
observation - so "what have we got" is answerable from the brain rather than
from a memory of an install log.
"""
import glob, json, os, subprocess, sys, time

sys.path.insert(0, os.environ.get("FILMBRAIN_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fb_store import Store

CNW = (0x08000000 if os.name == "nt" else 0)
PF = r"C:\Program Files"
PF86 = r"C:\Program Files (x86)"
LOCAL = os.path.expandvars(r"%LOCALAPPDATA%\Programs")

# node id, display, candidate glob patterns, version args (None = existence only)
TOOLS = [
    ("tool.blender", "Blender", [PF + r"\Blender Foundation\Blender *\blender.exe"], ["--version"]),
    ("tool.ffmpeg", "ffmpeg", ["ffmpeg"], ["-version"]),
    ("tool.kdenlive", "Kdenlive", [LOCAL + r"\Kdenlive\bin\kdenlive.exe",
                                   PF + r"\kdenlive\bin\kdenlive.exe"], None),
    ("tool.shotcut", "Shotcut", [LOCAL + r"\Shotcut\Shotcut.exe",
                                 PF + r"\Shotcut\Shotcut.exe"], None),
    ("tool.krita", "Krita", [PF + r"\Krita (x64)\bin\krita.exe"], None),
    ("tool.inkscape", "Inkscape", [PF + r"\Inkscape\bin\inkscape.exe",
                                   PF + r"\Inkscape\bin\inkscape.com"], ["--version"]),
    ("tool.gimp", "GIMP", [PF + r"\GIMP 3\bin\gimp*.exe",
                           LOCAL + r"\GIMP 3\bin\gimp*.exe"], None),
    ("tool.audacity", "Audacity", [PF + r"\Audacity\Audacity.exe"], None),
    ("tool.obs", "OBS Studio", [PF + r"\obs-studio\bin\64bit\obs64.exe"], None),
    ("tool.handbrake", "HandBrake", [PF + r"\HandBrake\HandBrake.exe",
                                     PF + r"\HandBrake\HandBrakeCLI.exe"], None),
    ("tool.imagemagick", "ImageMagick", [PF + r"\ImageMagick*\magick.exe", "magick"], ["-version"]),
    ("tool.pencil2d", "Pencil2D", [
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\**\pencil2d.exe"),
        LOCAL + r"\Pencil2D\pencil2d.exe",
        PF + r"\Pencil2D\pencil2d.exe"], None),
    ("tool.opentoonz", "OpenToonz", [PF + r"\OpenToonz*\OpenToonz.exe",
                                     PF + r"\OpenToonz*\opentoonz.exe"], None),
    ("tool.synfig", "Synfig Studio", [PF + r"\Synfig*\bin\synfigstudio.exe",
                                      PF + r"\Synfig*\bin\synfig.exe"], None),
    ("tool.losslesscut", "LosslessCut", [LOCAL + r"\Programs\losslesscut\LosslessCut.exe",
                                         LOCAL + r"\losslesscut\LosslessCut.exe",
                                         os.path.expandvars(r"%LOCALAPPDATA%\**\LosslessCut.exe")], None),
    ("tool.lmms", "LMMS", [PF + r"\LMMS\lmms.exe"], None),
    ("tool.comfyui", "ComfyUI", [os.path.expanduser(r"~/ComfyUI_portable/ComfyUI_windows_portable/python_embeded/python.exe")], None),
    ("tool.manim", "Manim", [os.path.expanduser(r"~/lecture/venv/Scripts/python.exe")], None),
    ("tool.davinci_resolve", "DaVinci Resolve", [PF + r"\Blackmagic Design\DaVinci Resolve\Resolve.exe"], None),
    ("tool.natron", "Natron", [PF + r"\INRIA\Natron*\bin\Natron.exe"], None),
]


def find(pats):
    for p in pats:
        if os.sep not in p and "/" not in p:
            w = subprocess.run(["where", p], capture_output=True, text=True,
                               creationflags=CNW, shell=True)
            if w.returncode == 0 and w.stdout.strip():
                return w.stdout.strip().splitlines()[0]
            continue
        hits = glob.glob(p, recursive=True)
        if hits:
            hits.sort(reverse=True)
            return hits[0]
    return None


def main():
    s = Store()
    found, missing = [], []
    for nid, name, pats, vargs in TOOLS:
        path = find(pats)
        ver = None
        if path and vargs:
            try:
                r = subprocess.run([path] + vargs, capture_output=True, text=True,
                                   timeout=60, creationflags=CNW, errors="replace")
                out = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
                ver = out[0][:110] if out else None
            except Exception:
                pass
        if path:
            found.append((name, path, ver))
            if not s.get(nid):
                s.add_node(nid, "tool", name, title=name, source="verified",
                           tags=["tool", "installed"])
            s.add_obs(nid, "installed_path", text=path, source="verified", commit=False)
            if ver:
                s.add_obs(nid, "version", text=ver, source="verified", commit=False)
            s.add_obs(nid, "available", num=1.0, source="verified", commit=False)
        else:
            missing.append(name)
            if s.get(nid):
                s.add_obs(nid, "available", num=0.0, source="verified", commit=False)
    s.commit()

    print("USABLE ({}):".format(len(found)))
    for name, path, ver in found:
        print("  {:<18} {}".format(name, (ver or os.path.basename(path))[:88]))
    if missing:
        print()
        print("NOT FOUND ({}): {}".format(len(missing), ", ".join(missing)))
    json.dump({"found": [{"name": n, "path": p, "version": v} for n, p, v in found],
               "missing": missing, "checked": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())},
              open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "INVENTORY.json"), "w", encoding="utf-8"), indent=2)


if __name__ == "__main__":
    # Discovery is not execution: this script writes, so a help request must exit
    # before any work. The ratchet dreaming/test_help_flag_is_inert.py holds the line.
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print(__doc__ or os.path.basename(__file__))
        raise SystemExit(0)
    main()
