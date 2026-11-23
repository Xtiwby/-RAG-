"""问答证据校验补充：仅 citations 篡改、judge 异常、跨语言降级。"""
import json
import pytest
from test_api import client, admin, application_lifespan, kb, upload, events, response_text, configured, save_config, fake_model
from app import rag, grounding, retrieval_planner, store


def test_citations_only_tamper_marks_legacy_for_unedited_model_text(admin, monkeypatch):
    """模型正文的 citations 被篡改但 content 不变 → 记录降级为 legacy，而非继续显示已核验。"""
    from test_api import generated_report
    monkeypatch.setattr(rag, 'llm_stream', fake_model)
    report = generated_report(admin, monkeypatch)
    section = report['sections'][0]
    cites = store.one('SELECT citations FROM report_sections WHERE id=?', (section['id'],))['citations']
    wrong = json.loads(cites)
    wrong[0]['version'] = 'tampered-version'
    store.execute('UPDATE report_sections SET citations=? WHERE id=?', (json.dumps(wrong), section['id']))
    row = client.get(f"/api/report-tasks/{report['id']}", headers=admin).json()
    assert row['sections'][0]['verification']['state'] == 'legacy'
    # 恢复后仍为 verified（内容未变，evidence_sha256 重新匹配）
    store.execute('UPDATE report_sections SET citations=? WHERE id=?', (cites, section['id']))
    row = client.get(f"/api/report-tasks/{report['id']}", headers=admin).json()
    assert row['sections'][0]['verification']['state'] in ('verified', 'partial', 'insufficient')


def test_judge_unavailable_does_not_trigger_repair(admin, configured, monkeypatch):
    """judge 异常时不触发 coverage 补写。"""
    monkeypatch.setenv('RAG_QA_VERIFICATION_MODE','independent')
    save_config(admin, lambda cfg: cfg['answer_quality'].update(enabled=True, repair=True))
    calls = []

    async def broken(messages, max_tokens=None):
        data = json.loads(messages[-1]['content'])
        calls.append(data)
        if 'verification_schema' in data:
            yield 'invalid'
            return
        if 'coverage_schema' in data:
            raise AssertionError('No repair after failed judge')
        yield '机壳使用陶瓷散热片。[1]'

    kid = kb(admin)
    upload(admin, kid, text='机壳使用陶瓷散热片。')
    monkeypatch.setattr(rag, 'llm_stream', broken)
    stream = events(client.post('/api/qa/stream', headers=admin,
                                json={'query': '机壳是什么材料？', 'knowledge_base_ids': [kid]}))
    assert response_text(stream) == grounding.REFUSAL


def test_cross_language_planner_skips_irrelevant_query_without_relaxing_gates(admin, configured, monkeypatch):
    """无可复用跨语言锚点时不调用模型，检索仍不得返回无关证据。"""
    retrieval_planner._cache.clear()
    save_config(admin, lambda cfg: cfg['retrieval'].update(cross_language=True))

    async def blocked(messages, max_tokens=None):
        raise AssertionError('无跨语言锚点时不应调用改写模型')
        yield '{}'

    kid = kb(admin)
    upload(admin, kid, text='The Orion manual describes passive cooling using a heat sink.')
    monkeypatch.setattr(rag, 'llm_stream', blocked)
    resp = client.post('/api/search', headers=admin,
                       json={'query': '不存在的中文量子水稻', 'knowledge_base_ids': [kid]}).json()
    assert resp['pipeline']['query_expansion']['status'] == 'not_needed'
    assert resp['results'] == []
    retrieval_planner._cache.clear()
