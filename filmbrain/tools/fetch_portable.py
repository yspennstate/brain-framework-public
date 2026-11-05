#!/usr/bin/env python
"""
Direct downloads for the tools winget could not actually deliver.

winget reported success for Inkscape and Audacity and neither landed - the
machine-scope MSI wanted an interactive elevation prompt no agent can answer.
So fetch the official portable builds and unpack them under
~\\filmtools, which needs no elevation at all.

Everything here is the project's own official download URL.
"""
import os, subprocess, sys, urllib.request, zipfile, shutil

DEST = os.path.expanduser(r"~/filmtools")
CNW = (0x08000000 if os.name == "nt" else 0)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) filmbrain-setup/1.0"}

# name, url, kind, the executable we expect afterwards (relative, glob ok)
ITEMS = [
    ("inkscape",
     "https://inkscape.org/gallery/item/56343/inkscape-1.4.2_2025-05-13_93dd465-x64.7z",
     "7z", "inkscape*/bin/inkscape.exe"),
    ("audacity",
     "https://github.com/audacity/audacity/releases/download/Audacity-3.7.6/audacity-win-3.7.6-64bit-portable.zip",
     "zip", "**/Audacity.exe"),
]


def get(url, dst):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=600) as r, open(dst, "wb") as f:
        shutil.copyfileobj(r, f)
    return os.path.getsize(dst)


def seven_zip():
    for p in (r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if os.path.exists(p):
            return p
    return None


def main():
    os.makedirs(DEST, exist_ok=True)
    sz = seven_zip()
    for name, url, kind, expect in ITEMS:
        out = os.path.join(DEST, name)
        os.makedirs(out, exist_ok=True)
        arch = os.path.join(DEST, name + ("." + kind))
        print("fetch", name, "...", flush=True)
        try:
            n = get(url, arch)
            print("   {:.1f} MB".format(n / 1e6), flush=True)
        except Exception as e:
            print("   download failed:", str(e)[:140])
            continue
        try:
            if kind == "zip":
                with zipfile.ZipFile(arch) as z:
                    z.extractall(out)
            elif kind == "7z":
                if not sz:
                    print("   no 7-Zip on this machine, cannot unpack")
                    continue
                subprocess.run([sz, "x", "-y", "-o" + out, arch],
                               creationflags=CNW, capture_output=True)
            print("   unpacked ->", out, flush=True)
        except Exception as e:
            print("   unpack failed:", str(e)[:140])
            continue
        finally:
            try:
                os.remove(arch)
            except Exception:
                pass
        import glob
        hits = glob.glob(os.path.join(out, expect), recursive=True)
        print("   executable:", hits[0] if hits else "NOT FOUND after unpack")


if __name__ == "__main__":
    # Discovery is not execution: this script writes, so a help request must exit
    # before any work. The ratchet dreaming/test_help_flag_is_inert.py holds the line.
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print(__doc__ or os.path.basename(__file__))
        raise SystemExit(0)
    main()
