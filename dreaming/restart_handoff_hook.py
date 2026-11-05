"""Read-only, windowless reminder for an explicit owner-requested restart.
It never resumes jobs, changes trading state, writes receipts or replaces the brain wake.
"""
from pathlib import Path
import hashlib,json,re,sys
POINTER=Path.home()/'.codex/ACTIVE_RESTART_HANDOFF.json'

def render(payload,pointer,home=None):
    home=Path.home() if home is None else Path(home)
    if not isinstance(pointer,dict) or pointer.get('status')!='READY_FOR_RESTART':return ''
    old=pointer.get('source_thread_id')
    if old and any(payload.get(k)==old for k in ('session_id','thread_id','conversation_id')):return ''
    try:
        p=Path(pointer['handoff_path']).resolve(strict=True)
        downloads=(home/'Downloads').resolve()
        rel=p.relative_to(downloads)
        if len(rel.parts)<2 or not rel.parts[0].startswith('Codex Restart Handoff '):raise ValueError('unexpected handoff location')
        expected=pointer.get('handoff_sha256','')
        if not re.fullmatch('[0-9a-f]{64}',expected):raise ValueError('invalid hash pin')
        if p.stat().st_size>2*1024*1024:raise ValueError('handoff exceeds bounded read')
        if hashlib.sha256(p.read_bytes()).hexdigest()!=expected:raise ValueError('handoff hash mismatch')
    except Exception as exc:
        return '[restart-handoff] OWNER ALERT: restart handoff integrity is unverified ('+str(exc)+'). Reconcile the saved checkpoint before relying on it.'
    return ('[restart-handoff] The owner explicitly paused this work to restart/sign into Codex. '
      'Run codex_wake.py for this exact new thread, then read the COMPLETE hash-verified handoff at '+str(p)+'. ' +
      ('Also read brain.py node codex_restart_handoff_2026_09_09, including every attached part. ' if pointer.get('brain_node_registered') else 'Shared-memory index registration is pending behind a live peer claim; the full canonical brain-repository copy and anchor draft are preserved at ~/brain-repo/12_cognitive_architecture/audits/evidence/2026/09/09/codex-restart-handoff. Register and attach them when the index claim is available. ') +
      'It contains all six subagent reviews, installed changes, pending patches, exact paths and next steps. '
      'Brain self-care remains DEGRADED/UNVERIFIED; do not convert pending tests or partial movie audits into passes. '
      'The rebalancing bot was stopped by the owner: verify its hold and never restart it from a stale readiness instruction. '
      'Rejoin the mesh, reacquire claims and read fresh compute/process state before continuing. '
      'Do not duplicate movie supervisors, simulations or workers. After reading fully, mark ACTIVE_RESTART_HANDOFF.json RESUMED under a mesh claim so this reminder falls silent.')

def main():
    try:payload=json.loads(sys.stdin.read() or '{}')
    except Exception:payload={}
    if not isinstance(payload,dict):payload={}
    try:pointer=json.loads(POINTER.read_text(encoding='utf-8'))
    except FileNotFoundError:return
    except Exception:
        print('[restart-handoff] OWNER ALERT: restart pointer unreadable; saved handoff remains unverified.');return
    result=render(payload,pointer)
    if result:print(result)

if __name__=='__main__':main()
