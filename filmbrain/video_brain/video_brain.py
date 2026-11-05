"""Read-only production brief and delivery-receipt checks.

No renderer, TTS request, network request, process launcher or global brain write
occurs here. RECEIPTS_COMPLETE means the submitted records are internally
consistent, never that this program watched a film or judged it beautiful.
The video producer must populate real observations, not generated pass labels.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys
from catalog import DOMAINS, knowledge

APPROVED_VOICES = {
    'en-US-AndrewMultilingualNeural', 'en-US-BrianMultilingualNeural',
    'en-GB-RyanNeural', 'en-US-GuyNeural',
}
GEOMETRIC_KINDS = {'geometry','plot','diagram','map','worked_example','data_image'}
REVIEW_METHODS = {
    'visual':{'view_native_render'}, 'audio':{'listen_actual_audio'},
    'av':{'watch_actual_export'}, 'facts':{'independent_source_check'},
}

def sha256(data):
    return hashlib.sha256(data).hexdigest()

def recording_key(text, configuration):
    # Never normalize the text or ignore voice/rate: either changes the audio.
    value={'text':text,'configuration':configuration}
    return sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8'))

def finite(value, *, positive=False):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and (not positive or value>0)

def hash_ok(value):
    return isinstance(value,str) and bool(re.fullmatch('[0-9a-f]{64}',value))

def covered(intervals, duration):
    """Union coverage, not sum of lengths (overlaps cannot fill a gap)."""
    if not finite(duration,positive=True) or not isinstance(intervals,list) or not intervals:
        return False
    valid=[]
    for item in intervals:
        if not isinstance(item,list) or len(item)!=2:
            return False
        a,b=item
        if not finite(a) or not finite(b) or a<0 or b<=a or b>duration:
            return False
        valid.append((a,b))
    end=0.0
    for a,b in sorted(valid):
        if a>end+1e-6:
            return False
        end=max(end,b)
    return end>=duration-1e-6

def validate(data, artifact_root=None):
    try:
        return _validate(data,artifact_root)
    except (ValueError,TypeError,KeyError,AttributeError) as exc:
        return {'status':'BLOCKED','findings':[{'code':'MALFORMED_INPUT','scope':'manifest','detail':str(exc)}]}

def _validate(data, artifact_root=None):
    errors=[]
    def fail(code,scope,detail):
        errors.append({'code':code,'scope':scope,'detail':detail})
    if not isinstance(data,dict) or data.get('schema')!='video_brain.delivery.v1':
        return {'status':'BLOCKED','findings':[{'code':'SCHEMA','scope':'manifest','detail':'Expected video_brain.delivery.v1 object.'}]}
    try:
        project=data['project'];shots=data['shots'];assets=data['assets'];recordings=data['recordings']
        reviews=data['reviews'];artifacts=data['artifacts'];policy=data['policy'];final=data['final']
        if not all(isinstance(x,list) for x in [shots,assets,recordings,reviews,artifacts]):
            raise ValueError('manifest collections must be arrays')
        if not isinstance(project,str) or not project.strip() or not shots:
            raise ValueError('nonempty project and shots required')
        if data.get('kind') not in {'lecture','documentary','fiction','explainer'}:
            raise ValueError('unknown video kind')
        if not isinstance(policy,dict) or not isinstance(final,dict):
            raise ValueError('policy/final must be objects')
        minimums={stage:policy.get(stage+'_passes',2) for stage in ['visual','audio','av','facts']}
        if any(not isinstance(n,int) or isinstance(n,bool) or n<2 for n in minimums.values()):
            raise ValueError('each required review-pass count must be an integer >=2')
    except (KeyError,TypeError,ValueError) as exc:
        return {'status':'BLOCKED','findings':[{'code':'SCHEMA','scope':'manifest','detail':str(exc)}]}

    def index(rows,scope):
        result={}
        for row in rows:
            if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not row['id'].strip():
                fail('ID',scope,'Every row requires a nonempty string ID.');continue
            if row['id'] in result:
                fail('DUPLICATE_ID',row['id'],scope)
            result[row['id']]=row
        return result
    amap=index(assets,'assets');fmap=index(artifacts,'artifacts');smap=index(shots,'shots')
    rmap=index(reviews,'reviews');index(recordings,'recordings')
    # A manifest claims file identity unless the producer explicitly asks for
    # local byte verification. Keep these assurance levels separate in output.
    for fid,artifact in fmap.items():
        if not hash_ok(artifact.get('sha256')):
            fail('ARTIFACT_HASH',fid,'A current SHA-256 is required.')
        if artifact_root is not None:
            try:
                relative=artifact.get('path')
                if not isinstance(relative,str) or not relative:
                    raise ValueError('missing artifact path')
                relative_path=Path(relative)
                if relative_path.is_absolute() or relative_path.drive or '\\' in relative or ':' in relative:
                    raise ValueError('artifact path must be a portable relative path')
                target=(artifact_root/relative_path).resolve(strict=True)
                target.relative_to(artifact_root.resolve(strict=True))
                if not target.is_file():
                    raise ValueError('artifact is not a file')
                h=hashlib.sha256()
                with target.open('rb') as stream:
                    for block in iter(lambda:stream.read(1024*1024),b''):
                        h.update(block)
                if h.hexdigest()!=artifact['sha256']:
                    fail('FILE_HASH_MISMATCH',fid,'Local bytes differ from manifest.')
            except (OSError,ValueError,KeyError) as exc:
                fail('ARTIFACT_PATH',fid,str(exc))

    def require_artifact(fid,scope):
        if fid not in fmap:
            fail('MISSING_ARTIFACT',scope,str(fid));return None
        return fmap[fid]

    final_art=require_artifact(final.get('artifact_id'),'final')
    duration=final.get('duration_seconds')
    probe=final.get('probe',{})
    if not finite(duration,positive=True):
        fail('FINAL_DURATION','final','Measured positive duration required.')
    if not isinstance(probe,dict) or not probe.get('evidence') or not all(probe.get(k) is True for k in ['video_stream','audio_stream']):
        fail('FINAL_PROBE','final','Independent media-probe evidence and expected streams required.')
    elif not finite(probe.get('duration_seconds'),positive=True) or not finite(duration,positive=True) or abs(probe['duration_seconds']-duration)>0.1:
        fail('FINAL_PROBE_DURATION','final','Media probe and manifest disagree.')
    if set(data.get('expected_shot_ids',[]))!=set(smap) or len(data.get('expected_shot_ids',[]))!=len(shots):
        fail('SHOT_COVERAGE','manifest','Expected shot set must exactly match actual shots, without duplicates.')
    elif all(finite(shot.get('start_seconds')) for shot in smap.values()):
        # Membership cannot catch a swapped chapter. Compare the intended order
        # with measured timeline order, independently of JSON row ordering.
        actual_order=sorted(smap,key=lambda sid:smap[sid]['start_seconds'])
        if data['expected_shot_ids']!=actual_order:
            fail('SHOT_ORDER','manifest','Measured timeline differs from the expected shot order.')

    valid_reviews={}
    for rid,review in rmap.items():
        scope=review.get('scope');stage=review.get('stage');fid=review.get('artifact_id')
        art=fmap.get(fid);pass_no=review.get('pass')
        if (stage not in REVIEW_METHODS or review.get('method') not in REVIEW_METHODS.get(stage,set())
            or review.get('result')!='pass' or not isinstance(pass_no,int) or isinstance(pass_no,bool) or pass_no<1
            or not isinstance(review.get('reviewer'),str) or not review['reviewer'].strip()
            or not isinstance(review.get('evidence'),str) or not review['evidence'].strip()
            or not isinstance(review.get('observed_at'),str) or not review['observed_at'].strip()):
            fail('REVIEW_EVIDENCE',rid,'Actual review method, evidence, reviewer, date, positive pass number and pass result required.');continue
        if art is None or review.get('artifact_sha256')!=art.get('sha256') or not hash_ok(review.get('artifact_sha256')):
            fail('STALE_REVIEW',rid,'Review is not bound to the current artifact.');continue
        # Distinct event IDs alone are not separate passes. One scope/stage/pass
        # may appear only once, preventing duplicated rows from inflating counts.
        key=(scope,stage,pass_no)
        if key in valid_reviews:
            fail('DUPLICATE_REVIEW_PASS',rid,'Duplicate scope, stage and pass number.');continue
        if scope=='final' and (stage!='av' or fid!=final.get('artifact_id') or not covered(review.get('intervals'),duration)):
            fail('FINAL_REVIEW_COVERAGE',rid,'A full pass must watch the exact final export from beginning to end.');continue
        if scope!='final' and scope not in smap:
            fail('REVIEW_SCOPE',rid,'Unknown shot scope.');continue
        valid_reviews[key]=review

    def need_reviews(scope,stage,fid):
        found={p for (s,k,p),v in valid_reviews.items() if s==scope and k==stage and v['artifact_id']==fid}
        required=set(range(1,minimums[stage]+1))
        if not required.issubset(found):
            fail('REVIEW_PASSES',scope,f'{stage}: required {sorted(required)}, recorded {sorted(found)}')

    uses={};hash_uses={};timeline=[]
    for sid,shot in smap.items():
        start=shot.get('start_seconds');end=shot.get('end_seconds')
        if not finite(start) or not finite(end) or start<0 or end<=start:
            fail('SHOT_TIME',sid,'Invalid timeline interval.')
        else:
            timeline.append([start,end])
        visual=shot.get('visual',{})
        if not isinstance(visual,dict) or not isinstance(visual.get('explanation'),str) or not visual['explanation'].strip() or not visual.get('asset_ids'):
            fail('EXPLANATORY_VISUAL',sid,'Every shot requires a relevant visual with an explanatory role.');visual={}
        if data['kind']=='lecture' and shot.get('explanatory',True) and visual.get('kind') not in GEOMETRIC_KINDS:
            fail('GEOMETRIC_VISUAL',sid,'Math explanation requires geometry, a graph, diagram, map, example or data image.')
        if not shot.get('source_evidence'):
            fail('SOURCE_EVIDENCE',sid,'The explanatory/factual claim needs a source or clearly labelled illustration.')
        for aid in visual.get('asset_ids',[]):
            asset=amap.get(aid)
            if asset is None:
                fail('MISSING_ASSET',sid,str(aid));continue
            origin=asset.get('source_asset_id');h=asset.get('sha256')
            if not origin or not hash_ok(h) or not asset.get('source_evidence'):
                fail('ASSET_PROVENANCE',aid,'Source identity, content hash and provenance required.')
            if data['kind']=='documentary':
                for identity,table in [(origin,uses),(h,hash_uses)]:
                    if identity in table and table[identity]!=sid:
                        fail('REUSED_DOCUMENTARY_ASSET',aid,'Same underlying source or bytes appear in separate shots.')
                    table[identity]=sid
        visual_art=shot.get('visual_artifact_id');audio_art=shot.get('audio_artifact_id')
        require_artifact(visual_art,sid);require_artifact(audio_art,sid)
        need_reviews(sid,'visual',visual_art);need_reviews(sid,'audio',audio_art);need_reviews(sid,'facts',visual_art)
        narration=shot.get('narration',{});config=narration.get('configuration',{}) if isinstance(narration,dict) else {}
        if not isinstance(config,dict):
            fail('VOICE_CONFIGURATION',sid,'Configuration object required.');continue
        if config.get('provider')!='microsoft' or config.get('voice') not in APPROVED_VOICES:
            fail('MICROSOFT_NATURAL_ONLY',sid,'Use a currently auditioned Microsoft natural/neural voice; update the approved set only with catalogue and listening evidence.')
        if config.get('rate')!='+10%':
            if not narration.get('rate_listening_evidence'):
                fail('RATE_EVIDENCE',sid,'A rate other than the working +10% needs an actual intelligibility/pace listening receipt.')
        if not config.get('language') or not config.get('pronunciation_revision') or not config.get('engine_settings_revision'):
            fail('RECORDING_CONFIGURATION',sid,'Complete language, pronunciation and engine-settings identity required.')
        text=narration.get('text') if isinstance(narration,dict) else None
        if not isinstance(text,str) or not text.strip():
            fail('NARRATION_TEXT',sid,'Current spoken text required.');continue
        key=recording_key(text,config)
        matches=[r for r in recordings if isinstance(r,dict) and r.get('shot_id')==sid and r.get('recording_key')==key]
        if not matches:
            fail('CURRENT_RECORDING',sid,'No recording matches current text and complete configuration.');continue
        identities={(r.get('artifact_id'),r.get('duration_seconds')) for r in matches}
        if len(identities)!=1:
            fail('CONFLICTING_RECORDINGS',sid,'Conflicting current manifest shards; no filename-order tiebreak.');continue
        rec=matches[0];seconds=rec.get('duration_seconds')
        if rec.get('artifact_id')!=audio_art or not finite(seconds,positive=True):
            fail('RECORDING_IDENTITY',sid,'Wrong audio artifact or invalid measured duration.')
        elif finite(start) and finite(end) and seconds>end-start+0.1:
            fail('AUDIO_OVERRUN',sid,'The current narration is longer than its visual slot.')
    if finite(duration,positive=True) and not covered(timeline,duration):
        fail('TIMELINE_COVERAGE','final','Shots do not cover exactly the measured final duration.')
    if finite(duration,positive=True) and abs(sum(b-a for a,b in timeline)-duration)>0.1:
        fail('TIMELINE_LENGTH','final','Logical shot slots must be disjoint and sum to final duration; describe a transition inside its owning slot.')
    if final_art:
        need_reviews('final','av',final['artifact_id'])
    return {'status':'BLOCKED' if errors else 'RECEIPTS_COMPLETE',
            'assurance':'local artifact bytes checked' if artifact_root else 'manifest and declared review receipts only',
            'limitation':'This checker cannot establish that a reviewer actually watched/listened, that an image explains the idea, or that the content is accurate.',
            'findings':errors}

def brief(query):
    q=re.sub(r'\b(?:vido[e]?s?|vidoes?|vieos?)\b','video',query.lower())
    q=re.sub(r'\b(?:lecutre|lecutres|leucture)\b','lecture',q)
    q=re.sub(r'\b(?:videos|movies|films|lectures|animations)\b',lambda m:m.group()[:-1],q)
    q=re.sub(r'\bdocumentaries\b','documentary',q)
    # Disambiguate compound nouns before looking for a production object.
    # Another genuine object in the same query (e.g. a documentary about a
    # gaming topic) may still route. These are lexical scope boundaries, not
    # claims of general semantic understanding.
    q=re.sub(r'\bvideo\s+(?:games?|drivers?|cards?|calls?|conferencing)\b',' ',q)
    q=re.sub(r'\blecture\s+(?:attendance|register|schedule|room|hall|fees)\b',' ',q)
    q=re.sub(r'\b(?:movie|film)\s+(?:tickets?|clubs?|ratings?|reviews?|awards?)\b',' ',q)
    q=re.sub(r'\bunrelated\s+to\s+(?:video|movie|film)\b',' ',q)
    # The action and object must co-occur. Standalone hardware memory, voice
    # settings and ordinary documents are not sufficient to route this pack.
    if not re.search(r'\b(video|movie|film|lecture|documentary|animation|narration)\b',q):
        return {'fired':False,'cards':[]}
    production_action=re.search(r'\b(make|making|build|building|create|creating|render|rendering|edit|editing|revise|revising|improve|fix|review|audit|produce|producing|prepare|teach|narrate|export|encode|encoding|animate|animating|check|deliver|delivery)\b',q)
    gpu_use=re.search(r'\b(use|using)\b',q) and re.search(r'\b(gpu|cuda|nvenc)\b',q)
    if not (production_action or gpu_use):
        return {'fired':False,'cards':[]}
    # GPU use is an explicit owner requirement for suitable movie stages. A
    # producer need not know to ask about hardware to receive that requirement.
    domains={'intent','review','voice','delivery','compute'}
    if re.search(r'lecture|math|proof|geometry|geometric|slide|latex',q):
        domains.update(['geometry','design','timing'])
    if re.search(r'documentary|battle|history|historical|film|movie',q):
        domains.add('story')
    if re.search(r'gpu|cpu|quick|fast|render|slow|computer',q):
        domains.add('compute')
    cards=knowledge()
    # A bounded entry packet, with pointers to complete modules. It is not an
    # exhaustive keyword search; the caller deliberately asks filmbrain for depth.
    # A fixed first-two slice omitted the proof card on a real producer's
    # complete-proof request. Prioritize the explicitly requested operation,
    # then use the usual foundation cards to fill the remaining space.
    focus={
        'geometry':[(r'proof|lemma|theorem|deriv','proof'),
                    (r'high.dimension|projection|three.dimension|3d','dimensions'),
                    (r'numerical|dataset|measured data','data')],
        'voice':[(r'fast|brisk|pace|pauses?|speed','pace'),
                 (r'pronounc|spoken.math|proper names','pronunciation')],
        'story':[(r'repeat|reus|duplicate|unique|once','nonreuse'),
                 (r'realis|glitch|morph','realism'),
                 (r'battle|30.to.60','passages')],
        'review':[(r'final|whole|entire|watch','final'),
                  (r'listen|audio.only','audio'),
                  (r'stale|hash|receipt','receipts')],
        'compute':[(r'gpu|cuda|nvenc|restart','gpu'),
                   (r'gpu|cpu|cores?|terminal|affinity','background'),
                   (r'memory|ram|pressure|headroom','memory')],
    }
    chosen=[]
    for domain in [d[0] for d in DOMAINS if d[0] in domains]:
        candidates=[c for c in cards if c['domain']==domain]
        preferred=['owner.video.'+domain+'.'+slug
                   for pattern,slug in focus.get(domain,[]) if re.search(pattern,q)]
        if domain=='voice':
            preferred.insert(0,'owner.video.voice.microsoft_only')
        if domain=='compute':
            preferred.insert(0,'owner.video.compute.gpu')
        rank={cid:i for i,cid in enumerate(dict.fromkeys(preferred))}
        candidates.sort(key=lambda c:rank.get(c['id'],len(rank)))
        chosen.extend(candidates[:2])
    return {'fired':True,'domains':sorted(domains),'cards':chosen,
            'read_next':['modules/'+d+'.md' for d in sorted(domains)],
            'note':'These are task-relevant hints with evidence, not permission to override current owner instructions.'}

def main():
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    p=commands.add_parser('brief');p.add_argument('query')
    p=commands.add_parser('check');p.add_argument('manifest',type=Path)
    p.add_argument('--artifact-root',type=Path,help='Read local files and verify hashes; potentially large I/O, use current compute policy.')
    args=parser.parse_args()
    if args.command=='brief':
        result=brief(args.query)
    else:
        try:
            data=json.loads(args.manifest.read_text(encoding='utf-8'))
            result=validate(data,args.artifact_root)
        except (OSError,ValueError,TypeError,KeyError,AttributeError) as exc:
            result={'status':'BLOCKED','findings':[{'code':'MALFORMED_INPUT','scope':'manifest','detail':str(exc)}]}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 1 if result.get('status')=='BLOCKED' else 0

if __name__=='__main__':
    raise SystemExit(main())
