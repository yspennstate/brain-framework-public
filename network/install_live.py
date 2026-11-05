"""Install the network into a LIVE brain and do the first full build.

Copies the network engine into <home>/.claude/dreaming/network/, builds the graph
+ episodes + node content from the deployment's current records, and prints how to
wire dream_grow.py into the nightly dream cycle. Idempotent: re-running rebuilds.

  python install_live.py            # default paths, this machine
  python install_live.py --home <home> --slug <memory-slug>

Runs at BelowNormal priority. The built database is deployment-local state and is
gitignored — it never leaves the machine.
"""
import argparse, os, shutil, sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODULES = ["netstore.py", "build_graph.py", "episodes.py", "fire.py", "grow.py",
           "dream_grow.py", "adapt_live.py", "eval_replay.py", "hook_network_fire.py",
           "firing_audit.py", "surprise_rank.py", "calibrate_caution.py", "ppr_local.py",
           "cascade_rules.py", "hook_cascade_fire.py", "dev_loop_turn.py",
           "test_network.py", "DESIGN.md", "README.md", "INTEGRATION_NOTES.md"]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=os.path.expanduser("~"))
    ap.add_argument("--slug", default=None)
    ap.add_argument("--no-build", action="store_true")
    a = ap.parse_args(argv)

    try:
        import ctypes
        ctypes.windll.kernel32.SetPriorityClass(
            ctypes.windll.kernel32.GetCurrentProcess(), 0x00004000)
    except Exception:
        pass

    dst = os.path.join(a.home, ".claude", "dreaming", "network")
    os.makedirs(dst, exist_ok=True)
    for m in MODULES:
        src = os.path.join(HERE, m)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(dst, m))
    print(f"installed {len(MODULES)} modules -> {dst}")

    if not a.no_build:
        sys.path.insert(0, dst)
        import dream_grow
        args = ["--home", a.home, "--db", os.path.join(dst, "net.sqlite3")]
        if a.slug:
            args += ["--slug", a.slug]
        dream_grow.main(args)

    print("\nto keep it growing, call each dream cycle (BelowNormal, cheap, idempotent):")
    print(f"    python \"{os.path.join(dst, 'dream_grow.py')}\"")
    print("and, optionally, wire hook_network_fire.py as a PostToolUse/UserPromptSubmit")
    print(f"hook with NETWORK_DB={os.path.join(dst, 'net.sqlite3')} (shadow mode until")
    print("eval_replay earns it a LIVE flag beside the db).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
