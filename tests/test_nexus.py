import unittest,tempfile,copy
from pathlib import Path
from datetime import datetime,timezone,timedelta
from nexus import engine,ingest,pipeline
from nexus.model import *
NOW=datetime(2026,10,28,tzinfo=timezone.utc)
TEXT='Maxim M1895 was introduced in Event A on 2026-09-01. Event A ended on 2026-09-30. Event B has a different weapon. Damage is 100.'
def fixture():
 s=ingest.source('https://example.com/source','Test',TEXT,'2026-09-01T00:00:00Z',NOW)
 state=engine.initial_state([]);engine.add_sources(state,[s]);job=engine.jobs(state)[0];q=[{'sourceId':s['id'],'quote':TEXT}]
 ex={'entities':[{'key':'weapon','label':'Maxim M1895','kind':'weapon','scope':'','group':'equipment','entityId':'','aliases':[],'evidence':copy.deepcopy(q)}],
 'assertions':[{'key':'intro','subject':'weapon','aspect':'도입 이벤트','value':'Event A','statement':'Maxim M1895는 Event A에서 도입됐다.','applicability':'historical','certainty':'attributed','effectiveFrom':'2026-09-01','effectiveTo':'2026-09-30','dateEvidence':copy.deepcopy(q),'supersedes':[],'evidence':copy.deepcopy(q)}],'relations':[]}
 return state,job,ex

def package(job,ex):
 return {'jobId':job['id'],'sourceHash':job['sourceHash'],'extraction':ex,'reviewedExtractionHash':digest(ex),'review':{'decisions':[{'key':c['key'],'supported':True,'identityCorrect':True,'temporalCorrect':True,'reason':'Literal context checked'} for group in ex.values() for c in group]}}

class NexusTests(unittest.TestCase):
 def test_private_source_rejected(self):
  st,j,ex=fixture();s=copy.deepcopy(st['sources'][j['sourceId']]);s['visibility']='private'
  with self.assertRaises(InvalidData):engine.add_sources(st,[s])
 def test_unsupported_quote_held(self):
  st,j,ex=fixture();ex['assertions'][0]['evidence'][0]['quote']='This quotation does not occur in the source.'
  new,_=engine.apply_package(st,j,package(j,ex),NOW)
  self.assertFalse(new['assertions']);self.assertTrue(new['reviewQueue']);self.assertFalse(st['assertions'])
 def test_review_hash_binding(self):
  st,j,ex=fixture();p=package(j,ex);p['reviewedExtractionHash']='0'*64
  with self.assertRaises(InvalidData):engine.apply_package(st,j,p,NOW)
 def test_reviewer_rejects_wrong_event(self):
  st,j,ex=fixture();p=package(j,ex);p['review']['decisions'][1]['temporalCorrect']=False
  new,_=engine.apply_package(st,j,p,NOW);self.assertFalse(new['assertions']);self.assertTrue(new['entities'])
 def test_old_event_remains_old(self):
  st,j,ex=fixture();new,_=engine.apply_package(st,j,package(j,ex),NOW)
  a=next(iter(new['assertions'].values()));self.assertEqual(lifecycle(a,NOW),'ended');self.assertFalse(visible_now(a,NOW))
  self.assertFalse(next(n for n in engine.project(new,NOW)['nodes'] if n['kind']=='topic')['recent'])
 def test_reprints_do_not_reset_first_publication(self):
  st,j,ex=fixture();st,_=engine.apply_package(st,j,package(j,ex),NOW)
  new=ingest.source('https://example.com/reprint','Reprint',TEXT,stamp(NOW),NOW);engine.add_sources(st,[new]);j2=engine.jobs(st)[0]
  for c in ex['entities']+ex['assertions']:
   for field in ['evidence','dateEvidence']:
    for q in c.get(field,[]):q['sourceId']=new['id']
  st,_=engine.apply_package(st,j2,package(j2,ex),NOW)
  self.assertEqual(len(st['entities']),1);self.assertEqual(len(st['assertions']),1)
  self.assertEqual(next(iter(st['assertions'].values()))['firstPublishedAt'],'2026-09-01T00:00:00Z')
 def test_stable_identity_and_idempotence(self):
  st,j,ex=fixture();new,_=engine.apply_package(st,j,package(j,ex),NOW);again,changed=engine.apply_package(new,j,package(j,ex),NOW)
  self.assertFalse(changed);self.assertEqual(new,again)
 def test_same_name_different_event_edition(self):
  st,j,ex=fixture();ex['entities'][0]['scope']='2026';st,_=engine.apply_package(st,j,package(j,ex),NOW)
  other=ingest.source('https://example.com/old','Old',TEXT,'2025-09-01T00:00:00Z',NOW);engine.add_sources(st,[other]);j2=engine.jobs(st)[0]
  ex['entities'][0]['scope']='2025';ex['assertions']=[];ex['entities'][0]['evidence']=[{'sourceId':other['id'],'quote':TEXT}]
  st,_=engine.apply_package(st,j2,package(j2,ex),NOW);self.assertEqual(len(st['entities']),2)
 def test_evergreen_current_not_age_expired(self):
  self.assertTrue(visible_now({'state':'active','applicability':'current','certainty':'attributed','firstPublishedAt':'2020-01-01T00:00:00Z'},NOW))
 def test_unresolved_is_hidden_not_disproven(self):
  a={'state':'active','applicability':'unknown','certainty':'unconfirmed','firstPublishedAt':'2020-01-01T00:00:00Z'}
  self.assertEqual(lifecycle(a,NOW),'unresolved');self.assertFalse(visible_now(a,NOW))
 def test_manifest_and_rollback(self):
  st,j,ex=fixture();new,_=engine.apply_package(st,j,package(j,ex),NOW)
  with tempfile.TemporaryDirectory() as d:
   r=Path(d);pipeline.publish(r,new,NOW,[]);m=pipeline.read(r/'data/manifest.json')
   self.assertEqual(digest((r/m['graphPath']).read_bytes()),m['sha256']);self.assertEqual(len((r/m['graphPath']).read_bytes()),m['bytes'])
   pipeline.rollback(r,m['stateRevision'],NOW+timedelta(days=1));self.assertEqual(pipeline.state_at(r)['audit'][-1]['action'],'rollback')
 def test_new_topic_not_in_seed(self):
  st,j,ex=fixture();new,_=engine.apply_package(st,j,package(j,ex),NOW);self.assertEqual(len(new['entities']),1)
 def test_semantic_edges_require_literal_evidence(self):
  st,j,ex=fixture();new,_=engine.apply_package(st,j,package(j,ex),NOW);g=engine.project(new,NOW)
  g['edges'][0]['type']='introduced_in';g['edges'][0]['quotations']=[]
  with self.assertRaises(InvalidData):validate_graph(g)
 def test_arbitrary_private_url_rejected(self):
  for url in ['http://example.com','https://127.0.0.1/a','https://user:secret@example.com/a','https://host.local/a']:
   with self.assertRaises(InvalidData):public_url(url)
 def test_two_phase_queue(self):
  st,j,ex=fixture()
  with tempfile.TemporaryDirectory() as d:
   r=Path(d);pipeline.publish(r,st,NOW,[])
   pipeline.write(r/f'inbox/extractions/{j["id"]}.json',{'jobId':j['id'],'sourceHash':j['sourceHash'],'extraction':ex})
   pipeline.run(r,NOW);q=pipeline.read(r/'data/work/queue.json');self.assertEqual(len(q['reviews']),1);self.assertFalse(pipeline.state_at(r)['assertions'])
   p=package(j,ex);p.pop('extraction');pipeline.write(r/f'inbox/reviews/{j["id"]}.json',p);pipeline.run(r,NOW)
   self.assertEqual(len(pipeline.state_at(r)['assertions']),1)
 def test_old_snapshot_stays_valid_after_new_generation(self):
  st,j,ex=fixture()
  with tempfile.TemporaryDirectory() as d:
   r=Path(d);pipeline.publish(r,st,NOW,[]);m=pipeline.read(r/'data/manifest.json');original=(r/m['graphPath']).read_bytes()
   st,_=engine.apply_package(st,j,package(j,ex),NOW);pipeline.publish(r,st,NOW,[])
   self.assertEqual((r/m['graphPath']).read_bytes(),original)
if __name__=='__main__':unittest.main()
