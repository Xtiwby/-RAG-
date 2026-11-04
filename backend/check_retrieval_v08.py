"""Repeat the unchanged external web benchmark against v0.8, without re-ingest.

Only the new retrieval flag is enabled through the versioned settings API.
Creates explicitly named test conversations/report drafts; never edits old ones.
"""
import argparse,hashlib,json,re,sqlite3,time
from pathlib import Path
import check_web_live as helper

ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT/'test-results/web-live-2026-11-01'
OUT=ROOT/'test-results/retrieval-v08-2026-11-01'
OUT.mkdir(parents=True,exist_ok=True)
DATASET=ROOT/'test-data/web-2026-10-31'
state=json.loads((OLD/'setup.json').read_text(encoding='utf-8'))
cases=[json.loads(x) for x in (DATASET/'labels/qa.jsonl').read_text(encoding='utf-8').splitlines()]
reverse={did:sid for sid,did in state['documents'].items()}
api=helper.api
def save(name,data):(OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
def read(name,default=None):return json.loads((OUT/name).read_text(encoding='utf-8')) if (OUT/name).exists() else default
def configure(c):
    cfg=api(c,'GET','/api/settings')
    if not read('settings-before.json'):save('settings-before.json',cfg)
    if not cfg['settings']['retrieval']['cross_language']:
        cfg['settings']['retrieval']['cross_language']=True
        cfg=api(c,'PUT','/api/settings',json={'settings':cfg['settings'],'expected_revision':cfg['revision']})
    save('settings-after.json',cfg)
def retrieval(c):
    results=read('retrieval.json',[]);done={(r['question_id'],r['mode']) for r in results}
    for case in cases:
        for mode in ('vector','keyword','hybrid'):
            if (case['id'],mode) in done:continue
            body={'query':case['question'],'mode':mode,'knowledge_base_ids':list(state['knowledge_bases'].values()),'top_k':5,'rerank':True}
            t=time.perf_counter();response=api(c,'POST','/api/search',json=body)
            got={reverse.get(x['document_id'],'UNKNOWN') for x in response['results']};expected=set(case['source_ids'])
            norm=lambda t:re.sub(r'\s+','',t).casefold()
            quotes=[any(norm(e['quote']) in norm(r['text']) for r in response['results'] if reverse.get(r['document_id'])==e['source_id']) for e in case.get('evidence',[])]
            results.append({'question_id':case['id'],'mode':mode,'request':body,'response':response,'answerable':case['answerable'],
                'expected_sources':sorted(expected),'retrieved_sources':sorted(got),
                'all_expected_source_hit':expected<=got if expected else None,'any_expected_source_hit':bool(expected&got) if expected else None,
                'gold_quote_hits':quotes,'latency_ms':round((time.perf_counter()-t)*1000)})
            save('retrieval.json',results)
        entries=[r for r in results if r['question_id']==case['id']]
        print(case['id'],[(r['mode'],r['all_expected_source_hit'],r['retrieved_sources'],r['response']['pipeline'].get('query_expansion',{}).get('status')) for r in entries],flush=True)
    summary={}
    for mode in ('vector','keyword','hybrid'):
        group=[r for r in results if r['mode']==mode and r['answerable']]
        summary[mode]={'answerable_questions':len(group),'all_sources_hit':sum(r['all_expected_source_hit'] for r in group),
            'any_source_hit':sum(r['any_expected_source_hit'] for r in group),
            'all_exact_quotes_hit':sum(bool(r['gold_quote_hits']) and all(r['gold_quote_hits']) for r in group)}
    save('retrieval-summary.json',summary);print(summary,flush=True)
def report(c):
    original=json.loads((OLD/'report-generated.json').read_text(encoding='utf-8'))
    params={k:original[k] for k in ('title','template_id','knowledge_base_id','requirements','workflow_version')}
    if not read('report-created.json'):
        # Exact old title/requirements/template for preflight comparison.
        check=api(c,'POST','/api/reports/preflight',json=params);save('report-preflight.json',check)
        print('Preflight',[(s['title'],[reverse.get(e['document_id']) for e in s['evidence']]) for s in check['sections']],flush=True)
        created=api(c,'POST','/api/report-tasks',json={**params,'preflight_id':check['preflight_id'],'allow_missing':True})
        save('report-created.json',created)
    created=read('report-created.json');job=helper.wait_job(c,created['job_id']);save('report-job.json',job)
    rid=created['id'];detail=api(c,'GET',f'/api/reports/{rid}');save('report-generated.json',detail)
    if not read('regenerate-job.json') and detail['sections'] and job['status'] in ('completed','needs_review'):
        sec=detail['sections'][1]
        r=api(c,'POST',f"/api/report-tasks/{rid}/sections/{sec['id']}/regenerate",json={'expected_revision':sec['revision'],
            'instructions':'只写一句pypdf不是OCR软件，并提供依据。','preview':True})
        save('regenerate-job.json',r)
    if read('regenerate-job.json'):
        helper.wait_job(c,read('regenerate-job.json')['job_id'])
    final=api(c,'GET',f'/api/reports/{rid}');save('report-regenerate-preview.json',final)
    assert all(a['content']==b['content'] and a['revision']==b['revision'] for a,b in zip(detail['sections'],final['sections']))
    print('Report',rid,[(s['title'],s['verification']['state'],s['content']) for s in detail['sections']],flush=True)
    print('Regenerate candidate sources',[[reverse.get(e['document_id']) for e in candidate['citations']] for candidate in final['candidates']],flush=True)
def qa(c,ids):
    results=read('qa.json',[]);done={r['question_id'] for r in results};by_id={q['id']:q for q in cases}
    for qid in ids.split(','):
        if qid in done:continue
        body={'query':by_id[qid]['question'],'mode':'hybrid','knowledge_base_ids':list(state['knowledge_bases'].values()),'top_k':5,'rerank':True}
        events=[];t=time.perf_counter();print('QA starting',qid,flush=True)
        with c.stream('POST','/api/qa/stream',json=body) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if line.startswith('data:'):events.append(json.loads(line[5:]))
        cid=next(e['conversation_id'] for e in events if e.get('conversation_id'))
        api(c,'PATCH',f'/api/conversations/{cid}',json={'title':f'v0.8 召回复测 {qid} · '+by_id[qid]['question'][:60]})
        conv=api(c,'GET',f'/api/conversations/{cid}')
        results.append({'question_id':qid,'request':body,'case':by_id[qid],'conversation':conv,'events':events,'latency_ms':round((time.perf_counter()-t)*1000)})
        save('qa.json',results);print('QA done',qid,conv['messages'][-1]['status'],conv['messages'][-1]['content'],flush=True)
def final(c):
    backup=ROOT/'backend/data/backups/pre-retrieval-v08-20261101.db'
    with sqlite3.connect(backup.as_uri()+'?mode=ro',uri=True) as before,sqlite3.connect((ROOT/'backend/data/rag_knowledge.db').as_uri()+'?mode=ro',uri=True) as after:
        checks={}
        for table in ('report_tasks','report_sections','report_templates','report_releases','documents','chunks','users','roles','kb_grants'):
            # Compare old rows/columns, ignoring migration-added default columns and login timestamp.
            cols=[r[1] for r in before.execute(f'PRAGMA table_info({table})') if r[1]!='last_login']
            old=before.execute(f"SELECT {','.join(cols)} FROM {table} ORDER BY rowid").fetchall()
            if 'id' in cols:
                index=cols.index('id');ids=[r[index] for r in old]
                current=after.execute(f"SELECT {','.join(cols)} FROM {table} WHERE id IN ({','.join('?' for _ in ids)}) ORDER BY rowid",ids).fetchall() if ids else []
            else:current=after.execute(f"SELECT {','.join(cols)} FROM {table} ORDER BY rowid").fetchall()
            checks[table+'_old_rows_unchanged']=old==current
        checks['db_integrity']=after.execute('PRAGMA integrity_check').fetchone()[0]
    save('preservation.json',checks);save('health.json',api(c,'GET','/api/health'));print(checks,flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['configure','retrieval','report','qa','final']);parser.add_argument('--ids',default='Q001,Q004,Q005,Q024,Q025,Q027,Q031,Q007,Q016')
    args=parser.parse_args();c=helper.client()
    try:
        if args.phase=='configure':configure(c)
        elif args.phase=='retrieval':retrieval(c)
        elif args.phase=='report':report(c)
        elif args.phase=='qa':qa(c,args.ids)
        else:final(c)
    finally:c.post('/api/auth/logout');c.close()
