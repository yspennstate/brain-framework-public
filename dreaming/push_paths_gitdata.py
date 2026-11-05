"""Land a scanned commit's paths on origin/main through GitHub's Git Data API, as ONE commit.

Called by push_paths.sh after the git push lost the tip race (main moves about once a minute; a git retry costs a
22 s fetch plus the push hooks on the loaded box, so it loses again). Here a retry is a tree + commit + ref update,
about two seconds. The content is read from the scanned commit C0 (never from disk), so what lands is what the
secret scan saw. Before each try the new tip is compared with the tip the scan was built on: a path a peer changed
in between is refused, as push_paths.sh's own retry refuses it. The ref moves only by fast-forward (force false).

usage: push_paths_gitdata.py SLUG BASE C0 MSG path1 [path2 ...]     token in env PP_TOK
exit 0 landed / 3 refused or failed / 4 the API itself is unusable here (caller may try another route)
"""

import sys
if __name__ == "__main__" and any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    print(__doc__ or "No command-line options.")
    raise SystemExit(0)

import base64, threading, concurrent.futures, json, os, subprocess, sys, time, urllib.error, urllib.request

API = "https://api.github.com/repos/"


# GitHub's secondary limit allows about 80 content-creating requests a minute. On 1 Nov eight parallel blob uploads ran
# near 150 a minute; after ~600 of them every request came back 403 and a 150-path batch gave up. So every POST/PATCH
# waits its turn (POST_GAP apart, about 70 a minute), and a rate-limit 403/429 is waited out, not treated as a refusal.
POST_GAP = float(os.environ.get("PP_POST_GAP", "0.85"))
_pace_lock = threading.Lock()
_last_post = [0.0]


def _pace():
    with _pace_lock:
        wait = _last_post[0] + POST_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_post[0] = time.time()


def _rate_wait(e, txt, n):
    """Seconds to wait when this error is a rate limit, else None."""
    if e.code not in (403, 429):
        return None
    h = e.headers or {}
    ra, rem, reset = h.get("Retry-After"), h.get("X-RateLimit-Remaining"), h.get("X-RateLimit-Reset")
    if not (ra or rem == "0" or "rate limit" in txt.lower()):
        return None
    try:
        s = float(ra) if ra else (float(reset) - time.time() + 2 if rem == "0" and reset else 60.0 * (n + 1))
    except ValueError:
        s = 60.0 * (n + 1)
    return min(max(s, 5.0), 300.0)


def call(slug, method, path, body=None, tries=3, rate_waits=4):
    url = API + slug + path
    data = json.dumps(body).encode() if body is not None else None
    i = n = 0
    while True:
        if method != "GET":
            _pace()
        q = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": "token " + os.environ["PP_TOK"], "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28"})
        try:
            with urllib.request.urlopen(q, timeout=120) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            txt = e.read()[:300].decode("utf-8", "replace")
            s = _rate_wait(e, txt, n)
            if s is not None and n < rate_waits:
                print("gitdata: rate limited (http %s), waiting %d s" % (e.code, s), flush=True)
                time.sleep(s); n += 1; continue
            i += 1
            if e.code >= 500 and i < tries:
                time.sleep(2 + 3 * (i - 1)); continue
            return e.code, {"message": txt}
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            i += 1
            if i < tries:
                time.sleep(2 + 3 * (i - 1)); continue
            return 0, {"message": str(e)}


def _tip_blobs_graphql(slug, tip, paths):
    """{path: blob sha or None} at `tip` in ONE GraphQL request, or None if it failed. GraphQL has its own hourly
    allowance; the REST core one is shared by every agent on the token and was spent on 1 Nov, when 30 contents GETs
    waited out four rate limits and the push gave up."""
    owner, name = slug.split("/", 1)
    parts = ['p%d: object(expression: %s) { oid }' % (i, json.dumps("%s:%s" % (tip, p))) for i, p in enumerate(paths)]
    q = "query { repository(owner: %s, name: %s) { %s } }" % (json.dumps(owner), json.dumps(name), " ".join(parts))
    req = urllib.request.Request("https://api.github.com/graphql", data=json.dumps({"query": q}).encode(),
                                 method="POST", headers={"Authorization": "bearer " + os.environ["PP_TOK"]})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            d = json.load(r)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        print("gitdata: graphql %s" % str(e)[:120]); return None
    repo = (d.get("data") or {}).get("repository")
    if d.get("errors") or not isinstance(repo, dict):
        print("gitdata: graphql errors %s" % str(d.get("errors"))[:160]); return None
    return {p: (repo.get("p%d" % i) or {}).get("oid") for i, p in enumerate(paths)}


def _changed_at_tip(slug, base, tip, paths):
    """Our paths whose blob at `tip` differs from the one at `base`, or None when a lookup failed. The base blobs come
    from the local clone (the scan was built on base); the tip's from one GraphQL request, else one contents GET a path."""
    from urllib.parse import quote
    was_of = {}
    for p in paths:
        r = subprocess.run(["git", "rev-parse", "--verify", "-q", "%s:%s" % (base, p)], capture_output=True)
        was_of[p] = r.stdout.decode().strip() or None
    now_of = _tip_blobs_graphql(slug, tip, paths)
    if now_of is not None:
        return sorted(p for p in paths if now_of[p] != was_of[p])
    hit = []
    for p in paths:
        was = was_of[p]
        st, d = call(slug, "GET", "/contents/%s?ref=%s" % (quote(p), tip))
        if st == 404:
            now = None
        elif st == 200 and isinstance(d, dict) and d.get("sha"):
            now = d["sha"]
        else:
            print("gitdata: contents %s http %s" % (p, st))
            return None
        if now != was:
            hit.append(p)
    return sorted(hit)


def main():
    slug, base, c0, msg = sys.argv[1:5]
    paths = sys.argv[5:]
    out = subprocess.run(["git", "ls-tree", "-z", c0, "--"] + paths, capture_output=True).stdout.decode("utf-8")
    entries = {}
    for rec in filter(None, out.split("\0")):
        meta, p = rec.split("\t", 1)
        mode, typ, sha = meta.split()
        if typ == "blob":
            entries[p] = (mode, sha)
    if set(entries) != set(paths):
        print("gitdata REFUSED: the scanned commit lacks %s" % sorted(set(paths) - set(entries))[:3]); return 3
    # Only paths that differ from the base go up (2 Nov): push_paths.sh hands over every NAMED path, and a memory mirror
    # names hundreds of which a few changed. Each unchanged one cost a blob POST, about 70 a minute, and the account's
    # 5,000-an-hour REST quota drained at 75-160 calls a minute (2 Nov 02:35-02:47). An unchanged path stays as the tip
    # has it; if a peer changed it since, their version stands, which is what a push of no change should do.
    out = subprocess.run(["git", "ls-tree", "-z", base, "--"] + paths, capture_output=True).stdout.decode("utf-8")
    at_base = {}
    for rec in filter(None, out.split("\0")):
        meta, p = rec.split("\t", 1)
        mode, typ, sha = meta.split()
        at_base[p] = (mode, sha)
    same = {p for p in paths if at_base.get(p) == entries[p]}
    if same:
        paths = [p for p in paths if p not in same]
        print("gitdata: %d of %d path(s) already match the base; %d to land" % (len(same), len(same) + len(paths),
                                                                             len(paths)))
    if not paths:
        print("gitdata: nothing to land: every path already matches the base"); return 0
    # The blobs, once: GitHub hashes them the git way, so each returned sha must be the scanned blob id.
    # Eight uploads at once (1 Nov): one at a time took ~3 s each, 7-8 minutes for a 150-path batch.
    def up(p):
        mode, sha = entries[p]
        raw = subprocess.run(["git", "cat-file", "blob", sha], capture_output=True).stdout
        st, d = call(slug, "POST", "/git/blobs", {"content": base64.b64encode(raw).decode(), "encoding": "base64"})
        return p, sha, st, d
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:   # paced by _pace(); 4 keep the gap filled
        results = list(ex.map(up, paths))
    for p, sha, st, d in results:
        if st in (401, 403, 404):
            print("gitdata unusable: blobs http %s %s" % (st, d.get("message", "")[:120])); return 4
    for p, sha, st, d in results:
        if st != 201 or d.get("sha") != sha:
            print("gitdata REFUSED: blob for %s came back http %s sha %s, scanned %s" % (p, st, d.get("sha"), sha)); return 3
    items = [{"path": p, "mode": entries[p][0], "type": "blob", "sha": entries[p][1]} for p in paths]
    # The last tip at which none of our paths differed from base. A retry after a lost ref race only has to check
    # ok_tip...tip, a minute of peer commits, instead of base...tip again (1 Nov: 30 per-path GETs under the rate
    # limit took longer than main stays still, so every retry lost the race).
    ok_tip = base
    for attempt in range(1, 31):
        st, ref = call(slug, "GET", "/git/ref/heads/main")
        if st != 200:
            print("gitdata: ref read http %s" % st); time.sleep(3); continue
        tip = ref["object"]["sha"]
        if tip != ok_tip:
            st, cmp = call(slug, "GET", "/compare/%s...%s" % (ok_tip, tip))
            if st != 200:
                print("gitdata: compare http %s" % st); time.sleep(3); continue
            files = cmp.get("files") or []
            if cmp.get("status") not in ("ahead", "identical"):
                print("gitdata REFUSED: main is %s of the scanned base (rewound?)" % cmp.get("status")); return 3
            if len(files) >= 300:
                # compare lists at most 300 files, so on a busy main it cannot say whether a peer touched ours
                # (1 Nov: every push of a 27-path batch was refused here). Ask the tip for each of our paths instead.
                hit = _changed_at_tip(slug, base, tip, paths)
                if hit is None:
                    print("gitdata REFUSED: %d files changed since the scan and a per-path check failed" % len(files))
                    return 3
                if hit:
                    print("gitdata REFUSED: a peer changed %s since the scan; merge their change first" % hit[:3]); return 3
                files = []
            hit = sorted({f["filename"] for f in files} & set(paths))
            if hit:
                print("gitdata REFUSED: a peer changed %s since the scan; merge their change first" % hit[:3]); return 3
            ok_tip = tip
        st, com = call(slug, "GET", "/git/commits/" + tip)
        if st != 200:
            print("gitdata: commit read http %s" % st); time.sleep(3); continue
        st, tree = call(slug, "POST", "/git/trees", {"base_tree": com["tree"]["sha"], "tree": items})
        if st != 201:
            print("gitdata: tree http %s %s" % (st, tree.get("message", "")[:120])); time.sleep(3); continue
        st, new = call(slug, "POST", "/git/commits", {"message": msg, "tree": tree["sha"], "parents": [tip]})
        if st != 201:
            print("gitdata: commit http %s %s" % (st, new.get("message", "")[:120])); time.sleep(3); continue
        st, upd = call(slug, "PATCH", "/git/refs/heads/main", {"sha": new["sha"], "force": False}, tries=1)
        if st == 200:
            # the landed commit must change exactly the named paths
            st, chk = call(slug, "GET", "/compare/%s...%s" % (tip, new["sha"]))
            got = {f["filename"] for f in (chk.get("files") or [])} if st == 200 else None
            note = "" if got is None or got <= set(paths) else " WARNING: also changed %s" % sorted(got - set(paths))[:3]
            print("pushed %d path(s) by the Git Data API as one commit %s onto %s (try %d)%s"
                  % (len(paths), new["sha"], tip, attempt, note))
            return 0
        print("gitdata: ref update try %d http %s (the tip moved); retrying on the new tip" % (attempt, st))
        time.sleep(1)
    print("gitdata: gave up after 30 ref tries"); return 3


if __name__ == "__main__":
    sys.exit(main())
