#!/usr/bin/env python3
"""Offline, snapshot-assisted checklist merge. Inputs must be quiescent copies.

Usage: python3 -s task_checklist_merge.py REFERENCE LOCAL OUTPUT --hook PATH
OUTPUT must not exist. No input store or live hook is modified.
"""
import argparse
import copy
import importlib.util
import json
from pathlib import Path
import re
import sys

ID = re.compile(r'^T-(\d{8})-(\d+)$')
STATUS = {'open', 'done', 'blocked', 'dropped'}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def stamp(event):
    value = event.get('at', event.get('ts'))
    if not isinstance(value, str) or not value:
        raise ValueError('event requires an at (hook format) or ts timestamp')
    return value.replace(' ', 'T')


def identity(entry):
    return tuple(entry.get(k, '') for k in ('asked_at', 'asked_words', 'agent', 'runtime', 'session'))


def read_store(path):
    path = Path(path)
    state = json.loads((path / 'state.json').read_text(encoding='utf-8'))
    entries = state.get('entries')
    if not isinstance(entries, dict):
        raise ValueError(f'{path}: state.entries must be an object')
    for tid, entry in entries.items():
        if not ID.fullmatch(tid) or entry.get('id') != tid:
            raise ValueError(f'{path}: invalid entry id {tid}')
        for key in ('asked_at', 'asked_words', 'agent', 'runtime', 'session', 'status',
                    'working_on', 'done_at', 'result', 'seen', 'seen_at', 'updates', 'last_activity'):
            if key not in entry:
                raise ValueError(f'{path}: {tid} lacks {key}')
        if entry['status'] not in STATUS:
            raise ValueError(f'{path}: unsupported status in {tid}')
    events = []
    for number, line in enumerate((path / 'ledger.jsonl').read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        event = json.loads(line)
        if not isinstance(event, dict) or not ID.fullmatch(str(event.get('id', ''))):
            raise ValueError(f'{path}: ledger line {number} has an invalid task id')
        stamp(event)
        if not isinstance(event.get('kind'), str):
            raise ValueError(f'{path}: ledger line {number} lacks kind')
        events.append(event)
    return state, events


def merge(reference, local):
    """Return state, lossless event union, audit. Reference retains collided IDs.

    Snapshot fields are chosen by their corresponding latest ledger operation;
    full snapshot text wins over ledger's deliberately truncated note. Equal-time
    disagreement is resolved in favor of reference and recorded in the audit.
    """
    rs, revents = copy.deepcopy(reference)
    ls, levents = copy.deepcopy(local)
    audit = {'local_id_map': {}, 'conflicts': [], 'warnings': [
        'Naive hook times use each machine clock; clocks must be synchronized.',
        'Legacy ledgers are lossy: both state.json snapshots are required.',
        'Equal timestamps use reference precedence; inspect conflicts before integration.']}
    reserved = set(rs['entries']) | set(ls['entries']) | {e['id'] for e in revents + levents}
    counters = {}
    for tid in reserved:
        day, n = ID.fullmatch(tid).groups()
        counters[day] = max(counters.get(day, 0), int(n))
    for state in (rs, ls):
        for day, n in state.get('seq', {}).items():
            counters[day] = max(counters.get(day, 0), int(n))
    identities = {}
    for tid, e in rs['entries'].items():
        identities.setdefault(identity(e), []).append(tid)
    remap = {}
    ref_history = {e['id'] for e in revents}
    ref_orphans = ref_history - set(rs['entries'])
    local_orphans = {e['id'] for e in levents} - set(ls['entries'])
    # An orphan is retained history, not enough information to reconstruct an ask.
    audit['reference_history_only_ids'] = sorted(ref_orphans)
    audit['local_history_only_ids'] = sorted(local_orphans)
    for tid in local_orphans & set(rs['entries']):
        adds = [{canonical(e) for e in events if e['id'] == tid and e['kind'] == 'add'}
                for events in (revents, levents)]
        if not (adds[0] & adds[1]):
            raise ValueError(f'cannot attribute local history-only collision {tid}; supply its recovered state')
    suppressed = set()
    for tid, entry in sorted(ls['entries'].items()):
        matches = identities.get(identity(entry), [])
        if tid in matches:
            target = tid
        elif len(matches) == 1:
            target = matches[0]
        elif tid in ref_orphans:
            adds = [{canonical(e) for e in events if e['id'] == tid and e['kind'] == 'add'}
                    for events in (revents, levents)]
            if adds[0] & adds[1]:
                # Reference already removed this shared task: do not resurrect stale state.
                suppressed.add(tid)
                target = tid
            elif not adds[0] or not adds[1]:
                raise ValueError(f'cannot attribute reference history-only collision {tid}')
            else:
                day = ID.fullmatch(tid).group(1)
                counters[day] += 1
                target = f'T-{day}-{counters[day]:03d}'
        elif tid in rs['entries']:
            day = ID.fullmatch(tid).group(1)
            counters[day] += 1
            target = f'T-{day}-{counters[day]:03d}'
        else:
            target = tid
        remap[tid] = target
        if tid != target:
            audit['local_id_map'][tid] = target
    ls['entries'] = {remap[k]: dict(v, id=remap[k]) for k, v in ls['entries'].items() if k not in suppressed}
    if len(ls['entries']) != len(remap) - len(suppressed):
        raise ValueError('ambiguous duplicate task identities in local snapshot')
    audit['suppressed_stale_entries'] = sorted(suppressed)
    for event in levents:
        event['id'] = remap.get(event['id'], event['id'])
    for session in ls.get('sessions', {}).values():
        if session.get('latest') in remap:
            session['latest'] = remap[session['latest']]

    # Index by id + timestamp, retaining distinct payloads (same-second events are common).
    union = {}
    for event in revents + levents:
        union.setdefault((event['id'], stamp(event)), {})[canonical(event)] = event
    events = [e for bucket in union.values() for e in bucket.values()]
    # Stable order within one clock tick: reference order, then local additions.
    events.sort(key=lambda e: stamp(e))
    indexes = []
    for source in (revents, levents):
        index = {}
        for pos, event in enumerate(source):
            index.setdefault(event['id'], []).append((stamp(event), pos, event['kind']))
        indexes.append(index)

    def clock(side, tid, kinds, fallback):
        times = [t for t, _, k in indexes[side].get(tid, []) if k in kinds]
        return max(times) if times else fallback.replace(' ', 'T')

    def said_clock(side, tid, entry):
        # A Stop without transcript words leaves last_said untouched in the hook.
        # Its newer event must not make an old transcript outrank newer actual words.
        said = entry.get('last_said') or {}
        at = said.get('at', entry['asked_at']).replace(' ', 'T')
        text = said.get('text', '')
        source = revents if side == 0 else levents
        matching = [stamp(e) for e in source if e['id'] == tid and e['kind'] == 'stop'
                    and text and e.get('note') == text[:200] and stamp(e)[:16] == at[:16]]
        return max(matching) if matching else at

    entries = copy.deepcopy(rs['entries'])
    groups = [({'status', 'done_at'}, STATUS),
              ({'seen', 'seen_at'}, {'seen', 'unseen', 'open'}),
              ({'working_on'}, {'working'}), ({'result'}, {'done'}),
              ({'last_said'}, {'stop'})]
    for tid, local_entry in ls['entries'].items():
        if tid not in entries:
            entries[tid] = copy.deepcopy(local_entry)
            continue
        ref_entry = entries[tid]
        for fields, kinds in groups:
            # No corresponding operation: only use ask time, not unrelated recent activity.
            a = clock(0, tid, kinds, ref_entry['asked_at'])
            b = clock(1, tid, kinds, local_entry['asked_at'])
            if fields == {'last_said'}:
                a = said_clock(0, tid, ref_entry)
                b = said_clock(1, tid, local_entry)
            if any(ref_entry.get(f) != local_entry.get(f) for f in fields):
                audit['conflicts'].append({'id': tid, 'fields': sorted(fields), 'at': a,
                                           'local_at': b,
                                           'resolution': 'local' if b > a else 'reference',
                                           'reference': {f: ref_entry.get(f) for f in sorted(fields)},
                                           'local': {f: local_entry.get(f) for f in sorted(fields)}})
            if b > a:
                for field in fields:
                    if field in local_entry:
                        ref_entry[field] = copy.deepcopy(local_entry[field])
                    else:
                        ref_entry.pop(field, None)
        updates = {canonical(u): u for u in ref_entry['updates'] + local_entry['updates']}
        ref_entry['updates'] = sorted(updates.values(), key=lambda u: (u.get('at', ''), canonical(u)))
        ref_entry['last_activity'] = max(ref_entry['last_activity'], local_entry['last_activity'])
        # Retain extension fields absent from reference.
        for field, value in local_entry.items():
            ref_entry.setdefault(field, copy.deepcopy(value))
    sessions = copy.deepcopy(rs.get('sessions', {}))
    for sid, data in ls.get('sessions', {}).items():
        target = sessions.setdefault(sid, {})
        for key, value in data.items():
            target.setdefault(key, value)
    # Rebuild session latest from original ask time, never from reallocated ID order.
    for sid in set(sessions) | {e['session'] for e in entries.values() if e['session']}:
        candidates = [e for e in entries.values() if e['session'] == sid]
        if candidates:
            sessions.setdefault(sid, {})['latest'] = max(candidates, key=lambda e: (e['asked_at'], e['id']))['id']
        elif sessions[sid].get('latest') not in entries:
            sessions[sid].pop('latest', None)
    state = copy.deepcopy(rs)
    state.update(entries=entries, sessions=sessions, seq=counters)
    audit.update(reference_events=len(revents), local_events=len(levents), merged_events=len(events),
                 entries=len(entries))
    return state, events, audit


def write_output(reference, local, output, hook):
    output = Path(output)
    if output.exists():
        raise ValueError('output must be a new directory; never overwrite a store')
    state, events, audit = merge(read_store(reference), read_store(local))
    spec = importlib.util.spec_from_file_location('checklist_view', hook)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.STORE = str(output.resolve())
    view = module.render(state)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'ledger.jsonl').write_text(''.join(canonical(e) + '\n' for e in events), encoding='utf-8')
    (output / 'state.json').write_text(json.dumps(state, ensure_ascii=False, sort_keys=True, indent=1) + '\n', encoding='utf-8')
    (output / 'CHECKLIST.md').write_text(view, encoding='utf-8')
    (output / 'MERGE_REPORT.json').write_text(json.dumps(audit, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('reference', 'local', 'output'):
        parser.add_argument(name, type=Path)
    parser.add_argument('--hook', type=Path, required=True, help='trusted task_checklist_hook.py used only for rendering')
    args = parser.parse_args()
    try:
        print(json.dumps(write_output(args.reference, args.local, args.output, args.hook), sort_keys=True))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.exit(1, f'checklist merge: {exc}\n')


if __name__ == '__main__':
    main()
