"""报告定稿/候选/质量门补充用例；mock 模型，隔离库。"""
import json
import pytest
from test_api import client, admin, application_lifespan, kb, upload, create_member, wait_job, generated_report, section_path, fake_model, login
from app import rag, store


def _report_with_sections(admin, monkeypatch):
    return generated_report(admin, monkeypatch)


def test_finalize_blocked_by_unreviewed_sections(admin, monkeypatch):
    """未审阅节 → finalize 409，issues 含 review。"""
    report = _report_with_sections(admin, monkeypatch)
    body = {'expected_revision': 0, 'fixed_text_reviewed': True}
    resp = client.post(f"/api/reports/{report['id']}/finalize", headers=admin, json=body)
    assert resp.status_code == 409, resp.text
    detail = resp.json()['detail']
    assert detail['issues']


def test_finalize_requires_fixed_text_review(admin, monkeypatch):
    """fixed_text_reviewed=false → 422。"""
    report = _report_with_sections(admin, monkeypatch)
    # 先审阅所有节
    for sec in report['sections']:
        client.post(f"/api/reports/{report['id']}/sections/{sec['id']}/review",
                    headers=admin, json={'expected_revision': 0})
    row = client.get(f"/api/reports/{report['id']}", headers=admin).json()
    body = {'expected_revision': row['revision'], 'fixed_text_reviewed': False}
    resp = client.post(f"/api/reports/{report['id']}/finalize", headers=admin, json=body)
    assert resp.status_code == 422, resp.text


def test_final_export_requires_release(admin, monkeypatch):
    """未定稿导出 final → 409；定稿后 200。"""
    report = _report_with_sections(admin, monkeypatch)
    resp = client.get(f"/api/reports/{report['id']}/export?format=md&mode=final", headers=admin)
    assert resp.status_code == 409
    # 审阅 + 定稿
    for sec in report['sections']:
        client.post(f"/api/reports/{report['id']}/sections/{sec['id']}/review",
                    headers=admin, json={'expected_revision': 0})
    row = client.get(f"/api/reports/{report['id']}", headers=admin).json()
    final = client.post(f"/api/reports/{report['id']}/finalize", headers=admin,
                        json={'expected_revision': row['revision'], 'fixed_text_reviewed': True})
    assert final.status_code == 201, final.text
    resp = client.get(f"/api/reports/{report['id']}/export?format=md&mode=final", headers=admin)
    assert resp.status_code == 200
    assert '参考来源' in resp.text and report['title'] in resp.text


def test_candidate_accept_conflict(admin, monkeypatch):
    """候选 accept 在正文已变更后应 409。"""
    report = _report_with_sections(admin, monkeypatch)
    section = report['sections'][0]
    path = section_path(report, section)
    # 生成候选（preview）
    job = client.post(path + '/regenerate', headers=admin,
                      json={'expected_revision': 0, 'preview': True}).json()
    assert wait_job(admin, job['job_id'])['status'] == 'completed'
    row = client.get(f"/api/reports/{report['id']}", headers=admin).json()
    cand = row['candidates'][0]
    # 先改正文
    assert client.patch(path, headers=admin, json={'expected_revision': 0, 'content': '人工修改。[1]'}).status_code == 200
    resp = client.post(f"/api/reports/{report['id']}/candidates/{cand['id']}",
                       headers=admin, json={'expected_revision': 1, 'action': 'accept'})
    assert resp.status_code == 409, resp.text


def test_quality_counts_human_accepted(admin, monkeypatch):
    """quality() 的 human_accepted_sections 与 reviewed 计数正确。"""
    report = _report_with_sections(admin, monkeypatch)
    section = report['sections'][0]
    client.post(f"/api/reports/{report['id']}/sections/{section['id']}/review",
                headers=admin, json={'expected_revision': 0})
    row = client.get(f"/api/reports/{report['id']}", headers=admin).json()
    q = row['quality']
    assert q['reviewed_sections'] >= 1
    assert q['human_accepted_sections'] >= 0
    assert 'issues' in q and 'warnings' in q
