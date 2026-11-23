"""权限矩阵参数化；读取 tests/data/permissions.json，与 main.py 的 check_kb/grant_level 对齐。"""
import json
import uuid
import pytest
import uuid
from pathlib import Path
from test_api import client, admin, application_lifespan, kb, upload, create_member, login, fake_model
from app import rag
from app import store

DATA = Path(__file__).resolve().parents[2] / 'tests' / 'data' / 'permissions.json'


def load():
    return json.loads(DATA.read_text(encoding='utf-8'))


@pytest.mark.parametrize('case', load()['matrix'], ids=lambda c: f"{c['role']}-{c['visibility']}-{c['grant']}-{c['action']}")
def test_permission_matrix(admin, case, monkeypatch):
    spec = load()
    user, headers = create_member(admin)
    # 按角色权限点调整（member 默认 role_id=2）
    if case['role'] == 'admin':
        headers = admin
    elif case['role'] != 'member':
        role = client.post('/api/roles', headers=admin,
                           json={'name': f"role-{case['role']}-{uuid.uuid4().hex[:8]}", 'permissions': spec['roles'][case['role']]['permissions']}).json()['id']
        client.patch(f"/api/users/{user['id']}", headers=admin,
                     json={'name': user['name'], 'role_id': role, 'status': 'active'})
        headers = login(user['username'], 'test123456')
    kid = client.post('/api/knowledge-bases', headers=admin,
                      json={'name': f"矩阵-{case['visibility']}", 'visibility': case['visibility']}).json()['id']
    if case['grant'] != 'none':
        client.put(f"/api/users/{user['id']}/grants", headers=admin,
                   json={'grants': [{'knowledge_base_id': kid, 'access': case['grant']}]})
    doc = upload(admin, kid)
    action = case['action']
    if action == 'read_document':
        r = client.get(f"/api/documents/{doc['id']}", headers=headers)
    elif action == 'read_original':
        r = client.get(f"/api/documents/{doc['id']}/original", headers=headers)
    elif action == 'upload_document':
        r = client.post('/api/documents', headers=headers, data={'knowledge_base_id': kid},
                        files={'file': ('x.txt', b'no')})
    elif action == 'search':
        r = client.post('/api/search', headers=headers, json={'query': '混合检索', 'knowledge_base_ids': [kid]})
    elif action == 'qa_stream':
        monkeypatch.setattr(rag, 'llm_stream', fake_model)
        r = client.post('/api/qa/stream', headers=headers, json={'query': '混合检索', 'knowledge_base_ids': [kid]})
    elif action == 'report_create':
        tpl = client.post('/api/templates', headers=admin, json={'name': '矩阵', 'sections': ['节']}).json()['id']
        r = client.post('/api/report-tasks', headers=headers,
                        json={'title': '矩阵', 'template_id': tpl, 'knowledge_base_id': kid})
    elif action == 'put_grants':
        target, _ = create_member(admin)
        r = client.put(f"/api/users/{target['id']}/grants", headers=headers,
                       json={'grants': [{'knowledge_base_id': kid, 'access': 'read'}]})
    else:
        pytest.skip(f"未实现动作 {action}")
    assert r.status_code == case['expect'], f"{case}: {r.status_code} {r.text}"
