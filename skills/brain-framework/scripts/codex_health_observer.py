"""Bind pinned diagnostic observers to the selected external Codex host.

The original diagnostics supply the guards. This adapter supplies the Codex
receiver, the designated corpus, and POSIX-specific evidence.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import datetime as dt
import hashlib
import importlib
import io
import json
import math
import os
from pathlib import Path
import re
import shlex
import stat
import sys
import time
import tomllib

from health_inputs import governor_findings, lexical_findings, owner_window, recurrence_findings

sys.dont_write_bytecode = True
SOURCE_HASHES = {
    # Each original is accepted only in byte sets whose overlay spans were read against those exact bytes.
    "liveness": ("dreaming/hook_liveness.py", frozenset({
        "d80451ef72edff5179a448498bec181d12a9510b0f682a4ea4b7049c9b72bf5d",  # c788e669
        "580c33231fbdc7d90001fec29cb70b7a4fd14183e6abbc11f7dc1619880a11e9",  # brain-framework-public 18e6a2f4 (comment-only change, same AST)
        "c4e8c88624c16903e9e79d52a9af169682e897747528a005d8c926fca81c0225",  # math-brain-public 58c06d0c
    })),
    "status": ("dreaming/brain_status.py", frozenset({
        "2c71fd2a15dd4f08eeb52cc89e83eca6e31edf69f84a45af1a905e0379664043",  # c788e669, brain-framework-public 18e6a2f4
        "1710cc96698b13f072b63400f7a4f82c245759706f1efaf74f821b81786050f2",  # math-brain-public 58c06d0c
    })),
}
MAX_LIVENESS_AGE = 30 * 3600


def regular_bytes(path, maximum=2 * 1024 * 1024):
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_size > maximum:
        raise ValueError("Not a bounded regular observer input: " + str(path))
    raw = path.read_bytes()
    after = path.lstat()
    if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError("Observer input changed while read: " + str(path))
    return raw


def read_object(path, maximum=2 * 1024 * 1024):
    result = json.loads(regular_bytes(path, maximum).decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("Observer input is not an object: " + str(path))
    return result


def selected_main(profile):
    import connector as c
    import brain_binding
    profile = c.profile_path(profile)
    bound = brain_binding.resolve(profile)
    deployment = c.active(profile)
    if (bound["profile"], bound["id"]) != (deployment["profile"], deployment["id"]):
        raise c.ConnectorError("Host observer requires this receiver to be the explicitly designated main")
    if deployment["runtime"] != "codex":
        raise c.ConnectorError("This diagnostic overlay is scoped to the Codex host")
    return deployment


def configuration(deployment):
    expected = Path(deployment["profile"]) / "hooks.json"
    if Path(deployment["hook_config"]) != expected:
        raise ValueError("Codex hook config differs from the selected external profile")
    document = read_object(expected)
    hooks = document.get("hooks")
    if not isinstance(hooks, dict) or any(not isinstance(groups, list) for groups in hooks.values()):
        raise ValueError("Codex hook registry is malformed: " + str(expected))
    for groups in hooks.values():
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ValueError("Codex hook group is malformed")
            if any(not isinstance(item, dict) or not isinstance(item.get("command"), str) for item in group["hooks"]):
                raise ValueError("Codex hook command is malformed")
    return hooks


def command_tokens(command):
    try:
        return shlex.split(command)
    except ValueError:
        return []


def settings_for(deployment):
    return tomllib.loads(regular_bytes(Path(deployment["profile"]) / "config.toml").decode("utf-8"))


def configured_handlers(deployment, hooks, settings=None):
    """Profile declarations only; eligible definitions are never called active host proof."""
    settings = settings_for(deployment) if settings is None else settings
    features = settings.get("features", {})
    review = settings.get("hooks", {}).get("state", {})
    if not isinstance(features, dict) or not isinstance(review, dict):
        raise ValueError("Native feature/review configuration is malformed")
    # Verified against native0.169 features list: canonical key wins its deprecated alias.
    feature = features.get("hooks", features.get("codex_hooks", True))
    if type(feature) is not bool:
        raise ValueError("Native hooks feature flag is not boolean")
    records = []
    for event, groups in hooks.items():
        event_id = re.sub(r"(?<!^)(?=[A-Z])", "_", event).lower()
        for gi, group in enumerate(groups):
            for hi, item in enumerate(group["hooks"]):
                key = "%s:%s:%d:%d" % (Path(deployment["profile"]) / "hooks.json", event_id, gi, hi)
                row = review.get(key, {})
                if not isinstance(row, dict) or type(row.get("enabled", True)) is not bool:
                    raise ValueError("Native per-hook enable state is malformed: " + key)
                reviewed = isinstance(row.get("trusted_hash"), str) and bool(row["trusted_hash"])
                unsupported = any(name in group or name in item for name in ("enabled", "disabled"))
                enabled = feature and row.get("enabled", True) and not unsupported
                reason = ("profile hooks feature is off" if not feature else
                          "native review state enabled=false" if not row.get("enabled", True) else
                          "unsupported definition enable/disable fields; effective eligibility UNQUALIFIED" if unsupported else
                          "native review record is absent" if not reviewed else "")
                records.append({"event": event, "group": group, "item": item, "key": key,
                                "review_present": reviewed, "eligible": enabled and reviewed, "reason": reason})
    return records, feature


def wired_originals(deployment, hooks):
    """Only actually registered Brain definitions select original handler files."""
    wired = {}
    bridge = str(Path(deployment["profile"]) / "skills/brain-framework/scripts/hook_bridge.py")
    records, _ = configured_handlers(deployment, hooks)
    for record in records:
        event, group, item = record["event"], record["group"], record["item"]
        if not record["eligible"] or group not in deployment.get("managed_hook_groups", {}).get(event, []):
            continue
        tokens = command_tokens(item["command"])
        if bridge not in tokens or "--handler" not in tokens or "--profile" not in tokens:
            continue
        pi = tokens.index("--profile") + 1
        if pi >= len(tokens) or Path(tokens[pi]).resolve() != Path(deployment["profile"]).resolve():
            continue
        index = tokens.index("--handler") + 1
        key = tokens[index] if index < len(tokens) else ""
        spec = deployment.get("handlers", {}).get(key)
        if not spec or spec.get("event") != event:
            continue
        for value in [bridge, *spec.get("argv", [])]:
            if str(value).endswith(".py") and Path(value).is_absolute():
                wired.setdefault(str(value), set()).add(event)
    return wired


def dispatcher_registered(deployment, hooks):
    expected = (Path(deployment["home"]) / ".claude/dreaming/hook_dispatch.py").resolve()
    records, _ = configured_handlers(deployment, hooks)
    for record in records:
        if record["eligible"] and direct_script(record["item"]["command"], expected):
            return True
    return any(Path(path).resolve() == expected for path in wired_originals(deployment, hooks))


def direct_script(command, expected):
    """Recognize a direct script invocation, never a path merely printed/passed as data."""
    tokens = command_tokens(command)
    if not tokens:
        return False
    if Path(tokens[0]).is_absolute() and Path(tokens[0]).resolve() == expected:
        return True
    if not re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", Path(tokens[0]).name):
        return False
    index = 1
    while index < len(tokens) and tokens[index] in ("-B", "-S", "-s", "-E", "-I", "-u", "-q", "-O", "-OO"):
        index += 1
    return index < len(tokens) and Path(tokens[index]).is_absolute() and Path(tokens[index]).resolve() == expected


def journal_observation(deployment, now=None):
    """Read a bounded protocol-journal tail; do not scan native rollouts or imply UI delivery."""
    now = time.time() if now is None else now
    path = Path(deployment["home"]).parent / "hook-delivery.jsonl"
    if not path.exists():
        return {"rows": 0, "unrecovered": [], "unqualified": ["bridge protocol journal unavailable; native delivery remains UNQUALIFIED"]}
    if path.is_symlink() or not path.is_file():
        raise ValueError("Bridge journal is not a regular non-link file")
    with path.open("rb") as stream:
        stream.seek(0, 2)
        offset = max(0, stream.tell() - 1024 * 1024)
        stream.seek(offset)
        if offset:
            stream.readline()
        raw = stream.read(1024 * 1024)
    lines = raw.splitlines()
    incomplete = bool(raw and not raw.endswith(b"\n"))
    if incomplete:
        lines = lines[:-1]
    latest, valid, malformed = {}, 0, 0
    for line in lines[-2000:]:
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("not an object")
            if row.get("revision") != deployment["source_revision"]:
                continue
            when = dt.datetime.fromisoformat(row["at"].replace("Z", "+00:00")).timestamp()
            if when < now - 7 * 86400:
                continue
            if when > now + 300 or type(row.get("rc")) is not int or not re.fullmatch(r"[0-9a-f]{64}", str(row.get("session_hash", ""))):
                raise ValueError("invalid protocol observation")
            key = row["handler"]
            if key not in deployment.get("handlers", {}):
                continue
            valid += 1
            if key not in latest or when >= latest[key][0]:
                latest[key] = (when, row["rc"])
        except (ValueError, TypeError, KeyError, AttributeError):
            malformed += 1
    unqualified = []
    if not valid:
        unqualified.append("no matching rows in bounded bridge protocol tail; activity/delivery UNQUALIFIED, not declared dead")
    if malformed or incomplete:
        unqualified.append("bridge protocol tail has %d malformed and %d partial observations; reader remains UNQUALIFIED" % (malformed, int(incomplete)))
    return {"rows": valid, "tail_bytes": len(raw), "unrecovered": [key for key, (_, rc) in sorted(latest.items()) if rc],
            "unqualified": unqualified, "scope": "last1MiB/2000rows, matching revision, observed within7days; protocol is not native UI/behavior proof"}


def observe_host(deployment):
    profile = Path(deployment["profile"])
    result = {"profile": str(profile), "hook_config": str(profile / "hooks.json"), "failures": [], "unqualified": []}
    try:
        hooks = configuration(deployment)
        import connector as c
        c.verify_guardian(deployment)
        required = deployment.get("managed_hook_groups", {})
        for event, groups in required.items():
            for group in groups:
                count = hooks.get(event, []).count(group)
                if count != 1:
                    result["failures"].append("actual Codex Brain registration %s occurs %d times; expected exactly1 in %s" % (event, count, profile / "hooks.json"))
        records, feature = configured_handlers(deployment, hooks)
        missing = [record["key"] for record in records if not record["review_present"]]
        owned_disabled = [record for record in records if not record["eligible"]
                          and record["group"] in required.get(record["event"], [])]
        if owned_disabled:
            result["failures"].append("actual Brain definitions are disabled/unqualified by native profile controls: " +
                                      "; ".join(record["key"] + " (" + record["reason"] + ")" for record in owned_disabled[:5]))
        result.update(registered_commands=len(records), configured_eligible_commands=sum(record["eligible"] for record in records),
                      stored_review_records=len(records) - len(missing), profile_hooks_feature=feature,
                      dispatcher_selected=dispatcher_registered(deployment, hooks))
        if missing:
            result["unqualified"].append("native review records missing for%d/%d configured commands; use /hooks, never write trust hashes" % (len(missing), len(records)))
        result["configuration_valid"] = not result["failures"]
        journal = journal_observation(deployment)
        result["protocol_journal"] = journal
        result["unqualified"].extend(journal["unqualified"])
        if journal["unrecovered"]:
            result["failures"].append("latest observed bridge protocol error remains unrecovered for: " + ", ".join(journal["unrecovered"]))
        from receiver_probe import validate_receivers
        receiver = validate_receivers(deployment)
        result["receiver"] = receiver
        if not receiver.get("passed"):
            result["unqualified"].append("native root/worker behavior UNQUALIFIED: " + str(receiver.get("reason", "no current receiver proof")))
        result["review_scope"] = "stored record presence only; actual native trust/behavior requires original receiver validation"
        result["enablement_scope"] = "selected profile declarations only, not active-host proof; managed/project/invocation overrides require native receiver evidence"
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RuntimeError) as exc:
        result["failures"].append("actual Codex host inspection UNVERIFIED at %s: %s: %s" % (profile, type(exc).__name__, str(exc)[:240]))
        result["configuration_valid"] = False
    return result


def wrapper_controls():
    failures = []
    for name in ("codex_warning_acceptance", "codex_reflex_hook", "codex_stop_guard"):
        try:
            module = importlib.import_module(name)
            with contextlib.redirect_stdout(io.StringIO()):
                rc = module._selftest()
            if rc:
                failures.append(name + ".py original selftest failed")
        except Exception as exc:
            failures.append(name + ".py original selftest UNVERIFIED: " + type(exc).__name__)
    return failures


_HOST_BRANCH = r"""
_order_bad = []
_codex_host = _brain_observe_host()
_codex_bad = list(_codex_host['failures']) + list(_codex_host['unqualified']) + _brain_wrapper_controls()
print('\nCODEX ACTUAL PROFILE + ORIGINAL WRAPPER CONTROLS:')
print('  actual profile - ' + _codex_host['profile'])
print('  registered commands=%s; profile-eligible definitions=%s; stored review records=%s (these are not active native trust/behavior proof)' % (_codex_host.get('registered_commands', 'unknown'), _codex_host.get('configured_eligible_commands', 'unknown'), _codex_host.get('stored_review_records', 'unknown')))
print('  original Claude Stop-order/virtual-profile assumptions are not this Codex host contract')
for _problem in _codex_bad:
    print('  ' + _problem)
"""


def _assigned(node, name):
    return isinstance(node, ast.Assign) and any(isinstance(part, ast.Name) and part.id == name
                                               for target in node.targets for part in ast.walk(target))


def _replace_unique(tree, predicate, replacement):
    matches = []
    for parent in ast.walk(tree):
        for _, value in ast.iter_fields(parent):
            if isinstance(value, list):
                matches.extend((value, index) for index, node in enumerate(value) if isinstance(node, ast.AST) and predicate(node))
    if len(matches) != 1:
        raise ValueError("Reviewed diagnostic branch is not unique: %d matches" % len(matches))
    body, index = matches[0]
    body[index:index + 1] = ast.parse(replacement).body


def _replace_span(main, first, next_section, replacement):
    starts = [i for i, node in enumerate(main.body) if _assigned(node, first)]
    ends = [i for i, node in enumerate(main.body)
            if isinstance(node, ast.Try) and any(_assigned(stmt, next_section) for stmt in node.body)]
    if len(starts) != 1 or len(ends) != 1 or ends[0] <= starts[0]:
        raise ValueError("Reviewed diagnostic section changed: " + first)
    main.body[starts[0]:ends[0]] = ast.parse(replacement).body


def _compile_readonly(path, **_):
    # Compiling with the default pyc destination writes within locked source.
    compile(Path(path).read_bytes(), path, "exec", dont_inherit=True)


def _lexical(deployment):
    try:
        import bm25
        index = Path(deployment["home"]) / ".claude/dreaming/bm25_index.json.gz"
        findings = lexical_findings(deployment["memory"], index, bm25.read_docs)
    except (ImportError, OSError, ValueError, TypeError, KeyError) as exc:
        return ["BM25 index or corpus UNVERIFIED: " + str(exc)[:300]]
    print("  BM25 corpus and scoring inputs " + ("differ" if findings else "match"))
    return findings


def _owner_prompts(deployment, wired):
    if not wired:
        print("  Owner-prompt views: no registered owner-prompt reader")
        return []
    try:
        import owner_prompts_hourly as builder
        prompts = Path(deployment["memory"]) / "prompts"
        ledger = Path(builder.LEDGER)
        if ledger.resolve() != (prompts / "PROMPTS.jsonl").resolve():
            raise ValueError("Owner-prompt builder is bound to a different ledger")
        # The native view may contain up to N_KEEP rows of JSON_TEXT_CAP each.
        view = read_object(prompts / "owner_prompts_hourly.json",
                           builder.N_KEEP * (4 * builder.JSON_TEXT_CAP + 2048))
        findings, kept, eligible, pending = owner_window(view, ledger, builder, time.time())
        print(f"  Owner-prompt view: {kept} retained, {eligible} eligible in current window, {pending} recent pending")
        return findings
    except (ImportError, OSError, ValueError, TypeError, KeyError) as exc:
        return ["Owner-prompt view or ledger UNVERIFIED: " + str(exc)[:300]]


def _compute_findings(deployment):
    home = Path(deployment["home"])
    compute = home / ".claude/compute"
    if ("governor" in deployment.get("enabled", [])
            or any(os.path.lexists(compute / name) for name in ("governor.pid", "STOP_GOVERNOR"))
            or (home.parent / "compute-governor-launchd").is_dir()):
        return governor_findings(home)
    return []


def _recurrence_findings(deployment):
    return recurrence_findings(Path(deployment["home"]) / ".claude/dreaming", deployment["memory"])


def original_namespace(deployment, mode):
    relative, expected = SOURCE_HASHES[mode]
    source = Path(deployment["source"]) / relative
    raw = regular_bytes(source)
    if hashlib.sha256(raw).hexdigest() not in expected:
        raise ValueError("Diagnostic overlay requires exact reviewed original: " + relative)
    tree = ast.parse(raw, filename=str(source))
    main = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "main")
    if mode == "liveness":
        start = [i for i, node in enumerate(main.body) if _assigned(node, "_order_bad")]
        stop = [i for i, node in enumerate(main.body) if isinstance(node, ast.Try) and node.body and _assigned(node.body[0], "snap")]
        if len(start) != 1 or len(stop) != 1 or stop[0] <= start[0]:
            raise ValueError("Reviewed host diagnostic span changed")
        main.body[start[0]:stop[0]] = ast.parse(_HOST_BRANCH).body
        compiles = [node for node in ast.walk(main) if isinstance(node, ast.Call)
                    and ast.unparse(node.func) == "py_compile.compile"]
        if len(compiles) != 1:
            raise ValueError("Reviewed hook compile check changed")
        compiles[0].func = ast.Name(id="_brain_compile_readonly", ctx=ast.Load())
        _replace_unique(main, lambda node: _assigned(node, "_artifact_bad"),
                        "_artifact_bad = _brain_compute() + _brain_recurrence()")
        _replace_span(main, "cov", "_p_routes", "_artifact_bad.extend(_brain_lexical())")
        _replace_span(main, "_op_json", "_hd",
                      "_artifact_bad.extend(_brain_owner_prompts(_wired_anywhere('owner_prompts_hook.py')))")
        _replace_unique(main, lambda node: isinstance(node, ast.If) and ast.unparse(node.test) == "_mt_age is None",
                        "if sys.platform == 'win32' and (_mt_age is None or _mt_age > 2.0):\n"
                        "    _mt_bad.append('Windows math transport has no run in two hours')\n"
                        "elif sys.platform != 'win32':\n"
                        "    print('  Math transport: the POSIX runner provides no short-interval timer; checking queued work')")
        _replace_unique(main, lambda node: isinstance(node, ast.If) and ast.unparse(node.test) == "_blk < len(_adv)",
                        "_source_owner_notes = ['pre_report source-owner examples UNQUALIFIED (%d/%d blocked; "
                        "no locally qualified prior baseline; original predicates and expected counts unchanged)' % (_blk, len(_adv))]\n"
                        "print('  ' + _source_owner_notes[0])")
        dispatch = [node for node in ast.walk(main) if isinstance(node, ast.If) and "HOOK_DISPATCH_ENABLED" in ast.unparse(node.test)]
        if len(dispatch) != 1:
            raise ValueError("Reviewed dispatcher-selection branch changed")
        dispatch[0].test = ast.BoolOp(op=ast.And(), values=[ast.Call(func=ast.Name(id="_brain_dispatcher_selected", ctx=ast.Load()), args=[], keywords=[]), dispatch[0].test])
        dispatch[0].orelse = ast.parse("print('  dispatcher allowlist   not selected by actual Codex registrations; copied marker is not active host wiring' if not _brain_dispatcher_selected() else '  dispatcher allowlist   registered dispatcher is not enabled/available; inspect marker and wiring')").body
        status = [node for node in ast.walk(main) if isinstance(node, ast.Dict) and {key.value for key in node.keys if isinstance(key, ast.Constant)} == {"ts", "dead", "stale", "safety", "artifacts"}]
        if len(status) != 1:
            raise ValueError("Reviewed liveness publication shape changed")
        status[0].keys.append(ast.Constant(value="observer_context"))
        status[0].values.append(ast.Name(id="_brain_observer_context", ctx=ast.Load()))
        status[0].keys.append(ast.Constant(value="source_owner_notes"))
        status[0].values.append(ast.Name(id="_source_owner_notes", ctx=ast.Load()))
        publishers = [node for node in ast.walk(main) if isinstance(node, ast.Call)
                      and ast.unparse(node.func) == "json.dump" and node.args and node.args[0] is status[0]]
        if len(publishers) != 1:
            raise ValueError("Reviewed liveness publisher changed")
        publishers[0].func = ast.Name(id="_brain_publish", ctx=ast.Load())
        publishers[0].args = [status[0]]
        publishers[0].keywords = []
        for node in ast.walk(main):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                node.value = node.value.replace("pre_report stale numeric oracle REGRESSED", "pre_report stale numeric oracle CHECK FAILED")
                node.value = node.value.replace("WIRING HEALTH (wired in settings.json ->", "WIRING HEALTH (actual Codex Brain registrations ->")
                node.value = node.value.replace("safety/delivery contract failure(s)", "unqualified/failed safety or delivery check(s)")
                node.value = node.value.replace("derived artifact(s) behind:", "live health finding(s):")
                node.value = node.value.replace("ok - blocks all %d durable contradictions; retired magnitude does not block",
                                                "Local wrapper checks and retired numeric control passed; %d source-owner examples evaluated separately")
    else:
        _replace_unique(main, lambda node: isinstance(node, ast.If) and "_blocked == len(_durable_discuss)" in ast.unparse(node.test),
                        "print('\\nSAFETY   source-owner report examples UNQUALIFIED: durable-discuss blocked %d/%d; retired-rate blocks %d/%d; no locally qualified prior baseline; original predicates/counts unchanged' % (_blocked, len(_durable_discuss), _stale_blocks, len(_retired_rates)))\nif _stale_blocks:\n    print('SAFETY   CHECK FAILED: retired source-owner numeric examples currently block; inspect original predicates')")
    ast.fix_missing_locations(tree)
    runtime = Path(deployment["home"]) / ".claude" / Path(relative).name
    if mode in ("liveness", "status"):
        runtime = Path(deployment["home"]) / ".claude/dreaming" / Path(relative).name
    namespace = {"__name__": "brain_original_scoped_diagnostic", "__file__": str(runtime),
                 "_source_owner_notes": []}
    exec(compile(tree, str(source), "exec"), namespace)
    namespace.update(HOME=str(deployment["home"]), MEM=deployment["memory"], MEMDIR=deployment["memory"],
                     DR=str(Path(deployment["home"]) / ".claude/dreaming"), HERE=str(Path(deployment["home"]) / ".claude/dreaming"),
                     BRAIN=str(Path(deployment["home"]) / "brain-repo/01_ai_consciousness/brain"),
                     CODEX_HOOKS=str(Path(deployment["profile"]) / "hooks.json"),
                     CODEX_CONFIG=str(Path(deployment["profile"]) / "config.toml"), CODEX_SESSIONS=str(Path(deployment["profile"]) / "sessions"),
                     _brain_observe_host=lambda: observe_host(deployment), _brain_wrapper_controls=wrapper_controls,
                     _brain_dispatcher_selected=lambda: dispatcher_registered(deployment, configuration(deployment)),
                     _brain_compile_readonly=_compile_readonly, _brain_lexical=lambda: _lexical(deployment),
                     _brain_owner_prompts=lambda wired: _owner_prompts(deployment, wired),
                     _brain_compute=lambda: _compute_findings(deployment),
                     _brain_recurrence=lambda: _recurrence_findings(deployment),
                     _brain_publish=lambda value: _publish(runtime.parent / ".liveness_status.json", value))
    namespace["_wired_hooks"] = lambda: wired_originals(deployment, configuration(deployment))
    namespace["_wired_anywhere"] = lambda script: any(
        Path(path).resolve() == (Path(deployment["home"]) / ".claude/dreaming" / script).resolve()
        for path in wired_originals(deployment, configuration(deployment)))
    namespace["_brain_observer_context"] = observer_context(deployment)
    return namespace


def _publish(path, value):
    from connector import atomic
    atomic(path, value)


def observer_context(deployment):
    from receiver_probe import deployment_fingerprint
    return {"schema": "codex-host-observer-v1", "runtime": "codex", "profile": deployment["profile"],
            "deployment": deployment["id"], "memory": deployment["memory"],
            "deployment_fingerprint": deployment_fingerprint(deployment), "baseline": "source-owner examples unqualified; no local prior baseline supplied"}


def bound_snapshot(deployment, path, now=None):
    snapshot = read_object(path)
    for key in ("dead", "stale", "safety"):
        if not isinstance(snapshot.get(key), list) or any(not isinstance(item, str) for item in snapshot[key]):
            raise ValueError("liveness findings malformed")
    if snapshot.get("observer_context") != observer_context(deployment):
        raise ValueError("source-layout liveness snapshot UNQUALIFIED for this Codex binding "
                         "(%d dead/%d stale/%d safety findings retained at %s); run bound native liveness observer"
                         % (len(snapshot["dead"]), len(snapshot["stale"]), len(snapshot["safety"]), path))
    for key in ("artifacts", "source_owner_notes"):
        if not isinstance(snapshot.get(key), list) or any(not isinstance(item, str) for item in snapshot[key]):
            raise ValueError("bound liveness findings malformed: " + key)
    when = snapshot.get("ts")
    now = time.time() if now is None else now
    if type(when) not in (int, float) or not math.isfinite(when) or not 0 <= now - when <= MAX_LIVENESS_AGE:
        raise ValueError("liveness observation time is absent, stale or invalid; rerun the bound audit")
    return snapshot


def wake_findings(deployment, path):
    host = observe_host(deployment)
    findings = list(host["failures"]) + list(host["unqualified"])
    findings.extend(_compute_findings(deployment))
    try:
        snapshot = bound_snapshot(deployment, path)
        for key in ("dead", "stale", "safety", "artifacts"):
            already_reported = {*findings, *snapshot["artifacts"]} if key == "stale" else set(findings)
            values = [value for value in snapshot[key] if value not in already_reported]
            if values:
                label = "live health" if key == "artifacts" else key
                findings.append("bound %s checks unresolved: %s" % (label, "; ".join(values)[:1600]))
    except (OSError, ValueError, TypeError) as exc:
        findings.append("bound liveness snapshot UNVERIFIED: " + type(exc).__name__ + ": " + str(exc)[:500])
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--mode", choices=tuple(SOURCE_HASHES), required=True)
    args, remaining = parser.parse_known_args()
    deployment = selected_main(args.profile)
    import connector as c
    c.verify_source(deployment["source"])
    c.verify_runtime_code(deployment)
    namespace = original_namespace(deployment, args.mode)
    sys.argv = [namespace["__file__"], *remaining]
    publishes = args.mode == "liveness" and not any(value in ("--help", "-h") for value in remaining)
    path = Path(deployment["home"]) / ".claude/dreaming/.liveness_status.json"
    before = regular_bytes(path) if publishes and os.path.lexists(path) else None
    started = time.time()
    result = namespace["main"]()
    finished = time.time()
    if publishes:
        after = regular_bytes(path)
        document = json.loads(after)
        when = document.get("ts") if isinstance(document, dict) else None
        if (after == before or type(when) not in (int, float) or not math.isfinite(when) or not started <= when <= finished
                or document.get("observer_context") != namespace["_brain_observer_context"]):
            raise ValueError("Original liveness did not produce a fresh bound publication; inspect actual writer effects")
        snapshot = bound_snapshot(deployment, path)
        if snapshot["dead"] or snapshot["artifacts"] or snapshot["safety"]:
            return 2
    if args.mode == "status":
        host = observe_host(deployment)
        print("\nCODEX HOST   actual external profile " + host["profile"])
        for finding in [*host["failures"], *host["unqualified"]]:
            print("  " + finding)
    return result


if __name__ == "__main__":
    raise SystemExit(main() or 0)
