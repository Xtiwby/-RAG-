"""Explicit versioned activation and non-generating live smoke checks."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path
import check_web_live as helper

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'test-results/semantic-v09-2026-11-01'

def save(name,value):
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')

def preservation():
    backup=ROOT/'backend/data/backups/pre-semantic-v09-20261101.db'
    current=ROOT/'backend/data/rag_knowledge.db'
    checks={}
    with closing(sqlite3.connect(backup.as_uri()+'?mode=ro',uri=True)) as before,closing(sqlite3.connect(current.as_uri()+'?mode=ro',uri=True)) as after:
        for table in ('report_tasks','report_sections','report_templates','report_releases','documents','chunks','users','roles','kb_grants','conversations','messages'):
            cols=[r[1] for r in before.execute(f'PRAGMA table_info({table})') if r[1]!='last_login']
            old=before.execute(f"SELECT rowid,{','.join(cols)} FROM {table} ORDER BY rowid").fetchall()
            ids=[row[0] for row in old]
            now=after.execute(f"SELECT rowid,{','.join(cols)} FROM {table} WHERE rowid IN ({','.join('?' for _ in ids)}) ORDER BY rowid",ids).fetchall() if ids else []
            checks[table+'_old_rows_unchanged']=old==now
        checks['integrity']=after.execute('PRAGMA integrity_check').fetchone()[0]
    save('preservation.json',checks)
    assert all(v for k,v in checks.items() if k!='integrity') and checks['integrity']=='ok'

def main():
    client=helper.client()
    try:
        health=helper.api(client,'GET','/api/health')
        assert health['version']=='0.9.0'
        config=helper.api(client,'GET','/api/settings')
        if not (OUT/'settings-before.json').exists():save('settings-before.json',config)
        config['settings']['embedding'].update(provider='local',model='BAAI/bge-m3')
        config['settings']['reranking'].update(provider='local',model='BAAI/bge-reranker-v2-m3')
        config['settings']['retrieval']['rerank']=True
        saved=helper.api(client,'PUT','/api/settings',json={'settings':config['settings'],'expected_revision':config['revision']})
        save('settings-after.json',saved)
        probes={target:helper.api(client,'POST','/api/operations/probe',json={'target':target}) for target in ('embedding','reranking')}
        save('live-probes.json',probes)
        setup=json.loads((ROOT/'test-results/web-live-2026-11-01/setup.json').read_text(encoding='utf-8'))
        # Preserve the existing optional rewrite setting. Code-heavy Vue pages
        # can be classified as mixed-language and trigger a Kimi rewrite.
        source_id=setup['documents']['S06']
        docs=helper.api(client,'GET','/api/documents')
        scope=next(doc['knowledge_base_id'] for doc in docs if doc['id']==source_id)
        search=helper.api(client,'POST','/api/search',json={'query':'DOM更新是否同步，如何等待更新完成？',
            'knowledge_base_ids':[scope],'mode':'hybrid','rerank':True})
        assert search['pipeline']['embedding']=='local-bge-m3'
        assert search['pipeline']['rerank']=='local-cross-encoder'
        assert search['results']
        save('live-search.json',search)
        save('live-status.json',helper.api(client,'GET','/api/model-status'))
        save('live-operations.json',helper.api(client,'GET','/api/operations'))
        save('health.json',health)
        preservation()
        print('Activated revision',saved['revision'],'probes',probes,'results',len(search['results']),flush=True)
    finally:
        helper.api(client,'POST','/api/auth/logout');client.close()

if __name__=='__main__':main()
