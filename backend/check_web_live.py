"""Scoped, resumable live acceptance run. Never imports app/store or reads secrets.

Only corpus files enter the application; labels remain external evaluation data.
Results and a private SQLite backup are deliberately outside the shared dataset.
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, sqlite3, time
from datetime import datetime, timezone
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / 'test-data/web-2026-10-31'
OUT = ROOT / 'test-results/web-live-2026-11-01'
OUT.mkdir(parents=True, exist_ok=True)

def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def read(name, default=None):
    p = OUT / name
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else default

def db():
    c = sqlite3.connect((ROOT / 'backend/data/rag_knowledge.db').as_uri() + '?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    return c

def rows(c, query):
    return [dict(r) for r in c.execute(query)]

def client():
    c = httpx.Client(base_url='http://127.0.0.1:8000', timeout=300, trust_env=False)
    # Existing local demo account; never persist credentials or bearer tokens.
    r = c.post('/api/auth/login', json={'username':'admin','password':'admin123'})
    if r.status_code != 200:
        raise RuntimeError('Admin login failed; do not reset credentials or brute-force.')
    c.headers['Authorization'] = 'Bearer ' + r.json()['token']
    return c

def api(c, method, path, **kwargs):
    r = c.request(method, path, **kwargs)
    if not r.is_success:
        raise RuntimeError(f'{method} {path}: {r.status_code} {r.text[:500]}')
    return r.json()

def setup(c):
    if read('setup.json'):
        print('Setup already complete; reusing recorded IDs.', flush=True)
        return
    if read('cleanup.json'):
        print('Cleanup already completed; no repeat deletion.', flush=True)
    else:
        with db() as source:
            active = rows(source, "SELECT id,kind,status FROM jobs WHERE status IN ('running','queued')")
            if active:
                raise RuntimeError('Active jobs exist; cleanup aborted.')
            inventory = {
                'knowledge_bases': rows(source,'SELECT id,name,status,description FROM knowledge_bases'),
                'documents': rows(source,'SELECT id,name,knowledge_base_id,status,deleted,version FROM documents'),
                'conversations': rows(source,'SELECT id,user_id,title,knowledge_base_ids FROM conversations'),
                'reports': rows(source,'SELECT id,title,knowledge_base_id,template_id,status,document_state,user_id FROM report_tasks'),
                'templates': rows(source,'SELECT id,name,version,lifecycle,knowledge_base_ids FROM report_templates'),
                'preserved_report_12': rows(source,'SELECT id,title,document_state,final_release_id,revision,content FROM report_tasks WHERE id=12'),
            }
            save('inventory-before.json',inventory)
            backup_dir = ROOT / 'backend/data/backups'
            backup_dir.mkdir(exist_ok=True)
            backup = backup_dir / ('pre-web-test-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.db')
            with sqlite3.connect(backup) as target:
                source.backup(target)
                assert target.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        old_kbs = {4,6,7,8,9,10}
        targets = [k for k in inventory['knowledge_bases'] if k['id'] in old_kbs]
        assert len(targets)==6 and all(
            '验收' in k['name'] or '导出范围' in k['name'] or
            (k['id']==9 and k['name']=='V0.6 模板与事实核验（合成资料）') for k in targets)
        log = {'backup':str(backup),'conversations':[],'documents':[],'knowledge_bases':[],
               'templates':[],'reports_retained_archived':[],'physical_files_deleted':False}
        # Delete conversations before KB soft deletion, otherwise owner access is lost.
        for conv in inventory['conversations']:
            ids = set(json.loads(conv['knowledge_base_ids']))
            if conv['user_id']==1 and ids and ids<=old_kbs:
                api(c,'DELETE',f"/api/conversations/{conv['id']}")
                log['conversations'].append(conv['id'])
        for report in inventory['reports']:
            if report['knowledge_base_id'] in old_kbs:
                assert report['document_state']=='archived'
                log['reports_retained_archived'].append(report['id'])
        for doc in inventory['documents']:
            if doc['knowledge_base_id'] in old_kbs and not doc['deleted']:
                api(c,'DELETE',f"/api/documents/{doc['id']}")
                log['documents'].append(doc['id'])
        for kid in sorted(old_kbs):
            api(c,'DELETE',f'/api/knowledge-bases/{kid}')
            log['knowledge_bases'].append(kid)
        test_template = next(t for t in inventory['templates'] if t['id']==7)
        assert '合成验收' in test_template['name']
        api(c,'DELETE','/api/report-templates/7')
        log['templates'].append(7)
        with db() as source:
            preserved = rows(source,'SELECT id,title,document_state,final_release_id,revision,content FROM report_tasks WHERE id=12')
            assert preserved == inventory['preserved_report_12']
        save('cleanup.json',log)
        print('Cleanup:',{k:len(v) for k,v in log.items() if isinstance(v,list)},flush=True)
    state = read('setup-progress.json',{'knowledge_bases':{},'documents':{},'ingestion':{}})
    manifest = json.loads((DATASET/'manifest.json').read_text(encoding='utf-8'))
    for group,label in [('retrieval','检索技术'),('backend','后端与接入'),('frontend','Vue前端')]:
        if group not in state['knowledge_bases']:
            kb = api(c,'POST','/api/knowledge-bases',json={
                'name':f'网络资料测试 2026-11-01 · {label}',
                'description':'公开网络资料测试专用。仅入库corpus，不含问题、标准答案或评分标签；来源见test-data/web-2026-10-31/manifest.json。',
                'visibility':'private'})
            state['knowledge_bases'][group]=kb['id']
            save('setup-progress.json',state)
    for source in manifest['sources']:
        sid=source['id'];path=DATASET/source['path']
        assert hashlib.sha256(path.read_bytes()).hexdigest()==source['sha256']
        if sid not in state['documents']:
            with path.open('rb') as f:
                doc = api(c,'POST','/api/documents',data={'knowledge_base_id':state['knowledge_bases'][source['group']]},files={'file':(path.name,f)})
            state['documents'][sid]=doc['id']
            save('setup-progress.json',state)
        did=state['documents'][sid]
        end=time.monotonic()+180
        while True:
            stage=api(c,'GET',f'/api/documents/{did}/processing')
            if stage['status'] not in ('processing','uploading'):break
            if time.monotonic()>end:raise RuntimeError('Ingestion timeout')
            time.sleep(.5)
        detail=api(c,'GET',f'/api/documents/{did}')
        original=c.get(f'/api/documents/{did}/original');original.raise_for_status()
        state['ingestion'][sid]={'document_id':did,'status':stage['status'],
            'stages':stage['stages'],'events':stage['events'],'page_count':detail['page_count'],
            'chunk_count':len(detail['chunks']),'version':detail['version'],'sha256':detail['sha256'],
            'original_sha256_match':hashlib.sha256(original.content).hexdigest()==source['sha256'],
            'four_stages_complete':all(s['status']=='completed' and s['progress']==100 for s in stage['stages'])}
        save('setup-progress.json',state)
        print('Ingested',sid,did,stage['status'],len(detail['chunks']),flush=True)
    state['model_status']=api(c,'GET','/api/model-status')
    save('setup.json',state)

def retrieve(c):
    state=read('setup.json');assert state
    cases=[json.loads(x) for x in (DATASET/'labels/qa.jsonl').read_text(encoding='utf-8').splitlines()]
    existing=read('retrieval.json',[]);done={(r['question_id'],r['mode']) for r in existing}
    reverse={did:sid for sid,did in state['documents'].items()}
    for case in cases:
        for mode in ('vector','keyword','hybrid'):
            if (case['id'],mode) in done:continue
            body={'query':case['question'],'mode':mode,'knowledge_base_ids':list(state['knowledge_bases'].values()),'top_k':5,'rerank':True}
            t=time.perf_counter();result=api(c,'POST','/api/search',json=body)
            got={reverse.get(x['document_id'],'UNKNOWN') for x in result['results']}
            expected=set(case['source_ids'])
            entry={'question_id':case['id'],'mode':mode,'request':body,'latency_ms':round((time.perf_counter()-t)*1000),
                'answerable':case['answerable'],'expected_sources':sorted(expected),'retrieved_sources':sorted(got),
                'all_expected_source_hit':expected<=got if expected else None,
                'any_expected_source_hit':bool(expected & got) if expected else None,
                'gold_quote_hits':[], 'response':result}
            normalize=lambda v:re.sub(r'\s+','',v).casefold()
            for evidence in case.get('evidence',[]):
                matches=[r for r in result['results'] if reverse.get(r['document_id'])==evidence['source_id']]
                entry['gold_quote_hits'].append(any(normalize(evidence['quote']) in normalize(r['text']) for r in matches))
            existing.append(entry);save('retrieval.json',existing)
        print('Retrieval',case['id'],flush=True)
    summary={}
    for mode in ('vector','keyword','hybrid'):
        group=[r for r in existing if r['mode']==mode and r['answerable']]
        summary[mode]={'answerable_questions':len(group),'all_sources_hit':sum(r['all_expected_source_hit'] for r in group),
            'any_source_hit':sum(r['any_expected_source_hit'] for r in group),
            'all_exact_quotes_hit':sum(bool(r['gold_quote_hits']) and all(r['gold_quote_hits']) for r in group),
            'mean_latency_ms':round(sum(r['latency_ms'] for r in group)/max(1,len(group)),1)}
    save('retrieval-summary.json',summary);print(json.dumps(summary,ensure_ascii=False),flush=True)

def qa(c, ids):
    state=read('setup.json');assert state
    cases={q['id']:q for q in [json.loads(x) for x in (DATASET/'labels/qa.jsonl').read_text(encoding='utf-8').splitlines()]}
    results=read('qa.json',[]);done={x['question_id'] for x in results}
    for qid in ids.split(','):
        if qid in done:continue
        case=cases[qid];t=time.perf_counter();events=[]
        body={'query':case['question'],'mode':'hybrid','knowledge_base_ids':list(state['knowledge_bases'].values()),'top_k':5,'rerank':True}
        print('QA starting',qid,flush=True)
        with c.stream('POST','/api/qa/stream',json=body) as response:
            response.raise_for_status()
            event_type=''
            for line in response.iter_lines():
                if line.startswith('event:'):event_type=line[6:].strip()
                elif line.startswith('data:'):
                    item=json.loads(line[5:]);item['_sse_event']=event_type
                    events.append(item)
        cid=next((x.get('conversation_id') for x in events if x.get('conversation_id')),None)
        conv=api(c,'GET',f'/api/conversations/{cid}') if cid else None
        if cid:
            api(c,'PATCH',f'/api/conversations/{cid}',json={'title':f'网络资料测试 {qid} · '+case['question'][:65]})
        entry={'question_id':qid,'case':case,'request':body,'events':events,'conversation':conv,'latency_ms':round((time.perf_counter()-t)*1000)}
        results.append(entry);save('qa.json',results)
        if cid:
            r=c.get(f'/api/conversations/{cid}/export',params={'format':'md'});r.raise_for_status()
            (OUT/f'{qid}-conversation.md').write_bytes(r.content)
        answer=next((m for m in reversed((conv or {}).get('messages',[])) if m['role']=='assistant'),{})
        print('QA done',qid,answer.get('status'),answer.get('content','')[:500],flush=True)

def wait_job(c,jid,seconds=600):
    end=time.monotonic()+seconds
    while True:
        item=api(c,'GET',f'/api/tasks/{jid}')
        if item['status'] not in ('queued','running'):return item
        if time.monotonic()>end:raise RuntimeError(f'Job {jid} still running; not cancelled automatically')
        time.sleep(1)

def diagnostics(c):
    state=read('setup.json');cases=[json.loads(x) for x in (DATASET/'labels/qa.jsonl').read_text(encoding='utf-8').splitlines()]
    results=[]
    for case in cases:
        for mode in ('vector','keyword','hybrid'):
            body={'query':case['question'],'mode':mode,'knowledge_base_ids':list(state['knowledge_bases'].values()),'top_k':5,'rerank':False}
            results.append({'question_id':case['id'],'request':body,'response':api(c,'POST','/api/search',json=body)})
    save('retrieval-no-rerank.json',results)
    controls=[]
    for qid,text in [('Q001','What encoder framework does the DPR paper use for dense question and passage representations?'),
                     ('Q002','What is the DPR improvement range over Lucene BM25 in top-20 passage retrieval accuracy?'),
                     ('Q004','Is a higher or lower bm25() score better in SQLite FTS5?'),
                     ('Q005','What are the k1 and b constants in SQLite FTS5 BM25?')]:
        body={'query':text,'mode':'hybrid','knowledge_base_ids':[state['knowledge_bases']['retrieval']],'top_k':5,'rerank':True}
        controls.append({'question_id':qid,'note':'English translation and narrowed scope diagnostic, not baseline score','request':body,'response':api(c,'POST','/api/search',json=body)})
    save('retrieval-controls.json',controls)
    # Existing member account only; no security settings, passwords or grants changed.
    auth=[]
    with httpx.Client(base_url=c.base_url,timeout=30,trust_env=False) as member:
        auth.append({'name':'anonymous documents','status':member.get('/api/documents').status_code,'expected':401})
        r=member.post('/api/auth/login',json={'username':'member','password':'admin123'})
        if r.status_code==200:
            member.headers['Authorization']='Bearer '+r.json()['token']
            kid=state['knowledge_bases']['backend'];did=state['documents']['S03']
            checks=[('GET',f'/api/documents/{did}',None,403),('GET',f'/api/documents/{did}/original',None,403),
                ('POST','/api/search',{'query':'UploadFile','knowledge_base_ids':[kid]},403),
                ('POST','/api/qa/stream',{'query':'UploadFile','knowledge_base_ids':[kid]},403),
                ('GET','/api/audits/export?format=json',None,403),('GET','/api/reports/12',None,404)]
            for method,path,body,expected in checks:
                response=member.request(method,path,json=body) if body else member.request(method,path)
                auth.append({'name':method+' '+path,'status':response.status_code,'expected':expected,
                             'resource_content_leaked':response.status_code<400})
            member.post('/api/auth/logout')
        else:auth.append({'name':'member login','status':r.status_code,'note':'No retry or credential reset attempted'})
    save('authorization.json',auth)
    # Export only the new test run's logs, not all historical user logs.
    created=min(x['events'][0]['created_at'] for x in state['ingestion'].values())
    filters={'start':created,'user_id':1,'result':'success'}
    audit=api(c,'GET','/api/audits',params={**filters,'page_size':100})
    exported=c.get('/api/audits/export',params={**filters,'format':'json'});exported.raise_for_status()
    payload=exported.json();save('audit-export.json',payload)
    csv=c.get('/api/audits/export',params={**filters,'format':'csv'});csv.raise_for_status();(OUT/'audit-export.csv').write_bytes(csv.content)
    save('audit-check.json',{'filters':filters,'query_total':audit['total'],'json_count':payload['count'],
        'json_matches_query':payload['count']==audit['total'],'csv_export_count':csv.headers.get('X-Export-Count'),
        'note':'Each export itself creates an audit entry; later CSV count may increase.'})
    print('Diagnostics complete; no global configuration changes.',flush=True)

def report(c):
    state=read('setup.json');progress=read('report-progress.json',{})
    if not progress:
        body={'blueprint':{'name':'网络资料测试 · 文件接收与OCR边界','purpose':'根据外部技术资料说明边界，不代表本项目已实现',
             'sections':[
                {'title':'UploadFile文件接收','instructions':'仅两句说明UploadFile的spooled内存磁盘机制，每句引用。','target_words':80},
                {'title':'pypdf与OCR边界','instructions':'仅两句说明pypdf是否执行OCR及扫描文件的处理边界，每句引用。','target_words':80},
                {'title':'本项目本次压测吞吐量','instructions':'本节仅说明本项目实测QPS，没有项目实测数据则标记待补充；严禁引用外部文档编造。','target_words':50}]},
            'knowledge_base_ids':[state['knowledge_bases']['backend']],'lifecycle':'published'}
        tid=api(c,'POST','/api/report-templates',json=body)['id']
        progress={'template_id':tid};save('report-progress.json',progress)
    params={'title':'网络资料测试 · 文件接收与OCR边界报告','template_id':progress['template_id'],
            'knowledge_base_id':state['knowledge_bases']['backend'],'workflow_version':1,
            'requirements':'只使用所选外部技术资料，不把教程功能当成本项目实现；每节最多两句，不输出章节标题。'}
    if 'report_id' not in progress:
        preflight=api(c,'POST','/api/reports/preflight',json=params);save('report-preflight.json',preflight)
        created=api(c,'POST','/api/report-tasks',json={**params,'preflight_id':preflight['preflight_id'],'allow_missing':True})
        progress.update(report_id=created['id'],job_id=created['job_id']);save('report-progress.json',progress)
    job=wait_job(c,progress['job_id']);save('report-job.json',job)
    rid=progress['report_id'];detail=api(c,'GET',f'/api/reports/{rid}');save('report-generated.json',detail)
    if job['status'] not in ('completed','needs_review'):
        print('Report job failed',job['status'],job.get('error'),flush=True);return
    if not progress.get('edited'):
        section=detail['sections'][0];other=detail['sections'][1:]
        saved=api(c,'PATCH',f"/api/report-tasks/{rid}/sections/{section['id']}",json={
            'expected_revision':section['revision'],'content':section['content']+'\n\n人工测试备注：此处用于验证草稿保存，不代表项目事实。'})
        again=api(c,'GET',f'/api/reports/{rid}')
        progress['edited']={'saved':saved,'other_sections_unchanged':all(a['content']==b['content'] and a['revision']==b['revision'] for a,b in zip(other,again['sections'][1:]))}
        save('report-progress.json',progress)
    if 'regenerate_job' not in progress:
        detail=api(c,'GET',f'/api/reports/{rid}');section=detail['sections'][1]
        progress['before_regenerate']=[{'id':s['id'],'content':s['content'],'revision':s['revision']} for s in detail['sections']]
        replacement=api(c,'POST',f"/api/report-tasks/{rid}/sections/{section['id']}/regenerate",json={
            'expected_revision':section['revision'],'instructions':'只写一句pypdf不是OCR软件，并提供依据。','preview':True})
        progress['regenerate_job']=replacement['job_id'];save('report-progress.json',progress)
    replacement=wait_job(c,progress['regenerate_job']);save('report-regenerate-job.json',replacement)
    detail=api(c,'GET',f'/api/reports/{rid}')
    progress['regenerate_did_not_overwrite']=all(a['content']==b['content'] and a['revision']==b['revision'] for a,b in zip(progress['before_regenerate'],detail['sections']))
    if detail['candidates']:
        cand=detail['candidates'][0]
        api(c,'POST',f"/api/reports/{rid}/candidates/{cand['id']}",json={'action':'accept','expected_revision':cand['expected_revision']})
    detail=api(c,'GET',f'/api/reports/{rid}');save('report-before-review.json',detail)
    if detail['document_state']!='finalized':
        for section in detail['sections']:
            api(c,'POST',f"/api/reports/{rid}/sections/{section['id']}/review",json={'expected_revision':section['revision']})
        detail=api(c,'GET',f'/api/reports/{rid}')
        progress['human_release']=api(c,'POST',f'/api/reports/{rid}/finalize',json={'expected_revision':detail['revision'],'fixed_text_reviewed':True})
    progress['final_report']=api(c,'GET',f'/api/reports/{rid}')
    progress['exports']={}
    for format in ('md','docx','pdf'):
        response=c.get(f'/api/reports/{rid}/export',params={'format':format,'mode':'final'});response.raise_for_status()
        path=OUT/f'report-{rid}-final.{format}';path.write_bytes(response.content)
        progress['exports'][format]={'path':str(path),'bytes':len(response.content),'sha256':hashlib.sha256(response.content).hexdigest()}
    save('report-progress.json',progress);print('Report workflow complete',rid,flush=True)

def multimodal(c):
    state=read('setup.json');progress=read('multimodal.json',{})
    if 'knowledge_base_id' not in progress:
        kb=api(c,'POST','/api/knowledge-bases',json={'name':'网络资料测试 2026-11-01 · 图像与边界','description':'隔离OCR、重复上传测试，避免污染检索基线','visibility':'private'})
        progress['knowledge_base_id']=kb['id'];save('multimodal.json',progress)
    kid=progress['knowledge_base_id']
    if 'ocr_original' not in progress:
        path=DATASET/'scenarios/ocr/S01_DPR_page01.png'
        with path.open('rb') as f:doc=api(c,'POST','/api/documents',data={'knowledge_base_id':kid,'modality':'image'},files={'file':(path.name,f)})
        progress['ocr_original']=doc;save('multimodal.json',progress)
    original=progress['ocr_original'];wait_job(c,original['job_id'])
    progress['image_before_vision']=api(c,'GET',f"/api/documents/{original['id']}/processing")
    if 'ocr_result' not in progress:
        print('Vision extraction starting (one page)',flush=True)
        response=c.post(f"/api/documents/{original['id']}/extract-vision")
        progress['ocr_http_status']=response.status_code
        if response.is_success:
            result=response.json();progress['ocr_result']=api(c,'GET',f"/api/documents/{result['id']}")
        else:progress['ocr_error']=response.text[:500]
        save('multimodal.json',progress)
    if 'duplicate_uploads' not in progress:
        records=[];path=DATASET/'corpus/S03_FastAPI_请求文件.md'
        for n in range(2):
            with path.open('rb') as f:doc=api(c,'POST','/api/documents',data={'knowledge_base_id':kid},files={'file':(path.name,f)})
            wait_job(c,doc['job_id']);records.append(api(c,'GET',f"/api/documents/{doc['id']}"))
        progress['duplicate_uploads']=[{k:d[k] for k in ('id','name','version','sha256','status','deleted')} for d in records]
        progress['duplicate_prompt_returned']=False
        save('multimodal.json',progress)
    print('Multimodal/duplicate checks complete',progress.get('ocr_http_status'),flush=True)

def grounding_probe(c):
    """Two real judges with controlled citations, isolated to our new test report.

    The original v1 release is immutable. Restore the exact draft afterwards and
    release v2; a deliberately false test claim is never finalized or exported.
    """
    if read('grounding-probe.json'):
        print('Grounding probe already complete.',flush=True);return
    state=read('setup.json');rp=read('report-progress.json');rid=rp['report_id']
    assert rid>12
    report=api(c,'GET',f'/api/reports/{rid}');section=report['sections'][1]
    before={'report':report,'section':section};save('grounding-probe-before.json',before)
    if report['document_state']=='finalized':
        api(c,'POST',f'/api/reports/{rid}/reopen',json={'expected_revision':report['revision']})
    doc=api(c,'GET',f"/api/documents/{state['documents']['S10']}")
    chunk=next(x for x in doc['chunks'] if '不是' in x['text'] and 'OCR' in x['text'])
    attached=api(c,'POST',f"/api/reports/{rid}/sections/{section['id']}/citations",json={'expected_revision':section['revision'],'chunk_id':chunk['id']})
    reference=attached['reference'];results=[]
    for label,text in [('supported',f'pypdf 本身不是 OCR 软件 [{reference}]。'),
                       ('contradicted',f'pypdf 本身是 OCR 软件，可以识别扫描图像中的文字 [{reference}]。')]:
        current=api(c,'GET',f'/api/reports/{rid}')['sections'][1]
        saved=api(c,'PATCH',f"/api/report-tasks/{rid}/sections/{section['id']}",json={'expected_revision':current['revision'],'content':text})
        job=api(c,'POST',f"/api/report-tasks/{rid}/sections/{section['id']}/verify",json={'expected_revision':saved['revision']})
        finished=wait_job(c,job['job_id'])
        checked=api(c,'GET',f'/api/reports/{rid}')['sections'][1]
        results.append({'expected':label,'claim':text,'evidence':chunk['text'],'job':finished,'verification':checked['verification']})
        save('grounding-probe-progress.json',results)
        print('Real grounding',label,checked['verification']['state'],flush=True)
    current=api(c,'GET',f'/api/reports/{rid}')['sections'][1]
    api(c,'PATCH',f"/api/report-tasks/{rid}/sections/{section['id']}",json={'expected_revision':current['revision'],'content':section['content']})
    report=api(c,'GET',f'/api/reports/{rid}')
    for s in report['sections']:
        api(c,'POST',f"/api/reports/{rid}/sections/{s['id']}/review",json={'expected_revision':s['revision']})
    report=api(c,'GET',f'/api/reports/{rid}')
    released=api(c,'POST',f'/api/reports/{rid}/finalize',json={'expected_revision':report['revision'],'fixed_text_reviewed':True})
    save('grounding-probe.json',{'cases':results,'restored_content':True,'release':released,
        'note':'Only controlled evidence-judge smoke checks, not broad hallucination guarantee. Deliberately false claim was restored before release.'})
    print('Grounding probe complete; false draft restored before finalization.',flush=True)

def final_checks(c):
    import csv,io,zipfile
    from xml.etree import ElementTree as ET
    state=read('setup.json');rp=read('report-progress.json');rid=rp['report_id']
    # Exercise state changes only on our dedicated auxiliary KB; restore its original state.
    aux=read('multimodal.json');kid=aux['knowledge_base_id']
    kb=next(x for x in api(c,'GET','/api/knowledge-bases') if x['id']==kid)
    assert kb['name'].startswith('网络资料测试 2026-11-01')
    states=[]
    for status in ('disabled','archived','active'):
        updated=api(c,'PATCH',f'/api/knowledge-bases/{kid}',json={k:kb[k] for k in ('name','description','visibility')}|{'status':status})
        r=c.post('/api/search',json={'query':'UploadFile','knowledge_base_ids':[kid]})
        states.append({'status':status,'saved':updated['status']==status,'search_http':r.status_code,'expected':200 if status=='active' else 409})
    save('knowledge-base-states.json',states)
    # Download latest restored final release; do not export the deliberately false test draft.
    report=api(c,'GET',f'/api/reports/{rid}');save('report-final-latest.json',report)
    export_checks={}
    for format in ('md','docx','pdf'):
        r=c.get(f'/api/reports/{rid}/export',params={'format':format,'mode':'final'});r.raise_for_status()
        path=OUT/f'report-{rid}-final.{format}';path.write_bytes(r.content)
        check={'bytes':len(r.content),'sha256':hashlib.sha256(r.content).hexdigest(),'http':r.status_code}
        if format=='docx':
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                check['zip_integrity']=z.testzip() is None
                check['xml_parts_valid']=all(ET.fromstring(z.read(n)) is not None for n in z.namelist() if n.endswith('.xml'))
                text=''.join(ET.fromstring(z.read('word/document.xml')).itertext())
                check['has_all_headings']=all(s['title'] in text for s in report['sections'])
                check['has_placeholder']='【待补充】' in text
                check['deliberately_false_claim_absent']='pypdf 本身是 OCR 软件，可以识别扫描图像中的文字' not in text
            check['visual_render']='blocked: bundled LibreOffice unavailable; not claimed visually passed'
        if format=='pdf':
            from pypdf import PdfReader
            pdf=PdfReader(io.BytesIO(r.content));check['pages']=len(pdf.pages)
            text='\n'.join(p.extract_text() or '' for p in pdf.pages)
            check['has_all_headings']=all(s['title'] in text for s in report['sections'])
            check['has_sources']='参考来源' in text
        export_checks[format]=check
    save('report-export-checks.json',export_checks)
    # Persist raw telemetry totals without provider credentials or bearer sessions.
    operations=api(c,'GET','/api/operations');save('operations-after.json',operations)
    cleanup=read('cleanup.json')
    with db() as current, sqlite3.connect(Path(cleanup['backup']).as_uri()+'?mode=ro',uri=True) as old:
        old.row_factory=sqlite3.Row
        checks={
            'formal_report_12_unchanged':rows(current,'SELECT * FROM report_tasks WHERE id=12')==rows(old,'SELECT * FROM report_tasks WHERE id=12'),
            'formal_report_12_sections_unchanged':rows(current,'SELECT * FROM report_sections WHERE task_id=12 ORDER BY id')==rows(old,'SELECT * FROM report_sections WHERE task_id=12 ORDER BY id'),
            'original_knowledge_bases_unchanged':rows(current,'SELECT * FROM knowledge_bases WHERE id IN (1,2,3) ORDER BY id')==rows(old,'SELECT * FROM knowledge_bases WHERE id IN (1,2,3) ORDER BY id'),
            'users_security_and_profile_unchanged':rows(current,'SELECT id,username,name,password_hash,role_id,status FROM users ORDER BY id')==rows(old,'SELECT id,username,name,password_hash,role_id,status FROM users ORDER BY id'),
            'roles_unchanged':rows(current,'SELECT * FROM roles ORDER BY id')==rows(old,'SELECT * FROM roles ORDER BY id'),
            'model_settings_unchanged':rows(current,'SELECT * FROM system_settings')==rows(old,'SELECT * FROM system_settings'),
            'old_grants_unchanged':rows(current,'SELECT * FROM kb_grants WHERE kb_id<=10 ORDER BY user_id,kb_id')==rows(old,'SELECT * FROM kb_grants WHERE kb_id<=10 ORDER BY user_id,kb_id'),
            'audit_history_retained':current.execute('SELECT COUNT(*) FROM audit_logs').fetchone()[0]>=old.execute('SELECT COUNT(*) FROM audit_logs').fetchone()[0],
            'db_integrity':current.execute('PRAGMA integrity_check').fetchone()[0],
            'foreign_key_issues':len(current.execute('PRAGMA foreign_key_check').fetchall())}
        old_calls=old.execute('SELECT COALESCE(MAX(id),0) FROM model_calls').fetchone()[0]
        calls=rows(current,f'SELECT kind,status,input_tokens,output_tokens FROM model_calls WHERE id>{old_calls}')
    save('preservation-checks.json',checks)
    save('model-usage.json',{'calls':len(calls),'succeeded':sum(x['status']=='success' for x in calls),'failed':sum(x['status']=='failure' for x in calls),
        'input_tokens':sum(x['input_tokens'] or 0 for x in calls) if any(x['input_tokens'] is not None for x in calls) else None,
        'output_tokens':sum(x['output_tokens'] or 0 for x in calls) if any(x['output_tokens'] is not None for x in calls) else None,
        'note':'Missing provider token telemetry is unknown, never zero cost. Not a billing statement.'})
    qa_checks=[]
    for item in read('qa.json',[]):
        text=(OUT/(item['question_id']+'-conversation.md')).read_text(encoding='utf-8-sig')
        qa_checks.append({'question_id':item['question_id'],
            'full_scope_present':all(f'#{kid} ' in text for kid in state['knowledge_bases'].values()),
            'message_times_present':all(m['created_at'] in text for m in item['conversation']['messages'])})
    save('qa-export-checks.json',qa_checks)
    with (OUT/'retrieval-results.csv').open('w',newline='',encoding='utf-8-sig') as f:
        fields=['question_id','mode','answerable','expected_sources','retrieved_sources','all_expected_source_hit','any_expected_source_hit','gold_quote_hits','latency_ms']
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for row in read('retrieval.json'):
            writer.writerow({k:','.join(map(str,row[k])) if isinstance(row[k],list) else row[k] for k in fields})
    print('Final checks',checks,flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['setup','retrieval','qa','diagnostics','report','multimodal','grounding','final']);parser.add_argument('--ids',default='Q004,Q007,Q010,Q016,Q018,Q022,Q025,Q027,Q031')
    args=parser.parse_args();c=client()
    try:
        if args.phase=='setup':setup(c)
        elif args.phase=='retrieval':retrieve(c)
        elif args.phase=='qa':qa(c,args.ids)
        elif args.phase=='diagnostics':diagnostics(c)
        elif args.phase=='report':report(c)
        elif args.phase=='multimodal':multimodal(c)
        elif args.phase=='grounding':grounding_probe(c)
        else:final_checks(c)
    finally:
        c.post('/api/auth/logout');c.close()
