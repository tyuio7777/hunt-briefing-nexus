"""Atomic generations. Only this gate publishes the manifest; AI writes proposals only."""
from __future__ import annotations
import argparse,json,os,re,shutil
from datetime import datetime,timezone,timedelta
from pathlib import Path
from . import contracts,engine,ingest,providers,vocabulary,originals
from .model import InvalidData,digest,encoded,instant,stamp,validate_graph


def read(path,default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_bytes(encoded(value)+b'\n');temp.replace(path)


def state_at(root):
    pointer=read(root/'data/state.json')
    if pointer:
        rev=pointer['revision']
        if not re.fullmatch('[a-f0-9]{64}',rev):raise InvalidData('Invalid state pointer')
        state=read(root/f'data/states/{rev}.json')
        if digest(state)!=rev:raise InvalidData('State hash mismatch')
        return state
    return engine.initial_state(read(root/'config/seed-vocabulary.json',[]))


def publish(root,state,now,notes):
    graph=engine.project(state,now,notes)
    # An interrupted writer never damages the previously published generation.
    state_rev=digest(state);write(root/f'data/states/{state_rev}.json',state)
    data=encoded(graph)+b'\n';graph_sha=digest(data)
    path=f'data/releases/{graph_sha}.json';write(root/path,graph)
    manifest={'schemaVersion':2,'status':'ready' if any(n['kind']=='topic' for n in graph['nodes']) else 'pending',
              'generatedAt':stamp(now),'revision':graph['revision'],'stateRevision':state_rev,
              'graphPath':path,'sha256':graph_sha,'bytes':len(data),'sourceCount':len(state['sources']),
              'topicCount':sum(n['kind']=='topic' for n in graph['nodes']),'pendingReview':graph['reviewPending']}
    # Both pointers become visible together in the single Git commit.
    write(root/'data/state.json',{'revision':state_rev})
    write(root/'data/manifest.json',manifest)
    return graph


def run(root,now,collect=False,api_limit=0):
    state=state_at(root);errors=[]
    if collect:
        try:
            sources,failures=ingest.collect(now);engine.add_sources(state,sources);errors.extend(failures)
            linked,failures=originals.collect(state,now);engine.add_sources(state,linked);errors.extend(failures)
        except (InvalidData,ValueError,OSError,KeyError) as exc:
            errors.append({'stage':'collect','error':type(exc).__name__})
    pending=engine.jobs(state)
    # Changed extraction explicitly reopens a previously rejected job. Existing accepted IDs stay stable.
    for path in sorted((root/'inbox/extractions').glob('*.json')) if (root/'inbox/extractions').exists() else []:
        try:
            row=read(path); job_id=row['jobId']; old=state['receipts'].get(job_id)
            contracts.validate(row['extraction'],contracts.EXTRACTION)
            if old and old.get('extractionHash')!=digest(row['extraction']):
                del state['receipts'][job_id]
        except (InvalidData,ValueError,KeyError): pass
    pending=engine.jobs(state);reviews=[]
    for job in pending:
        extraction_path=root/f'inbox/extractions/{job["id"]}.json'
        review_path=root/f'inbox/reviews/{job["id"]}.json'
        try:
            proposal=read(extraction_path)
            if proposal is None and api_limit>0:
                result=providers.call_json(providers.EXTRACTION_PROMPT,{'source':job,'catalog':engine.catalog(state),'legacyClassificationHints':read(root/'config/legacy-topics-reference.json',{})},contracts.EXTRACTION)
                proposal={'jobId':job['id'],'sourceHash':job['sourceHash'],'extraction':result}
                write(extraction_path,proposal);api_limit-=1
                review=providers.call_json(providers.REVIEW_PROMPT,{'source':job,'catalog':engine.catalog(state),'legacyClassificationHints':read(root/'config/legacy-topics-reference.json',{}),'extraction':result},contracts.REVIEW)
                write(review_path,{'jobId':job['id'],'sourceHash':job['sourceHash'],'reviewedExtractionHash':digest(result),'review':review,
                                  'provider':os.environ['NEXUS_AI_PROVIDER'],'model':os.environ['NEXUS_AI_MODEL']})
            if proposal is None: continue
            if proposal['jobId']!=job['id'] or proposal['sourceHash']!=job['sourceHash']:raise InvalidData('Stale extraction')
            extraction=proposal['extraction'];contracts.validate(extraction,contracts.EXTRACTION)
            review=read(review_path)
            request={'job':job,'extraction':extraction,'reviewedExtractionHash':digest(extraction)}
            if review is None or review.get('reviewedExtractionHash')!=digest(extraction):
                reviews.append(request);continue
            package={**review,'extraction':extraction}
            state,changed=engine.apply_package(state,job,package,now)
            if changed:state['receipts'][job['id']]['extractionHash']=digest(extraction)
        except (InvalidData,ValueError,KeyError,OSError) as exc:
            errors.append({'stage':'proposal','jobId':job['id'],'error':str(exc)[:180]})
    # Vocabulary audit is a separate two-pass job, not a rewrite of the legacy dictionaries.
    audit_id=now.strftime('%G-W%V')
    vocab=read(root/'inbox/vocabulary/proposal.json')
    if vocab:
        try:
            contracts.validate(vocab,contracts.VOCABULARY)
            reviewed=read(root/'inbox/vocabulary/review.json',{})
            if (reviewed.get('proposalHash')==digest(vocab) and reviewed.get('auditId')==audit_id
                and (state.get('vocabularyProposalHash')!=digest(vocab) or state.get('vocabularyAuditId')!=audit_id)):
                decisions=reviewed.get('decisions')
                expected={digest(c) for c in vocab['decisions']}
                if not isinstance(decisions,dict) or set(decisions)!=expected or any(v not in ('approve','reject') for v in decisions.values()):
                    raise InvalidData('Every vocabulary candidate needs an exact second-review decision')
                state=vocabulary.apply_review(state,vocab,now,decisions=reviewed.get('decisions',{}))
                state['vocabularyProposalHash']=digest(vocab)
                state['vocabularyAuditId']=audit_id
        except (InvalidData,ValueError,KeyError) as exc: errors.append({'stage':'vocabulary','error':str(exc)[:180]})
    pending=engine.jobs(state)
    for job in pending:write(root/f'data/work/jobs/{job["id"]}.json',job)
    for request in reviews:write(root/f'data/work/reviews/{request["job"]["id"]}.json',request)
    last=state.get('vocabularyReviewedAt')
    due=not last or now-instant(last)>=timedelta(days=7)
    write(root/'data/work/queue.json',{'generatedAt':stamp(now),'extractions':[{'id':j['id'],'path':f'data/work/jobs/{j["id"]}.json'} for j in pending if not (root/f'inbox/extractions/{j["id"]}.json').exists()],
        'reviews':[{'id':r['job']['id'],'path':f'data/work/reviews/{r["job"]["id"]}.json'} for r in reviews],
        'vocabularyDue':due,'vocabularyAuditId':audit_id,'vocabularyProposalHash':digest(vocab) if vocab else '',
        'vocabularyDecisions':[{ 'hash':digest(c),'candidate':c} for c in (vocab or {}).get('decisions',[])],
        'errors':errors})
    write(root/'data/work/catalog.json',engine.catalog(state))
    write(root/'data/work/pending-review.json',list(state['reviewQueue'].values()))
    notes=[]
    if pending:notes.append(f'AI 정리·검토 대기 {len(pending)}구간 · 완료된 자료부터 표시합니다.')
    if errors:notes.append(f'갱신 확인 필요 {len(errors)}건 · 마지막 정상 지식은 유지합니다.')
    graph=publish(root,state,now,notes)
    return {'sources':len(state['sources']),'topics':sum(n['kind']=='topic' for n in graph['nodes']),'pending':len(pending),'errors':errors}


def rollback(root,revision,now):
    if not re.fullmatch('[a-f0-9]{64}',revision):raise InvalidData('Invalid revision')
    state=read(root/f'data/states/{revision}.json')
    if state is None or digest(state)!=revision:raise InvalidData('Snapshot hash mismatch')
    state['audit'].append({'action':'rollback','toRevision':revision,'at':stamp(now)})
    return publish(root,state,now,['선택한 정상 기록으로 복원했습니다. 이후 자료는 다시 검토합니다.'])


def main():
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['run','collect','rollback']);parser.add_argument('--root',default='.');parser.add_argument('--revision');parser.add_argument('--api-limit',type=int,default=0)
    args=parser.parse_args();root=Path(args.root).resolve();now=datetime.now(timezone.utc)
    if args.command=='rollback':rollback(root,args.revision or '',now);print('Restored reviewed snapshot')
    else:print(json.dumps(run(root,now,collect=args.command=='collect',api_limit=max(0,args.api_limit)),ensure_ascii=False))

if __name__=='__main__':main()
