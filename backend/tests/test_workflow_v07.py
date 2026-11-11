"""Authoring user journeys and security invariants. All model calls are mocked."""
import io
import json
import zipfile
from xml.etree import ElementTree as ET
from pypdf import PdfReader
from test_api import client,admin,application_lifespan,kb,upload,wait_job,create_member,fake_model,generated_report,section_path
from test_reports_v06 import template_bytes,parsed_import
from app import rag,store,report_layout,template_docx,report_workflow

def setup_report(admin,monkeypatch,imported=False):
    monkeypatch.setattr(rag,'llm_stream',fake_model)
    kid=kb(admin,'新工作流合成知识库');upload(admin,kid)
    parsed=parsed_import(admin) if imported else None
    heads=[b for b in parsed['structure']['blocks'] if b['type']=='heading'] if parsed else []
    sections=[{'title':'混合检索方案','block_id':heads[0]['id'] if heads else 'section-0','instructions':'重点检索BM25和RRF的算法说明'},
              {'title':'混合检索实施','block_id':heads[1]['id'] if heads else 'section-1'}]
    maps=[{'key':'项目负责人','target':'user_input','required':True}]
    if parsed:
        maps=[{'key':f['key'],'target':'report_title' if f['key']=='报告名' else 'generated_at' if f['key']=='日期' else 'section','section_block_id':heads[0]['id']} for f in parsed['structure']['fields']]
    body={'blueprint':{'name':'工作流验收模板','purpose':'解释混合检索','sections':sections},'knowledge_base_ids':[kid],'mappings':maps,'lifecycle':'published'}
    if parsed:body['import_id']=parsed['import_id']
    response=client.post('/api/report-templates',headers=admin,json=body);assert response.status_code==201,response.text
    template=response.json()['id']
    params={'title':'混合检索技术报告','template_id':template,'knowledge_base_id':kid,'workflow_version':1,'field_values':{'项目负责人':'验收用户'},'period_start':'2026-10-01','period_end':'2026-10-31'}
    return params,parsed

def create(admin,params):
    preflight=client.post('/api/reports/preflight',headers=admin,json=params);assert preflight.status_code==200,preflight.text
    created=client.post('/api/report-tasks',headers=admin,json={**params,'preflight_id':preflight.json()['preflight_id']});assert created.status_code==201,created.text
    wait_job(admin,created.json()['job_id'])
    return client.get(f"/api/reports/{created.json()['id']}",headers=admin).json()

def test_preflight_parameter_validation_and_invalid_combinations(admin,monkeypatch):
    params,_=setup_report(admin,monkeypatch)
    assert client.post('/api/reports/preflight',headers=admin,json={**params,'title':'   '}).status_code==422
    assert client.post('/api/report-tasks',headers=admin,json=params).status_code==409
    assert client.post('/api/reports/preflight',headers=admin,json={**params,'knowledge_base_id':kb(admin)}).status_code==403
    assert client.post('/api/reports/preflight',headers=admin,json={**params,'period_start':'2026-11-01'}).status_code==422
    assert client.post('/api/reports/preflight',headers=admin,json={**params,'field_values':{}}).status_code==422
    checked=client.post('/api/reports/preflight',headers=admin,json=params).json()
    assert checked['sections'][0]['ready'] and checked['sections'][0]['evidence_count']>0
    assert client.post('/api/report-tasks',headers=admin,json={**params,'title':'改变的参数','preflight_id':checked['preflight_id']}).status_code==409
    client.patch(f"/api/report-templates/{params['template_id']}/lifecycle?state=disabled",headers=admin)
    assert client.post('/api/reports/preflight',headers=admin,json=params).status_code==409

def test_empty_preflight_requires_explicit_missing_acceptance(admin,monkeypatch):
    params,_=setup_report(admin,monkeypatch)
    for doc in client.get('/api/documents',headers=admin,params={'knowledge_base_id':params['knowledge_base_id']}).json():client.delete(f"/api/documents/{doc['id']}",headers=admin)
    checked=client.post('/api/reports/preflight',headers=admin,json=params).json();assert checked['missing']==2
    body={**params,'preflight_id':checked['preflight_id']}
    assert client.post('/api/report-tasks',headers=admin,json=body).status_code==422
    created=client.post('/api/report-tasks',headers=admin,json={**body,'allow_missing':True}).json();assert wait_job(admin,created['job_id'])['status']=='needs_review'
    report=client.get(f"/api/reports/{created['id']}",headers=admin).json();assert report['quality']['issues']

def test_template_fills_fixed_text_fields_tables_in_all_exports(admin,monkeypatch):
    params,_=setup_report(admin,monkeypatch,True);report=create(admin,params);tid=report['id']
    preview=client.get(f'/api/reports/{tid}/preview',headers=admin);assert preview.status_code==200,preview.text
    tree=preview.json()['nodes'];text=report_layout.markdown(tree)
    assert '固定说明' in text and '方案表' in text and '标题缓存' not in text and '{{' not in text and '${' not in text
    assert params['title'] in text and '参考来源' in text and '草稿' in text and 'SHA256' not in text
    assert any(n['type']=='table' for n in tree)
    word=client.get(f'/api/reports/{tid}/export?format=docx',headers=admin)
    with zipfile.ZipFile(io.BytesIO(word.content)) as z:
        xml=z.read('word/document.xml');ET.fromstring(xml);assert b'<w:tbl>' in xml and b'gridSpan' in xml
    pdf=client.get(f'/api/reports/{tid}/export?format=pdf',headers=admin)
    assert pdf.status_code==200,pdf.text[:300]
    assert pdf.content.startswith(b'%PDF') and len(PdfReader(io.BytesIO(pdf.content)).pages)>=1
    assert client.get(f'/api/report-tasks/{tid}/export',headers=admin).status_code==409

def test_review_finalize_immutable_release_and_reopen(admin,monkeypatch):
    params,_=setup_report(admin,monkeypatch);report=create(admin,params);tid=report['id']
    assert report['document_state']=='draft' and report['status']=='completed'
    assert client.get(f'/api/reports/{tid}/export?mode=final',headers=admin).status_code==409
    assert client.post(f'/api/reports/{tid}/finalize',headers=admin,json={'expected_revision':report['revision'],'fixed_text_reviewed':True}).status_code==409
    for sec in report['sections']:
        assert client.post(f"/api/reports/{tid}/sections/{sec['id']}/review",headers=admin,json={'expected_revision':sec['revision']}).status_code==200
    report=client.get(f'/api/reports/{tid}',headers=admin).json();assert report['quality']['ready']
    released=client.post(f'/api/reports/{tid}/finalize',headers=admin,json={'expected_revision':report['revision'],'fixed_text_reviewed':True});assert released.status_code==201,released.text
    release=released.json()['id'];original=client.get(f'/api/reports/{tid}/export?mode=final&format=md&release_id={release}',headers=admin).content
    report=client.get(f'/api/reports/{tid}',headers=admin).json();sec=report['sections'][0]
    assert client.patch(section_path(report,sec),headers=admin,json={'expected_revision':sec['revision'],'content':'不能覆盖'}).status_code==409
    assert client.post(f'/api/reports/{tid}/reopen',headers=admin,json={'expected_revision':report['revision']}).status_code==200
    assert client.patch(section_path(report,sec),headers=admin,json={'expected_revision':sec['revision'],'content':'新的草稿。[1]'}).status_code==200
    assert client.get(f'/api/reports/{tid}/export?mode=final&format=md&release_id={release}',headers=admin).content==original
    updated=client.get(f'/api/reports/{tid}',headers=admin).json()
    assert updated['sections'][0]['verification']['state']=='unverified_manual' and updated['quality']['issues']
    assert '项目负责人：验收用户' in client.get(f'/api/reports/{tid}/export?format=md',headers=admin).text

def test_candidate_compare_accept_preserves_other_section_and_stale_guard(admin,monkeypatch):
    params,_=setup_report(admin,monkeypatch);report=create(admin,params);first,second=report['sections'];tid=report['id']
    assert client.patch(section_path(report,first),headers=admin,json={'expected_revision':first['revision'],'confirmed':True}).status_code==200
    protected=client.get(f'/api/reports/{tid}',headers=admin).json()['sections'][0]
    response=client.post(section_path(report,second)+'/regenerate',headers=admin,json={'expected_revision':second['revision'],'preview':True});wait_job(admin,response.json()['job_id'])
    after=client.get(f'/api/reports/{tid}',headers=admin).json();candidate=after['candidates'][0]
    assert after['sections'][0]==protected and after['sections'][1]['content']==second['content'] and after['sections'][1]['revision']==second['revision']
    accepted=client.post(f"/api/reports/{tid}/candidates/{candidate['id']}",headers=admin,json={'expected_revision':second['revision'],'action':'accept'})
    assert accepted.status_code==200,accepted.text
    after=client.get(f'/api/reports/{tid}',headers=admin).json();assert after['sections'][1]['revision']==second['revision']+1
    sec=after['sections'][1];job=client.post(section_path(after,sec)+'/regenerate',headers=admin,json={'expected_revision':sec['revision'],'preview':True}).json();wait_job(admin,job['job_id'])
    candidate=client.get(f'/api/reports/{tid}',headers=admin).json()['candidates'][0]
    client.patch(section_path(after,sec),headers=admin,json={'expected_revision':sec['revision'],'content':'候选生成后人工修改。[1]'})
    assert client.post(f"/api/reports/{tid}/candidates/{candidate['id']}",headers=admin,json={'expected_revision':sec['revision']+1,'action':'accept'}).status_code==409

def test_attach_restore_acl_lock_and_review_state(admin,monkeypatch):
    params,_=setup_report(admin,monkeypatch);report=create(admin,params);tid=report['id'];sec=report['sections'][0]
    doc=upload(admin,params['knowledge_base_id'],name='新增资料.md',text='混合检索的新增合成资料。关键词检索使用BM25算法并结合RRF排名融合。这不是实际项目进度。')
    chunk=client.get(f"/api/documents/{doc['id']}",headers=admin).json()['chunks'][0]['id']
    endpoint=f"/api/reports/{tid}/sections/{sec['id']}"
    _,other=create_member(admin)
    assert client.post(endpoint+'/citations',headers=other,json={'expected_revision':sec['revision'],'chunk_id':chunk}).status_code==404
    result=client.post(endpoint+'/citations',headers=admin,json={'expected_revision':sec['revision'],'chunk_id':chunk});assert result.status_code==200,result.text
    current=client.get(f'/api/reports/{tid}',headers=admin).json()['sections'][0]
    assert current['revision']==sec['revision']+1 and current['verification']['state']=='unverified_manual'
    history=client.get(section_path(report,sec)+'/history',headers=admin).json()
    restore=client.post(endpoint+f"/restore/{history[0]['id']}",headers=admin,json={'expected_revision':current['revision']});assert restore.status_code==200,restore.text
    current=client.get(f'/api/reports/{tid}',headers=admin).json()['sections'][0]
    client.patch(section_path(report,sec),headers=admin,json={'expected_revision':current['revision'],'confirmed':True})
    current=client.get(f'/api/reports/{tid}',headers=admin).json()['sections'][0]
    job=client.post(section_path(report,sec)+'/verify',headers=admin,json={'expected_revision':current['revision']});assert job.status_code==202,job.text
    wait_job(admin,job.json()['job_id'])
    assert client.get(f'/api/reports/{tid}',headers=admin).json()['sections'][0]['confirmed']==1

def test_legacy_empty_blueprint_can_open_new_editor(admin):
    template=client.post('/api/templates',headers=admin,json={'name':'旧版兼容','sections':['背景']}).json()['id']
    kid=kb(admin);uid=client.get('/api/auth/me',headers=admin).json()['id']
    tid=store.execute('INSERT INTO report_tasks(title,template_id,knowledge_base_id,status,created_at,user_id) VALUES(?,?,?,?,?,?)',('旧报告',template,kid,'legacy',store.now(),uid))
    response=client.get(f'/api/reports/{tid}',headers=admin)
    assert response.status_code==200,response.text
    assert response.json()['blueprint']['sections'][0]['title']=='背景' and not response.json()['quality']['ready']
    assert response.json()['template_snapshot']['id']==template
    assert store.one('SELECT template_snapshot FROM report_tasks WHERE id=?',(tid,))['template_snapshot']=='{}'


def test_inline_word_fields_preserve_fixed_prefix_and_suffix():
    raw=io.BytesIO()
    xml='''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
      <w:p><w:r><w:t>负责人：</w:t></w:r><w:fldSimple w:instr='MERGEFIELD "姓名"'><w:r><w:t>缓存姓名</w:t></w:r></w:fldSimple><w:r><w:t>；审核：</w:t></w:r>
      <w:sdt><w:sdtPr><w:tag w:val="审核人"/></w:sdtPr><w:sdtContent><w:r><w:t>审核缓存</w:t></w:r></w:sdtContent></w:sdt><w:r><w:t>（合成测试）</w:t></w:r></w:p>
      <w:p><w:r><w:t>日期：</w:t></w:r><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText>MERGEFIELD 日期</w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>旧日期</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r><w:r><w:t> 完成</w:t></w:r></w:p>
      </w:body></w:document>'''
    with zipfile.ZipFile(raw,'w') as z:z.writestr('word/document.xml',xml)
    structure=template_docx.parse_docx(raw.getvalue())
    report={'title':'行内域测试','created_at':store.now(),'blueprint':{'name':'行内域模板','sections':[{'title':'说明','kind':'fixed','fixed_content':'测试'}]},
            'template_snapshot':{'structure':structure,'field_mappings':[{'key':key,'target':'literal','value':value} for key,value in [('姓名','合成用户甲'),('审核人','合成用户乙'),('日期','2026-10-31')]]}}
    text=report_layout.markdown(report_layout.nodes(report,[]))
    assert '负责人：合成用户甲；审核：合成用户乙（合成测试）' in text
    assert '日期：2026-10-31 完成' in text and '缓存' not in text and '旧日期' not in text
    assert '【待生成章节】' in text


def test_optional_nonempty_claim_requires_review_but_human_review_is_final(admin,monkeypatch):
    params,_=setup_report(admin,monkeypatch);report=create(admin,params)
    plan=json.loads(json.dumps(report['blueprint']));plan['sections'][1]['required']=False
    report['blueprint']=plan
    sections=report['sections']
    for sec in sections:sec['reviewed_revision']=sec['revision']
    sections[1]['content']='没有证据的可选章节结论。'
    sections[1]['verification']={}
    result=report_workflow.quality(report,sections)
    assert result['ready'] and result['human_accepted_sections']==1
    assert any(i.get('section_id')==sections[1]['id'] and i['human_accepted'] for i in result['warnings'])
    sections[1]['reviewed_revision']=-1
    result=report_workflow.quality(report,sections)
    assert not result['ready'] and any(i.get('section_id')==sections[1]['id'] and i['kind']=='review' for i in result['issues'])


def test_unpublished_template_hidden_and_version_save_does_not_overwrite(admin,monkeypatch):
    params,_=setup_report(admin,monkeypatch)
    parent=client.get(f"/api/report-templates/{params['template_id']}",headers=admin).json()
    user,member=create_member(admin)
    client.put(f"/api/users/{user['id']}/grants",headers=admin,json={'grants':[{'knowledge_base_id':params['knowledge_base_id'],'access':'read'}]})
    changed={**parent['blueprint'],'name':'未发布的新版本'}
    saved=client.post('/api/report-templates',headers=admin,json={'blueprint':changed,'parent_id':parent['id'],'knowledge_base_ids':[params['knowledge_base_id']],'lifecycle':'draft'})
    assert saved.status_code==201,saved.text
    child=saved.json()['id']
    assert client.get(f'/api/report-templates/{child}',headers=member).status_code==403
    assert all(t['id']!=child for t in client.get('/api/templates',headers=member).json())
    assert client.get(f"/api/report-templates/{parent['id']}",headers=admin).json()['blueprint']==parent['blueprint']
    assert client.patch(f'/api/report-templates/{child}/lifecycle?state=published',headers=admin).status_code==200
    assert client.get(f'/api/report-templates/{child}',headers=member).status_code==200


def test_template_delete_permissions_trash_and_restore(admin,monkeypatch):
    params,_=setup_report(admin,monkeypatch);tid=params['template_id']
    user,member=create_member(admin)
    client.put(f"/api/users/{user['id']}/grants",headers=admin,json={'grants':[{'knowledge_base_id':params['knowledge_base_id'],'access':'read'}]})
    assert client.delete(f'/api/report-templates/{tid}',headers=member).status_code==403
    assert client.post(f'/api/report-templates/{tid}/restore',headers=member).status_code==403
    assert client.get('/api/templates?deleted=true',headers=member).status_code==403
    assert client.delete('/api/report-templates/9999999',headers=admin).status_code==404
    assert client.post(f'/api/report-templates/{tid}/restore',headers=admin).status_code==409
    template=client.get(f'/api/report-templates/{tid}',headers=admin).json()
    assert client.delete(f'/api/report-templates/{tid}',headers=admin).status_code==200
    assert client.delete(f'/api/report-templates/{tid}',headers=admin).status_code==200
    assert store.one('SELECT * FROM report_templates WHERE id=?',(tid,))['lifecycle']=='deleted'
    assert not any(t['id']==tid for t in client.get('/api/templates',headers=admin).json())
    assert any(t['id']==tid for t in client.get('/api/templates?deleted=true',headers=admin).json())
    assert client.get(f'/api/report-templates/{tid}',headers=admin).status_code==404
    assert client.get(f'/api/templates/{tid}/structure',headers=admin).status_code==403
    assert client.post('/api/reports/preflight',headers=admin,json=params).status_code==404
    assert client.post('/api/report-tasks',headers=admin,json={**params,'workflow_version':0}).status_code==403
    assert client.patch(f'/api/report-templates/{tid}/lifecycle?state=published',headers=admin).status_code==409
    assert client.post('/api/report-templates',headers=admin,json={'blueprint':template['blueprint'],'parent_id':tid}).status_code==409
    assert client.post(f'/api/report-templates/{tid}/restore',headers=admin).status_code==200
    assert store.one('SELECT lifecycle FROM report_templates WHERE id=?',(tid,))['lifecycle']=='disabled'
    assert client.post('/api/reports/preflight',headers=admin,json=params).status_code==409
    assert client.patch(f'/api/report-templates/{tid}/lifecycle?state=published',headers=admin).status_code==200
    assert client.post('/api/reports/preflight',headers=admin,json=params).status_code==200
    actions=store.rows('SELECT action FROM audit_logs WHERE object_id=?',(str(tid),))
    assert {'删除报告模板版本','恢复报告模板版本'}<={r['action'] for r in actions}


def test_delete_template_preserves_reports_releases_original_and_other_versions(admin,monkeypatch):
    params,parsed=setup_report(admin,monkeypatch,True);report=create(admin,params);tid=params['template_id'];rid=report['id']
    template=client.get(f'/api/report-templates/{tid}',headers=admin).json()
    child=client.post('/api/report-templates',headers=admin,json={'blueprint':template['blueprint'],'parent_id':tid,'lifecycle':'published'}).json()['id']
    for sec in report['sections']:assert client.post(f"/api/reports/{rid}/sections/{sec['id']}/review",headers=admin,json={'expected_revision':sec['revision']}).status_code==200
    report=client.get(f'/api/reports/{rid}',headers=admin).json()
    release=client.post(f'/api/reports/{rid}/finalize',headers=admin,json={'expected_revision':report['revision'],'fixed_text_reviewed':True});assert release.status_code==201,release.text
    before=client.get(f'/api/reports/{rid}/export?mode=final&format=md',headers=admin).content
    assert client.delete(f'/api/report-templates/{tid}',headers=admin).status_code==200
    assert client.get(f'/api/report-templates/{child}',headers=admin).status_code==200
    assert client.get(f'/api/reports/{rid}',headers=admin).status_code==200
    assert any(r['id']==rid for r in client.get('/api/report-tasks',headers=admin).json())
    assert client.get(f'/api/reports/{rid}/export?mode=final&format=md',headers=admin).content==before
    # Legacy reports may have no template snapshot: the retained row must still resolve read-only.
    store.execute("UPDATE report_tasks SET template_snapshot='{}' WHERE id=?",(rid,))
    assert client.get(f'/api/reports/{rid}/preview',headers=admin).status_code==200
    assert client.post(f"/api/templates/imports/{parsed['import_id']}/save",headers=admin,json={
        'name':'不得绕过删除状态','version':'v9.0','parent_id':tid,'headings':[],
        'mappings':template['field_mappings']}).status_code==409
    source=client.get(f"/api/templates/imports/{parsed['import_id']}",headers=admin)
    assert source.status_code==200


def test_human_review_allows_evidence_missing_final_without_faking_verification(admin,monkeypatch):
    params,_=setup_report(admin,monkeypatch)
    async def no_evidence(*args,**kwargs):return [],{}
    monkeypatch.setattr(rag,'retrieve',no_evidence)
    report=create(admin,{**params,'allow_missing':True});tid=report['id']
    assert not report['quality']['ready'] and report['quality']['verified_sections']==0
    original=[(s['content'],s['verification']) for s in report['sections']]
    assert all('【待补充】' in text and proof['state']=='insufficient' for text,proof in original)
    _,other=create_member(admin)
    sec=report['sections'][0]
    review_path=f"/api/reports/{tid}/sections/{sec['id']}/review"
    assert client.post(review_path,headers=other,json={'expected_revision':sec['revision']}).status_code==404
    assert client.post(review_path,headers=admin,json={'expected_revision':sec['revision']+1}).status_code==409
    for section in report['sections']:
        response=client.post(f"/api/reports/{tid}/sections/{section['id']}/review",headers=admin,json={'expected_revision':section['revision']})
        assert response.status_code==200,response.text
    report=client.get(f'/api/reports/{tid}',headers=admin).json()
    assert report['quality']['ready'] and report['quality']['issues']==[]
    assert report['quality']['verified_sections']==0 and report['quality']['human_accepted_sections']==2
    assert all(w['human_accepted'] for w in report['quality']['warnings'])
    assert [(s['content'],s['verification']) for s in report['sections']]==original
    body={'expected_revision':report['revision'],'fixed_text_reviewed':False}
    assert client.post(f'/api/reports/{tid}/finalize',headers=admin,json=body).status_code==422
    response=client.post(f'/api/reports/{tid}/finalize',headers=admin,json={**body,'fixed_text_reviewed':True})
    assert response.status_code==201,response.text
    release=store.one('SELECT * FROM report_releases WHERE id=?',(response.json()['id'],));snapshot=json.loads(release['snapshot'])
    assert snapshot['decision']['policy']=='human-review-final' and snapshot['decision']['human_accepted_sections']==2
    assert all(s['verification'] and json.loads(s['verification'])['state']=='insufficient' for s in snapshot['sections'])
    exported=client.get(f'/api/reports/{tid}/export?mode=final&format=md',headers=admin)
    assert exported.status_code==200 and '【待补充】' in exported.text and '不代表已核实事实' in exported.text and '人工审阅决定保留' in exported.text
    report=client.get(f'/api/reports/{tid}',headers=admin).json()
    assert client.post(f'/api/reports/{tid}/reopen',headers=admin,json={'expected_revision':report['revision']}).status_code==200
    sec=report['sections'][0]
    assert client.patch(section_path(report,sec),headers=admin,json={'expected_revision':sec['revision'],'content':'人工修改后的待补充说明。'}).status_code==200
    updated=client.get(f'/api/reports/{tid}',headers=admin).json()
    assert not updated['quality']['ready'] and updated['sections'][0]['reviewed_revision']!=updated['sections'][0]['revision']
    assert client.get(f'/api/reports/{tid}/export?mode=final&format=md',headers=admin).content==exported.content
