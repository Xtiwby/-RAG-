"""Integration tests use an isolated database and a deterministic mocked model (no paid API calls)."""
import asyncio
import hashlib
import io
import json
import os
import tempfile
import uuid
import zipfile
import time
import threading
from pathlib import Path
from urllib.parse import quote
from xml.etree import ElementTree

_data=tempfile.TemporaryDirectory(prefix='rag-api-tests-')
os.environ['RAG_DATA_DIR']=_data.name
# Tests must never inherit the live PostgreSQL/S3 cutover from backend/.env.
os.environ['RAG_DATABASE_URL']=''
os.environ['RAG_FILE_STORAGE']='local'
os.environ['RAG_WORKER_MODE']='embedded'
os.environ['KIMI_API_KEY']='test-only-not-a-real-key'
os.environ['EMBEDDING_BASE_URL']=''
os.environ['EMBEDDING_MODEL']=''
from fastapi.testclient import TestClient
import httpx
import pytest
from app.main import app
from app import rag,store
from app import settings,jobs,blueprints,grounding

client=TestClient(app)

@pytest.fixture(scope='session',autouse=True)
def application_lifespan():
    with client:
        yield

def login(username='admin',password='admin123'):
    response=client.post('/api/auth/login',json={'username':username,'password':password})
    assert response.status_code==200,response.text
    return {'Authorization':'Bearer '+response.json()['token']}

@pytest.fixture
def admin():
    return login()

def kb(admin,title='测试知识库'):
    response=client.post('/api/knowledge-bases',headers=admin,json={'name':title})
    assert response.status_code==200,response.text
    return response.json()['id']

def upload(admin,kid,name='资料.md',text='# 混合检索\n\n混合检索采用向量检索、BM25 关键词检索与 RRF 排名融合。实施计划：建立检索索引并评估召回率。'):
    response=client.post('/api/documents',headers=admin,data={'knowledge_base_id':kid},files={'file':(name,text.encode(),'text/markdown')})
    assert response.status_code==201,response.text
    return finish_upload(admin,response.json())

def finish_upload(headers,doc):
    for _ in range(200):
        current=client.get(f"/api/documents/{doc['id']}",headers=headers).json()
        if current['status'] not in {'uploading','processing'}:
            return current
        time.sleep(.03)
    raise AssertionError('文档未完成后台处理')

def events(response):
    assert response.status_code==200,response.text
    return [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith('data:')]

def response_text(stream):
    return ''.join(event.get('content','') for event in stream if event['type']=='token')

def create_member(admin):
    name='u'+uuid.uuid4().hex[:10]
    response=client.post('/api/users',headers=admin,json={'username':name,'name':'新用户','password':'test123456','role_id':2})
    assert response.status_code==201,response.text
    return response.json(),login(name,'test123456')

def test_document_compact_window_locates_cited_chunk(admin):
    kid=kb(admin)
    doc=upload(admin,kid,name='长文.md',text=' '.join(f'段落{i}说明检索定位。' for i in range(600)))
    assert len(doc['chunks'])>5
    target=doc['chunks'][4]['id']
    response=client.get(f"/api/documents/{doc['id']}?compact=true&limit=3&chunk_id={target}",headers=admin)
    assert response.status_code==200,response.text
    page=response.json()
    assert page['chunk_count']==len(doc['chunks']) and len(page['chunks'])==3
    assert target in {part['id'] for part in page['chunks']}
    assert 'structured' not in page
    assert client.get(f"/api/documents/{doc['id']}?compact=true&limit=101",headers=admin).status_code==422

def test_auth_acl_roles_and_revocation(admin):
    kid=kb(admin)
    doc=upload(admin,kid)
    user,headers=create_member(admin)
    for path in ('/api/users','/api/audits',f"/api/documents/{doc['id']}",f"/api/documents/{doc['id']}/original"):
        assert client.get(path,headers=headers).status_code==403
    assert client.get('/api/knowledge-bases',headers=headers).json()==[]
    assert client.post('/api/search',headers=headers,json={'query':'混合检索','knowledge_base_ids':[kid]}).status_code==403
    assert client.get('/api/dashboard',headers=headers).json()['counts']['documents']==0
    grant={'grants':[{'knowledge_base_id':kid,'access':'read'}]}
    assert client.put(f"/api/users/{user['id']}/grants",headers=admin,json=grant).status_code==200
    assert len(client.get('/api/documents',headers=headers).json())==1
    assert client.get(f"/api/documents/{doc['id']}/original",headers=headers).status_code==200
    assert client.post('/api/documents',headers=headers,data={'knowledge_base_id':kid},files={'file':('x.txt',b'no')}).status_code==403
    assert client.delete(f"/api/documents/{doc['id']}",headers=headers).status_code==403
    # Role permission points take effect on existing sessions, without trusting the cached role in the UI.
    role=client.post('/api/roles',headers=admin,json={'name':uuid.uuid4().hex,'permissions':['kb.read','qa.use']}).json()['id']
    client.patch(f"/api/users/{user['id']}",headers=admin,json={'name':'新用户','role_id':role,'status':'active'})
    assert client.get('/api/auth/me',headers=headers).status_code==401
    headers=login(user['username'],'test123456')
    client.put(f'/api/roles/{role}',headers=admin,json={'name':uuid.uuid4().hex,'permissions':['kb.read']})
    assert client.post('/api/search',headers=headers,json={'query':'混合检索'}).status_code==403
    client.patch(f"/api/users/{user['id']}",headers=admin,json={'name':'新用户','role_id':role,'status':'disabled'})
    assert client.get('/api/auth/me',headers=headers).status_code==401
    assert client.post('/api/auth/login',json={'username':user['username'],'password':'test123456'}).status_code==401

def test_public_library_is_readable_without_grants(admin):
    user,member=create_member(admin)
    body={'name':'公开资料','visibility':'public'}
    kid=client.post('/api/knowledge-bases',headers=admin,json=body).json()['id']
    doc=upload(admin,kid)
    path=f"/api/documents/{doc['id']}"
    libraries=client.get('/api/knowledge-bases',headers=member).json()
    assert {row['id']:row['access'] for row in libraries}[kid]=='read'
    assert client.get('/api/documents',headers=member).json()[0]['id']==doc['id']
    assert client.get(path+'/original',headers=member).status_code==200
    assert client.get(path,headers=member).status_code==200
    assert client.get(path).status_code==401
    assert client.post('/api/documents',headers=member,data={'knowledge_base_id':kid},
                       files={'file':('另一个文档.txt',b'test')}).status_code==403
    assert client.delete(path,headers=member).status_code==403
    search={'query':'混合检索','mode':'keyword','knowledge_base_ids':[kid]}
    result=client.post('/api/search',headers=member,json=search)
    assert result.status_code==200 and result.json()['results']

    grants_path=f"/api/users/{user['id']}/grants"
    assert client.put(grants_path,headers=admin,json={'grants':[{'knowledge_base_id':kid,'access':'write'}]}).status_code==200
    assert upload(member,kid,name='授权后上传.md')['status']=='ready'
    assert client.put(grants_path,headers=admin,json={'grants':[]}).status_code==200
    assert client.patch(f'/api/knowledge-bases/{kid}',headers=admin,json={**body,'visibility':'team'}).status_code==200
    assert client.get(path+'/original',headers=member).status_code==403
    assert client.post('/api/search',headers=member,json=search).status_code==403


def test_team_library_members_control_access(admin):
    reader,read_headers=create_member(admin)
    writer,write_headers=create_member(admin)
    _,outsider=create_member(admin)
    kid=client.post('/api/knowledge-bases',headers=admin,json={'name':'团队资料','visibility':'team'}).json()['id']
    other=kb(admin,'其他资料')
    assert client.put(f"/api/users/{reader['id']}/grants",headers=admin,
                      json={'grants':[{'knowledge_base_id':other,'access':'read'}]}).status_code==200
    path=f'/api/knowledge-bases/{kid}/members'
    changes={'members':[{'user_id':reader['id'],'access':'read'},{'user_id':writer['id'],'access':'write'}]}
    assert client.get(path,headers=outsider).status_code==403
    assert client.patch(path,headers=outsider,json=changes).status_code==403
    assert client.patch(path,headers=admin,json=changes).status_code==200
    members={row['id']:row['access'] for row in client.get(path,headers=admin).json()}
    assert members[reader['id']]=='read' and members[writer['id']]=='write'
    doc=upload(write_headers,kid)
    original=f"/api/documents/{doc['id']}/original"
    assert client.get(original,headers=read_headers).status_code==200
    assert client.get(original,headers=outsider).status_code==403
    assert kid not in {row['id'] for row in client.get('/api/knowledge-bases',headers=outsider).json()}
    assert client.patch(path,headers=admin,json={'members':[{'user_id':reader['id'],'access':None}]}).status_code==200
    assert client.get(original,headers=read_headers).status_code==403
    assert client.get(original,headers=write_headers).status_code==200
    assert {row['id'] for row in client.get('/api/knowledge-bases',headers=read_headers).json()}=={other}


def test_public_read_access_does_not_expand_delegated_grants(admin):
    role=client.post('/api/roles',headers=admin,json={'name':'授权助理','permissions':['kb.read','roles.manage']}).json()
    delegate,_=create_member(admin)
    assert client.patch(f"/api/users/{delegate['id']}",headers=admin,
                        json={'name':delegate['name'],'role_id':role['id'],'status':'active'}).status_code==200
    headers=login(delegate['username'],'test123456')
    target,_=create_member(admin)
    kid=client.post('/api/knowledge-bases',headers=admin,json={'name':'只读公开资料','visibility':'public'}).json()['id']
    path=f"/api/users/{target['id']}/grants"
    grant={'grants':[{'knowledge_base_id':kid,'access':'read'}]}
    assert client.put(path,headers=headers,json=grant).status_code==403
    assert client.put(path,headers=admin,json=grant).status_code==200
    assert client.put(path,headers=headers,json={'grants':[]}).status_code==403
    assert client.get(path,headers=admin).json()==grant['grants']
    assert client.patch(f'/api/knowledge-bases/{kid}',headers=admin,
                        json={'name':'只读公开资料','visibility':'private'}).status_code==200


def test_profile_password_and_reset(admin):
    user,headers=create_member(admin)
    response=client.patch('/api/auth/me',headers=headers,json={'name':'显示名更新'})
    assert response.json()['name']=='显示名更新'
    assert client.post('/api/auth/password',headers=headers,json={'current_password':'wrong','new_password':'another123'}).status_code==400
    assert client.post('/api/auth/password',headers=headers,json={'current_password':'test123456','new_password':'another123'}).status_code==200
    assert client.get('/api/auth/me',headers=headers).status_code==401
    headers=login(user['username'],'another123')
    result=client.post(f"/api/users/{user['id']}/reset-password",headers=admin,json={}).json()
    assert client.get('/api/auth/me',headers=headers).status_code==401
    login(user['username'],result['temporary_password'])
    assert client.patch('/api/users/1',headers=admin,json={'name':'admin','role_id':1,'status':'disabled'}).status_code==409

def test_original_versions_chunks_search_and_archive(admin):
    kid=kb(admin)
    content='# 检索章节\n\n混合检索采用 RRF 融合。\n\n测试内容。'
    first=upload(admin,kid,text=content)
    assert first['sha256']==hashlib.sha256(content.encode()).hexdigest()
    detail=client.get(f"/api/documents/{first['id']}",headers=admin).json()
    assert detail['chunks'] and detail['chunks'][0]['section']=='检索章节'
    assert all(c['page'] is None for c in detail['chunks'])
    original=client.get(f"/api/documents/{first['id']}/original",headers=admin)
    assert original.content==content.encode()
    second=upload(admin,kid,text='# 新章节\n\n混合检索采用 RRF 最新版本。')
    assert second['version']=='v2.0'
    first_path=Path(store.one('SELECT path FROM documents WHERE id=?',(first['id'],))['path'])
    second_path=Path(store.one('SELECT path FROM documents WHERE id=?',(second['id'],))['path'])
    assert first_path.name==second_path.name=='资料.md'
    assert first_path.parent!=second_path.parent
    for mode in ('vector','keyword','hybrid'):
        result=client.post('/api/search',headers=admin,json={'query':'混合检索','mode':mode,'knowledge_base_ids':[kid]}).json()
        assert result['results'] and all(c['document_id']==second['id'] for c in result['results'])
        assert all(c['chunk_id'] and c['version']=='v2.0' for c in result['results'])
    assert client.delete(f"/api/documents/{second['id']}",headers=admin).status_code==200
    # Archived originals remain traceable with the same KB ACL, but are excluded from retrieval.
    assert client.get(f"/api/documents/{second['id']}/original",headers=admin).status_code==200
    assert client.get(f"/api/documents/{first['id']}/original",headers=admin).content==content.encode()

async def fake_model(messages,max_tokens=4096):
    data=json.loads(messages[-1]['content'])
    if 'verification_schema' in data:
        yield json.dumps({'verdicts':[{'claim_id':c['claim_id'],'verdict':'supported','quotes':[{'reference':r,'quote':next(e['text'] for e in data['evidence_json'] if e['reference']==r)} for r in c['references']]} for c in data['claims']]})
        return
    assert data['evidence_json']
    for token in ('检索证据表明，','采用混合检索。','[1]'):
        yield token

def test_rag_stream_history_citations_export_feedback_and_refusal(admin,monkeypatch):
    monkeypatch.setattr(rag,'llm_stream',fake_model)
    retrieval_calls=[]
    retrieve=rag.retrieve

    async def track_retrieval(query,ids,mode,top_k,rerank):
        retrieval_calls.append((query,ids,mode,rerank))
        return await retrieve(query,ids,mode,top_k,rerank)

    monkeypatch.setattr(rag,'retrieve',track_retrieval)
    kid=kb(admin)
    doc=upload(admin,kid)
    body={'query':'混合检索采用什么方式？','knowledge_base_ids':[kid],'mode':'keyword','rerank':False}
    stream=events(client.post('/api/qa/stream',headers=admin,json=body))
    assert stream[-1]['type']=='done'
    meta=stream[0];cid=meta['conversation_id'];mid=meta['message_id']
    assert meta['citations'][0]['document_id']==doc['id']
    assert all(k in meta['citations'][0] for k in ('section','page','chunk_id','version','relevance','sha256'))
    assert ''.join(e['content'] for e in stream if e['type']=='token').strip().endswith('[1]')
    row=client.get(f'/api/conversations/{cid}',headers=admin).json()
    assert len(row['messages'])==2 and row['messages'][1]['status']=='complete'
    assert row['retrieval_settings']=={'mode':'keyword','rerank':False}
    assert client.patch(f'/api/conversations/{cid}',headers=admin,json={'title':'新名字'}).status_code==200
    assert client.post(f'/api/messages/{mid}/feedback',headers=admin,json={'feedback':'helpful'}).status_code==200
    follow_up={'query':'它有什么作用？','conversation_id':cid}
    more=events(client.post('/api/qa/stream',headers=admin,json=follow_up))
    assert more[-1]['type']=='done'
    assert retrieval_calls[-1]==(rag.preprocess(body['query'])+' '+rag.preprocess(follow_up['query']),[kid],'keyword',False)
    assert len(client.get(f'/api/conversations/{cid}',headers=admin).json()['messages'])==4
    exported=client.get(f'/api/conversations/{cid}/export',headers=admin)
    assert quote('新名字.docx') in exported.headers['content-disposition']
    with zipfile.ZipFile(io.BytesIO(exported.content)) as package:
        text=package.read('word/document.xml').decode()
        assert '提问 02' in text and '回答 02' in text
        assert '此引用暂无可用的原文页面。' in text
        assert not any(name.startswith('word/media/') for name in package.namelist())
    empty=kb(admin,'空知识库')
    assert client.post('/api/qa/stream',headers=admin,json={**follow_up,'knowledge_base_ids':[empty]}).status_code==409
    refusal=events(client.post('/api/qa/stream',headers=admin,json={'query':'火星天气','knowledge_base_ids':[empty],'mode':'keyword','rerank':False}))
    assert '证据不足' in ''.join(e.get('content','') for e in refusal)
    empty_cid=refusal[0]['conversation_id']
    retry=events(client.post('/api/qa/stream',headers=admin,json={'query':'继续介绍一下','conversation_id':empty_cid,'mode':'hybrid','rerank':True}))
    assert retry[-1]['type']=='done'
    assert retrieval_calls[-1][1:]==([empty],'keyword',False)
    assert client.post('/api/qa/stream',headers=admin,json={'query':'混合检索'}).status_code==422
    another,other=create_member(admin)
    assert client.get(f'/api/conversations/{cid}',headers=other).status_code==404
    assert client.get(f'/api/conversations/{cid}/export',headers=other).status_code==404
    assert client.delete(f'/api/conversations/{cid}',headers=admin).status_code==200
    assert client.get(f'/api/conversations/{cid}',headers=admin).status_code==404

def test_model_failure_and_invalid_citation_do_not_create_fake_success(admin,monkeypatch):
    kid=kb(admin)
    upload(admin,kid)
    body={'query':'混合检索','knowledge_base_ids':[kid]}
    monkeypatch.setenv('KIMI_API_KEY','')
    assert client.post('/api/qa/stream',headers=admin,json=body).status_code==503
    monkeypatch.setenv('KIMI_API_KEY','test-key')
    async def bad_model(*args,**kwargs):
        yield '没有根据的回答 [99]'
    monkeypatch.setattr(rag,'llm_stream',bad_model)
    stream=events(client.post('/api/qa/stream',headers=admin,json=body))
    assert any(e['type']=='verification' and e['checks'][0]['status']=='rejected' for e in stream)
    assert '没有根据的回答' not in response_text(stream)
    assert '证据不足' in response_text(stream)
    async def failed_model(*args,**kwargs):
        yield 'partial'
        raise RuntimeError('模型服务超时，请重试')
    monkeypatch.setattr(rag,'llm_stream',failed_model)
    stream=events(client.post('/api/qa/stream',headers=admin,json=body))
    assert stream[-1]['type']=='error'
    row=client.get(f"/api/conversations/{stream[0]['conversation_id']}",headers=admin).json()
    assert row['messages'][-1]['status']=='error'

def test_report_real_content_and_export(admin,monkeypatch):
    monkeypatch.setattr(rag,'llm_stream',fake_model)
    kid=kb(admin)
    upload(admin,kid)
    template=client.post('/api/templates',headers=admin,json={'name':'测试报告','sections':['混合检索方案','混合检索实施计划']}).json()['id']
    stream=events(client.post('/api/report-tasks/stream',headers=admin,json={'title':'混合检索技术方案','template_id':template,'knowledge_base_id':kid}))
    assert stream[-1]['type']=='done'
    tid=stream[0]['task_id']
    row=client.get(f'/api/report-tasks/{tid}',headers=admin).json()
    assert row['progress']==100 and row['status']=='completed'
    assert len(row['sections'])==2 and all(c['citations'] for c in row['sections'])
    assert '混合检索' in row['content'] and '本章来源' in row['content']
    result=client.get(f'/api/report-tasks/{tid}/export?format=docx',headers=admin)
    with zipfile.ZipFile(io.BytesIO(result.content)) as z:
        assert '混合检索' in z.read('word/document.xml').decode()

def test_provider_sse_protocol_and_prompt_no_network(monkeypatch):
    seen=[]
    async def transport(request):
        data=json.loads(request.content)
        seen.append(data)
        assert request.url.path.endswith('/chat/completions')
        assert data['stream'] is True
        body='data: '+json.dumps({'choices':[{'delta':{'content':'中文'}}]},ensure_ascii=False)+'\n\n'
        body+='data: '+json.dumps({'choices':[{'delta':{'content':' [1]'},'finish_reason':'stop'}]})+'\n\ndata: [DONE]\n\n'
        return httpx.Response(200,content=body.encode())
    original=httpx.AsyncClient
    monkeypatch.setattr(rag.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(transport),**kwargs))
    async def run():
        return ''.join([token async for token in rag.llm_stream([{'role':'user','content':'test'}])])
    assert asyncio.run(run())=='中文 [1]'
    assert seen[0]['model']

def test_health(admin):
    assert client.get('/api/health').json()['status']=='ok'
    assert client.get('/api/dashboard',headers=admin).json()['retrieval_metrics'] is None

def test_pdf_pages_docx_sections_and_excel_rows(admin):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
    from openpyxl import Workbook
    kid=kb(admin)
    writer=PdfWriter()
    for text in ('Alpha vector retrieval evidence','Beta BM25 keyword retrieval evidence'):
        page=writer.add_blank_page(width=595,height=842)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
        stream=DecodedStreamObject();stream.set_data(f'BT /F1 12 Tf 40 700 Td ({text}) Tj ET'.encode())
        page[NameObject('/Contents')]=stream
    buffer=io.BytesIO();writer.write(buffer)
    response=client.post('/api/documents',headers=admin,data={'knowledge_base_id':kid},files={'file':('pages.pdf',buffer.getvalue(),'application/pdf')})
    assert response.status_code==201,response.text
    finish_upload(admin,response.json())
    detail=client.get(f"/api/documents/{response.json()['id']}",headers=admin).json()
    assert detail['page_count']==2 and {c['page'] for c in detail['chunks']}=={1,2}
    assert 'Beta' in detail['chunks'][1]['text']
    # Flow DOCX paragraphs keep the heading and never invent physical pages.
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as z:
        z.writestr('word/document.xml','''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>技术章节</w:t></w:r></w:p><w:p><w:r><w:t>章节正文包含混合检索证据。</w:t></w:r></w:p></w:body></w:document>''')
    doc=client.post('/api/documents',headers=admin,data={'knowledge_base_id':kid},files={'file':('chapter.docx',buffer.getvalue())}).json()
    finish_upload(admin,doc)
    detail=client.get(f"/api/documents/{doc['id']}",headers=admin).json()
    assert detail['chunks'][1]['section']=='技术章节' and detail['chunks'][1]['page'] is None
    workbook=Workbook();workbook.active.title='召回指标';workbook.active.append(['检索模式','召回率']);workbook.active.append(['混合检索',0.95])
    buffer=io.BytesIO();workbook.save(buffer)
    doc=client.post('/api/documents',headers=admin,data={'knowledge_base_id':kid},files={'file':('evaluation.xlsx',buffer.getvalue())}).json()
    finish_upload(admin,doc)
    detail=client.get(f"/api/documents/{doc['id']}",headers=admin).json()
    assert detail['modality']=='table' and detail['chunks'][1]['section']=='召回指标 / 行 2'
    assert '0.95' in detail['chunks'][1]['text']

def test_semantic_embedding_cache_and_explicit_failure(admin,monkeypatch):
    kid=kb(admin);upload(admin,kid)
    monkeypatch.setenv('EMBEDDING_BASE_URL','https://embedding.example/v1')
    monkeypatch.setenv('EMBEDDING_MODEL','semantic-test')
    calls=[]
    fail=[False]
    async def transport(request):
        assert request.url.path=='/v1/embeddings'
        if fail[0]:
            return httpx.Response(503)
        data=json.loads(request.content);calls.append(len(data['input']))
        return httpx.Response(200,json={'data':[{'index':i,'embedding':[1.0,.5,.1]} for i in range(len(data['input']))]})
    original=httpx.AsyncClient
    monkeypatch.setattr(rag.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(transport),**kwargs))
    body={'query':'混合检索','mode':'vector','knowledge_base_ids':[kid]}
    first=client.post('/api/search',headers=admin,json=body)
    assert first.status_code==200,first.text
    assert first.json()['pipeline']['embedding']=='semantic-api'
    count=len(calls)
    assert client.post('/api/search',headers=admin,json=body).status_code==200
    assert len(calls)==count+1  # only query embedded on repeat; document vectors come from cache
    paraphrase=client.post('/api/search',headers=admin,json={**body,'query':'semantic paraphrase'})
    assert paraphrase.status_code==200 and paraphrase.json()['results']  # dense recall must not require literal overlap
    fail[0]=True
    assert client.post('/api/search',headers=admin,json=body).status_code==502

def test_revoked_scope_and_report_partial_evidence(admin,monkeypatch):
    monkeypatch.setattr(rag,'llm_stream',fake_model)
    kid=kb(admin);upload(admin,kid)
    user,headers=create_member(admin)
    client.put(f"/api/users/{user['id']}/grants",headers=admin,json={'grants':[{'knowledge_base_id':kid,'access':'read'}]})
    stream=events(client.post('/api/qa/stream',headers=headers,json={'query':'混合检索','knowledge_base_ids':[kid]}))
    cid=stream[0]['conversation_id']
    client.put(f"/api/users/{user['id']}/grants",headers=admin,json={'grants':[]})
    assert client.get(f'/api/conversations/{cid}',headers=headers).status_code==403
    assert client.get(f'/api/conversations/{cid}/export',headers=headers).status_code==403
    assert client.get('/api/conversations',headers=headers).json()==[]
    async def partial_evidence(*args,**kwargs):
        yield '采用混合检索 [1]。当前知识库证据不足，缺少评测记录。'
    monkeypatch.setattr(rag,'llm_stream',partial_evidence)
    template=client.post('/api/templates',headers=admin,json={'name':'待评审验证','sections':['混合检索']} ).json()['id']
    report=events(client.post('/api/report-tasks/stream',headers=admin,json={'title':'混合检索报告','template_id':template,'knowledge_base_id':kid}))
    assert report[-1]['status']=='needs_review'
    record=client.get(f"/api/report-tasks/{report[0]['task_id']}",headers=admin).json()
    assert record['sections'][0]['status']=='needs_review'

def wait_job(headers,jid,statuses=None):
    statuses=statuses or {'completed','needs_review','failed','interrupted','cancelled'}
    for _ in range(160):
        response=client.get(f'/api/tasks/{jid}',headers=headers)
        assert response.status_code==200,response.text
        row=response.json()
        if row['status'] in statuses:
            return row
        time.sleep(.05)
    raise AssertionError(f'Job {jid} did not reach {statuses}: {row}')

@pytest.fixture
def configured(admin):
    old=store.one('SELECT * FROM system_settings WHERE id=1')
    yield
    with store.connect() as c:
        c.execute('DELETE FROM system_settings')
        if old:
            c.execute('INSERT INTO system_settings VALUES(?,?,?,?,?)',(old['id'],old['value'],old['revision'],old['updated_at'],old['updated_by']))

def save_config(admin,modify):
    result=client.get('/api/settings',headers=admin).json()
    modify(result['settings'])
    response=client.put('/api/settings',headers=admin,json={'settings':result['settings'],'expected_revision':result['revision']})
    assert response.status_code==200,response.text
    return response.json()

def test_settings_validation_real_effect_and_reindex_version(admin,configured):
    saved=save_config(admin,lambda c:(c['chunk'].update(size=120,overlap=20),c['retrieval'].update(top_k=1,min_coverage=.01)))
    assert saved['revision']>=1
    stale=client.put('/api/settings',headers=admin,json={'settings':saved['settings'],'expected_revision':saved['revision']-1})
    assert stale.status_code==409
    invalid=json.loads(json.dumps(saved['settings']));invalid['chunk']['overlap']=invalid['chunk']['size']
    assert client.put('/api/settings',headers=admin,json={'settings':invalid,'expected_revision':saved['revision']}).status_code==422
    invalid=json.loads(json.dumps(saved['settings']));invalid['chat']['base_url']='https://user:secret@example.com/v1'
    assert client.put('/api/settings',headers=admin,json={'settings':invalid,'expected_revision':saved['revision']}).status_code==422
    kid=kb(admin)
    doc=upload(admin,kid,text='# 混合检索\n\n'+('混合检索采用 BM25 和 RRF 融合。'*70))
    before=client.get(f"/api/documents/{doc['id']}",headers=admin).json()
    assert len(before['chunks'])>5 and max(len(c['text']) for c in before['chunks'])<=120
    result=client.post('/api/search',headers=admin,json={'query':'混合检索','knowledge_base_ids':[kid]}).json()
    assert result['total']==1
    save_config(admin,lambda c:c['chunk'].update(size=300,overlap=30))
    jid=client.post('/api/tasks/reindex',headers=admin,json={'document_id':doc['id']}).json()['job_id']
    job=wait_job(admin,jid)
    assert job['status']=='completed',job
    after=client.get(f"/api/documents/{job['result']['document_id']}",headers=admin).json()
    assert after['version']=='v2.0' and max(len(c['text']) for c in after['chunks'])==300
    old=client.get(f"/api/documents/{doc['id']}",headers=admin).json()
    assert [c['id'] for c in old['chunks']]==[c['id'] for c in before['chunks']]
    assert old['sha256']==after['sha256']
    user,headers=create_member(admin)
    assert client.get('/api/settings',headers=headers).status_code==403
    assert client.get('/api/operations',headers=headers).status_code==403
    assert client.post('/api/tasks/reindex',headers=headers,json={'document_id':doc['id']}).status_code==403

def test_audit_composite_filters_pagination_time_and_sensitive_redaction(admin):
    marker='audit-'+uuid.uuid4().hex
    actor=client.get('/api/auth/me',headers=admin).json()
    for outcome in ('success','failure','denied'):
        store.audit(actor,marker,'文档','合成审计事件',outcome,'test-42',200 if outcome=='success' else 403)
    filters={'user_id':actor['id'],'action':marker,'resource':'文档','object_id':'test-42','result':'failure',
             'start':'2000-01-01T00:00:00Z','end':'2099-01-01T00:00:00Z'}
    response=client.get('/api/audits',headers=admin,params=filters)
    assert response.status_code==200,response.text
    assert response.json()['total']==1 and response.json()['items'][0]['result']=='failure'
    paged=client.get('/api/audits',headers=admin,params={'action':marker,'page_size':1,'page':2}).json()
    assert paged['total']==3 and len(paged['items'])==1
    assert client.get('/api/audits',headers=admin,params={'start':'not-a-date'}).status_code==422
    assert client.get('/api/audits',headers=admin,params={'start':'2099-01-01T00:00:00Z','end':'2000-01-01T00:00:00Z'}).status_code==422
    password='NeverLogThisPassword-'+uuid.uuid4().hex
    username='failed-'+uuid.uuid4().hex
    client.post('/api/auth/login',json={'username':username,'password':password})
    log=client.get('/api/audits',headers=admin,params={'actor':username,'action':'登录','result':'failure'}).json()
    assert log['total']==1 and password not in json.dumps(log)
    assert client.get('/api/audits/options',headers=admin).status_code==200

def example_plan():
    return blueprints.ReportBlueprint(name='模型设计技术报告',purpose='向老师解释技术方案',format=blueprints.ReportFormat(orientation='landscape',font='仿宋',font_size_pt=14,line_spacing=2,cover=True,margin_left_cm=3.1),
        sections=[blueprints.ReportSection(title='混合检索方案',instructions='说明双路召回与融合',target_words=150)])

def test_natural_template_schema_constraints_and_actual_word_format(admin,monkeypatch):
    async def design_model(messages,max_tokens=None):
        data=json.loads(messages[-1]['content'])
        assert 'schema' in data and data['request']
        yield example_plan().model_dump_json()
    monkeypatch.setattr(rag,'llm_stream',design_model)
    response=client.post('/api/templates/design',headers=admin,json={'prompt':'设计一份给老师看的混合检索技术报告模板','constraints':{'paper':'Letter','orientation':'portrait'}})
    assert response.status_code==202,response.text
    draft=wait_job(admin,response.json()['job_id'])
    assert draft['status']=='completed',draft
    plan=draft['result']['blueprint']
    assert plan['format']['paper']=='Letter' and plan['format']['orientation']=='portrait'
    assert plan['format']['font']=='仿宋'
    saved=client.post('/api/templates',headers=admin,json={'name':plan['name'],'description':'模型设计','sections':[s['title'] for s in plan['sections']],'blueprint':plan})
    assert saved.status_code==201,saved.text
    monkeypatch.setattr(rag,'llm_stream',fake_model)
    kid=kb(admin);upload(admin,kid)
    report=client.post('/api/report-tasks',headers=admin,json={'title':'混合检索技术报告','template_id':saved.json()['id'],'knowledge_base_id':kid,'requirements':'突出技术路线'})
    assert report.status_code==201,report.text
    done=wait_job(admin,report.json()['job_id'])
    assert done['status']=='completed',done
    row=client.get(f"/api/report-tasks/{done['result']['report_id']}",headers=admin).json()
    assert row['blueprint']['format']['font_size_pt']==14
    word=client.get(f"/api/report-tasks/{row['id']}/export?format=docx",headers=admin)
    with zipfile.ZipFile(io.BytesIO(word.content)) as z:
        xml=z.read('word/document.xml').decode();styles=z.read('word/styles.xml').decode()
        assert 'w:w="12240"' in xml and 'w:h="15840"' in xml and 'w:type="page"' in xml
        assert 'w:left="1757"' in xml
        assert '仿宋' in styles and 'w:val="28"' in styles and 'w:line="480"' in styles
        assert 'word/footer1.xml' in z.namelist()

def test_queue_wait_cancel_retry_and_bad_model_json(admin,monkeypatch,configured):
    save_config(admin,lambda c:c['queue'].update(concurrency=1,max_attempts=3))
    release=threading.Event()
    async def blocking_model(*args,**kwargs):
        while not release.is_set():
            await asyncio.sleep(.02)
        yield example_plan().model_dump_json()
    monkeypatch.setattr(rag,'llm_stream',blocking_model)
    body={'prompt':'设计一份项目技术报告模板'}
    first=client.post('/api/templates/design',headers=admin,json=body).json()['job_id']
    wait_job(admin,first,{'running'})
    second=client.post('/api/templates/design',headers=admin,json=body).json()['job_id']
    assert client.get(f'/api/tasks/{second}',headers=admin).json()['status']=='queued'
    runtime=client.get('/api/operations',headers=admin).json()
    assert runtime['queue']['worker_alive'] and runtime['queue']['counts']['queued']>=1
    assert client.post(f'/api/tasks/{second}/cancel',headers=admin).status_code==200
    assert wait_job(admin,second)['status']=='cancelled'
    release.set();assert wait_job(admin,first)['status']=='completed'
    async def invalid_json(*args,**kwargs):
        yield '{"sections": "wrong-type"}'
    monkeypatch.setattr(rag,'llm_stream',invalid_json)
    failed=client.post('/api/templates/design',headers=admin,json=body).json()['job_id']
    failure=wait_job(admin,failed)
    assert failure['status']=='failed' and '校验' in failure['error']
    failure_log=client.get('/api/audits',headers=admin,params={'resource':'任务','object_id':failed,'action':'任务失败','result':'failure'}).json()
    assert failure_log['items'][0]['username']=='admin'
    assert client.get('/api/tasks',headers=admin,params={'status':'failed'}).json()['total']>=1
    async def fixed_model(*args,**kwargs):
        yield example_plan().model_dump_json()
    monkeypatch.setattr(rag,'llm_stream',fixed_model)
    assert client.post(f'/api/tasks/{failed}/retry',headers=admin).status_code==202
    result=wait_job(admin,failed)
    assert result['status']=='completed' and result['attempts']==2
    assert client.post(f'/api/tasks/{failed}/retry',headers=admin).status_code==409
    assert client.get(f'/api/tasks/{failed}/events',headers=admin).json()
    _,other=create_member(admin)
    assert client.get(f'/api/tasks/{failed}',headers=other).status_code==403
    assert client.post(f'/api/tasks/{failed}/cancel',headers=other).status_code==403

def test_model_probe_telemetry_and_storage_are_measured(admin,monkeypatch):
    async def transport(request):
        assert request.url.path.endswith('/models')
        return httpx.Response(200,json={'data':[{'id':settings.get().chat.model}]})
    original=httpx.AsyncClient
    monkeypatch.setattr(rag.httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(transport),**kwargs))
    assert client.post('/api/operations/probe',headers=admin,json={'target':'chat'}).status_code==200
    runtime=client.get('/api/operations',headers=admin).json()
    assert runtime['storage']['disk_free_bytes']>0 and runtime['storage']['database_bytes']>0
    assert runtime['storage']['chunks']>0
    assert runtime['model_calls']['total']>0
    call=runtime['recent_calls'][0]
    assert call['kind']=='probe_chat' and call['status']=='success' and call['user_id']==1
    assert call['latency_ms'] is not None and call['status_code']==200
    assert 'KIMI_API_KEY' not in json.dumps(runtime)


def test_kb_admin_edit_disable_archive_and_historical_scope(admin,monkeypatch):
    monkeypatch.setattr(rag,'llm_stream',fake_model)
    kid=kb(admin);doc=upload(admin,kid)
    user,headers=create_member(admin)
    client.put(f"/api/users/{user['id']}/grants",headers=admin,json={'grants':[{'knowledge_base_id':kid,'access':'write'}]})
    stream=events(client.post('/api/qa/stream',headers=headers,json={'query':'混合检索','knowledge_base_ids':[kid]}))
    cid=stream[0]['conversation_id']
    body={'name':'编辑后的知识库','description':'新说明','visibility':'team','status':'active'}
    assert client.patch(f'/api/knowledge-bases/{kid}',headers=headers,json=body).status_code==403
    assert client.get('/api/knowledge-bases?include_inactive=true',headers=headers).status_code==403
    assert client.patch(f'/api/knowledge-bases/{kid}',headers=admin,json=body).json()['description']=='新说明'
    for state in ('disabled','archived'):
        assert client.patch(f'/api/knowledge-bases/{kid}',headers=admin,json={**body,'status':state}).status_code==200
        assert kid not in [k['id'] for k in client.get('/api/knowledge-bases',headers=headers).json()]
        assert kid in [k['id'] for k in client.get('/api/knowledge-bases?include_inactive=true',headers=admin).json()]
        assert client.post('/api/conversations',headers=headers,json={'knowledge_base_ids':[kid]}).status_code==409
        assert client.post('/api/qa/stream',headers=headers,json={'query':'混合检索','knowledge_base_ids':[kid]}).status_code==409
        assert client.post('/api/search',headers=headers,json={'query':'混合检索','knowledge_base_ids':[kid]}).status_code==409
        assert client.post('/api/documents',headers=headers,data={'knowledge_base_id':kid},files={'file':('x.txt',b'no')}).status_code==409
        assert client.get(f"/api/documents/{doc['id']}",headers=headers).status_code==200
        assert client.get(f'/api/conversations/{cid}/export',headers=headers).status_code==200
        assert cid in [item['id'] for item in client.get('/api/conversations',headers=headers).json()]
    assert client.patch(f'/api/knowledge-bases/{kid}',headers=admin,json=body).status_code==200
    assert kid in [k['id'] for k in client.get('/api/knowledge-bases',headers=headers).json()]
    log=client.get('/api/audits',headers=admin,params={'action':'归档知识库','object_id':kid}).json()
    assert log['total']==1


def test_invalid_docx_explains_reupload_in_processing_panel(admin):
    kid=kb(admin)
    response=client.post('/api/documents',headers=admin,data={'knowledge_base_id':kid},
        files={'file':('损坏文档.docx',b'not-a-zip-file')})
    assert response.status_code==201,response.text
    doc=finish_upload(admin,response.json())
    assert doc['status']=='parse_failed'
    detail=client.get(f"/api/documents/{doc['id']}/processing",headers=admin).json()
    assert '扩展名正确后重新上传' in detail['error']

def test_document_stage_events_failure_retry_and_atomic_index(admin,monkeypatch):
    kid=kb(admin)
    original=rag.parse_local
    fail=[True]
    def parser(*args):
        if fail[0]:
            raise ValueError('合成解析失败')
        return original(*args)
    monkeypatch.setattr(rag,'parse_local',parser)
    response=client.post('/api/documents',headers=admin,data={'knowledge_base_id':kid},files={'file':('流水线.md','# 混合检索\n\n混合检索采用BM25和RRF排名融合。'.encode())})
    doc=finish_upload(admin,response.json());jid=doc['job_id']
    assert wait_job(admin,jid)['status']=='failed'
    state=client.get(f"/api/documents/{doc['id']}/processing",headers=admin).json()
    assert state['stages'][0]['status']=='completed' and state['stages'][1]['status']=='failed'
    assert state['stages'][2]['status']=='pending' and not doc['chunks']
    assert any(e['message']=='合成解析失败' for e in state['events'])
    fail[0]=False
    assert client.post(f'/api/tasks/{jid}/retry',headers=admin).status_code==202
    assert wait_job(admin,jid)['status']=='completed'
    state=client.get(f"/api/documents/{doc['id']}/processing",headers=admin).json()
    assert state['progress']==100 and all(stage['status']=='completed' for stage in state['stages'])
    assert any(e['attempt']==1 and e['status']=='failed' for e in state['events'])
    assert any(e['attempt']==2 and e['stage']=='index' for e in state['events'])
    assert store.one('SELECT COUNT(*) n FROM documents WHERE knowledge_base_id=?',(kid,))['n']==1
    _,other=create_member(admin)
    assert client.get(f"/api/documents/{doc['id']}/processing",headers=other).status_code==403


def make_pptx():
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as z:
        z.writestr('ppt/presentation.xml','<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="256" r:id="rId2"/><p:sldId id="257" r:id="rId1"/></p:sldIdLst></p:presentation>')
        z.writestr('ppt/_rels/presentation.xml.rels','<Relationships><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide2.xml"/></Relationships>')
        for i,text in ((1,'第二页：关键词检索采用 BM25。'),(2,'第一页：混合检索采用 RRF。')):
            z.writestr(f'ppt/slides/slide{i}.xml',f'<p:sld xmlns:p="urn:p" xmlns:a="urn:a"><p:cSld><a:p><a:r><a:t>{text}</a:t></a:r></a:p><a:tbl><a:tr><a:tc><a:p><a:r><a:t>表格单元格 {i}</a:t></a:r></a:p></a:tc></a:tr></a:tbl></p:cSld></p:sld>')
    return buffer.getvalue()

def make_ppt():
    import struct
    def rec(kind,body,container=False,instance=0):
        return struct.pack('<HHI',(instance<<4)|(15 if container else 0),kind,len(body))+body
    def text(value):
        return rec(0x0fa0,value.encode('utf-16-le'))
    slide1=rec(0x03ee,text('第二页：关键词检索采用 BM25。'),True)
    slide2=rec(0x03ee,text('第一页：混合检索采用 RRF。'),True)
    listing=b''.join(rec(0x03f3,struct.pack('<5I',pid,0,0,256+pid,0)) for pid in (3,2))
    doc=rec(0x03e8,rec(0x0ff0,listing,True),True)
    offsets=(0,len(doc),len(doc)+len(slide1))
    body=doc+slide1+slide2+rec(0x03ee,text('已删除幻灯片，不得进入证据'),True)
    directory_offset=len(body)
    body+=rec(0x1772,struct.pack('<4I',(3<<20)|1,*offsets))
    edit_offset=len(body)
    body+=rec(0x0ff5,struct.pack('<7I',256,0x03000000,0,directory_offset,1,4,0))
    current=rec(0x0ff6,struct.pack('<IIIHHBBH',20,0xe391c05f,edit_offset,0,0x03f4,3,0,0))
    def entry(name,kind,start,size,right=0xffffffff,child=0xffffffff):
        value=bytearray(128);encoded=(name+'\0').encode('utf-16-le');value[:len(encoded)]=encoded
        struct.pack_into('<HBBIII',value,64,len(encoded),kind,1,0xffffffff,right,child)
        struct.pack_into('<I',value,116,start);struct.pack_into('<Q',value,120,size)
        return bytes(value)
    directory=entry('Root Entry',5,0xfffffffe,0,child=1)+entry('PowerPoint Document',2,2,4096,right=2)+entry('Current User',2,10,4096)+bytes(128)
    fat=[0xffffffff]*128;fat[0]=0xfffffffe;fat[1]=0xfffffffd
    for first in (2,10):
        for sid in range(first,first+8):
            fat[sid]=sid+1 if sid<first+7 else 0xfffffffe
    header=bytearray(512);header[:8]=bytes.fromhex('D0CF11E0A1B11AE1')
    struct.pack_into('<HHHHH',header,24,0x003e,3,0xfffe,9,6)
    struct.pack_into('<9I',header,40,0,1,0,0,4096,0xfffffffe,0,0xfffffffe,0)
    struct.pack_into('<109I',header,76,1,*([0xffffffff]*108))
    return bytes(header)+directory+struct.pack('<128I',*fat)+body.ljust(4096,b'\0')+current.ljust(4096,b'\0')

@pytest.mark.parametrize('suffix,maker',[('pptx',make_pptx),('ppt',make_ppt)])
def test_powerpoint_text_slide_order_original_and_pipeline(admin,suffix,maker):
    kid=kb(admin);raw=maker()
    response=client.post('/api/documents',headers=admin,data={'knowledge_base_id':kid},files={'file':('演示.'+suffix,raw)})
    assert response.status_code==201,response.text
    doc=finish_upload(admin,response.json())
    assert doc['status']=='ready',doc
    assert doc['page_count']==2
    assert '第一页' in doc['structured'][0]['text'] and '第二页' in doc['structured'][1]['text']
    assert '已删除幻灯片' not in doc['structured'][0]['text']
    assert client.get(f"/api/documents/{doc['id']}/original",headers=admin).content==raw
    state=client.get(f"/api/documents/{doc['id']}/processing",headers=admin).json()
    assert [stage['stage'] for stage in state['stages']]==['upload','parse','chunk','index']
    assert all(stage['status']=='completed' for stage in state['stages'])
    response=client.post('/api/documents',headers=admin,data={'knowledge_base_id':kid},files={'file':('损坏.'+suffix,b'not a presentation')})
    bad=finish_upload(admin,response.json())
    assert bad['status']=='parse_failed' and bad['parse_error']


def test_powerpoint_preview_requires_document_access_and_preserves_original(admin, monkeypatch):
    from app import presentation_preview

    kid = kb(admin)
    original = make_pptx()
    uploaded = client.post('/api/documents', headers=admin, data={'knowledge_base_id': kid},
                           files={'file': ('演示.pptx', original)})
    assert uploaded.status_code == 201, uploaded.text
    document_id = finish_upload(admin, uploaded.json())['id']
    url = f'/api/documents/{document_id}'
    _, other = create_member(admin)
    assert client.get(url + '/presentation-preview', headers=other).status_code == 403

    def convert(command, directory):
        assert (directory / 'slides.pptx').read_bytes() == original
        (directory / 'slides.pdf').write_bytes(b'%PDF-1.7\npreview')
        return 0

    monkeypatch.setattr(presentation_preview, '_soffice', lambda: 'test-converter')
    monkeypatch.setattr(presentation_preview, '_convert', convert)
    preview = client.get(url + '/presentation-preview', headers=admin)
    assert preview.status_code == 200 and preview.headers['content-type'] == 'application/pdf'
    assert preview.content == b'%PDF-1.7\npreview'
    assert client.get(url + '/original', headers=admin).content == original


def test_grounding_uncited_false_and_forged_quotes_fail_closed(admin,monkeypatch):
    kid=kb(admin);upload(admin,kid,text='# 检索\n\n混合检索采用 BM25 和 RRF，样例评测召回率为 80%。')
    async def model(messages,max_tokens=4096):
        data=json.loads(messages[-1]['content'])
        if 'verification_schema' not in data:
            yield '混合检索采用 BM25 和 RRF。[1]\n没有引用的陈述。\n混合检索可以治愈疾病。[1]\n混合检索召回率为 99%。[1]\n当前知识库证据不足但随意断言。[99]'
        else:
            yield json.dumps({'verdicts':[{'claim_id':c['claim_id'],'verdict':'insufficient' if '疾病' in c['text'] else 'supported',
                'quotes':[{'reference':1,'quote':data['evidence_json'][0]['text']}]} for c in data['claims']]})
    monkeypatch.setattr(rag,'llm_stream',model)
    stream=events(client.post('/api/qa/stream',headers=admin,json={'query':'混合检索','knowledge_base_ids':[kid]}))
    text=response_text(stream)
    assert 'BM25 和 RRF' in text and '疾病' not in text and '99%' not in text and '随意断言' not in text and '没有引用的陈述' not in text
    row=client.get(f"/api/conversations/{stream[0]['conversation_id']}",headers=admin).json()['messages'][-1]
    assert sum(c['status']=='supported' for c in row['verification'])==1
    assert sum(c['status']=='rejected' for c in row['verification'])==4
    async def forged(messages,max_tokens=4096):
        data=json.loads(messages[-1]['content'])
        if 'verification_schema' not in data:
            yield '混合检索采用 BM25 和 RRF。[1]'
        else:
            yield json.dumps({'verdicts':[{'claim_id':1,'verdict':'supported','quotes':[{'reference':1,'quote':'伪造的原文短引文'}]}]})
    monkeypatch.setattr(rag,'llm_stream',forged)
    stream=events(client.post('/api/qa/stream',headers=admin,json={'query':'混合检索','knowledge_base_ids':[kid]}))
    assert response_text(stream)==grounding.REFUSAL

def test_grounding_judge_failure_does_not_publish_raw_answer(admin,monkeypatch):
    kid=kb(admin);upload(admin,kid)
    async def model(messages,max_tokens=4096):
        if 'verification_schema' in json.loads(messages[-1]['content']):
            raise RuntimeError('合成校验服务故障')
        yield '未经校验的候选答案不得输出。[1]'
    monkeypatch.setattr(rag,'llm_stream',model)
    stream=events(client.post('/api/qa/stream',headers=admin,json={'query':'混合检索','knowledge_base_ids':[kid]}))
    assert response_text(stream)==grounding.REFUSAL
    assert any('校验服务失败' in c['reason'] for event in stream if event['type']=='verification' for c in event['checks'])

def test_verified_stream_cancel_retains_only_checked_units(monkeypatch):
    async def model(messages,max_tokens=4096):
        data=json.loads(messages[-1]['content'])
        if 'verification_schema' in data:
            yield json.dumps({'verdicts':[{'claim_id':c['claim_id'],'verdict':'supported','quotes':[{'reference':1,'quote':'混合检索采用 BM25 和 RRF。'}]} for c in data['claims']]})
        else:
            yield '混合检索采用 BM25 和 RRF。[1]\n'
            yield '未核验的下一段'
            raise AssertionError('Cancelled stream must not advance this far')
    monkeypatch.setattr(rag,'llm_stream',model)
    async def run():
        stream=grounding.verified_stream('检索',[{'text':'混合检索采用 BM25 和 RRF。','name':'a','section':'b','page':1,'version':'v1'}])
        items=[]
        async for item in stream:
            items.append(item)
            if item['type']=='token':
                await stream.aclose();break
        return items
    items=asyncio.run(run())
    assert items[0]['type']=='verification' and items[0]['checks'][0]['status']=='supported'
    assert items[1]['content']=='混合检索采用 BM25 和 RRF。[1]\n'

def test_export_embeds_highlighted_original_page_without_links(admin,monkeypatch):
    from PIL import Image
    from reportlab.pdfgen.canvas import Canvas

    monkeypatch.setattr(rag,'llm_stream',fake_model)
    first=kb(admin,'导出范围甲');second=kb(admin,'导出范围乙')

    def upload_pdf(color):
        file=io.BytesIO()
        pdf=Canvas(file,pagesize=(300,400))
        pdf.drawString(30,350,'Cover page')
        pdf.showPage()
        pdf.drawString(30,350,'Keyword retrieval uses BM25 ranking.')
        pdf.setFillColorRGB(*color)
        pdf.rect(30,100,240,160,fill=1,stroke=0)
        pdf.save()
        response=client.post('/api/documents',headers=admin,data={'knowledge_base_id':first},
                             files={'file':('资料.pdf',file.getvalue(),'application/pdf')})
        assert response.status_code==201
        return finish_upload(admin,response.json())

    doc=upload_pdf((0,0,1))
    stream=events(client.post('/api/qa/stream',headers=admin,json={
        'query':'BM25 ranking','knowledge_base_ids':[first,second],'mode':'keyword','rerank':False}))
    cid=stream[0]['conversation_id'];row=client.get(f'/api/conversations/{cid}',headers=admin).json()
    citation=row['messages'][1]['citations'][0];chunk=citation['chunk_id']
    assert citation['document_id']==doc['id'] and citation['page']==2
    upload_pdf((1,0,0))
    endpoint=f'/api/conversations/{cid}/export'
    for kind in ('md','md-zip'):
        assert client.get(endpoint,headers=admin,params={'format':kind}).status_code==422
    response=client.get(endpoint,headers=admin,params={'format':'docx'})
    assert response.status_code==200
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        content=z.read('word/document.xml').decode()
        document=ElementTree.fromstring(content)
        assert document.find('.//{*}hyperlink') is None
        assert document.find('.//{*}hlinkClick') is None
        assert document.find('.//{*}drawing') is not None
        rels=ElementTree.fromstring(z.read('word/_rels/document.xml.rels'))
        assert all(rel.get('TargetMode')!='External' for rel in rels)
        image=Image.open(io.BytesIO(z.read('word/media/citation-1.png'))).convert('RGB')
        assert image.getpixel((image.width//2,image.height//2))==(0,0,255)
        text_area=image.crop((0,0,image.width,image.height//4))
        assert any(r>240 and 200<g<250 and 120<b<210 for _,(r,g,b) in text_area.getcolors(maxcolors=10000))
    assert all(label in content for label in ('提问 01','回答 01','引用索引','资料.pdf','原版 PDF · 第 2 页'))
    assert all(label not in content for label in ('导出范围甲','导出范围乙','消息时间','相关度','Keyword retrieval uses BM25'))
    detail=client.get(f'/api/documents/{doc["id"]}',headers=admin,
                      params={'compact':True,'chunk_id':chunk,'include_context':True}).json()
    assert detail['citation_context']['before'].strip()=='Cover page'
    assert any(c['id']==chunk and c['text']==citation['text'] for c in detail['chunks'])

@pytest.mark.parametrize('text,expected',[
    ('第一句。[1] 第二句。[2]',2),('第一句[1]。第二句[2]。',2),
    ('值为 3.14。[1] 下一句未引用。',2),('证据不足 [99]',1)])
def test_sentence_boundaries_do_not_share_citations(text,expected):
    units=grounding.sentences(text)
    assert len(units)==expected
    if '未引用' in text:
        assert grounding.claim_references(units[1],2) is None
    if '[99]' in text:
        assert grounding.claim_references(units[0],2) is None

def test_ppt_minifat_current_user_stream(admin):
    import struct
    raw=make_ppt();header=bytearray(raw[:512]);directory=bytearray(raw[512:1024])
    # Relocate the small Current User record into the root mini stream; keep document normal.
    current=raw[(10+1)*512:(10+1)*512+32]
    struct.pack_into('<I',directory,116,11);struct.pack_into('<Q',directory,120,64)
    struct.pack_into('<I',directory,2*128+116,0);struct.pack_into('<Q',directory,2*128+120,len(current))
    struct.pack_into('<II',header,60,10,1)
    fat=list(struct.unpack('<128I',raw[1024:1536]));fat[10]=0xfffffffe;fat[11]=0xfffffffe
    mini=struct.pack('<128I',0xfffffffe,*([0xffffffff]*127))
    # Directory0/FAT1/PowerPoint2..9/miniFAT10/root-mini-stream11.
    result=bytes(header)+bytes(directory)+struct.pack('<128I',*fat)+raw[1536:(10+1)*512]+mini+current.ljust(512,b'\0')
    kid=kb(admin)
    doc=client.post('/api/documents',headers=admin,data={'knowledge_base_id':kid},files={'file':('mini-stream.ppt',result)}).json()
    doc=finish_upload(admin,doc)
    assert doc['status']=='ready',doc['parse_error']
    assert doc['page_count']==2 and '第一页' in doc['structured'][0]['text']

def test_ingestion_reports_measured_intermediate_progress(admin):
    kid=kb(admin);doc=upload(admin,kid,text='\n\n'.join(f'混合检索段落 {i}：采用 BM25 和 RRF。' for i in range(10)))
    state=client.get(f"/api/documents/{doc['id']}/processing",headers=admin).json()
    for stage in ('parse','chunk'):
        assert any(e['stage']==stage and e['status']=='running' and 0<e['progress']<100 for e in state['events'])


def generated_report(headers,monkeypatch,kid=None):
    monkeypatch.setattr(rag,'llm_stream',fake_model)
    kid=kid or kb(headers)
    if not client.get('/api/documents',headers=headers,params={'knowledge_base_id':kid}).json():
        upload(headers,kid)
    # A member can use a template created by an administrator without gaining template permission.
    template=client.post('/api/templates',headers=login(),json={'name':'草稿与单节验收','sections':['混合检索原理','混合检索实施']}).json()['id']
    created=client.post('/api/report-tasks',headers=headers,json={'title':'混合检索草稿验收报告','template_id':template,'knowledge_base_id':kid}).json()
    assert wait_job(headers,created['job_id'])['status']=='completed'
    return client.get(f"/api/report-tasks/{created['id']}",headers=headers).json()

def section_path(report,section):
    return f"/api/report-tasks/{report['id']}/sections/{section['id']}"

def test_report_draft_revision_confirmation_history_and_export(admin,monkeypatch):
    report=generated_report(admin,monkeypatch)
    first,second=report['sections'];path=section_path(report,first)
    original_second=dict(second)
    edited='人工修改：采用混合检索，待人工核对。[1]'
    result=client.patch(path,headers=admin,json={'expected_revision':0,'content':edited})
    assert result.status_code==200,result.text
    row=client.get(f"/api/report-tasks/{report['id']}",headers=admin).json()
    assert row['status']=='draft' and row['sections'][0]['revision']==1 and row['sections'][0]['edited']==1
    assert row['sections'][1]==original_second
    assert client.patch(path,headers=admin,json={'expected_revision':0,'content':'不应覆盖'}).status_code==409
    assert client.patch(path,headers=admin,json={'expected_revision':1,'content':'越界 [99]'}).status_code==422
    assert client.patch(path,headers=admin,json={'expected_revision':1,'confirmed':True}).status_code==200
    assert client.patch(path,headers=admin,json={'expected_revision':2,'content':'不能覆盖已确认内容'}).status_code==409
    assert client.post(path+'/regenerate',headers=admin,json={'expected_revision':2}).status_code==409
    history=client.get(path+'/history',headers=admin).json()
    assert history[-1]['content']==first['content'] and history[0]['content']==edited
    assert client.patch(path,headers=admin,json={'expected_revision':2,'confirmed':False}).status_code==200
    word=client.get(f"/api/report-tasks/{report['id']}/export?format=docx",headers=admin)
    assert word.status_code==200,word.text
    with zipfile.ZipFile(io.BytesIO(word.content)) as z:
        xml=z.read('word/document.xml').decode()
        assert edited in xml and '人工编辑草稿' in xml and '未执行章节事实支撑核验' in xml
    audit=client.get('/api/audits',headers=admin,params={'action':'保存报告草稿','resource':'报告章节','object_id':first['id']}).json()
    assert audit['total']>=1 and all(edited not in item['detail'] for item in audit['items'])

def test_single_section_regeneration_preserves_confirmed_chapter(admin,monkeypatch):
    report=generated_report(admin,monkeypatch)
    first,second=report['sections']
    assert client.patch(section_path(report,first),headers=admin,json={'expected_revision':0,'confirmed':True}).status_code==200
    protected=client.get(f"/api/report-tasks/{report['id']}",headers=admin).json()['sections'][0]
    async def replacement(*args,**kwargs):
        data=json.loads(args[0][-1]['content'])
        if 'verification_schema' in data:
            async for token in fake_model(*args,**kwargs):yield token
        else:yield '本节重新生成的混合检索实施方案。[1]'
    monkeypatch.setattr(rag,'llm_stream',replacement)
    response=client.post(section_path(report,second)+'/regenerate',headers=admin,json={'expected_revision':0,'instructions':'只修改实施一节'})
    assert response.status_code==202,response.text
    job=wait_job(admin,response.json()['job_id'])
    assert job['status']=='completed',job
    updated=client.get(f"/api/report-tasks/{report['id']}",headers=admin).json()
    assert updated['sections'][0]==protected
    assert updated['sections'][1]['content'].strip()=='本节重新生成的混合检索实施方案。[1]'
    assert updated['sections'][1]['revision']==1 and updated['sections'][1]['edited']==0
    assert store.one('SELECT COUNT(*) n FROM report_tasks WHERE id=?',(report['id'],))['n']==1
    assert client.get(section_path(report,second)+'/history',headers=admin).json()[0]['content']==second['content']

def test_section_busy_cancel_and_other_section_edit_do_not_overwrite(admin,monkeypatch):
    report=generated_report(admin,monkeypatch);first,second=report['sections'];release=threading.Event()
    async def blocked(*args,**kwargs):
        yield '未完成的候选'
        while not release.is_set():
            await asyncio.sleep(.01)
        yield '不应提交 [1]'
    monkeypatch.setattr(rag,'llm_stream',blocked)
    path=section_path(report,second)
    response=client.post(path+'/regenerate',headers=admin,json={'expected_revision':0})
    jid=response.json()['job_id'];wait_job(admin,jid,{'running'})
    assert client.patch(path,headers=admin,json={'expected_revision':0,'content':'冲突'}).status_code==409
    assert client.post(path+'/regenerate',headers=admin,json={'expected_revision':0}).status_code==409
    assert client.patch(section_path(report,first),headers=admin,json={'expected_revision':0,'content':'另一节人工内容。[1]'}).status_code==200
    assert client.post(f'/api/tasks/{jid}/cancel',headers=admin).status_code==200
    assert wait_job(admin,jid)['status']=='cancelled';release.set()
    current=client.get(f"/api/report-tasks/{report['id']}",headers=admin).json()
    assert current['sections'][0]['content']=='另一节人工内容。[1]'
    assert current['sections'][1]['content']==second['content'] and current['sections'][1]['revision']==0
    assert current['sections'][1]['generation_status']=='cancelled'

def test_section_failed_retry_and_stale_old_retry(admin,monkeypatch):
    report=generated_report(admin,monkeypatch);section=report['sections'][0];path=section_path(report,section)
    async def failed(*args,**kwargs):
        yield '不应保存的半成品'
        raise RuntimeError('合成单节失败')
    monkeypatch.setattr(rag,'llm_stream',failed)
    jid=client.post(path+'/regenerate',headers=admin,json={'expected_revision':0}).json()['job_id']
    assert wait_job(admin,jid)['status']=='failed'
    current=client.get(f"/api/report-tasks/{report['id']}",headers=admin).json()['sections'][0]
    assert current['content']==section['content'] and current['generation_error']=='合成单节失败'
    monkeypatch.setattr(rag,'llm_stream',fake_model)
    assert client.post(f'/api/tasks/{jid}/retry',headers=admin).status_code==202
    assert wait_job(admin,jid)['status']=='completed'
    monkeypatch.setattr(rag,'llm_stream',failed)
    jid=client.post(path+'/regenerate',headers=admin,json={'expected_revision':1}).json()['job_id']
    assert wait_job(admin,jid)['status']=='failed'
    assert client.patch(path,headers=admin,json={'expected_revision':1,'content':'失败后保存的新草稿。[1]'}).status_code==200
    assert client.post(f'/api/tasks/{jid}/retry',headers=admin).status_code==409

def test_section_owner_acl_and_member_can_read_finished_job(admin,monkeypatch):
    report=generated_report(admin,monkeypatch);section=report['sections'][0];path=section_path(report,section)
    user,headers=create_member(admin)
    client.put(f"/api/users/{user['id']}/grants",headers=admin,json={'grants':[{'knowledge_base_id':report['knowledge_base_id'],'access':'read'}]})
    assert client.patch(path,headers=headers,json={'expected_revision':0,'content':'越权'}).status_code==404
    assert client.get(path+'/history',headers=headers).status_code==404
    assert client.post(path+'/regenerate',headers=headers,json={'expected_revision':0}).status_code==404
    own=generated_report(headers,monkeypatch,report['knowledge_base_id']);target=own['sections'][0]
    jid=client.post(section_path(own,target)+'/regenerate',headers=headers,json={'expected_revision':0}).json()['job_id']
    assert wait_job(headers,jid)['status']=='completed'
    assert client.get(f'/api/tasks/{jid}',headers=headers).status_code==200
    assert client.post(section_path(report,section)+'/regenerate',headers=admin,json={'expected_revision':0,'unexpected':True}).status_code==422

def test_audit_export_filters_all_pages_csv_safety_and_permissions(admin,monkeypatch):
    import csv
    from app import audit_export
    actor=client.get('/api/auth/me',headers=admin).json();marker='export-'+uuid.uuid4().hex
    for index in range(3):
        store.audit({**actor,'name':'=SUM(1,1)'},marker,'文档','@formula\n含逗号,和引号"','failure' if index<2 else 'success',str(index),500)
    query={'action':marker,'resource':'文档','result':'failure','start':'2000-01-01T00:00:00Z','end':'2099-01-01T00:00:00Z'}
    page=client.get('/api/audits',headers=admin,params={**query,'page_size':1}).json()
    assert page['total']==2 and len(page['items'])==1
    exported=client.get('/api/audits/export',headers=admin,params={**query,'format':'json'})
    assert exported.status_code==200 and exported.json()['count']==2
    assert all(item['action']==marker and item['result']=='failure' for item in exported.json()['items'])
    assert exported.json()['filters']['resource']=='文档' and exported.headers['x-export-count']=='2'
    result=client.get('/api/audits/export',headers=admin,params={**query,'format':'csv'})
    assert result.status_code==200 and result.content.startswith(b'\xef\xbb\xbf')
    parsed=list(csv.reader(io.StringIO(result.content.decode('utf-8-sig'))))
    assert len(parsed)==3 and parsed[1][4]=="'=SUM(1,1)" and parsed[1][-1].startswith("'@formula")
    assert 'no-store' in result.headers['cache-control']
    _,other=create_member(admin)
    assert client.get('/api/audits/export',headers=other).status_code==403
    assert client.get('/api/audits/export').status_code==401
    assert client.get('/api/audits/export',headers=admin,params={'start':'2026-10-31'}).status_code==422
    assert client.get('/api/audits/export',headers=admin,params={'start':'2099-01-01T00:00:00Z','end':'2000-01-01T00:00:00Z'}).status_code==422
    log=client.get('/api/audits',headers=admin,params={'action':'导出审计日志'}).json()
    assert log['total']>=2
    monkeypatch.setattr(audit_export,'EXPORT_LIMIT',1)
    assert client.get('/api/audits/export',headers=admin,params=query).status_code==422
