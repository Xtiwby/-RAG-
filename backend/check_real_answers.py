"""Generate and locally verify answers using frozen, independently retrieved evidence.

Only public corpus passages and question text enter inference. Gold answers and
labels are retained solely for subsequent review, never in the model prompt.
"""
import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'test-results/remaining-2026-11-19'


async def run():
    from app import settings,grounding,store
    settings.active.set(settings.defaults())
    records=json.loads((OUT/'real-retrieval-final.json').read_text(encoding='utf-8'))['results']
    selected={x['question_id']:x for x in records if x['mode']=='hybrid'}
    cases=[json.loads(line) for line in (ROOT/'test-data/web-2026-10-31/labels/qa.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    results=[]
    source_names={p.name.split('_')[0]:p.name for p in (ROOT/'test-data/web-2026-10-31/corpus').iterdir() if p.is_file()}
    for case in cases:
        evidence=[{'name':source_names[chunk['source']],'section':f"片段 {chunk['ordinal']}",'page':None,'version':'public-test',
                   'text':chunk['text']} for chunk in selected[case['id']]['chunks']]
        start=time.perf_counter()
        events=[event async for event in grounding.verified_stream(case['question'],evidence)]
        content=''.join(event.get('content','') for event in events if event['type']=='token')
        results.append({'id':case['id'],'question':case['question'],'answer':content,'answerable':case['answerable'],
            'seconds':time.perf_counter()-start,'events':events,'evidence':evidence})
        calls=store.rows('SELECT kind,model,status,latency_ms,input_tokens,output_tokens FROM model_calls')
        (OUT/'real-answers.json').write_text(json.dumps({'results':results,'model_calls':calls},ensure_ascii=False,indent=2),encoding='utf-8')
        print('Generated and verified',case['id'],flush=True)


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='rag-real-answers-') as folder:
        os.environ.update(PYTHON_DOTENV_DISABLED='1',RAG_DATA_DIR=folder,RAG_DATABASE_URL='',RAG_FILE_STORAGE='local',
                          RAG_QA_VERIFICATION_MODE='citation-first',RAG_QA_HEDGE_DELAY_SECONDS='0')
        from dotenv import dotenv_values
        for key,value in dotenv_values(Path(__file__).resolve().parent/'.env').items():
            if key in ('KIMI_API_KEY','KIMI_BASE_URL','KIMI_MODEL') and value:os.environ[key]=value
        asyncio.run(run())
