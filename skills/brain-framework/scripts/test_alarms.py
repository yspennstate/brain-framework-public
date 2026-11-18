"""Retained behavioral alarm controls for parent review/integration.

Runs real cold bootstrap and copied CLI hosts, entirely under the explicit fixture
root. Synthetic fault hosts are declared in the complete runtime manifest. No
model, network install, native UI, service or live private corpus is involved.
Protocol exit 0 proves JSON transport, never handler success or UI delivery.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

OWN_ROOT = not os.environ.get('ALARM_TEST_ROOT')  # a root the caller names is kept; one made here is removed
ROOT = Path(os.environ.get('ALARM_TEST_ROOT') or tempfile.mkdtemp(prefix='brain-alarm-controls-')).resolve()
SKILL = Path(os.environ.get('ALARM_TEST_SKILL', Path(__file__).absolute().parent.parent)).resolve()
ROOT.mkdir(parents=True, exist_ok=True)
TEMP = ROOT / 'temp'; TEMP.mkdir(exist_ok=True)
os.environ['TMPDIR'] = str(TEMP)
tempfile.tempdir = str(TEMP)
sys.dont_write_bytecode = True
sys.path.insert(0, str(SKILL / 'scripts'))
import connector as c
import brain_binding as binding
import guardian

RUN = ROOT / ('retained-run-' + str(time.time_ns()))
RUN.mkdir()
RESULTS = []

def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value, indent=2))

def replace(value, old, new):
    if isinstance(value, str): return value.replace(old, new)
    if isinstance(value, list): return [replace(v, old, new) for v in value]
    if isinstance(value, dict): return {k: replace(v, old, new) for k,v in value.items()}
    return value

class AlarmControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = RUN / 'cold-base' / 'profile with spaces λ'
        cls.base.mkdir(parents=True)
        cls.deployment = c.create_deployment(cls.base, dependencies=False)
        cls.deployment = c.activate(cls.base, cls.deployment)
        binding.bind_main(cls.base, 'SYNTHETIC isolated alarm qualification; not the live owner brain', apply=True)
        # Independently validate the positive fixture before any fault injection.
        cls.source_proof = c.verify_source(cls.deployment['source'])
        cls.code_proof = c.verify_runtime_code(cls.deployment)
        assert (cls.base/'skills/brain-framework/SKILL.md').is_file()
        write(RUN/'base-proof.json', {'source':cls.source_proof,'runtime':cls.code_proof,
              'synthetic_fixture':True,'native_ui_delivery':'UNTESTED'})

    def setUp(self):
        self.folder = RUN/self._testMethodName
        self.profile = self.folder/'profile with spaces λ'
        shutil.copytree(self.base, self.profile, symlinks=True)
        old,new = str(self.base),str(self.profile)
        d = replace(self.deployment,old,new)
        self.d = d
        self.save_deployment()
        write(self.profile/'brain-framework/active.json',{'deployment':str(Path(d['home']).parent/'deployment.json')})
        reg=replace(json.loads((self.base/'brain-framework/brain-bindings.json').read_text()),old,new)
        write(self.profile/'brain-framework/brain-bindings.json',reg)
        write(self.profile/'hooks.json',replace(json.loads((self.base/'hooks.json').read_text()),old,new))
        for name in ['AGENTS.md','AGENTS.override.md','config.toml']:
            path=self.profile/name
            if path.exists(): write(path,path.read_text().replace(old,new))
        guardian.install(self.profile,self.d,self.profile/'skills/brain-framework')
        self.save_deployment()
        guardian.refresh_manifest(self.profile,self.d,self.profile/'skills/brain-framework')
        self.host = Path(d['home'])/'.claude/dreaming/synthetic_alarm_host.py'
        self.marker = self.host.parent/'synthetic-effect.txt'

    def save_deployment(self): write(Path(self.d['home']).parent/'deployment.json',self.d)

    def host_spec(self,body='pass',event='PreToolUse'):
        write(self.host, "# SYNTHETIC FAULT/ALLOWED HOST, not Brain health evidence\nfrom pathlib import Path\nPath('synthetic-effect.txt').write_text('executed')\n"+body+'\n')
        self.d['runtime_code']={str(p.relative_to(self.d['home'])):c.sha(p) for p in Path(self.d['home']).rglob('*.py')}
        key=event+'-alarm-test'
        self.d['handlers'][key]={'event':event,'argv':[self.d['python'],str(self.host)],'timeout':2}
        self.save_deployment()
        guardian.refresh_manifest(self.profile,self.d,self.profile/'skills/brain-framework')
        c.verify_source(self.d['source']);c.verify_runtime_code(self.d)
        return key

    def invoke(self,key=None,event='PreToolUse',payload=None,raw=None,command=None):
        data=payload or {'hook_event_name':event,'session_id':'alarm-test-session','cwd':str(self.folder),'last_assistant_message':'Here is the requested result.'}
        cmd=command or [sys.executable,'-B',str(self.profile/'skills/brain-framework/scripts/hook_bridge.py'),'--profile',str(self.profile),'--handler',key or event+'-alarm-test']
        env={k:v for k,v in os.environ.items() if k not in {'PYTHONPATH','PYTHONHOME'}}
        env.update({'HOME':str(self.folder/'outer-home'),'USERPROFILE':str(self.folder/'outer-home'),'CODEX_HOME':str(self.profile),'PYTHONDONTWRITEBYTECODE':'1','BRAIN_SELFCARE_DESKTOP_ALERT':'0'})
        env['CODEX_THREAD_ID']=data['session_id']  # real host supplies this; do not inherit the evaluator's session
        p=subprocess.run(cmd,input=json.dumps(data) if raw is None else raw,capture_output=True,text=True,env=env,timeout=20)
        rows=[];log=Path(self.d['home']).parent/'hook-delivery.jsonl'
        if log.exists(): rows=[json.loads(s) for s in log.read_text().splitlines()]
        result={'case':self._testMethodName,'argv':cmd,'payload':data if raw is None else None,
                'raw_input_bytes':len((raw if raw is not None else json.dumps(data)).encode()),
                'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr,'host_effect':self.marker.exists(),
                'journal':rows,'synthetic_fixture':True,'native_ui_delivery':'UNTESTED'}
        RESULTS.append(result);write(self.folder/('result-'+str(len(RESULTS))+'.json'),result)
        return p,rows

    def alarm(self,p,code,event='PreToolUse'):
        self.assertEqual(p.returncode,0,'protocol must deliver the failure payload')
        self.assertNotIn('Traceback',p.stderr)
        out=json.loads(p.stdout)
        self.assertIn(code,out['systemMessage'])
        if event=='PreToolUse': self.assertEqual(out['hookSpecificOutput']['permissionDecision'],'deny')
        if event=='Stop': self.assertIs(out['continue'],False)
        return out

    def test_allowed_complete_synthetic_host_executes(self):
        self.host_spec("print('SYNTHETIC ALLOWED CONTROL')")
        p,rows=self.invoke();self.assertEqual(p.returncode,0)
        self.assertNotIn('systemMessage',json.loads(p.stdout));self.assertTrue(self.marker.exists())
        self.assertEqual(rows[-1]['rc'],0)

    def test_profile_disconnect_is_intentionally_silent(self):
        self.host_spec();write(self.profile/'brain-framework/DISCONNECTED','explicit test disconnect')
        p,rows=self.invoke();self.assertEqual((p.returncode,p.stdout,p.stderr),(0,'',''));self.assertFalse(self.marker.exists())

    def setup_fault(self,mutate):
        self.host_spec();mutate();p,rows=self.invoke();self.alarm(p,'SETUP_OR_DELIVERY_FAILED');self.assertFalse(self.marker.exists())

    def test_missing_active(self): self.setup_fault(lambda:(self.profile/'brain-framework/active.json').unlink())
    def test_missing_binding(self): self.setup_fault(lambda:(self.profile/'brain-framework/brain-bindings.json').unlink())
    def test_corrupt_active_json(self): self.setup_fault(lambda:write(self.profile/'brain-framework/active.json','{BROKEN'))
    def test_corrupt_binding_shape(self): self.setup_fault(lambda:write(self.profile/'brain-framework/brain-bindings.json',{'schema':1,'profile':str(self.profile),'main':{},'projects':{}}))
    def test_missing_corpus_index(self): self.setup_fault(lambda:(Path(self.d['memory'])/'MEMORY.md').unlink())
    def test_missing_skill(self): self.setup_fault(lambda:(self.profile/'skills/brain-framework/SKILL.md').unlink())
    def test_truncated_skill(self): self.setup_fault(lambda:write(self.profile/'skills/brain-framework/SKILL.md',''))
    def test_missing_source_file(self): self.setup_fault(lambda:(Path(self.d['source'])/'docs/METHOD.md').unlink())
    def test_unexpected_source_file(self): self.setup_fault(lambda:write(Path(self.d['source'])/'unexpected.py','pass'))
    def test_tampered_runtime_prevents_effect(self): self.setup_fault(lambda:self.host.write_text(self.host.read_text()+'\n# corruption\n'))
    def test_tampered_journal_prevents_effect(self): self.setup_fault(lambda:write(Path(self.d['home'])/'.claude/dreaming/append_journal.py','raise RuntimeError("must not load")'))
    def test_extra_runtime_code_prevents_effect(self): self.setup_fault(lambda:write(Path(self.d['home'])/'unexpected.py','pass'))
    def test_undeclared_synthetic_host_is_rejected(self):
        self.host_spec();del self.d['runtime_code'][str(self.host.relative_to(self.d['home']))];self.save_deployment()
        p,_=self.invoke();self.alarm(p,'SETUP_OR_DELIVERY_FAILED');self.assertFalse(self.marker.exists())

    def test_missing_handler(self):
        self.host_spec();del self.d['handlers']['PreToolUse-alarm-test'];self.save_deployment()
        p,_=self.invoke();self.alarm(p,'SETUP_OR_DELIVERY_FAILED');self.assertFalse(self.marker.exists())

    def test_silent_child_failure_retains_original_rc(self):
        self.host_spec('raise SystemExit(7)');p,rows=self.invoke();self.alarm(p,'HANDLER_FAILED')
        self.assertIn('exit 7',json.loads(p.stdout)['systemMessage']);self.assertTrue(self.marker.exists());self.assertEqual(rows[-1]['rc'],7)

    def test_silent_allowed_child_is_not_an_alarm(self):
        self.host_spec('raise SystemExit(0)');p,rows=self.invoke();self.assertEqual((p.returncode,p.stdout,p.stderr),(0,'',''))
        self.assertTrue(self.marker.exists());self.assertEqual(rows[-1]['rc'],0)

    def test_stop_plain_warning_becomes_bounded_alarm(self):
        self.host_spec("print('SYNTHETIC BROKEN SETUP WARNING')",'Stop');p,rows=self.invoke(event='Stop')
        self.alarm(p,'INVALID_STOP_OUTPUT','Stop');self.assertTrue(self.marker.exists());self.assertLess(len(p.stdout),1200)

    def test_pretool_plain_context_survives(self):
        self.host_spec("print('SYNTHETIC BROKEN SETUP WARNING')");p,_=self.invoke()
        self.assertIn('SYNTHETIC BROKEN SETUP WARNING',json.loads(p.stdout)['hookSpecificOutput']['additionalContext'])
        self.assertNotIn('systemMessage',json.loads(p.stdout))

    def test_bad_payloads_alarm_before_host(self):
        self.host_spec()
        for raw in ['[]','{BROKEN','x'*(2*1024*1024+1)]:
            with self.subTest(bytes=len(raw)):
                p,_=self.invoke(raw=raw);self.alarm(p,'INVALID_HOOK_INPUT');self.assertFalse(self.marker.exists())

    def test_connector_import_failure_has_stdlib_alarm(self):
        self.host_spec();(self.profile/'skills/brain-framework/scripts/connector.py').unlink()
        p,_=self.invoke();self.alarm(p,'CONNECTOR_UNAVAILABLE');self.assertFalse(self.marker.exists())

    def test_invalid_invocation_emits_alarm_without_dispatch(self):
        self.host_spec()
        command=[sys.executable,'-B',str(self.profile/'skills/brain-framework/scripts/hook_bridge.py'),'--profile',str(self.profile),'--handler','PreToolUse-alarm-test','--invalid-option']
        p,_=self.invoke(command=command);self.alarm(p,'BRIDGE_FAILED');self.assertFalse(self.marker.exists())

    def test_cold_original_guard_missing_receipt_blocks_stop(self):
        key=next(k for k,v in self.d['handlers'].items() if v['event']=='Stop' and 'codex_stop_guard.py' in str(v))
        p,_=self.invoke(key=key,event='Stop');self.assertEqual(p.returncode,0)
        out=json.loads(p.stdout);self.assertEqual(out['decision'],'block');self.assertIn('brain_wake_receipt',out['reason'])

    def test_cold_original_guard_corrupt_receipt_requires_disclosure(self):
        write(Path(self.d['home'])/'.claude/brain_health/state.json','{BROKEN')
        key=next(k for k,v in self.d['handlers'].items() if v['event']=='Stop' and 'codex_stop_guard.py' in str(v))
        p,_=self.invoke(key=key,event='Stop');self.assertIn('WAKE_CORRUPT',json.loads(p.stdout)['systemMessage'])

    def test_cold_original_guard_honest_disclosure_is_allowed(self):
        write(Path(self.d['home'])/'.claude/brain_health/state.json','{BROKEN')
        key=next(k for k,v in self.d['handlers'].items() if v['event']=='Stop' and 'codex_stop_guard.py' in str(v))
        p,_=self.invoke(key=key,event='Stop',payload={'hook_event_name':'Stop','session_id':'alarm-test-session','last_assistant_message':'Brain wake is degraded because its ledger is corrupt.'})
        self.assertEqual(p.returncode,0)
        self.assertIn('WAKE_CORRUPT',json.loads(p.stdout)['systemMessage'])
        self.assertNotIn('decision',json.loads(p.stdout))

    def test_known_gap_truncated_skill_must_alarm_before_host(self):
        self.host_spec("print('SYNTHETIC ALLOWED CONTROL')")
        write(self.profile/'skills/brain-framework/SKILL.md','')
        p,_=self.invoke()
        self.alarm(p,'SETUP_OR_DELIVERY_FAILED')
        self.assertFalse(self.marker.exists())

    def test_missing_guardian_is_seen_by_bridge_before_host_effect(self):
        self.host_spec("print('SYNTHETIC ALLOWED CONTROL')")
        Path(self.d['guardian']['root'],'guardian.py').unlink()
        p,_=self.invoke();self.alarm(p,'SETUP_OR_DELIVERY_FAILED');self.assertFalse(self.marker.exists())

    def test_missing_guardian_registration_is_seen_by_bridge_before_host_effect(self):
        self.host_spec("print('SYNTHETIC ALLOWED CONTROL')")
        config=json.loads((self.profile/'hooks.json').read_text())
        config['hooks']['PreToolUse'].remove(self.d['guardian']['groups']['PreToolUse'][0]);write(self.profile/'hooks.json',config)
        p,_=self.invoke();self.alarm(p,'SETUP_OR_DELIVERY_FAILED');self.assertFalse(self.marker.exists())

    def test_known_gap_quoted_example_is_not_actual_disclosure(self):
        write(Path(self.d['home'])/'.claude/brain_health/state.json','{BROKEN')
        key=next(k for k,v in self.d['handlers'].items() if v['event']=='Stop' and 'codex_stop_guard.py' in str(v))
        p,_=self.invoke(key=key,event='Stop',payload={'hook_event_name':'Stop','session_id':'alarm-test-session','last_assistant_message':'The documentation uses the example "brain wake failed" to illustrate log messages. Here is the requested result.'})
        self.assertIn('WAKE_CORRUPT',json.loads(p.stdout)['systemMessage'])

    def test_known_gap_cold_prompt_advertised_wake_command_can_run(self):
        key=next(k for k,v in self.d['handlers'].items() if v['event']=='UserPromptSubmit' and 'codex_stop_guard.py' in str(v))
        p,_=self.invoke(key=key,event='UserPromptSubmit',payload={'hook_event_name':'UserPromptSubmit','session_id':'alarm-test-session','prompt':'Inspect the setup.'})
        context=json.loads(p.stdout)['hookSpecificOutput']['additionalContext']
        line=next(s for s in context.splitlines() if s.startswith('Before substantive work, run: '))
        cmd=shlex.split(line.split('run: ',1)[1]);p,_=self.invoke(command=cmd)
        self.assertEqual(p.returncode,0,p.stderr)
        receipt=Path(self.d['home'])/'.claude/brain_health/state.json'
        self.assertTrue(receipt.is_file(), 'advertised repair must leave a real wake receipt')
        state=json.loads(receipt.read_text());fp=hashlib.sha256(b'alarm-test-session').hexdigest()
        self.assertEqual(state['checks']['codex:wake:codex:session:'+fp]['session_fingerprint'],fp)

    def test_legacy_original_wake_command_is_only_narrow_prerequisite_repair(self):
        cmd=[sys.executable,'-B',str(self.profile/'skills/brain-framework/scripts/brainctl.py'),'--profile',str(self.profile),'native','script','dreaming/codex_wake.py']
        p,_=self.invoke(command=cmd);self.assertEqual(p.returncode,0,p.stderr)
        state=json.loads((Path(self.d['home'])/'.claude/brain_health/state.json').read_text());fp=hashlib.sha256(b'alarm-test-session').hexdigest()
        self.assertEqual(state['checks']['codex:wake:codex:session:'+fp]['session_fingerprint'],fp)
        slug='must_not_follow_wake_repair';target=Path(self.d['memory'])/(slug+'.md')
        denied=cmd[:-1]+['dreaming/brain.py','remember',slug,'--type','project','--desc','Must remain blocked']
        p,_=self.invoke(command=denied);self.assertNotEqual(p.returncode,0);self.assertFalse(target.exists())

def remove_own_root():
    if not OWN_ROOT:
        return
    size = sum(p.stat().st_size for p in ROOT.rglob('*') if p.is_file() and not p.is_symlink())
    shutil.rmtree(ROOT, ignore_errors=True)
    print('removed test root %s (%.1f GB); set ALARM_TEST_ROOT to keep the observations' % (ROOT, size / 1e9), file=sys.stderr)

def tearDownModule():
    if __name__ != '__main__':
        remove_own_root()  # under a test runner; run directly, the block below removes it

if __name__=='__main__':
    program = None
    try: program = unittest.main(verbosity=2,exit=False)
    finally:
        write(RUN/'observations.json',RESULTS)
        write(ROOT/'latest-retained-run.json',{'folder':str(RUN),'cases':len(RESULTS),'native_ui_delivery':'UNTESTED'})
        remove_own_root()
    raise SystemExit(0 if program and program.result.wasSuccessful() else 1)
