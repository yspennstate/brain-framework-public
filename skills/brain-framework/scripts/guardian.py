#!/usr/bin/env python3
"""Independent POSIX/Codex Brain setup observer; protocol effects are not UI proof.

Installed outside the skill. No Brain imports occur before package integrity is
verified. install/refresh are explicit authorized connector operations; hook
checks read state and emit warnings only. This observer creates no admission,
corpus, credentials, service, reconnect action or maintenance authority.
"""
from __future__ import annotations
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import tempfile
import types

SCHEMA = 1
EVENTS = ('SessionStart','UserPromptSubmit','SubagentStart','PreToolUse','PostCompact')
MAX_INPUT = 2 * 1024 * 1024
MAX_JSON = 16 * 1024 * 1024
OK = 'BRAIN_GUARDIAN_CHECK_OK_V1'
FALLBACK = '{"systemMessage":"BRAIN ALARM [GUARDIAN_UNAVAILABLE]: The outside-skill guardian or its configured host Python cannot run. Repair the owned guardian before ordinary Brain-dependent work. This warning grants no new permissions."}'
DISCONNECT = ("/don't",'/dont','/brain off','/brainoff','/disconnect','/independent','[[brain:off]]')
IGNORED = {'__pycache__','.git','.DS_Store'}
ADMISSION_POLICY = {'required':'fresh actual session/workspace/selection admission',
                    'max_age_seconds':1800,'unchecked_is_warning':True,'receipt_is_not_delivery':True}

class GuardianError(RuntimeError): pass

def absolute(value): return Path(os.path.abspath(os.path.expanduser(str(value))))

def regular(path, limit=None):
    path=Path(path); info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise GuardianError('Not a regular owned file: '+str(path))
    if limit is not None and info.st_size > limit: raise GuardianError('Oversized state file: '+str(path))
    raw=path.read_bytes()
    if len(raw)!=info.st_size: raise GuardianError('File changed during inspection: '+str(path))
    return raw

def digest(path): return hashlib.sha256(regular(path)).hexdigest()

def read_json(path):
    value=json.loads(regular(path,MAX_JSON).decode('utf-8','strict'))
    if not isinstance(value,dict): raise GuardianError('Expected an object in '+str(path))
    return value

def atomic(path,value,mode=0o600):
    path=Path(path)
    if path.is_symlink(): raise GuardianError('Refusing an owned-file symlink: '+str(path))
    path.parent.mkdir(parents=True,exist_ok=True)
    raw=value if isinstance(value,bytes) else (json.dumps(value,indent=2,ensure_ascii=False)+'\n').encode()
    fd,name=tempfile.mkstemp(prefix='.guardian-',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream: stream.write(raw);stream.flush();os.fsync(stream.fileno())
        os.chmod(name,mode);os.replace(name,path)
    finally: Path(name).unlink(missing_ok=True)

def package_files(skill):
    result={}
    for p in sorted(Path(skill).rglob('*')):
        rel=p.relative_to(skill)
        if any(part in IGNORED for part in rel.parts) or p.suffix=='.pyc': continue
        if p.is_symlink(): raise GuardianError('Skill package contains a symlink: '+str(rel))
        if p.is_file(): result[rel.as_posix()]=digest(p)
    needed={'SKILL.md','scripts/connector.py','scripts/brain_binding.py','scripts/receiver_probe.py',
            'scripts/standing_activation.py','scripts/brainctl.py','references/upstream-lock.json'}
    if not needed <= set(result): raise GuardianError('Required skill files are missing')
    if not regular(Path(skill)/'SKILL.md',MAX_JSON).strip(): raise GuardianError('Installed skill is empty')
    return result

def _launcher(profile,home,python,script,script_sha,manifest,runtime_python):
    # Fixed absolute host paths; never search PATH for a replacement interpreter.
    # The inline loader handles exact payload session disconnect before loading
    # a missing/corrupt script. Only this stdlib loader is executed unconditionally.
    session_loader=r'''
import hashlib,io,json,os,pathlib,sys
script,expected,manifest,profile,home,event=sys.argv[1:]
raw=sys.stdin.buffer.read(2*1024*1024+1)
if len(raw)>2*1024*1024: raise RuntimeError("oversized hook payload")
data=json.loads(raw.decode("utf-8","strict") or "{}")
if not isinstance(data,dict): raise RuntimeError("hook input is not an object")
sid=str(data.get("session_id") or data.get("thread_id") or "")
safe=sid if sid and all(c.isalnum() or c in "-_" for c in sid) else ""
offline=pathlib.Path(home)/".claude/brain_offline"
prompt=str(data.get("prompt") or "").strip().lower()
tokens=("/don't","/dont","/brain off","/brainoff","/disconnect","/independent","[[brain:off]]")
off_prompt=event=="UserPromptSubmit" and any((t in prompt if t.startswith("[[") else prompt==t or prompt.startswith(t+" ") or prompt.startswith(t+"\n")) for t in tokens)
if pathlib.Path(profile,"brain-framework/DISCONNECTED").exists() or (offline/"ALL.flag").exists() or (safe and (offline/(safe+".flag")).exists()) or off_prompt:
 print("BRAIN_GUARDIAN_CHECK_OK_V1");sys.exit(0)
'''
    loader=session_loader+r'''
p=pathlib.Path(script)
code=p.read_bytes() if not p.is_symlink() and p.is_file() else b""
if not code or hashlib.sha256(code).hexdigest()!=expected:
 print(json.dumps({"systemMessage":"BRAIN ALARM [GUARDIAN_UNAVAILABLE]: The independent guardian script is missing or changed. Restore its owned files before ordinary Brain-dependent work."}));sys.exit(0)
sys.argv=[script,"check","--manifest",manifest,"--profile",profile,"--event",event]
sys.stdin=io.TextIOWrapper(io.BytesIO(raw),encoding="utf-8")
exec(compile(code,script,"exec"),{"__name__":"__main__","__file__":script,"__package__":None})
'''
    # The independent host remains the observer. The known Brain interpreter
    # may parse a disconnect only when that host is absent; it never substitutes
    # for the missing observer or guesses identity from inherited environment.
    q=shlex.quote
    validate='import json,sys; raw=sys.stdin.buffer.read(4097); d=json.loads(raw); assert len(raw)<=4096 and isinstance(d,dict) and set(d)=={"systemMessage"} and isinstance(d["systemMessage"],str); print("BRAIN_GUARDIAN_JSON_OK_V1")'
    return ("#!/bin/sh\n# Owned independent Brain guardian; POSIX only.\n"
        + 'if [ -f '+q(str(profile/'brain-framework/DISCONNECTED'))+' ] || [ -f '+q(str(home/'.claude/brain_offline/ALL.flag'))+' ]; then exit 0; fi\n'
        + 'if [ ! -x '+q(str(python))+' ]; then guardian_disconnect=$('+q(str(runtime_python))+' -I -S -B -c '+q(session_loader+'\nsys.exit(1)\n')+' '+q(str(script))+' '+q(script_sha)+' '+q(str(manifest))+' '+q(str(profile))+' '+q(str(home))+' "$1" 2>/dev/null); if [ "$?" -eq 0 ] && [ "$guardian_disconnect" = '+q(OK)+' ]; then exit 0; fi; printf \'%s\\n\' '+q(FALLBACK)+'; exit 0; fi\n'
        + 'out=$('+q(str(python))+' -I -S -B -c '+q(loader)+' '+q(str(script))+' '+q(script_sha)+' '+q(str(manifest))+' '+q(str(profile))+' '+q(str(home))+' "$1" 2>/dev/null)\n'
        + 'rc=$?\nif [ "$rc" -eq 0 ] && [ "$out" = '+q(OK)+' ]; then exit 0; fi\n'
        + 'if [ "$rc" -eq 0 ]; then case "$out" in \'{"systemMessage":\'*) checked=$(printf \'%s\' "$out" | '+q(str(python))+' -I -S -B -c '+q(validate)+' 2>/dev/null); if [ "$?" -eq 0 ] && [ "$checked" = BRAIN_GUARDIAN_JSON_OK_V1 ]; then printf \'%s\\n\' "$out"; exit 0; fi ;; esac; fi\n'
        + 'printf \'%s\\n\' '+q(FALLBACK)+'\nexit 0\n').encode()

def _native_groups(root):
    return {event:[{'matcher':'.*','hooks':[{'type':'command','command':shlex.join(['/bin/sh',str(root/'launch.sh'),event]),'timeout':30}]}] for event in EVENTS}

def install(profile,deployment,installed_skill):
    if os.name!='posix' or not os.access('/bin/sh',os.X_OK):
        raise GuardianError('Independent guardian supports a qualified POSIX /bin/sh host only; Windows is unqualified')
    if deployment.get('runtime')!='codex': raise GuardianError('This guardian requires Codex native hook contracts; other runtimes are unqualified')
    profile=absolute(profile);skill=absolute(installed_skill)
    if skill!=profile/'skills/brain-framework': raise GuardianError('Installed skill is outside the selected profile')
    if str(profile)!=deployment.get('profile'): raise GuardianError('Deployment/profile mismatch')
    package_files(skill)
    root=profile/'brain-framework/guardian'
    if root.is_symlink(): raise GuardianError('Guardian directory must not be a symlink')
    root.mkdir(parents=True,exist_ok=True)
    python=absolute(getattr(sys,'_base_executable',None) or sys.executable)
    if not python.is_file() or not os.access(python,os.X_OK): raise GuardianError('Actual install host/base Python is unavailable: '+str(python))
    probe=subprocess.run([str(python),'-I','-S','-B','-c','import json,hashlib;print("guardian-host-v1")'],capture_output=True,text=True,timeout=5)
    if probe.returncode or probe.stdout.strip()!='guardian-host-v1': raise GuardianError('Actual host/base Python failed its stdlib execution control')
    script=root/'guardian.py';launcher=root/'launch.sh';manifest=root/'manifest.json'
    raw=regular(Path(__file__))
    atomic(script,raw)
    atomic(launcher,_launcher(profile,Path(deployment['home']),python,script,hashlib.sha256(raw).hexdigest(),manifest,Path(deployment['python'])),0o700)
    groups=_native_groups(root)
    meta={'schema':SCHEMA,'groups':groups,'files':{str(script):digest(script),str(launcher):digest(launcher)},
          'manifest':str(manifest),'guardian_python':str(python),'root':str(root),
          'qualification':'Current POSIX host execution only; native UI delivery and Windows are unqualified',
          'observability_boundary':'Removing every observer/registration or /bin/sh cannot be detected by that removed observer.'}
    original=deployment.get('guardian');deployment['guardian']=meta
    try: refresh_manifest(profile,deployment,skill)
    except Exception:
        if original is None: deployment.pop('guardian',None)
        else: deployment['guardian']=original
        raise
    return meta

def refresh_manifest(profile,deployment,installed_skill):
    """Explicitly refresh skill/owned-group dependencies after authorized integration.

    Never marks current admission passed or repins a changed guardian executable.
    No full mutable config/manifest digest is placed in its own dependency hash.
    """
    profile=absolute(profile);skill=absolute(installed_skill);meta=deployment['guardian']
    if profile!=absolute(deployment['profile']) or skill!=profile/'skills/brain-framework': raise GuardianError('Manifest profile/skill mismatch')
    expected_root=profile/'brain-framework/guardian'
    if absolute(meta['manifest'])!=expected_root/'manifest.json': raise GuardianError('Manifest is outside its owned guardian directory')
    if set(meta['files'])!={str(expected_root/'guardian.py'),str(expected_root/'launch.sh')} or meta['groups']!=_native_groups(expected_root):
        raise GuardianError('Guardian executable/group ownership metadata is incomplete or changed')
    if any(absolute(p).parent!=expected_root or digest(p)!=h for p,h in meta['files'].items()): raise GuardianError('Guardian executables changed; inspect and reinstall explicitly')
    owned={event:list(groups) for event,groups in deployment.get('managed_hook_groups',{}).items()}
    for event,groups in meta['groups'].items():
        owned.setdefault(event,[])
        for group in groups:
            if group not in owned[event]: owned[event].append(group)
    fields=('id','profile','home','memory','source','source_revision','python','runtime','slug')
    doc={'schema':SCHEMA,'profile':str(profile),'revision':deployment['source_revision'],'skill':str(skill),
         'guardian_files':meta['files'],'guardian_python':meta['guardian_python'],
         'skill_files':package_files(skill),'deployment':{k:deployment[k] for k in fields},
         'runtime_manifest_sha256':hashlib.sha256(json.dumps(deployment['runtime_code'],sort_keys=True).encode()).hexdigest(),
         'required_groups':owned,'hook_config':deployment['hook_config'],
         'admission_dependency':dict(ADMISSION_POLICY),
         'delivery':'Only emitted native protocol output; no owner-delivery or acknowledgement claim'}
    atomic(meta['manifest'],doc)
    return doc

def warning(code,detail):
    detail=' '.join(str(detail).split())[:1100]
    return {'systemMessage':'BRAIN ALARM ['+code+']: '+detail+' Ordinary work is unadmitted. Use only already-authorized diagnosis/repair; this warning grants no new permission.'}

def _load_modules(skill,expected):
    # All bytes were independently checked before this function. Compile source
    # explicitly so an unpinned cached .pyc is never an authority for these modules.
    loaded={}
    for name in ('connector','receiver_probe','brain_binding','standing_activation'):
        p=skill/'scripts'/(name+'.py');raw=regular(p)
        if hashlib.sha256(raw).hexdigest()!=expected['scripts/'+name+'.py']:
            raise GuardianError('Verified module changed before execution: '+name)
        m=types.ModuleType(name);m.__file__=str(p)
        sys.modules[name]=m;exec(compile(raw,str(p),'exec'),m.__dict__);loaded[name]=m
    return loaded

def inspect(profile,manifest,payload,event):
    profile=absolute(profile)
    if (profile/'brain-framework/DISCONNECTED').exists(): return None
    doc=read_json(manifest)
    if doc.get('schema')!=SCHEMA or doc.get('profile')!=str(profile): raise GuardianError('Guardian manifest schema/profile is missing or changed')
    if doc.get('revision')!=doc.get('deployment',{}).get('source_revision'): raise GuardianError('Guardian manifest source revision mismatch')
    if not isinstance(payload,dict): raise GuardianError('Hook payload must be an object')
    home=Path(doc['deployment']['home']);offline=home/'.claude/brain_offline'
    sid=str(payload.get('session_id') or payload.get('thread_id') or '')
    safe=sid if sid and all(c.isalnum() or c in '-_' for c in sid) else ''
    prompt=str(payload.get('prompt') or '').strip().lower()
    request_off=event=='UserPromptSubmit' and any((t in prompt if t.startswith('[[') else prompt==t or prompt.startswith(t+' ') or prompt.startswith(t+'\n')) for t in DISCONNECT)
    if (offline/'ALL.flag').exists() or (safe and (offline/(safe+'.flag')).exists()) or request_off: return None
    expected_root=profile/'brain-framework/guardian'
    if absolute(manifest)!=expected_root/'manifest.json': raise GuardianError('Guardian manifest moved outside its owned directory')
    if set(doc['guardian_files'])!={str(expected_root/'guardian.py'),str(expected_root/'launch.sh')}:
        raise GuardianError('Guardian executable manifest is incomplete')
    if doc.get('admission_dependency')!=ADMISSION_POLICY: raise GuardianError('Guardian admission dependency policy is missing or changed')
    for key,groups in _native_groups(expected_root).items():
        if any(group not in doc['required_groups'].get(key,[]) for group in groups): raise GuardianError('Guardian independent registration manifest is incomplete: '+key)
    for p,h in doc['guardian_files'].items():
        if absolute(p).parent!=expected_root or digest(p)!=h: raise GuardianError('Outside-skill guardian file is missing or changed: '+str(p))
    skill=Path(doc['skill'])
    if skill!=profile/'skills/brain-framework': raise GuardianError('Guardian manifest selects another skill/profile')
    expected=doc['skill_files'];actual=package_files(skill)
    if actual!=expected: raise GuardianError('Installed skill bytes/file set changed or disappeared; inspect and refresh only the authorized package')
    runtime_python=Path(doc['deployment']['python'])
    if not runtime_python.is_file() or not os.access(runtime_python,os.X_OK): raise GuardianError('Configured Brain runtime Python is unavailable: '+str(runtime_python))
    probe=subprocess.run([str(runtime_python),'-I','-S','-B','-c','import sys;print("brain-runtime-v1")'],capture_output=True,text=True,timeout=5)
    if probe.returncode or probe.stdout.strip()!='brain-runtime-v1': raise GuardianError('Configured Brain runtime Python failed its execution control')
    mods=_load_modules(skill,expected);c=mods['connector'];b=mods['brain_binding']
    active=c.active(profile)
    if any(active.get(k)!=v for k,v in doc['deployment'].items()): raise GuardianError('Active deployment identity/configuration changed; inspect migration and guardian binding')
    if c.source_lock()['revision']!=doc['revision']: raise GuardianError('Skill source revision differs from pinned guardian revision')
    if hashlib.sha256(json.dumps(active['runtime_code'],sort_keys=True).encode()).hexdigest()!=doc['runtime_manifest_sha256']: raise GuardianError('Runtime code manifest changed; inspect before refreshing')
    for key,groups in active.get('managed_hook_groups',{}).items():
        if any(group not in doc['required_groups'].get(key,[]) for group in groups): raise GuardianError('Owned registration manifest is incomplete: '+key)
    main=b.resolve(profile)
    for d in (active,) if main['id']==active['id'] and main['profile']==active['profile'] else (active,main):
        c.verify_source(d['source']);c.verify_runtime_code(d)
        expected={row['path'] for row in c.source_lock()['files']}
        observed={p.relative_to(d['source']).as_posix() for p in Path(d['source']).rglob('*') if p.is_file() or p.is_symlink()}
        if observed-expected-{'bootstrap/settings.hooks.generated.json','bootstrap/hooks.codex.generated.json'}: raise GuardianError('Original source has unexpected files')
    current=read_json(doc['hook_config']).get('hooks',{})
    for key,groups in doc['required_groups'].items():
        if any(group not in current.get(key,[]) for group in groups): raise GuardianError('Required owned hook registration is missing/modified: '+key)
    captured=io.StringIO()
    with contextlib.redirect_stdout(captured): mods['standing_activation'].manage('status',profile)
    if 'activation is enabled in' not in captured.getvalue(): raise GuardianError('Standing Brain activation is unavailable; restore its authorized instruction path')
    workspace=payload.get('cwd')
    if not sid or not isinstance(workspace,str) or not Path(workspace).is_dir():
        return warning('ADMISSION_UNPROVEN','A stable actual session/workspace is unavailable. Run fresh admission in the actual task before ordinary work.')
    try: b.require_hook_admission(profile,payload)
    except Exception as exc:
        # A failed/unchecked ledger is evidence of a dependency, never delivery.
        detail=str(exc)
        path=b.activation_path(profile,sid,workspace)
        try:
            receipt=read_json(path)
            opened=sum(len(row.get('applicable_setup',{}).get('open_required_checks',[])) for row in receipt.get('brains',[]))
            if not receipt.get('passed') and opened: detail=str(opened)+' applicable checks remain unproven; '+detail
        except Exception: pass
        command=shlex.join([str(runtime_python),str(skill/'scripts/brainctl.py'),'--profile',str(profile),'--workspace',workspace,'admit','--session',sid])
        return warning('ADMISSION_UNPROVEN',detail+' Run: '+command)
    return None

def main(argv=None):
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['check']);p.add_argument('--profile',required=True);p.add_argument('--manifest',required=True);p.add_argument('--event',required=True)
    args=p.parse_args(argv)
    try:
        raw=sys.stdin.buffer.read(MAX_INPUT+1)
        if len(raw)>MAX_INPUT: raise GuardianError('Hook input exceeds 2 MiB')
        payload=json.loads(raw.decode('utf-8','strict') or '{}')
        value=inspect(args.profile,args.manifest,payload,args.event)
    except Exception as exc: value=warning('GUARDIAN_DEPENDENCY_FAILED',str(exc))
    print(json.dumps(value,ensure_ascii=False) if value else OK)
    return 0

if __name__=='__main__': raise SystemExit(main())
