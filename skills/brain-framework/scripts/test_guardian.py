"""Deterministic standalone guardian controls. Protocol only; no native UI proof."""
from __future__ import annotations
import datetime as dt
import hashlib
import importlib.util
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
from unittest.mock import patch

OWN_ROOT=not os.environ.get('GUARDIAN_TEST_ROOT')  # a root the caller names is kept; one made here is removed
ROOT=Path(os.environ.get('GUARDIAN_TEST_ROOT') or tempfile.mkdtemp(prefix='brain-guardian-controls-')).resolve()
RUN=ROOT/'guardian-tests'/str(time.time_ns());RUN.mkdir(parents=True)
TEMP=RUN/'temp';TEMP.mkdir();os.environ['TMPDIR']=str(TEMP);tempfile.tempdir=str(TEMP)
sys.dont_write_bytecode=True
SKILL=Path(os.environ.get('GUARDIAN_TEST_SKILL',Path(__file__).absolute().parent.parent)).resolve()
sys.path.insert(0,str(SKILL/'scripts'))
import connector as c
import brain_binding as b
spec=importlib.util.spec_from_file_location('tested_guardian',SKILL/'scripts/guardian.py')
g=importlib.util.module_from_spec(spec);spec.loader.exec_module(g)
RESULTS=[]

def put(p,x):
    p.parent.mkdir(parents=True,exist_ok=True);p.write_text(x if isinstance(x,str) else json.dumps(x,indent=2))

def replace(v,old,new):
    if isinstance(v,str): return v.replace(old,new)
    if isinstance(v,list): return [replace(x,old,new) for x in v]
    if isinstance(v,dict): return {k:replace(x,old,new) for k,x in v.items()}
    return v

class GuardianControls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base=RUN/'cold-base/profile with spaces λ';cls.base.mkdir(parents=True)
        cls.d=c.create_deployment(cls.base,dependencies=False);c.activate(cls.base,cls.d)
        b.bind_main(cls.base,'Synthetic standalone guardian qualification only; no live owner data',apply=True)
        put(RUN/'base-proof.json',{'source':c.verify_source(cls.d['source']),'runtime':c.verify_runtime_code(cls.d),'native_ui':'UNTESTED'})

    def setUp(self):
        self.folder=RUN/self._testMethodName;self.profile=self.folder/'profile with spaces λ'
        shutil.copytree(self.base,self.profile,symlinks=True)
        old,new=str(self.base),str(self.profile);self.d=replace(self.__class__.d,old,new)
        for path in [self.profile/'brain-framework/active.json',self.profile/'brain-framework/brain-bindings.json',self.profile/'hooks.json']:
            put(path,replace(json.loads(path.read_text()),old,new))
        for name in ['AGENTS.md','AGENTS.override.md','config.toml']:
            path=self.profile/name
            if path.exists():put(path,path.read_text().replace(old,new))
        self.save()
        # A fixture-owned configured host wrapper delegates to the actual base
        # interpreter. It permits deletion tests without touching that interpreter.
        self.host=self.profile/'configured-host-python'
        actual=Path(getattr(sys,'_base_executable',None) or sys.executable)
        put(self.host,'#!/bin/sh\nexec '+shlex.quote(str(actual))+' "$@"\n');self.host.chmod(0o700)
        with patch.object(sys,'_base_executable',str(self.host)):
            self.meta=g.install(self.profile,self.d,self.profile/'skills/brain-framework')
        config=json.loads((self.profile/'hooks.json').read_text())
        for event,groups in self.meta['groups'].items():
            for group in groups:
                if group not in config['hooks'].setdefault(event,[]): config['hooks'][event].append(group)
                if group not in self.d['managed_hook_groups'].setdefault(event,[]): self.d['managed_hook_groups'][event].append(group)
        put(self.profile/'hooks.json',config);self.save()
        g.refresh_manifest(self.profile,self.d,self.profile/'skills/brain-framework')
        self.launcher=Path(self.meta['root'])/'launch.sh';self.script=Path(self.meta['root'])/'guardian.py'
        self.manifest=Path(self.meta['manifest']);self.session='guardian-test-session'
        self.offline=Path(self.d['home'])/'.claude/brain_offline'

    def save(self):put(Path(self.d['home']).parent/'deployment.json',self.d)

    def run_guardian(self,event='PreToolUse',payload=None,raw=None,session_env=True,env_session=None):
        data=payload or {'session_id':self.session,'cwd':str(self.folder),'hook_event_name':event,'prompt':'Inspect the setup.'}
        env={k:v for k,v in os.environ.items() if k not in {'CODEX_THREAD_ID','CLAUDE_CODE_SESSION_ID','PYTHONPATH','PYTHONHOME'}}
        env.update({'HOME':str(self.folder/'outer-home'),'USERPROFILE':str(self.folder/'outer-home'),'TMPDIR':str(TEMP),'PYTHONDONTWRITEBYTECODE':'1'})
        if session_env:env['CODEX_THREAD_ID']=env_session or self.session
        cmd=['/bin/sh',str(self.launcher),event]
        p=subprocess.run(cmd,input=json.dumps(data) if raw is None else raw,capture_output=True,text=True,env=env,timeout=15)
        result={'test':self._testMethodName,'argv':cmd,'payload':data if raw is None else None,'input_bytes':len((raw if raw is not None else json.dumps(data)).encode()),
                'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr,'native_ui':'UNTESTED','host_wrapper':'synthetic configured wrapper delegates actual base Python'}
        RESULTS.append(result);put(self.folder/('observation-'+str(len(RESULTS))+'.json'),result)
        return p

    def alarm(self,p,code):
        self.assertEqual(p.returncode,0,'JSON transport success is distinct from dependency success')
        self.assertNotIn('Traceback',p.stderr)
        self.assertTrue(p.stdout.strip(),'Expected '+code+'; observer incorrectly stayed silent')
        value=json.loads(p.stdout);self.assertIn(code,value['systemMessage']);self.assertLess(len(p.stdout),1600)
        self.assertNotIn('permissionDecision',p.stdout,'Warnings must permit already-authorized repair tools')
        return value

    def test_intact_cold_control_visibly_unadmitted(self):
        self.alarm(self.run_guardian(),'ADMISSION_UNPROVEN')
        self.assertEqual(set(self.meta['groups']),set(g.EVENTS))
        self.assertTrue(all('hook_bridge' not in str(x) for x in self.meta['groups'].values()))

    def test_admission_repair_command_preserves_spaces_and_unicode(self):
        value=self.alarm(self.run_guardian(),'ADMISSION_UNPROVEN')
        argv=shlex.split(value['systemMessage'].split(' Run: ',1)[1].split(' Ordinary work is unadmitted.',1)[0])
        self.assertEqual(argv,[self.d['python'],str(self.profile/'skills/brain-framework/scripts/brainctl.py'),
                              '--profile',str(self.profile),'--workspace',str(self.folder),'admit','--session',self.session])

    def test_existing_profile_upgrade_preserves_identity_owner_hooks_and_dry_run(self):
        config=json.loads((self.profile/'hooks.json').read_text())
        owner={'hooks':[{'type':'command','command':'printf untouched_owner','timeout':2}]}
        config['hooks'].setdefault('SessionEnd',[]).append(owner);put(self.profile/'hooks.json',config)
        watched=[*c.guardian_paths(self.profile),self.profile/'hooks.json',Path(self.d['home']).parent/'deployment.json',self.profile/'brain-framework/brain-bindings.json']
        before={str(p):p.read_bytes() for p in watched}
        plan=c.install_guardian(self.profile)
        self.assertFalse(plan['changes_applied']);self.assertEqual(before,{str(p):p.read_bytes() for p in watched})
        result=c.install_guardian(self.profile,apply=True);self.assertTrue(result['changes_applied'])
        current=c.active(self.profile)
        self.assertEqual((current['id'],current['home'],current['memory']),(self.d['id'],self.d['home'],self.d['memory']))
        self.assertIn(owner,json.loads((self.profile/'hooks.json').read_text())['hooks']['SessionEnd'])
        self.assertEqual((self.profile/'brain-framework/brain-bindings.json').read_bytes(),before[str(self.profile/'brain-framework/brain-bindings.json')])
        self.assertEqual(c.verify_guardian(current)['events_registered'],5)
        for event,groups in current['guardian']['groups'].items():
            for group in groups:self.assertEqual(json.loads((self.profile/'hooks.json').read_text())['hooks'][event].count(group),1)

    def test_existing_profile_failed_upgrade_restores_exact_owned_files(self):
        paths=[*c.guardian_paths(self.profile),self.profile/'hooks.json',Path(self.d['home']).parent/'deployment.json']
        before={str(p):(p.read_bytes(),p.stat().st_mode) for p in paths}
        # Fail after the observer installer has overwritten an owned file.
        import guardian as installed_guardian
        def broken(*args):
            (self.profile/'brain-framework/guardian/guardian.py').write_text('partial failed integration')
            raise RuntimeError('controlled post-write failure')
        with patch.object(installed_guardian,'install',side_effect=broken):
            with self.assertRaisesRegex(RuntimeError,'controlled post-write'):c.install_guardian(self.profile,apply=True)
        self.assertEqual(before,{str(p):(p.read_bytes(),p.stat().st_mode) for p in paths})
        self.assertFalse((self.profile/'brain-framework/DISCONNECTED').exists())

    def test_disconnected_profile_refuses_upgrade_without_changing_owner_flag(self):
        flag=self.profile/'brain-framework/DISCONNECTED';flag.write_bytes(b'Owner disconnected this profile.\r\n');flag.chmod(0o640)
        before=(flag.read_bytes(),flag.stat().st_mode)
        with self.assertRaisesRegex(c.ConnectorError,'disconnected'):c.install_guardian(self.profile,apply=True)
        self.assertEqual((flag.read_bytes(),flag.stat().st_mode),before)

    def test_complete_skill_removal_still_alarms(self):
        shutil.rmtree(self.profile/'skills/brain-framework');self.alarm(self.run_guardian(),'GUARDIAN_DEPENDENCY_FAILED')
    def test_truncated_skill_is_not_a_valid_package(self):
        put(self.profile/'skills/brain-framework/SKILL.md','');self.alarm(self.run_guardian(),'GUARDIAN_DEPENDENCY_FAILED')
    def test_unverified_connector_cannot_execute(self):
        marker=self.folder/'unverified-connector-executed'
        put(self.profile/'skills/brain-framework/scripts/connector.py','from pathlib import Path\nPath('+repr(str(marker))+').write_text("bad")')
        self.alarm(self.run_guardian(),'GUARDIAN_DEPENDENCY_FAILED');self.assertFalse(marker.exists())
    def test_module_buffer_changed_after_package_check_is_not_executed(self):
        marker=self.folder/'second-read-executed'
        skill=self.profile/'skills/brain-framework';expected=g.package_files(skill)
        original=g.regular
        planted=('from pathlib import Path\nPath('+repr(str(marker))+').write_text("bad")').encode()
        def changed_buffer(path,limit=None):
            return planted if Path(path)==skill/'scripts/connector.py' else original(path,limit)
        with patch.object(g,'regular',side_effect=changed_buffer):
            with self.assertRaisesRegex(g.GuardianError,'changed before execution'):g._load_modules(skill,expected)
        self.assertFalse(marker.exists())
    def test_runtime_python_missing_has_independent_warning(self):
        Path(self.d['python']).unlink();self.alarm(self.run_guardian(),'GUARDIAN_DEPENDENCY_FAILED')
    def test_runtime_code_corruption_has_warning(self):
        put(Path(self.d['home'])/'.claude/dreaming/brain.py','raise RuntimeError("must not run")');self.alarm(self.run_guardian(),'GUARDIAN_DEPENDENCY_FAILED')
    def test_source_corruption_has_warning(self):
        put(Path(self.d['source'])/'docs/METHOD.md','corrupt');self.alarm(self.run_guardian(),'GUARDIAN_DEPENDENCY_FAILED')
    def test_required_registration_removed_is_seen_by_remaining_observer(self):
        config=json.loads((self.profile/'hooks.json').read_text());config['hooks']['PreToolUse'].remove(self.meta['groups']['PreToolUse'][0]);put(self.profile/'hooks.json',config)
        self.alarm(self.run_guardian(event='SessionStart'),'GUARDIAN_DEPENDENCY_FAILED')
    def test_unrelated_owner_hook_is_allowed(self):
        config=json.loads((self.profile/'hooks.json').read_text());config['hooks'].setdefault('SessionEnd',[]).append({'hooks':[{'type':'command','command':'printf owner_control','timeout':2}]});put(self.profile/'hooks.json',config)
        self.alarm(self.run_guardian(),'ADMISSION_UNPROVEN')
    def test_missing_standing_activation_is_reported(self):
        (self.profile/'AGENTS.md').unlink();self.alarm(self.run_guardian(),'GUARDIAN_DEPENDENCY_FAILED')
    def test_unknown_main_binding_is_not_replaced(self):
        (self.profile/'brain-framework/brain-bindings.json').unlink();self.alarm(self.run_guardian(),'GUARDIAN_DEPENDENCY_FAILED')
        self.assertFalse((self.profile/'brain-framework/brain-bindings.json').exists())

    def test_failed_admission_count_remains_visible(self):
        path=b.activation_path(self.profile,self.session,self.folder)
        put(path,{'at':dt.datetime.now(dt.timezone.utc).isoformat(),'passed':False,'brains':[{'applicable_setup':{'open_required_checks':[{}]*70}}]})
        out=self.alarm(self.run_guardian(),'ADMISSION_UNPROVEN');self.assertIn('70 applicable checks',out['systemMessage'])
    def test_stale_synthetic_receipt_cannot_silence_admission(self):
        put(b.activation_path(self.profile,self.session,self.folder),{'at':'2000-01-01T00:00:00+00:00','passed':True,'session':self.session,'workspace':str(self.folder),'selection':b.selection(self.profile,workspace=self.folder)})
        self.alarm(self.run_guardian(),'ADMISSION_UNPROVEN')

    def test_guardian_script_missing_uses_literal_fallback(self):
        self.script.unlink();self.alarm(self.run_guardian(),'GUARDIAN_UNAVAILABLE')
    def test_guardian_script_tamper_cannot_execute(self):
        marker=self.folder/'unverified-guardian-executed'
        put(self.script,'from pathlib import Path\nPath('+repr(str(marker))+').write_text("bad")\nprint("'+g.OK+'")')
        self.alarm(self.run_guardian(),'GUARDIAN_UNAVAILABLE');self.assertFalse(marker.exists())
    def test_guardian_executes_the_verified_buffer_despite_second_read_race(self):
        marker=self.folder/'second-guardian-read-executed'
        tokens=shlex.split(self.launcher.read_text(),comments=True)
        # The disconnect-only secondary parser precedes the full observer now.
        # Plant the race at the verified-buffer executor, not the first -c token.
        loaders=[tokens[i+1] for i,t in enumerate(tokens[:-1]) if t=='-c' and 'exec(compile(code,script' in tokens[i+1]]
        self.assertEqual(len(loaders),1,'Exactly one verified-buffer observer must be exercised')
        loader=loaders[0]
        planted=('from pathlib import Path\nPath('+repr(str(marker))+').write_text("bad")\nprint("'+g.OK+'")\n').encode()
        shim='''import pathlib,sys
_original_read=pathlib.Path.read_bytes
_calls=0
def _race_read(path):
 global _calls
 raw=_original_read(path)
 if str(path)==sys.argv[1] and _calls==0:
  _calls+=1
  path.write_bytes(PLANTED)
 return raw
pathlib.Path.read_bytes=_race_read
'''.replace('PLANTED',repr(planted))
        cmd=[str(self.host),'-I','-S','-B','-c',shim+'\n'+loader,str(self.script),self.meta['files'][str(self.script)],str(self.manifest),str(self.profile),self.d['home'],'PreToolUse']
        payload={'session_id':self.session,'cwd':str(self.folder),'hook_event_name':'PreToolUse'}
        p=subprocess.run(cmd,input=json.dumps(payload),capture_output=True,text=True,timeout=15)
        observation={'test':self._testMethodName,'argv':cmd,'payload':payload,'exit':p.returncode,'stdout':p.stdout,'stderr':p.stderr,'marker':marker.exists(),'fault':'replace on-disk guardian immediately after first exact-byte read','native_ui':'UNTESTED'}
        RESULTS.append(observation);put(self.folder/'race-observation.json',observation)
        self.alarm(p,'GUARDIAN_DEPENDENCY_FAILED');self.assertFalse(marker.exists())
    def test_configured_host_python_missing_uses_literal_fallback(self):
        self.host.unlink();self.alarm(self.run_guardian(),'GUARDIAN_UNAVAILABLE')
    def test_configured_host_python_nonzero_uses_literal_fallback(self):
        put(self.host,'#!/bin/sh\nexit 9\n');self.host.chmod(0o700);self.alarm(self.run_guardian(),'GUARDIAN_UNAVAILABLE')
    def test_configured_host_python_false_quiet_success_is_not_trusted(self):
        put(self.host,'#!/bin/sh\nexit 0\n');self.host.chmod(0o700);self.alarm(self.run_guardian(),'GUARDIAN_UNAVAILABLE')
    def test_configured_host_malformed_warning_is_replaced_by_literal_json(self):
        put(self.host,'#!/bin/sh\nprintf \'%s\\n\' \'{"systemMessage":bad}\'\nexit 0\n');self.host.chmod(0o700)
        self.alarm(self.run_guardian(),'GUARDIAN_UNAVAILABLE')
    def test_corrupt_manifest_is_not_a_healthy_receipt(self):
        put(self.manifest,'{BROKEN');self.alarm(self.run_guardian(),'GUARDIAN_DEPENDENCY_FAILED')
    def test_emptied_manifest_dependency_sets_are_rejected(self):
        original=json.loads(self.manifest.read_text())
        for key in ['guardian_files','required_groups','admission_dependency']:
            with self.subTest(key=key):
                doc=dict(original);doc[key]={};put(self.manifest,doc)
                self.alarm(self.run_guardian(),'GUARDIAN_DEPENDENCY_FAILED')
    def test_launcher_change_is_detected_when_it_still_runs(self):
        self.launcher.write_text(self.launcher.read_text()+'\n# changed\n');self.alarm(self.run_guardian(),'GUARDIAN_DEPENDENCY_FAILED')
    def test_invalid_payload_is_a_bounded_protocol_warning(self):
        for raw in ['[]','{BROKEN','x'*(2*1024*1024+1)]:
            with self.subTest(bytes=len(raw)):self.alarm(self.run_guardian(raw=raw),'GUARDIAN_UNAVAILABLE')

    def test_permanent_disconnect_survives_missing_skill_and_python(self):
        put(self.profile/'brain-framework/DISCONNECTED','explicit disconnect');shutil.rmtree(self.profile/'skills');self.host.unlink()
        p=self.run_guardian();self.assertEqual((p.returncode,p.stdout,p.stderr),(0,'',''))
    def test_global_disconnect_survives_missing_script(self):
        put(self.offline/'ALL.flag','off');self.script.unlink();p=self.run_guardian();self.assertEqual((p.returncode,p.stdout),(0,''))
    def test_exact_session_disconnect_survives_missing_script_without_env(self):
        put(self.offline/(self.session+'.flag'),'off');self.script.unlink();p=self.run_guardian(session_env=False);self.assertEqual((p.returncode,p.stdout),(0,''))
    def test_payload_disconnect_survives_primary_parser_loss_despite_parent_environment(self):
        put(self.offline/(self.session+'.flag'),'off');self.host.unlink();p=self.run_guardian(env_session='different-session');self.assertEqual((p.returncode,p.stdout),(0,''))
    def test_other_session_disconnect_does_not_silence_current(self):
        put(self.offline/'different-session.flag','off');self.alarm(self.run_guardian(),'ADMISSION_UNPROVEN')
    def test_payload_session_wins_over_stale_environment_when_parser_works(self):
        put(self.offline/'different-session.flag','off')
        self.alarm(self.run_guardian(env_session='different-session'),'ADMISSION_UNPROVEN')
    def test_requested_disconnect_is_respected_without_creating_flags(self):
        shutil.rmtree(self.profile/'skills');p=self.run_guardian('UserPromptSubmit',{'session_id':self.session,'cwd':str(self.folder),'hook_event_name':'UserPromptSubmit','prompt':'/brain off'})
        self.assertEqual((p.returncode,p.stdout),(0,''));self.assertFalse((self.offline/(self.session+'.flag')).exists())
    def test_guardian_never_reconnects_existing_offline_session(self):
        flag=self.offline/(self.session+'.flag');put(flag,'off')
        p=self.run_guardian('UserPromptSubmit',{'session_id':self.session,'cwd':str(self.folder),'hook_event_name':'UserPromptSubmit','prompt':'/connect'})
        self.assertEqual((p.returncode,p.stdout),(0,''));self.assertTrue(flag.exists())
    def test_secondary_parser_honors_actual_payload_disconnect_without_environment(self):
        put(self.offline/(self.session+'.flag'),'off');self.host.unlink()
        p=self.run_guardian(session_env=False);self.assertEqual((p.returncode,p.stdout),(0,''))
    def test_missing_primary_parser_does_not_inherit_parent_disconnect(self):
        put(self.offline/'disconnected-parent.flag','off');self.host.unlink()
        self.alarm(self.run_guardian(env_session='disconnected-parent'),'GUARDIAN_UNAVAILABLE')
    def test_missing_both_parsers_cannot_guess_a_session_disconnect(self):
        put(self.offline/(self.session+'.flag'),'off');self.host.unlink();Path(self.d['python']).unlink()
        self.alarm(self.run_guardian(),'GUARDIAN_UNAVAILABLE')
    def test_malformed_event_identity_cannot_alias_another_disconnect_flag(self):
        put(self.offline/'othersession.flag','off')
        self.alarm(self.run_guardian(payload={'session_id':'other/session','cwd':str(self.folder),'hook_event_name':'PreToolUse'}),'ADMISSION_UNPROVEN')
    def test_passing_native_event_admission_survives_inherited_parent_environment(self):
        # Healthy verdict producers are isolated fixtures; actual descriptor,
        # source, wake reads, fingerprints and standalone shell observer run.
        inventory = {"items": [{"id": "S01", "capability": "core", "checks": [
            {"requirement": "Current source", "status": "checked-current-observation"},
        ]}], "selected_capabilities": {"core": ["fixture"]}}
        with patch.dict(os.environ,{'CODEX_THREAD_ID':self.session}), \
             patch.object(c,'verify',return_value={'ready':True}), \
             patch('setup_checklist.inspect',return_value=inventory):
            self.assertTrue(b.admit(self.profile,self.session,self.folder)['passed'])
        p=self.run_guardian(env_session='unrelated-parent')
        self.assertEqual((p.returncode,p.stdout,p.stderr),(0,'',''))
        self.alarm(self.run_guardian(payload={'session_id':'another-child','cwd':str(self.folder),'hook_event_name':'PreToolUse'},env_session='unrelated-parent'),'ADMISSION_UNPROVEN')
    def test_refresh_refuses_to_repin_changed_guardian(self):
        self.script.write_text(self.script.read_text()+'\n# changed\n')
        with self.assertRaises(g.GuardianError):g.refresh_manifest(self.profile,self.d,self.profile/'skills/brain-framework')
    def test_unsupported_windows_install_fails_explicitly(self):
        with patch.object(g.os,'name','nt'):
            with self.assertRaisesRegex(g.GuardianError,'Windows is unqualified'):g.install(self.profile,self.d,self.profile/'skills/brain-framework')

def remove_own_root():
    if not OWN_ROOT: return
    size=sum(p.stat().st_size for p in ROOT.rglob('*') if p.is_file() and not p.is_symlink())
    shutil.rmtree(ROOT,ignore_errors=True)
    print('removed test root %s (%.1f GB); set GUARDIAN_TEST_ROOT to keep the observations'%(ROOT,size/1e9),file=sys.stderr)

def tearDownModule():
    if __name__!='__main__': remove_own_root()  # under a test runner; run directly, the block below removes it

if __name__=='__main__':
    program=None
    try:program=unittest.main(verbosity=2,exit=False)
    finally:
        put(RUN/'observations.json',RESULTS)
        put(ROOT/'guardian-tests/latest.json',{'folder':str(RUN),'observations':len(RESULTS),'native_ui':'UNTESTED'})
        remove_own_root()
    raise SystemExit(0 if program and program.result.wasSuccessful() else 1)
