#!/usr/bin/env python
"""
Install the tools winget could not deliver, per-user, without elevation.

winget's machine-scope MSI wants an interactive UAC prompt that no agent can
answer, and it reports success anyway. An MSI installed with
MSIINSTALLPERUSER=1 ALLUSERS=2 goes under the user profile and needs no
elevation at all.

Downloads come from each project's own release URL, and the check afterwards is
that the executable EXISTS, not that msiexec returned zero - because a return
code has already lied about this once today.
"""
import glob, json, os, shutil, subprocess, sys, urllib.request

DL = os.path.expanduser(r"~/filmtools/_msi")
CNW = (0x08000000 if os.name == "nt" else 0)
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) filmbrain-setup/1.0"}


def latest_github_asset(repo, must, timeout=90):
    url = "https://api.github.com/repos/" + repo + "/releases/latest"
    d = json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers=UA), timeout=timeout).read())
    for a in d.get("assets", []):
        n = a["name"].lower()
        if all(m in n for m in must):
            return a["browser_download_url"], a["name"], a["size"]
    return None, None, 0


def get(url, dst):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=900) as r, \
            open(dst, "wb") as f:
        shutil.copyfileobj(r, f)
    return os.path.getsize(dst)


def install_peruser(msi):
    log = msi + ".log"
    r = subprocess.run(["msiexec", "/i", msi, "/qn", "/norestart",
                        "MSIINSTALLPERUSER=1", "ALLUSERS=2", "/L*v", log],
                       creationflags=CNW, capture_output=True, timeout=1800)
    return r.returncode, log


def find(pats):
    for p in pats:
        hits = glob.glob(p, recursive=True)
        if hits:
            return sorted(hits, reverse=True)[0]
    return None


LOCAL = os.path.expandvars(r"%LOCALAPPDATA%")
PF = r"C:\Program Files"

JOBS = [
    {"name": "Audacity",
     "github": ("audacity/audacity", ["win", "x86_64", ".msi"]),
     "expect": [LOCAL + r"\Programs\**\Audacity.exe",
                LOCAL + r"\**\Audacity.exe",
                PF + r"\Audacity\Audacity.exe"]},
    {"name": "Inkscape",
     "url": "https://inkscape.org/gallery/item/56344/inkscape-1.4.2_2025-05-13_93dd465-x64.msi",
     "expect": [LOCAL + r"\Programs\**\inkscape.exe",
                LOCAL + r"\**\inkscape.exe",
                PF + r"\Inkscape\bin\inkscape.exe"]},
]


def main():
    os.makedirs(DL, exist_ok=True)
    for j in JOBS:
        name = j["name"]
        already = find(j["expect"])
        if already:
            print(name, "already present ->", already)
            continue
        if "github" in j:
            url, fn, size = latest_github_asset(*j["github"])
            if not url:
                print(name, ": no matching asset")
                continue
        else:
            url, fn = j["url"], j["url"].rsplit("/", 1)[-1]
        dst = os.path.join(DL, fn)
        if not os.path.exists(dst) or os.path.getsize(dst) < 1_000_000:
            print("fetch", name, fn, flush=True)
            try:
                n = get(url, dst)
                print("   {:.1f} MB".format(n / 1e6), flush=True)
            except Exception as e:
                print("   download failed:", str(e)[:160])
                continue
        rc, log = install_peruser(dst)
        found = find(j["expect"])
        print("   msiexec rc={}  executable={}".format(rc, found or "NOT FOUND"))
        if not found:
            print("   log:", log)


if __name__ == "__main__":
    # Discovery is not execution: this script writes, so a help request must exit
    # before any work. The ratchet dreaming/test_help_flag_is_inert.py holds the line.
    if "-h" in sys.argv[1:] or "--help" in sys.argv[1:]:
        print(__doc__ or os.path.basename(__file__))
        raise SystemExit(0)
    main()
