"""模板生命周期状态机测试。

覆盖 PATCH /api/report-templates/{tid}/lifecycle 的状态转移校验、
审计详情、幂等、权限，以及与报告预检的联动。
隔离数据：每个用例自建模板与用户，不依赖正式库。
"""
import json
import pytest
from test_api import (
    admin as admin_headers,
    application_lifespan,
    client as api_client,
    create_member,
    kb,
    store as app_store,
)

@pytest.fixture
def client():
    return api_client


@pytest.fixture
def store():
    return app_store


@pytest.fixture
def member_headers(admin_headers):
    _, headers = create_member(admin_headers)
    return headers

def _make_template(store, *, name='测试模板', version='v1.0', lifecycle='draft',
                   family_id=None, parent_id=None, kb_ids=None):
    """直接 INSERT 一行 report_templates，返回 tid。
    注意：必须走 store.execute 而不是绕过约束；lifecycle 由调用方指定，
    以覆盖"历史数据里可能存在的任意状态"。
    """
    kb_ids = kb_ids if kb_ids is not None else []
    tid = store.execute(
        '''INSERT INTO report_templates
           (name, description, sections, version, created_at, blueprint,
            import_id, parent_id, structure, field_mappings,
            knowledge_base_ids, lifecycle, family_id)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (name, '', json.dumps(['第一章']), version, store.now(), '{}',
         None, parent_id, '{}', '[]',
         json.dumps(kb_ids), lifecycle, family_id),
    )
    return tid


def _set_lifecycle(store, tid, state):
    store.execute('UPDATE report_templates SET lifecycle=? WHERE id=?', (state, tid))


def _get_lifecycle(store, tid):
    row = store.one('SELECT lifecycle FROM report_templates WHERE id=?', (tid,))
    return row['lifecycle'] if row else None


def _patch_lifecycle(client, tid, state, headers):
    return client.patch(f'/api/report-templates/{tid}/lifecycle',
                        params={'state': state}, headers=headers)


def _audit_rows(store, tid):
    """取针对该模板对象的全部审计行，按时间/id 排序。"""
    return store.rows(
        "SELECT * FROM audit_logs WHERE object_id=? AND action LIKE '%模板状态%' ORDER BY id",
        (tid,),
    )



def test_draft_to_published_ok(client, admin_headers, store):
    """1. draft -> published 成功，响应 ok 且回读状态一致。"""
    tid = _make_template(store, lifecycle='draft')
    r = _patch_lifecycle(client, tid, 'published', admin_headers)
    assert r.status_code == 200, r.text
    assert r.json().get('ok') is True
    assert r.json().get('lifecycle') == 'published'
    assert _get_lifecycle(store, tid) == 'published'


def test_published_cannot_revert_to_draft(client, admin_headers, store):
    """禁止发布后回草稿，以及通过 PATCH 绕过删除和恢复入口。"""
    illegal = [
        ('published', 'draft'),
        ('disabled', 'draft'),
        ('published', 'deleted'),   # 删除必须走 DELETE，不能走 PATCH
        ('disabled', 'deleted'),
        ('deleted', 'draft'),
        ('deleted', 'published'),
        ('deleted', 'disabled'),
    ]
    for current, target in illegal:
        tid = _make_template(store, lifecycle=current, version=f'v-{current}-{target}')
        r = _patch_lifecycle(client, tid, target, admin_headers)
        expected_status = 422 if target == 'deleted' else 409
        assert r.status_code == expected_status, (
            f'{current} -> {target} 应被拒绝，实际 {r.status_code}: {r.text}')
        # 状态不能被错误地改掉
        assert _get_lifecycle(store, tid) == current


def test_published_to_disabled_to_published_ok(client, admin_headers, store):
    """3. published -> disabled -> published 重新发布成功。"""
    tid = _make_template(store, lifecycle='published')
    r1 = _patch_lifecycle(client, tid, 'disabled', admin_headers)
    assert r1.status_code == 200
    assert _get_lifecycle(store, tid) == 'disabled'

    r2 = _patch_lifecycle(client, tid, 'published', admin_headers)
    assert r2.status_code == 200
    assert _get_lifecycle(store, tid) == 'published'


def test_disabled_to_draft_rejected(client, admin_headers, store):
    """4. disabled -> draft 拒绝（停用只能重新发布或删除，不能降级草稿）。"""
    tid = _make_template(store, lifecycle='disabled')
    r = _patch_lifecycle(client, tid, 'draft', admin_headers)
    assert r.status_code == 409
    assert _get_lifecycle(store, tid) == 'disabled'


def test_deleted_template_patch_rejected(client, admin_headers, store):
    """5. deleted 模板 PATCH lifecycle 返回 409，并提示用恢复接口。
    同时验证 restore 后回到 disabled（而不是 published）。
    """
    tid = _make_template(store, lifecycle='deleted')
    r = _patch_lifecycle(client, tid, 'published', admin_headers)
    assert r.status_code == 409
    body = r.text
    assert ('回收站' in body) or ('恢复' in body) or ('restore' in body.lower())

    rr = client.post(f'/api/report-templates/{tid}/restore', headers=admin_headers)
    assert rr.status_code == 200, rr.text
    assert _get_lifecycle(store, tid) == 'disabled', '恢复后必须是 disabled，需重新发布'


def test_same_state_is_idempotent(client, admin_headers, store):
    """6. 同状态 PATCH 幂等返回 200，不写多余审计行。"""
    tid = _make_template(store, lifecycle='published')
    before = len(_audit_rows(store, tid))
    r = _patch_lifecycle(client, tid, 'published', admin_headers)
    assert r.status_code == 200
    assert r.json().get('ok') is True
    after = len(_audit_rows(store, tid))
    assert after == before, '同状态幂等不应新增审计记录'


def test_audit_records_old_and_new_state(client, admin_headers, store):
    """7. 每次成功转移的审计 detail 含 "旧状态 → 新状态"。"""
    tid = _make_template(store, lifecycle='draft')
    r = _patch_lifecycle(client, tid, 'published', admin_headers)
    assert r.status_code == 200
    rows = _audit_rows(store, tid)
    assert rows, '状态变更必须写审计'
    # 取最后一条针对本模板的状态变更审计
    detail = rows[-1].get('detail') or ''
    assert 'draft' in detail and 'published' in detail, (
        f'审计应记录旧→新状态，实际 detail={detail!r}')
    assert ('→' in detail) or ('->' in detail) or ('to' in detail), (
        '审计应含转移方向标记')


def test_permission_denied_without_templates_manage(client, member_headers, store):
    """8. 无 templates.manage 的普通用户 PATCH 返回 403。"""
    tid = _make_template(store, lifecycle='draft')
    r = _patch_lifecycle(client, tid, 'published', member_headers)
    assert r.status_code == 403, r.text
    assert _get_lifecycle(store, tid) == 'draft', '403 后状态不能变'


def test_report_preflight_rejects_non_published_template(client, admin_headers, store):
    """9. 与报告模块的接口契约：
       - disabled / draft 模板调 /api/reports/preflight 返回 409
       - deleted 模板返回 404
       - published 模板可完成预检
    """
    kb_id = kb(admin_headers, '生命周期测试KB')
    tid_pub = _make_template(store, lifecycle='published', kb_ids=[kb_id])

    def body(template_id):
        return {
            'title': '生命周期契约测试',
            'template_id': template_id,
            'knowledge_base_id': kb_id,
            'requirements': '',
            'period_start': '',
            'period_end': '',
            'field_values': {},
            'allow_missing': True,
        }

    r_pub = client.post('/api/reports/preflight', json=body(tid_pub),
                        headers=admin_headers)
    assert r_pub.status_code == 200, (
        f'published 模板不应因 lifecycle 被拒，实际 {r_pub.status_code}: {r_pub.text}')

    tid_dis = _make_template(store, lifecycle='disabled', kb_ids=[kb_id])
    r_dis = client.post('/api/reports/preflight', json=body(tid_dis),
                        headers=admin_headers)
    assert r_dis.status_code == 409, r_dis.text
    assert ('发布' in r_dis.text) or ('停用' in r_dis.text)


    tid_draft = _make_template(store, lifecycle='draft', kb_ids=[kb_id])
    r_draft = client.post('/api/reports/preflight', json=body(tid_draft),
                          headers=admin_headers)
    assert r_draft.status_code == 409, r_draft.text

    tid_del = _make_template(store, lifecycle='deleted', kb_ids=[kb_id])
    r_del = client.post('/api/reports/preflight', json=body(tid_del),
                        headers=admin_headers)
    assert r_del.status_code == 404, r_del.text
