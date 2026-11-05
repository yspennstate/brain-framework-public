import copy,hashlib,unittest
from datetime import datetime,timezone
from correction_ingestion_status import *

NOW=datetime(2026,9,8,11,20,tzinfo=timezone.utc)
def seal(r):
    r['digest_sha256']=hashlib.sha256(canonical_bytes({k:v for k,v in r.items() if k!='digest_sha256'})).hexdigest()
    return r
def fixture():
    return seal(dict(schema=SCHEMA,checked_at='2026-09-08T11:20:00Z',scope='selected-occurrences-only',
        coverage={'codex':'selected-only','claude':'not-checked'},source_review='coordinator-identified-human-events',
        rows=[dict(source_id='codex-rollout-v2:example',source_line=12,source_record_sha256='a'*64,
            text_sha256='b'*64,legacy_ledger='missing',framework_ingress='missing',correction_present='yes',
            candidate_sha256=None,disposition='pending',disposition_sha256=None)]))

class Tests(unittest.TestCase):
    def test_origins(self):
        for r,expected in [({},'unknown'),({'origin':'owner'},'owner'),({'origin':'automation','class_model':'mistake'},'automation'),
            ({'origin':'system'},'automation'),({'origin':'future'},'unknown'),([], 'unknown'),
            ({'origin':'owner','isCompactSummary':True},'automation'),({'origin':'owner','isMeta':True},'automation'),
            ({'origin':'owner','isVisibleInTranscriptOnly':True},'automation'),({'origin':'owner','isMeta':'false'},'unknown'),
            ({'origin':'owner','text':'I quoted [AGENT] here'},'owner')]:
            with self.subTest(r=r): self.assertEqual(recorded_origin(r),expected)
    def test_class_cannot_promote_origin(self):
        self.assertEqual(routed_class({'origin':'automation','class_model':'mistake'},['mistake','other']),'automation')
        self.assertEqual(routed_class({'class_model':'mistake'},['mistake','other']),'unknown')
        self.assertEqual(routed_class({'origin':'owner','class_model':'mistake'},['mistake','other']),'mistake')
    def test_valid_is_never_global_verified(self):
        s=validate_receipt(fixture(),now=NOW)
        self.assertEqual(s['overall_health'],'UNVERIFIED'); self.assertEqual(s['unresolved'],1)
    def test_tamper(self):
        r=fixture();r['rows'][0]['framework_ingress']='matched'
        self.assertRaises(ValueError,validate_receipt,r,now=NOW)
    def test_adversarial_resealed(self):
        variants=[('scope','all-history'),('schema','v2'),('coverage',{'codex':'complete','claude':'complete'}),
                  ('source_review','authenticated'),('checked_at','2027-01-01T00:00:00Z'),
                  ('checked_at','2026-09-01T00:00:00Z'),('checked_at','2026-09-08T11:20:00'),('rows',[])]
        for k,v in variants:
            with self.subTest(k=k,v=v):
                r=fixture();r[k]=v;seal(r);self.assertRaises(ValueError,validate_receipt,r,now=NOW)
    def test_invalid_rows(self):
        for k,v in [('source_line',True),('source_line',0),('source_id','hook'),('text_sha256','fake'),
                    ('framework_ingress','verified'),('correction_present','improve'),('disposition','resolved'),
                    ('disposition','not-a-correction'),('candidate_sha256','x'*64)]:
            with self.subTest(k=k):
                r=fixture();r['rows'][0][k]=v;seal(r);self.assertRaises(ValueError,validate_receipt,r,now=NOW)
    def test_duplicate(self):
        r=fixture();r['rows']*=2;seal(r);self.assertRaises(ValueError,validate_receipt,r,now=NOW)
    def test_fake_verified_field(self):
        r=fixture();r['verified']=True;seal(r);self.assertRaises(ValueError,validate_receipt,r,now=NOW)
    def test_bound_resolution_still_limited(self):
        r=fixture();r['rows'][0].update(framework_ingress='matched',candidate_sha256='c'*64,
            disposition='resolved',disposition_sha256='d'*64);seal(r)
        s=validate_receipt(r,now=NOW);self.assertEqual(s['unresolved'],0);self.assertEqual(s['overall_health'],'UNVERIFIED')
    def test_summary_fail_closed(self):
        for r in (None,{},[],{'schema':SCHEMA}):
            self.assertIn('UNVERIFIED',summarize_receipt(r,now=NOW))

if __name__=='__main__':unittest.main()
