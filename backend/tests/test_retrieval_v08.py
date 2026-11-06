"""Generic bilingual/focused-retrieval regressions; no web-set answers in code."""
import asyncio
import json
import pytest
from test_api import client,admin,application_lifespan,kb,upload,configured,save_config,create_member,wait_job,fake_model
from app import rag,retrieval_planner,report_retrieval,store,settings

def enable(admin):
    save_config(admin,lambda cfg:cfg['retrieval'].update(cross_language=True))

@pytest.fixture(autouse=True)
def clear_plans():
    retrieval_planner._cache.clear()
    yield
    retrieval_planner._cache.clear()

@pytest.mark.parametrize('mode',['vector','keyword','hybrid'])
def test_chinese_question_retrieves_english_fact_without_answer_leak(admin,configured,monkeypatch,mode):
    enable(admin);kid=kb(admin)
    doc=upload(admin,kid,name='english-manual.txt',text='The Orion controller uses a ceramic heat sink for passive cooling. The manual describes thermal dissipation without any moving fan. This design requires unobstructed ventilation around the enclosure.')
    calls=[]
    async def translator(messages,max_tokens=None):
        data=json.loads(messages[-1]['content']);calls.append(data)
        assert 'retrieval_queries_schema' in data and 'evidence_json' not in data
        assert 'ceramic' not in json.dumps(data)  # no gold answer or corpus sent
        yield json.dumps({'chinese':'Orion控制器如何散热？','english':'How does the Orion controller dissipate heat?','subquestions':[]})
    monkeypatch.setattr(rag,'llm_stream',translator)
    body={'query':'Orion控制器如何散热？','mode':mode,'knowledge_base_ids':[kid]}
    response=client.post('/api/search',headers=admin,json=body)
    assert response.status_code==200,response.text
    result=response.json();assert doc['id'] in {d['document_id'] for d in result['results']}
    assert result['pipeline']['embedding']==('not-used' if mode=='keyword' else 'local-tfidf')
    assert result['pipeline']['query_expansion']['status']=='expanded'
    assert result['results'][0]['text'] in store.one('SELECT content FROM documents WHERE id=?',(doc['id'],))['content']
    assert client.post('/api/search',headers=admin,json=body).json()['pipeline']['query_expansion']['cache']=='hit'
    assert len(calls)==1

def test_rewrite_cache_scope_and_failure_are_explicit(admin,configured,monkeypatch):
    enable(admin);kid=kb(admin);other=kb(admin)
    text='The Orion controller uses passive cooling through an external heat sink. The product engineering manual describes an installation with unobstructed ventilation and heat dissipation.'
    upload(admin,kid,text=text);upload(admin,other,text=text)
    calls=[]
    async def bad(messages,max_tokens=None):calls.append(1);yield '{not valid json'
    monkeypatch.setattr(rag,'llm_stream',bad)
    body={'query':'Orion控制器散热说明','knowledge_base_ids':[kid]}
    response=client.post('/api/search',headers=admin,json=body).json()
    assert response['pipeline']['query_expansion']['status']=='degraded'
    assert response['pipeline']['query_expansion']['warnings']
    assert not retrieval_planner._cache  # no permanently cached failures
    user,headers=create_member(admin)
    denied=client.post('/api/search',headers=headers,json=body)
    assert denied.status_code==403 and len(calls)==1  # ACL before paid rewrite
    async def good(messages,max_tokens=None):
        calls.append(1);yield json.dumps({'chinese':'Orion散热','english':'Orion heat dissipation','subquestions':[]})
    monkeypatch.setattr(rag,'llm_stream',good)
    client.post('/api/search',headers=admin,json=body)
    client.post('/api/search',headers=admin,json={**body,'knowledge_base_ids':[other]})
    assert len(calls)==3

def test_compound_question_covers_both_subtopics_and_latest_authorized_versions(admin,configured,monkeypatch):
    enable(admin);kid=kb(admin)
    a=upload(admin,kid,name='controller.txt',text='The Orion controller cools the enclosure using a ceramic heat sink and unobstructed ventilation. No moving fan is required for its passive thermal dissipation system.')
    b=upload(admin,kid,name='sensor.txt',text='The Vega sensor measures relative humidity using a calibrated capacitance probe. Its product manual describes the calibration procedure and environmental operating conditions for accurate measurements.')
    hidden=kb(admin);upload(admin,hidden,name='hidden.txt',text='The Orion controller and the Vega sensor are secret hidden material and must never enter another knowledge scope. They measure temperature with an unrelated method.')
    async def translator(messages,max_tokens=None):
        yield json.dumps({'chinese':'Orion如何散热及Vega测量什么？','english':'How does Orion dissipate heat and what does Vega measure?',
            'subquestions':[{'chinese':'Orion如何散热？','english':'How does Orion dissipate heat?'},
                            {'chinese':'Vega测量什么？','english':'What does the Vega sensor measure?'}]})
    monkeypatch.setattr(rag,'llm_stream',translator)
    response=client.post('/api/search',headers=admin,json={'query':'Orion如何散热及Vega测量什么？','knowledge_base_ids':[kid],'top_k':2}).json()
    assert {d['document_id'] for d in response['results']}=={a['id'],b['id']}
    assert all(response['pipeline']['part_coverage'].values())
    new=upload(admin,kid,name='controller.txt',text='The Orion controller cools its enclosure with a ceramic heat sink and has a revised manual describing the passive thermal dissipation and the required ventilation distance for installation.')
    result=client.post('/api/search',headers=admin,json={'query':'Orion如何散热及Vega测量什么？','knowledge_base_ids':[kid],'top_k':5}).json()
    assert a['id'] not in {d['document_id'] for d in result['results']}
    assert new['id'] in {d['document_id'] for d in result['results']}

def test_rewrite_timeout_falls_back_without_disabling_gates(admin,configured,monkeypatch):
    enable(admin);save_config(admin,lambda cfg:cfg['retrieval'].update(query_rewrite_timeout_seconds=2))
    kid=kb(admin);upload(admin,kid,text='The Orion technical manual describes a calibrated measurement system using a ceramic controller and passive thermal cooling for the sensor enclosure during normal operation.')
    async def blocked(messages,max_tokens=None):
        await asyncio.sleep(4);yield '{}'
    monkeypatch.setattr(rag,'llm_stream',blocked)
    response=client.post('/api/search',headers=admin,json={'query':'不存在的中文量子水稻','knowledge_base_ids':[kid]}).json()
    assert response['pipeline']['query_expansion']['status']=='degraded'
    assert response['results']==[]

@pytest.mark.parametrize('path',['/api/search','/api/qa/stream'])
def test_revocation_during_rewrite_never_returns_evidence(admin,configured,monkeypatch,path):
    enable(admin);kid=kb(admin)
    upload(admin,kid,text='The Orion controller technical manual describes passive cooling using an external heat sink. The hardware requires unobstructed ventilation around the enclosure to dissipate heat during normal operation.')
    user,headers=create_member(admin)
    client.put(f"/api/users/{user['id']}/grants",headers=admin,json={'grants':[{'knowledge_base_id':kid,'access':'read'}]})
    before=store.one('SELECT COUNT(*) n FROM conversations')['n']
    async def revoke(messages,max_tokens=None):
        store.execute('DELETE FROM kb_grants WHERE user_id=? AND kb_id=?',(user['id'],kid))
        yield json.dumps({'chinese':'Orion散热','english':'Orion heat dissipation','subquestions':[]})
    monkeypatch.setattr(rag,'llm_stream',revoke)
    response=client.post(path,headers=headers,json={'query':'Orion散热说明','knowledge_base_ids':[kid]})
    assert response.status_code==403
    assert 'heat sink' not in response.text
    assert store.one('SELECT COUNT(*) n FROM conversations')['n']==before

def test_report_preflight_and_regeneration_share_section_focus(admin,configured,monkeypatch):
    kid=kb(admin)
    upload_doc=upload(admin,kid,name='upload.txt',text='UploadFile只负责接收客户端上传的文件。文件上传过程不等于完成后续图像识别，文件接收使用内存与磁盘暂存机制。')
    optical=upload(admin,kid,name='optical.txt',text='Lumen解析器只提取文档已有文字层，不会识别扫描图像中的文字。扫描件只有图像时需要独立视觉识别步骤，不能把解析文字层当作图像识别。')
    seen=[];original=report_retrieval.retrieve_section
    async def observe(*args,**kwargs):
        result=await original(*args,**kwargs);seen.append(result[1]);return result
    monkeypatch.setattr(report_retrieval,'retrieve_section',observe)
    monkeypatch.setattr(rag,'llm_stream',fake_model)
    template=client.post('/api/report-templates',headers=admin,json={'blueprint':{'name':'接入报告','purpose':'文档接入',
        'sections':[{'title':'文件接收','instructions':'说明UploadFile'},
                    {'title':'Lumen解析器与视觉识别边界','instructions':'区分文字层与扫描图像'}]},'knowledge_base_ids':[kid],'lifecycle':'published'}).json()['id']
    params={'title':'文件上传与接收机制报告','template_id':template,'knowledge_base_id':kid,'workflow_version':1,
        'requirements':'UploadFile文件接收上传内存磁盘，'+('只写两句，每句引用，不编造。'*30)}
    preflight=client.post('/api/reports/preflight',headers=admin,json=params).json()
    second=preflight['sections'][1]
    assert optical['id'] in {e['document_id'] for e in second['evidence']}
    assert second['retrieval']['policy']=='section-focus-v4'
    focused=second['retrieval']['attempts'][0]['query']
    assert 'UploadFile' not in focused and '两句' not in focused
    created=client.post('/api/report-tasks',headers=admin,json={**params,'preflight_id':preflight['preflight_id'],'allow_missing':True}).json()
    wait_job(admin,created['job_id'])
    report=client.get(f"/api/reports/{created['id']}",headers=admin).json();sec=report['sections'][1]
    job=client.post(f"/api/report-tasks/{report['id']}/sections/{sec['id']}/regenerate",headers=admin,json={'expected_revision':sec['revision'],'preview':True}).json()
    wait_job(admin,job['job_id'])
    assert seen[-1]['attempts'][0]['query']==focused
    final=client.get(f"/api/reports/{created['id']}",headers=admin).json()
    assert final['sections'][0]['content']==report['sections'][0]['content']
    assert optical['id'] in {e['document_id'] for e in final['candidates'][0]['citations']}


def test_imported_template_notes_focus_section_without_global_topic_bleed(monkeypatch):
    structure={'body_order':['a','b','c','d'], 'blocks':[
        {'id':'a','type':'heading','text':'2 基准测试脚本与模型规模'},
        {'id':'b','type':'paragraph','text':'来源定位：原作业第 2-4 页；对应题目：benchmarking_script。'},
        {'id':'c','type':'paragraph','text':'本节覆盖：模型规模表与基准计时方法。'},
        {'id':'d','type':'heading','text':'3 Nsight Systems 性能剖析'}]}
    hint=report_retrieval.template_section_hint(structure,'a')
    assert 'benchmarking_script' in hint and '模型规模表' in hint
    scoped=report_retrieval.scoped_requirements('逐章写实验要求。重点说明 flash_forward 的 Triton 实现。',
        '2 基准测试脚本与模型规模',hint)
    assert '逐章' in scoped and 'flash_forward' not in scoped
    assert 'flash_forward' in report_retrieval.scoped_requirements('重点说明 flash_forward。',
        '7 FlashAttention 前向','对应题目：flash_forward')
    calls=[]
    async def fake_retrieve(query,ids,**kwargs):
        calls.append((query,kwargs))
        return [{'text':'已命中原文'}],{}
    monkeypatch.setattr(rag,'retrieve',fake_retrieve)
    asyncio.run(report_retrieval.retrieve_section(1,{'title':'2 基准测试脚本与模型规模','block_id':'a'},
        template_structure=structure))
    assert 'benchmarking_script' in calls[0][0] and calls[0][1]['expand_queries'] is False
