#!/usr/bin/env python3
"""Compile Lean input and optionally check an explicitly reviewed named theorem.

Compilation alone is COMPILED (exit 3), not a proof certificate. Certification
requires --target and --reviewed-sha256 matching the exact submitted source bytes.
For --stmt those bytes include the selected imports, a newline, stripped statement,
and final newline. --file hashes the file bytes exactly.

Review is a CALLER ASSERTION: inspect source/imports/macros for output spoofing and
unsafe behavior. Lean source can execute code; this wrapper is not a sandbox.
VERIFIED_TARGET (exit 0) concerns only the named Lean theorem under propext,
Classical.choice and Quot.sound. It does not certify translation from prose.
Failed compilation is not evidence that the intended mathematical claim is false.

Exit 1: compiler rejection, admitted target, non-theorem or unapproved axiom.
Exit 2: unavailable setup/runtime, stale review or changed/missing evidence.
Exit 3: compilation succeeded but certificate evidence is incomplete/unreviewed.
"""
import argparse
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid

from proof_receipt import classify, digest, exit_code, target_commands, validate_target
from lean_process import run

# The project whose built Mathlib the checker compiles against: LEAN_CHECK_PROJECT, else the first candidate that has one.
# Until 30 Sep this was omega_comb alone, whose Mathlib a disk cleanup had emptied; the erdos_fc build the September
# papers were checked against went on 26 Sep, and every Lean route in the brain ended at SETUP. ~/lean_verify is the
# shared checker (Lean v4.33.1, Mathlib 0df444a3, the erdos_fc pins), restored 30 Sep from the local cache.
_CANDIDATES = [p for p in (os.environ.get('LEAN_CHECK_PROJECT'), Path.home() / 'lean_verify',
                           Path.home() / 'phase_retrieval_omega/lean/omega_comb') if p]


def _built(project):
    return (Path(project) / '.lake/packages/mathlib/.lake/build/lib/lean/Mathlib.olean').is_file()


def _toolchain(project):
    """The lean.exe of the project's own pinned toolchain; an olean compiled by one Lean does not load in another."""
    try:
        pin = (Path(project) / 'lean-toolchain').read_text(encoding='utf-8').strip()
        exe = Path.home() / '.elan/toolchains' / pin.replace('/', '--').replace(':', '---') / 'bin/lean.exe'
        if exe.is_file():
            return exe
    except OSError:
        pass
    return Path.home() / '.elan/toolchains/leanprover--lean4---v4.29.1/bin/lean.exe'


PROJECT = Path(next((p for p in _CANDIDATES if _built(p)), _CANDIDATES[0]))
LEAN = _toolchain(PROJECT)
COMPUTE = Path.home() / '.claude/compute/compute_state.json'
IMPORTS = ('import Mathlib.Tactic\nimport Mathlib.Analysis.SpecialFunctions.Exp\n'
           'import Mathlib.Analysis.SpecialFunctions.Log.Basic\n'
           'import Mathlib.Probability.Independence.Basic\n'
           'import Mathlib.MeasureTheory.Integral.Bochner.Basic\n')


MEM_FLOOR_MB = 3000   # below this a running compile is stopped by the 15 s poll: crash territory
# A compiler is STARTED only above this. A one-line Mathlib check commits about 4.1 GB (17 compiles logged on 5 Oct, peaks
# 4,098-4,101 MB), so a compile admitted with 3 GB free goes straight into paging: on 5 Oct evening three such compiles got
# about 2 pct of a core each and every one ran out its 600 s limit (memory a_lean_compile_commits_4gb_and_one_ran_to_38gb).
# The start floor keeps that from happening without stopping running work any sooner than before.
START_FLOOR_MB = int(os.environ.get('LEAN_START_FLOOR_MB') or 5500)


def validate_state(state, now=None, floor=MEM_FLOOR_MB):
    """Admit one compiler process unless the machine is in crash territory. Until 30 Sep this also held at CPU over
    85 pct and under 8 GB free, stricter than his 9 Sep order that guards act only on crash-shaped signals; with the
    films rendering the laptop sits at 75-90 pct CPU, so Lean was refused most of the day. The compiler runs one
    thread at BelowNormal (lean_process), so CPU load is not a reason to refuse it."""
    if not isinstance(state, dict):
        raise RuntimeError('Compute telemetry is not an object')
    if state.get('status') not in {'ok', 'warn', 'throttle'}:
        raise RuntimeError('Compute status is distressed or unknown')
    try:
        stamp = dt.datetime.strptime(state['ts'], '%Y-%m-%d %H:%M:%S')
        age = ((now or dt.datetime.now()) - stamp).total_seconds()
    except (ValueError, TypeError, KeyError) as exc:
        raise RuntimeError('Compute timestamp is missing or malformed') from exc
    if not 0 <= age <= 600:
        raise RuntimeError('Compute telemetry is stale or future-dated')
    for field in ('mem_avail_mb', 'commit_avail_mb'):
        value = state.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise RuntimeError('Compute metric is missing, nonfinite or invalid: ' + field)
    hung = state.get('hung_windows')
    if isinstance(hung, bool) or not isinstance(hung, int) or hung < 0:
        raise RuntimeError('Hung-window count is missing or invalid')
    if hung != 0 or state['mem_avail_mb'] < floor or state['commit_avail_mb'] < floor:
        raise RuntimeError('Compute admission held (hung window or under %d MB free); no new compiler process' % floor)


def read_state(tries=5, wait=0.2):
    """The governor replaces compute_state.json while others read it, so a read can land on the swap and see a
    PermissionError or a half-written file (4 Oct: one Lean check of the hard test failed that way, reported as a
    missing runtime). Try a few times, 0.2 s apart, before calling the telemetry unreadable."""
    for attempt in range(tries):
        try:
            return json.loads(COMPUTE.read_text(encoding="utf-8-sig"))
        except (PermissionError, json.JSONDecodeError):
            if attempt + 1 == tries:
                raise
            time.sleep(wait)


def admission(start=False):
    """start=True before a compiler process is launched (START_FLOOR_MB); otherwise the running-compile poll (MEM_FLOOR_MB)."""
    validate_state(read_state(), floor=START_FLOOR_MB if start else MEM_FLOOR_MB)


def compiler_environment(project=PROJECT):
    project = Path(project).resolve(strict=True)
    raw = (project / 'lake-manifest.json').read_bytes()
    manifest = json.loads(raw)
    if manifest.get('packagesDir') != '.lake/packages':
        raise ValueError('Unsupported dependency layout')
    paths = [project / '.lake/build/lib/lean']
    revisions = {}
    for package in manifest['packages']:
        name = package['name']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name):
            raise ValueError('Unsupported dependency name')
        path = project / '.lake/packages' / name / '.lake/build/lib/lean'
        if path.is_dir():
            paths.append(path)
        elif name == 'mathlib':
            raise FileNotFoundError('Built Mathlib missing; automatic builds are disabled')
        revisions[name] = package.get('rev')
    if 'mathlib' not in revisions:
        raise FileNotFoundError('Mathlib is not in the dependency manifest')
    env = dict(os.environ)
    env.pop('LEAN_SYSROOT', None)
    env['LEAN_PATH'] = os.pathsep.join(str(p) for p in paths if p.is_dir())
    return env, {'manifest_sha256': digest(raw), 'declared_revisions': revisions,
                 'dependency_bytes_independently_verified': False}


def check_source(source, *, target=None, reviewed_sha256=None, timeout=120,
                 source_path=None, output_root=None, use_mathlib=True):
    if not isinstance(source, bytes):
        raise TypeError('Provide exact UTF-8 source bytes')
    text = source.decode('utf-8')
    source_hash = digest(source)
    if target is not None:
        validate_target(target)
    if reviewed_sha256 is not None and reviewed_sha256 != source_hash:
        raise ValueError('Review hash does not match submitted bytes; no process launched')
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 900:
        raise ValueError('Timeout must be positive and at most 900 seconds')
    admission(start=True)
    compiler = Path(LEAN).resolve(strict=True)
    compiler_hash = digest(compiler.read_bytes())
    if use_mathlib:
        env, environment = compiler_environment()
    else:
        env, environment = dict(os.environ), {}
        env.pop('LEAN_SYSROOT', None)
        env.pop('LEAN_PATH', None)
    # Explicit imports permit the environment inspection commands. This does
    # not modify the source file; unsupported prelude/module syntax fails closed.
    generated = ('import Lean.Elab.Command\nimport Lean.Util.CollectAxioms\nimport Lean.Data.Json.Printer\n' + text + (target_commands(target) if target else '')).encode('utf-8')
    folder = Path(output_root or os.environ.get('LEAN_CHECK_DIR') or
                  Path(tempfile.gettempdir()) / 'lean_check') / uuid.uuid4().hex
    folder.mkdir(parents=True, exist_ok=False)
    original, checked = folder / 'INPUT.lean', folder / 'Check.lean'
    original.write_bytes(source)
    checked.write_bytes(generated)
    started = time.monotonic()
    timed_out, code, output = False, None, ''

    def progress(event):
        if event['phase'] == 'waiting':
            admission()

    try:
        admission(start=True)
        completed = run([str(compiler), "-j1", str(checked)], cwd=folder, env=env,
                        timeout=timeout, progress=progress)
        code, output = completed.returncode, completed.stdout + completed.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        output = (exc.output or '') + (exc.stderr or '')
    except (OSError, RuntimeError, ValueError) as exc:
        output = 'Runtime unavailable: ' + str(exc)
    try:
        integrity = (original.read_bytes() == source and checked.read_bytes() == generated
                     and digest(compiler.read_bytes()) == compiler_hash
                     and (source_path is None or Path(source_path).read_bytes() == source))
        if use_mathlib:
            integrity = integrity and digest((Path(PROJECT) / 'lake-manifest.json').read_bytes()) == environment['manifest_sha256']
    except OSError:
        integrity = False
    result = classify(code, output, source_sha256=source_hash,
                      reviewed_sha256=reviewed_sha256, target=target,
                      timed_out=timed_out, evidence_integrity=integrity)
    return {'result': result, 'compiler_exit_code': code, 'compiler_output': output,
            'seconds': round(time.monotonic() - started, 3),
            'compiler_sha256': compiler_hash, 'generated_sha256': digest(generated),
            'environment': environment, 'evidence_integrity': integrity,
            'source_path': str(source_path) if source_path else None,
            'receipt_directory': str(folder)}


def run_file(path, timeout=120, target=None, reviewed_sha256=None):
    receipt = check_source(Path(path).read_bytes(), source_path=path, target=target,
                           reviewed_sha256=reviewed_sha256, timeout=timeout)
    return exit_code(receipt['result']), receipt['compiler_output'], receipt['seconds']


def check_stmt(stmt, imports=IMPORTS, target=None, reviewed_sha256=None, timeout=120):
    raw = (imports + '\n' + stmt.strip() + '\n').encode('utf-8')
    receipt = check_source(raw, target=target, reviewed_sha256=reviewed_sha256, timeout=timeout)
    return exit_code(receipt['result']), receipt['compiler_output'], receipt['seconds']


def selftest(timeout=60):
    """Three authored core-Lean controls; no Mathlib import or automatic builds.

    This explicitly reviews these fixed tiny source strings, not arbitrary input.
    Run only under the coordinator's serialized compiler admission.
    """
    cases = [('true', b'theorem CheckTrue : True := True.intro\n', 'CheckTrue', 'FORMAL_TARGET_VERIFIED'),
             ('false', b'theorem CheckFalse : False := by decide\n', 'CheckFalse', 'COMPILE_FAILED'),
             ('admitted', b'theorem CheckAdmitted : False := by sorry\n', 'CheckAdmitted', 'ADMITTED_TARGET')]
    passed = True
    for label, raw, target, expected in cases:
        receipt = check_source(raw, target=target, reviewed_sha256=digest(raw),
                               use_mathlib=False, timeout=timeout)
        actual = receipt['result']['status']
        print(label + ': ' + actual)
        passed = passed and actual == expected
    return 0 if passed else 1


def main(argv=None):
    # Lean's messages carry the statement's own symbols (ℝ, ≤, ⊢); on a cp1252 console printing the first failure
    # raised UnicodeEncodeError and the agent saw a traceback instead of the error (30 Sep)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--stmt')
    source.add_argument('--file', type=Path)
    source.add_argument('--selftest', action='store_true')
    parser.add_argument('--target')
    parser.add_argument('--reviewed-sha256')
    parser.add_argument('--full-mathlib', action='store_true')
    parser.add_argument('--core', action='store_true', help='Core Lean only; no Mathlib environment')
    parser.add_argument('--timeout', type=float, default=120)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    if args.core and args.full_mathlib:
        parser.error('--core and --full-mathlib are mutually exclusive')
    try:
        if args.selftest:
            return selftest(timeout=min(args.timeout, 60))
        imports = '' if args.core else ('import Mathlib\n' if args.full_mathlib else IMPORTS)
        raw = args.file.read_bytes() if args.file else (imports + '\n' + args.stmt.strip() + '\n').encode('utf-8')
        receipt = check_source(raw, source_path=args.file, target=args.target,
                               reviewed_sha256=args.reviewed_sha256, timeout=args.timeout,
                               use_mathlib=not args.core)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print('SETUP_UNVERIFIED:', str(exc))
        return 2
    if args.json:
        print(json.dumps(receipt, indent=2))
    else:
        print(receipt['result']['status'], 'in %.1fs' % receipt['seconds'])
        print('source_sha256:', receipt['result']['source_sha256'])
        print('Translation fidelity: UNVERIFIED')
        if receipt['compiler_output'].strip():
            print(receipt['compiler_output'].strip()[:4000])
    return exit_code(receipt['result'])


if __name__ == '__main__':
    raise SystemExit(main())
