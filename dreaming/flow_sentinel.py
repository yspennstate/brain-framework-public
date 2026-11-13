"""Read-only scheduled-flow audit. Only --output-dir receives writes; no scripts execute.

Reads the brain's Windows scheduled tasks (names matching FLOW_SENTINEL_TASKS, default ^Brain), follows the
literal script paths in each action to the logs those scripts write, and reports tasks whose last result is
nonzero, whose last run is older than three cadences, or whose newest log lines end on a failure. Log text is
reduced to a short category, so no private line is copied into the report. Paths matching the secret patterns
below, or FLOW_SENTINEL_FORBIDDEN (an extra regex for folders the audit must never open), are never read.
On another system, give a recorded task list with --tasks-file (and --now to replay it).
"""
from __future__ import annotations
import sys
sys.dont_write_bytecode = True
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import time

TASKS = os.environ.get('FLOW_SENTINEL_TASKS', '^Brain').replace("'", "''")
FIRST = tuple(x for x in os.environ.get('FLOW_SENTINEL_FIRST', '').split(',') if x)
PS = r"""$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.Encoding]::UTF8
@(Get-ScheduledTask | Where-Object {$_.TaskName -match '""" + TASKS + r"""'} | ForEach-Object {
$t=$_; $i=$t | Get-ScheduledTaskInfo
[pscustomobject]@{name=$t.TaskName;path=$t.TaskPath;state=[string]$t.State;
last_run=$(if($i.LastRunTime){$i.LastRunTime.ToString('o')});next_run=$(if($i.NextRunTime){$i.NextRunTime.ToString('o')});last_result=$i.LastTaskResult;
actions=@($t.Actions | Select-Object Execute,Arguments,WorkingDirectory);
triggers=@($t.Triggers | ForEach-Object {[pscustomobject]@{interval=$_.Repetition.Interval;days=$_.DaysInterval;weeks=$_.WeeksInterval;start=$_.StartBoundary;class=$_.CimClass.CimClassName;enabled=$_.Enabled}})
}}) | ConvertTo-Json -Depth 6 -Compress"""
FORBIDDEN = re.compile(r'(?i)([\\/]\.ssh[\\/]|browser|auth\.json|client_secret|rclone\.conf|[\\/]\.env(?:[./\\]|$)|\.(?:pem|key)$|[\\/]tokens?[./\\]|[\\/]cookies?[./\\]|[\\/]\.codex[^/\\]*[\\/].*profile)')
EXTRA = re.compile(os.environ['FLOW_SENTINEL_FORBIDDEN'], re.I) if os.environ.get('FLOW_SENTINEL_FORBIDDEN') else None
ABS = re.compile(r'''[A-Za-z]:[\\/][^\s"'<>|]*?\.(?:vbs|ps1|py|cmd|bat|log)(?:\.child)?''', re.I)
QUOTED = re.compile(r'''["']([^"'\r\n]+\.(?:vbs|ps1|py|cmd|bat|log)(?:\.child)?)["']''', re.I)

def allowed(path):
    return not FORBIDDEN.search(str(path)) and not (EXTRA and EXTRA.search(str(path)))

def task_snapshot(timeout=6):
    completed = subprocess.run(['powershell', '-NoProfile', '-Command', PS],
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), capture_output=True,
        timeout=timeout, encoding='utf-8', errors='replace')
    if completed.returncode:
        raise RuntimeError('scheduled_task_query_failed: ' + completed.stderr[:240])
    data = json.loads(completed.stdout.lstrip('\ufeff'))
    return data if isinstance(data, list) else [data]

def date(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        parsed = datetime.strptime(value, '%m/%d/%Y %H:%M:%S')
    return parsed.astimezone(timezone.utc)

def duration(value):
    match = re.fullmatch(r'P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?', value or '')
    if not match:
        return None
    days, hours, minutes, seconds = (float(x or 0) for x in match.groups())
    return days*86400 + hours*3600 + minutes*60 + seconds or None

def interval(task):
    values = []
    for trigger in task.get('triggers', []):
        if trigger.get('enabled') is False:
            continue
        value = duration(trigger.get('interval'))
        days = trigger.get('days', trigger.get('DaysInterval'))
        weeks = trigger.get('weeks', trigger.get('WeeksInterval'))
        if value:
            values.append(value)
        elif days:
            values.append(int(days)*86400)
        elif weeks:
            values.append(int(weeks)*604800)
    # Multiple daily start times can make the cadence shorter than a day.
    starts = sorted({date(t.get('start')).hour*3600 + date(t.get('start')).minute*60
                     for t in task.get('triggers', [])
                     if t.get('days') == 1 and t.get('start') and t.get('enabled') is not False})
    if len(starts) > 1:
        values.append(max(b-a for a,b in zip(starts, starts[1:]+[starts[0]+86400])))
    return min(values) if values else None

def references(text, parent):
    found = set()
    for raw in ABS.findall(text):
        found.add(Path(raw.replace('\\\\', '\\')))
    for raw in QUOTED.findall(text):
        raw = raw.replace('\\\\', '\\')
        if ':' in raw and re.match(r'^[A-Za-z]:[\\/]',raw) and not re.search(r'\.(?:exe|py)\s+[-\w]',raw):
            found.add(Path(raw))
        elif not re.search(r'[\s$%{}*]',raw):
            found.add(parent / raw)
    return sorted((p for p in found if allowed(p)), key=str)

def launcher_references(text, parent):
    code = '\n'.join(line for line in text.splitlines()
                     if not line.lstrip().startswith(("'", '#', 'REM ')))
    # Expand only the current user's home literal, never arbitrary environment secrets.
    code = re.sub(r'(?i)["\']\s*&\s*home\s*&\s*["\']',str(Path.home()).replace('\\','/'),code)
    refs = references(code,parent)
    refs.extend(parent/filename for filename in re.findall(r'(?<![\\/\w])([\w.-]+\.py)\b',code))
    refs.extend(parent/filename for filename in re.findall(r'>>?\s*([\w.-]+\.log)\b',code,re.I))
    variables = {name.casefold():value for name,value in re.findall(
        r'(?m)^\s*\$(\w+)\s*=\s*["\']([A-Za-z]:[^"\']+)["\']',code)}
    for variable,literal in re.findall(r'Join-Path\s+\$(\w+)\s+["\']([^"\']+\.log)["\']',code,re.I):
        if variable.casefold() in variables:
            refs.append(Path(variables[variable.casefold()])/literal)
    return [p for p in refs if allowed(p)]

def discover(task, deadline, cache):
    """Follow literal script references, never execute or import their contents."""
    pending = []
    for action in task.get('actions', []):
        parent = Path(action.get('WorkingDirectory') or '.')
        pending.extend((p, 0) for p in references(
            (action.get('Execute') or '')+' '+(action.get('Arguments') or ''), parent))
    visited, logs, gaps = set(), set(), []
    while pending and time.monotonic() < deadline and len(visited) < 12:
        path, depth = pending.pop(0)
        if path in visited:
            continue
        visited.add(path)
        if '.log' in path.name.lower():
            if path.is_file():
                logs.add(path)
            else:
                alternatives = [path.parent.parent/path.name,path.parent/'logs'/path.name,
                                path.parent.parent/'logs'/path.name]
                found = next((p for p in alternatives if allowed(p) and p.is_file()),None)
                if found:
                    logs.add(found)
                else:
                    gaps.append('missing_log:' + str(path))
            continue
        if depth > 3:
            continue
        try:
            if path not in cache:
                with path.open('rb') as handle:
                    cache[path] = handle.read(256*1024).decode('utf-8-sig', 'replace')
            text = cache[path]
            parent = path.parent
            # Launchers often cd before invoking a relative child.
            directory = re.search(r'(?im)^\s*(?:sh\.CurrentDirectory|Set-Location)\s*=?\s*["\']([^"\']+)["\']',text)
            if directory:
                parent = Path(directory.group(1))
            # Comments and Python docstrings contain example commands, unrelated logs,
            # and old paths. Follow launcher code only; Python children contribute logs.
            if path.suffix.lower() == '.py':
                import ast
                try:
                    tree = ast.parse(text)
                    literals = [n.value for n in ast.walk(tree) if isinstance(n,ast.Constant)
                                and isinstance(n.value,str) and '\n' not in n.value]
                    refs = []
                    for literal in literals:
                        if re.search(r'\.log(?:\.child)?$',literal,re.I):
                            refs.extend(references('"'+literal+'"',parent))
                except SyntaxError:
                    refs = []
            else:
                refs = launcher_references(text,parent)
            pending.extend((p, depth+1) for p in refs if allowed(p))
        except OSError:
            gaps.append('unreadable_script:' + str(path))
    if pending:
        gaps.append('discovery_budget_exhausted')
    return sorted(logs, key=str), gaps

FAIL = re.compile(r'(?i)\b(refused|error|failed|failure|exception|traceback|RuntimeError|ValueError|PermissionError|BREACH|timeout|timed out|past its .*cap|hourly_runner_already_active)\b|exited [1-9]\d*|\b(?:rc|exit_code|returncode)\s*[=:]\s*[1-9]\d*')
SUCCESS = re.compile(r'(?i)\b(published|succeeded|success|healthy|completed successfully|uploaded|pushed)\b|\bdone:|push: nothing changed|cycle completed|verdict:\s*\*?\*?PASS\b|\bfailed\s+0\b|exited 0\b|\b(?:rc|exit_code|returncode)\s*(?:[=:]\s*)?0\b')

def signature(line):
    # Avoid copying private log text into machine artifacts: use a short category.
    exception = re.search(r'\b(?:RuntimeError|ValueError|PermissionError): ([\w_]+)', line)
    if exception:
        return exception.group(1)
    refused = re.search(r'REFUSED:\s*([^:]+)', line)
    if refused:
        value = refused.group(1).split(' near ')[0]
        if 'private label' in value:
            return 'private_labels_refused'
        value = re.sub(r'\d+', '#', value).strip()
        return value[:100]
    match = FAIL.search(line)
    return re.sub(r'\d+', '#', match.group(0).lower()) if match else None

def analyze_log(path):
    try:
        with Path(path).open('rb') as handle:
            handle.seek(0, 2)
            size = handle.tell()
            offset = max(0, size-65536)
            handle.seek(offset)
            text = handle.read(65536).decode('utf-8', 'replace')
        lines = text.splitlines()[1 if offset else 0:][-300:]
    except OSError as exc:
        return {'path': str(path), 'error': type(exc).__name__}
    failures = []
    last_failure = None
    for line in lines:
        # JSON booleans/null and counters named error/failed are healthy diagnostics.
        if re.search(r'(?i)["\'](?:error|failure|failed)["\']\s*:\s*(?:null|false|0)\b',line) and not re.search(r'(?i)REFUSED|Traceback|Exception',line):
            continue
        if SUCCESS.search(line) and not re.search(r'(?i)REFUSED|\bERROR:|\bBREACH:',line):
            failures = []
            last_failure = None
        elif FAIL.search(line):
            key = signature(line)
            failures.append(key)
            stamp = re.search(r'\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d', line)
            last_failure = stamp.group(0) if stamp else None
    count = Counter(failures)
    key = failures[-1] if failures else None
    repeats = count[key] if key else 0
    return {'path': str(path), 'mtime': datetime.fromtimestamp(Path(path).stat().st_mtime, timezone.utc).isoformat(),
            'failure': failures[-1] if failures else None, 'last_failure_at': last_failure,
            'repeating_failure': key if repeats >= 3 else None, 'repeats': repeats}

def audit(tasks, now, deadline, inspect_logs=True):
    findings, coverage, cache, log_cache = [], [], {}, {}
    ordered = sorted(tasks, key=lambda t: (t['name'] not in FIRST,
                                          t.get('last_result') in (0,None), t.get('state')=='Disabled'))
    for task in ordered:
        name = task['name']
        state = task.get('state', '')
        result = task.get('last_result')
        cadence = interval(task)
        last = date(task.get('last_run'))
        item = {'name': name, 'state': state, 'last_result': result,
                'last_run': task.get('last_run'), 'interval_seconds': cadence}
        # Record all nonzero values; running/disabled status is context, not proof of failure.
        if result not in (0, None):
            findings.append({**item, 'kind': 'nonzero_result',
                'classification': 'running_status' if state == 'Running' else
                                  'disabled_status' if state == 'Disabled' else 'failure'})
        if cadence and last and (now-last).total_seconds() > cadence*3:
            findings.append({**item, 'kind': 'stale_run', 'age_seconds': round((now-last).total_seconds()),
                             'classification': 'disabled_status' if state == 'Disabled' else 'failure'})
        logs, gaps = discover(task, min(deadline,time.monotonic()+0.4), cache) if inspect_logs else ([], [])
        for path in logs:
            if time.monotonic() >= deadline:
                gaps.append('log_budget_exhausted')
                break
            if path not in log_cache:
                log_cache[path] = analyze_log(path)
            evidence = log_cache[path]
            if evidence.get('failure'):
                findings.append({**item, 'kind': 'log_failure', 'classification':
                    'disabled_status' if state == 'Disabled' else 'failure', **evidence})
        coverage.append({'name': name, 'logs': [str(p) for p in logs], 'gaps': gaps,
                         'interval_known': cadence is not None})
    return findings, coverage

def hook_lines(report):
    failed = sorted({f['name'] for f in report['findings'] if f['classification']=='failure'})
    repeats = sum(bool(f.get('repeating_failure')) for f in report['findings'])
    gaps = sum(not c['logs'] or bool(c['gaps']) for c in report['coverage'])
    return [f"flow_sentinel: {len(report['tasks'])} tasks; {len(failed)} flows need review; {report['elapsed_seconds']:.2f}s",
            'flows: ' + (', '.join(failed) or 'none'),
            f"logs: {repeats} repeating failures; {gaps} flows with coverage gaps; sentinel.json has evidence"]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--tasks-file', type=Path)
    parser.add_argument('--no-logs', action='store_true')
    parser.add_argument('--now', help='ISO timestamp for replaying recorded snapshots only')
    args = parser.parse_args()
    start = time.monotonic()
    if args.now and not args.tasks_file:
        parser.error('--now requires --tasks-file')
    now = date(args.now) if args.now else datetime.now(timezone.utc)
    error = None
    try:
        tasks = json.loads(args.tasks_file.read_text(encoding='utf-8-sig')) if args.tasks_file else task_snapshot()
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        tasks, error = [], str(exc)
    findings, coverage = audit(tasks, now, start+16, not args.no_logs)
    report = {'observed_at': datetime.now(timezone.utc).isoformat(), 'task_evaluation_at': now.isoformat(),
              'task_source': str(args.tasks_file) if args.tasks_file else 'live_powershell',
              'elapsed_seconds': round(time.monotonic()-start, 3),
              'query_error': error, 'tasks': tasks, 'findings': findings, 'coverage': coverage}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir/'sentinel.json').write_text(json.dumps(report, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    lines = hook_lines(report)
    if error:
        lines[0] = f"flow_sentinel: task query unavailable; {report['elapsed_seconds']:.2f}s"
        lines[1] = 'flows: task query unavailable; see sentinel.json'
    print('\n'.join(lines))
    return 2 if error else 0

if __name__ == '__main__':
    raise SystemExit(main())
