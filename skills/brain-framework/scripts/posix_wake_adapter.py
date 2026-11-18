"""Observe the POSIX runner's stable-inode flock in the pinned original wake.

The original source remains unchanged. Only its marker-age/process-name lock
branch is replaced in memory; all other wake checks and output stay original.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import sys

sys.dont_write_bytecode = True
SOURCE_REVISION = "c788e6690a6c40f5637bb1c9f9caef3cf1353a73"
SOURCE_SHA256 = "424d2057b53245a0bb4564c1856c03c5d6e8af7d7617afd4b9ff5bf53aa1289c"


def observe_lock(path):
    """Sample a real flock without creating, unlinking, writing or restamping it.

    A nonblocking conflict proves a holder at this instant. An acquired lock
    proves availability at this instant, including old persistent markers.
    Every other inability to inspect is unverified, never an available lock.
    """
    descriptor = None
    acquired = False
    try:
        import fcntl
        try:
            before = os.lstat(path)
        except FileNotFoundError:
            return {"state": "absent"}
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("dream.lock is not a regular file; preserve it for inspection")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0))
        opened = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("dream.lock changed while opening")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
            state = "available"
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise
            state = "held"
        current = os.lstat(path)
        if not stat.S_ISREG(current.st_mode) or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("dream.lock inode changed during observation")
        return {"state": state, "mtime": opened.st_mtime, "inode": opened.st_ino}
    except (ImportError, OSError, ValueError) as exc:
        return {"state": "unverified", "error": type(exc).__name__ + ": " + str(exc)}
    finally:
        if descriptor is not None:
            if acquired:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


_LOCK_BRANCH = """
_lock_observation = _brain_observe_posix_lock(lockp)
if _lock_observation['state'] == 'held':
    lock_t = _lock_observation['mtime']
    dream_note = ('a dream lock is held right now (POSIX flock observed; marker timestamp is not a start time) - '
                  'leave the dream-owned files alone (MEMORY.md, INSTINCTS.md, the brain views) until it lands')
elif _lock_observation['state'] == 'unverified':
    warn.append('POSIX dream lock observation UNVERIFIED: ' + _lock_observation['error'] +
                ' - preserve the stable inode; inspect the lock before dream-owned writes')
"""


def original_namespace(source, observer=observe_lock, liveness_observer=None):
    """Fail closed on any source revision/shape change; never patch on a guess."""
    source = Path(source)
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError("POSIX wake overlay requires the reviewed original at " + SOURCE_REVISION)
    tree = ast.parse(raw, filename=str(source))
    part = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "part1")
    replacements = []
    for parent in ast.walk(part):
        for field, value in ast.iter_fields(parent):
            if not isinstance(value, list):
                continue
            for index, node in enumerate(value):
                if isinstance(node, ast.If) and ast.unparse(node.test) == "os.path.exists(lockp)":
                    replacements.append((value, index))
    if len(replacements) != 1:
        raise ValueError("Reviewed original POSIX lock branch is not unique")
    statements, index = replacements[0]
    statements[index:index + 1] = ast.parse(_LOCK_BRANCH).body
    if liveness_observer is not None:
        matches = []
        for parent in ast.walk(part):
            for _, value in ast.iter_fields(parent):
                if not isinstance(value, list):
                    continue
                for index, node in enumerate(value):
                    if (isinstance(node, ast.Try) and node.body and isinstance(node.body[0], ast.Assign)
                            and any(isinstance(target, ast.Name) and target.id == "lv" for target in node.body[0].targets)):
                        matches.append((value, index))
        if len(matches) != 1:
            raise ValueError("Reviewed original liveness consumer is not unique")
        value, index = matches[0]
        value[index:index + 1] = ast.parse("for _finding in _brain_live_liveness(os.path.join(DR, '.liveness_status.json')):\n    warn.append(_finding)").body
        # The scoped observer verifies embedded heartbeat time and process
        # identity. The original only checks mtime and advises a restart.
        compute = [node for node in part.body if isinstance(node, ast.Try) and node.body
                   and isinstance(node.body[0], ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "csf" for target in node.body[0].targets)]
        if len(compute) != 1:
            raise ValueError("Reviewed original compute consumer is not unique")
        part.body.remove(compute[0])
    dream_logs = [(body, index) for node in ast.walk(part) for _, body in ast.iter_fields(node)
                  if isinstance(body, list) for index, stmt in enumerate(body)
                  if isinstance(stmt, ast.Assign) and ast.unparse(stmt) == "txt = read(dlog)"]
    if len(dream_logs) != 1:
        raise ValueError("Reviewed original latest dream reader is not unique")
    body, index = dream_logs[0]
    body[index + 1:index + 1] = ast.parse(
        'for _step in re.findall(r"STEP (?:FAILED|CAPPED|SKIPPED): ([^\\r\\n]+)", txt):\n'
        '    _notice = "Latest dream step did not pass: " + _step\n'
        '    warn.append(_notice)\n'
        '    _brain_dream_notices.append(_notice)'
    ).body
    headings = [node for node in ast.walk(part) if isinstance(node, ast.Constant)
                and node.value == "\n--- Waking check: WARN - the identity layer may not have fully travelled ---"]
    guidance = [node for node in ast.walk(part) if isinstance(node, ast.Constant) and isinstance(node.value, str)
                and node.value.startswith("  Distrust the green light: treat the flagged reads as stale")]
    if len(headings) != 1 or len(guidance) != 1:
        raise ValueError("Reviewed original wake warning presentation changed")
    headings[0].value = "\n--- Waking check: attention required ---"
    guidance[0].value = "  Inspect the reported findings before treating this wake as healthy."
    ast.fix_missing_locations(tree)
    observed_errors = []
    observed_liveness = []
    observed_dream_steps = []

    def sample(path):
        observation = observer(path)
        if observation["state"] == "unverified":
            observed_errors.append(observation["error"])
        return observation

    def live_liveness(path):
        try:
            findings = liveness_observer(path)
            if not isinstance(findings, list) or any(not isinstance(item, str) for item in findings):
                raise ValueError("host liveness observer returned malformed findings")
        except Exception as exc:
            findings = ["host liveness observer UNVERIFIED: " + type(exc).__name__]
        observed_liveness.extend(findings)
        return findings

    namespace = {"__name__": "brain_original_posix_wake", "__file__": str(source),
                 "_brain_observe_posix_lock": sample, "_brain_live_liveness": live_liveness,
                 "_brain_liveness_notices": observed_liveness, "_brain_dream_notices": observed_dream_steps}
    exec(compile(tree, str(source), "exec"), namespace)
    return namespace, observed_errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--profile")
    args, remaining = parser.parse_known_args()
    liveness = None
    if args.profile:
        import connector as c
        import codex_health_observer as health
        deployment = health.selected_main(args.profile)
        c.verify_source(deployment["source"])
        c.verify_runtime_code(deployment)
        liveness = lambda path: health.wake_findings(deployment, path)
    namespace, errors = original_namespace(args.source, liveness_observer=liveness)
    sys.argv = [args.source, *remaining]
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        result = namespace["main"]()
    body = output.getvalue()
    alerts = []
    for code, findings, limit in (
        ("DREAM_LOCK_OBSERVER_UNVERIFIED", errors, 1),
        ("HOST_DIAGNOSTICS_ATTENTION", namespace["_brain_liveness_notices"], 2),
        ("DREAM_STEPS_ATTENTION", namespace["_brain_dream_notices"], 2),
    ):
        if findings:
            distinct = list(dict.fromkeys(findings))
            visible = [" ".join(finding.split())[:150] for finding in distinct[:limit]]
            if len(distinct) > limit:
                visible.append(f"{len(distinct) - limit} more in the wake context")
            alerts.append(f"BRAIN ALARM [{code}] " + " | ".join(visible))
    if alerts:
        print(json.dumps({"systemMessage": "\n".join(alerts),
                          "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": body}}, ensure_ascii=False))
    else:
        sys.stdout.write(body)
    return result


if __name__ == "__main__":
    raise SystemExit(main() or 0)
