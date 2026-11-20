"""Evaluate real BGE retrieval on repository corpus; labels never enter inference."""
import asyncio
import argparse
import hashlib
import json
import os
import statistics
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DATASET=ROOT/'test-data/web-2026-10-31'
OUT=ROOT/'test-results/remaining-2026-11-19'


async def run(args):
    from app import rag,settings,store,ingestion,evidence_matching,retrieval_planner
    config=settings.defaults()
    config.embedding=settings.EmbeddingSettings(provider='local',model='BAAI/bge-m3',timeout_seconds=300)
    config.reranking=settings.RerankingSettings(provider='local',timeout_seconds=300)
    config.reranking.candidate_k=args.candidates
    config.answer_quality.context_neighbors=args.neighbors
    config.retrieval.cross_language=args.rewrite
    settings.active.set(config)
    if args.plans:
        previous=json.loads(Path(args.plans).read_text(encoding='utf-8'))
        plans={entry['pipeline']['query_expansion']['queries'][0]['query']:entry['pipeline']['query_expansion']
               for entry in previous['results']}
        async def replay(query,kb_ids,items):
            meta=plans[query]
            return meta['queries'],{**meta,'cache':'frozen-live-plan-replay'}
        retrieval_planner.expand=replay
    kid=store.execute('INSERT INTO knowledge_bases(name,description,visibility,status,created_at) VALUES(?,?,?,?,?)',
                      ('Real retrieval corpus','','private','active',store.now()))
    sources={}
    for path in sorted((DATASET/'corpus').iterdir()):
        if not path.is_file():continue
        sid=path.name.split('_')[0]
        did=ingestion.create_document(kid,path.name,path.suffix.lstrip('.'),'text',str(path))
        units=rag.parse_local(path,path.suffix.lstrip('.'));prepared=rag.prepare_index(units)
        await rag.cache_local_embeddings([chunk[3] for chunk in prepared['chunks']])
        store.execute('UPDATE documents SET sha256=? WHERE id=?',(hashlib.sha256(path.read_bytes()).hexdigest(),did))
        rag.commit_index(did,prepared);sources[did]=sid
        print('Indexed',sid,len(prepared['chunks']),flush=True)
    labels=[json.loads(line) for line in (DATASET/'labels/qa.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    records=[]
    for case in labels:
        for mode in args.modes:
            start=time.perf_counter()
            results,pipeline=await rag.retrieve(case['question'],[kid],mode,5,True,split_question=True)
            found={sources[item['document_id']] for item in results}
            quotes=[any(evidence_matching.locate(quote['quote'],item['text']) for item in results
                        if sources[item['document_id']]==quote['source_id']) for quote in case.get('evidence',[])]
            records.append({'question_id':case['id'],'mode':mode,'answerable':case['answerable'],
                'chunks':[{'source':sources[x['document_id']],'ordinal':x['ordinal'],'text':x['text']} for x in results],
                'expected_sources':case['source_ids'],'found_sources':sorted(found),
                'all_sources_hit':set(case['source_ids'])<=found if case['answerable'] else None,
                'quote_hits':quotes,'milliseconds':(time.perf_counter()-start)*1000,'pipeline':pipeline})
        print('Evaluated',case['id'],[x['all_sources_hit'] for x in records[-len(args.modes):]],flush=True)
    summary={}
    for mode in args.modes:
        group=[x for x in records if x['mode']==mode and x['answerable']]
        times=sorted(x['milliseconds'] for x in records if x['mode']==mode)
        summary[mode]={'answerable':len(group),'all_sources_hit':sum(x['all_sources_hit'] for x in group),
            'all_quotes_hit':sum(bool(x['quote_hits']) and all(x['quote_hits']) for x in group),
            'median_ms':statistics.median(times),'p95_ms':times[min(len(times)-1,int(len(times)*.95))]}
    OUT.mkdir(parents=True,exist_ok=True)
    calls=store.rows('SELECT kind,model,status,latency_ms,input_tokens,output_tokens FROM model_calls')
    (OUT/args.output).write_text(json.dumps({'summary':summary,'results':records,
        'paid_calls':sum(x['kind']=='query_rewrite' for x in calls),'model_calls':calls,
        'configuration':{'candidate_k':args.candidates,'context_neighbors':args.neighbors,'query_rewrite':args.rewrite,
                         'replayed_plans':args.plans}},ensure_ascii=False,indent=2),encoding='utf-8')
    print(summary,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--neighbors',type=int,default=0,choices=range(7))
    parser.add_argument('--candidates',type=int,default=20,choices=range(8,101))
    parser.add_argument('--output',default='real-retrieval.json')
    parser.add_argument('--rewrite',action='store_true')
    parser.add_argument('--plans',help='Replay frozen real query plans for a controlled retrieval comparison')
    parser.add_argument('--modes',nargs='+',choices=('vector','keyword','hybrid'),default=['vector','keyword','hybrid'])
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='rag-real-retrieval-') as folder:
        os.environ.update(PYTHON_DOTENV_DISABLED='1',RAG_DATA_DIR=folder,RAG_DATABASE_URL='',RAG_FILE_STORAGE='local',RAG_MIN_SIMILARITY='.3')
        if args.rewrite:
            from dotenv import dotenv_values
            values=dotenv_values(Path(__file__).resolve().parent/'.env')
            for key in ('KIMI_API_KEY','KIMI_BASE_URL','KIMI_MODEL'):
                if values.get(key):os.environ[key]=values[key]
        asyncio.run(run(args))
