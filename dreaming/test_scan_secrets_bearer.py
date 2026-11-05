"""test_scan_secrets_bearer.py - the secret scanner catches an opaque bearer token (29 Sep 2026).

    python test_scan_secrets_bearer.py [--module SCAN_SECRETS.py] [--old OLD_SCAN_SECRETS.py]

Every synthetic token is assembled at run time from pieces, so this file never holds a string the scanner flags.
 B1 an Authorization header carrying a 32-character opaque token: exactly one finding, the bearer rule's
 B2 tabs, repeated spaces and other casing: one finding each
 B3 a curl header and a JSON header: one finding each
 B4 a JWT after Bearer: the JWT rule's finding only
 B5 a variable, a template, a placeholder, prose and an unmistakable fake: no finding
With --old, the scanner before this change finds nothing in B1 (the check can fail).
"""
import importlib.machinery
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = sys.argv[1:]
MOD = os.path.abspath(ARGS[ARGS.index("--module") + 1]) if "--module" in ARGS else os.path.join(HERE, "scan_secrets.py")
OLD = os.path.abspath(ARGS[ARGS.index("--old") + 1]) if "--old" in ARGS else None
fails, ran = [], []
TOK = "Qm7" + "x9Lp2Vd8Rk4Tz6Ws1Yh3Nf5Bc0Ja"          # 32 characters, digits and both cases
SCHEME = "Bea" + "rer"
JWT = "ey" + "JhbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiJ4In0" + "." + "c2lnbmF0dXJlLXBhcnQ"


def check(ok, what):
    print(("PASS  " if ok else "FAIL  ") + what)
    ran.append(what)
    if not ok:
        fails.append(what)


def load(path, name):
    loader = importlib.machinery.SourceFileLoader(name, path)
    spec = importlib.util.spec_from_file_location(name, path, loader=loader)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m          # dataclasses look their module up here while the class is built
    loader.exec_module(m)
    return m


def names(m, text):
    return sorted(n for n, _ in m.scan(text))


def main():
    m = load(MOD, "scan_secrets_under_test")
    got = names(m, "Authorization: %s %s" % (SCHEME, TOK))
    check(got == ["bearer token"], "B1 an opaque token in an Authorization header: %s" % got)
    for label, text in (("tab", "authorization:\t%s %s" % (SCHEME.upper(), TOK)),
                        ("spaces", "Authorization:   %s   %s" % (SCHEME.lower(), TOK)),
                        ("title case", "AUTHORIZATION: %s %s" % (SCHEME, TOK))):
        got = names(m, text)
        check(got == ["bearer token"], "B2 %s: %s" % (label, got))
    for label, text in (("curl", 'curl -H "Authorization: %s %s" https://api.example.test/x' % (SCHEME, TOK)),
                        ("json", '{"headers": {"Authorization": "%s %s"}}' % (SCHEME, TOK))):
        got = names(m, text)
        check(got == ["bearer token"], "B3 %s: %s" % (label, got))
    got = names(m, "Authorization: %s %s" % (SCHEME, JWT))
    check(got == ["JWT"], "B4 a JWT after the scheme is the JWT rule's alone: %s" % got)
    for label, text in (("variable", "Authorization: %s $GITHUB_TOKEN_FROM_THE_ENVIRONMENT" % SCHEME),
                        ("template", 'headers = {"Authorization": f"%s {token}"}' % SCHEME),
                        ("placeholder", "Authorization: %s <your-access-token-goes-here>" % SCHEME),
                        ("prose", "%s tokens are sent in the Authorization header by the client" % SCHEME),
                        ("fake", "Authorization: %s your_token_0123456789abcdefghij" % SCHEME)):
        got = names(m, text)
        check(got == [], "B5 %s: %s" % (label, got))
    if OLD:
        o = load(OLD, "scan_secrets_old")
        got = names(o, "Authorization: %s %s" % (SCHEME, TOK))
        check(got == [], "N1 the scanner before this change finds nothing in B1: %s (the check can fail)" % got)
    print("FAIL" if fails else "PASS", "- %d controls, %d failed" % (len(ran), len(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
