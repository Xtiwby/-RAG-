from copy import deepcopy
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / 'docs'
SYSTEM_SRC = DOCS / '基于RAG的知识库管理系统_软件系统设计文档_V1.0.docx'
SYSTEM_DST = DOCS / '基于RAG的知识库管理系统_软件系统设计文档_V1.1.docx'
DETAIL_SRC = DOCS / '基于RAG的知识库管理系统_软件详细设计文档_V1.0.docx'
DETAIL_DST = DOCS / '基于RAG的知识库管理系统_软件详细设计文档_V1.1.docx'


def find_para(doc, text):
    found = [p for p in doc.paragraphs if p.text.strip() == text]
    if not found:
        raise KeyError(text)
    return found[-1]


def find_para_prefix(doc, prefix):
    found = [p for p in doc.paragraphs if p.text.strip().startswith(prefix)]
    if not found:
        raise KeyError(prefix)
    return found[-1]


def find_table(doc, first_header):
    for table in doc.tables:
        if table.rows and table.rows[0].cells[0].text.strip() == first_header:
            return table
    raise KeyError(first_header)


def append_para_text(paragraph, suffix):
    if paragraph.runs:
        run = deepcopy(paragraph.runs[-1]._r)
        for child in list(run):
            if child.tag != qn('w:rPr'):
                run.remove(child)
        text = OxmlElement('w:t')
        text.set(qn('xml:space'), 'preserve')
        text.text = suffix
        run.append(text)
        paragraph._p.append(run)
    else:
        paragraph.add_run(suffix)


def clone_paragraph(template, text):
    element = deepcopy(template._p)
    for child in list(element):
        if child.tag != qn('w:pPr'):
            element.remove(child)
    if template.runs:
        run = deepcopy(template.runs[0]._r)
        for child in list(run):
            if child.tag != qn('w:rPr'):
                run.remove(child)
    else:
        run = OxmlElement('w:r')
    text_element = OxmlElement('w:t')
    text_element.set(qn('xml:space'), 'preserve')
    text_element.text = text
    run.append(text_element)
    element.append(run)
    return element


def insert_before(reference, template, text):
    reference._p.addprevious(clone_paragraph(template, text))


def set_cell_text(cell, value):
    paragraph = cell.paragraphs[0]
    for extra in list(cell.paragraphs)[1:]:
        extra._p.getparent().remove(extra._p)
    if paragraph.runs:
        paragraph.runs[0].text = value
        for run in paragraph.runs[1:]:
            run._r.getparent().remove(run._r)
    else:
        paragraph.add_run(value)


def replace_table_body(table, rows):
    table_element = table._tbl
    template_row = deepcopy(table.rows[1]._tr)
    for row in list(table.rows)[1:]:
        table_element.remove(row._tr)
    for _ in rows:
        table_element.append(deepcopy(template_row))
    for row, values in zip(list(table.rows)[1:], rows):
        for cell, value in zip(row.cells, values):
            set_cell_text(cell, value)


def append_table_rows(table, rows):
    template_row = deepcopy(table.rows[1]._tr)
    for values in rows:
        table._tbl.append(deepcopy(template_row))
        row = table.rows[-1]
        for cell, value in zip(row.cells, values):
            set_cell_text(cell, value)


def enable_toc_update(doc):
    settings = doc.settings.element
    if settings.find(qn('w:updateFields')) is None:
        element = OxmlElement('w:updateFields')
        element.set(qn('w:val'), 'true')
        settings.append(element)


def save(doc, path):
    enable_toc_update(doc)
    doc.save(path)
    print(f'saved {path.name}')


PAGE_ROWS = [
    ('App / SideNavigation', '全局布局、分组菜单、账户入口、权限可见性与健康检查状态。', '/api/auth/me、/api/health'),
    ('LoginView', '账号密码登录、失败提示和登录后跳转。', '/api/auth/login'),
    ('DashboardView', '工作台统计、快捷入口、最近报告与文档、系统概况。', '/api/dashboard、/api/report-tasks、/api/operations'),
    ('KnowledgeView', '知识库列表、创建、编辑、停用、归档和成员授权。', '/api/knowledge-bases、/api/knowledge-bases/{id}/members'),
    ('DocumentsView', '文档筛选、上传进度、阶段事件与处理状态轮询。', '/api/documents、/api/tasks/{id}/events'),
    ('DocumentView', '片段与版本浏览、原件预览、下载和引用定位高亮。', '/api/documents/{id}、/api/documents/{id}/original'),
    ('SearchView', '关键词、向量和混合检索测试，模式与重排选项、检索详情和原文预览。', '/api/search'),
    ('ChatView', '多知识库问答、SSE 流式输出、会话历史、引用侧栏、反馈与 Word 导出。', '/api/qa/stream、/api/conversations*'),
    ('ReportsHome（报告中心/列表）', '报告列表、状态筛选、归档与回收站入口。', '/api/report-tasks'),
    ('ReportWizard', '模板与知识库选择、业务字段、预检查、创建生成任务。', '/api/reports/preflight、/api/report-tasks'),
    ('ReportEditor', '分章正文、自动保存、核验、候选、审阅、定稿与导出。', '/api/report-tasks/{id}*、/api/tasks/{id}/events'),
    ('TemplateLibrary（模板库/新建模板）', '模板列表、DOCX 导入、AI 设计与版本状态。', '/api/report-templates、/api/templates/*'),
    ('TemplateEditor', '模板结构预览、字段映射校正与版本保存。', '/api/report-templates/{id}*'),
    ('UsersView', '用户增改、重置密码、角色权限点与知识库授权。', '/api/users、/api/roles、/api/permissions、/api/users/{id}/grants'),
    ('AuditView', '审计组合筛选、分页、详情与 CSV/JSON 导出。', '/api/audits、/api/audits/options、/api/audits/export'),
    ('SettingsView', '分组配置读写、修订冲突提示、模型探测与密钥配置状态。', '/api/settings、/api/operations/probe'),
    ('OperationsView', '任务筛选、重试、取消、模型调用、队列、心跳、存储与失败任务。', '/api/operations、/api/tasks*'),
    ('ProfileView', '显示名修改、密码变更与会话失效提示。', '/api/auth/me、/api/auth/password'),
]

API_ROWS = [
    ('POST /api/auth/login', '公开', 'username、password', 'token、user；失败写审计。'),
    ('GET/PATCH /api/auth/me；POST /api/auth/logout；POST /api/auth/password', '登录用户', '显示名或当前密码+新密码', '本人资料；退出或改密撤销会话。'),
    ('GET/POST /api/users；PATCH /api/users/{uid}；POST /api/users/{uid}/reset-password', 'users.manage', '用户、角色、状态、密码', '用户记录；改角色或停用撤销会话。'),
    ('GET/POST /api/roles；PUT /api/roles/{rid}；GET /api/permissions', 'users.manage 或 roles.manage', '角色名称、permissions', '角色与权限点；系统管理员保留角色不可改。'),
    ('GET/PUT /api/users/{uid}/grants', 'roles.manage', 'knowledge_base_id、access', '知识库读写授权。'),
    ('GET/PUT /api/settings；POST /api/operations/probe', 'settings.manage', 'RuntimeSettings、expected_revision、target', '新配置版本；修订冲突 409；模型探测记录。'),
    ('GET /api/tasks；GET /api/tasks/{jid}；GET /api/tasks/{jid}/events；POST /api/tasks/{jid}/cancel；POST /api/tasks/{jid}/retry', '本人任务或 ops.read/ops.manage', '状态、kind、分页；任务操作', '任务与事件；取消、重试写审计。'),
    ('GET /api/operations', 'ops.read', '—', '队列、worker 心跳、存储、模型调用与失败任务。'),
    ('GET /api/audits；GET /api/audits/options；GET /api/audits/export', 'audit.read', '组合筛选、分页、csv/json', '审计列表、筛选选项和受控导出。'),
]

TRACK_ROWS = [
    ('FR-UM-01~04', 'users、roles、sessions、kb_grants；main.py 认证授权', '登录退出、改密/重置/停用撤销会话、角色权限即时生效、403 越权、最后管理员保护。'),
    ('FR-SY-01/02', 'citations、audit_logs、DocumentView 有界窗口', '历史引用可定位原版本；审计记录操作者、对象、结果并支持组合筛选。'),
]

GAP_BULLETS = [
    '• 配置版本支持修订号与 409 冲突拒绝，但未实现历史配置回滚。',
    '• 审计日志未保存请求标识，错误响应中也没有可追踪的 request_id；前端仅按权限隐藏菜单，尚未配置路由级权限守卫和独立 403/404 页面。',
    '• 运行状态页对每个原件执行存在性检查，文件量大时可能增加响应耗时；应用日志尚无集中查看入口。',
]


def revise_detailed():
    doc = Document(DETAIL_SRC)
    doc.core_properties.title = '基于RAG的知识库管理系统 软件详细设计文档'
    append_table_rows(find_table(doc, '版本'), [
        ('V1.1', '2026-11-11', '按 0.11.0 实现核对，完善前端页面、权限、接口与运行维护描述', '胡艺镭'),
    ])

    replace_table_body(find_table(doc, '组件/页面'), PAGE_ROWS)
    body_template = find_para_prefix(doc, '领域模块不得导入前端实现')
    insert_before(find_para(doc, '2.2 后端组件'), body_template,
                  '上述页面共 17 类；App / SideNavigation 为跨页面布局组件。桌面侧栏 216px（收起 64px）、顶栏 56px，'
                  '响应式断点 1199/1000/767px；列表与表单统一提供加载、空、错误和重试状态。'
                  '路由共 18 条并按需加载；菜单按权限点隐藏，前端暂无路由级权限守卫，直接访问仍由后端 403 拦截。')
    insert_before(find_para(doc, '3.4 核心表字段'), body_template,
                  '当前实现中，用户只对应一个角色（users.role_id 指向 roles），角色权限点保存在 roles.permissions JSON 数组；'
                  '审计日志在基础字段外还保存 user_id、username、result、status_code 和 source，用于筛选与导出。')

    append_para_text(find_para_prefix(doc, '不能停用本人或变更本人角色；'), '改角色与停用同样撤销该用户现有会话。')

    append_table_rows(find_table(doc, '方法与路径'), API_ROWS)
    append_para_text(find_para_prefix(doc, '当前表未保存 owner、lease_expires_at'),
                     '实现同时保留 embedded（默认开发）与 external（正式部署，python -m app.worker）两种模式；正式路径只启动一个外部 worker。')

    append_table_rows(find_table(doc, '需求'), TRACK_ROWS)
    bullet_template = find_para_prefix(doc, '• FR-UM-01 的连续失败')
    for text in GAP_BULLETS:
        insert_before(find_para(doc, '附录A 状态与错误码'), bullet_template, text)

    save(doc, DETAIL_DST)


def revise_system():
    doc = Document(SYSTEM_SRC)
    doc.core_properties.title = '基于RAG的知识库管理系统 软件系统设计文档'
    append_table_rows(find_table(doc, '版本'), [
        ('V1.1', '2026-11-11', '按 0.11.0 实现核对，完善权限模型、系统管理与前端范围描述', '胡艺镭'),
    ])
    append_para_text(find_para_prefix(doc, '管理用户、角色、权限点、服务端会话和知识库授权。'),
                     '当前实现中用户使用单一角色，角色权限点为 JSON 数组；系统管理员为保留角色，权限集合固定。')
    append_para_text(find_para_prefix(doc, '统一维护引用快照、审计日志、模型与检索配置、任务队列'),
                     '前端对应页面包括用户与权限、系统配置、审计日志和运行状态；运行状态聚合任务队列、worker 心跳、'
                     '对象存储与模型调用信息，详细设计见 2.1、4、8、9 章。')
    save(doc, SYSTEM_DST)


if __name__ == '__main__':
    revise_detailed()
    revise_system()
