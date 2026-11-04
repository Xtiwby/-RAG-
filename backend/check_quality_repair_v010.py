"""Real coverage+repair in a disposable DB, not another end-to-end QA score."""
import asyncio
import json
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'test-results/quality-v010-2026-11-01'

async def run():
    from dotenv import load_dotenv
    load_dotenv(ROOT/'backend/.env')
    from app import answer_quality,settings
    config=settings.get();config.answer_quality.enabled=True;config.answer_quality.repair=True
    settings.active.set(config)
    real=json.loads((OUT/'trial2/qa-Q001.json').read_text(encoding='utf-8'))
    evidence=real['conversation']['messages'][-1]['citations']
    # Deliberately incomplete but source-supported premise, not a user answer.
    published=['问题与文段之间的相似度通过它们向量的点积计算。[3]']
    assessment,extra,checks=await answer_quality.assess(real['question'],evidence,published,[],1)
    result={'test_kind':'controlled incomplete answer; not end-to-end QA accuracy',
        'question':real['question'],'initial_controlled_answer':published,'assessment':assessment,
        'verified_supplements':extra,'checks':checks}
    (OUT/'real-repair.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False),flush=True)

if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='rag-repair-v010-') as folder:
        live=ROOT/'backend/data/rag_knowledge.db'
        with closing(sqlite3.connect(live.as_uri()+'?mode=ro',uri=True)) as before,closing(sqlite3.connect(Path(folder)/'rag_knowledge.db')) as after:
            before.backup(after)
        os.environ['RAG_DATA_DIR']=folder
        asyncio.run(run())
