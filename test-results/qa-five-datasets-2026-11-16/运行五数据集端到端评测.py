"""End-to-end QA benchmark over vehicle, MIRACL, RAGTruth, RAGBench and RGB.

The runner starts an isolated API and worker against a unique PostgreSQL database
and MinIO bucket.  Every measured row calls /api/qa/stream and checks persisted
history plus citation back-links.  It does not modify the user's normal database.
"""
import argparse
import csv
import gzip
import hashlib
import json
import math
import os
import random
import re
import secrets
import subprocess
import sys
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
from statistics import median

import boto3
import httpx
import psycopg
from botocore.config import Config
from dotenv import dotenv_values
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

ROOT=Path(__file__).resolve().parent
SEED=336
REFS=re.compile(r'\[(\d+)\]')
REFUSAL=re.compile(r'证据不足|Unable to answer|insufficient evidence',re.I)


def load_json(path):return json.loads(path.read_text(encoding='utf8'))
def jsonl(path):
    with path.open(encoding='utf8') as stream:return [json.loads(line) for line in stream if line.strip()]
def write_json(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
def normalized(text):
    text=unicodedata.normalize('NFKC',text).casefold()
    text=REFS.sub('',text)
    return re.sub(r'[^\w]','',text)
def percentile(values,p):
    values=sorted(values)
    return values[math.ceil(p*len(values))-1] if values else None


def build_cases(args):
    public=args.public
    vehicle=args.vehicle
    functional=load_json(vehicle/'cases/功能测试集.json')
    performance=load_json(vehicle/'cases/性能测试集.json')
    cases=[];documents=defaultdict(list)
    for row in functional:
        cases.append({**row,'id':'VEH-'+row['id'],'dataset':'车辆维修自定义集','phase':'functional',
                      'expected_section':None,'gold_kind':'vehicle'})
    for row in performance:
        cases.append({**row,'id':'VEH-P-'+row['id'],'dataset':'车辆维修自定义集','phase':'performance',
                      'expected_section':None,'gold_kind':'vehicle'})
    for path in sorted((vehicle/'corpus').iterdir()):
        if path.suffix.lower() in ('.pdf','.pptx','.docx','.md'):
            documents['车辆维修自定义集'].append(path)

    # MIRACL: deterministic 10-query samples per language; all judged passages
    # form the shared retrieval corpus, not a per-question oracle context.
    for language in ('zh','en'):
        sample=load_json(public/f'results/miracl/sample-{language}.json')
        selected=sample['cases'][:args.miracl_per_language]
        needed={docid for case in selected for docid in case['qrels']}
        passages=[p for p in sample['passages'] if p['docid'] in needed]
        lines=['# MIRACL '+language+' judged passage pool']
        for p in passages:
            lines.extend(['',f"## DOCID {p['docid']} | {p['title']}",p['text']])
        target=args.generated/f'MIRACL-{language}.md';target.write_text('\n'.join(lines)+'\n',encoding='utf8')
        documents[f'MIRACL-{language}'].append(target)
        for case in selected:
            positives=[docid for docid,value in case['qrels'].items() if value>0]
            cases.append({'id':f'MIRACL-{language}-{case["id"]}','dataset':f'MIRACL-{language}',
                'phase':'functional','question':case['query'],'question_chars':len(case['query']),
                'length':'short' if len(case['query'])<45 else 'medium','difficulty':'public-benchmark',
                'gold_kind':'miracl','positive_docids':positives,'expected_section':None})

    # RAGTruth QA test: unique, human-labelled good answers and their original
    # MARCO contexts.  The reference answer is used only for post-run review.
    truth_root=public/'sources/ragtruth'
    source={r['source_id']:r for r in jsonl(truth_root/'source_info.jsonl') if r['task_type']=='QA'}
    candidates=[];seen=set()
    for row in jsonl(truth_root/'response.jsonl'):
        src=source.get(row['source_id']);question=src['source_info']['question'] if src else ''
        if not src or row['split']!='test' or row['quality']!='good' or row['labels'] or question.casefold() in seen:continue
        seen.add(question.casefold());candidates.append((row,src))
    random.Random(SEED).shuffle(candidates);candidates=candidates[:args.truth]
    lines=['# RAGTruth QA contexts']
    for row,src in candidates:
        sid=row['source_id'];info=src['source_info'];section=f'RAGTRUTH-{sid}'
        lines.extend(['',f'## {section}',info['passages']])
        cases.append({'id':section,'dataset':'RAGTruth-QA','phase':'functional','question':info['question'],
            'question_chars':len(info['question']),'length':'medium','difficulty':'public-benchmark',
            'gold_kind':'reference','reference_answer':row['response'],'expected_section':section})
    target=args.generated/'RAGTruth-QA.md';target.write_text('\n'.join(lines)+'\n',encoding='utf8')
    documents['RAGTruth-QA'].append(target)

    # RAGBench DelucionQA: human/model adjudicated supported rows only.  The
    # system must retrieve and generate its own answer from the shared corpus.
    delusion=[];seen=set()
    for row in jsonl(public/'sources/ragbench-delucionqa/test.jsonl'):
        key=row['question'].strip().casefold()
        if row.get('adherence_score') is not True or key in seen:continue
        seen.add(key);delusion.append(row)
    random.Random(SEED).shuffle(delusion);delusion=delusion[:args.delusion]
    lines=['# RAGBench DelucionQA contexts']
    for row in delusion:
        section=f'DELUCION-{row["id"]}'
        lines.extend(['',f'## {section}','\n\n'.join(row['documents'])])
        cases.append({'id':section,'dataset':'RAGBench-DelucionQA','phase':'functional','question':row['question'],
            'question_chars':len(row['question']),'length':'medium','difficulty':'public-benchmark',
            'gold_kind':'reference','reference_answer':row['response'],'expected_section':section})
    target=args.generated/'RAGBench-DelucionQA.md';target.write_text('\n'.join(lines)+'\n',encoding='utf8')
    documents['RAGBench-DelucionQA'].append(target)

    # RGB sample contains both answer integration and no-answer robustness.
    rgb=load_json(public/'results/rgb/samples.json')
    lines=['# RGB published fixed contexts']
    for row in rgb:
        section='RGB-'+row['id']
        lines.extend(['',f'## {section}',*row['texts']])
        cases.append({'id':section,'dataset':'RGB','phase':'functional','question':row['query'],
            'question_chars':len(row['query']),'length':'short','difficulty':row['mode'],
            'gold_kind':'rgb','expected':row['expected'],'excluded_answer':row['excluded_answer'],
            'expected_section':section})
    target=args.generated/'RGB.md';target.write_text('\n'.join(lines)+'\n',encoding='utf8')
    documents['RGB'].append(target)
    return cases,documents


def deployment(args,paths):
    dotenv=dotenv_values(args.backend/'.env');env=dict(os.environ)
    for key in ('KIMI_API_KEY','KIMI_MODEL','KIMI_BASE_URL'):
        if dotenv.get(key):env.setdefault(key,dotenv[key])
    if not env.get('KIMI_API_KEY') or env['KIMI_API_KEY'].startswith('replace-'):
        raise RuntimeError('backend/.env has no usable Kimi credential')
    password=env.get('QA_POSTGRES_PASSWORD','rag-local-dev')
    base=conninfo_to_dict(env.get('QA_POSTGRES_ADMIN_DSN') or make_conninfo(
        host='127.0.0.1',port='5432',user='rag',password=password))
    suffix=time.strftime('%Y%m%d%H%M%S')+secrets.token_hex(2)
    name='qa_five_'+suffix;bucket='qa-five-'+suffix
    with psycopg.connect(make_conninfo(**{**base,'dbname':'postgres'}),autocommit=True,connect_timeout=8) as root:
        version=root.execute('SHOW server_version').fetchone()[0]
        root.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    dsn=make_conninfo(**{**base,'dbname':name})
    env.update(RAG_DATABASE_URL=dsn,RAG_FILE_STORAGE='s3',RAG_S3_BUCKET=bucket,
        RAG_S3_ENDPOINT=env.get('QA_S3_ENDPOINT','http://127.0.0.1:9000'),
        RAG_S3_ACCESS_KEY=env.get('QA_S3_ACCESS_KEY','rag-local-dev'),
        RAG_S3_SECRET_KEY=env.get('QA_S3_SECRET_KEY','change-me-local'),RAG_S3_REGION='us-east-1',
        RAG_WORKER_MODE='external',RAG_WORKER_PROCESS='0',RAG_DATA_DIR=str(paths/'local'),
        QA_MODEL_DIR=str(args.models),INITIAL_ADMIN_PASSWORD=secrets.token_urlsafe(24),
        PYTHONUNBUFFERED='1',PYTHONIOENCODING='utf-8',EMBEDDING_BASE_URL='',EMBEDDING_MODEL='',
        RAG_QA_VERIFICATION_MODE='citation-first',RAG_QA_HEDGE_DELAY_SECONDS='8')
    s3=boto3.client('s3',endpoint_url=env['RAG_S3_ENDPOINT'],region_name='us-east-1',
        aws_access_key_id=env['RAG_S3_ACCESS_KEY'],aws_secret_access_key=env['RAG_S3_SECRET_KEY'],
        config=Config(connect_timeout=8,read_timeout=15))
    s3.create_bucket(Bucket=bucket)
    return env,s3,{'database':'PostgreSQL','postgresql_version':version,'database_name':name,
        'object_storage':'MinIO/S3','object_bucket':bucket,'worker_mode':'external',
        'verification_mode':'citation-first','endpoint':f'http://127.0.0.1:{args.port}'}


def spawn(args,env,paths,label,command):
    bootstrap="import os; from pathlib import Path; from app import local_models; local_models.MODEL_ROOT=Path(os.environ['QA_MODEL_DIR']); "
    log=(paths/f'{label}.log').open('wb')
    process=subprocess.Popen([sys.executable,'-X','utf8','-c',bootstrap+command],cwd=args.backend,env=env,
        stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    process._benchmark_log=log
    return process


def request(client,method,path,**kwargs):
    response=client.request(method,path,**kwargs)
    if response.is_error:raise RuntimeError(f'{method} {path}: HTTP {response.status_code} {response.text[:600]}')
    return response


def ready(client,api):
    for _ in range(120):
        if api.poll() is not None:raise RuntimeError('API bootstrap failed; see api.log')
        try:
            response=client.get('/api/health',timeout=2)
            if response.status_code==200:return response.json()
        except httpx.TransportError:pass
        time.sleep(.5)
    raise RuntimeError('API startup timed out')


def ingest(client,worker,kid,paths):
    pending={};stored=[]
    for path in paths:
        with path.open('rb') as stream:
            row=request(client,'POST','/api/documents',data={'knowledge_base_id':kid},
                        files={'file':(path.name,stream)}).json()
        pending[row['id']]=path
    deadline=time.monotonic()+900
    while pending:
        if worker.poll() is not None:raise RuntimeError('worker stopped during ingestion')
        if time.monotonic()>deadline:raise RuntimeError('ingestion timed out')
        for did,path in list(pending.items()):
            doc=request(client,'GET',f'/api/documents/{did}?compact=true').json()
            if doc['status']=='ready':
                original=request(client,'GET',f'/api/documents/{did}/original').content
                digest=hashlib.sha256(path.read_bytes()).hexdigest()
                if hashlib.sha256(original).hexdigest()!=digest:raise RuntimeError(path.name+' object checksum mismatch')
                stored.append({'id':did,'file':path.name,'chunks':doc['chunk_count'],'sha256':digest})
                del pending[did]
            elif 'failed' in doc['status'] or doc['status']=='needs_extraction':
                raise RuntimeError(path.name+': '+doc['status'])
        if pending:time.sleep(.5)
    return stored


def source_valid(client,citations,answer):
    checks=[]
    for number in sorted({int(n) for n in REFS.findall(answer)}):
        ok=False
        if 0<number<=len(citations):
            cite=citations[number-1]
            detail=request(client,'GET',f"/api/documents/{cite['document_id']}?compact=true&chunk_id={cite['chunk_id']}").json()
            ok=detail['version']==cite['version'] and any(
                chunk['id']==cite['chunk_id'] and chunk['text']==cite['text'] for chunk in detail['chunks'])
        checks.append({'number':number,'ok':ok})
    return checks


def score(case,answer,citations,checks):
    refused=bool(REFUSAL.search(answer));all_sources=bool(checks) and all(x['ok'] for x in checks)
    sections='\n'.join(str(c.get('section') or '')+'\n'+str(c.get('text') or '') for c in citations)
    kind=case['gold_kind'];passed=False;detail=''
    if kind=='vehicle':
        if not case['answerable']:
            passed=refused and not re.search(case['refusal_target'],answer,re.I);detail='expected refusal'
        else:
            clean=re.sub(r'\*\*|__|`','',REFS.sub('',answer))
            facts=[bool(re.search(f['pattern'],clean,re.I|re.S)) for f in case['facts']]
            passed=all(facts) and all_sources;detail='fact regex '+str(facts)
    elif kind=='miracl':
        hit=[docid for docid in case['positive_docids'] if 'DOCID '+docid in sections]
        passed=not refused and bool(hit) and all_sources;detail='positive qrel citations '+str(hit)
    elif kind=='reference':
        hit=case['expected_section'] in sections
        passed=not refused and hit and all_sources;detail='expected context cited='+str(hit)
    elif kind=='rgb':
        if case['expected']:
            hits=[normalized(term) in normalized(answer) for term in case['expected']]
            passed=all(hits) and all_sources;detail='answer terms '+str(hits)
        else:
            leaks=[normalized(term) in normalized(answer) for term in case['excluded_answer']]
            passed=refused and not any(leaks);detail='refusal, excluded leaks '+str(leaks)
    return passed,detail


def ask(client,case,kid):
    row={key:case.get(key) for key in ('id','dataset','phase','question','question_chars','length','difficulty','reference_answer')}
    began=time.perf_counter();first=None;meta={};done={};parts=[];verify=[];error=''
    try:
        with client.stream('POST','/api/qa/stream',json={'query':case['question'],'knowledge_base_ids':[kid],
                'mode':'hybrid','top_k':8,'rerank':True}) as response:
            row['http']=response.status_code
            if response.is_error:return {**row,'response_ok':False,'error':response.read().decode('utf8')[:600]}
            for line in response.iter_lines():
                if not line.startswith('data: '):continue
                item=json.loads(line[6:]);kind=item['type']
                if kind=='meta':meta=item
                elif kind=='token':
                    if first is None:first=round((time.perf_counter()-began)*1000,2)
                    parts.append(item['content'])
                elif kind=='verification':verify=item['checks']
                elif kind=='done':done=item
                elif kind=='error':error=item.get('message','error')
        total=round((time.perf_counter()-began)*1000,2);answer=''.join(parts);citations=meta.get('citations',[])
        saved_ok=False
        if done and meta:
            saved=request(client,'GET',f"/api/conversations/{meta['conversation_id']}").json()['messages'][-1]
            saved_ok=saved['content']==answer and saved['status']=='complete'
        checks=source_valid(client,citations,answer) if done else []
        response_ok=bool(done and first is not None and saved_ok and not error)
        passed,detail=score(case,answer,citations,checks) if response_ok else (False,'invalid stream')
        timing=done.get('timings',{});pipeline=meta.get('pipeline',{})
        return {**row,'response_ok':response_ok,'screen_pass':passed,'screen_detail':detail,'answer':answer,
            'error':error,'retrieval_ms':timing.get('retrieval_ms',pipeline.get('retrieval_ms')),
            'first_token_ms':first,'total_ms':total,'server_total_ms':timing.get('total_ms'),
            'retrieved':len(citations),'used_citations':len(checks),'citation_backlink_pass':bool(checks) and all(c['ok'] for c in checks),
            'verification_supported':sum(c.get('status')=='supported' for c in verify),
            'verification_rejected':sum(c.get('status')!='supported' for c in verify),
            'search_index':pipeline.get('search_index'),'citations':citations,'source_checks':checks,'verification':verify}
    except Exception as exc:
        return {**row,'response_ok':False,'screen_pass':False,'answer':'','error':str(exc)[:700],
                'retrieval_ms':None,'first_token_ms':None,'total_ms':round((time.perf_counter()-began)*1000,2)}


def summarize(rows):
    output={}
    for name in ['全部',*sorted({r['dataset'] for r in rows})]:
        group=rows if name=='全部' else [r for r in rows if r['dataset']==name]
        valid=[r for r in group if r['response_ok']]
        totals=sorted(r['total_ms'] for r in valid);firsts=sorted(r['first_token_ms'] for r in valid)
        retrieval=sorted(r['retrieval_ms'] for r in valid if r['retrieval_ms'] is not None)
        output[name]={'planned':len(group),'completed':len(valid),'screen_pass':sum(r['screen_pass'] for r in valid),
            'screen_pass_rate':round(sum(r['screen_pass'] for r in valid)/len(valid),4) if valid else None,
            'retrieval_p50_ms':round(median(retrieval),2) if retrieval else None,'retrieval_p95_ms':percentile(retrieval,.95),
            'first_token_p50_ms':round(median(firsts),2) if firsts else None,'first_token_p95_ms':percentile(firsts,.95),
            'total_p50_ms':round(median(totals),2) if totals else None,'total_p95_ms':percentile(totals,.95)}
    return output


def csv_rows(path,rows):
    fields=['id','dataset','phase','response_ok','screen_pass','screen_detail','retrieval_ms','first_token_ms','total_ms',
            'server_total_ms','retrieved','used_citations','citation_backlink_pass','verification_supported',
            'verification_rejected','question','reference_answer','answer','error']
    with path.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(rows)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backend',type=lambda s:Path(s).resolve(),required=True)
    parser.add_argument('--models',type=lambda s:Path(s).resolve(),required=True)
    parser.add_argument('--vehicle',type=lambda s:Path(s).resolve(),required=True)
    parser.add_argument('--public',type=lambda s:Path(s).resolve(),required=True)
    parser.add_argument('--work',type=lambda s:Path(s).resolve(),required=True)
    parser.add_argument('--output',type=lambda s:Path(s).resolve(),required=True)
    parser.add_argument('--port',type=int,default=18082)
    parser.add_argument('--miracl-per-language',type=int,default=10)
    parser.add_argument('--truth',type=int,default=10)
    parser.add_argument('--delusion',type=int,default=10)
    parser.add_argument('--limit',type=int)
    parser.add_argument('--performance-only',action='store_true')
    args=parser.parse_args();args.generated=args.work/'generated-corpus'
    args.output.mkdir(parents=True,exist_ok=False);args.generated.mkdir(parents=True,exist_ok=True)
    cases,documents=build_cases(args)
    if args.performance_only:
        cases=[case for case in cases if case['phase']=='performance' or case['dataset']!='车辆维修自定义集']
    if args.limit:cases=cases[:args.limit]
    write_json(args.output/'测试用例.json',cases)
    env,s3,info=deployment(args,args.work);api=spawn(args,env,args.work,'api',f"import uvicorn; uvicorn.run('app.main:app',host='127.0.0.1',port={args.port})")
    worker=None
    try:
        with httpx.Client(base_url=info['endpoint'],timeout=150,trust_env=False) as client:
            health=ready(client,api);login=request(client,'POST','/api/auth/login',json={'username':'admin','password':env['INITIAL_ADMIN_PASSWORD']}).json()
            client.headers['Authorization']='Bearer '+login['token']
            current=request(client,'GET','/api/settings').json();config=current['settings']
            config['embedding'].update(provider='local',model='BAAI/bge-m3',device='cuda',batch_size=16,max_length=1024)
            config['reranking'].update(provider='local',device='cuda',candidate_k=16,batch_size=16,max_length=256)
            config['retrieval'].update(mode='hybrid',top_k=8,cross_language=False)
            config['answer_quality'].update(enabled=False,context_neighbors=2)
            config['chat'].update(timeout_seconds=45,max_tokens=1400)
            request(client,'PUT','/api/settings',json={'settings':config,'expected_revision':current['revision']})
            worker=spawn(args,env,args.work,'worker',"import runpy; runpy.run_module('app.worker',run_name='__main__')")
            kb_ids={};stored=[]
            for dataset,paths in documents.items():
                kid=request(client,'POST','/api/knowledge-bases',json={'name':'五数据集评测-'+dataset}).json()['id'];kb_ids[dataset]=kid
                stored.extend({'dataset':dataset,**row} for row in ingest(client,worker,kid,paths))
                print('indexed',dataset,flush=True)
            with psycopg.connect(env['RAG_DATABASE_URL']) as con:
                pgvector=con.execute("select extversion from pg_extension where extname='vector'").fetchone()[0]
                chunks,vectors=con.execute('select count(*),count(embedding) from chunk_search').fetchone()
            write_json(args.output/'运行环境.json',{**info,'api_health':health,'pgvector_version':pgvector,
                'indexed_chunks':chunks,'vector_chunks':vectors,'documents':stored,'configuration':config,
                'case_count':len(cases),'seed':SEED})
            # One full-pipeline warm-up is excluded from the formal sample.
            warmup=ask(client,cases[0],kb_ids[cases[0]['dataset']])
            write_json(args.output/'预热结果.json',warmup)
            rows=[]
            with (args.output/'runs.jsonl').open('w',encoding='utf8') as stream:
                for number,case in enumerate(cases,1):
                    row=ask(client,case,kb_ids[case['dataset']]);rows.append(row)
                    stream.write(json.dumps(row,ensure_ascii=False)+'\n');stream.flush()
                    print(number,'/',len(cases),case['id'],row['response_ok'],row.get('screen_pass'),row.get('total_ms'),flush=True)
            write_json(args.output/'自动汇总.json',summarize(rows));csv_rows(args.output/'逐题结果.csv',rows)
            print(json.dumps(summarize(rows),ensure_ascii=False,indent=2),flush=True)
    finally:
        for process in (worker,api):
            if process and process.poll() is None:
                process.terminate()
                try:process.wait(timeout=15)
                except subprocess.TimeoutExpired:process.kill()
            if process and hasattr(process,'_benchmark_log'):process._benchmark_log.close()


if __name__=='__main__':main()
