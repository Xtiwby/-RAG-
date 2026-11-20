"""Regression coverage for scanned PDFs, duplicate choices, ranges and plan sharing."""
import asyncio
import io
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image,ImageDraw
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

from test_api import client, admin, application_lifespan, kb, upload, finish_upload, configured, save_config, create_member
from app import ocr, store, object_storage, retrieval_planner, rag, settings,grounding
from test_storage_upgrade import FakeS3


@pytest.mark.parametrize('base,model,disabled',[
    ('https://api.deepseek.com','deepseek-flash',True),
    ('https://api.deepseek.com/v1','deepseek-v4-pro',True),
    ('https://compatible.example/v1','deepseek-flash',False),
    ('https://api.deepseek.com','custom-model',False),
    ('https://api.moonshot.cn/v1','kimi-k2.5',True),
])
def test_bounded_generation_explicitly_disables_supported_reasoning(monkeypatch,base,model,disabled):
    monkeypatch.setattr(rag,'llm_config',lambda:('test',base,model))
    body=rag.payload([{'role':'user','content':'Return JSON'}],stream=True,max_tokens=1400)
    assert body.get('thinking')==({'type':'disabled'} if disabled else None)
    assert body['max_tokens']==1400 and body['stream_options']['include_usage']


def scanned_pdf(mixed=False,blank=False):
    stream=io.BytesIO(); doc=canvas.Canvas(stream)
    if mixed:
        doc.drawString(50,750,'Existing digital text remains unchanged.')
        doc.showPage()
    image=Image.new('RGB',(800,200),'white')
    if not blank:ImageDraw.Draw(image).rectangle((20,20,100,50),fill='black')
    doc.drawImage(ImageReader(image),50,500,width=400,height=100)
    doc.save()
    return stream.getvalue()


def submit(headers,kid,raw,name='scan.pdf',**values):
    return client.post('/api/documents',headers=headers,data={'knowledge_base_id':kid,**values},
                       files={'file':(name,raw)})


def test_mixed_pdf_manual_ocr_preserves_pages_and_original(admin,monkeypatch):
    kid=kb(admin); raw=scanned_pdf(mixed=True)
    response=submit(admin,kid,raw)
    doc=finish_upload(admin,response.json())
    assert doc['status']=='needs_extraction'
    path=f"/api/documents/{doc['id']}"
    assert client.get(path+'/processing',headers=admin).json()['can_continue_vision']
    seen=[]
    async def recognize(path,section='',page=1):
        with Image.open(path) as image:
            assert image.width*image.height<=4_100_000
        seen.append(page)
        return [{'page':page,'section':section,'text':'Scan page 2 uses passive cooling.'}]
    monkeypatch.setattr(ocr,'extract',recognize)
    assert client.post(path+'/vision-processing',headers=admin).status_code==202
    ready=finish_upload(admin,doc)
    assert ready['status']=='ready' and seen==[2]
    assert {c['page'] for c in ready['chunks']}=={1,2}
    assert any('Existing digital text' in c['text'] for c in ready['chunks'])
    assert client.get(path+'/original',headers=admin).content==raw
    assert ready['version']==doc['version']


def test_blank_pdf_page_keeps_number_without_ocr_error(admin,configured,monkeypatch):
    save_config(admin,lambda cfg:cfg['ocr'].update(provider='local'))
    async def unexpected(*args,**kwargs):
        raise AssertionError('blank pages must not call OCR')
    monkeypatch.setattr(ocr,'extract',unexpected)
    response=submit(admin,kb(admin),scanned_pdf(mixed=True,blank=True))
    doc=finish_upload(admin,response.json())
    assert doc['status']=='ready' and doc['page_count']==2
    assert all(chunk['page']==1 for chunk in doc['chunks'])


def test_pdf_local_auto_ocr_and_failure_are_explicit(admin,configured,monkeypatch):
    save_config(admin,lambda cfg:cfg['ocr'].update(provider='local'))
    async def broken(*args,**kwargs):
        raise RuntimeError('合成 OCR 故障')
    monkeypatch.setattr(ocr,'extract',broken)
    response=submit(admin,kb(admin),scanned_pdf())
    doc=finish_upload(admin,response.json())
    assert doc['status']=='parse_failed' and '第 1 页' in doc['parse_error']
    assert doc['chunks']==[]


def test_duplicate_cancel_reuse_and_new_version(admin):
    kid=kb(admin); first=upload(admin,kid,text='Same immutable source and references.')
    raw=b'Same immutable source and references.'
    before=store.one('SELECT COUNT(*) n FROM documents')['n']
    duplicate=submit(admin,kid,raw,name='renamed.md')
    assert duplicate.status_code==409 and duplicate.json()['detail']['code']=='duplicate_document'
    assert store.one('SELECT COUNT(*) n FROM documents')['n']==before
    reused=submit(admin,kid,raw,name='renamed.md',duplicate_policy='reuse')
    assert reused.status_code==201 and reused.json()['id']==first['id'] and reused.json()['reused']
    assert store.one('SELECT COUNT(*) n FROM documents')['n']==before
    new=submit(admin,kid,raw,name='资料.md',duplicate_policy='new_version')
    assert new.status_code==201 and new.json()['version']=='v2.0'
    assert new.json()['id']!=first['id']
    _,other=create_member(admin)
    assert submit(other,kid,raw,name='renamed.md').status_code==403
    assert client.get(f"/api/documents/{first['id']}/original",headers=admin).content==raw


def test_corrupted_s3_range_is_rejected_before_any_bytes(admin,monkeypatch):
    fake=FakeS3()
    monkeypatch.setenv('RAG_FILE_STORAGE','s3');monkeypatch.setenv('RAG_S3_BUCKET','range-regression')
    monkeypatch.setattr(object_storage,'client',lambda:fake)
    doc=upload(admin,kb(admin),text='Unchanged source content for range validation.')
    row=store.one('SELECT * FROM documents WHERE id=?',(doc['id'],))
    location=(row['storage_bucket'],row['object_key'])
    data,metadata=fake.data[location]
    fake.data[location]=(b'corrupted contents',metadata)
    response=client.get(f"/api/documents/{doc['id']}/original",headers={**admin,'Range':'bytes=0-3'})
    assert response.status_code==502 and '哈希' in response.json()['detail']
    fake.data[location]=(data,metadata)
    for header,expected in [('bytes=0-3',data[:4]),('bytes=-5',data[-5:])]:
        response=client.get(f"/api/documents/{doc['id']}/original",headers={**admin,'Range':header})
        assert response.status_code==206 and response.content==expected
    store.execute("UPDATE documents SET sha256='' WHERE id=?",(doc['id'],))
    assert client.get(f"/api/documents/{doc['id']}/original",headers=admin).status_code==409


def test_local_asr_missing_weights_are_explicit_and_never_downloaded(monkeypatch,tmp_path):
    from app import local_asr,media
    config=settings.RuntimeSettings(asr=settings.AsrSettings(provider='local'))
    monkeypatch.setattr(settings,'get',lambda:config)
    monkeypatch.setattr(local_asr,'ROOT',tmp_path)
    assert not media.asr_configured()
    with pytest.raises(RuntimeError,match='本地语音模型未准备完整'):
        local_asr.infer(tmp_path/'missing.wav','zh')
    with pytest.raises(ValueError,match='本地转写目前支持'):
        settings.AsrSettings(provider='local',model='unknown-model')


def test_local_asr_uses_offline_model_and_segment_timestamps(monkeypatch,tmp_path):
    import sys
    from types import SimpleNamespace
    from app import local_asr
    for name in ('model.bin','config.json','tokenizer.json'):
        (tmp_path/name).touch()
    calls=[]
    class Model:
        def __init__(self,path,**options):
            calls.append((path,options))
        def transcribe(self,path,**options):
            assert options['language']=='en'
            return iter([SimpleNamespace(start=.5,end=2,text=' passive cooling ')]),None
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr(local_asr,'ROOT',tmp_path)
    monkeypatch.setattr(local_asr,'_engine',None)
    for _ in range(2):
        text,segments,model=local_asr.infer(tmp_path/'sample.wav','en')
        assert text=='passive cooling' and segments==[{'start':.5,'end':2.,'text':text}]
    assert len(calls)==1 and calls[0][1]['local_files_only'] is True


def test_bilingual_rerank_uses_passage_language_without_losing_acl(admin,configured,monkeypatch):
    from app import local_models
    kid=kb(admin)
    english=upload(admin,kid,name='english.txt',text='The controller uses passive cooling and a ceramic enclosure to remove excess heat.')
    chinese=upload(admin,kid,name='chinese.txt',text='控制器使用被动散热和陶瓷外壳排出多余热量，维护之前必须先断电。')
    save_config(admin,lambda cfg:cfg['reranking'].update(provider='local'))
    query='控制器如何散热'
    async def plan(*args):
        return ([{'query':query,'group':'question','weight':1.,'language':'original'},
                 {'query':'How is the controller cooled?','group':'question','weight':1.,'language':'en'}],{})
    calls=[]
    async def rerank(config,question,texts):
        calls.append((question,texts))
        return [.9]*len(texts),{'device':'mock','at_length_limit':0}
    monkeypatch.setattr(retrieval_planner,'expand',plan)
    monkeypatch.setattr(local_models,'rerank',rerank)
    response=client.post('/api/search',headers=admin,json={'query':query,'mode':'keyword','knowledge_base_ids':[kid]})
    assert response.status_code==200,response.text
    assert {x['document_id'] for x in response.json()['results']}=={english['id'],chinese['id']}
    assert any(question=='How is the controller cooled?' and all('ceramic' in text for text in texts) for question,texts in calls)
    assert any(question==query and all('陶瓷' in text for text in texts) for question,texts in calls)


def test_citation_first_project_claim_gets_scope_judgement(admin,monkeypatch):
    monkeypatch.setenv('RAG_QA_VERIFICATION_MODE','citation-first')
    monkeypatch.setenv('RAG_QA_HEDGE_DELAY_SECONDS','0')
    calls=[]
    async def model(messages,**kwargs):
        calls.append(messages)
        if len(calls)==1:
            yield json.dumps({'claims':[{'text':'本项目使用JWT令牌。','references':[1],
                'quotes':[{'reference':1,'quote':'本教程使用JWT令牌。'}]}]})
        else:
            assert 'FastAPI tutorial' in messages[-1]['content']
            yield json.dumps({'verdicts':[{'claim_id':1,'verdict':'insufficient','quotes':[]}]})
    monkeypatch.setattr(rag,'llm_stream',model)
    evidence=[{'name':'FastAPI tutorial','section':'example','page':None,'version':'1','text':'本教程使用JWT令牌。'}]
    async def run():return [event async for event in grounding.verified_stream('本项目使用JWT吗？',evidence)]
    events=asyncio.run(run())
    assert len(calls)==2
    assert [e['content'] for e in events if e['type']=='token']==[grounding.REFUSAL]
    assert all(c['status']=='rejected' for e in events if e['type']=='verification' for c in e['checks'])


def test_citation_first_malformed_response_retries_with_more_budget(admin,monkeypatch):
    monkeypatch.setenv('RAG_QA_VERIFICATION_MODE','citation-first')
    monkeypatch.setenv('RAG_QA_HEDGE_DELAY_SECONDS','0')
    budgets=[]
    async def model(messages,max_tokens=None):
        budgets.append(max_tokens)
        if len(budgets)==1:yield '{"claims":['
        else:yield json.dumps({'claims':[{'text':'设备采用被动散热。','references':[1],
                    'quotes':[{'reference':1,'quote':'设备采用被动散热。'}]}]})
    monkeypatch.setattr(rag,'llm_stream',model)
    evidence=[{'name':'设备说明','section':'规格','page':None,'version':'1','text':'设备采用被动散热。'}]
    async def run():return [event async for event in grounding.verified_stream('设备如何散热？',evidence)]
    events=asyncio.run(run())
    assert budgets==[1400,4096]
    assert any('被动散热' in e['content'] for e in events if e['type']=='token')


def test_contextual_rerank_keeps_original_chunks_and_scope(admin,configured,monkeypatch):
    from app import local_models
    kid=kb(admin)
    before='Overview of the controller specification and its operating conditions.'
    main='NX90217 uses passive cooling for its ceramic enclosure.'
    after='The following maintenance instructions apply only after disconnecting power.'
    doc=upload(admin,kid,name='context.txt',text='\n\n'.join([before,main,after]))
    upload(admin,kb(admin),name='private.txt',text='PRIVATE_UNSELECTED_MATERIAL NX90217')
    save_config(admin,lambda cfg:(cfg['reranking'].update(provider='local'),cfg['answer_quality'].update(context_neighbors=2)))
    inputs=[]
    async def rerank(config,question,texts):
        inputs.extend(texts);return [.9]*len(texts),{}
    monkeypatch.setattr(local_models,'rerank',rerank)
    response=client.post('/api/search',headers=admin,json={'query':'NX90217','knowledge_base_ids':[kid],'mode':'keyword','top_k':1})
    assert response.status_code==200,response.text
    result=response.json()
    assert any(before in text and main in text and after in text for text in inputs)
    assert all('PRIVATE_UNSELECTED_MATERIAL' not in text for text in inputs)
    assert result['results'][0]['text']==main
    assert {r['text'] for r in result['results']}=={before,main,after}
    assert all(r['document_id']==doc['id'] for r in result['results'])
    assert result['pipeline']['reranking']['adjacent_context'] is True


def test_adjacent_evidence_budget_skips_intervening_heading(admin,configured,monkeypatch):
    from app import local_models
    kid=kb(admin)
    prior='Alpha only stores files; interpreting their contents is a separate step.'
    main='Alpha handling uses a separate processing stage for extracted content.'
    doc=upload(admin,kid,name='boundaries.md',text='# Storage\n\n'+prior+'\n\n## Processing\n\n'+main)
    save_config(admin,lambda cfg:(cfg['reranking'].update(provider='local'),cfg['answer_quality'].update(context_neighbors=1)))
    async def rank(config,query,texts):return [.99 if main in text else .01 for text in texts],{}
    monkeypatch.setattr(local_models,'rerank',rank)
    response=client.post('/api/search',headers=admin,json={'query':'Alpha handling','knowledge_base_ids':[kid],'mode':'keyword','top_k':1})
    assert response.status_code==200,response.text
    result=response.json()['results']
    assert [item['text'] for item in result]==[main,prior]
    assert result[1]['document_id']==doc['id'] and result[1]['kind']=='adjacent-context'


@pytest.mark.parametrize('header',['<h2>NX90217 cooling</h2>','<p>5.1.1. NX90217 cooling</p>'])
def test_html_heading_cannot_replace_factual_body(admin,configured,monkeypatch,header):
    from app import local_models
    kid=kb(admin)
    doc=upload(admin,kid,name='heading.html',text=header+'<p>NX90217 uses passive cooling.</p>')
    save_config(admin,lambda cfg:cfg['reranking'].update(provider='local'))
    seen=[]
    async def rank(config,question,texts):
        seen.extend(texts);return [.9]*len(texts),{}
    monkeypatch.setattr(local_models,'rerank',rank)
    response=client.post('/api/search',headers=admin,json={'query':'NX90217 cooling','knowledge_base_ids':[kid],'mode':'keyword'})
    assert response.status_code==200,response.text
    assert seen==['NX90217 uses passive cooling.']
    assert response.json()['results'][0]['document_id']==doc['id']
    assert not rag.heading_only('5.1. Voltage 24 V','正文')


def test_partial_project_scope_cannot_hide_missing_conclusion(admin,monkeypatch):
    monkeypatch.setenv('RAG_QA_VERIFICATION_MODE','citation-first')
    monkeypatch.setenv('RAG_QA_HEDGE_DELAY_SECONDS','0')
    calls=[]
    async def model(messages,**kwargs):
        calls.append(1)
        quote={'reference':1,'quote':'论文在实验中提升19%。'}
        if len(calls)==1:
            yield json.dumps({'claims':[{'text':'论文在实验中提升19%。','references':[1],'quotes':[quote]},
                {'text':'本项目也提升19%。','references':[1],'quotes':[quote]}]})
        else:
            yield json.dumps({'verdicts':[{'claim_id':1,'verdict':'supported','quotes':[quote]},
                {'claim_id':2,'verdict':'insufficient','quotes':[]}]})
    monkeypatch.setattr(rag,'llm_stream',model)
    evidence=[{'name':'外部论文','section':'实验','page':1,'version':'1','text':'论文在实验中提升19%。'}]
    async def run():return [event async for event in grounding.verified_stream('本项目也提升19%吗？',evidence)]
    events=asyncio.run(run())
    content=''.join(e['content'] for e in events if e['type']=='token')
    assert '本项目也提升19%' not in content and '证据不足' in content
    checks=[c for e in events if e['type']=='verification' for c in e['checks']]
    assert [(c['claim_id'],c['status']) for c in checks]==[(1,'supported'),(2,'rejected')]


def test_compound_rerank_reserves_direct_evidence_for_each_part(admin,configured,monkeypatch):
    from app import local_models
    kid=kb(admin)
    upload(admin,kid,name='Alpha.txt',text='Alpha 的供电方式为电池供电，使用可更换电池。')
    upload(admin,kid,name='Beta.txt',text='Beta 使用说明和限制值概览，未提供具体参数。\n\nBeta 的限制值为42次，操作需要遵守此上限。')
    save_config(admin,lambda cfg:cfg['reranking'].update(provider='local'))
    question='比较Alpha供电方式与Beta限制值'
    async def plan(*args):
        return ([{'query':question,'group':'question','weight':1.,'language':'original'},
                 {'query':'Alpha 供电方式','group':'part-1','weight':.8,'language':'original'},
                 {'query':'Beta 限制值','group':'part-2','weight':.8,'language':'original'}],{})
    calls=[]
    async def rank(config,query,texts):
        calls.append(query)
        if query=='Beta 限制值':scores=[.99 if '42次' in text else .01 for text in texts]
        else:scores=[.99 if '概览' in text else .98 if '电池' in text else .1 for text in texts]
        return scores,{}
    monkeypatch.setattr(retrieval_planner,'expand',plan)
    monkeypatch.setattr(local_models,'rerank',rank)
    response=client.post('/api/search',headers=admin,json={'query':question,'knowledge_base_ids':[kid],'mode':'keyword','top_k':2})
    assert response.status_code==200,response.text
    result=response.json()
    assert any('电池' in r['text'] for r in result['results'])
    assert any('42次' in r['text'] for r in result['results'])
    assert all('概览' not in r['text'] for r in result['results'])
    assert set(calls)=={question,'Alpha 供电方式','Beta 限制值'}


def test_query_plan_cancel_does_not_cancel_another_waiter(monkeypatch):
    retrieval_planner._cache.clear()
    config=settings.RuntimeSettings();config.retrieval.cross_language=True
    monkeypatch.setattr(settings,'get',lambda:config)
    monkeypatch.setattr(rag,'llm_config',lambda:('test-key','https://example.invalid','test-model'))
    calls=[]
    async def run():
        started=asyncio.Event();release=asyncio.Event()
        async def model(*args,**kwargs):
            calls.append(1);started.set();await release.wait()
            yield json.dumps({'chinese':'控制器如何散热','english':'controller cooling','subquestions':[]})
        monkeypatch.setattr(rag,'llm_stream',model)
        args=('控制器如何散热',[991],[{'text':'The controller uses a passive cooling system with a ceramic enclosure.'}])
        first=asyncio.create_task(retrieval_planner.expand(*args));await started.wait()
        second=asyncio.create_task(retrieval_planner.expand(*args));await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):await first
        release.set()
        result=await second
        assert result[1]['status']=='expanded' and len(calls)==1
    asyncio.run(run())
    assert not retrieval_planner._inflight


def test_query_plan_two_event_loops_do_not_share_tasks(monkeypatch):
    retrieval_planner._cache.clear()
    config=settings.RuntimeSettings();config.retrieval.cross_language=True
    monkeypatch.setattr(settings,'get',lambda:config)
    monkeypatch.setattr(rag,'llm_config',lambda:('test-key','https://example.invalid','test-model'))
    async def model(*args,**kwargs):
        await asyncio.sleep(.02)
        yield json.dumps({'chinese':'控制器如何散热','english':'controller cooling','subquestions':[]})
    monkeypatch.setattr(rag,'llm_stream',model)
    def run(scope):
        return asyncio.run(retrieval_planner.expand('控制器如何散热',[scope],
            [{'text':'The controller uses a passive cooling system with a ceramic enclosure.'}]))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(run,[992,993]))
    assert all(result[1]['status']=='expanded' for result in results)
    assert not retrieval_planner._inflight
