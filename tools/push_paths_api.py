#!/usr/bin/env python3
"""Put a set of files on a busy repo's main branch in one commit through GitHub's git-data API, with no local clone push.

A busy repo's main can move every few seconds, and a shallow or far-behind clone then cannot win a local push.
Here:
  1. each file goes up as a blob; GitHub hashes like git, so the returned sha must equal the local blob sha;
  2. per try: read main's tip, make a tree from the tip's tree with only these paths set, a commit with the tip as
     its one parent, then move main with force=false (GitHub refuses a non-fast-forward, so nobody's commit is undone);
  3. read the new commit's tree back and check every path carries the uploaded sha.
Files already identical on main are skipped; nothing to change means no commit. Secret-scan the files before calling.
The token comes from `gh auth token`, stays in memory, and is never printed.

    python3 push_paths_api.py --repo OWNER/NAME --message MSG --root DIR PATH [PATH ...]
PATH is relative to DIR and is also the path in the repo. Exit 0 landed (or nothing to do), 1 failed.
A file past ~30 MB: git hash-object -w it in a clone, mktree a one-entry tree, commit-tree, push that commit to a
throwaway branch, run this (it finds the blob), then delete the branch.
"""
import argparse, base64, hashlib, http.client, json, os, subprocess, sys, time, urllib.error, urllib.request


def token():
    for _ in range(3):
        try:
            return subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=60,
                                  stdin=subprocess.DEVNULL).stdout.strip()
        except subprocess.TimeoutExpired:
            time.sleep(10)
    return ""


def make_call(api, tok):
    def call(method, path, body=None, ok=(200, 201)):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(api + path, data=data, method=method, headers={
            "Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    return r.status, json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as e:
                if e.code in ok:
                    return e.code, json.loads(e.read() or b"{}")
                if e.code in (500, 502, 503, 504) and attempt < 4:
                    time.sleep(3 * (attempt + 1))
                    continue
                return e.code, {"message": e.read()[:300].decode(errors="replace")}
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as e:
                if attempt < 4:
                    time.sleep(3 * (attempt + 1))
                    continue
                return 0, {"message": repr(e)[:300]}
    return call


def blob_sha(b):
    return hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()


def listed(call, commit, paths):
    """{path: blob sha on commit} for the paths, by listing each parent folder's tree once (a call per folder level,
    not one per file: 150 files were 300 /contents calls of a shared quota). None if a listing came back truncated."""
    st, c = call("GET", f"/git/commits/{commit}")
    trees = {"": c["tree"]["sha"]}

    def tree_of(d):
        if d in trees:
            return trees[d]
        parent, _, name = d.rpartition("/")
        ps = tree_of(parent)
        sha = None
        if ps:
            sha = entries(ps).get(name, (None, None))[1]
        trees[d] = sha
        return sha

    cache = {}

    def entries(sha):
        if sha not in cache:
            st, t = call("GET", f"/git/trees/{sha}")
            if t.get("truncated"):
                raise RuntimeError(f"tree {sha} listing truncated")
            cache[sha] = {e["path"]: (e["type"], e["sha"]) for e in t.get("tree", [])}
        return cache[sha]

    out = {}
    try:
        for p in paths:
            d, _, name = p.rpartition("/")
            ts = tree_of(d)
            if ts:
                typ, sha = entries(ts).get(name, (None, None))
                if typ == "blob":
                    out[p] = sha
    except RuntimeError as e:
        print(e); return None
    return out


def gql_push(repo, tok, branch, root, paths, local, message):
    """The REST quota is shared by every machine on the account and ran dry mid-push (147 of 150 blobs
    up); GraphQL has its own budget. createCommitOnBranch per chunk of about 3 MB of base64, guarded by expectedHeadOid,
    then one aliased query per chunk reads every blob oid back. Paths main already holds are skipped the same way."""
    owner, name = repo.split("/")

    def gql(q, v=None):
        req = urllib.request.Request("https://api.github.com/graphql", data=json.dumps({"query": q, "variables": v or {}})
                                     .encode(), method="POST", headers={"Authorization": "Bearer " + tok,
                                                                        "Content-Type": "application/json"})
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    return json.loads(r.read())
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException) as e:
                if attempt == 4:
                    return {"errors": [{"message": repr(e)[:300]}]}
                time.sleep(3 * (attempt + 1))

    def oids(ps):
        out, head = {}, None
        for i in range(0, len(ps), 100):
            part = ps[i:i + 100]
            fields = " ".join(f'f{j}: object(expression: {json.dumps(branch + ":" + p)}) {{ ... on Blob {{ oid }} }}'
                              for j, p in enumerate(part))
            d = gql(f'query {{ repository(owner: "{owner}", name: "{name}") {{ ref(qualifiedName: "refs/heads/{branch}")'
                    f' {{ target {{ oid }} }} {fields} }} }}')
            r = (d.get("data") or {}).get("repository")
            if not r:
                print("graphql read:", d.get("errors")); return None, None
            head = head or r["ref"]["target"]["oid"]
            for j, p in enumerate(part):
                if r.get(f"f{j}"):
                    out[p] = r[f"f{j}"]["oid"]
        return out, head

    have, _ = oids(paths)
    if have is None:
        return 1
    todo = [p for p in paths if have.get(p) != local[p]]
    if not todo:
        print("nothing to do: main already has every file"); return 0
    chunks, cur, size = [], [], 0
    for p in todo:
        n = os.path.getsize(os.path.join(root, p)) * 4 // 3
        if cur and size + n > 3_000_000:
            chunks.append(cur); cur, size = [], 0
        cur.append(p); size += n
    chunks.append(cur)
    for k, part in enumerate(chunks):
        adds = [{"path": p, "contents": base64.b64encode(open(os.path.join(root, p), "rb").read()).decode()}
                for p in part]
        for attempt in range(8):
            _, head = oids(part[:1])
            d = gql("mutation($i:CreateCommitOnBranchInput!){createCommitOnBranch(input:$i){commit{oid}}}",
                    {"i": {"branch": {"repositoryNameWithOwner": repo, "branchName": branch},
                           "message": {"headline": message + (f" ({k + 1}/{len(chunks)})" if len(chunks) > 1 else "")},
                           "expectedHeadOid": head, "fileChanges": {"additions": adds}}})
            c = ((d.get("data") or {}).get("createCommitOnBranch") or {}).get("commit")
            if c:
                break
            print(f"chunk {k + 1}: try {attempt + 1} refused: {str(d.get('errors'))[:200]}", flush=True)
            time.sleep(2)
        else:
            print(f"chunk {k + 1} could not land"); return 1
        got, _ = oids(part)
        bad = [p for p in part if (got or {}).get(p) != local[p]]
        if bad:
            print("readback differs:", bad[:5]); return 1
        print(f"landed {c['oid'][:10]}: chunk {k + 1}/{len(chunks)}, {len(part)} files read back", flush=True)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--message", required=True)
    ap.add_argument("--root", required=True)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--graphql", action="store_true", help="commit through GraphQL (own quota); files under ~2 MB")
    ap.add_argument("paths", nargs="+")
    a = ap.parse_args()
    tok = token()
    if not tok:
        print("no token from gh auth token"); return 1
    call = make_call("https://api.github.com/repos/" + a.repo, tok)
    local = {}
    for p in a.paths:
        b = open(os.path.join(a.root, p), "rb").read()
        if len(b) > 95_000_000:
            print(f"{p}: {len(b)} bytes, over GitHub's 100 MB file limit"); return 1
        local[p] = blob_sha(b)
    if a.graphql:
        return gql_push(a.repo, tok, a.branch, a.root, a.paths, local, a.message)
    # skip what main already has
    st, ref = call("GET", f"/git/ref/heads/{a.branch}")
    if st != 200:
        print("read ref:", st, ref.get("message")); return 1
    tip = ref["object"]["sha"]
    have = listed(call, tip, a.paths)
    if have is None:
        return 1
    todo = [p for p in a.paths if have.get(p) != local[p]]
    if not todo:
        print("nothing to do: main already has every file"); return 0
    for p in todo:
        b = open(os.path.join(a.root, p), "rb").read()
        # the blob API refuses a ~47 MB file (422); such a file goes up first by a git push of a one-file
        # commit to a throwaway branch, and then only needs to be found here
        if len(b) > 30_000_000:
            st, r = call("GET", f"/git/blobs/{local[p]}", ok=(200, 404))
            if st == 200:
                print(f"blob {p} already on GitHub", flush=True); continue
            print(f"{p}: {len(b)} bytes is past the blob API; push its blob by git to a temp branch first"); return 1
        st, r = call("POST", "/git/blobs", {"content": base64.b64encode(b).decode(), "encoding": "base64"})
        if st != 201 or r.get("sha") != local[p]:
            print(f"blob {p}: {st} {r.get('sha')} != {local[p]}"); return 1
        print(f"blob {p} {len(b)} bytes ok", flush=True)
    for attempt in range(8):
        st, ref = call("GET", f"/git/ref/heads/{a.branch}")
        tip = ref["object"]["sha"]
        st, c = call("GET", f"/git/commits/{tip}")
        st, t = call("POST", "/git/trees", {"base_tree": c["tree"]["sha"], "tree": [
            {"path": p, "mode": "100644", "type": "blob", "sha": local[p]} for p in todo]})
        if st != 201:
            print("tree:", st, t.get("message")); return 1
        st, nc = call("POST", "/git/commits", {"message": a.message, "tree": t["sha"], "parents": [tip]})
        if st != 201:
            print("commit:", st, nc.get("message")); return 1
        st, r = call("PATCH", f"/git/refs/heads/{a.branch}", {"sha": nc["sha"], "force": False})
        if st == 200:
            break
        print(f"main moved (try {attempt + 1}: {st}), again", flush=True)
        time.sleep(2)
    else:
        print("could not land after 8 tries"); return 1
    got = listed(call, nc["sha"], todo) or {}
    bad = [p for p in todo if got.get(p) != local[p]]
    if bad:
        print("readback differs:", bad); return 1
    print(f"landed {nc['sha'][:10]} on {a.branch}: {len(todo)} files, every blob read back")
    return 0


if __name__ == "__main__":
    sys.exit(main())
