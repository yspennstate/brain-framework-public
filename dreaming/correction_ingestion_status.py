"""Pure provenance routing and bounded correction-ingestion evidence checks.

This module does not authenticate a human, ingest prompts, access a database,
or decide mathematical truth. An explicit ledger origin is a recorded claim;
generated-message metadata wins over it. Missing/invalid origin stays unknown.
The receipt describes selected occurrences only, never whole-history health.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

SCHEMA = 'correction-ingestion-check/v1'
HEX = re.compile(r'^[0-9a-f]{64}$')
GENERATED_FLAGS = ('isMeta', 'isCompactSummary', 'isVisibleInTranscriptOnly')


def recorded_origin(record):
    """Conservative routing; deliberately not a human-authenticity assertion."""
    if not isinstance(record, dict):
        return 'unknown'
    if any(record.get(k) is True for k in GENERATED_FLAGS):
        return 'automation'
    if record.get('origin') in ('automation', 'system'):
        return 'automation'
    if any(k in record and type(record[k]) is not bool for k in GENERATED_FLAGS):
        return 'unknown'
    return 'owner' if record.get('origin') == 'owner' else 'unknown'


def routed_class(record, taxonomy):
    origin = recorded_origin(record)
    if origin != 'owner':
        return origin
    proposed = record.get('class_model') or record.get('class_provisional') or 'other'
    return proposed if proposed in taxonomy else 'other'


def explicit_correction_candidate(record):
    """Correction presence is separate from an action-class label; never override origin."""
    return (recorded_origin(record) == 'owner' and
            (record.get('correction_present') is True or record.get('class_model') == 'mistake'))


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
                      allow_nan=False).encode('ascii')


def _hash(value):
    if not isinstance(value, str) or not HEX.fullmatch(value):
        raise ValueError('invalid SHA256')


def validate_receipt(receipt, *, now=None):
    """Validate structure/consistency only. Self-declared hashes are not authenticity.

    All rows must bind native occurrence identity and immutable source text hash.
    A resolved correction additionally needs evidence hashes for candidate and
    explicit disposition. Unchecked runtimes remain visible even if every selected
    row is resolved. A supplied receipt cannot assert global health.
    """
    keys={'schema','checked_at','scope','coverage','source_review','rows','digest_sha256'}
    if not isinstance(receipt, dict) or set(receipt)!=keys or receipt['schema']!=SCHEMA:
        raise ValueError('unsupported receipt schema')
    if receipt['scope']!='selected-occurrences-only' or receipt['source_review']!='coordinator-identified-human-events':
        raise ValueError('unsupported scope/source review')
    coverage=receipt['coverage']
    if not isinstance(coverage,dict) or set(coverage)!={'codex','claude'}:
        raise ValueError('runtime coverage required')
    if coverage!={'codex':'selected-only','claude':'not-checked'}:
        raise ValueError('global coverage cannot be asserted by this profile')
    try:
        stamp=datetime.fromisoformat(receipt['checked_at'].replace('Z','+00:00'))
        if stamp.tzinfo is None: raise ValueError('naive timestamp')
        age=((now or datetime.now(timezone.utc))-stamp).total_seconds()
    except (TypeError,AttributeError,OverflowError) as e:
        raise ValueError('invalid timestamp') from e
    if age < -300 or age > 108000:
        raise ValueError('receipt stale or future')
    rows=receipt['rows']
    if not isinstance(rows,list) or not 1<=len(rows)<=40:
        raise ValueError('bounded nonempty rows required')
    seen=set(); unresolved=0
    rowkeys={'source_id','source_line','source_record_sha256','text_sha256','legacy_ledger',
             'framework_ingress','correction_present','candidate_sha256','disposition','disposition_sha256'}
    for row in rows:
        if not isinstance(row,dict) or set(row)!=rowkeys: raise ValueError('unsupported row')
        sid=row['source_id']; line=row['source_line']
        if not isinstance(sid,str) or not sid.startswith('codex-rollout-v2:') or len(sid)>200:
            raise ValueError('invalid source identity')
        if type(line) is not int or not 1<=line<=10000000: raise ValueError('invalid source line')
        if (sid,line) in seen: raise ValueError('duplicate occurrence')
        seen.add((sid,line))
        for k in ('source_record_sha256','text_sha256'): _hash(row[k])
        for k in ('legacy_ledger','framework_ingress'):
            if row[k] not in ('matched','missing','not-checked'): raise ValueError('invalid ingress status')
        if row['correction_present'] not in ('yes','no','unresolved'):
            raise ValueError('correction presence is distinct from action class')
        for k in ('candidate_sha256','disposition_sha256'):
            if row[k] is not None: _hash(row[k])
        d=row['disposition']
        if d not in ('not-reviewed','pending','repair-in-progress','resolved','not-a-correction'):
            raise ValueError('invalid disposition')
        if d=='resolved' and (row['correction_present']!='yes' or
            row['framework_ingress']!='matched' or not row['candidate_sha256'] or not row['disposition_sha256']):
            raise ValueError('resolved correction requires bound ingress/candidate/disposition')
        if d=='not-a-correction' and (row['correction_present']!='no' or not row['disposition_sha256']):
            raise ValueError('negative review requires disposition evidence')
        if d not in ('resolved','not-a-correction') or row['framework_ingress']!='matched': unresolved+=1
    _hash(receipt['digest_sha256'])
    payload={k:v for k,v in receipt.items() if k!='digest_sha256'}
    if hashlib.sha256(canonical_bytes(payload)).hexdigest()!=receipt['digest_sha256']:
        raise ValueError('receipt digest mismatch')
    return {'selected':len(rows),'unresolved':unresolved,'legacy_missing':sum(r['legacy_ledger']=='missing' for r in rows),
            'framework_missing':sum(r['framework_ingress']=='missing' for r in rows),
            'overall_health':'UNVERIFIED','authenticity':'NOT_ESTABLISHED_BY_HASHES',
            'coverage':'selected Codex occurrences only; Claude and remaining history not checked'}


def summarize_receipt(receipt, *, now=None):
    try:
        s=validate_receipt(receipt,now=now)
    except (ValueError,TypeError,OverflowError,RecursionError):
        return 'Correction ingestion: UNVERIFIED (invalid, missing, stale, or inconsistent bounded receipt).'
    return ('Correction ingestion: BOUNDED CHECK, OVERALL UNVERIFIED; %(selected)d selected, '
            '%(unresolved)d unresolved, %(legacy_missing)d missing from legacy ledger, '
            '%(framework_missing)d missing from framework; %(coverage)s.' % s)
