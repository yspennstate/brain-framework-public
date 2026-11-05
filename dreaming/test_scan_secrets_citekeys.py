"""test_scan_secrets_citekeys.py - the long-key rule passes LaTeX citation keys and SHA checksums, and nothing else
(3 Nov 2026, claude-76886ed8; the hourly math-store export push was stopped by 235 such hits in public paper text).

    python test_scan_secrets_citekeys.py [--module SCAN_SECRETS.py] [--old OLD_SCAN_SECRETS.py]

Every token is assembled at run time from pieces, so this file never holds a string the scanner flags.
 C1 citation keys and equation labels after "{", "[", "\\cite[...]{", "eq:" and an escaped "\\n": no long-key finding
 C2 "SHA" + 64 lowercase hex and "SHA256" + 64 lowercase hex: no finding
 P1 5000 random base62 keys of 40-64 characters, in each of those contexts and in plain ones: at most 2 missed each
 P2 a citation-key-shaped token outside those contexts (quoted, after "=", after a space or ":"): still a finding
 P3 the checksum exclusion is exact: a 65th character, an uppercase hex digit, "SHA1" or 65 hex: still a finding
 T1 adversarial text: each search under one second
With --old, the scanner before this change flags the C1 keys (the check can fail).
"""
import importlib.machinery
import importlib.util
import os
import random
import string
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = sys.argv[1:]
MOD = os.path.abspath(ARGS[ARGS.index("--module") + 1]) if "--module" in ARGS else os.path.join(HERE, "scan_secrets.py")
OLD = os.path.abspath(ARGS[ARGS.index("--old") + 1]) if "--old" in ARGS else None
fails, ran = [], []
RULE = "long API key"
BS = chr(92)
KEYS = ["Bahouri" + "Chemin" + "Danchin" + "2011" + "Fourier" + "Analysis" + "Nonlinear",
        "Duminil" + "Lectures" + "On" + "Ising" + "and" + "Potts" + "Models" + "2019",
        "Kato" + "Perturbation" + "Theory" + "Linear" + "Operators" + "1995"]
HEX = "9c03" + "3a1c0b37d03bc3a755dcfc4e37d7" + "1825292bf47b5e67313dc67f1e3e036a"


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    loader.exec_module(m)
    return m


def longkey(m, text):
    return sum(1 for n, _ in m.scan(text) if n == RULE)


def main():
    m = load(MOD, "scan_secrets_under_test")
    cite_ctx = ["{%s}", "[%s]", BS + "cite{%s}", BS + "cite[(1.7)]{%s}", "(eq:%s)", "Eq:%s", BS + "n%s"]
    for k in KEYS:
        check(len(k) >= 40, "C0 %s... is long enough for the rule: %d characters" % (k[:12], len(k)))
        got = [c for c in cite_ctx if longkey(m, "see " + c % k + " for the proof")]
        check(got == [], "C1 %s... in every citation context: flagged in %s" % (k[:12], got))
    for pre in ("SHA", "SHA256"):
        got = longkey(m, "a vector of %s%s satisfies it" % (pre, HEX))
        check(got == 0, "C2 %s + 64 lowercase hex: %d findings" % (pre, got))

    rng = random.Random(20261103)
    alpha = string.ascii_letters + string.digits
    keys = []
    while len(keys) < 5000:
        k = "".join(rng.choice(alpha) for _ in range(rng.randint(40, 64)))
        if any(c.islower() for c in k) and any(c.isupper() for c in k) and any(c.isdigit() for c in k):
            keys.append(k)
    for c in cite_ctx + ['"%s"', " %s ", "=%s"]:
        miss = sum(1 for k in keys if not longkey(m, c % k))
        check(miss <= 2, "P1 random keys in %r: %d of 5000 missed" % (c, miss))
    for c in ['"%s"', " %s", "=%s", ":%s", "'%s'"]:
        got = longkey(m, c % KEYS[0])
        check(got == 1, "P2 a key-shaped word chain in %r: %d findings" % (c, got))
    for label, tok in (("65th character", "SHA" + HEX + "x"), ("uppercase hex digit", "SHA" + HEX[:-1] + "A"),
                       ("SHA1 prefix", "SHA1" + HEX), ("65 hex", "SHA" + HEX + "0")):
        got = longkey(m, " %s " % tok)
        check(got == 1, "P3 %s: %d findings" % (label, got))

    worst = 0.0
    for s in ["{" + "a" * 200000 + "1A!", "{" + "Ab" * 100000 + "1", "{" + "ABCDE" * 40000 + "a1",
              "[" + "1234" * 50000 + "aB", "{" + "On" * 100000 + "a1"]:
        t0 = time.time()
        m.scan(s)
        worst = max(worst, time.time() - t0)
    check(worst < 1.0, "T1 adversarial text: slowest search %.3f s" % worst)

    if OLD:
        o = load(OLD, "scan_secrets_old")
        got = longkey(o, "see {%s} for the proof" % KEYS[0])
        check(got == 1, "N1 the scanner before this change flags C1: %d (the check can fail)" % got)
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
