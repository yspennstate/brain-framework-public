#!/usr/bin/env python3
"""Install, inspect, operate and verify a source-pinned Brain deployment."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import zipfile

sys.dont_write_bytecode = True
import connector as c


def emit(value):
    print(json.dumps(value, indent=2, ensure_ascii=False))


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", help="Codex profile; defaults to CODEX_HOME or ~/.codex")
    p.add_argument("--brain", default="main", help="Explicit brain id; main is mandatory, project ids require a binding")
    p.add_argument("--workspace", help="Actual workspace; required for project access and activation admission")
    p.add_argument("--session", dest="operation_session", help="Actual session id for admitted memory mutations")
    p.add_argument("--maintenance", choices=["setup", "repair", "qualification", "skill-maintenance"],
                   help="Explicit bounded maintenance lane; never authorizes ordinary task work")
    sub = p.add_subparsers(dest="command", required=True)
    main_binding = sub.add_parser("bind-main", help="Record an owner-designated main deployment; never create a corpus")
    main_binding.add_argument("--authorization", required=True, help="Evidence pointer to the owner's designation")
    main_binding.add_argument("--apply", action="store_true")
    main_binding.add_argument("--target-profile", help="Existing main profile when this runtime profile shares another profile's main")
    project_binding = sub.add_parser("bind-project", help="Map an explicitly authorized separate project deployment")
    project_binding.add_argument("--id", required=True)
    project_binding.add_argument("--target-profile", required=True)
    project_binding.add_argument("--root", action="append", required=True)
    project_binding.add_argument("--authorization", required=True)
    project_binding.add_argument("--apply", action="store_true")
    sub.add_parser("resolve", help="Validate owner-designated main/project identity without writing memory")
    admission = sub.add_parser("admit", help="Recompute complete setup, health, wake and receiver evidence; fail closed")
    admission.add_argument("--session", required=True)
    install = sub.add_parser("install", help="Plan a fresh isolated deployment; --apply performs it")
    install.add_argument("--apply", action="store_true")
    install.add_argument("--source", help="Original checkout matching the locked revision")
    install.add_argument("--network", action="store_true", help="Fetch the pinned GitHub archive instead of the offline bundle")
    install.add_argument("--semantic", action="store_true", help="Install and verify production BGE semantic recall")
    install.add_argument("--runtime", choices=["codex", "claude"], default="codex")
    install.add_argument("--partner", default="the user")
    install.add_argument("--timezone", default="UTC")
    install.add_argument("--python")
    install.add_argument("--without-dependency-install", action="store_true", help="For offline core rehearsal only; no full readiness claim")
    sub.add_parser("status", help="Inspect installation and the last observation without asserting current readiness")
    sub.add_parser("fingerprints", help="Inspect explicitly typed deployment/admission fingerprints and raw/parsed config digests; read-only")
    checklist = sub.add_parser("setup-checklist", help="Inspect every source-backed setup obligation and warn about unchecked items")
    checklist.add_argument("--refresh", action="store_true", help="Recompute local source/runtime/config/recall/semantic checks")
    sub.add_parser("maintain-recall", help="Run original incremental lexical/vector builders and verify publication/coverage")
    repair = sub.add_parser("repair-wiring", help="Plan or restore missing owned hook groups and standing activation")
    repair.add_argument("--apply", action="store_true")
    guardian = sub.add_parser("guardian", help="Plan or transactionally install the independent setup observer")
    guardian.add_argument("--apply", action="store_true")
    verify = sub.add_parser("verify", help="Recompute current readiness through original entry points")
    verify.add_argument("--skip-hook-probes", action="store_true")
    verify.add_argument("--skip-smoke", action="store_true")
    sub.add_parser("disable", help="Disconnect loaded bridge hooks immediately and remove standing activation")
    sub.add_parser("enable", help="Reconnect this installed deployment")
    sub.add_parser("rollback", help="Restore the previous configuration; preserve all corpus/state files")
    bundle = sub.add_parser("bundle", help="Create a transferable skill package containing public source and no lived state")
    bundle.add_argument("--output", required=True)
    source = sub.add_parser("source", help="Acquire and verify the locked original source")
    source.add_argument("--network", action="store_true")
    source.add_argument("--source")
    review = sub.add_parser("source-review", help="Compare GitHub HEAD or a commit as an inactive candidate; never automatically repin")
    review.add_argument("--revision")
    native = sub.add_parser("native", help="Run an upstream entry point in its bound private deployment")
    native.add_argument("tool", choices=["brain", "checklist", "graph-capture", "mesh", "workspace", "checkpoint", "switch", "dream", "status", "script"])
    native.add_argument("arguments", nargs=argparse.REMAINDER)
    recall = sub.add_parser("recall")
    recall.add_argument("arguments", nargs=argparse.REMAINDER)
    faculty = sub.add_parser("faculty", help="Inspect source-backed scope, setup and admission evidence for every mechanism")
    faculty.add_argument("id", nargs="?")
    probe = sub.add_parser("probe-root", help="Run a bounded actual fresh-root test (uses model access)")
    probe.add_argument("--apply", action="store_true")
    probe.add_argument("--reviewed-automation", action="store_true", help="Test source-vetted hooks for this invocation; does not persist runtime trust")
    probe.add_argument("--cli")
    probe.add_argument("--with-worker", action="store_true", help="Require one actual native read-only worker and parent verification")
    probe.add_argument("--timeout", type=int, default=900, help="Bounded receiver deadline in seconds; preserve the recorded session on interruption")
    resume = sub.add_parser("resume-root", help="Resume the exact interrupted receiver session and its existing fixture")
    resume.add_argument("--folder", required=True)
    resume.add_argument("--apply", action="store_true")
    resume.add_argument("--with-worker", action="store_true")
    resume.add_argument("--cli")
    resume.add_argument("--timeout", type=int, default=900)
    return p


def bundle(output):
    output = c.absolute(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    permitted = []
    for path in c.SKILL.rglob("*"):
        relative = path.relative_to(c.SKILL)
        if path.is_symlink():
            raise c.ConnectorError("Skill package contains a symlink: " + str(relative))
        if not path.is_file() or any(x in {"__pycache__", ".DS_Store", ".git"} for x in relative.parts) or path.suffix == ".pyc":
            continue
        if relative.parts[0] not in {"SKILL.md", "scripts", "references", "assets", "agents"}:
            raise c.ConnectorError("Unclassified skill package file: " + str(relative))
        permitted.append(path)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(permitted):
            archive.write(path, "brain-framework/" + path.relative_to(c.SKILL).as_posix())
    return {"package": str(output), "sha256": c.sha(output), "files": len(permitted), "revision": c.source_lock()["revision"], "contains_lived_state": False}


def main():
    args = parser().parse_args()
    profile = c.profile_path(args.profile)
    if args.command in {"bind-main", "bind-project", "resolve", "admit"}:
        import brain_binding as b
        if args.command == "bind-main":
            if args.apply:
                with c.lock(profile):
                    emit(b.bind_main(profile, args.authorization, True, args.target_profile))
            else:
                emit(b.bind_main(profile, args.authorization, False, args.target_profile))
        elif args.command == "bind-project":
            if args.apply:
                with c.lock(profile):
                    emit(b.bind_project(profile, args.id, args.target_profile, args.root, args.authorization, True))
            else:
                emit(b.bind_project(profile, args.id, args.target_profile, args.root, args.authorization, False))
        elif args.command == "resolve":
            emit(b.selection(profile, args.brain, args.workspace))
        else:
            if not args.workspace:
                raise c.ConnectorError("admit requires --workspace before the subcommand")
            report = b.admit(profile, args.session, args.workspace, args.brain)
            emit(report)
            return 0 if report["passed"] else 2
        return 0
    if args.command == "install":
        plan = {"profile": str(profile), "runtime": args.runtime, "source_revision": c.source_lock()["revision"],
                "source_mode": "supplied checkout" if args.source else ("GitHub archive" if args.network else "verified offline bundle"),
                "writes": [str(profile / "skills/brain-framework"), str(c.state_dir(profile)), str(profile / "AGENTS.md"),
                           str(profile / "hooks.json") if args.runtime == "codex" else str(profile.parent / ".claude/settings.json")],
                "semantic": args.semantic, "services_started": [], "external_publication": False,
                "note": "Runtime hook trust and fresh root/worker behavior must be verified before embedded readiness."}
        if not args.apply:
            emit(plan)
            return 0
        with c.lock(profile):
            deployment = c.create_deployment(profile, supplied=args.source, network=args.network, semantic=args.semantic,
                                             runtime=args.runtime, partner=args.partner, timezone=args.timezone, python=args.python,
                                             dependencies=not args.without_dependency_install)
            if args.semantic:
                c.build_semantic(deployment)
            preliminary = c.verify(deployment, integrated=False, hooks=True, smoke=True)
            c.atomic(Path(deployment["home"]).parent / "preactivation-verification.json", preliminary)
            if not preliminary["passed"]:
                emit(preliminary)
                return 1
            deployment = c.activate(profile, deployment)
            report = c.verify(deployment, integrated=True, hooks=False, smoke=False)
            emit({"installed": True, "profile": str(profile), "generation": deployment["id"], "local_checks_passed": report["passed"],
                  "embedded": report["ready"], "verification": str(Path(deployment["home"]).parent / "verification.json"),
                  "next": "Review the new hooks in the target runtime, then run fresh root and worker receiver checks."})
            return 0 if report["passed"] else 1
    if args.command == "source":
        with c.lock(profile):
            source = c.acquire_source(profile, supplied=args.source, network=args.network)
        emit({"source": str(source), **c.verify_source(source, strict=True)})
    elif args.command == "source-review":
        from source_review import review
        emit(review(profile, args.revision))
    elif args.command == "setup-checklist":
        from setup_checklist import inspect
        emit(inspect(profile, args.refresh))
    elif args.command == "maintain-recall":
        with c.lock(profile):
            emit(c.maintain_recall(c.active(profile)))
    elif args.command == "repair-wiring":
        with c.lock(profile):
            emit(c.repair_wiring(profile, args.apply))
    elif args.command == "guardian":
        with c.lock(profile):
            emit(c.install_guardian(profile, args.apply))
    elif args.command == "status":
        emit(c.describe(profile))
    elif args.command == "bundle":
        emit(bundle(args.output))
    elif args.command == "faculty":
        catalog = c.read_json(c.SKILL / "references/mechanisms.json")
        rows = [row for row in catalog["mechanisms"] if not args.id or row["id"] == args.id]
        if not rows:
            raise c.ConnectorError("Unknown mechanism id")
        emit({"revision": catalog["revision"], "mechanisms": rows, "note": "Discovery/configuration is not an engine-admission or A-grade claim."})
    elif args.command == "probe-root":
        if not args.apply:
            emit({"plan": "Run one fresh Codex session with two raw import-count claims, actual bound recall, and receiver evidence.", "profile": str(profile), "uses_model_access": True,
                  "writes": "Private generation fixture, native recall/cache/telemetry and task checklist", "trust": "one-invocation reviewed automation" if args.reviewed_automation else "normal runtime review"})
        else:
            from receiver_probe import run_root
            emit(run_root(profile, cli=args.cli, reviewed_automation=args.reviewed_automation, with_worker=args.with_worker, timeout=args.timeout))
    elif args.command == "resume-root":
        if not args.apply:
            emit({"profile": str(profile), "folder": args.folder, "uses_model_access": True,
                  "plan": "Resume only the recorded native thread in the same authorized receiver fixture; retain all failed gates."})
        else:
            from receiver_probe import resume_root
            emit(resume_root(profile, args.folder, cli=args.cli, with_worker=args.with_worker, timeout=args.timeout))
    elif args.command == "verify":
        report = c.verify(c.active(profile), integrated=True, hooks=not args.skip_hook_probes, smoke=not args.skip_smoke)
        emit(report)
        return 0 if report["ready"] else 2 if report["passed"] else 1
    elif args.command == "fingerprints":
        import brain_binding as binding
        from fingerprint_evidence import inspect
        binding.resolve(profile)
        emit(inspect(c.active(profile)))
    elif args.command == "disable":
        with c.lock(profile):
            emit(c.disable(profile))
    elif args.command == "enable":
        deployment = c.active(profile)
        with c.lock(profile):
            if deployment["runtime"] == "codex":
                from profile_scope import configure
                permission = configure(profile)
                deployment["codex_scope"] = {**deployment.get("codex_scope", {}), **permission, "owned": permission["added"] or deployment.get("codex_scope", {}).get("owned", False)}
                c.atomic(Path(deployment["home"]).parent / "deployment.json", deployment)
            c.native(deployment, "switch", ["on"])
            (c.state_dir(profile) / "DISCONNECTED").unlink(missing_ok=True)
            result = c.subprocess.run([sys.executable, str(profile / "skills/brain-framework/scripts/standing_activation.py"), "enable", "--runtime", deployment["runtime"]], env={**c.os.environ, "CODEX_HOME": str(profile)}, capture_output=True, text=True)
            if result.returncode:
                raise c.ConnectorError(result.stderr)
            emit({"connected": True})
    elif args.command == "rollback":
        with c.lock(profile):
            emit(c.rollback(profile))
    else:
        from brain_binding import resolve
        deployment = resolve(profile, args.brain, args.workspace)
        if (c.state_dir(profile) / "DISCONNECTED").exists():
            raise c.ConnectorError("This profile is disconnected; reconnect before Brain reads or writes")
        tool = "brain" if args.command == "recall" else args.tool
        arguments = (["recall"] + args.arguments) if args.command == "recall" else args.arguments
        mutating = ((tool == "brain" and (not arguments or arguments[0] not in {"recall", "node", "neighbors", "status", "search"}))
                    or tool in {"script", "dream", "graph-capture", "workspace", "checkpoint"})
        # This exact original wake establishes the prerequisite that admission
        # checks. Older deployed guards advertise it without the newer lane flag.
        # It still requires the correct designated brain; no other script route
        # or extra arguments acquire repair authority through this exception.
        prerequisite_wake = tool == "script" and arguments == ["dreaming/codex_wake.py"]
        if mutating and not args.maintenance and not prerequisite_wake:
            from brain_binding import require_admission
            require_admission(profile, args.operation_session, args.workspace, args.brain)
        if tool == "checklist" and arguments and arguments[0] == "seen":
            raise c.ConnectorError("The seen mark belongs to the user; use the owner's native checklist interface")
        result = c.native(deployment, tool, arguments, check=False, timeout=1800 if tool == "dream" else 120,
                          session_id=args.operation_session)
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        return result.returncode
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (c.ConnectorError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
