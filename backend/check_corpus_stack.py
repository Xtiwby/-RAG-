"""Representative public-corpus acceptance inside the disposable real stack."""
import json
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATASET=ROOT/'test-data/web-2026-10-31'
OUT=ROOT/'test-results/remaining-2026-11-19'


def run(client,headers):
    from app import retrieval_planner,evidence_matching,rag
    current=client.get('/api/settings',headers=headers).json()
    current['settings']['reranking']['candidate_k']=40
    current['settings']['answer_quality']['context_neighbors']=6
    response=client.put('/api/settings',headers=headers,json={'settings':current['settings'],'expected_revision':current['revision']})
    response.raise_for_status()
    response=client.post('/api/knowledge-bases',headers=headers,json={'name':'Representative public corpus'})
    response.raise_for_status();kid=response.json()['id']
    sources={};chunks=0;started=time.perf_counter()
    for path in sorted((DATASET/'corpus').iterdir()):
        if not path.is_file():continue
        response=client.post('/api/documents',headers=headers,data={'knowledge_base_id':kid},files={'file':(path.name,path.read_bytes())})
        response.raise_for_status();did=response.json()['id']
        for _ in range(1800):
            doc=client.get(f'/api/documents/{did}',headers=headers).json()
            if doc['status'] not in ('uploading','processing'):break
            time.sleep(.1)
        assert doc['status']=='ready',doc
        sources[did]=path.name.split('_')[0];chunks+=len(doc['chunks'])
        print('Real stack corpus indexed',sources[did],len(doc['chunks']),flush=True)
    ingestion_seconds=time.perf_counter()-started
    previous=json.loads((OUT/'real-retrieval-before-context.json').read_text(encoding='utf-8'))
    plans={rag.preprocess(record['pipeline']['query_expansion']['queries'][0]['query']):record['pipeline']['query_expansion'] for record in previous['results']}
    original=retrieval_planner.expand
    async def replay(query,kb_ids,items):
        meta=plans[rag.preprocess(query)]
        return meta['queries'],{**meta,'cache':'frozen-live-plan-replay'}
    retrieval_planner.expand=replay
    try:
        labels=[json.loads(line) for line in (DATASET/'labels/qa.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
        records=[]
        def search(query,mode):
            response=client.post('/api/search',headers=headers,json={'query':query,'knowledge_base_ids':[kid],
                                    'mode':mode,'top_k':5,'rerank':True})
            response.raise_for_status();result=response.json()
            assert result['pipeline'].get('search_index')=='postgresql-fts-pgvector',result['pipeline']
            return result
        for case in labels:
            for mode in ('vector','keyword','hybrid'):
                start=time.perf_counter();result=search(case['question'],mode);found={sources[x['document_id']] for x in result['results']}
                quote_hits=[any(evidence_matching.locate(q['quote'],item['text']) for item in result['results']
                               if sources[item['document_id']]==q['source_id']) for q in case.get('evidence',[])]
                records.append({'id':case['id'],'mode':mode,'answerable':case['answerable'],'expected_sources':case['source_ids'],
                    'found_sources':sorted(found),'all_sources_hit':set(case['source_ids'])<=found if case['answerable'] else None,
                    'quote_hits':quote_hits,'milliseconds':(time.perf_counter()-start)*1000,'pipeline':result['pipeline']})
            print('Real stack corpus searched',case['id'],flush=True)
        summary={}
        for mode in ('vector','keyword','hybrid'):
            group=[x for x in records if x['mode']==mode and x['answerable']]
            times=sorted(x['milliseconds'] for x in records if x['mode']==mode)
            summary[mode]={'answerable':len(group),'all_sources_hit':sum(x['all_sources_hit'] for x in group),
                          'median_ms':statistics.median(times),'p95_ms':times[int(len(times)*.95)]}
        representative=[x['question'] for x in labels if x['id'] in ('Q001','Q002','Q007','Q009','Q024','Q025','Q005','Q031')]
        def sample(index):
            start=time.perf_counter();search(representative[index%len(representative)],'hybrid')
            return (time.perf_counter()-start)*1000
        start=time.perf_counter()
        with ThreadPoolExecutor(max_workers=4) as pool:samples=list(pool.map(sample,range(40)))
        result={'documents':len(sources),'chunks':chunks,'ingestion_seconds':ingestion_seconds,'summary':summary,'results':records,
                'concurrency':{'workers':4,'requests':len(samples),'failures':0,'seconds':time.perf_counter()-start,
                               'median_ms':statistics.median(samples),'p95_ms':sorted(samples)[37]},'paid_calls':0}
        (OUT/'real-postgres-corpus.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        assert all(group['all_sources_hit']==group['answerable'] for group in summary.values()),'Real PostgreSQL corpus source recall failed; see result'
        return result
    finally:
        retrieval_planner.expand=original
