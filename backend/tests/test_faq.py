"""FAQ knowledge base lifecycle, ACL, routing and chat integration tests."""
from test_api import application_lifespan, admin, client, create_member, events, kb, response_text, upload
from app import rag, store


def create_faq(headers, kid, chunk_id, question='检查发动机机油前需要做什么？'):
    response = client.post('/api/faqs', headers=headers, json={
        'knowledge_base_id': kid,
        'standard_question': question,
        'standard_answer': '车辆应停在平坦地面，关闭发动机并等待数分钟后读取机油尺。',
        'variants': ['什么时候可以看机油尺？', '查机油前要熄火吗？'],
        'keywords': ['机油', '机油尺', '熄火', '等待'],
        'category': '车辆保养',
        'evidence_chunk_ids': [chunk_id],
    })
    assert response.status_code == 201, response.text
    return response.json()


def test_faq_lifecycle_version_acl_and_three_retrieval_modes(admin, monkeypatch):
    kid = kb(admin, 'FAQ 车辆知识库')
    doc = upload(admin, kid, name='机油检查.md', text='# 发动机机油检查\n\n车辆应停在平坦地面，关闭发动机并等待数分钟后读取机油尺。')
    item = create_faq(admin, kid, doc['chunks'][0]['id'])
    assert item['status'] == 'draft' and item['version'] == 1

    published = client.post(f"/api/faqs/{item['id']}/lifecycle", headers=admin, json={'status': 'published'})
    assert published.status_code == 200, published.text
    assert published.json()['status'] == 'published'

    result = client.post('/api/faqs/search', headers=admin, json={
        'query': '什么时候可以看机油尺？', 'mode': 'hybrid', 'knowledge_base_ids': [kid], 'rerank': True,
    })
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload['route'] == 'high'
    assert payload['results'][0]['id'] == item['id']
    assert payload['results'][0]['citations'][0]['chunk_id'] == doc['chunks'][0]['id']
    for mode in ('keyword', 'vector', 'hybrid'):
        response = client.post('/api/faqs/search', headers=admin, json={
            'query': '什么时候可以看机油尺？', 'mode': mode, 'knowledge_base_ids': [kid], 'rerank': True,
        })
        assert response.status_code == 200 and response.json()['results'][0]['id'] == item['id']

    paraphrase = client.post('/api/faqs/search', headers=admin, json={
        'query': '检查机油前要等待多久，为什么？', 'mode': 'hybrid',
        'knowledge_base_ids': [kid], 'rerank': True,
    }).json()
    assert paraphrase['route'] == 'high'
    assert paraphrase['pipeline']['keyword_hits'] >= 2

    monkeypatch.setenv('RAG_FAQ_HIGH_THRESHOLD', '0.9999')
    monkeypatch.setenv('RAG_FAQ_MEDIUM_THRESHOLD', '0.80')
    assert client.post('/api/faqs/search', headers=admin, json={
        'query': '什么时候可以看机油尺？', 'mode': 'hybrid', 'knowledge_base_ids': [kid],
    }).json()['route'] == 'medium'
    assert client.post('/api/faqs/search', headers=admin, json={
        'query': '量子纠缠卫星通信协议', 'mode': 'hybrid', 'knowledge_base_ids': [kid],
    }).json()['route'] == 'low'

    user, member = create_member(admin)
    assert item['id'] not in {row['id'] for row in client.get('/api/faqs?include_all=true', headers=member).json()}
    client.put(f"/api/users/{user['id']}/grants", headers=admin,
               json={'grants': [{'knowledge_base_id': kid, 'access': 'read'}]})
    visible = client.get(f'/api/faqs?knowledge_base_id={kid}', headers=member)
    assert visible.status_code == 200 and visible.json()[0]['id'] == item['id']
    forbidden = client.patch(f"/api/faqs/{item['id']}", headers=member, json={
        'knowledge_base_id': kid, 'standard_question': '篡改', 'standard_answer': '不允许', 'evidence_chunk_ids': [doc['chunks'][0]['id']],
    })
    assert forbidden.status_code == 403

    version = client.post(f"/api/faqs/{item['id']}/versions", headers=admin)
    assert version.status_code == 201, version.text
    assert version.json()['version'] == 2 and version.json()['status'] == 'draft'
    assert client.patch(f"/api/faqs/{item['id']}", headers=admin, json={
        'knowledge_base_id': kid, 'standard_question': '不能直接改已发布版本', 'standard_answer': '不应成功',
        'evidence_chunk_ids': [doc['chunks'][0]['id']],
    }).status_code == 409


def test_high_confidence_faq_answers_without_calling_document_rag_or_kimi(admin, monkeypatch):
    kid = kb(admin, 'FAQ 直答知识库')
    doc = upload(admin, kid, name='轮胎压力.md', text='# 轮胎检查\n\n轮胎压力应在冷车状态下按车辆制造商标注值检查。')
    item = create_faq(admin, kid, doc['chunks'][0]['id'], '轮胎压力应该在什么状态下检查？')
    client.patch(f"/api/faqs/{item['id']}", headers=admin, json={
        'knowledge_base_id': kid,
        'standard_question': '轮胎压力应该在什么状态下检查？',
        'standard_answer': '轮胎压力应在冷车状态下按车辆制造商标注值检查。',
        'variants': ['什么时候测胎压？'], 'keywords': ['轮胎', '胎压', '冷车'],
        'category': '轮胎', 'evidence_chunk_ids': [doc['chunks'][0]['id']],
        'expected_updated_at': item['updated_at'],
    })
    client.post(f"/api/faqs/{item['id']}/lifecycle", headers=admin, json={'status': 'published'})

    async def forbidden_retrieve(*_args, **_kwargs):
        raise AssertionError('高置信 FAQ 不应调用文档 RAG')
    async def forbidden_model(*_args, **_kwargs):
        raise AssertionError('高置信 FAQ 不应调用 Kimi')
        yield
    monkeypatch.setattr(rag, 'retrieve', forbidden_retrieve)
    monkeypatch.setattr(rag, 'llm_stream', forbidden_model)

    stream = events(client.post('/api/qa/stream', headers=admin, json={
        'query': '轮胎压力应该在什么状态下检查？', 'mode': 'hybrid', 'knowledge_base_ids': [kid],
    }))
    answer = response_text(stream)
    assert '冷车状态' in answer and '[1]' in answer
    meta = next(event for event in stream if event['type'] == 'meta')
    assert meta['pipeline']['source'] == 'faq'
    feedback_id = meta['pipeline']['faq_feedback_id']
    done = next(event for event in stream if event['type'] == 'done')
    assert done['timings']['total_ms'] >= 0
    assert client.post(f'/api/faqs/feedback/{feedback_id}', headers=admin,
                       json={'helpful': True, 'reason': '标准答案正确'}).status_code == 200
    assert store.one('SELECT helpful FROM faq_feedback WHERE id=?', (feedback_id,))['helpful'] == 1


def test_faq_publish_requires_traceable_evidence(admin):
    kid = kb(admin, 'FAQ 证据约束')
    draft = client.post('/api/faqs', headers=admin, json={
        'knowledge_base_id': kid, 'standard_question': '没有证据可以发布吗？',
        'standard_answer': '不可以。', 'variants': [], 'keywords': [], 'category': '规则', 'evidence_chunk_ids': [],
    })
    assert draft.status_code == 201
    publish = client.post(f"/api/faqs/{draft.json()['id']}/lifecycle", headers=admin, json={'status': 'published'})
    assert publish.status_code == 422 and '来源证据' in publish.text


def test_faq_delete_removes_entire_version_family_and_related_data(admin):
    kid = kb(admin, 'FAQ 删除知识库')
    doc = upload(admin, kid, name='删除测试.md', text='# 删除测试\n\n问答对删除时应清理全部版本和关联数据。')
    item = create_faq(admin, kid, doc['chunks'][0]['id'], '删除问答对时会清理什么？')
    client.post(f"/api/faqs/{item['id']}/lifecycle", headers=admin, json={'status': 'published'})
    version_response = client.post(f"/api/faqs/{item['id']}/versions", headers=admin)
    assert version_response.status_code == 201, version_response.text
    version = version_response.json()

    admin_id = store.one("SELECT id FROM users WHERE username='admin'")['id']
    store.execute('''INSERT INTO faq_feedback(faq_id,faq_version,user_id,query,retrieval_mode,score,latency_ms,created_at)
        VALUES(?,?,?,?,?,?,?,?)''', (item['id'], 1, admin_id, '删除测试', 'hybrid', 1.0, 1, store.now()))

    _, member = create_member(admin)
    assert client.delete(f"/api/faqs/{item['id']}", headers=member).status_code == 403

    deleted = client.delete(f"/api/faqs/{version['id']}", headers=admin)
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()['deleted_versions'] == 2
    assert client.get(f"/api/faqs/{item['id']}", headers=admin).status_code == 404
    assert client.get(f"/api/faqs/{version['id']}", headers=admin).status_code == 404
    assert store.one('SELECT COUNT(*) n FROM faq_variants WHERE faq_id IN (?,?)',
                     (item['id'], version['id']))['n'] == 0
    assert store.one('SELECT COUNT(*) n FROM faq_feedback WHERE faq_id IN (?,?)',
                     (item['id'], version['id']))['n'] == 0
