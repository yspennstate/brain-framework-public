"""sweep_credential_urls.py - count every credential-bearing URL in a repository's history, never printing a value
(a read-only audit of what writes through a hosting API may have left in history).

    python sweep_credential_urls.py REPO [REV] [--json OUT]

Reads every blob reachable from REV (default origin/main) once, through git cat-file --batch, and matches URL shapes that
carry a credential: a GitHub raw or object download with a token query, AWS and Google signed URLs, an Azure SAS
signature, any URL with a token/access_token/jwt query of 16+ characters, and credentials written into a URL
(scheme://user:secret@host). The output is counts by class, the paths that carried a hit and whether each is still in
REV's tree - never a matched value, not even a prefix. A task that writes through the GitHub contents API passes no
local push gate, so this is the check that can see what such writes left behind.
"""
import json
import os
import re
import subprocess
import sys

if __name__ == "__main__" and ("-h" in sys.argv[1:] or "--help" in sys.argv[1:]):
    print(__doc__)
    sys.exit(0)

NOWIN = 0x08000000 if os.name == "nt" else 0
U = r"[^\s\"'<>()\]]"
CLASSES = {
    "github_download_token": re.compile(r"(?:raw|objects|codeload|media)\.githubusercontent\.com/" + U + r"*[?&](?:token|jwt)=", re.I),
    "aws_signed_url": re.compile(r"https?://" + U + r"*[?&]X-Amz-(?:Signature|Credential)=", re.I),
    "google_signed_url": re.compile(r"https?://" + U + r"*[?&]X-Goog-(?:Signature|Credential)=", re.I),
    "azure_sas": re.compile(r"https?://" + U + r"*[?&]sv=" + U + r"*[?&]sig=[A-Za-z0-9%/+=]{16,}", re.I),
    "url_token_query": re.compile(r"https?://" + U + r"*[?&](?:token|access_token|jwt)=[A-Za-z0-9._%\-]{16,}", re.I),
    "url_userinfo": re.compile(r"https?://[^\s/:@\"'<>]+:[^\s/@\"'<>]{6,}@[A-Za-z0-9.\-]+", re.I),
}


def main():
    a = [x for x in sys.argv[1:]]
    out = a[a.index("--json") + 1] if "--json" in a else None
    if out:
        i = a.index("--json")
        del a[i:i + 2]
    repo = a[0]
    rev = a[1] if len(a) > 1 else "origin/main"
    objs = subprocess.run(["git", "-C", repo, "rev-list", "--objects", rev], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", creationflags=NOWIN, check=True).stdout.splitlines()
    paths = {}
    for ln in objs:
        parts = ln.split(" ", 1)
        if len(parts) == 2:
            paths.setdefault(parts[0], parts[1])
    tree_now = set(subprocess.run(["git", "-C", repo, "ls-tree", "-r", "--name-only", rev], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", creationflags=NOWIN, check=True).stdout.splitlines())
    p = subprocess.Popen(["git", "-C", repo, "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         creationflags=NOWIN)
    counts = {k: 0 for k in CLASSES}
    hit_paths = {k: set() for k in CLASSES}
    blobs = 0
    for oid in paths:
        p.stdin.write((oid + "\n").encode())
        p.stdin.flush()
        header = p.stdout.readline().decode().split()
        if len(header) < 3:
            continue
        size = int(header[2])
        data = p.stdout.read(size)
        p.stdout.read(1)
        if header[1] != "blob":
            continue
        blobs += 1
        if size > 20_000_000:
            continue
        text = data.decode("utf-8", "replace")
        for k, rx in CLASSES.items():
            n = len(rx.findall(text))
            if n:
                counts[k] += n
                hit_paths[k].add(paths[oid])
    p.stdin.close()
    p.wait()
    res = {"repo": os.path.basename(os.path.abspath(repo)), "rev": rev,
           "rev_oid": subprocess.run(["git", "-C", repo, "rev-parse", rev], capture_output=True, text=True,
                                     creationflags=NOWIN).stdout.strip(),
           "blobs_read": blobs, "matches": counts,
           "paths": {k: [{"path": x, "in_tree_now": x in tree_now} for x in sorted(v)] for k, v in hit_paths.items() if v}}
    print(json.dumps({k: v for k, v in res.items() if k != "paths"}, indent=1))
    for k, v in res["paths"].items():
        print(k, len(v), "paths,", sum(1 for x in v if x["in_tree_now"]), "still in the tree")
        for x in v[:20]:
            print("   ", x["path"], "(in tree)" if x["in_tree_now"] else "(history only)")
    if out:
        json.dump(res, open(out, "w", encoding="utf-8"), indent=1)


if __name__ == "__main__":
    main()
