"""Two report generation policies, with isolated storage and mocked models."""
import asyncio
import json
import pytest
from test_api import client,admin,application_lifespan,wait_job,configured,create_member,fake_model
from test_workflow_v07 import setup_report,create
from app import rag,report_grounding as g,report_workflow as workflow,report_layout

EVIDENCE=[{'chunk_id':1,'document_id':1,'version':'v1','text':'混合检索使用BM25和RRF。'}]

def test_fulltext_reference_remap_keeps_one_document():
    text,order,invalid=workflow.remap_references('甲[2] 乙[2] 丙[1]',EVIDENCE*2)
    assert text=='甲[1] 乙[1] 丙[2]' and order==[EVIDENCE[0],EVIDENCE[0]] and not invalid
    _,_,invalid=workflow.remap_references('正文[9]',EVIDENCE)
    assert invalid==[9]

def test_fulltext_generation_uses_one_model_call_and_keeps_one_document(admin,monkeypatch):
    params,_=setup_report(admin,monkeypatch)
    calls=[]
    async def model(messages,max_tokens=4096):
        data=json.loads(messages[-1]['content'])
        if 'verification_schema' in data:
            async for token in fake_model(messages,max_tokens):yield token
            return
        if 'coverage_schema' in data:
            yield json.dumps({'items':[{'requirement':'报告要求','status':'covered','references':[]}]},ensure_ascii=False)
            return
        calls.append(data)
        assert '一次输出完整报告正文' in data['writing_requirements']
        assert [section['title'] for section in data['sections']]==['混合检索方案','混合检索实施']
        for token in ('## 1. 混合检索方案\n','检索证据表明，采用混合检索。[1]\n\n',
                      '## 2. 混合检索实施\n','检索证据表明，采用混合检索。[1]'):
            yield token
    monkeypatch.setattr(rag,'llm_stream',model)
    report=create(admin,params)
    assert len(calls)==1
    assert report['generation_policy']=='whole-document-v1'
    assert report['status']=='completed'
    assert len(report['sections'])==1 and report['sections'][0]['title']=='全文正文'
    assert '## 1. 混合检索方案' in report['sections'][0]['content']
    assert '## 2. 混合检索实施' in report['sections'][0]['content']
    assert report['sections'][0]['verification']['state']=='verified'
    assert report['sections'][0]['generation_status']=='completed'
    preview=client.get(f"/api/reports/{report['id']}/preview",headers=admin).json()
    rendered=report_layout.markdown(preview['nodes'])
    assert '## 1. 混合检索方案' in rendered and '## 2. 混合检索实施' in rendered

def test_synthesis_preserves_analysis_and_does_not_claim_verified(monkeypatch):
    async def model(messages,**kwargs):
        data=json.loads(messages[-1]['content'])
        assert data['evidence_json'][0]['text']==EVIDENCE[0]['text']
        assert '待实测' in messages[0]['content']
        yield '混合检索使用BM25和RRF。[1]\n建议：建立实验方案，记录待实测结果。'
    async def forbidden(*args,**kwargs):raise AssertionError('synthesis must not invoke strict filtering')
    monkeypatch.setattr(rag,'llm_stream',model)
    monkeypatch.setattr(g.grounding,'check_batch',forbidden)
    text,v=asyncio.run(workflow.generate_section('写实验报告',EVIDENCE,lambda:None,generation_mode='synthesis'))
    assert '建议' in text and v['state']=='synthesized' and not v['publishable']
    assert v['accepted_count']==0 and g.current({'content':text,'citations':EVIDENCE,'verification':v})['state']=='synthesized'

@pytest.mark.parametrize('text',['无引用正文','正文[0]','正文[2]','   '])
def test_synthesis_rejects_invalid_model_citations(monkeypatch,text):
    async def model(*args,**kwargs):yield text
    monkeypatch.setattr(rag,'llm_stream',model)
    with pytest.raises(ValueError):asyncio.run(g.generate_synthesis('正文',EVIDENCE,lambda:None))

def test_missing_evidence_never_calls_model(monkeypatch):
    async def forbidden(*args,**kwargs):
        raise AssertionError('no evidence must not invoke model')
        yield ''
    monkeypatch.setattr(rag,'llm_stream',forbidden)
    text,v=asyncio.run(g.generate_synthesis('写报告',[],lambda:None))
    assert '待补充' in text and v['state']=='insufficient'

def test_revocation_prevents_synthesis_publication(monkeypatch):
    revoked=False
    async def model(*args,**kwargs):
        nonlocal revoked
        revoked=True
        yield '事实。[1]'
    def authorize():
        if revoked:raise PermissionError('revoked')
    monkeypatch.setattr(rag,'llm_stream',model)
    with pytest.raises(PermissionError):asyncio.run(g.generate_synthesis('报告',EVIDENCE,authorize))

def test_mode_preflight_snapshot_regeneration_review_and_export(admin,monkeypatch,configured):
    params,_=setup_report(admin,monkeypatch)
    checked=client.post('/api/reports/preflight',headers=admin,json=params).json()
    assert client.post('/api/report-tasks',headers=admin,json={**params,'preflight_id':checked['preflight_id'],'generation_mode':'synthesis'}).status_code==409
    assert client.post('/api/report-tasks',headers=admin,json={**params,'generation_mode':'invent'}).status_code==422
    report=create(admin,{**params,'generation_mode':'synthesis'})
    tid=report['id'];sec=report['sections'][0]
    assert report['generation_mode']=='synthesis'
    assert report['template_snapshot']['generation_mode']=='synthesis'
    assert sec['verification']['state']=='synthesized'
    _,other=create_member(admin)
    assert client.get(f'/api/reports/{tid}',headers=other).status_code==404
    queued=client.post(f"/api/report-tasks/{tid}/sections/{sec['id']}/regenerate",headers=admin,json={'expected_revision':sec['revision'],'preview':True}).json()
    assert wait_job(admin,queued['job_id'])['status']=='needs_review'
    latest=client.get(f'/api/reports/{tid}',headers=admin).json()
    assert latest['candidates'][0]['verification']['state']=='synthesized'
    assert len(latest['sections'])==1 and latest['sections'][0]['content']==report['sections'][0]['content']
    # Human review remains the final publication decision, mode freezes into release.
    for section in latest['sections']:
        assert client.post(f"/api/reports/{tid}/sections/{section['id']}/review",headers=admin,json={'expected_revision':section['revision']}).status_code==200
    latest=client.get(f'/api/reports/{tid}',headers=admin).json()
    response=client.post(f'/api/reports/{tid}/finalize',headers=admin,json={'expected_revision':latest['revision'],'fixed_text_reviewed':True})
    assert response.status_code==201,response.text
    exported=client.get(f'/api/reports/{tid}/export?format=md&mode=final',headers=admin)
    assert '基于证据综合生成' in exported.text and '未执行逐句事实核验' in exported.text

def test_old_reports_default_to_strict():
    assert g.generation_mode({'template_snapshot':'{}'})=='strict'
