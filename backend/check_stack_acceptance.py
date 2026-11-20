"""Disposable real PostgreSQL/pgvector, optional MinIO and external-worker acceptance.

Requires setup_integration_runtime.py; --minio also requires the pinned local
build from build_acceptance_minio.py. No business data or .env is modified.
"""
import argparse
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import tempfile
import uuid
import statistics
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote

ROOT=Path(__file__).resolve().parent
TOOLS=ROOT/'.integration-tools'
OUT=ROOT.parent/'test-results/remaining-2026-11-19'
FLAGS={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}


def port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0))
        return listener.getsockname()[1]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--minio',action='store_true');parser.add_argument('--models',action='store_true')
    parser.add_argument('--corpus',action='store_true');args=parser.parse_args()
    if args.corpus and not args.models:parser.error('--corpus requires --models')
    run=TOOLS/'runs'/uuid.uuid4().hex;run.mkdir(parents=True)
    OUT.mkdir(parents=True,exist_ok=True)
    bin=TOOLS/'runtime/Library/bin';password=secrets.token_urlsafe(24)
    pwfile=run/'password';pwfile.write_text(password,encoding='ascii')
    env={**os.environ,'PATH':str(bin)+os.pathsep+os.environ.get('PATH',''),'PYTHON_DOTENV_DISABLED':'1'}
    def command(name,*values):
        # PostgreSQL's Windows child can inherit pipe handles. A real log file
        # avoids waiting for a background server to close stdout at shutdown.
        with tempfile.TemporaryFile(mode='w+',encoding='utf-8') as output:
            result=subprocess.run([str(bin/(name+'.exe')),*map(str,values)],env=env,stdout=output,stderr=output,**FLAGS)
            output.seek(0);text=output.read()
        if result.returncode:
            raise RuntimeError(name+' failed: '+text[-2000:])
        return text
    pgport=port();pgdata=run/'pgdata';processes=[];logs=[]
    try:
        command('initdb','-D',pgdata,'-U','rag_acceptance','--pwfile',pwfile,'--encoding=UTF8','--locale=C','--auth-host=scram-sha-256','--auth-local=trust')
        command('pg_ctl','-D',pgdata,'-l',run/'postgres.log','-o',f'-h 127.0.0.1 -p {pgport}','-w','start')
        pwfile.unlink()
        url=f'postgresql://rag_acceptance:{quote(password)}@127.0.0.1:{pgport}/postgres'
        env.update(RAG_DATA_DIR=str(run/'appdata'),RAG_DATABASE_URL=url,RAG_WORKER_MODE='external',RAG_FILE_STORAGE='local',
                   RAG_QA_VERIFICATION_MODE='citation-first',RAG_QA_HEDGE_DELAY_SECONDS='0',RAG_OCR_PROVIDER='local')
        if args.minio:
            import boto3
            endpoint=f'http://127.0.0.1:{port()}'
            access='ragacceptance';secret=secrets.token_urlsafe(30)
            env.update(MINIO_ROOT_USER=access,MINIO_ROOT_PASSWORD=secret,
                       RAG_FILE_STORAGE='s3',RAG_S3_ENDPOINT=endpoint,RAG_S3_ACCESS_KEY=access,
                       RAG_S3_SECRET_KEY=secret,RAG_S3_BUCKET='acceptance-'+uuid.uuid4().hex[:12],RAG_S3_REGION='us-east-1')
            log=(run/'minio.log').open('w',encoding='utf-8');logs.append(log)
            process=subprocess.Popen([str(TOOLS/'minio.exe'),'server',str(run/'objects'),'--address',endpoint.removeprefix('http://'),
                                      '--console-address',f'127.0.0.1:{port()}'],env=env,stdout=log,stderr=log,**FLAGS);processes.append(process)
            s3=boto3.client('s3',endpoint_url=endpoint,aws_access_key_id=access,aws_secret_access_key=secret,region_name='us-east-1')
            for _ in range(100):
                try:s3.list_buckets();break
                except Exception:
                    if process.poll() is not None:raise RuntimeError('MinIO exited; see ignored runtime log')
                    time.sleep(.2)
            s3.create_bucket(Bucket=env['RAG_S3_BUCKET'])
            s3.put_bucket_versioning(Bucket=env['RAG_S3_BUCKET'],VersioningConfiguration={'Status':'Enabled'})
        os.environ.update(env)
        import psycopg
        from fastapi.testclient import TestClient
        from app.main import app
        from app import store
        log=(run/'worker.log').open('w',encoding='utf-8');logs.append(log)
        process=subprocess.Popen([sys.executable,'-m','app.worker'],env=env,cwd=ROOT,stdout=log,stderr=log,**FLAGS);processes.append(process)
        with TestClient(app) as client:
            response=client.post('/api/auth/login',json={'username':'admin','password':'admin123'});response.raise_for_status()
            headers={'Authorization':'Bearer '+response.json()['token']}
            if args.models:
                setting=client.get('/api/settings',headers=headers).json()
                setting['settings']['embedding'].update(provider='local',model='BAAI/bge-m3',timeout_seconds=300)
                setting['settings']['reranking'].update(provider='local',timeout_seconds=300)
                setting['settings']['retrieval']['cross_language']=False
                response=client.put('/api/settings',headers=headers,json={'settings':setting['settings'],'expected_revision':setting['revision']});response.raise_for_status()
            kid=client.post('/api/knowledge-bases',headers=headers,json={'name':'PostgreSQL 实机验收'}).json()['id']
            source='NX90217控制器采用被动散热。The NX90217 controller uses passive cooling. 设备保留期限为21天，维护前必须先断电。'
            t=time.perf_counter();response=client.post('/api/documents',headers=headers,data={'knowledge_base_id':kid},files={'file':('acceptance.txt',source.encode())});response.raise_for_status();doc=response.json()
            for _ in range(1800):
                current=client.get(f"/api/documents/{doc['id']}",headers=headers).json()
                if current['status'] not in ('uploading','processing'):break
                if process.poll() is not None:raise RuntimeError('Worker exited; see ignored runtime log')
                time.sleep(.1)
            assert current['status']=='ready',current
            ingest=time.perf_counter()-t
            original=client.get(f"/api/documents/{doc['id']}/original",headers=headers);assert original.content==source.encode()
            ranged=client.get(f"/api/documents/{doc['id']}/original",headers={**headers,'Range':'bytes=-9'});assert ranged.status_code==206 and ranged.content==source.encode()[-9:]
            if args.minio:
                saved=store.one('SELECT * FROM documents WHERE id=?',(doc['id'],))
                corrupted=s3.put_object(Bucket=saved['storage_bucket'],Key=saved['object_key'],Body=b'corrupted acceptance object',Metadata={'sha256':saved['sha256']})
                # Same key, new version: the pinned original remains immutable.
                assert client.get(f"/api/documents/{doc['id']}/original",headers=headers).content==source.encode()
                store.execute('UPDATE documents SET object_version_id=? WHERE id=?',(corrupted['VersionId'],doc['id']))
                bad=client.get(f"/api/documents/{doc['id']}/original",headers={**headers,'Range':'bytes=0-3'})
                assert bad.status_code==502 and '哈希' in bad.json()['detail']
                store.execute('UPDATE documents SET object_version_id=? WHERE id=?',(saved['object_version_id'],doc['id']))
            marker='acceptance-rollback-'+uuid.uuid4().hex
            try:
                with store.connect() as transaction:
                    transaction.execute('INSERT INTO knowledge_bases(name,description,visibility,status,created_at) VALUES(?,?,?,?,?)',
                                        (marker,'','private','active',store.now()))
                    raise ValueError('expected rollback')
            except ValueError:pass
            assert store.one('SELECT COUNT(*) n FROM knowledge_bases WHERE name=?',(marker,))['n']==0
            queries=[]
            for mode in ('keyword','vector','hybrid'):
                for query in ('NX90217如何散热？','How is NX90217 cooled?'):
                    t=time.perf_counter();response=client.post('/api/search',headers=headers,json={'query':query,'knowledge_base_ids':[kid],'mode':mode});response.raise_for_status()
                    result=response.json();assert any(x['document_id']==doc['id'] for x in result['results']),result
                    queries.append({'mode':mode,'query':query,'ms':(time.perf_counter()-t)*1000,'pipeline':result['pipeline']})
            export=client.get('/api/audits/export',headers=headers,params={'format':'json','actor':'系统管理员','start':'2020-01-01T00:00:00Z','end':'2099-01-01T08:00:00+08:00'})
            export.raise_for_status();assert export.json()['count']>0
            def request_sample(index):
                started=time.perf_counter()
                response=client.get(f"/api/documents/{doc['id']}/original",headers=headers) if index%2 else client.post('/api/search',headers=headers,
                    json={'query':'NX90217散热','knowledge_base_ids':[kid],'mode':'keyword'})
                assert response.status_code==200,response.text
                return (time.perf_counter()-started)*1000
            started=time.perf_counter()
            with ThreadPoolExecutor(max_workers=4) as pool:
                samples=list(pool.map(request_sample,range(40)))
            duration=time.perf_counter()-started
            with psycopg.connect(url) as connection:
                vector=connection.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0]
                indexes=connection.execute("SELECT indexname FROM pg_indexes WHERE tablename='chunk_search'").fetchall()
            parity=subprocess.run([sys.executable,'-m','pytest','tests/test_pg_sqlite_parity.py','-q',
                    '--junitxml='+str(OUT/'postgres-parity.xml')],env={**env,'RAG_TEST_POSTGRES_URL':url},
                    cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,**FLAGS)
            assert parity.returncode==0,'Real PostgreSQL parity failed; see postgres-parity.xml'
            result={'pass':True,'postgres':command('postgres','--version').strip(),'pgvector':vector,
                    'minio':args.minio,'external_worker':True,'real_bge':args.models,'ingestion_seconds':ingest,
                    'original_sha256':hashlib.sha256(original.content).hexdigest(),'range_pass':True,
                    'audit_count':export.json()['count'],'indexes':[x[0] for x in indexes],'queries':queries}
            result.update(pool_rollback_pass=True,s3_version_and_corruption_pass=args.minio,real_postgres_pytest_pass=True)
            result['concurrency']={'requests':len(samples),'workers':4,'failures':0,'seconds':duration,
                                   'median_ms':statistics.median(samples),'p95_ms':sorted(samples)[37]}
            if args.corpus:
                from check_corpus_stack import run as run_corpus
                result['corpus']=run_corpus(client,headers)
            filename='stack-minio-corpus.json' if args.corpus else 'stack-minio.json' if args.minio else 'stack-postgres.json'
            (OUT/filename).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            print('Real stack PASS; PostgreSQL',vector,'MinIO',args.minio,'worker external',flush=True)
    finally:
        for process in reversed(processes):
            process.terminate()
            try:process.wait(timeout=15)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        for log in logs:log.close()
        if (pgdata/'postmaster.pid').exists():command('pg_ctl','-D',pgdata,'-m','fast','-w','stop')
        pwfile.unlink(missing_ok=True)


if __name__=='__main__':main()
