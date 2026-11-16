"""问答证据校验补充：仅 citations 篡改、judge 异常、跨语言降级。"""
import json
import pytest
from test_api import client, admin, application_lifespan, kb, upload, events, response_text, configured, save_config, fake_model
from app import rag, grounding, store


def test_citations_only_tamper_marks_unverified(admin, monkeypatch):
    """citations 被篡改但 content 不变 → 报告章节回 unverified_manual。"""
    from test_api import generated_report
    monkeypatch.setattr(rag, 'llm_stream', fake_model)
    report = generated_report(admin, monkeypatch)
    section = report['sections'][0]
    cites = store.one('SELECT citations FROM report_sections WHERE id=?', (section['id'],))['citations']
    wrong = json.loads(cites)
    wrong[0]['version'] = 'tampered-version'
    store.execute('UPDATE report_sections SET citations=? WHERE id=?', (json.dumps(wrong), section['id']))
    row = client.get(f"/api/report-tasks/{report['id']}", headers=admin).json()
    assert row['sections'][0]['verification']['state'] == 'unverified_manual'
    # 恢复后仍为 verified（内容未变，evidence_sha256 重新匹配）
    store.execute('UPDATE report_sections SET citations=? WHERE id=?', (cites, section['id']))
    row = client.get(f"/api/report-tasks/{report['id']}", headers=admin).json()
    assert row['sections'][0]['verification']['state'] in ('verified', 'partial', 'insufficient')


def test_judge_unavailable_does_not_trigger_repair(admin, configured, monkeypatch):
    """judge 异常时不触发 coverage 补写。"""
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


def test_cross_language_degraded_keeps_gates(admin, configured, monkeypatch):
    """跨语言改写降级时，检索仍走证据门禁，不静默放宽。"""
    save_config(admin, lambda cfg: cfg['retrieval'].update(cross_language=True))

    async def blocked(messages, max_tokens=None):
        import asyncio
        await asyncio.sleep(4)
        yield '{}'

    kid = kb(admin)
    upload(admin, kid, text='The Orion manual describes passive cooling using a heat sink.')
    monkeypatch.setattr(rag, 'llm_stream', blocked)
    resp = client.post('/api/search', headers=admin,
                       json={'query': '不存在的中文量子水稻', 'knowledge_base_ids': [kid]}).json()
    assert resp['pipeline']['query_expansion']['status'] == 'degraded'
    assert resp['results'] == []