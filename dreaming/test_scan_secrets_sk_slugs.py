"""test_scan_secrets_sk_slugs.py - the OpenAI/Anthropic key rule passes lowercase word slugs after "sk-" (two or more
words, letters only) and still flags every key shape (3 Nov 2026; the math-store export was stopped by lemma labels and
slugs for the Sherrington-Kirkpatrick model, "sk-" + two lowercase words).

    python test_scan_secrets_sk_slugs.py [--module SCAN_SECRETS.py] [--old OLD_SCAN_SECRETS.py]

Tokens are assembled at run time so this file never holds a string the scanner flags.
 S1 sk- + two or three lowercase words, in a LaTeX label, a slug and plain text: no finding
 K1 2000 random base62 keys after sk-, sk-ant-api03-, sk-proj-: every one flagged
 K2 one lowercase run of 20+ letters (no hyphen), two words with a digit, two words with a capital, a word run
    followed by an underscore or a digit: still flagged
With --old, the scanner before this change flags S1 (the check can fail).
"""
import importlib.machinery, importlib.util, os, random, string, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = sys.argv[1:]
MOD = os.path.abspath(ARGS[ARGS.index("--module") + 1]) if "--module" in ARGS else os.path.join(HERE, "scan_secrets.py")
OLD = os.path.abspath(ARGS[ARGS.index("--old") + 1]) if "--old" in ARGS else None
RULE = "OpenAI/Anthropic key"
P = "s" + "k-"
fails, ran = [], []


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


def hits(m, text):
    return sum(1 for n, _ in m.scan(text) if n == RULE)


def main():
    m = load(MOD, "scan_secrets_under_test")
    slugs = [P + "concentration" + "-bounds", P + "overlap" + "-distribution", P + "gibbs" + "-measure" + "-sampling"]
    for s in slugs:
        for ctx in ("see (lem:%s) for it", "arxiv-2608-22159-lemma-16-%s\"", "sampling-%s from the paper", " %s "):
            got = hits(m, ctx % s)
            check(got == 0, "S1 %s... in %r: %d findings" % (s[:8], ctx[:12], got))
    rng = random.Random(20261103)
    al = string.ascii_letters + string.digits
    miss = 0
    for i in range(2000):
        pre = (P, P + "ant-api03-", P + "proj-")[i % 3]
        k = pre + "".join(rng.choice(al) for _ in range(rng.randint(24, 64)))
        if not any(c.isdigit() for c in k[3:]):
            k += "7"
        miss += hits(m, " %s " % k) == 0
    check(miss == 0, "K1 random keys: %d of 2000 missed" % miss)
    w = lambda n: "".join(rng.choice(string.ascii_lowercase) for _ in range(n))  # random, not a placeholder run
    w1, w2 = w(11), w(10)
    for label, tok in (("one lowercase run", P + w(23)),
                       ("two words with a digit", P + w1 + "-" + w2[:-1] + "5"),
                       ("two words with a capital", P + w1 + "-" + w2[:5] + "Q" + w2[5:]),
                       ("words then underscore", P + w1 + "-" + w2 + "_x"),
                       ("words then a digit", P + w1 + "-" + w2 + "9")):
        got = hits(m, " %s " % tok)
        check(got == 1, "K2 %s: %d findings" % (label, got))
    if OLD:
        o = load(OLD, "scan_secrets_old")
        got = hits(o, "see (lem:%s) for it" % slugs[0])
        check(got == 1, "N1 the scanner before this change flags S1: %d (the check can fail)" % got)
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
