"""Candidate gate: complete-note consistency, not mathematical truth."""
from pathlib import Path
import time
from math_brain.current_note import read_current_note, NoteReadError


class SelectionUnavailable(RuntimeError):
    """A partial pool cannot establish the original rank-margin comparison."""


ON_REFUSAL = ('drop', 'first', 'all')


def consistent_hits(hits, database, *, deadline, reader=read_current_note,
                    clock=time.monotonic, diagnostics=None, on_refusal='drop'):
    """The hits whose complete current note reads back consistent, in their order.

    on_refusal says what a selected card the read refuses does to the whole
    selection. The caller ranked and gated the list as a whole - a margin
    against the runner-up, an exception granted to the first card, a test over
    the set - so a card dropped afterwards can leave a list no gate passed
    (scheduled audit account04/B04 r69, 29 Sep 2026):
      'drop'   the refused card is dropped and the rest kept, the behaviour
               before that audit; a refused first card moves the runner-up
               first under the first card's decision.
      'first'  a refused first card refuses the selection. A later one is
               dropped, which only widens the first card's margin; a caller
               that decided anything over the whole set decides it again.
      'all'    any refused card refuses the selection.
    """
    if on_refusal not in ON_REFUSAL:
        raise ValueError(f'on_refusal must be one of {ON_REFUSAL}')
    checked=[]
    first=True
    for hit in hits:
        if clock()>=deadline:
            raise SelectionUnavailable('integrity_budget_exhausted')
        node=hit.node
        if str(node.kind)=='evidence':
            continue
        metadata=node.metadata or {}
        declared=any(k in metadata for k in ('artifact_path','artifact_digest'))
        root=Path(database).parent/'objects' if declared else None
        try:
            full=reader(database,node.id,artifact_root=root)
        except NoteReadError as exc:
            reason=str(exc)
            if diagnostics is not None:
                diagnostics.append({'id':node.id,'status':'REFUSED','reason':reason})
            if reason in {'read_failed_or_inconsistent','unsupported_store_schema'}:
                raise SelectionUnavailable(reason) from exc
            if on_refusal=='all' or (on_refusal=='first' and first):
                raise SelectionUnavailable('selected_card_refused:'+reason) from exc
            first=False
            continue
        first=False
        if clock()>=deadline:
            raise SelectionUnavailable('integrity_budget_exhausted')
        if (full['current_id']!=node.id or
                full['computed_node_digest']!=node.digest()):
            raise SelectionUnavailable('candidate_changed_since_ranking')
        if not full.get('body_complete'):
            raise SelectionUnavailable('incomplete_note_contract')
        checked.append(hit)
        if diagnostics is not None:
            diagnostics.append({'id':node.id,'status':'CONSISTENT',
                                'artifact':full['artifact']['verification']})
    if clock()>=deadline:
        raise SelectionUnavailable('integrity_budget_exhausted')
    return checked
