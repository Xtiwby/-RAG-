"""Versioned activation and resumable real QA checks; no old report rewrites."""
import argparse
import concurrent.futures
import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path
import check_web_live as helper

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'test-results/quality-v010-2026-11-01'
OUT.mkdir(parents=True,exist_ok=True)
RUN='final'

def save(name,value):(OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def read(name,default=None):return json.loads((OUT/name).read_text(encoding='utf-8')) if (OUT/name).exists() else default

def configure():
    client=helper.client()
    try:
        state=helper.api(client,'GET','/api/settings')
        if not read('settings-before.json'):save('settings-before.json',state)
        state['settings']['answer_quality'].update(enabled=True,repair=True,context_neighbors=4)
        result=helper.api(client,'PUT','/api/settings',json={'settings':state['settings'],'expected_revision':state['revision']})
        save('settings-after.json',result);print('Enabled revision',result['revision'])
    finally:helper.api(client,'POST','/api/auth/logout');client.close()

def question(qid):
    # Labels stay in the scorer process. Only the original question enters QA.
    cases=[json.loads(s) for s in (ROOT/'test-data/web-2026-10-31/labels/qa.jsonl').read_text(encoding='utf-8').splitlines()]
    case=next(c for c in cases if c['id']==qid)
    setup=json.loads((ROOT/'test-results/web-live-2026-11-01/setup.json').read_text(encoding='utf-8'))
    client=helper.client();events=[];started=time.perf_counter()
    try:
        with client.stream('POST','/api/qa/stream',json={'query':case['question'],
            'knowledge_base_ids':list(setup['knowledge_bases'].values()),'mode':'hybrid','top_k':5,'rerank':True}) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line.startswith('data:'):events.append(json.loads(line[5:]))
        meta=next(e for e in events if e['type']=='meta')
        helper.api(client,'PATCH',f"/api/conversations/{meta['conversation_id']}",json={'title':f'v0.10 质量复测 {qid} · '+case['question'][:50]})
        conversation=helper.api(client,'GET',f"/api/conversations/{meta['conversation_id']}")
        result={'question_id':qid,'question':case['question'],'expected_for_human_review':case['expected_answer'],
            'answerable':case['answerable'],'elapsed_ms':round((time.perf_counter()-started)*1000),
            'conversation':conversation,'events':events,'grading':'人工复核，不以模型自评或词项命中自动判正确'}
        (OUT/RUN).mkdir(exist_ok=True)
        save(RUN+'/qa-'+qid+'.json',result)
        message=conversation['messages'][-1]
        print(qid,message['status'],message['content'],json.dumps(message.get('coverage'),ensure_ascii=False),flush=True)
        return result
    finally:helper.api(client,'POST','/api/auth/logout');client.close()

def preserve():
    backup=ROOT/'backend/data/backups/pre-quality-v010-20261101.db'
    live=ROOT/'backend/data/rag_knowledge.db';checks={}
    with closing(sqlite3.connect(backup.as_uri()+'?mode=ro',uri=True)) as old,closing(sqlite3.connect(live.as_uri()+'?mode=ro',uri=True)) as new:
        for table in ('report_tasks','report_sections','report_templates','report_releases','documents','chunks','users','roles','kb_grants','conversations','messages'):
            cols=[r[1] for r in old.execute(f'PRAGMA table_info({table})') if r[1]!='last_login']
            before=old.execute(f"SELECT rowid,{','.join(cols)} FROM {table} ORDER BY rowid").fetchall()
            ids=[r[0] for r in before]
            after=new.execute(f"SELECT rowid,{','.join(cols)} FROM {table} WHERE rowid IN ({','.join('?' for _ in ids)}) ORDER BY rowid",ids).fetchall() if ids else []
            checks[table+'_old_rows_unchanged']=before==after
        checks['integrity']=new.execute('PRAGMA integrity_check').fetchone()[0]
    save('preservation.json',checks);assert all(v for k,v in checks.items() if k!='integrity') and checks['integrity']=='ok'
    print(checks)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['configure','qa','preserve'])
    parser.add_argument('--ids',default='Q001,Q005,Q007,Q025,Q027,Q031,Q004,Q016');parser.add_argument('--workers',type=int,choices=[1,2],default=2)
    parser.add_argument('--run',choices=['final','trial2'],default='final')
    args=parser.parse_args()
    RUN=args.run
    if args.phase=='configure':configure()
    elif args.phase=='preserve':preserve()
    else:
        pending=[qid for qid in args.ids.split(',') if not read(RUN+'/qa-'+qid+'.json')]
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            for result in pool.map(question,pending):pass
        save(RUN+'/qa.json',[read(RUN+'/qa-'+qid+'.json') for qid in args.ids.split(',')])
