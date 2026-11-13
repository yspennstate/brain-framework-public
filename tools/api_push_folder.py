#!/usr/bin/env python3
"""Land a local folder at <dest> on a GitHub repo's main through the git-data API, without a clone.

usage: api_push_folder.py <owner/repo> <local_dir> <dest_path> "<commit message>"

Busy repos move faster than a local push from a shallow clone can win (brain repo, 29 Oct: main moved every ~25 s). So:
blobs for every file under local_dir (each returned sha checked against git's hash of the bytes), a tree for the
folder, then per try: read main's tip, graft the folder at dest into the tip tree, commit with the tip as the only
parent, move main with force=false. GitHub refuses a non-fast-forward, so nobody else's commit can be undone.
Files already at dest that are not in local_dir are replaced along with the folder: use a fresh dest.
The token comes from `gh auth token` and stays in memory.
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import base64, hashlib, json, os, subprocess, sys, time, urllib.error, urllib.request


def token():
    for _ in range(3):
        try:
            return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=60,
                                  stdin=subprocess.DEVNULL).stdout.strip()
        except subprocess.TimeoutExpired:
            time.sleep(10)
    return ""


def make_call(api, tok):
    def call(method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(api + path, data=data, method=method, headers={
            "Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    return r.status, json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as e:
                if e.code in (500, 502, 503, 504) and attempt < 4:
                    time.sleep(3 * (attempt + 1))
                    continue
                return e.code, {"message": e.read()[:300].decode(errors="replace")}
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < 4:
                    time.sleep(3 * (attempt + 1))
                    continue
                return 0, {"message": repr(e)[:300]}
    return call


def git_blob_sha(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def main():
    if len(sys.argv) != 5:
        print(__doc__)
        return 2
    repo, local, dest, msg = sys.argv[1], os.path.abspath(sys.argv[2]), sys.argv[3].strip("/"), sys.argv[4]
    tok = token()
    if not tok:
        print("no gh token; nothing pushed")
        return 1
    call = make_call("https://api.github.com/repos/" + repo, tok)
    entries = []
    for root, _, files in os.walk(local):
        for fn in sorted(files):
            p = os.path.join(root, fn)
            data = open(p, "rb").read()
            st, r = call("POST", "/git/blobs", {"content": base64.b64encode(data).decode(), "encoding": "base64"})
            if st != 201 or r.get("sha") != git_blob_sha(data):
                print("blob failed for %s: %s %s" % (p, st, r.get("message", "")))
                return 1
            mode = "100755" if os.access(p, os.X_OK) else "100644"
            entries.append({"path": dest + "/" + os.path.relpath(p, local), "mode": mode, "type": "blob",
                            "sha": r["sha"]})
    for attempt in range(8):
        st, ref = call("GET", "/git/ref/heads/main")
        if st != 200:
            print("ref read failed:", st, ref.get("message"))
            return 1
        tip = ref["object"]["sha"]
        st, c = call("GET", "/git/commits/" + tip)
        st, t = call("POST", "/git/trees", {"base_tree": c["tree"]["sha"], "tree": entries})
        if st != 201:
            print("tree failed:", st, t.get("message"))
            return 1
        st, nc = call("POST", "/git/commits", {"message": msg, "tree": t["sha"], "parents": [tip]})
        if st != 201:
            print("commit failed:", st, nc.get("message"))
            return 1
        st, r = call("PATCH", "/git/refs/heads/main", {"sha": nc["sha"], "force": False})
        if st == 200:
            print("landed %s: %d files at %s (parent %s)" % (nc["sha"], len(entries), dest, tip[:10]))
            return 0
        print("try %d lost the race (%s); retrying" % (attempt + 1, st))
        time.sleep(2 + attempt)
    print("gave up after 8 tries; nothing landed")
    return 1


if __name__ == "__main__":
    if "-h" in __import__("sys").argv[1:] or "--help" in __import__("sys").argv[1:]:
        print((__doc__ or "").strip() or "usage: python %s" % __import__("os").path.basename(__file__))
        raise SystemExit(0)
    sys.exit(main())
