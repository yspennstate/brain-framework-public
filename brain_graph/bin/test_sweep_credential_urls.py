"""Controls for sweep_credential_urls.py on a throwaway git repository.

    python test_sweep_credential_urls.py

The fixture repository gets one commit with a file holding one URL of each credential class, a second commit that
deletes it and adds a file with ordinary URLs only. The fake values are assembled at run time, so this file itself
carries no credential-shaped URL. Controls: every class counted once; the deleted file reported as history only; the
clean file never reported; no fake value (nor its first eight characters) appears in the output or the JSON. Nothing
outside a temporary folder is read or written. Needs git on PATH.
"""
if __name__ == "__main__" and __import__("sys").argv[1:2] in (["-h"], ["--help"]):
    print((__doc__ or "").strip() or "run with no arguments")
    raise SystemExit(0)

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
NOWIN = 0x08000000 if os.name == "nt" else 0
FAILS = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (("  " + detail) if detail and not ok else ""), flush=True)
    if not ok:
        FAILS.append(name)


def git(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.invalid", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.invalid", GIT_CONFIG_NOSYSTEM="1", HOME=repo)
    subprocess.run(["git", "-C", repo, "-c", "core.hooksPath=", "-c", "init.templateDir=", "-c", "commit.gpgsign=false"]
                   + list(args), check=True, capture_output=True, env=env, creationflags=NOWIN)


def fakes():
    v = "Q" + "z9" * 12          # 25 characters, nothing a real issuer would mint
    q, a = "?", "&"
    return v, {
        "github_download_token": "https://raw." + "githubusercontent.com/o/r/main/f.txt" + q + "tok" + "en=" + v,
        "aws_signed_url": "https://b.s3.example.com/k" + q + "X-Amz-" + "Signature=" + v,
        "google_signed_url": "https://storage.example.com/b/k" + q + "X-Goog-" + "Signature=" + v,
        "azure_sas": "https://acct.blob.example.net/c/b" + q + "sv=2024-01-01" + a + "si" + "g=" + v,
        "url_userinfo": "https://user:" + v + "@host.example.com/path",
    }


def main():
    tmp = tempfile.mkdtemp(prefix="credsweep_test_")
    try:
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        git(repo, "init", "-q", "-b", "main")
        v, urls = fakes()
        with open(os.path.join(repo, "leaky.md"), "w", encoding="utf-8") as fh:
            for u in urls.values():
                fh.write("see " + u + "\n")
        git(repo, "add", "leaky.md")
        git(repo, "commit", "-q", "-m", "one")
        os.remove(os.path.join(repo, "leaky.md"))
        with open(os.path.join(repo, "clean.md"), "w", encoding="utf-8") as fh:
            fh.write("https://example.com/a?page=2&sort=asc\nhttps://example.com/u@v\nhttp://host:8080/x\n")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "two")
        out_json = os.path.join(tmp, "res.json")
        r = subprocess.run([sys.executable, os.path.join(HERE, "sweep_credential_urls.py"), repo, "main", "--json",
                            out_json], capture_output=True, text=True, creationflags=NOWIN)
        check("1 the sweep runs", r.returncode == 0, r.stderr[-300:])
        if r.returncode:
            return 1
        res = json.load(open(out_json, encoding="utf-8"))
        m = res["matches"]
        want = {k: 1 for k in urls}
        want["url_token_query"] = 1   # the raw download URL is also a 16+ character token query
        check("2 every class counted", all(m.get(k) == n for k, n in want.items()), json.dumps(m))
        paths = {x["path"]: x["in_tree_now"] for v2 in res["paths"].values() for x in v2}
        check("3 the deleted file is reported as history only", paths.get("leaky.md") is False, json.dumps(paths))
        check("4 the clean file is never reported", "clean.md" not in paths, json.dumps(paths))
        blob = r.stdout + r.stderr + open(out_json, encoding="utf-8").read()
        check("5 no value, nor its first eight characters, is printed", v not in blob and v[:8] not in blob)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("%d of 5 controls pass" % (5 - len(FAILS)), flush=True)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
