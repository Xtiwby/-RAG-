"""Real BGE validation against an isolated DB snapshot; no paid LLM calls.

Creates only result artifacts and disposable local snapshots, never edits live
documents, labels, conversations or reports. Use --warm-live to copy verified
embedding cache rows only after the benchmark has completed.
"""
import argparse
from contextlib import closing
import asyncio
import json
import os
import re
import sqlite3
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'test-results/semantic-v09-2026-11-01'
LIVE=ROOT/'backend/data/rag_knowledge.db'

def save(name,data):
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

async def run(args):
    from app import rag,settings,local_models,store
    config=settings.get()
    config.embedding=settings.EmbeddingSettings(provider='local',model='BAAI/bge-m3',timeout_seconds=300)
    config.reranking=settings.RerankingSettings(provider='local',timeout_seconds=300)
    config.retrieval.cross_language=False
    settings.active.set(config)
    import torch
    save('environment.json',{'torch':torch.__version__,'cuda_available':torch.cuda.is_available(),
        'device':torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu',
        'settings':config.model_dump(),'paid_llm_calls':0})
    query='空气中的水分如何去除？'
    docs=['The dehumidifier removes excess moisture from ambient air using a condensation coil.',
          'The network router forwards packets between subnets using its routing table.',
          'The warehouse has a printed shipping label on every cardboard carton.']
    dense=await local_models.embed(config.embedding,[query,*docs])
    similarities=[sum(a*b for a,b in zip(dense[0],v)) for v in dense[1:]]
    scores,meta=await local_models.rerank(config.reranking,query,docs)
    save('smoke.json',{'query':query,'texts':docs,'dimensions':len(dense[0]),
        'cosine':similarities,'rerank_scores':scores,'inference':meta,
        'embedding_pass':similarities[0]==max(similarities),'reranking_pass':scores[0]==max(scores)})
    assert len(dense[0])==1024 and similarities[0]==max(similarities) and scores[0]==max(scores)
    if args.smoke_only:
        save('models.json',local_models.status());return
    dataset=ROOT/'test-data/web-2026-10-31'
    cases=[json.loads(x) for x in (dataset/'labels/qa.jsonl').read_text(encoding='utf-8').splitlines()]
    if args.report_only:cases=[]
    setup=json.loads((ROOT/'test-results/web-live-2026-11-01/setup.json').read_text(encoding='utf-8'))
    reverse={did:sid for sid,did in setup['documents'].items()}
    scope=list(setup['knowledge_bases'].values())
    results=[]
    for case in cases:
        for mode in ('vector','keyword','hybrid'):
            started=time.perf_counter()
            evidence,pipeline=await rag.retrieve(case['question'],scope,mode,5,True)
            got={reverse.get(item['document_id'],'UNKNOWN') for item in evidence}
            norm=lambda text:re.sub(r'\s+','',text).casefold()
            quotes=[any(norm(e['quote']) in norm(item['text']) for item in evidence if reverse.get(item['document_id'])==e['source_id']) for e in case.get('evidence',[])]
            results.append({'question_id':case['id'],'mode':mode,'answerable':case['answerable'],
                'expected_sources':case['source_ids'],'retrieved_sources':sorted(got),
                'all_expected_source_hit':set(case['source_ids'])<=got if case['answerable'] else None,
                'gold_quote_hits':quotes,'evidence':evidence,'pipeline':pipeline,
                'latency_ms':round((time.perf_counter()-started)*1000)})
        print(case['id'],[(r['mode'],r['all_expected_source_hit'],r['retrieved_sources']) for r in results[-3:]],flush=True)
        save('retrieval.json',results)
    summary={}
    for mode in ('vector','keyword','hybrid'):
        group=[r for r in results if r['mode']==mode and r['answerable']]
        if not group:continue
        summary[mode]={'answerable_questions':len(group),'all_sources_hit':sum(r['all_expected_source_hit'] for r in group),
            'all_exact_quotes_hit':sum(bool(r['gold_quote_hits']) and all(r['gold_quote_hits']) for r in group),
            'mean_latency_ms':round(sum(r['latency_ms'] for r in group)/len(group))}
    if results:save('summary.json',summary)
    save('models.json',local_models.status())
    from app import report_retrieval
    report_checks=[]
    report=store.one('SELECT * FROM report_tasks WHERE id=14')
    if report:
        blueprint=json.loads(report['blueprint'])
        for section in blueprint['sections']:
            evidence,pipeline=await report_retrieval.retrieve_section(report['knowledge_base_id'],section,
                purpose=blueprint.get('purpose',''),report_title=report['title'],requirements=report['requirements'])
            report_checks.append({'title':section['title'],'sources':sorted({reverse.get(e['document_id'],'UNKNOWN') for e in evidence}),
                'evidence':evidence,'pipeline':pipeline})
        save('report-sections.json',report_checks)
    save('calls-report.json' if args.report_only else 'calls.json',store.rows('SELECT kind,model,status,latency_ms,error FROM model_calls WHERE kind IN (?,?) ORDER BY id DESC LIMIT 200',('embedding','reranking')))
    if args.warm_live:
        rows=store.rows("SELECT * FROM embedding_cache WHERE namespace LIKE 'local-cls-normalized-v1|%'")
        with closing(sqlite3.connect(LIVE)) as conn:
            with conn:
                conn.executemany('INSERT OR REPLACE INTO embedding_cache VALUES(?,?,?)',[(r['namespace'],r['sha256'],r['vector']) for r in rows])
        save('warm-cache.json',{'rows':len(rows),'documents_and_reports_modified':False})
    print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--smoke-only',action='store_true');parser.add_argument('--warm-live',action='store_true')
    parser.add_argument('--report-only',action='store_true')
    args=parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='rag-semantic-v09-') as folder:
        snapshot=Path(folder)/'rag_knowledge.db'
        with closing(sqlite3.connect(LIVE.as_uri()+'?mode=ro',uri=True)) as before,closing(sqlite3.connect(snapshot)) as after:
            before.backup(after)
        os.environ['RAG_DATA_DIR']=folder
        asyncio.run(run(args))
