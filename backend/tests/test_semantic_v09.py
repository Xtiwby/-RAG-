"""Model adapters mocked here; real local GPU checks live in check_semantic_v09.py."""
import pytest
from fastapi import HTTPException
from test_api import client,admin,application_lifespan,kb,upload,configured,save_config,create_member,wait_job
from app import rag,local_models,settings,store,report_retrieval


def enable(admin,monkeypatch):
    monkeypatch.setattr(local_models,'namespace',lambda cfg:'test-v09|'+cfg.model+'|'+str(cfg.max_length))
    return save_config(admin,lambda cfg:(cfg['embedding'].update(provider='local',model='BAAI/bge-m3'),
        cfg['reranking'].update(provider='local'),cfg['retrieval'].update(cross_language=False)))


@pytest.mark.parametrize('mode',['vector','hybrid'])
def test_dense_cross_language_without_llm_rewrite(admin,configured,monkeypatch,mode):
    kid=kb(admin)
    wanted=upload(admin,kid,name='manual.txt',text='The appliance removes excess moisture from the ambient air using a condensation coil.')
    upload(admin,kid,name='unrelated.txt',text='The shipping carton contains a printed label and recycled packaging material.')
    enable(admin,monkeypatch)
    async def embed(cfg,texts):
        return [[1.,0.] if 'moisture' in t or '空气除湿' in t else [0.,1.] for t in texts]
    async def rerank(cfg,query,texts):
        return [.98 if 'moisture' in t else .01 for t in texts],{'device':'mock','revision':'a'*40}
    monkeypatch.setattr(local_models,'embed',embed);monkeypatch.setattr(local_models,'rerank',rerank)
    async def forbidden(*args,**kwargs):raise AssertionError('Should not call LLM');yield ''
    monkeypatch.setattr(rag,'llm_stream',forbidden)
    response=client.post('/api/search',headers=admin,json={'query':'空气除湿的原理是什么','mode':mode,'knowledge_base_ids':[kid]})
    assert response.status_code==200,response.text
    data=response.json()
    assert data['results'][0]['document_id']==wanted['id']
    assert data['pipeline']['embedding']=='local-bge-m3'
    assert data['pipeline']['rerank']=='local-cross-encoder'
    assert data['pipeline']['query_expansion']['status']=='disabled'
    assert data['results'][0]['rerank_score']==.98


def test_neural_scores_actually_reverse_lexical_order_and_disable_works(admin,configured,monkeypatch):
    kid=kb(admin)
    weak=upload(admin,kid,name='weak.txt',text='电池寿命 电池寿命 电池寿命。这份宣传材料未提供测试方法。')
    strong=upload(admin,kid,name='strong.txt',text='设备电池在标准工况下可连续运行八小时，测试环境温度为二十五摄氏度。')
    save_config(admin,lambda cfg:cfg['reranking'].update(provider='local'))
    calls=[]
    async def rerank(cfg,query,texts):
        calls.append(texts)
        return [.99 if '八小时' in t else .01 for t in texts],{'device':'mock'}
    monkeypatch.setattr(local_models,'rerank',rerank)
    body={'query':'电池寿命','mode':'keyword','knowledge_base_ids':[kid]}
    ranked=client.post('/api/search',headers=admin,json=body).json()
    assert ranked['results'][0]['document_id']==strong['id']
    assert ranked['pipeline']['embedding']=='not-used'
    assert ranked['pipeline']['reranking']['candidate_count']==2
    plain=client.post('/api/search',headers=admin,json={**body,'rerank':False}).json()
    assert plain['results'][0]['document_id']==weak['id'] and len(calls)==1


def test_cache_hash_dedup_namespace_isolation_and_originals_preserved(admin,configured,monkeypatch):
    enable(admin,monkeypatch)
    calls=[]
    async def embed(cfg,texts):calls.append(texts);return [[1.,0.] for _ in texts]
    monkeypatch.setattr(local_models,'embed',embed)
    import asyncio
    async def run():
        assert len(await rag.cache_local_embeddings(['alpha','alpha','beta']))==3
        assert len(await rag.cache_local_embeddings(['beta','alpha']))==2
    asyncio.run(run())
    assert calls==[['alpha','beta']]
    save_config(admin,lambda cfg:cfg['embedding'].update(max_length=512))
    asyncio.run(rag.cache_local_embeddings(['alpha']))
    assert calls[-1]==['alpha'] and len(calls)==2
    assert store.one('SELECT COUNT(*) n FROM embedding_cache WHERE namespace LIKE ?',('test-v09%',))['n']>=3


def test_missing_model_and_inference_failure_are_not_lexical_fallback(admin,configured,monkeypatch):
    kid=kb(admin);upload(admin,kid)
    enable(admin,monkeypatch)
    async def broken(*args,**kwargs):raise HTTPException(503,'本地模型不可用')
    monkeypatch.setattr(local_models,'embed',broken)
    result=client.post('/api/search',headers=admin,json={'query':'混合检索','mode':'vector','knowledge_base_ids':[kid]})
    assert result.status_code==503 and '不可用' in result.text
    save_config(admin,lambda cfg:cfg['embedding'].update(provider='tfidf'))
    monkeypatch.setattr(local_models,'rerank',broken)
    result=client.post('/api/search',headers=admin,json={'query':'混合检索','mode':'keyword','knowledge_base_ids':[kid]})
    assert result.status_code==503


@pytest.mark.parametrize('score',[float('nan'),float('inf'),-1,2])
def test_invalid_neural_scores_fail_closed(admin,configured,monkeypatch,score):
    kid=kb(admin);upload(admin,kid)
    save_config(admin,lambda cfg:cfg['reranking'].update(provider='local'))
    async def bad(*args):return [score],{}
    monkeypatch.setattr(local_models,'rerank',bad)
    assert client.post('/api/search',headers=admin,json={'query':'混合检索','knowledge_base_ids':[kid]}).status_code==502


def test_permissions_rechecked_after_neural_inference(admin,configured,monkeypatch):
    kid=kb(admin);upload(admin,kid)
    user,headers=create_member(admin)
    client.put(f"/api/users/{user['id']}/grants",headers=admin,json={'grants':[{'knowledge_base_id':kid,'access':'read'}]})
    save_config(admin,lambda cfg:cfg['reranking'].update(provider='local'))
    async def revoke(*args):
        store.execute('DELETE FROM kb_grants WHERE user_id=? AND kb_id=?',(user['id'],kid))
        return [.9],{}
    monkeypatch.setattr(local_models,'rerank',revoke)
    assert client.post('/api/search',headers=headers,json={'query':'混合检索','knowledge_base_ids':[kid]}).status_code==403


def test_local_probe_and_report_section_use_models(admin,configured,monkeypatch):
    kid=kb(admin);upload(admin,kid)
    enable(admin,monkeypatch);seen=[]
    async def embed(cfg,texts):return [[1.,0.] for _ in texts]
    async def rank(cfg,query,texts):seen.append(query);return [.9 for _ in texts],{'device':'mock'}
    monkeypatch.setattr(local_models,'embed',embed);monkeypatch.setattr(local_models,'rerank',rank)
    assert client.post('/api/operations/probe',headers=admin,json={'target':'embedding'}).status_code==200
    assert client.post('/api/operations/probe',headers=admin,json={'target':'reranking'}).status_code==200
    import asyncio
    evidence,meta=asyncio.run(report_retrieval.retrieve_section(kid,{'title':'混合检索','instructions':'说明检索流程'}))
    assert evidence and meta['attempts'][0]['retrieval']['rerank']=='local-cross-encoder'
    assert '混合检索' in seen[-1]


def test_local_config_rejects_unknown_models_and_bad_limits():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):settings.EmbeddingSettings(provider='local',model='../../private')
    with pytest.raises(ValidationError):settings.RerankingSettings(candidate_k=1000)
    with pytest.raises(ValueError):local_models.validate_vectors([[0.,0.]],1)
    with pytest.raises(ValueError):local_models.validate_vectors([[1.,0.],[1.]],2)


def test_safe_missing_model_manifest(monkeypatch,tmp_path):
    monkeypatch.setattr(local_models,'MODEL_ROOT',tmp_path)
    with pytest.raises(RuntimeError):local_models.identity('BAAI/bge-m3')
    assert local_models.status()['BAAI/bge-m3']['downloaded'] is False


def test_reindex_model_failure_preserves_existing_version(admin,configured,monkeypatch):
    kid=kb(admin);doc=upload(admin,kid)
    enable(admin,monkeypatch)
    async def broken(*args):raise HTTPException(503,'本地语义模型失败')
    monkeypatch.setattr(local_models,'embed',broken)
    created=client.post('/api/tasks/reindex',headers=admin,json={'document_id':doc['id']})
    job=wait_job(admin,created.json()['job_id'])
    assert job['status']=='failed'
    assert store.one('SELECT COUNT(*) n FROM documents WHERE knowledge_base_id=?',(kid,))['n']==1
    assert store.one('SELECT status FROM documents WHERE id=?',(doc['id'],))['status']=='ready'


def test_new_upload_embeds_before_ready_and_preserves_stage_history(admin,configured,monkeypatch):
    kid=kb(admin);enable(admin,monkeypatch);calls=[]
    async def embed(cfg,texts):calls.extend(texts);return [[1.,0.] for _ in texts]
    monkeypatch.setattr(local_models,'embed',embed)
    doc=upload(admin,kid,text='文档索引时先计算预训练语义向量，完成后再发布可用片段。')
    assert doc['status']=='ready' and calls
    assert store.one('SELECT COUNT(*) n FROM embedding_cache WHERE namespace LIKE ?',('test-v09%',))['n']>0
    stages=store.rows("SELECT stage FROM document_events WHERE document_id=? AND status='completed' ORDER BY id",(doc['id'],))
    assert [r['stage'] for r in stages][-3:]==['parse','chunk','index']
